@echo off
rem Stop trpg-prep server listening on port 8000
for /f "tokens=5" %%a in ('netstat -ano ^| findstr ":8000" ^| findstr "LISTENING"') do (
  taskkill /F /PID %%a >nul 2>&1
)
echo Stopped trpg-prep if it was running.
pause
