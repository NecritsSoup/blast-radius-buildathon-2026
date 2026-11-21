"""Generate a fresh multi-file change and check selection against actual pytest failures.

This development-only check never reads sample answers and never contributes
mutation outputs to the submission image or coverage map.
"""
import ast
import difflib
import json
import os
import random
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def runner(source, report, targets=()):
    import pytest
    failed, errors = set(), set()
    class Reports:
        def pytest_runtest_logreport(self, report):
            if report.failed:
                failed.add(report.nodeid)
        def pytest_collectreport(self, report):
            if report.failed:
                errors.add(report.nodeid)
    os.chdir(source)
    sys.path.insert(0, str(source))
    result = pytest.main(['-q', '-p', 'no:cacheprovider', *targets], plugins=[Reports()])
    Path(report).write_text(json.dumps({'exit_code': int(result), 'failed': sorted(failed),
                                      'collection_errors': sorted(errors)}))
    return 0


def validate_individual():
    from selector import select
    mapping = json.loads((ROOT / 'coverage-map.json').read_text())
    tests = json.loads((ROOT / 'sample/tests.json').read_text())['tests']
    metadata = json.loads((ROOT / 'work/heldout-summary.json').read_text())
    full_patch = (ROOT / 'work/heldout.patch').read_text()
    chunks = ['diff --git ' + chunk for chunk in full_patch.split('diff --git ')[1:]]
    jobs = []
    # The first three include two body changes and one specific class setting.
    # The fourth shared base-class change already selected nearly every test.
    for index, mutation in enumerate(metadata['mutations'][:3]):
        name = mutation[0]
        patch = next(chunk for chunk in chunks if chunk.splitlines()[0] == f'diff --git a/{name} b/{name}')
        source = ROOT / f'work/heldout-case-{index}'
        if source.exists():
            shutil.rmtree(source)
        shutil.copytree(ROOT / 'work/sqlglot', source,
                        ignore=shutil.ignore_patterns('__pycache__', '.pytest_cache', '.git'))
        shutil.copyfile(ROOT / 'work/heldout-source' / name, source / name)
        selection = {t['id'] for t in select(patch, tests, mapping, 'block')}
        report = ROOT / f'work/heldout-case-{index}.json'
        log = (ROOT / f'work/heldout-case-{index}.log').open('w')
        env = dict(os.environ, PYTHONPATH=str(source), SKIP_INTEGRATION='0', PYTHON_CPU_COUNT='2')
        process = subprocess.Popen([sys.executable, str(Path(__file__).resolve()), '--runner', str(source), str(report)],
                                   env=env, stdout=log, stderr=subprocess.STDOUT)
        jobs.append((process, log, report, name, selection))
        print(f'Started independent case: {name}; {len(selection)} selected', flush=True)
    official = {test['id'] for test in tests}
    summaries = []
    for process, log, report, name, selected in jobs:
        process.wait(timeout=180)
        log.close()
        if process.returncode:
            raise RuntimeError(f'Validation runner failed: {name}')
        result = json.loads(report.read_text())
        failed = set(result['failed']) & official
        missed = failed - selected
        summary = {'source': name, 'selected': len(selected), 'actual_failed': len(failed),
                   'missed': sorted(missed), 'collection_errors': result['collection_errors'],
                   'pytest_exit_code': result['exit_code']}
        summaries.append(summary)
        print(json.dumps(summary), flush=True)
    (ROOT / 'work/individual-summary.json').write_text(json.dumps(summaries, indent=2))
    if any(item['missed'] or item['collection_errors'] or item['pytest_exit_code'] not in (0, 1) for item in summaries):
        raise SystemExit('Independent mutation validation failed')


