@echo off
setlocal EnableExtensions

cd /d "%~dp0"
echo Starting Stock Keyworder...

set "PYTHON_EXE="
set "PYTHON_ARGS="

call :FindPython
if errorlevel 1 (
  echo Python 3.9 or newer was not found.
  choice /C YN /M "Install Python automatically with winget now"
  if errorlevel 2 (
    echo Cancelled. Install Python manually from https://www.python.org/downloads/
    pause
    exit /b 1
  )

  call :InstallPython
  if errorlevel 1 (
    echo Automatic Python installation failed.
    echo Install Python manually from https://www.python.org/downloads/
    pause
    exit /b 1
  )

  call :FindPython
  if errorlevel 1 (
    echo Python was installed, but this Command Prompt cannot find it yet.
    echo Close this window and double-click start_windows.bat again.
    pause
    exit /b 1
  )
)

set "PYTHONUTF8=1"

"%PYTHON_EXE%" %PYTHON_ARGS% setup_environment.py --run
if errorlevel 1 (
  echo.
  echo Stock Keyworder exited with an error.
  pause
  exit /b 1
)

pause
exit /b 0

:FindPython
set "PYTHON_EXE="
set "PYTHON_ARGS="

where py >nul 2>nul
if not errorlevel 1 (
  py -3 -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 9) else 1)" >nul 2>nul
  if not errorlevel 1 (
    set "PYTHON_EXE=py"
    set "PYTHON_ARGS=-3"
    exit /b 0
  )
)

for %%P in (
  "%LocalAppData%\Programs\Python\Python314\python.exe"
  "%LocalAppData%\Programs\Python\Python313\python.exe"
  "%LocalAppData%\Programs\Python\Python312\python.exe"
  "%LocalAppData%\Programs\Python\Python311\python.exe"
  "%LocalAppData%\Programs\Python\Python310\python.exe"
  "%LocalAppData%\Programs\Python\Python39\python.exe"
  "%ProgramFiles%\Python314\python.exe"
  "%ProgramFiles%\Python313\python.exe"
  "%ProgramFiles%\Python312\python.exe"
  "%ProgramFiles%\Python311\python.exe"
  "%ProgramFiles%\Python310\python.exe"
  "%ProgramFiles%\Python39\python.exe"
) do (
  if exist "%%~P" (
    "%%~P" -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 9) else 1)" >nul 2>nul
    if not errorlevel 1 (
      set "PYTHON_EXE=%%~P"
      set "PYTHON_ARGS="
      exit /b 0
    )
  )
)

where python >nul 2>nul
if not errorlevel 1 (
  python -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 9) else 1)" >nul 2>nul
  if not errorlevel 1 (
    set "PYTHON_EXE=python"
    set "PYTHON_ARGS="
    exit /b 0
  )
)

exit /b 1

:InstallPython
where winget >nul 2>nul
if errorlevel 1 (
  echo winget was not found on this Windows computer.
  start https://www.python.org/downloads/windows/
  exit /b 1
)

for %%I in (
  Python.Python.3.14
  Python.Python.3.13
  Python.Python.3.12
  Python.Python.3.11
  Python.Python.3.10
  Python.Python.3.9
) do (
  echo Trying %%I...
  winget install --id %%I -e --source winget --scope user --accept-package-agreements --accept-source-agreements
  if not errorlevel 1 exit /b 0

  winget install --id %%I -e --source winget --accept-package-agreements --accept-source-agreements
  if not errorlevel 1 exit /b 0
)

exit /b 1
