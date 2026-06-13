@echo off
setlocal

cd /d "%~dp0"
echo Starting Stock Keyworder...

where py >nul 2>nul
if not errorlevel 1 (
  set "PYTHON_LAUNCHER=py"
) else (
  where python >nul 2>nul
  if errorlevel 1 (
    echo Python was not found. Install Python 3 first:
    echo https://www.python.org/downloads/
    pause
    exit /b 1
  )
  set "PYTHON_LAUNCHER=python"
)

%PYTHON_LAUNCHER% -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 9) else 1)" >nul 2>nul
if errorlevel 1 (
  echo Python 3.9 or newer is required.
  echo https://www.python.org/downloads/
  pause
  exit /b 1
)

if not exist ".venv\Scripts\python.exe" (
  echo Creating virtual environment...
  %PYTHON_LAUNCHER% -m venv .venv
)

call ".venv\Scripts\activate.bat"
set "PYTHONUTF8=1"

python -c "import PIL" >nul 2>nul
if errorlevel 1 (
  echo Installing requirements...
  python -m pip install -r requirements.txt
)

python stock_keyworder.py
pause
