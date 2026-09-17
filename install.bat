@echo off
cd /d "%~dp0"
python --version >nul 2>&1 || (echo Instaleaza Python 3.11+ si bifeaza Add Python to PATH.&pause&exit /b 1)
if not exist .venv python -m venv .venv
call .venv\Scripts\activate.bat
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
if not exist config.json copy /Y config.example.json config.json >nul
echo Instalare gata. Configureaza camerele in Setari si ruleaza run.bat.
pause
