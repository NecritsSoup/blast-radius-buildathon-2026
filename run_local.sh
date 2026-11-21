#!/bin/sh
# Native run of the image's program on the sample set, then the official scorer.
# Usage: ./run_local.sh /path/to/pinned/sqlglot [python]
set -e
here=$(cd "$(dirname "$0")" && pwd)
py=${2:-python3}
DATA_DIR="$here/sample" SQLGLOT_ROOT="$1" /usr/bin/time -p "$py" "$here/selector.py" > "$here/output.jsonl"
python3 "$here/score.py" "$here/sample" "$here/sample/answers.json" "$here/output.jsonl"
