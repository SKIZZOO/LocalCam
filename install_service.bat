@echo off
setlocal
cd /d %~dp0
if not exist .venv\Scripts\python.exe (
  echo Run install.bat first.
  pause
  exit /b 1
)
call .venv\Scripts\activate.bat
python -m pip install pywin32
python service.py install
sc config LocalCamService start= auto
sc failure LocalCamService reset= 86400 actions= restart/60000/restart/60000/restart/60000
net start LocalCamService
if %ERRORLEVEL%==0 echo LocalCam Windows service installed and started.
pause
