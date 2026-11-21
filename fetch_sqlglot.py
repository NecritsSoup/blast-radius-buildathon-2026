"""Build step: fetch sqlglot at the pinned commit for run-time verification.

The archive's content is checked against a digest of the pinned tree's
sqlglot/ and tests/ files (file paths and contents only, so archive packaging
cannot change it). On any failure the tree is removed and the selector runs
without verification instead of failing the build.
"""
import hashlib
import io
import shutil
import sys
import tarfile
import urllib.request
from pathlib import Path

COMMIT = 'eb11aec4be8cb2122afe713a4816662c3f0fb2fa'
URL = f'https://github.com/tobymao/sqlglot/archive/{COMMIT}.tar.gz'
TREE = 'e514bf38e738b6ef3c5426ad81242103fbff2578eb63ea0035838afe8d2ef806'


def digest(root):
    h = hashlib.sha256()
    for sub in ('sqlglot', 'tests'):
        for path in sorted((root / sub).rglob('*')):
            if path.is_file() and '__pycache__' not in path.parts:
                h.update(path.relative_to(root).as_posix().encode() + b'\0'
                         + hashlib.sha256(path.read_bytes()).digest())
    return h.hexdigest()


def main():
    target = Path(sys.argv[1] if len(sys.argv) > 1 else '/app/sqlglot')
    staging = target.with_name(target.name + '.download')
    try:
        data = urllib.request.urlopen(URL, timeout=120).read()
        shutil.rmtree(staging, ignore_errors=True)
        staging.mkdir(parents=True)
        with tarfile.open(fileobj=io.BytesIO(data), mode='r:gz') as archive:
            archive.extractall(staging, filter='data')
        root = staging / f'sqlglot-{COMMIT}'
        found = digest(root)
        if found != TREE:
            raise RuntimeError(f'tree digest {found} != {TREE}')
        shutil.rmtree(target, ignore_errors=True)
        target.parent.mkdir(parents=True, exist_ok=True)
        root.rename(target)
        print(f'sqlglot {COMMIT[:7]} ready at {target}')
    except Exception as error:
        shutil.rmtree(target, ignore_errors=True)
        print(f'WARNING: verification disabled, sqlglot fetch failed: {error!r}')
    finally:
        shutil.rmtree(staging, ignore_errors=True)


if __name__ == '__main__':
    main()
