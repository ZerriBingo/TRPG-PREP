@echo off
rem Visible launcher for trpg-prep. Use start.vbs for a hidden window.
rem Keep this file ASCII-only to avoid cmd codepage issues.
cd /d "%~dp0"
if errorlevel 1 goto :fail
where python >nul 2>&1
if errorlevel 1 goto :python_missing
if not exist "backend\app\main.py" goto :fail
python -c "import fastapi, uvicorn, fitz" >nul 2>&1
if errorlevel 1 goto :deps_missing
echo Starting TRPG prep assistant ...
echo Open http://127.0.0.1:8000 in your browser.
python -m uvicorn backend.app.main:app --host 127.0.0.1 --port 8000
goto :end
:python_missing
echo ERROR: Python was not found on PATH.
echo Install Python 3.11+ and reopen this launcher.
goto :end
:deps_missing
echo ERROR: Python dependencies are missing.
echo Run: python -m pip install -r "%~dp0backend\requirements.txt"
goto :end
:fail
echo ERROR: cannot enter the project directory.
:end
if /I "%~1"=="--no-pause" exit /b
pause
