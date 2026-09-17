@echo off
setlocal
cd /d %~dp0
net stop LocalCamService >nul 2>&1
if exist .venv\Scripts\python.exe (
  call .venv\Scripts\activate.bat
  python service.py remove
) else (
  echo Virtual environment not found. Install dependencies first.
)
pause
