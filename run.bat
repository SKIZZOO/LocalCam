@echo off
cd /d "%~dp0"
if not exist .venv\Scripts\python.exe (
  echo Ruleaza install.bat mai intai.
  pause
  exit /b 1
)
call .venv\Scripts\activate.bat
python app.py
