"""Local benchmark. Answers are used only by the supplied scorer afterward."""
import argparse
import importlib.util
import json
import subprocess
import sys
import time
from pathlib import Path

root = Path(__file__).resolve().parent
sys.path.insert(0, str(root))
from selector import select, split

parser = argparse.ArgumentParser()
parser.add_argument('--mode', choices=['function', 'line', 'block'], default='block')
parser.add_argument('--map', default=str(root / 'coverage-map.json'))
args = parser.parse_args()
tests = json.loads((root / 'sample/tests.json').read_text())['tests']
mapping = json.loads(Path(args.map).read_text())
output = root / f'output-{args.mode}.jsonl'
start = time.process_time()
with output.open('w') as stream:
    for patch in sorted((root / 'sample/commits').glob('*.patch')):
        selection = select(patch.read_text(), tests, mapping, args.mode)
        print(f'{patch.stem}: {len(selection)}/{len(tests)} selected', file=sys.stderr)
        stream.write(json.dumps({'commit': patch.stem, 'workers': split(selection)}, separators=(',', ':')) + '\n')
print(f'Local selector CPU: {time.process_time() - start:.3f}s', file=sys.stderr)
subprocess.run([sys.executable, str(root / 'score.py'), str(root / 'sample'),
                str(root / 'sample/answers.json'), str(output)], check=True)
