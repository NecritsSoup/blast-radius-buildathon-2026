# Blast Radius

A coverage- and operand-domain-based test selector for sqlglot at pinned commit
`eb11aec4be8cb2122afe713a4816662c3f0fb2fa`.

## Local results

| Variant | Simulated seconds | Missed sample commits |
| --- | ---: | ---: |
| Official starter | 1614.1 | 0 |
| Full-suite optimized scheduling | 1104.510 | 0 |
| Statement blocks plus class dependencies | 178.737 | 0 |
| Above plus operand-domain equivalence (default) | 128.496 | 0 |

The current candidate reduces sample simulated cost by approximately 92.0%
against the starter and 28.1% against the previous 178.737-second candidate.
Local selection CPU is approximately 0.23 seconds, excluding map loading and
interpreter startup. Container CPU is charged separately by the official scorer.
These results cover all 30 public sample commits. The leaderboard uses hidden
commits, so this result does not establish our ranking.

Validated on macOS/arm64 with Python 3.14:
- 1,621 pristine sqlglot tests and 25,454 subtests pass under the collector.
- 42 selector and equivalence regression tests pass.
- A fresh four-change patch produced seven failing competition tests, all selected.
- Three of those changes were checked independently against the complete suite:
  selections of 1, 16 and 143 tests caught all 1, 2 and 3 failures respectively.
- The new collector preserves exactly the previous line, class and fixture test
  footprints, so those independent selection checks remain applicable.
- A fresh harmful identity-to-equality edit was checked on its targeted existing
  test: the selector retained that test and the test failed as expected.
- The latest native collection completed in 184 seconds; the map is 7.8 MB.

The Linux/amd64 Docker image with Python 3.13 remains unverified. No repository
has been published and no leaderboard submission has been made.

## Collection

The build collects per-test source coverage, fixture reads, shared setup/teardown
contexts, class usage including inheritance, and operand domains at identity
comparisons. Original constructors and dialect lookup methods are wrapped to
observe class usage. Pool tasks run serially under the caller's context with
pickle copies across input/output boundaries, exposing work otherwise hidden
in subprocesses. The C coverage tracer is selected for dynamic-context support.

Identity expressions are instrumented through an import hook to observe operand
types before performing the original operation. The checkout is unchanged.
Custom equality slots remain opaque to equivalence checks; slot and class
identities are retained so replaced descriptors and duplicate class names stay
separate. Unsupported domains are cached conservatively to reduce overhead.
A pristine-suite failure rejects the map. Collection never reads event patches,
sample answers, mutation results or mutation-kind labels.

## Selection

Body changes select consumers of the enclosing statement block, including the
complete guard of a multi-line condition. Unreached blocks need no tests. Class
settings select class consumers; fixture edits select fixture readers. Unknown
dependencies, mismatched patch context and invalid Python syntax fall back to
all tests. Identical complete-file ASTs, including docstrings and type comments,
can be skipped.

For operator edits, the selector additionally checks the complete changed ASTs
against the collected operand domains. It accepts only identity/value-equality
substitutions that agree for default identity-equality objects or the same
canonical string/integer Enum domain with unique raw values. It rejects custom
equality/negation, mixed Enum/string domains, synthetic Enum values, NaN and
unobserved sites. Every changed file must be AST-identical or equivalently
changed before this rule can skip a patch. Additional body or fixture changes
prevent that shortcut. No patch-specific or mutation-kind answer tables exist.

This is a dynamic argument over recorded executions, not a guarantee for every
possible input. Source introspection, unobserved paths/types, global cache state,
instrumentation differences and unseen dependencies may still cause hidden
misses. Passing local checks is not proof of a hidden-data score.

Worker balancing evaluates 1–8 workers in integer milliseconds, including startup
overhead. `SELECT_PROOFS=0` disables operand-based equivalence. `SELECT_MODE=function`
uses broader function scopes. Experimental line mode remains a development option
and is not recommended: it missed a sample failure.

## Reproduce

With Docker:

```sh
make sample
```

Docker builds from the pinned source with the supplied dependency lock,
collects a Linux map and runs under competition limits. The build has a ten-minute
budget. The complete Linux build, including dependencies, remains unmeasured.

Using the local map:

```sh
python3 -m unittest -v test_selector test_semantic
python3 evaluate.py
SELECT_PROOFS=0 python3 evaluate.py
```

Collect a new map with the required dependencies installed:

```sh
PYTHONPATH=/path/to/sqlglot python build_map.py --source /path/to/sqlglot --output coverage-map.json
```

The source must be the pinned commit; Docker enforces the pin. Fresh mutation
checks require the test environment's Python and a pristine `work/sqlglot` checkout:

```sh
python validate_mutations.py
python validate_mutations.py --individual
python validate_identity.py --map coverage-map.json --path sqlglot/expressions/core.py --line 838 --column 15 --test tests/test_expressions.py::TestExprs::test_eq_on_same_instance_short_circuits
```

The second command requires outputs from the first. The last command is a
TARGETED harmful-comparison check, not a full mutated-suite run. All checks create
isolated working copies. No mutation result enters the image or selection map.

Sample answers are used only by the supplied local scorer. The source checkout,
sample data, maps, logs and generated outputs are ignored by Git. The image builds
its own map, independent of the local macOS map.

## Research

The general block-dependency approach was reviewed against
[pytest-testmon's explanation](https://www.testmon.org/blog/determining-affected-tests/)
and its [public repository](https://github.com/tarpas/pytest-testmon).
No testmon source was copied and no other team's competition implementation was used.

## Submission

Before submitting, validate the Linux image. Publish this directory as a public
repository with Dockerfile at the root, then submit its commit hash. Keep submitted
commits and the public repository available until results are published.
