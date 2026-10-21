"""Check the interpreter and pinned application dependencies without importing the app."""
from importlib import metadata
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
MIN_PYTHON = (3, 12)


def mismatches(requirements, version=None):
    version = version or metadata.version
    problems = []
    for line in requirements.splitlines():
        line = line.strip()
        if not line or line.startswith('#'):
            continue
        match = re.fullmatch(r'([A-Za-z0-9_.-]+)==([A-Za-z0-9_.+-]+)', line)
        if match is None:
            raise ValueError('Unsupported dependency pin: ' + line)
        package, wanted = match.groups()
        try:
            installed = version(package)
        except metadata.PackageNotFoundError:
            installed = None
        if installed != wanted:
            problems.append((package, wanted, installed))
    return problems


def main():
    if sys.version_info[:2] < MIN_PYTHON:
        print('DCSJ requires Python 3.12 or newer. Recreate .venv with a supported interpreter.', file=sys.stderr)
        return 2
    try:
        problems = mismatches((ROOT / 'requirements.txt').read_text(encoding='utf-8'))
    except (OSError, ValueError) as error:
        print(str(error), file=sys.stderr)
        return 2
    if problems:
        for package, wanted, installed in problems:
            print(f'{package}: requires {wanted}; installed {installed or "missing"}')
        return 1
    print('DCSJ pinned application dependencies are ready.')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
