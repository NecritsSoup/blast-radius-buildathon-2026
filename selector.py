"""Coverage-based selection; no sample answers are used at runtime or build time."""
import ast
import builtins
import json
import os
import re
import sys
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


UPPER = re.compile(r'^[A-Z][A-Z0-9_]*$')


def find_class(tree, qualname):
    """Locate a class by the qualified name structure.class_scopes records."""
    def visit(node, prefix):
        for child in ast.iter_child_nodes(node):
            if isinstance(child, ast.ClassDef):
                name = prefix + child.name
                if name == qualname:
                    yield child
                yield from visit(child, name + '.')
            elif isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                yield from visit(child, prefix + child.name + '.<locals>.')
            else:
                yield from visit(child, prefix)
    found = list(visit(tree, ''))
    return found[0] if len(found) == 1 else None


def class_layout(node):
    """Split a class body into simple constant assignments and everything else."""
    head = [ast.dump(part) for part in node.decorator_list + node.bases + node.keywords]
    constants, other = {}, []
    for stmt in node.body:
        target = None
        if isinstance(stmt, ast.Assign) and len(stmt.targets) == 1:
            target = stmt.targets[0]
        elif isinstance(stmt, ast.AnnAssign) and stmt.value is not None:
            target = stmt.target
        if isinstance(target, ast.Name) and UPPER.match(target.id):
            if target.id in constants:
                return None
            constants[target.id] = stmt
        else:
            other.append(ast.dump(stmt))
    return node.name, head, other, constants


def loaded_paths(node):
    paths = set()
    for leaf in ast.walk(node):
        if isinstance(leaf, (ast.Name, ast.Attribute)):
            try:
                paths.add(ast.unparse(leaf))
            except (ValueError, TypeError, AttributeError, RecursionError):
                return None
    return paths


def constant_readers(before, after, qualname, mapping):
    """Tests reading the class constants a patch changes, or None when unsure.

    Only applies when the class differs solely in UPPER_CASE single-name
    assignments whose names are read exclusively from function code at run time
    (recorded offline from pristine coverage). New values may only use names and
    attribute paths that already appear in the pristine file, so the class body
    still evaluates at import.
    """
    readers, unsafe = mapping.get('attr_readers'), mapping.get('attr_unsafe')
    if readers is None or unsafe is None:
        return None
    old, new = find_class(before, qualname), find_class(after, qualname)
    if old is None or new is None:
        return None
    old_layout, new_layout = class_layout(old), class_layout(new)
    if old_layout is None or new_layout is None or old_layout[:3] != new_layout[:3]:
        return None
    old_constants, new_constants = old_layout[3], new_layout[3]
    changed = {name for name in set(old_constants) | set(new_constants)
               if name not in old_constants or name not in new_constants
               or ast.dump(old_constants[name]) != ast.dump(new_constants[name])}
    known = loaded_paths(before)
    if known is None:
        return None
    known |= set(dir(builtins))
    tests = set()
    unsafe = set(unsafe)
    for name in changed:
        if name in unsafe or name not in readers:
            return None
        if name in new_constants:
            stmt = new_constants[name]
            used = loaded_paths(stmt.value) if stmt.value is not None else set()
            if stmt.__class__ is ast.AnnAssign:
                used = (used or set()) | (loaded_paths(stmt.annotation) or set())
            if used is None or not used <= known | {name}:
                return None
        tests.update(mapping['sets'][readers[name]])
    return tests


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
    parsed, trees = {}, {}
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
        trees[path] = (before, after)
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
                scopes = [c for c in info.get('classes', []) if c[0] <= number <= c[1]]
                narrowed = None
                if scopes and path in trees:
                    scope = min(scopes, key=lambda c: c[1] - c[0])
                    narrowed = constant_readers(*trees[path], scope[2], mapping)
                contexts = class_consumers(info, number, pools)
                if not contexts:
                    return tests
                if narrowed is not None:
                    # Readers are recorded by name across every class, so keep
                    # only those that also use this class: never wider than before.
                    contexts = set(contexts) & narrowed
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


def overlay(patch, root):
    """Patched sources for every changed file, or None when any cannot be built."""
    patched = {}
    for path in changes(patch):
        original = root / path
        if not original.is_file():
            return None
        replacement = patched_source(patch, path, original.read_text())
        if not replacement:
            return None
        patched[path] = replacement
    return patched or None


def main():
    data = Path(os.environ.get('DATA_DIR', '/data'))
    tests = json.loads((data / 'tests.json').read_text())['tests']
    map_path = Path(__file__).with_name('coverage-map.json')
    mapping = json.loads(map_path.read_text()) if map_path.exists() else None
    mode = os.environ.get('SELECT_MODE', 'block')
    results, jobs = {}, []
    root = Path(os.environ.get('SQLGLOT_ROOT', '/app/sqlglot'))
    verify_on = os.environ.get('VERIFY', '1') == '1' and (root / 'sqlglot').is_dir()
    for patch_path in sorted((data / 'commits').glob('*.patch')):
        patch = patch_path.read_text()
        selected = select(patch, tests, mapping, mode)
        results[patch_path.stem] = selected
        if verify_on and selected and len(selected) < len(tests):
            try:
                import verify
                if verify.eligible(changes(patch), selected):
                    patched = overlay(patch, root)
                    if patched:
                        jobs.append((patch_path.stem, patched, selected))
            except Exception as error:  # never let the check cost an answer
                print(f'verify setup failed for {patch_path.stem}: {error!r}', file=sys.stderr)
    if jobs:
        try:
            import verify
            costs = None
            if mapping and mapping.get('test_cpu'):
                costs = {'test': {test: cpu for test, cpu in zip(mapping['tests'], mapping['test_cpu']) if cpu},
                         'class': mapping.get('class_cpu', {})}
            refined = verify.refine(jobs, str(root), split, log=lambda m: print(m, file=sys.stderr), costs=costs)
            results.update(refined)
        except Exception as error:
            print(f'verify failed: {error!r}', file=sys.stderr)
    for commit, selected in results.items():
        print(json.dumps({'commit': commit, 'workers': split(selected)}, separators=(',', ':')))
    sys.stdout.flush()


if __name__ == '__main__':
    main()
