@echo off
setlocal EnableExtensions EnableDelayedExpansion
cd /d "%~dp0"
title LocalCam Launcher

set "ROOT=%~dp0"
set "VENV=%ROOT%.venv\Scripts\python.exe"
set "SYSTEM_PY="
set "UI_VERSION=0.9.2"
set "MISSING_FILE=%TEMP%\localcam_missing.txt"

rem Double-click safe: every exit path ends at a visible pause.
call :main
set "EXIT_CODE=%ERRORLEVEL%"
echo.
echo LocalCam launcher finished with exit code %EXIT_CODE%.
pause
exit /b %EXIT_CODE%

:main
call :detect_python
if errorlevel 1 exit /b 1
call :project_checks
if errorlevel 1 exit /b 1
call :check_web_version
if errorlevel 1 exit /b 1

if exist "%VENV%" goto :environment_exists

echo.
echo No LocalCam Python environment was found.
echo Your existing system Python is fine - LocalCam uses a separate .venv, so it will not interfere with Redbot or other projects.
echo.
choice /C YN /N /M "Create the LocalCam environment and install dependencies? [Y/N] "
if errorlevel 2 exit /b 1
call :install
if errorlevel 1 exit /b 1
goto :environment_ready

:environment_exists
echo LocalCam environment found.
"%VENV%" -m pip --version >nul 2>&1
if errorlevel 1 goto :repair_environment

set "MISSING="
"%VENV%" -c "import importlib.util; mods=['PIL','psutil','onvif']; print(','.join(m for m in mods if importlib.util.find_spec(m) is None))" > "%MISSING_FILE%" 2>&1
set /p MISSING=<"%MISSING_FILE%"
del /q "%MISSING_FILE%" >nul 2>&1
if not defined MISSING goto :environment_ready

echo.
echo Missing LocalCam components: !MISSING!
echo.
choice /C YN /N /M "Install the missing components now? [Y/N] "
if errorlevel 2 exit /b 1
call :install
if errorlevel 1 exit /b 1
goto :environment_ready

:repair_environment
echo.
echo The existing LocalCam Python environment is incomplete.
echo.
choice /C YN /N /M "Repair the LocalCam Python environment now? [Y/N] "
if errorlevel 2 exit /b 1
rmdir /s /q "%ROOT%.venv" >nul 2>&1
call :install
if errorlevel 1 exit /b 1

:environment_ready
if not exist "%ROOT%config.json" goto :create_config

goto :check_ffmpeg

:create_config
if not exist "%ROOT%config.example.json" (
  echo config.example.json was not found.
  exit /b 1
)
copy /y "%ROOT%config.example.json" "%ROOT%config.json" >nul 2>&1
if errorlevel 1 (
  echo Could not create config.json. Check folder permissions.
  exit /b 1
)
echo Created LocalCam configuration.

:check_ffmpeg
where ffmpeg >nul 2>&1
if not errorlevel 1 goto :start_localcam

echo.
echo [NOTICE] FFmpeg is not installed or is not on PATH.
echo          LocalCam can open, but camera preview and recording need FFmpeg.
where winget >nul 2>&1
if errorlevel 1 goto :start_localcam
choice /C YN /N /M "Install FFmpeg with Windows winget now? [Y/N] "
if errorlevel 2 goto :start_localcam
winget install --id Gyan.FFmpeg.Shared --exact --accept-package-agreements --accept-source-agreements
if errorlevel 1 echo FFmpeg installation was not completed. Install it manually and add it to PATH.

:start_localcam
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
echo Starting LocalCam...
echo Keep this window open while LocalCam is running.
echo.

if not exist "%VENV%" (
  echo LocalCam Python environment is missing after setup.
  exit /b 1
)
"%VENV%" "%ROOT%app.py"
set "APP_EXIT=%ERRORLEVEL%"
if not "%APP_EXIT%"=="0" (
  echo.
  echo LocalCam stopped with exit code %APP_EXIT%.
  exit /b %APP_EXIT%
)
exit /b 0

