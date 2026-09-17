@echo off
setlocal EnableExtensions EnableDelayedExpansion
cd /d "%~dp0"
title LocalCam Launcher

set "ROOT=%~dp0"
set "PY="

where python >nul 2>&1
if not errorlevel 1 set "PY=python"
if not defined PY (
  where py >nul 2>&1
  if not errorlevel 1 set "PY=py -3"
)
if not defined PY goto :python_missing

%PY% -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)" >nul 2>&1
if errorlevel 1 goto :python_old

if not exist "%ROOT%app.py" goto :project_missing
if not exist "%ROOT%requirements.txt" goto :requirements_missing

if not exist "%ROOT%.venv\Scripts\python.exe" (
  echo.
  echo ========================================
  echo            LOCALCAM FIRST START
  echo ========================================
  echo.
  echo Creating the LocalCam Python environment. This can take a few minutes.
  echo.
  %PY% -m venv "%ROOT%.venv"
  if errorlevel 1 goto :setup_failed

  "%ROOT%.venv\Scripts\python.exe" -m pip install --upgrade pip
  if errorlevel 1 goto :setup_failed

  "%ROOT%.venv\Scripts\python.exe" -m pip install -r "%ROOT%requirements.txt"
  if errorlevel 1 goto :setup_failed

  if not exist "%ROOT%config.json" (
    if not exist "%ROOT%config.example.json" goto :config_missing
    copy /y "%ROOT%config.example.json" "%ROOT%config.json" >nul
    if errorlevel 1 goto :setup_failed
  )
  echo.
  echo LocalCam setup completed.
) else (
  echo.
  echo LocalCam environment found.
)

echo.
echo ========================================
echo              STARTING LOCALCAM
echo ========================================
echo.
echo The LocalCam web interface will open automatically.
echo Keep this window open while LocalCam is running.
echo.
echo Server output:
echo.
"%ROOT%.venv\Scripts\python.exe" "%ROOT%app.py"
set "APP_EXIT=%ERRORLEVEL%"
echo.
if not "%APP_EXIT%"=="0" (
  echo LocalCam stopped with exit code %APP_EXIT%.
  echo Review the messages above for the cause.
  pause
  exit /b %APP_EXIT%
)

echo LocalCam stopped.
pause
exit /b 0

:python_missing
echo.
echo ERROR: Python 3.11 or newer was not found.
echo Install Python from:
echo https://www.python.org/downloads/windows/
echo.
pause
exit /b 1

:python_old
echo.
echo ERROR: Python 3.11 or newer is required.
echo Install or update Python from:
echo https://www.python.org/downloads/windows/
echo.
pause
exit /b 1

:project_missing
echo.
echo ERROR: app.py was not found.
echo Run run.bat from the LocalCam project folder.
pause
exit /b 1

:requirements_missing
echo.
echo ERROR: requirements.txt was not found.
echo The LocalCam project files are incomplete.
pause
exit /b 1

:config_missing
echo.
echo ERROR: config.example.json was not found, so LocalCam cannot create its configuration.
pause
exit /b 1

:setup_failed
echo.
echo ERROR: LocalCam setup failed.
echo Check the command output above, then run run.bat again.
pause
exit /b 1

endlocal
