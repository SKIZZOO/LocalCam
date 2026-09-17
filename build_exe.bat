@echo off
setlocal
cd /d %~dp0
call .venv\Scripts\activate.bat
python -m PyInstaller --noconfirm --clean --onefile --name LocalCam --add-data "web;web" --add-data "config.example.json;." app.py
if exist dist\LocalCam.exe echo Built: dist\LocalCam.exe
pause
