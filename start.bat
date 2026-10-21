@echo off
set "PYTHONUTF8=1"
cd /d "%~dp0"
if not exist .venv\Scripts\python.exe (
  py -3 -m venv .venv
  if errorlevel 1 exit /b 1
)
.venv\Scripts\python.exe scripts\check_dependencies.py
if errorlevel 2 exit /b 2
if errorlevel 1 (
  .venv\Scripts\python.exe -m pip install -r requirements.txt
  if errorlevel 1 exit /b 1
  .venv\Scripts\python.exe scripts\check_dependencies.py
  if errorlevel 1 exit /b 1
)
.venv\Scripts\python.exe scripts\install_pdf_font.py
if errorlevel 1 echo PDF font installation failed. Web printing remains available; retry scripts/install_pdf_font.py later.
.venv\Scripts\python.exe run.py %*
