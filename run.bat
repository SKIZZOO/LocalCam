@echo off
setlocal EnableExtensions EnableDelayedExpansion
cd /d "%~dp0"
title LocalCam

set "ROOT=%~dp0"
set "PY="
set "VENV=%ROOT%.venv\Scripts\python.exe"

where python >nul 2>&1
if not errorlevel 1 set "PY=python"
if not defined PY (
  where py >nul 2>&1
  if not errorlevel 1 set "PY=py -3"
)
if not defined PY goto :python_missing

%PY% -c "import sys; raise SystemExit(0 if sys.version_info >= (3,11) else 1)" >nul 2>&1
if errorlevel 1 goto :python_old

if not exist "%ROOT%app.py" goto :project_missing
if not exist "%ROOT%requirements.txt" goto :requirements_missing

echo.
echo ========================================
echo              LOCALCAM
echo ========================================
echo.

if not exist "%VENV%" (
  echo LocalCam has not been installed on this computer yet.
  echo A local Python environment is required.
  echo.
  choice /C YN /N /M "Install the required components now? [Y/N] "
  if errorlevel 2 goto :cancelled
  call :install
  if errorlevel 1 goto :failed
) else (
  set "MISSING="
  for /f "delims=" %%M in ('"%VENV%" -c "import importlib.util; mods=['PIL','psutil','onvif']; print(','.join(m for m in mods if importlib.util.find_spec(m) is None))"') do set "MISSING=%%M"
  if defined MISSING (
    echo Missing Python components: !MISSING!
    echo.
    choice /C YN /N /M "Install or repair the missing components now? [Y/N] "
    if errorlevel 2 goto :cancelled
    call :install
    if errorlevel 1 goto :failed
  ) else (
    echo Python environment is ready.
  )
)

if not exist "%ROOT%config.json" (
  if not exist "%ROOT%config.example.json" goto :config_missing
  copy /y "%ROOT%config.example.json" "%ROOT%config.json" >nul
  if errorlevel 1 goto :config_failed
  echo Created local configuration.
)

where ffmpeg >nul 2>&1
if errorlevel 1 (
  echo.
  echo [NOTICE] FFmpeg was not found on PATH.
  echo Camera preview and recording need FFmpeg.
  echo Install FFmpeg and add it to PATH, or configure ffmpeg_path in Settings.
  echo.
) else (
  echo FFmpeg found.
)

echo.
echo Starting LocalCam...
echo The browser will open automatically.
echo Keep this window open while LocalCam is running.
echo.
"%VENV%" "%ROOT%app.py"
set "EXIT_CODE=%ERRORLEVEL%"
echo.
if not "%EXIT_CODE%"=="0" (
  echo LocalCam stopped with exit code %EXIT_CODE%.
  pause
  exit /b %EXIT_CODE%
)
pause
exit /b 0

:install
echo.
echo Installing Python packages from requirements.txt...
"%VENV%" -m pip install --upgrade pip
if errorlevel 1 exit /b 1
"%VENV%" -m pip install -r "%ROOT%requirements.txt"
if errorlevel 1 exit /b 1
exit /b 0

:python_missing
echo Python 3.11 or newer was not found.
echo Install Python from: https://www.python.org/downloads/windows/
pause
exit /b 1

:python_old
echo Python 3.11 or newer is required.
echo Install or update Python from: https://www.python.org/downloads/windows/
pause
exit /b 1

:project_missing
echo app.py was not found. Run run.bat from the LocalCam folder.
pause
exit /b 1

:requirements_missing
echo requirements.txt was not found. The project files are incomplete.
pause
exit /b 1

:config_missing
echo config.example.json was not found.
pause
exit /b 1

:config_failed
echo Could not create config.json. Check folder permissions.
pause
exit /b 1

:failed
echo.
echo LocalCam setup failed. Review the messages above.
pause
exit /b 1

:cancelled
echo.
echo Setup was cancelled. LocalCam was not started.
pause
exit /b 1
