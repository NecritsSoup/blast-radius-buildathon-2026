# Blast Radius

Test selection for sqlglot at pinned commit
`eb11aec4be8cb2122afe713a4816662c3f0fb2fa`.

## Submission build

The coverage map is generated **offline** and checked in as `coverage-map.json`.
The Docker image copies `selector.py`, `semantic.py` and that map into the pinned
Python 3.13 image. It does not clone source, install dependencies or run tests.

The first submission timed out because its Dockerfile performed collection during
the image build. That build stage has been removed. Committed maps generated from
the pristine pinned version are allowed by the competition rules.

The map contains source, class/fixture dependencies, operand-domain observations,
its pinned source revision, and the upstream source license. It contains no event
patches, mutation outcomes, sample answers or mutation-kind answer tables.

## Local validation

| Variant | Simulated seconds | Missed sample commits |
| --- | ---: | ---: |
| Official starter | 1614.1 | 0 |
| Statement blocks plus class dependencies | 178.737 | 0 |
| Above plus operand-domain equivalence (default) | 128.496 | 0 |

All 30 public sample commits pass. The standalone, dependency-free runtime was
also checked with Python 3.13.18 on macOS/arm64, using only the three copied files:
128.496 simulated seconds, zero misses. All 42 selector/equivalence regression
tests pass on Python 3.13. The actual Linux Docker image has not been run locally.
The leaderboard uses hidden commits, so these results do not establish a ranking.

Offline collection used the pristine pinned source: 1,621 tests and 25,454
subtests passed. Fresh mutation checks caught all failures in a combined change
and three independent complete-suite checks. A harmful identity-to-equality
change was also checked on a targeted existing test; that test failed and remained
selected. These are development checks, not guarantees for every hidden change.

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
