"""Validate a fresh Is/IsNot mutation against the complete pristine test suite.

Development only; neither this runner nor its results enter the image.
"""
import argparse
import ast
import difflib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
from selector import select

root = Path(__file__).resolve().parent
parser = argparse.ArgumentParser()
parser.add_argument('--map', required=True)
parser.add_argument('--path', required=True)
parser.add_argument('--line', type=int, required=True)
parser.add_argument('--column', type=int)
parser.add_argument('--test', action='append', default=[])
args = parser.parse_args()
mapping = json.loads(Path(args.map).read_text())
original = mapping['files'][args.path]['source']
candidates = [node for node in ast.walk(ast.parse(original)) if isinstance(node, ast.Compare)
              and len(node.ops) == 1 and isinstance(node.ops[0], (ast.Is, ast.IsNot))
              and node.lineno == args.line and node.lineno == node.end_lineno]
if args.column is not None:
    candidates = [node for node in candidates if node.col_offset == args.column]
if len(candidates) != 1:
    raise SystemExit('Expected exactly one one-line identity comparison')
node = candidates[0]
old = original.splitlines(keepends=True)
new = old.copy()
start, end = node.left.end_col_offset, node.comparators[0].col_offset
middle = old[node.lineno - 1][start:end]
replacement = middle.replace('is not', '!=') if isinstance(node.ops[0], ast.IsNot) else middle.replace('is', '==')
new[node.lineno - 1] = old[node.lineno - 1][:start] + replacement + old[node.lineno - 1][end:]
patch = f'diff --git a/{args.path} b/{args.path}\n' + ''.join(difflib.unified_diff(old, new,
                                fromfile='a/' + args.path, tofile='b/' + args.path))
(root / 'work/identity-heldout.patch').write_text(patch)
tests = json.loads((root / 'sample/tests.json').read_text())['tests']
os.environ['SELECT_PROOFS'] = '1'
selected = {test['id'] for test in select(patch, tests, mapping)}
print(f'{args.path}:{args.line}: selected {len(selected)}/{len(tests)}', flush=True)
if not selected:
    raise SystemExit('Changed comparison was skipped: expected a custom-equality counterexample')
source = root / 'work/identity-source'
if source.exists():
    shutil.rmtree(source)
shutil.copytree(root / 'work/sqlglot', source,
                ignore=shutil.ignore_patterns('__pycache__', '.pytest_cache', '.git'))
(source / args.path).write_text(''.join(new))
report = root / 'work/identity-report.json'
env = dict(os.environ, PYTHONPATH=str(source), SKIP_INTEGRATION='0', PYTHON_CPU_COUNT='2')
with (root / 'work/identity-pytest.log').open('w') as log:
    subprocess.run([sys.executable, str(root / 'validate_mutations.py'), '--runner', str(source), str(report), *args.test],
                   env=env, stdout=log, stderr=subprocess.STDOUT, check=True, timeout=180)
result = json.loads(report.read_text())
failed = set(result['failed']) & {test['id'] for test in tests}
missed = failed - selected
summary = {'scope': 'targeted' if args.test else 'full_suite', 'targets': args.test, 'source': args.path, 'line': args.line, 'selected': len(selected), 'actual_failed': len(failed),
           'missed': sorted(missed), 'collection_errors': result['collection_errors'],
           'pytest_exit_code': result['exit_code']}
(root / 'work/identity-summary.json').write_text(json.dumps(summary, indent=2))
print(json.dumps(summary, indent=2))
if missed or result['collection_errors'] or not failed or result['exit_code'] != 1:
    raise SystemExit('Custom-equality counterexample validation did not pass')
