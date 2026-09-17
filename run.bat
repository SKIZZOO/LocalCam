@echo off
setlocal EnableExtensions
cd /d "%~dp0"
title LocalCam Launcher

set "ROOT=%~dp0"
set "VENV=%ROOT%.venv\Scripts\python.exe"
set "SYSTEM_PY="

rem ================================================================
rem LocalCam Windows launcher
rem Simple label flow so double-click startup stays reliable.
rem ================================================================

cls
echo.
echo ========================================
echo              LOCALCAM NVR
echo ========================================
echo.
echo Windows launcher and environment check
echo Project folder: %ROOT%
echo.

:find_python
set "SYSTEM_PY="
where py >nul 2>&1
if not errorlevel 1 set "SYSTEM_PY=py -3"
if defined SYSTEM_PY goto check_python_version
where python >nul 2>&1
if not errorlevel 1 set "SYSTEM_PY=python"
if defined SYSTEM_PY goto check_python_version
goto python_missing

:check_python_version
%SYSTEM_PY% -c "import sys; print('Detected Python', '.'.join(map(str,sys.version_info[:3]))); raise SystemExit(0 if sys.version_info >= (3,11) else 1)"
if not errorlevel 1 goto project_checks
goto python_old

:project_checks
if not exist "%ROOT%app.py" goto project_missing
if not exist "%ROOT%requirements.txt" goto requirements_missing
if not exist "%ROOT%config.example.json" goto config_missing
if not exist "%ROOT%web\assets\app.css" goto web_missing
if not exist "%ROOT%web\assets\app.js" goto web_missing
if not exist "%ROOT%web\login.html" goto web_missing
if not exist "%ROOT%web\setup.html" goto web_missing

echo Web UI files: OK
echo.
if not exist "%VENV%" goto create_env
echo LocalCam Python environment found.
"%VENV%" -m pip --version >nul 2>&1
if not errorlevel 1 goto check_modules
echo.
echo The existing LocalCam Python environment is incomplete.
echo.
choice /C YN /N /M "Repair the LocalCam Python environment now? [Y/N] "
if errorlevel 2 goto cancelled
rmdir /s /q "%ROOT%.venv" >nul 2>&1
if exist "%VENV%" goto env_remove_failed
goto create_env

:create_env
echo.
echo No usable LocalCam Python environment was found.
echo Your existing system Python is fine - LocalCam uses a separate .venv, so it will not interfere with Redbot or other projects.
echo.
choice /C YN /N /M "Create the LocalCam environment and install dependencies? [Y/N] "
if errorlevel 2 goto cancelled
echo.
echo Creating LocalCam virtual environment...
%SYSTEM_PY% -m venv "%ROOT%.venv"
if errorlevel 1 goto venv_failed
if not exist "%VENV%" goto venv_failed
echo.
echo Installing Python packages from requirements.txt...
"%VENV%" -m ensurepip --upgrade
if errorlevel 1 goto pip_failed
"%VENV%" -m pip install --upgrade pip
if errorlevel 1 goto pip_failed
"%VENV%" -m pip install -r "%ROOT%requirements.txt"
if errorlevel 1 goto pip_failed
goto check_modules

:check_modules
set "NEED_INSTALL=0"
"%VENV%" -c "import PIL" >nul 2>&1
if errorlevel 1 set "NEED_INSTALL=1"
"%VENV%" -c "import psutil" >nul 2>&1
if errorlevel 1 set "NEED_INSTALL=1"
"%VENV%" -c "import onvif" >nul 2>&1
if errorlevel 1 set "NEED_INSTALL=1"
"%VENV%" -c "import rtsp_backchannel" >nul 2>&1
if errorlevel 1 set "NEED_INSTALL=1"
"%VENV%" -c "import aiortc, av" >nul 2>&1
if errorlevel 1 set "NEED_INSTALL=1"
if "%NEED_INSTALL%"=="0" goto config_check
echo.
echo One or more LocalCam Python components are missing.
echo.
choice /C YN /N /M "Install the missing components now? [Y/N] "
if errorlevel 2 goto cancelled
"%VENV%" -m pip install -r "%ROOT%requirements.txt"
if errorlevel 1 goto pip_failed
goto config_check

:config_check
if exist "%ROOT%config.json" goto ffmpeg_check
copy /y "%ROOT%config.example.json" "%ROOT%config.json" >nul 2>&1
if errorlevel 1 goto config_failed
echo Created LocalCam configuration.
goto ffmpeg_check