def main():
    from selector import select
    source = ROOT / 'work/sqlglot'
    target = ROOT / 'work/heldout-source'
    if target.exists():
        shutil.rmtree(target)
    shutil.copytree(source, target, ignore=shutil.ignore_patterns('__pycache__', '.pytest_cache', '.git'))
    mapping = json.loads((ROOT / 'coverage-map.json').read_text())
    tests = json.loads((ROOT / 'sample/tests.json').read_text())['tests']
    comparisons, settings = [], []
    for name, info in sorted(mapping['files'].items()):
        tree = ast.parse(info['source'])
        lines = info['source'].splitlines(keepends=True)
        for node in ast.walk(tree):
            if (isinstance(node, ast.Compare) and len(node.ops) == 1
                    and isinstance(node.ops[0], ast.Eq) and node.lineno == node.end_lineno):
                users = mapping['sets'][info['lines'].get(str(node.lineno), 0)]
                if 0 < len(users) <= 8:
                    start, end = node.left.end_col_offset, node.comparators[0].col_offset
                    if '==' in lines[node.lineno - 1][start:end]:
                        comparisons.append((name, node.lineno, start, end, 'comparison'))
            if isinstance(node, ast.ClassDef):
                for statement in node.body:
                    if isinstance(statement, (ast.Assign, ast.AnnAssign)):
                        value = statement.value
                        if isinstance(value, ast.Constant) and isinstance(value.value, bool):
                            settings.append((name, value.lineno, value.col_offset, value.end_col_offset, 'setting'))
    randomizer = random.Random(20261121)
    randomizer.shuffle(comparisons)
    randomizer.shuffle(settings)
    chosen, used = [], set()
    for candidates in (comparisons, settings):
        count = 0
        for candidate in candidates:
            if candidate[0] not in used:
                chosen.append(candidate)
                used.add(candidate[0])
                count += 1
                if count == 2:
                    break
    if len(chosen) != 4:
        raise RuntimeError('Not enough independent mutation candidates')
    patch_parts = []
    for name, line, start, end, kind in chosen:
        old = mapping['files'][name]['source'].splitlines(keepends=True)
        new = old.copy()
        middle = old[line - 1][start:end]
        replacement = middle.replace('==', '!=', 1) if kind == 'comparison' else ('False' if middle == 'True' else 'True')
        new[line - 1] = old[line - 1][:start] + replacement + old[line - 1][end:]
        (target / name).write_text(''.join(new))
        patch_parts.append(f'diff --git a/{name} b/{name}\n' + ''.join(difflib.unified_diff(old, new, fromfile='a/' + name, tofile='b/' + name)))
    patch = ''.join(patch_parts)
    (ROOT / 'work/heldout.patch').write_text(patch)
    selected = select(patch, tests, mapping, 'block')
    selected_ids = {test['id'] for test in selected}
    report = ROOT / 'work/heldout-report.json'
    env = dict(os.environ, PYTHONPATH=str(target), SKIP_INTEGRATION='0')
    with (ROOT / 'work/heldout-pytest.log').open('w') as log:
        subprocess.run([sys.executable, str(Path(__file__).resolve()), '--runner', str(target), str(report)],
                       env=env, stdout=log, stderr=subprocess.STDOUT, timeout=180, check=True)
    results = json.loads(report.read_text())
    if results['collection_errors']:
        raise RuntimeError(f"Mutation caused collection errors: {results['collection_errors']}")
    # Ignore tests explicitly absent from the competition's numbered test list.
    official = {test['id'] for test in tests}
    failed = set(results['failed']) & official
    missed = failed - selected_ids
    summary = {'mutations': chosen, 'selected': len(selected), 'actual_failed': len(failed),
               'missed': sorted(missed), 'pytest_exit_code': results['exit_code']}
    (ROOT / 'work/heldout-summary.json').write_text(json.dumps(summary, indent=2))
    print(json.dumps(summary, indent=2))
    if missed or not failed or results['exit_code'] not in (0, 1):
        raise SystemExit('Mutation validation did not pass')


if __name__ == '__main__':
    if len(sys.argv) > 1 and sys.argv[1] == '--runner':
        raise SystemExit(runner(Path(sys.argv[2]), Path(sys.argv[3]), sys.argv[4:]))
    if '--individual' in sys.argv:
        validate_individual()
    else:
        main()
