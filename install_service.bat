@echo off
setlocal EnableExtensions
cd /d "%~dp0"

if not exist .venv\Scripts\python.exe (
  echo ERROR: Virtual environment not found. Run install.bat first.
  pause
  exit /b 1
)
if not exist service.py (
  echo ERROR: service.py is missing. Run this file from the LocalCam folder.
  pause
  exit /b 1
)

net session >nul 2>&1
if errorlevel 1 (
  echo ERROR: Administrator privileges are required.
  echo Right-click install_service.bat and choose Run as administrator.
  pause
  exit /b 1
)

.venv\Scripts\python.exe -m pip install pywin32
if errorlevel 1 goto :failed

.venv\Scripts\python.exe service.py install
if errorlevel 1 goto :failed

sc config LocalCamService start= auto
if errorlevel 1 goto :failed

sc failure LocalCamService reset= 86400 actions= restart/60000/restart/60000/restart/60000
if errorlevel 1 goto :failed

net start LocalCamService
if errorlevel 1 goto :failed

echo.
echo LocalCam Windows service installed and started successfully.
pause
exit /b 0

:failed
echo.
echo ERROR: Service installation did not complete. Review the command output above.
echo If the service was partially installed, correct the reported issue and rerun this file as Administrator.
pause
exit /b 1
