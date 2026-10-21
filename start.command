#!/bin/sh
set -eu
cd "$(dirname "$0")"
if [ ! -x .venv/bin/python ]; then
  python3 -m venv .venv
fi
if ! .venv/bin/python -c "import fastapi, uvicorn, pydantic, reportlab, fontTools" >/dev/null 2>&1; then
  .venv/bin/python -m pip install -r requirements.txt
fi
.venv/bin/python scripts/install_pdf_font.py || printf '%s\n' 'PDF font installation failed. Web printing remains available; retry scripts/install_pdf_font.py later.'
exec .venv/bin/python run.py "$@"
