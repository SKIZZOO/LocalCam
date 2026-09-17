@echo off
setlocal
set TASK_NAME=LocalCam NVR
schtasks /Delete /TN "%TASK_NAME%" /F
if %ERRORLEVEL% EQU 0 echo Startup task removed: %TASK_NAME%
pause
