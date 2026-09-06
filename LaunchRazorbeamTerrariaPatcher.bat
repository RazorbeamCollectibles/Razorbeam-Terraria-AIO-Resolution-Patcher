@echo off
setlocal
cd /d "%~dp0"
if exist "RazorbeamTerrariaPatcher.exe" (
    start "" "RazorbeamTerrariaPatcher.exe"
    exit /b 0
)
where py >nul 2>nul
if errorlevel 1 (
    echo Python 3.11 or newer is required for the source release.
    echo Use the portable Windows release to run without Python.
    pause
    exit /b 1
)
if not exist ".venv\Scripts\pythonw.exe" (
    py -3 -m venv .venv
    if errorlevel 1 goto :failed
    ".venv\Scripts\python.exe" -m pip install -r requirements-runtime.txt
    if errorlevel 1 goto :failed
)
start "" ".venv\Scripts\pythonw.exe" "app\app.py"
exit /b 0
:failed
echo Setup failed. Check the messages above. No game files were changed.
pause
exit /b 1