:detect_python
set "SYSTEM_PY="
where py >nul 2>&1
if not errorlevel 1 set "SYSTEM_PY=py -3"
if defined SYSTEM_PY goto :python_version
where python >nul 2>&1
if not errorlevel 1 set "SYSTEM_PY=python"
if defined SYSTEM_PY goto :python_version
goto :python_missing

:python_version
%SYSTEM_PY% -c "import sys; raise SystemExit(0 if sys.version_info >= (3,11) else 1)" >nul 2>&1
if not errorlevel 1 exit /b 0
goto :python_old

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

:project_checks
if not exist "%ROOT%app.py" (
  echo app.py was not found. Run run.bat from the LocalCam folder.
  exit /b 1
)
if not exist "%ROOT%requirements.txt" (
  echo requirements.txt was not found. The project files are incomplete.
  exit /b 1
)
if not exist "%ROOT%web\assets\app.css" (
  echo LocalCam web files are missing.
  exit /b 1
)
if not exist "%ROOT%web\login.html" (
  echo LocalCam web files are missing.
  exit /b 1
)
if not exist "%ROOT%web\assets\app.js" (
  echo LocalCam web files are missing.
  exit /b 1
)
exit /b 0

:check_web_version
findstr /C:"/assets/app.css?v=%UI_VERSION%" "%ROOT%web\login.html" >nul 2>&1
if errorlevel 1 goto :stale_project
findstr /C:"/assets/app.css?v=%UI_VERSION%" "%ROOT%web\setup.html" >nul 2>&1
if errorlevel 1 goto :stale_project
findstr /C:"/assets/app.css?v=%UI_VERSION%" "%ROOT%web\index.html" >nul 2>&1
if errorlevel 1 goto :stale_project
findstr /C:"/assets/app.js?v=%UI_VERSION%" "%ROOT%web\index.html" >nul 2>&1
if errorlevel 1 goto :stale_project
exit /b 0

:python_missing
cls
echo.
echo ========================================
echo              LOCALCAM NVR
echo ========================================
echo.
echo Python 3.11 or newer was not found on this computer.
echo LocalCam needs Python to create its private environment.
echo.
where winget >nul 2>&1
if errorlevel 1 goto :python_manual
choice /C YN /N /M "Install Python 3.13 with Windows winget now? [Y/N] "
if errorlevel 2 exit /b 1
echo.
echo Installing Python 3.13...
winget install --id Python.Python.3.13 --exact --accept-package-agreements --accept-source-agreements
if errorlevel 1 (
  echo.
  echo Python installation did not complete successfully.
  exit /b 1
)
echo.
echo Python installation completed.
echo Close this window and run run.bat again so Windows refreshes the Python path.
exit /b 1

:python_old
cls
echo.
echo ========================================
echo              LOCALCAM NVR
echo ========================================
echo.
echo Python 3.11 or newer is required.
echo Your detected Python installation is older than 3.11.
echo.
where winget >nul 2>&1
if errorlevel 1 goto :python_manual
choice /C YN /N /M "Install Python 3.13 with Windows winget now? [Y/N] "
if errorlevel 2 exit /b 1
echo.
echo Installing Python 3.13...
winget install --id Python.Python.3.13 --exact --accept-package-agreements --accept-source-agreements
if errorlevel 1 (
  echo.
  echo Python installation did not complete successfully.
  exit /b 1
)
echo.
echo Python installation completed.
echo Close this window and run run.bat again so Windows refreshes the Python path.
exit /b 1

:python_manual
 echo Windows Package Manager (winget) is not available.
 echo Install Python 3.11 or newer manually from:
 echo https://www.python.org/downloads/windows/
 exit /b 1

:stale_project
 echo.
 echo This LocalCam folder contains an older or mixed web UI.
 echo Expected web UI version: %UI_VERSION%
 echo Download the current main branch from GitHub and replace the project files.
 exit /b 1
