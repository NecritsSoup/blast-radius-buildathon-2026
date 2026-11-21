"""Development check for the run-time verifier (never used by the image).

Makes random single-expression mutations inside covered sqlglot functions,
applies the static selector and then verify.refine, and compares with the
ground truth from real pytest runs (spawned process pools, files on disk) on
the static selection. Any test that the verifier dropped but pytest failed is
a MISS. Static misses (failures outside the static selection) are reported too.

    python validate_verify.py --source work/sqlglot --count 60 --jobs 3
"""
import argparse
import ast
import concurrent.futures
import json
import os
import random
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
import selector  # noqa: E402
import verify  # noqa: E402
from validate_constants import make_patch, run_case  # noqa: E402

SWAPS = {ast.Eq: '!=', ast.NotEq: '==', ast.Lt: '<=', ast.LtE: '<', ast.Gt: '>=', ast.GtE: '>',
         ast.In: 'not in', ast.NotIn: 'in', ast.Is: 'is not', ast.IsNot: 'is'}


def mutations(node):
    """Yield (old segment node, replacement text) options for a node."""
    if isinstance(node, ast.Compare) and len(node.ops) == 1 and type(node.ops[0]) in SWAPS:
        left, right = ast.unparse(node.left), ast.unparse(node.comparators[0])
        yield node, f'{left} {SWAPS[type(node.ops[0])]} {right}'
    if isinstance(node, ast.Constant):
        if isinstance(node.value, bool):
            yield node, repr(not node.value)
        elif isinstance(node.value, int):
            yield node, repr(node.value + 1)
        elif isinstance(node.value, str) and node.value and '\n' not in node.value:
            yield node, repr(node.value[:-1] or 'Z')
    if isinstance(node, ast.BoolOp):
        op = ' or ' if isinstance(node.op, ast.And) else ' and '
        yield node, op.join(f'({ast.unparse(v)})' for v in node.values)
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.Not):
        yield node, f'({ast.unparse(node.operand)})'
    if isinstance(node, ast.BinOp) and isinstance(node.op, (ast.Add, ast.Sub)):
        op = '-' if isinstance(node.op, ast.Add) else '+'
        yield node, f'{ast.unparse(node.left)} {op} {ast.unparse(node.right)}'


def candidates(source, mapping):
    found = []
    for relative, info in mapping['files'].items():
        if not relative.startswith('sqlglot/'):
            continue
        covered = {int(line) for line in info['lines']}
        text = (source / relative).read_text()
        tree = ast.parse(text)
        functions = [n for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]
        for fn in functions:
            for node in ast.walk(fn):
                if getattr(node, 'lineno', None) in covered and node.lineno == getattr(node, 'end_lineno', -1):
                    for target, replacement in mutations(node):
                        found.append((relative, target.lineno, target.col_offset, target.end_col_offset, replacement))
    return found


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--source', required=True)
    parser.add_argument('--count', type=int, default=60)
    parser.add_argument('--jobs', type=int, default=3)
    parser.add_argument('--seed', type=int, default=5)
    args = parser.parse_args()
    source = Path(args.source).resolve()
    mapping = json.loads((ROOT / 'coverage-map.json').read_text())
    tests = json.loads((ROOT / 'sample/tests.json').read_text())['tests']
    rng = random.Random(args.seed)
    pool = candidates(source, mapping)
    rng.shuffle(pool)
    print(f'{len(pool)} candidate mutations', file=sys.stderr)
    chosen = []
    for relative, line, col, end_col, replacement in pool:
        if len(chosen) >= args.count:
            break
        text = (source / relative).read_text()
        lines = text.splitlines(keepends=True)
        raw = lines[line - 1].encode()
        mutated_line = (raw[:col] + replacement.encode() + raw[end_col:]).decode()
        new_text = ''.join(lines[:line - 1]) + mutated_line + ''.join(lines[line:])
        try:
            compile(new_text, relative, 'exec')
        except SyntaxError:
            continue
        patch = make_patch(relative, text, new_text)
        static = selector.select(patch, tests, mapping)
        if not static or len(static) == len(tests) or not verify.eligible(selector.changes(patch), static):
            continue
        patched = selector.overlay(patch, source)
        if not patched:
            continue
        refined = verify.refine([('m', patched, static)], str(source), selector.split)
        if 'm' not in refined:
            continue  # the verifier would not run on this one
        chosen.append((relative, line, replacement, new_text, static, refined['m']))
    print(f'{len(chosen)} mutants where the verifier runs', file=sys.stderr)
    cases = [(str(source), rel, new_text, sorted(t['id'] for t in static))
             for rel, _, _, new_text, static, _ in chosen]
    misses = static_misses = dropped_total = 0
    with concurrent.futures.ProcessPoolExecutor(args.jobs) as executor:
        for (rel, line, repl, _, static, refined), failed in zip(chosen, executor.map(run_case, cases)):
            kept = {t['id'] for t in refined}
            dropped = {t['id'] for t in static} - kept
            bad = sorted(set(failed) & dropped)
            misses += bool(bad)
            dropped_total += bool(dropped) and not failed
            print(f"{'MISS' if bad else 'ok  '} {rel}:{line} -> {repl[:30]!r} static {len(static)} "
                  f"kept {len(kept)} pytest-failed {len(failed)} {bad[:2]}", flush=True)
    print(f'verifier misses: {misses}/{len(chosen)}; all-pass commits emptied: {dropped_total}')


if __name__ == '__main__':
    main()
