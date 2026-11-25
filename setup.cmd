@echo off
cd /d "%~dp0"
py -3.12 -m venv ".venv" || goto failed
".venv\Scripts\python.exe" -m pip install -r requirements.txt || goto failed
echo Setup complete. Open the game courtyard and run calibrate.cmd next.
pause
exit /b 0
:failed
echo Setup failed. Check that Python 3.12 is installed.
pause
exit /b 1