:ffmpeg_check
where ffmpeg >nul 2>&1
if not errorlevel 1 goto start_localcam
echo.
echo [NOTICE] FFmpeg is not installed or is not on PATH.
echo LocalCam camera preview and recording need FFmpeg.
where winget >nul 2>&1
if errorlevel 1 goto start_localcam
choice /C YN /N /M "Install FFmpeg with Windows winget now? [Y/N] "
if errorlevel 2 goto start_localcam
echo.
echo Installing FFmpeg...
winget install --id Gyan.FFmpeg.Shared --exact --accept-package-agreements --accept-source-agreements
if errorlevel 1 goto ffmpeg_install_failed
echo.
echo FFmpeg installation completed. Refreshing the Windows PATH for this launcher...
for /f "delims=" %%P in ('powershell -NoProfile -Command "[Environment]::GetEnvironmentVariable('Path','Machine') + ';' + [Environment]::GetEnvironmentVariable('Path','User')" 2^>nul') do set "PATH=%%P"
where ffmpeg >nul 2>&1
if not errorlevel 1 goto start_localcam
echo.
echo FFmpeg was installed, but this Windows session still cannot find ffmpeg.exe.
echo Close this window and run run.bat again so Windows reloads the updated PATH.
goto stop_with_pause

:start_localcam
cls
echo.
echo ========================================
echo              LOCALCAM NVR
echo ========================================
echo.
echo Windows launcher and environment check
echo Project folder: %ROOT%
echo.
echo Starting LocalCam...
echo Keep this window open while LocalCam is running.
echo.
if not exist "%VENV%" goto venv_failed
"%VENV%" "%ROOT%app.py"
set "APP_EXIT=%ERRORLEVEL%"
echo.
if "%APP_EXIT%"=="0" goto normal_stop
echo LocalCam stopped with exit code %APP_EXIT%.
goto failed

:normal_stop
set "FINAL_EXIT=0"
echo LocalCam has stopped normally.
goto stop_with_pause

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
if errorlevel 1 goto python_manual
choice /C YN /N /M "Install Python 3.13 with Windows winget now? [Y/N] "
if errorlevel 2 goto cancelled
echo.
echo Installing Python 3.13...
winget install --id Python.Python.3.13 --exact --accept-package-agreements --accept-source-agreements
if errorlevel 1 goto python_install_failed
echo.
echo Python installation completed.
echo Close this window and run run.bat again so Windows refreshes PATH.
goto stop_with_pause

:python_old
cls
echo.
echo ========================================
echo              LOCALCAM NVR
echo ========================================
echo.
echo Python 3.11 or newer is required.
echo The detected Python installation is older than 3.11.
echo.
where winget >nul 2>&1
if errorlevel 1 goto python_manual
choice /C YN /N /M "Install Python 3.13 with Windows winget now? [Y/N] "
if errorlevel 2 goto cancelled
echo.
echo Installing Python 3.13...
winget install --id Python.Python.3.13 --exact --accept-package-agreements --accept-source-agreements
if errorlevel 1 goto python_install_failed
echo.
echo Python installation completed.
echo Close this window and run run.bat again so Windows refreshes PATH.
goto stop_with_pause

:python_manual
echo Windows Package Manager (winget) is not available.
echo Install Python 3.11 or newer manually from:
echo https://www.python.org/downloads/windows/
set "FINAL_EXIT=1"
goto stop_with_pause

:python_install_failed
echo.
echo Python installation did not complete successfully.
echo Check the winget message above and run run.bat again.
set "FINAL_EXIT=1"
goto stop_with_pause

:ffmpeg_install_failed
echo.
echo FFmpeg installation did not complete successfully.
echo Check the winget message above and run run.bat again.
set "FINAL_EXIT=1"
goto stop_with_pause

:project_missing
echo.
echo app.py was not found. Run run.bat from the LocalCam folder.
set "FINAL_EXIT=1"
goto stop_with_pause

:requirements_missing
echo.
echo requirements.txt was not found. The project files are incomplete.
set "FINAL_EXIT=1"
goto stop_with_pause

:web_missing
echo.
echo LocalCam web files are missing.
echo Re-download the current project from GitHub and run run.bat again.
set "FINAL_EXIT=1"
goto stop_with_pause

:config_missing
echo.
echo config.example.json was not found.
set "FINAL_EXIT=1"
goto stop_with_pause

:config_failed
echo.
echo Could not create config.json. Check folder permissions.
set "FINAL_EXIT=1"
goto stop_with_pause

:venv_failed
echo.
echo Could not create or repair the LocalCam Python environment.
echo Check that Python has the venv module available and that this folder is writable.
set "FINAL_EXIT=1"
goto stop_with_pause

:env_remove_failed
echo.
echo The old LocalCam .venv could not be removed.
echo Close any LocalCam/Python process and run run.bat again.
set "FINAL_EXIT=1"
goto stop_with_pause

:pip_failed
echo.
echo Python package installation failed.
echo Review the pip output above.
set "FINAL_EXIT=1"
goto stop_with_pause

:cancelled
echo.
echo Setup was cancelled. LocalCam was not started.
set "FINAL_EXIT=1"
goto stop_with_pause

:failed
echo.
echo LocalCam launcher finished with an error.
set "FINAL_EXIT=1"
goto stop_with_pause

:stop_with_pause
if not defined FINAL_EXIT set "FINAL_EXIT=1"
echo.
echo LocalCam launcher finished with exit code %FINAL_EXIT%.
pause
exit /b %FINAL_EXIT%
