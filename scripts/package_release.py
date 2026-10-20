"""Package only reviewed, Git-tracked public source. Never bundle private assets."""
from pathlib import Path
import sys
import zipfile

from check_public_scope import ROOT, check, git_files

sys.path.insert(0, str(ROOT))
from dcsj import __version__


def main():
    paths = git_files()
    check(paths)
    destination = ROOT / 'dist'
    destination.mkdir(exist_ok=True)
    output = destination / f'DCSJ-Source-{__version__}.zip'
    with zipfile.ZipFile(output, 'w', zipfile.ZIP_DEFLATED) as archive:
        for name in sorted(paths):
            archive.write(ROOT / name, 'DCSJ-Source/' + name)
    print(output)


if __name__ == '__main__':
    main()
