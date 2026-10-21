#!/bin/sh
set -eu
export PYTHONUTF8=1
cd "$(dirname "$0")"
if [ ! -x .venv/bin/python ]; then
  python3 -m venv .venv
fi
if .venv/bin/python scripts/check_dependencies.py; then
  :
else
  dependency_status=$?
  [ "$dependency_status" -eq 1 ] || exit "$dependency_status"
  .venv/bin/python -m pip install -r requirements.txt
  .venv/bin/python scripts/check_dependencies.py
fi
.venv/bin/python scripts/install_pdf_font.py || printf '%s\n' 'PDF font installation failed. Web printing remains available; retry scripts/install_pdf_font.py later.'
exec .venv/bin/python run.py "$@"
