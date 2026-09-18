@echo off
setlocal EnableExtensions
cd /d "%~dp0"
title LocalCam Updater

set "ROOT=%~dp0"
set "REPO=https://github.com/SKIZZOO/LocalCam.git"
set "BRANCH=main"

echo.
echo ========================================
echo          LOCALCAM UPDATE
echo ========================================
echo.
echo Project folder: %ROOT%
echo Updating from: %REPO% [%BRANCH%]
echo.

where git >nul 2>&1
if not errorlevel 1 goto git_update

echo Git was not found. Using the GitHub ZIP fallback...
echo This keeps config.json, recordings, snapshots, and the Python environment.
echo.

where powershell >nul 2>&1
if errorlevel 1 goto no_updater

set "TMP=%TEMP%\LocalCam-update-%RANDOM%%RANDOM%"
mkdir "%TMP%" >nul 2>&1
if errorlevel 1 goto zip_failed

powershell -NoProfile -ExecutionPolicy Bypass -Command "$ErrorActionPreference='Stop'; $zip = Join-Path $env:TEMP 'LocalCam-latest.zip'; if (Test-Path $zip) { Remove-Item $zip -Force }; Invoke-WebRequest -UseBasicParsing 'https://github.com/SKIZZOO/LocalCam/archive/refs/heads/main.zip' -OutFile $zip; Expand-Archive -LiteralPath $zip -DestinationPath '%TMP%' -Force; Remove-Item $zip -Force"
if errorlevel 1 goto zip_failed

if not exist "%TMP%\LocalCam-main\app.py" goto zip_failed
echo.
echo Copying latest project files...
robocopy "%TMP%\LocalCam-main" "%ROOT%" /E /R:2 /W:1 /XD ".git" ".venv" "recordings" "snapshots" /XF "config.json" "localcam.sqlite3" "localcam.log" "*.mkv" "*.mp4" "*.avi" "*.mov" "*.jpg" "*.jpeg"
if errorlevel 8 goto copy_failed

rmdir /s /q "%TMP%" >nul 2>&1
echo.
echo Update completed from GitHub main.
echo Starting LocalCam...
echo.
call "%ROOT%run.bat"
set "FINAL_EXIT=%ERRORLEVEL%"
goto finish

:git_update
if not exist "%ROOT%.git\HEAD" goto git_not_repo

echo Fetching the latest main branch...
git fetch origin main --prune
if errorlevel 1 goto git_fetch_failed

for /f "delims=" %%H in ('git rev-parse HEAD 2^>nul') do set "LOCAL_HEAD=%%H"
for /f "delims=" %%H in ('git rev-parse origin/main 2^>nul') do set "REMOTE_HEAD=%%H"

if /I "%LOCAL_HEAD%"=="%REMOTE_HEAD%" goto already_current

echo Updating local files with fast-forward only...
git merge --ff-only origin/main
if errorlevel 1 goto git_merge_failed

echo.
echo Update completed successfully.
echo Starting LocalCam...
echo.
call "%ROOT%run.bat"
set "FINAL_EXIT=%ERRORLEVEL%"
goto finish

:already_current
echo.
echo LocalCam is already up to date with origin/main.
echo Starting LocalCam...
echo.
call "%ROOT%run.bat"
set "FINAL_EXIT=%ERRORLEVEL%"
goto finish

:git_not_repo
echo.
echo This folder is not a Git checkout, so Git update mode cannot be used.
echo Falling back to the GitHub ZIP updater...
echo.
set "PATH=%PATH%"
goto zip_fallback_from_git

:git_fetch_failed
echo.
echo Git could not fetch origin/main.
echo Check your internet connection and GitHub access.
set "FINAL_EXIT=1"
goto finish

:git_merge_failed
echo.
echo Local changes prevent a safe fast-forward update.
echo No files were overwritten.
echo Commit/stash your local tracked changes, then run update.bat again.
set "FINAL_EXIT=1"
goto finish

:zip_fallback_from_git
where powershell >nul 2>&1
if errorlevel 1 goto no_updater
goto zip_update

:zip_update
set "TMP=%TEMP%\LocalCam-update-%RANDOM%%RANDOM%"
mkdir "%TMP%" >nul 2>&1
if errorlevel 1 goto zip_failed

powershell -NoProfile -ExecutionPolicy Bypass -Command "$ErrorActionPreference='Stop'; $zip = Join-Path $env:TEMP 'LocalCam-latest.zip'; if (Test-Path $zip) { Remove-Item $zip -Force }; Invoke-WebRequest -UseBasicParsing 'https://github.com/SKIZZOO/LocalCam/archive/refs/heads/main.zip' -OutFile $zip; Expand-Archive -LiteralPath $zip -DestinationPath '%TMP%' -Force; Remove-Item $zip -Force"
if errorlevel 1 goto zip_failed

if not exist "%TMP%\LocalCam-main\app.py" goto zip_failed
echo.
echo Copying latest project files while preserving local data...
robocopy "%TMP%\LocalCam-main" "%ROOT%" /E /R:2 /W:1 /XD ".git" ".venv" "recordings" "snapshots" /XF "config.json" "localcam.sqlite3" "localcam.log" "*.mkv" "*.mp4" "*.avi" "*.mov" "*.jpg" "*.jpeg"
if errorlevel 8 goto copy_failed

rmdir /s /q "%TMP%" >nul 2>&1
echo.
echo Update completed from GitHub main.
echo Starting LocalCam...
echo.
call "%ROOT%run.bat"
set "FINAL_EXIT=%ERRORLEVEL%"
goto finish

:zip_failed
echo.
echo GitHub ZIP download/update failed.
echo Check your internet connection and try again.
set "FINAL_EXIT=1"
if defined TMP rmdir /s /q "%TMP%" >nul 2>&1
goto finish

:copy_failed
echo.
echo Some project files could not be copied.
echo Make sure LocalCam is stopped before updating, then try again.
set "FINAL_EXIT=1"
if defined TMP rmdir /s /q "%TMP%" >nul 2>&1
goto finish

:no_updater
echo.
echo Neither Git nor PowerShell is available.
echo Install Git for Windows or PowerShell, then run update.bat again.
set "FINAL_EXIT=1"
goto finish

:finish
echo.
echo LocalCam updater finished with exit code %FINAL_EXIT%.
pause
exit /b %FINAL_EXIT%
