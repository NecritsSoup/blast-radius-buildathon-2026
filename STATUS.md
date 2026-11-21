# Blast Radius status

Current candidate: statement-block selection plus class/fixture maps and
whole-patch equivalence checks over pristine operand domains.

Completed:
- 128.496 simulated seconds, zero misses on all 30 public sample commits.
- Approximately 0.23 seconds of local selection CPU, excluding loading/startup.
- Conservative equivalence-off mode: 178.737 seconds, zero sample misses.
- 42 regression tests pass.
- 1,621 pristine tests and 25,454 subtests pass with the current collector.
- Latest native collection: 184 seconds; source/class/fixture footprints match
  the previous collector exactly.
- Prior complete-suite mutation checks remain applicable to those footprints.
- A fresh harmful comparison edit retained its failing targeted existing test.
- Dockerfile, local benchmark and validation scripts prepared.

Remaining:
- Validate Docker on Linux/amd64 with Python 3.13 and competition limits.
- Publish a public GitHub repository and submit a commit when authorized to proceed.
- Use official hidden scores to assess generalization.

Nothing has been published or submitted. Read README.md for methods and limitations.
