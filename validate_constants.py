"""Development check for class-constant narrowing (never used by the image).

Mutates random narrowable UPPER_CASE class constants in the pristine source,
runs the broader class-consumer selection under pytest, and verifies that every
failing test is inside the narrowed selection.

    python validate_constants.py --source work/sqlglot --count 30 --jobs 4
"""
import argparse
import ast
import concurrent.futures
import difflib
import json
import os
import random
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
from selector import select, UPPER  # noqa: E402

RUNNER = r'''
import json, os, sys, pytest
failed = set()
class Reports:
    def pytest_runtest_logreport(self, report):
        if report.failed:
            failed.add(report.nodeid)
    def pytest_collectreport(self, report):
        if report.failed:
            failed.add(report.nodeid)
targets = json.load(open(sys.argv[1]))
code = pytest.main(['-q', '-p', 'no:cacheprovider', '-x' if False else '-q', *targets], plugins=[Reports()])
json.dump(sorted(failed), open(sys.argv[2], 'w'))
'''


def mutate_value(node, rng):
    """Return replacement source for a literal-ish value, or None."""
    text = ast.unparse(node)
    if isinstance(node, ast.Constant):
        value = node.value
        if isinstance(value, bool):
            return repr(not value)
        if isinstance(value, int):
            return repr(value + rng.choice([-1, 1]))
        if isinstance(value, str):
            return repr(value + 'Z') if value else repr('Z')
        if value is None:
            return None
    if isinstance(node, ast.UnaryOp) and isinstance(node.operand, ast.Constant) and isinstance(node.operand.value, int):
        return repr(-node.operand.value + rng.choice([-1, 1]))
    if isinstance(node, (ast.Set, ast.List, ast.Tuple)) and node.elts:
        drop = rng.randrange(len(node.elts))
        items = [ast.unparse(e) for i, e in enumerate(node.elts) if i != drop]
        if isinstance(node, ast.Set):
            return '{' + ', '.join(items) + '}' if items else 'set()'
        if isinstance(node, ast.List):
            return '[' + ', '.join(items) + ']'
        return '(' + ', '.join(items) + (',)' if len(items) == 1 else ')')
    if isinstance(node, ast.Dict) and node.keys and all(k is not None for k in node.keys):
        drop = rng.randrange(len(node.keys))
        items = [f'{ast.unparse(k)}: {ast.unparse(v)}' for i, (k, v) in enumerate(zip(node.keys, node.values)) if i != drop]
        return '{' + ', '.join(items) + '}'
    del text
    return None


def candidates(source, mapping):
    unsafe = set(mapping['attr_unsafe'])
    found = []
    for path in sorted((source / 'sqlglot').rglob('*.py')):
        relative = path.relative_to(source).as_posix()
        text = path.read_text()
        for node in ast.walk(ast.parse(text)):
            if not isinstance(node, ast.ClassDef):
                continue
            for stmt in node.body:
                if isinstance(stmt, ast.Assign) and len(stmt.targets) == 1 and isinstance(stmt.targets[0], ast.Name):
                    name = stmt.targets[0].id
                    if UPPER.match(name) and name not in unsafe and name in mapping['attr_readers']:
                        found.append((relative, stmt.value.lineno, stmt.value.col_offset,
                                      stmt.value.end_lineno, stmt.value.end_col_offset, name))
    return found


def make_patch(relative, old, new):
    diff = difflib.unified_diff(old.splitlines(keepends=True), new.splitlines(keepends=True),
                                f'a/{relative}', f'b/{relative}', n=3)
    return f'diff --git a/{relative} b/{relative}\n' + ''.join(diff)


def run_case(case):
    source, relative, new_text, targets = case
    work = Path(tempfile.mkdtemp(prefix='constcase-'))
    try:
        tree = work / 'sqlglot'
        shutil.copytree(source, tree, ignore=shutil.ignore_patterns('__pycache__', '.pytest_cache', '.git'))
        (tree / relative).write_text(new_text)
        (work / 'targets.json').write_text(json.dumps(targets))
        env = dict(os.environ, PYTHONPATH=str(tree), SKIP_INTEGRATION='0', PYTHONDONTWRITEBYTECODE='1')
        subprocess.run([sys.executable, '-c', RUNNER, str(work / 'targets.json'), str(work / 'out.json')],
                       cwd=tree, env=env, capture_output=True, timeout=900)
        return json.loads((work / 'out.json').read_text())
    finally:
        shutil.rmtree(work, ignore_errors=True)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--source', required=True)
    parser.add_argument('--count', type=int, default=30)
    parser.add_argument('--jobs', type=int, default=4)
    parser.add_argument('--seed', type=int, default=11)
    args = parser.parse_args()
    source = Path(args.source).resolve()
    mapping = json.loads((ROOT / 'coverage-map.json').read_text())
    legacy = {k: v for k, v in mapping.items() if k not in ('attr_readers', 'attr_unsafe')}
    tests = json.loads((ROOT / 'sample/tests.json').read_text())['tests']
    rng = random.Random(args.seed)
    pool = candidates(source, mapping)
    rng.shuffle(pool)
    cases, meta = [], []
    for relative, line, col, end_line, end_col, name in pool:
        if len(cases) >= args.count:
            break
        text = (source / relative).read_text()
        lines = text.splitlines(keepends=True)
        node = ast.parse(text)
        value = next((s.value for s in ast.walk(node) if isinstance(s, ast.Assign)
                      and s.value.lineno == line and s.value.col_offset == col), None)
        replacement = mutate_value(value, rng) if value is not None else None
        if replacement is None:
            continue
        start = sum(len(l) for l in lines[:line - 1]) + len(lines[line - 1][:col].encode()[:col].decode(errors='ignore'))
        end = sum(len(l) for l in lines[:end_line - 1]) + end_col
        new_text = text[:start] + replacement + text[end:]
        try:
            ast.parse(new_text)
        except SyntaxError:
            continue
        patch = make_patch(relative, text, new_text)
        narrow = {t['id'] for t in select(patch, tests, mapping)}
        broad = {t['id'] for t in select(patch, tests, legacy)}
        if narrow == broad or not narrow <= broad:
            continue
        cases.append((str(source), relative, new_text, sorted(broad)))
        meta.append((relative, name, replacement[:40], narrow, broad))
    print(f'{len(cases)} narrowed mutants', file=sys.stderr)
    misses = 0
    with concurrent.futures.ProcessPoolExecutor(args.jobs) as executor:
        for (relative, name, replacement, narrow, broad), failed in zip(meta, executor.map(run_case, cases)):
            missed = sorted(set(failed) - narrow)
            misses += bool(missed)
            print(f"{'MISS' if missed else 'ok  '} {relative} {name} -> {replacement} | "
                  f"narrow {len(narrow)} broad {len(broad)} failed {len(failed)} {missed[:3]}")
    print(f'misses: {misses}/{len(cases)}')


if __name__ == '__main__':
    main()
