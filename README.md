# Blast Radius

Test selection for sqlglot at pinned commit
`eb11aec4be8cb2122afe713a4816662c3f0fb2fa`.

## Submission build

The coverage map is generated **offline** and checked in as `coverage-map.json`
(`build_map.py`, then `augment_map.py` for class-constant readers). The image
installs the test dependencies pinned by the organisers' `requirements.lock`,
fetches sqlglot at the pinned commit from GitHub (content checked against a
tree digest in `fetch_sqlglot.py`) and precompiles it. No tests run during the
build. If the fetch fails, the build still succeeds and the selector runs
without run-time verification.

The map contains source, class/fixture dependencies, class-constant reader
sets, operand-domain observations, its pinned source revision and the upstream
license. It contains no event patches, mutation outcomes, sample answers or
answer tables keyed by patch or edit kind.

## Local validation

| Variant | Simulated seconds | Missed sample commits |
| --- | ---: | ---: |
| Official starter | 1614.1 | 0 |
| Statement blocks plus class dependencies | 178.737 | 0 |
| Above plus operand-domain equivalence (practice submission, 225.4 s on the board) | 128.496 | 0 |
| Above plus class-constant reader narrowing | 120.120 | 0 |
| Above plus run-time verification (default) | 109.749 | 0 |

Linux image (`linux/amd64` under Rosetta via Colima), built from scratch in
40 s and run three times with the scorer's exact `docker run` flags: 109.749 s
simulated, 0 misses, 4.8 s container CPU each run (cgroup `cpu.stat`). For
comparison the practice submission's image used 0.69 s on the sample in the same
setup (1.23 s on the board's hidden batch).

`measure_cpu.py` stores each pristine test's in-process CPU and each class's
setup CPU in the map. Tests that use process pools, or classes with costly
setup, cost several times their recorded duration when run in one process, so
the verifier's go/no-go estimate uses the measured figures.

Mutation checks against real pytest (spawned pools, files on disk):
- `validate_verify.py`: 60 random single-expression mutants in covered
  functions where the verifier runs: 0 verifier misses, 8 all-pass commits
  emptied.
- `validate_constants.py`: random value changes to narrowable class constants.
  The first round (40 mutants) missed 6, all `Dialects` enum members. Enum,
  dataclass and namedtuple members are now never narrowed. A fresh round with
  another seed: 0 misses in 50 mutants, 42 of which broke at least one test.

## Method

Body changes select consumers of enclosing statement blocks, including complete
multi-line guards. Class settings use class-consumer maps; fixture changes use
reader maps. Unknown dependencies, mismatched patch context and invalid Python
syntax fall back to all tests. Worker balancing evaluates 1–8 workers with startup
costs in integer milliseconds.

A whole patch can be skipped when every changed file has an identical AST or
only operator substitutions that agree across its recorded operand domains.
The latter accepts default identity-equality objects and the same canonical
string/integer Enum domain with unique raw values. Custom equality, mixed domains,
synthetic Enum values, NaN and unobserved sites are rejected. Other edits in the
patch prevent this shortcut. `SELECT_PROOFS=0` disables operand-based equivalence;
`SELECT_MODE=function` selects broader function scopes.

This is a dynamic argument over pristine observations, not a proof for all inputs.
Unobserved dependencies/types, global state, source introspection and platform
or instrumentation differences remain limitations. The checked-in map was collected
on macOS with Python 3.14 and the selector was separately tested on Python 3.13.


### Class constants

An edit to an UPPER_CASE class constant used to select every test that
instantiated the class. `augment_map.py` records, from the pristine source and
coverage, which statements read each constant (attribute loads, bare names and
string literals such as `getattr`/`__dict__` keys) and which tests executed
them. When a class differs only in such assignments, the selector picks those
readers. It falls back to class consumers when any read of the name runs at
import time (metaclasses, class bodies, derived tables), when the class also
changed elsewhere, or when a new value uses a name or attribute path that does
not already appear in the file.

### Run-time verification (`verify.py`)

For Python-only patches whose static selection is cheap relative to its
schedule cost, a forked child serves the patched files from memory, replaces
process pools with an in-process pickling executor so pool work sees the
patch, and runs the candidate tests with unittest, cheapest first, stopping at
the first failure. Tests that explicitly pass are dropped; failures, errors,
unexpected successes, import errors, crashes and timeouts keep the static
selection. Tests that read source text, patch imports or start a fresh
interpreter are never dropped. A 95 s wall budget keeps the batch within the
run limit, and numeric libraries are single-threaded because CPU is charged.

## Reproduce

```sh
make sample
python3 -m unittest -v test_selector test_semantic
python3 evaluate.py
SELECT_PROOFS=0 python3 evaluate.py
```

`make sample` builds the small runtime image and scores it on a local sample set.
The dataset is mounted at run time and is not committed.

To regenerate the map OFFLINE in an environment with the test dependencies:

```sh
PYTHONPATH=/path/to/sqlglot python build_map.py --source /path/to/sqlglot --output coverage-map.json
```

Use the pinned source revision and `requirements.lock`, plus `coverage==7.17.4`.
Review the regenerated map and validate it before committing it. The collector
wraps constructors and dialect lookup, records identity-comparison operand types
through an import hook, and runs pool tasks serially with pickle copies so their
coverage stays in the correct context. A pristine-suite failure rejects the map.

The general dependency-block approach was reviewed against
[pytest-testmon](https://www.testmon.org/blog/determining-affected-tests/).
No testmon source or another team's implementation was copied.

## Submission

Keep `Dockerfile`, `selector.py`, `semantic.py` and `coverage-map.json` at the root
of the public repository. Submit the NEW commit containing the corrected Dockerfile
and map. Keep the repository and submitted commits available through results.
