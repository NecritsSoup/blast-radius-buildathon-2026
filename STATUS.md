# Blast Radius status

Practice board: 225.4 s (rank 57/131), 0 misses, commit 7e14c6c.

Branch `feature/attr-narrowing-runtime-verify` (not pushed, not submitted):
- Class-constant reader narrowing (offline map addition): sample 128.496 -> 120.120 s.
- Run-time verification of cheap selections: sample 120.120 -> 109.749 s, 0 misses,
  4.8 s container CPU (was 0.69 s), so about 114.6 s total vs about 129.2 s before.
- 60 verifier mutants vs real pytest: 0 misses.
- Dockerfile now installs pinned test deps and fetches sqlglot at eb11aec.

- Linux image built (40 s) and run 3x under the scorer's flags: identical results.

Remaining: commit, push and submit when authorized.
