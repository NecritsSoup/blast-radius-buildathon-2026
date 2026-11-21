"""Run cheap candidate tests against a patched sqlglot and drop the ones that pass.

Each commit is checked in a forked child that serves the patched .py files from
memory (the disk is read-only) and imports everything else from the pristine
pinned tree. A test is dropped only when the child explicitly reports it passed
(including every subtest). Failures, errors, unexpected successes, import
problems, crashes and timeouts all keep the static selection, so the check can
only remove tests that were observed to pass on the patched code.

Running tests costs CPU seconds, so a commit is checked only when the expected
CPU is below the scheduling cost it could save.
"""
import concurrent.futures
import concurrent.futures.process
import importlib
import importlib.abc
import importlib.util
import json
import linecache
import os
import pickle
import select as _select
import signal
import sys
import time
import unittest

for _name in ('OPENBLAS_NUM_THREADS', 'OMP_NUM_THREADS', 'MKL_NUM_THREADS',
              'NUMEXPR_NUM_THREADS', 'VECLIB_MAXIMUM_THREADS'):
    os.environ.setdefault(_name, '1')

# Tests that read sqlglot's source text, patch the import system, or build
# extensions. Their outcome on an in-memory overlay may differ from pytest.
NEVER_DROP_FILES = {
    'tests/test_typing.py', 'tests/test_docs.py', 'tests/test_sqlglotc.py',
    'tests/test_dialect_imports.py', 'tests/test_integration_loader.py',
    'tests/test_build.py',
}
# Tests that start a fresh interpreter, which would import the pristine files.
NEVER_DROP_TESTS = {'tests/dialects/test_dialect.py::TestDialect::test_lazy_load'}
HEAVY_IMPORTS = {'tests/test_optimizer.py', 'tests/test_executor.py'}
CPU_FACTOR = float(os.environ.get('VERIFY_CPU_FACTOR', '1.3'))
# Development machine to scorer CPU speed (measured costs come from a fast Mac).
MEASURED_FACTOR = float(os.environ.get('VERIFY_MEASURED_FACTOR', '2.0'))
STARTUP = float(os.environ.get('VERIFY_STARTUP', '0.45'))
LAST_CPU = 0.0
BUDGET = float(os.environ.get('VERIFY_BUDGET', '95'))


class _Loader(importlib.abc.Loader):
    def __init__(self, filename, source):
        self.filename, self.source = filename, source

    def create_module(self, spec):
        return None

    def exec_module(self, module):
        exec(compile(self.source, self.filename, 'exec', dont_inherit=True), module.__dict__)

    def get_source(self, name):
        return self.source


class _Overlay(importlib.abc.MetaPathFinder):
    def __init__(self, root, patched):
        self.modules = {}
        for relative, source in patched.items():
            name = relative[:-3].replace('/', '.')
            package = name.endswith('.__init__')
            if package:
                name = name[:-9]
            self.modules[name] = (os.path.join(root, relative), source, package)

    def find_spec(self, name, path=None, target=None):
        hit = self.modules.get(name)
        if hit is None:
            return None
        filename, source, package = hit
        return importlib.util.spec_from_file_location(
            name, filename, loader=_Loader(filename, source),
            submodule_search_locations=[os.path.dirname(filename)] if package else None)


class _InlineExecutor(concurrent.futures.Executor):
    """Process-pool stand-in: runs tasks here so they see the patched overlay.

    Arguments and results still cross a pickle boundary, like a real pool.
    """
    def __init__(self, max_workers=None, mp_context=None, initializer=None, initargs=(), **kwargs):
        if initializer is not None:
            initializer(*pickle.loads(pickle.dumps(initargs)))

    def submit(self, fn, /, *args, **kwargs):
        future = concurrent.futures.Future()
        try:
            args, kwargs = pickle.loads(pickle.dumps((args, kwargs)))
            future.set_result(pickle.loads(pickle.dumps(fn(*args, **kwargs))))
        except BaseException as error:  # delivered through the future, like a pool
            future.set_exception(error)
        return future

    def shutdown(self, wait=True, *, cancel_futures=False):
        pass


class _Result(unittest.TestResult):
    def __init__(self, channel, stop_on_failure):
        super().__init__()
        self.channel, self.stop_on_failure = channel, stop_on_failure
        self.bad = set()

    def _key(self, test):
        return getattr(test, '_br_key', None)

    def _fail(self, test):
        key = self._key(test)
        if key is not None:
            self.bad.add(key)
            os.write(self.channel, f'F\t{key}\n'.encode())
        if self.stop_on_failure or key is None:
            self.stop()

    def addError(self, test, err):
        self._fail(test)

    def addFailure(self, test, err):
        self._fail(test)

    def addUnexpectedSuccess(self, test):
        self._fail(test)

    def addSubTest(self, test, subtest, err):
        if err is not None:
            self._fail(test)

    def _pass(self, test):
        key = self._key(test)
        if key is not None and key not in self.bad:
            os.write(self.channel, f'P\t{key}\n'.encode())

    def addSuccess(self, test):
        self._pass(test)

    def addSkip(self, test, reason):
        self._pass(test)

    def addExpectedFailure(self, test, err):
        self._pass(test)


