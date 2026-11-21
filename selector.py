"""Coverage-based selection; no sample answers are used at runtime or build time."""
import ast
import json
import os
import re
from pathlib import Path
from semantic import equivalent_operator_edits


def split(tests):
    best, cost = [], float('inf')
    ordered = sorted(tests, key=lambda t: (-t['seconds'], t['number']))
    for count in range(1, min(8, len(tests)) + 1):
        workers, loads = [[] for _ in range(count)], [0] * count
        for test in ordered:
            i = min(range(count), key=lambda i: loads[i])
            workers[i].append(test['number'])
            loads[i] += round(test['seconds'] * 1000)
        value = max(loads) + 2000 * count
        if value < cost:
            best, cost = workers, value
    return best


def changes(patch):
    result = {}
    path, old, removed = None, None, False
    for line in patch.splitlines():
        if line.startswith('diff --git a/'):
            path = line.split(' b/', 1)[0][13:]
            result.setdefault(path, set())
            old = None
        elif line.startswith('--- a/'):
            path = line[6:]
            result.setdefault(path, set())
        elif line.startswith('@@'):
            removed = False
            match = re.match(r'@@ -(\d+)(?:,(\d+))? \+', line)
            old = int(match[1]) + (match[2] == '0') if match else None
        elif path is not None and old is not None:
            if line.startswith('-'):
                result[path].add(max(1, old))
                old += 1
                removed = True
            elif line.startswith('+'):
                # Insertions can change either neighboring branch.
                if removed:
                    result[path].add(max(1, old - 1))
                else:
                    result[path].update((max(1, old - 1), max(1, old)))
            elif line.startswith(' '):
                old += 1
                removed = False
    return result


def patched_source(patch, path, source):
    """Apply a complete file diff, refusing mismatched context."""
    if not source or not path.endswith('.py'):
        return None
    chunks = re.split(r'(?=^diff --git )', patch, flags=re.MULTILINE)
    relevant = [chunk for chunk in chunks if path in changes(chunk)]
    if len(relevant) != 1:
        return None
    original = source.splitlines()
    output, cursor, active = [], 0, False
    try:
        for line in relevant[0].splitlines():
            if line.startswith('@@'):
                match = re.match(r'@@ -(\d+)(?:,(\d+))? \+', line)
                start = int(match[1]) - 1 + (match[2] == '0')
                if start < cursor:
                    return None
                output.extend(original[cursor:start])
                cursor, active = start, True
            elif active and line[:1] in (' ', '-', '+'):
                if line[0] in (' ', '-'):
                    if cursor >= len(original) or original[cursor] != line[1:]:
                        return None
                    cursor += 1
                if line[0] in (' ', '+'):
                    output.append(line[1:])
                if line[0] in ('-', '+') and re.search(r'coding[:=]', line):
                    return None
            elif active and line.startswith('\\'):
                return None
        if not active:
            return None
        output.extend(original[cursor:])
        return '\n'.join(output) + '\n'
    except (ValueError, TypeError, AttributeError):
        return None


def unchanged_ast(patch, path, source):
    replacement = patched_source(patch, path, source)
    if not replacement:
        return False
    try:
        return ast.dump(ast.parse(source, type_comments=True)) == ast.dump(ast.parse(replacement, type_comments=True))
    except (SyntaxError, ValueError, TypeError):
        return False


def class_consumers(info, number, pools):
    candidates = [scope for scope in info.get('classes', []) if scope[0] <= number <= scope[1]]
    if not candidates:
        return []
    scope = min(candidates, key=lambda candidate: candidate[1] - candidate[0])
    return pools[scope[3]]


def select(patch, tests, mapping, mode='block'):
    affected = changes(patch)
    if not affected or mapping is None:
        return tests
    ids = set()
    if mapping.get('version') != 2:
        raise ValueError('Unsupported coverage map version')
    files, pools, mapped_tests = mapping['files'], mapping['sets'], mapping['tests']
    # Platform-specific collection differences require a conservative fallback.
    known_tests = set(mapped_tests)
    if any(t['id'] not in known_tests for t in tests):
        return tests
    parsed = {}
    all_equivalent = bool(mapping.get('operand_types')) and os.environ.get('SELECT_PROOFS', '1') == '1'
    for path, lines in affected.items():
        source = files.get(path, {}).get('source')
        if not lines:
            return tests
        if not source:
            all_equivalent = False
            continue
        replacement = patched_source(patch, path, source)
        if not replacement:
            return tests
        try:
            before = ast.parse(source, type_comments=True)
            after = ast.parse(replacement, type_comments=True)
            compile(after, path, 'exec', dont_inherit=True)
        except (SyntaxError, ValueError, TypeError):
            return tests
        unchanged = ast.dump(before) == ast.dump(after)
        parsed[path] = unchanged
        if all_equivalent and not unchanged:
            all_equivalent = equivalent_operator_edits(before, after,
                               files[path].get('comparisons', {}), mapping['operand_types'])
    if all_equivalent:
        return []
    for path, lines in affected.items():
        if parsed.get(path):
            continue
        if path in mapping['fixtures']:
            ids.update(mapped_tests[i] for i in pools[mapping['fixtures'][path]])
            continue
        if path.startswith('tests/') and path.endswith('.py'):
            direct = [t for t in tests if t['file'] == path]
            if not direct:
                return tests
            ids.update(t['id'] for t in direct)
            continue
        info = files.get(path)
        if info is None:
            return tests
        for number in lines:
            functions = [s for s in info['spans'] if s[0] <= number <= s[1]]
            if not functions:
                contexts = class_consumers(info, number, pools)
                if not contexts:
                    return tests
            else:
                start, end = min(functions, key=lambda s: s[1] - s[0])
                if mode == 'block':
                    blocks = [s for s in info.get('blocks', [])
                              if start <= s[0] <= number <= s[1] <= end]
                    if blocks:
                        start, end = min(blocks, key=lambda s: s[1] - s[0])
                if mode == 'line':
                    contexts = pools[info['lines'][str(number)]] if str(number) in info['lines'] else []
                    if not contexts:
                        return tests
                else:
                    contexts = {i for key, group in info['lines'].items()
                                if start <= int(key) <= end for i in pools[group]}
                    if not contexts and mode != 'block':
                        contexts = class_consumers(info, number, pools)
                        if not contexts:
                            return tests
            ids.update(mapped_tests[i] for i in contexts)
    return [t for t in tests if t['id'] in ids]


def main():
    data = Path(os.environ.get('DATA_DIR', '/data'))
    tests = json.loads((data / 'tests.json').read_text())['tests']
    map_path = Path(__file__).with_name('coverage-map.json')
    mapping = json.loads(map_path.read_text()) if map_path.exists() else None
    for patch in sorted((data / 'commits').glob('*.patch')):
        selected = select(patch.read_text(), tests, mapping, os.environ.get('SELECT_MODE', 'block'))
        print(json.dumps({'commit': patch.stem, 'workers': split(selected)}, separators=(',', ':')))


if __name__ == '__main__':
    main()
