@echo off
setlocal EnableExtensions
cd /d "%~dp0"

where py >nul 2>&1
if errorlevel 1 (
  echo ERROR: Python Launcher (py.exe) was not found.
  echo Install Python 3.11 or newer from https://www.python.org/downloads/windows/
  echo Make sure the Python Launcher is selected during installation.
  pause
  exit /b 1
)

py -3.11 -c "import sys; print(sys.version)" >nul 2>&1
if errorlevel 1 (
  echo ERROR: Python 3.11 or newer was not found by the Python Launcher.
  echo Install Python 3.11 or newer, then run this installer again.
  pause
  exit /b 1
)

if not exist requirements.txt (
  echo ERROR: requirements.txt is missing. Run this file from the LocalCam folder.
  pause
  exit /b 1
)

if not exist .venv\Scripts\python.exe (
  echo Creating virtual environment...
  py -3.11 -m venv .venv
  if errorlevel 1 goto :failed
) else (
  echo Reusing existing .venv environment...
)

.venv\Scripts\python.exe -m pip install --upgrade pip
if errorlevel 1 goto :failed

.venv\Scripts\python.exe -m pip install -r requirements.txt
if errorlevel 1 goto :failed

if not exist config.json (
  if not exist config.example.json (
    echo ERROR: config.example.json is missing; cannot create config.json.
    goto :failed
  )
  copy /y config.example.json config.json >nul
  if errorlevel 1 goto :failed
  echo Created config.json from the example.
) else (
  echo Existing config.json preserved.
)

echo.
echo LocalCam dependencies installed successfully.
echo Next: run run.bat, or install_service.bat from an Administrator terminal.
pause
exit /b 0

:failed
echo.
echo ERROR: Installation failed. The command above reported an error.
echo Fix the reported issue and run install.bat again; your config.json was not overwritten.
pause
exit /b 1
