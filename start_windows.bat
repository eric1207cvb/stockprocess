@echo off
setlocal

cd /d "%~dp0"
echo Starting Stock Keyworder...

set "PYTHON_LAUNCHER="

where py >nul 2>nul
if not errorlevel 1 (
  py -3 -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 9) else 1)" >nul 2>nul
  if not errorlevel 1 set "PYTHON_LAUNCHER=py -3"
)

if not defined PYTHON_LAUNCHER (
  where python >nul 2>nul
  if not errorlevel 1 (
    python -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 9) else 1)" >nul 2>nul
    if not errorlevel 1 set "PYTHON_LAUNCHER=python"
  )
)

if not defined PYTHON_LAUNCHER (
  echo Python 3.9 or newer was not found. Install Python 3 first:
  echo https://www.python.org/downloads/
  pause
  exit /b 1
)

set "PYTHONUTF8=1"

%PYTHON_LAUNCHER% setup_environment.py --run
if errorlevel 1 (
  echo.
  echo Stock Keyworder exited with an error.
  pause
  exit /b 1
)

pause
