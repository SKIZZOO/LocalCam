@echo off
setlocal EnableExtensions
cd /d "%~dp0"

if not exist .venv\Scripts\python.exe (
  echo Run run.bat first so the LocalCam environment is created.
  pause
  exit /b 1
)

.venv\Scripts\python.exe -c "import importlib.util; raise SystemExit(0 if importlib.util.find_spec('PyInstaller') else 1)" >nul 2>&1
if errorlevel 1 (
  echo PyInstaller is not installed. Installing the build dependency...
  .venv\Scripts\python.exe -m pip install PyInstaller>=6.10.0
  if errorlevel 1 (
    echo PyInstaller installation failed.
    pause
    exit /b 1
  )
)

.venv\Scripts\python.exe -m PyInstaller --noconfirm --clean LocalCam.spec
if exist dist\LocalCam\LocalCam.exe echo Built: dist\LocalCam\LocalCam.exe
if exist dist\LocalCamService\LocalCamService.exe echo Built: dist\LocalCamService\LocalCamService.exe
pause