def _child(root, patched, ordered, channel, stop_on_failure):
    devnull = os.open(os.devnull, os.O_WRONLY)
    os.dup2(devnull, 1)
    os.dup2(devnull, 2)
    sys.dont_write_bytecode = True
    concurrent.futures.ProcessPoolExecutor = _InlineExecutor
    concurrent.futures.process.ProcessPoolExecutor = _InlineExecutor
    os.chdir(root)
    sys.path.insert(0, root)
    sys.meta_path.insert(0, _Overlay(root, patched))
    for relative, source in patched.items():
        filename = os.path.join(root, relative)
        linecache.cache[filename] = (len(source), None, source.splitlines(True), filename)
    suite = unittest.TestSuite()
    for key in ordered:
        file, cls, method = key.split('::')
        module = importlib.import_module(file[:-3].replace('/', '.'))
        case = getattr(module, cls)(method)
        case._br_key = key
        suite.addTest(case)
    suite.run(_Result(channel, stop_on_failure))
    if os.environ.get('VERIFY_DEBUG'):
        loaded = {name: type(getattr(getattr(sys.modules[name], '__spec__', None), 'loader', None)).__name__
                  for name in sys.modules if name.startswith('sqlglot.dialects.t')}
        os.write(channel, f'D\t{loaded}\n'.encode())


def run_child(root, patched, ordered, timeout, stop_on_failure=True):
    """Return {test id: True (passed) | False (failed)}; unreported ids are absent."""
    read, write = os.pipe()
    sys.stdout.flush()
    sys.stderr.flush()
    pid = os.fork()
    if pid == 0:
        os.close(read)
        code = 0
        try:
            signal.signal(signal.SIGALRM, signal.SIG_DFL)
            signal.alarm(max(1, int(timeout) + 1))
            _child(root, patched, ordered, write, stop_on_failure)
        except BaseException:
            code = 1
        finally:
            os._exit(code)
    os.close(write)
    outcome, buffer = {}, b''
    deadline = time.monotonic() + timeout
    try:
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            ready, _, _ = _select.select([read], [], [], remaining)
            if not ready:
                break
            chunk = os.read(read, 65536)
            if not chunk:
                break
            buffer += chunk
    finally:
        os.close(read)
        try:
            os.kill(pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        _, _, usage = os.wait4(pid, 0)
        global LAST_CPU
        LAST_CPU = usage.ru_utime + usage.ru_stime
    if os.environ.get("VERIFY_DEBUG"):
        print("RAW", patched.keys(), buffer.decode(errors="replace"), file=sys.stderr)
    for line in buffer.decode(errors='replace').splitlines():
        status, _, key = line.partition('\t')
        if status == 'F':
            outcome[key] = False
        elif status == 'P' and outcome.get(key) is not False:
            outcome[key] = True
    return outcome


def schedule_cost(tests, split):
    workers = split(tests)
    seconds = {t['number']: round(t['seconds'] * 1000) for t in tests}
    if not workers:
        return 0
    return (max(sum(seconds[n] for n in w) for w in workers) + 2000 * len(workers)) / 1000


def eligible(patch_files, selection):
    if not selection:
        return False
    for path in patch_files:
        if not path.endswith('.py') or not (path.startswith('sqlglot/') or path.startswith('tests/')):
            return False
        if path.endswith('conftest.py'):
            return False
    return all(t['id'].count('::') == 2 for t in selection)


def estimate_cpu(candidates, costs):
    """Expected CPU to run candidates: measured pristine CPU (scaled for a
    slower scorer) or the recorded duration, whichever is larger, plus each
    distinct class's import/setUpClass cost and the child's startup."""
    test_cpu, class_cpu = (costs or {}).get('test', {}), (costs or {}).get('class', {})
    total = STARTUP
    for t in candidates:
        total += CPU_FACTOR * max(MEASURED_FACTOR * (test_cpu.get(t['id']) or 0.0), t['seconds'])
    for key in {t['id'].rsplit('::', 1)[0] for t in candidates}:
        total += MEASURED_FACTOR * class_cpu.get(key, 0.0)
    return total


def refine(jobs, root, split, log=None, costs=None):
    """jobs: list of (commit, {path: patched source}, selection). Returns {commit: selection}.

    costs: optional {'test': {id: cpu seconds}, 'class': {file::Class: seconds}}
    measured offline on the pristine suite (measure_cpu.py).
    """
    start = time.monotonic()
    refined = {}
    preloaded = False
    plans = []
    for commit, patched, selection in jobs:
        pinned = lambda t: t['file'] in NEVER_DROP_FILES or t['id'] in NEVER_DROP_TESTS
        keep = [t for t in selection if pinned(t)]
        candidates = sorted((t for t in selection if not pinned(t)),
                            key=lambda t: (t['seconds'], t['number']))
        if not candidates:
            continue
        cost = schedule_cost(selection, split)
        estimate = estimate_cpu(candidates, costs)
        saving = cost - schedule_cost(keep, split)
        if estimate < saving:
            plans.append((saving - estimate, commit, patched, selection, keep, candidates, estimate))
    # Most valuable first, so a tight budget is spent where it pays.
    plans.sort(key=lambda plan: -plan[0])
    for _, commit, patched, selection, keep, candidates, estimate in plans:
        remaining = BUDGET - (time.monotonic() - start)
        if remaining < 5:
            break
        if not preloaded and any(t['file'] in HEAVY_IMPORTS for t in candidates):
            before = time.process_time()
            try:
                import duckdb  # noqa: F401
                import pandas  # noqa: F401
            except Exception:
                pass
            preloaded = True
            if log:
                log(f'preload cpu {time.process_time() - before:.2f}s')
        timeout = min(remaining - 2, max(6.0, 4 * estimate))
        outcome = run_child(root, patched, [t['id'] for t in candidates], timeout)
        passed = {key for key, ok in outcome.items() if ok}
        refined[commit] = [t for t in selection if t['id'] not in passed]
        if log:
            log(f'verify {commit}: {len(selection)} -> {len(refined[commit])} '
                f'(est {estimate:.2f}s, cpu {LAST_CPU:.2f}s, {len(passed)} passed, '
                f'{sum(not v for v in outcome.values())} failed)')
    return refined
