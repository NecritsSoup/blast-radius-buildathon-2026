"""Build a compact coverage/fixture map from the pristine pinned source only."""
import argparse
import concurrent.futures
import functools
import pickle
import ast
import json
import os
import sys
from collections import defaultdict
from pathlib import Path
import coverage
import pytest
from structure import class_scopes
from comparison_data import ComparisonData


class CoverageExecutor(concurrent.futures.Executor):
    """Execute pristine pool tasks under the caller's coverage context.

    Preserve the Future interface and the process pool's copy boundary. This is
    build-only instrumentation; the tested sqlglot source is never modified.
    """
    def __init__(self, max_workers=None, mp_context=None, initializer=None, initargs=(), **kwargs):
        if initializer is not None:
            initializer(*pickle.loads(pickle.dumps(initargs)))

    def submit(self, fn, /, *args, **kwargs):
        future = concurrent.futures.Future()
        try:
            args, kwargs = pickle.loads(pickle.dumps((args, kwargs)))
            value = fn(*args, **kwargs)
            future.set_result(pickle.loads(pickle.dumps(value)))
        except BaseException as error:
            future.set_exception(error)
        return future

    def shutdown(self, wait=True, *, cancel_futures=False):
        pass


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--source', default='/sqlglot')
    parser.add_argument('--output', default='/app/coverage-map.json')
    args = parser.parse_args()
    root = Path(args.source).resolve()
    output = Path(args.output).resolve()
    os.chdir(root)
    sys.path.insert(0, str(root))
    os.environ['SKIP_INTEGRATION'] = '0'
    # Python 3.14 defaults to sys.monitoring, which lacks dynamic contexts.
    os.environ['COVERAGE_CORE'] = 'ctrace'
    cov = coverage.Coverage(source=['sqlglot'], data_file=None)
    fixtures = defaultdict(set)
    state = {'context': '', 'tests': [], 'seen_types': set()}
    type_uses = defaultdict(set)
    type_keys = {}

    def audit(event, arguments):
        if event != 'open' or not state['context']:
            return
        name = arguments[0]
        if isinstance(name, str) and ('fixtures/' in name or 'fixtures' + os.sep in name):
            path = Path(name).resolve()
            if path.is_relative_to(root):
                fixtures[path.relative_to(root).as_posix()].add(state['context'])

    sys.addaudithook(audit)

    def context(value):
        state['context'] = value
        state['seen_types'].clear()
        cov.switch_context(value)

    def record_type(cls):
        current = state['context']
        if not current or cls in state['seen_types']:
            return
        state['seen_types'].add(cls)
        if cls not in type_keys:
            bases = set(cls.__mro__)
            if issubclass(cls, Dialect):
                for attribute in ('parser_class', 'generator_class', 'tokenizer_class'):
                    component = getattr(cls, attribute, None)
                    if isinstance(component, type):
                        bases.update(component.__mro__)
            type_keys[cls] = tuple(base.__module__ + ':' + base.__qualname__
                                   for base in bases if base.__module__.startswith('sqlglot.'))
        for key in type_keys[cls]:
            type_uses[key].add(current)

    def track_constructor(cls):
        original = cls.__init__
        @functools.wraps(original)
        def initialize(self, *args, **kwargs):
            record_type(type(self))
            return original(self, *args, **kwargs)
        cls.__init__ = initialize

    class Contexts:
        def pytest_collection_modifyitems(self, items):
            state['tests'] = [item.nodeid for item in items]

        @pytest.hookimpl(hookwrapper=True)
        def pytest_runtest_setup(self, item):
            context('@scope:' + item.nodeid.rsplit('::', 1)[0])
            yield
            context('')

        @pytest.hookimpl(hookwrapper=True)
        def pytest_runtest_call(self, item):
            context(item.nodeid)
            yield
            context('')

        @pytest.hookimpl(hookwrapper=True)
        def pytest_runtest_teardown(self, item):
            context('@scope:' + item.nodeid.rsplit('::', 1)[0])
            yield
            context('')

    concurrent.futures.ProcessPoolExecutor = CoverageExecutor
    cov.start()
    comparisons = ComparisonData()
    comparisons.install(root)
    from sqlglot import Dialect, exp
    from sqlglot.parser import Parser
    from sqlglot.generator import Generator
    from sqlglot.tokens import Tokenizer
    for cls in (exp.Expr, Dialect, Parser, Generator, Tokenizer):
        track_constructor(cls)
    original_get = Dialect.get_or_raise.__func__
    @functools.wraps(original_get)
    def get_dialect(cls, dialect):
        result = original_get(cls, dialect)
        record_type(type(result))
        return result
    Dialect.get_or_raise = classmethod(get_dialect)
    status = pytest.main(['-q', '-p', 'no:cacheprovider'], plugins=[Contexts()])
    cov.stop()
    if status != 0:
        raise SystemExit(f'Pristine test suite failed: {status}')
    tests = state['tests']
    indexes = {nodeid: i for i, nodeid in enumerate(tests)}
    pool, interned = [], {}

    def intern(contexts):
        ids = set()
        for value in contexts:
            if value.startswith('@scope:'):
                prefix = value[7:] + '::'
                ids.update(i for test, i in indexes.items() if test.startswith(prefix))
            elif value in indexes:
                ids.add(indexes[value])
        key = tuple(sorted(ids))
        if key not in interned:
            interned[key] = len(pool)
            pool.append(key)
        return interned[key]

    files = {}
    data = cov.get_data()
    if len(data.measured_contexts()) < 2:
        raise RuntimeError('Per-test coverage contexts were not recorded')
    for filename in sorted(data.measured_files()):
        path = Path(filename)
        tree = ast.parse(path.read_text())
        spans = [[min([n.lineno] + [d.lineno for d in getattr(n, 'decorator_list', [])]), n.end_lineno]
                 for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda))]
        lines = {str(line): intern(contexts)
                 for line, contexts in data.contexts_by_lineno(filename).items()}
        relative = path.relative_to(root).as_posix()
        module = relative[:-3].replace('/', '.')
        classes = [[start, end, name, intern(type_uses[module + ':' + name])]
                   for start, end, name in class_scopes(tree)]
        blocks = [[n.lineno, n.end_lineno] for n in ast.walk(tree)
                  if isinstance(n, ast.stmt) and not isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))]
        files[relative] = {'spans': spans, 'blocks': blocks, 'classes': classes,
                           'lines': lines, 'source': path.read_text(),
                           'comparisons': {site: sorted(pairs) for site, pairs in comparisons.sites.get(relative, {}).items()}}
    result = {'version': 2, 'commit': 'eb11aec4be8cb2122afe713a4816662c3f0fb2fa',
              'python': sys.version, 'coverage': coverage.__version__, 'tests': tests,
              'sets': pool, 'files': files, 'operand_types': comparisons.types,
              'fixtures': {name: intern(contexts) for name, contexts in sorted(fixtures.items())}}
    output.write_text(json.dumps(result, separators=(',', ':')))
    print(f'Coverage map: {len(tests)} tests, {len(files)} sources, {len(fixtures)} fixtures, {output.stat().st_size} bytes', file=sys.stderr)


if __name__ == '__main__':
    main()
