@echo off
cd /d "%~dp0"
python gp2rs_studio.py
if errorlevel 1 (
    echo.
    echo If you saw a "No module named numpy" error, run this once:
    echo   pip install numpy
    echo.
    echo For any other error, take a screenshot and send it.
    pause
)
