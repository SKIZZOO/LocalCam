@echo off
setlocal EnableExtensions
cd /d "%~dp0"
title LocalCam Launcher

echo ========================================
echo             LOCALCAM LAUNCHER
echo ========================================
echo.

where py >nul 2>&1
if errorlevel 1 (
  echo Python 3.11 or newer is required, but the Python Launcher was not found.
  echo Official download: https://www.python.org/downloads/windows/
  echo Install Python 3.11 or newer, then open this launcher again.
  choice /C YN /M "Open the official Python download page now"
  if errorlevel 2 goto :end
  start "" "https://www.python.org/downloads/windows/"
  goto :end
)

py -3 -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)" >nul 2>&1
if errorlevel 1 (
  echo Python 3.11 or newer is required.
  echo Official download: https://www.python.org/downloads/windows/
  choice /C YN /M "Open the official Python download page now"
  if errorlevel 2 goto :end
  start "" "https://www.python.org/downloads/windows/"
  goto :end
)

if not exist app.py (
  echo ERROR: app.py was not found. Run this file from the LocalCam folder.
  pause
  goto :end
)

if not exist requirements.txt (
  echo ERROR: requirements.txt was not found. The project files may be incomplete.
  pause
  goto :end
)

if not exist .venv\Scripts\python.exe (
  echo LocalCam's Python environment has not been set up yet.
  echo The setup will create a virtual environment and install requirements.txt.
  echo Python packages will be downloaded from the package index configured for pip.
  echo.
  choice /C YN /M "Run setup now"
  if errorlevel 2 goto :end
  call install.bat
  if errorlevel 1 goto :end
) else (
  echo Existing LocalCam Python environment found.
)

echo.
where ffmpeg >nul 2>&1
if errorlevel 1 (
  echo WARNING: FFmpeg was not found on PATH.
  echo LocalCam needs FFmpeg for camera streams and recording.
  echo Install it from https://ffmpeg.org/download.html
  echo or configure its executable path in LocalCam Settings.
  echo.
  choice /C YN /M "Open the official FFmpeg download page"
  if errorlevel 1 if not errorlevel 2 start "" "https://ffmpeg.org/download.html"
)

echo.
echo Starting LocalCam. Keep this window open while using it.
echo.
.venv\Scripts\python.exe app.py
echo.
echo LocalCam has stopped or exited with an error.
pause

:end
endlocal
