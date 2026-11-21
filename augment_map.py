"""Add class-constant reader sets to a pristine coverage map (offline, build-free).

For every UPPER_CASE name assigned in a sqlglot class body, record where the
pristine source reads it: attribute loads, bare-name loads and string literals
(getattr / __dict__ lookups). The readers of a name are the tests whose recorded
coverage executed one of those reads. A name is *unsafe* when any read runs
while sqlglot is imported (metaclasses, class bodies, derived tables) or sits at
module/class level, because then a value change can alter derived state that
the read-site coverage does not describe. The selector falls back to class
consumers for unsafe names.

Inputs are the pinned pristine sources and the existing per-line coverage map.
No patches, sample answers or mutation outcomes are used.
"""
import argparse
import ast
import json
import re
import subprocess
import sys
from collections import defaultdict
from pathlib import Path

UPPER = re.compile(r'^[A-Z][A-Z0-9_]*$')
COMPOUND = (ast.If, ast.For, ast.AsyncFor, ast.While, ast.With, ast.AsyncWith, ast.Try,
            ast.Match, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)
if hasattr(ast, 'TryStar'):
    COMPOUND += (ast.TryStar,)

IMPORT_PASS = r'''
import importlib, json, pkgutil, sys, coverage
cov = coverage.Coverage(source=['sqlglot'], data_file=None)
cov.start()
import sqlglot
failed = []
for info in pkgutil.walk_packages(sqlglot.__path__, 'sqlglot.'):
    try:
        importlib.import_module(info.name)
    except Exception as error:
        failed.append([info.name, repr(error)])
from sqlglot.dialects.dialect import Dialect
for name in list(Dialect.classes):
    Dialect.get_or_raise(name)
cov.stop()
import dataclasses, enum
magic, seen = set(), set()
def scan(owner):
    for value in list(vars(owner).values()):
        if not isinstance(value, type) or id(value) in seen:
            continue
        if not getattr(value, '__module__', '').startswith('sqlglot'):
            continue
        seen.add(id(value))
        # Class bodies whose assignments are not read back by attribute name.
        if issubclass(value, enum.Enum):
            magic.update(value.__members__)
            magic.update(k for k in vars(value) if not k.startswith('_'))
        if dataclasses.is_dataclass(value):
            magic.update(f.name for f in dataclasses.fields(value))
        if issubclass(value, tuple) and hasattr(value, '_fields'):
            magic.update(value._fields)
        scan(value)
for name, module in list(sys.modules.items()):
    if name == 'sqlglot' or name.startswith('sqlglot.'):
        scan(module)
data = cov.get_data()
root = sys.argv[1]
lines = {f[len(root) + 1:]: sorted(data.lines(f) or []) for f in data.measured_files()}
print(json.dumps({'lines': lines, 'failed': failed, 'magic': sorted(magic)}))
'''


def candidate_names(root):
    names = set()
    for path in sorted((root / 'sqlglot').rglob('*.py')):
        for node in ast.walk(ast.parse(path.read_text())):
            if isinstance(node, ast.ClassDef):
                for stmt in node.body:
                    targets = []
                    if isinstance(stmt, ast.Assign):
                        targets = stmt.targets
                    elif isinstance(stmt, (ast.AnnAssign, ast.AugAssign)):
                        targets = [stmt.target]
                    for target in targets:
                        for leaf in ast.walk(target):
                            if isinstance(leaf, ast.Name) and UPPER.match(leaf.id):
                                names.add(leaf.id)
    return names


def header(stmt):
    """Lines of a statement that evaluate its own expressions (not nested bodies)."""
    if isinstance(stmt, COMPOUND):
        bodies = [getattr(stmt, field, None) for field in ('body', 'cases', 'handlers')]
        firsts = [b[0].lineno for b in bodies if b]
        if firsts:
            return stmt.lineno, max(stmt.lineno, min(firsts) - 1)
    return stmt.lineno, stmt.end_lineno


