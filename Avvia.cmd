@echo off
cd /d "%~dp0"
if exist "dist\iPhone Desktop\iPhone Desktop.exe" (
    start "" "dist\iPhone Desktop\iPhone Desktop.exe"
) else if exist ".venv\Scripts\pythonw.exe" (
    start "" ".venv\Scripts\pythonw.exe" "run.py"
) else (
    echo Run scripts\build.ps1 first to prepare the app.
    pause
)
