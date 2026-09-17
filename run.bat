@echo off
setlocal EnableExtensions EnableDelayedExpansion
cd /d "%~dp0"
title LocalCam Launcher

set "ROOT=%~dp0"
set "VENV=%ROOT%.venv\Scripts\python.exe"
set "SYSTEM_PY="
set "UI_VERSION=0.8.1"

where py >nul 2>&1
if not errorlevel 1 set "SYSTEM_PY=py -3"
if not defined SYSTEM_PY (
  where python >nul 2>&1
  if not errorlevel 1 set "SYSTEM_PY=python"
)
if not defined SYSTEM_PY goto :python_missing

%SYSTEM_PY% -c "import sys; raise SystemExit(0 if sys.version_info >= (3,11) else 1)" >nul 2>&1
if errorlevel 1 goto :python_old

if not exist "%ROOT%app.py" goto :project_missing
if not exist "%ROOT%requirements.txt" goto :requirements_missing
if not exist "%ROOT%web\assets\app.css" goto :web_missing
if not exist "%ROOT%web\login.html" goto :web_missing

cls
echo.
echo ========================================
echo              LOCALCAM NVR
echo ========================================
echo.
echo Windows launcher and environment check
echo.
echo Project folder: %ROOT%
echo Expected web UI:  %UI_VERSION%
echo.

findstr /C:"/assets/app.css?v=%UI_VERSION%" "%ROOT%web\login.html" >nul 2>&1
if errorlevel 1 goto :stale_project
findstr /C:"/assets/app.css?v=%UI_VERSION%" "%ROOT%web\index.html" >nul 2>&1
if errorlevel 1 goto :stale_project

if not exist "%VENV%" (
  echo No LocalCam Python environment was found.
  echo Your existing system Python is fine - LocalCam uses a separate .venv, so it will not interfere with Redbot or other projects.
  echo.
  choice /C YN /N /M "Create the LocalCam environment and install dependencies? [Y/N] "
  if errorlevel 2 goto :cancelled
  call :install
  if errorlevel 1 goto :failed
) else (
  echo LocalCam environment found.
  "%VENV%" -m pip --version >nul 2>&1
  if errorlevel 1 (
    echo The existing LocalCam environment is incomplete.
    echo.
    choice /C YN /N /M "Repair the LocalCam Python environment now? [Y/N] "
    if errorlevel 2 goto :cancelled
    rmdir /s /q "%ROOT%.venv" >nul 2>&1
    call :install
    if errorlevel 1 goto :failed
  ) else (
    set "MISSING="
    for /f "delims=" %%M in ('"%VENV%" -c "import importlib.util; mods=['PIL','psutil','onvif']; print(','.join(m for m in mods if importlib.util.find_spec(m) is None))"') do set "MISSING=%%M"
    if defined MISSING (
      echo Missing LocalCam components: !MISSING!
      echo.
      choice /C YN /N /M "Install the missing components now? [Y/N] "
      if errorlevel 2 goto :cancelled
      call :install
      if errorlevel 1 goto :failed
    ) else (
      echo Python environment is ready.
    )
  )
)

if not exist "%ROOT%config.json" (
  if not exist "%ROOT%config.example.json" goto :config_missing
  copy /y "%ROOT%config.example.json" "%ROOT%config.json" >nul
  if errorlevel 1 goto :config_failed
  echo Created LocalCam configuration.
)

echo.
where ffmpeg >nul 2>&1
if errorlevel 1 (
  echo [NOTICE] FFmpeg is not installed or is not on PATH.
  echo          LocalCam can open, but camera preview and recording need FFmpeg.
  where winget >nul 2>&1
  if not errorlevel 1 (
    choice /C YN /N /M "Install FFmpeg with Windows winget now? [Y/N] "
    if not errorlevel 1 (
      winget install --id Gyan.FFmpeg.Shared --exact --accept-package-agreements --accept-source-agreements
      if errorlevel 1 echo FFmpeg installation was not completed. Install it manually and add it to PATH.
    )
  )
) else (
  echo FFmpeg found.
)

echo.
echo ========================================
echo              STARTING LOCALCAM
echo ========================================
echo.
echo Opening the LocalCam web interface...
echo Keep this window open while LocalCam is running.
echo.
"%VENV%" "%ROOT%app.py"
set "EXIT_CODE=%ERRORLEVEL%"
echo.
if not "%EXIT_CODE%"=="0" (
  echo LocalCam stopped with exit code %EXIT_CODE%.
  echo Review the error above.
  pause
  exit /b %EXIT_CODE%
)

pause
exit /b 0

:install
if not exist "%VENV%" (
  echo.
  echo Creating LocalCam virtual environment...
  %SYSTEM_PY% -m venv "%ROOT%.venv"
  if errorlevel 1 (
    echo Could not create the LocalCam virtual environment.
    echo Make sure the installed Python includes venv support.
    exit /b 1
  )
)

echo.
echo Installing Python packages from requirements.txt...
"%VENV%" -m ensurepip --upgrade
if errorlevel 1 exit /b 1
"%VENV%" -m pip install --upgrade pip
if errorlevel 1 exit /b 1
"%VENV%" -m pip install -r "%ROOT%requirements.txt"
if errorlevel 1 exit /b 1
exit /b 0

:python_missing
echo.
echo Python 3.11 or newer was not found.
echo Install Python from:
echo https://www.python.org/downloads/windows/
pause
exit /b 1

:python_old
echo.
echo Python 3.11 or newer is required.
echo Install or update Python from:
echo https://www.python.org/downloads/windows/
pause
exit /b 1

:project_missing
echo.
echo app.py was not found. Run run.bat from the LocalCam folder.
pause
exit /b 1

:requirements_missing
echo.
echo requirements.txt was not found. The project files are incomplete.
pause
exit /b 1

:web_missing
echo.
echo LocalCam web files are missing.
echo Re-download the current project from GitHub and run run.bat again.
pause
exit /b 1

:stale_project
echo.
echo This LocalCam folder contains an older web UI.
echo Expected web UI version: %UI_VERSION%
echo.
echo Replace this project folder with the latest copy from GitHub.
echo Your .venv, config.json, database and recordings can be kept separately.
pause
exit /b 1

:config_missing
echo.
echo config.example.json was not found.
pause
exit /b 1

:config_failed
echo.
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
