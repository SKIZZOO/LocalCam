@echo off
setlocal EnableExtensions
cd /d "%~dp0"
title LocalCam | Startup
color 0B

:header
cls
echo.
echo  +----------------------------------------------------------+
echo  ^|                         LOCALCAM                         ^|
echo  ^|                 Network Camera Recorder                  ^|
echo  +----------------------------------------------------------+
echo.
echo  This launcher checks requirements and starts the app.
echo.

where py >nul 2>&1
if errorlevel 1 goto :python_missing
py -3 -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)" >nul 2>&1
if errorlevel 1 goto :python_old

if not exist app.py (
  echo  [ERROR] app.py was not found.
  echo          Make sure this file is inside the LocalCam folder.
  goto :error_pause
)
if not exist requirements.txt (
  echo  [ERROR] requirements.txt was not found. Project files may be incomplete.
  goto :error_pause
)

if not exist .venv\Scripts\python.exe (
  echo  [SETUP] LocalCam has not been installed on this computer yet.
  echo          Setup creates a local Python environment and installs packages.
  echo          Packages are downloaded using pip from its configured index.
  echo.
  choice /C YN /N /M "  Install requirements now? [Y/N] "
  if errorlevel 2 goto :cancelled
  echo.
  call install.bat
  if errorlevel 1 goto :error_pause
) else (
  echo  [OK] LocalCam Python environment found.
)

echo.
where ffmpeg >nul 2>&1
if errorlevel 1 (
  echo  [NOTICE] FFmpeg was not found on PATH.
  echo           It is required for camera streams and recording.
  echo           Official information: https://ffmpeg.org/download.html
  echo.
  choice /C YN /N /M "  Open the FFmpeg download page? [Y/N] "
  if errorlevel 2 goto :start_app
  start "" "https://ffmpeg.org/download.html"
)

:start_app
echo.
echo  [START] Launching LocalCam...
echo          Keep this window open while using LocalCam.
echo.
.venv\Scripts\python.exe app.py
echo.
echo  [STOP] LocalCam has exited.
goto :error_pause

:python_missing
echo  [MISSING] Python 3.11 or newer was not found.
echo            Official download: https://www.python.org/downloads/windows/
echo.
choice /C YN /N /M "  Open the Python download page? [Y/N] "
if errorlevel 2 goto :cancelled
start "" "https://www.python.org/downloads/windows/"
goto :cancelled

:python_old
echo  [MISSING] Python 3.11 or newer is required.
echo            Official download: https://www.python.org/downloads/windows/
echo.
choice /C YN /N /M "  Open the Python download page? [Y/N] "
if errorlevel 2 goto :cancelled
start "" "https://www.python.org/downloads/windows/"
goto :cancelled

:cancelled
echo.
echo  No changes were made by this launcher.
pause
goto :done

:error_pause
echo.
pause
:done
endlocal
