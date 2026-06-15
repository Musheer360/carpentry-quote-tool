@echo off
REM ============================================================
REM  Carpentry Quote Tool - local Windows launcher
REM  Double-click this file. It sets up Python deps the first
REM  time, starts the backend, and opens the app in your browser.
REM ============================================================
setlocal
cd /d "%~dp0"

where py >nul 2>nul
if %errorlevel%==0 ( set PY=py ) else ( set PY=python )

if not exist ".venv\" (
  echo Creating virtual environment ^(first run only^)...
  %PY% -m venv .venv
)

call ".venv\Scripts\activate.bat"

echo Installing/updating dependencies...
python -m pip install --quiet --upgrade pip
python -m pip install --quiet -r requirements.txt

echo Starting Carpentry Quote Tool...
start "" http://127.0.0.1:5000
python api\index.py

endlocal
