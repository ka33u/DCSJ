"""Fail closed if Git contains private resources; no content is printed."""
from pathlib import Path, PurePosixPath
import subprocess

ROOT = Path(__file__).resolve().parents[1]
TOP_FILES = {'README.md', 'ROADMAP.md', 'THIRD_PARTY.md', '.gitignore', '.env.example',
             'requirements.txt', 'requirements-dev.txt', 'run.py', 'start.command', 'start.bat',
             'Dockerfile', '.dockerignore', 'compose.yaml'}
SCRIPTS = {'bundle_catalog.py', 'package_release.py', 'check_public_scope.py', 'backup.py', 'install_pdf_font.py'}


def allowed(name):
    p = PurePosixPath(name)
    if len(p.parts) == 1:
        return name in TOP_FILES
    if p.parts[0] == 'data':
        return name in {'data/schema.sql', 'data/README.md'}
    if p.parts[0] == 'scripts':
        return len(p.parts) == 2 and p.name in SCRIPTS
    if p.parts[0] == 'dcsj':
        return len(p.parts) == 2 and p.suffix == '.py'
    if p.parts[0] == 'tests':
        return len(p.parts) == 2 and p.suffix == '.py'
    if p.parts[0] == 'frontend':
        return (len(p.parts) == 2 and p.suffix in {'.html', '.css', '.js'}) or name in {
            'frontend/vendor/swagger-ui-bundle.js', 'frontend/vendor/swagger-ui.css', 'frontend/vendor/LICENSE'}
    return name == '.github/workflows/ci.yml'


def check(names):
    denied = [name for name in names if not allowed(name)]
    if denied:
        raise RuntimeError('Private or unreviewed paths in public payload: ' + ', '.join(denied))


def git_files():
    raw = subprocess.check_output(['git', 'ls-files', '-z'], cwd=ROOT)
    names = raw.decode().strip('\0').split('\0')
    return [name for name in names if name]


if __name__ == '__main__':
    paths = git_files()
    check(paths)
    print(f'Public source scope verified: {len(paths)} files; no private resource paths.')