def reads(tree, names):
    """Return (name, start, end, top_level) for each read of a candidate name."""
    found = []

    def span(stmt):
        return stmt if isinstance(stmt, tuple) else header(stmt)

    def walk(node, stmt, in_function):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            # Decorators and defaults run where the def runs.
            for part in node.decorator_list + node.args.defaults + [d for d in node.args.kw_defaults if d]:
                walk(part, (part.lineno, part.end_lineno), in_function)
            for body in node.body:
                walk(body, body, True)
            return
        if isinstance(node, ast.Lambda):
            for part in node.args.defaults + [d for d in node.args.kw_defaults if d]:
                walk(part, stmt, in_function)
            walk(node.body, (node.lineno, node.end_lineno), True)
            return
        if isinstance(node, ast.ClassDef):
            for part in node.decorator_list + node.bases + [k.value for k in node.keywords]:
                walk(part, (part.lineno, part.end_lineno), in_function)
            for body in node.body:
                walk(body, body, in_function)
            return
        if isinstance(node, ast.stmt):
            stmt = node
        name = None
        augmented = isinstance(stmt, ast.AugAssign) and not isinstance(stmt, tuple)
        if isinstance(node, ast.Attribute) and node.attr in names:
            if isinstance(node.ctx, ast.Load) or augmented:
                name = node.attr
        elif isinstance(node, ast.Name) and node.id in names:
            if isinstance(node.ctx, ast.Load) or augmented:
                name = node.id
        elif isinstance(node, ast.Constant) and isinstance(node.value, str) and node.value in names:
            name = node.value
        if name and stmt is not None:
            start, end = span(stmt)
            found.append((name, start, end, not in_function))
        for child in ast.iter_child_nodes(node):
            walk(child, stmt, in_function)

    walk(tree, None, False)
    return found


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--source', required=True)
    parser.add_argument('--map', required=True)
    parser.add_argument('--output', required=True)
    args = parser.parse_args()
    root = Path(args.source).resolve()
    mapping = json.loads(Path(args.map).read_text())
    names = candidate_names(root)

    result = subprocess.run([sys.executable, '-c', IMPORT_PASS, str(root)], cwd=root,
                            capture_output=True, text=True, check=True,
                            env={'PYTHONPATH': str(root), 'COVERAGE_CORE': 'ctrace', 'PATH': '/usr/bin:/bin'})
    imported = json.loads(result.stdout)
    import_lines = {path: set(lines) for path, lines in imported['lines'].items()}
    if imported['failed']:
        print('Modules that failed to import (their top-level reads count as unsafe):',
              imported['failed'], file=sys.stderr)

    files, pools, tests = mapping['files'], mapping['sets'], mapping['tests']
    readers, unsafe, why = defaultdict(set), set(), {}
    for name in imported['magic']:
        if name in names:
            unsafe.add(name)
            why[name] = 'enum, dataclass or namedtuple member'
    print(f"{len(imported['magic'])} enum/dataclass/namedtuple member names", file=sys.stderr)
    for path in sorted((root / 'sqlglot').rglob('*.py')):
        relative = path.relative_to(root).as_posix()
        executed = import_lines.get(relative, set())
        lines = files.get(relative, {}).get('lines', {})
        for name, start, end, top_level in reads(ast.parse(path.read_text()), names):
            if top_level or executed.intersection(range(start, end + 1)):
                if name not in unsafe:
                    why[name] = f'{relative}:{start}'
                unsafe.add(name)
            for line in range(start, end + 1):
                group = lines.get(str(line))
                if group is not None:
                    readers[name].update(pools[group])

    # Reads inside the test suite itself: the enclosing test, else its whole file.
    by_file = defaultdict(list)
    for index, test in enumerate(tests):
        by_file[test.split('::', 1)[0]].append(index)
    index_of = {test: i for i, test in enumerate(tests)}
    for path in sorted((root / 'tests').rglob('*.py')):
        relative = path.relative_to(root).as_posix()
        tree = ast.parse(path.read_text())
        owners = {}
        for cls in [n for n in tree.body if isinstance(n, ast.ClassDef)]:
            for fn in cls.body:
                if isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)) and fn.name.startswith('test'):
                    owners[(fn.lineno, fn.end_lineno)] = f'{relative}::{cls.name}::{fn.name}'
        for name, start, end, _ in reads(tree, names):
            owner = next((test for (a, b), test in owners.items() if a <= start and end <= b), None)
            if owner in index_of:
                readers[name].add(index_of[owner])
            else:
                # Helpers, fixtures or module code: every test in the file, and
                # any file importing helpers is covered by the shared-helper rule below.
                readers[name].update(by_file.get(relative, []))
                if relative.endswith('helpers.py') or '/' not in relative.removeprefix('tests/'):
                    if not relative.split('/')[-1].startswith('test_'):
                        unsafe.add(name)
                        why.setdefault(name, f'{relative}:{start}')

    interned = {tuple(p): i for i, p in enumerate(pools)}
    attr_readers = {}
    for name in sorted(names):
        key = tuple(sorted(readers.get(name, ())))
        if key not in interned:
            interned[key] = len(pools)
            pools.append(list(key))
        attr_readers[name] = interned[key]
    mapping['attr_readers'] = attr_readers
    mapping['attr_unsafe'] = sorted(unsafe)
    Path(args.output).write_text(json.dumps(mapping, separators=(',', ':')))
    safe = [n for n in names if n not in unsafe]
    print(f'{len(names)} class constants, {len(safe)} narrowable, {len(unsafe)} unsafe', file=sys.stderr)
    for probe in ('WEEK_OFFSET', 'NUMERIC_ESCAPES', 'DUCKDB', 'ATHENA'):
        print(probe, 'unsafe' if probe in unsafe else 'safe', why.get(probe, ''),
              len(readers.get(probe, ())), 'readers', file=sys.stderr)


if __name__ == '__main__':
    main()
