@echo off
setlocal EnableExtensions
cd /d "%~dp0"
title LocalCam Launcher

set "ROOT=%~dp0"
set "VENV=%ROOT%.venv\Scripts\python.exe"
set "SYSTEM_PY="
set "UI_VERSION=0.9.0"

rem ================================================================
rem LocalCam Windows launcher
rem No CALL-to-label flow is used here so double-click startup is robust.
rem ================================================================

cls
echo.
echo ========================================
echo              LOCALCAM NVR
echo ========================================
echo.
echo Windows launcher and environment check
echo.
echo Project folder: %ROOT%
echo.

rem --- Find a usable system Python --------------------------------
where py >nul 2>&1
if not errorlevel 1 set "SYSTEM_PY=py -3"
if not defined SYSTEM_PY (
    where python >nul 2>&1
    if not errorlevel 1 set "SYSTEM_PY=python"
)
if defined SYSTEM_PY goto PYTHON_FOUND

echo Python 3.11 or newer was not found on this computer.
echo LocalCam needs Python to create its private environment.
echo.
where winget >nul 2>&1
if errorlevel 1 goto PYTHON_MANUAL
choice /C YN /N /M "Install Python 3.13 with Windows winget now? [Y/N] "
if errorlevel 2 goto CANCELLED

echo.
echo Installing Python 3.13...
winget install --id Python.Python.3.13 --exact --accept-package-agreements --accept-source-agreements
if errorlevel 1 goto PYTHON_INSTALL_FAILED

echo.
echo Python installation completed.
echo Close this window and run run.bat again so Windows refreshes PATH.
goto STOP_WITH_PAUSE

:PYTHON_FOUND
%SYSTEM_PY% -c "import sys; print('Detected Python', '.'.join(map(str,sys.version_info[:3]))); raise SystemExit(0 if sys.version_info >= (3,11) else 1)"
if not errorlevel 1 goto PROJECT_CHECKS

echo.
echo Python 3.11 or newer is required.
echo Your detected Python installation is older than 3.11.
echo LocalCam will use a separate .venv and will not replace other project environments.
echo.
where winget >nul 2>&1
if errorlevel 1 goto PYTHON_MANUAL
choice /C YN /N /M "Install Python 3.13 with Windows winget now? [Y/N] "
if errorlevel 2 goto CANCELLED

echo.
echo Installing Python 3.13...
winget install --id Python.Python.3.13 --exact --accept-package-agreements --accept-source-agreements
if errorlevel 1 goto PYTHON_INSTALL_FAILED

echo.
echo Python installation completed.
echo Close this window and run run.bat again so Windows refreshes PATH.
goto STOP_WITH_PAUSE

:PROJECT_CHECKS
if not exist "%ROOT%app.py" goto PROJECT_MISSING
if not exist "%ROOT%requirements.txt" goto REQUIREMENTS_MISSING
if not exist "%ROOT%config.example.json" goto CONFIG_MISSING
if not exist "%ROOT%web\assets\app.css" goto WEB_MISSING
if not exist "%ROOT%web\assets\app.js" goto WEB_MISSING
if not exist "%ROOT%web\login.html" goto WEB_MISSING
if not exist "%ROOT%web\setup.html" goto WEB_MISSING

rem Do not hard-fail on an exact UI cache version. The actual asset files are what matter.
echo Web UI files: OK

echo.
if not exist "%VENV%" goto CREATE_ENV

echo LocalCam Python environment found.
"%VENV%" -m pip --version >nul 2>&1
if not errorlevel 1 goto CHECK_MODULES

echo The existing LocalCam Python environment is incomplete.
choice /C YN /N /M "Repair the LocalCam Python environment now? [Y/N] "
if errorlevel 2 goto CANCELLED
rmdir /s /q "%ROOT%.venv" >nul 2>&1
if exist "%VENV%" goto ENV_REMOVE_FAILED

:CREATE_ENV
echo.
echo No usable LocalCam Python environment was found.
echo Your existing system Python is fine - LocalCam uses a separate .venv, so it will not interfere with Redbot or other projects.
echo.
choice /C YN /N /M "Create the LocalCam environment and install dependencies? [Y/N] "
if errorlevel 2 goto CANCELLED

echo.
echo Creating LocalCam virtual environment...
%SYSTEM_PY% -m venv "%ROOT%.venv"
if errorlevel 1 goto VENV_FAILED
if not exist "%VENV%" goto VENV_FAILED

echo.
echo Installing Python packages from requirements.txt...
"%VENV%" -m ensurepip --upgrade
if errorlevel 1 goto PIP_FAILED
"%VENV%" -m pip install --upgrade pip
if errorlevel 1 goto PIP_FAILED
"%VENV%" -m pip install -r "%ROOT%requirements.txt"
if errorlevel 1 goto PIP_FAILED

goto CHECK_MODULES

:CHECK_MODULES
set "NEED_INSTALL=0"
"%VENV%" -c "import PIL" >nul 2>&1
if errorlevel 1 set "NEED_INSTALL=1"
"%VENV%" -c "import psutil" >nul 2>&1
if errorlevel 1 set "NEED_INSTALL=1"
"%VENV%" -c "import onvif" >nul 2>&1
if errorlevel 1 set "NEED_INSTALL=1"
if "%NEED_INSTALL%"=="0" goto CONFIG_CHECK

echo.
echo One or more LocalCam Python components are missing.
choice /C YN /N /M "Install the missing components now? [Y/N] "
if errorlevel 2 goto CANCELLED
"%VENV%" -m pip install -r "%ROOT%requirements.txt"
if errorlevel 1 goto PIP_FAILED

goto CONFIG_CHECK

:CONFIG_CHECK
if exist "%ROOT%config.json" goto FFMPEG_CHECK
copy /y "%ROOT%config.example.json" "%ROOT%config.json" >nul 2>&1
if errorlevel 1 goto CONFIG_FAILED
echo Created LocalCam configuration.

a:FFMPEG_CHECK
