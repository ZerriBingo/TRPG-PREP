@echo off
rem Visible launcher for trpg-prep. Use start.vbs for a hidden window.
rem Keep this file ASCII-only to avoid cmd codepage issues.
setlocal EnableExtensions
cd /d "%~dp0"
if errorlevel 1 goto :fail
if not exist "backend\app\main.py" goto :fail

set "UV_VERSION=0.12.9"
set "UV_ROOT=%LOCALAPPDATA%\TRPG-Prep\uv"
set "UV_EXE=%UV_ROOT%\%UV_VERSION%\uv.exe"
set "UV_PROJECT_ENVIRONMENT=%LOCALAPPDATA%\TRPG-Prep\environment"
set "POWERSHELL_EXE=%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe"
if not exist "%POWERSHELL_EXE%" set "POWERSHELL_EXE=pwsh.exe"

echo Preparing the Python runtime...
"%POWERSHELL_EXE%" -NoLogo -NoProfile -NonInteractive -ExecutionPolicy Bypass -File "%~dp0scripts\bootstrap_uv.ps1" -Version "%UV_VERSION%" -DestinationRoot "%UV_ROOT%"
if errorlevel 1 goto :uv_missing
if not exist "%UV_EXE%" goto :uv_missing

echo Synchronizing Python dependencies...
"%UV_EXE%" sync --system-certs --locked --no-dev --managed-python --python 3.11 --link-mode copy
if errorlevel 1 goto :deps_missing

echo Starting TRPG prep assistant ...
echo Open http://127.0.0.1:8000 in your browser.
"%UV_EXE%" run --system-certs --locked --no-dev --managed-python --python 3.11 --no-sync python -m uvicorn backend.app.main:app --host 127.0.0.1 --port 8000
set "EXIT_CODE=%ERRORLEVEL%"
goto :end

:uv_missing
echo ERROR: Could not prepare uv.
echo Check your internet connection and run start.bat again.
set "EXIT_CODE=1"
goto :end

:deps_missing
echo ERROR: Python dependencies could not be synchronized.
echo Check your internet connection and the uv.lock file, then run start.bat again.
set "EXIT_CODE=1"
goto :end

:fail
echo ERROR: cannot enter the project directory.
set "EXIT_CODE=1"

:end
if /I "%~1"=="--no-pause" exit /b %EXIT_CODE%
pause
exit /b %EXIT_CODE%
