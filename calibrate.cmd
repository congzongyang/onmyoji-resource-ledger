@echo off
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
    echo Run setup.cmd first.
    pause
    exit /b 1
)
".venv\Scripts\python.exe" ledger.py --calibrate
if errorlevel 1 echo Calibration failed. Check ADB and the game resolution.
pause
