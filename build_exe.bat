@echo off
setlocal
cd /d %~dp0
if not exist .venv\Scripts\python.exe (
  echo Run install.bat first.
  pause
  exit /b 1
)
call .venv\Scripts\activate.bat
python -m PyInstaller --noconfirm --clean LocalCam.spec
if exist dist\LocalCam\LocalCam.exe echo Built: dist\LocalCam\LocalCam.exe
if exist dist\LocalCamService\LocalCamService.exe echo Built: dist\LocalCamService\LocalCamService.exe
pause
