@echo off
setlocal
cd /d %~dp0
if not exist .venv py -3 -m venv .venv
call .venv\Scripts\activate.bat
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
if not exist config.json copy /y config.example.json config.json >nul
echo.
echo LocalCam dependencies installed.
echo Make sure ffmpeg.exe is installed and available in PATH, or set its full path in Settings.
echo Next: run.bat
pause
