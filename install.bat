@echo off
setlocal EnableExtensions
cd /d "%~dp0"

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

if not exist requirements.txt goto :requirements_missing

echo Creating or updating LocalCam's Python environment...
if not exist .venv\Scripts\python.exe (
  %PY% -m venv .venv
  if errorlevel 1 goto :failed
)

.venv\Scripts\python.exe -m pip install --upgrade pip
if errorlevel 1 goto :failed
.venv\Scripts\python.exe -m pip install -r requirements.txt
if errorlevel 1 goto :failed

if not exist config.json (
  if not exist config.example.json goto :config_missing
  copy /y config.example.json config.json >nul
  if errorlevel 1 goto :failed
  echo Created config.json from config.example.json.
) else (
  echo Existing config.json preserved.
)

echo.
where ffmpeg >nul 2>&1
if errorlevel 1 (
  echo WARNING: FFmpeg was not found on PATH.
  echo LocalCam can start, but camera streaming and recording require FFmpeg.
  echo Configure ffmpeg_path in Settings after installing FFmpeg.
) else (
  echo FFmpeg found on PATH.
)

echo.
echo LocalCam installation completed successfully.
echo Start the application with run.bat.
exit /b 0

:python_missing
echo.
echo ERROR: Python 3.11 or newer was not found.
echo Install Python from:
echo https://www.python.org/downloads/windows/
pause
exit /b 1

:python_old
echo.
echo ERROR: Python 3.11 or newer is required.
echo Update Python from:
echo https://www.python.org/downloads/windows/
pause
exit /b 1

:requirements_missing
echo.
echo ERROR: requirements.txt was not found.
echo Run this installer from the LocalCam project folder.
pause
exit /b 1

:config_missing
echo.
echo ERROR: config.example.json was not found.
pause
exit /b 1

:failed
echo.
echo ERROR: Installation failed. Review the command output above.
pause
exit /b 1
