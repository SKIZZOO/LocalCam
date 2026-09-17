@echo off
setlocal
cd /d %~dp0
set TASK_NAME=LocalCam NVR
set RUNNER=%~dp0run.bat
schtasks /Create /TN "%TASK_NAME%" /TR "\"%RUNNER%\"" /SC ONLOGON /RL LIMITED /F
if %ERRORLEVEL% EQU 0 echo Startup task installed: %TASK_NAME%
pause
