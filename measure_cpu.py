"""Offline: measure each pristine test's CPU the way verify.py runs it.

Runs every TestCase class in-process (process pools replaced by the same
pickling inline executor), records per-test CPU seconds plus each class's
import + setUpClass CPU, and stores them in the map as `test_cpu` (list aligned
with map['tests']) and `class_cpu` ({"file::Class": seconds}). These only feed
verify.py's cost estimate; no selection depends on them.

    python measure_cpu.py --source /path/to/sqlglot --map coverage-map.json
"""
import argparse
import contextlib
import importlib
import io
import json
import os
import sys
import time
import unittest
from pathlib import Path

for _name in ('OPENBLAS_NUM_THREADS', 'OMP_NUM_THREADS', 'MKL_NUM_THREADS'):
    os.environ.setdefault(_name, '1')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--source', required=True)
    parser.add_argument('--map', required=True)
    args = parser.parse_args()
    here = Path(__file__).resolve().parent
    sys.path.insert(0, str(here))
    import verify
    import concurrent.futures
    import concurrent.futures.process
    concurrent.futures.ProcessPoolExecutor = verify._InlineExecutor
    concurrent.futures.process.ProcessPoolExecutor = verify._InlineExecutor
    mapping_path = Path(args.map).resolve()
    mapping = json.loads(mapping_path.read_text())
    root = Path(args.source).resolve()
    os.chdir(root)
    sys.path.insert(0, str(root))
    import duckdb  # noqa: F401  (preloaded by verify.py as well)
    import pandas  # noqa: F401

    by_class = {}
    for index, test in enumerate(mapping['tests']):
        file, cls, method = test.split('::')
        by_class.setdefault((file, cls), []).append((index, method))
    test_cpu = [None] * len(mapping['tests'])
    class_cpu = {}
    sink = io.StringIO()
    for (file, cls), members in sorted(by_class.items()):
        start = time.process_time()
        try:
            with contextlib.redirect_stdout(sink), contextlib.redirect_stderr(sink):
                module = importlib.import_module(file[:-3].replace('/', '.'))
                case_class = getattr(module, cls)
                case_class.setUpClass()
        except Exception as error:
            print(f'skip {file}::{cls}: {error!r}', file=sys.stderr)
            continue
        class_cpu[f'{file}::{cls}'] = round(time.process_time() - start, 3)
        for index, method in members:
            start = time.process_time()
            with contextlib.redirect_stdout(sink), contextlib.redirect_stderr(sink):
                unittest.TestSuite([case_class(method)]).run(unittest.TestResult())
            test_cpu[index] = round(time.process_time() - start, 3)
        with contextlib.redirect_stdout(sink), contextlib.redirect_stderr(sink):
            try:
                case_class.doClassCleanups()
                case_class.tearDownClass()
            except Exception:
                pass
        sink.seek(0)
        sink.truncate()
    mapping['test_cpu'] = test_cpu
    mapping['class_cpu'] = class_cpu
    mapping_path.write_text(json.dumps(mapping, separators=(',', ':')))
    measured = [c for c in test_cpu if c is not None]
    print(f'measured {len(measured)} tests, {sum(measured):.1f}s CPU, {len(class_cpu)} classes', file=sys.stderr)


if __name__ == '__main__':
    main()
