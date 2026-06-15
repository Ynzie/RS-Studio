@echo off
title GP2RS - Diagnose Timing
cd /d "%~dp0"
echo ================================================
echo  GP2Rocksmith - Timing Diagnostic
echo ================================================
echo.
echo Drag a .gp file onto this window and press Enter,
echo OR just press Enter to be prompted for a file path.
echo.
echo If you also have the original audio, it will compare
echo the chart length against the audio length for you.
echo.

set /p GPFILE=Path to .gp file: 
if "%GPFILE%"=="" (
    echo No file given. Exiting.
    pause
    exit /b 1
)

set /p AUDIOFILE=Path to audio file (leave blank to skip): 

if "%AUDIOFILE%"=="" (
    python diagnose_timing.py "%GPFILE%"
) else (
    python diagnose_timing.py "%GPFILE%" --audio "%AUDIOFILE%"
)

if errorlevel 1 (
    echo.
    echo Something went wrong. Copy the error above and send it.
)
echo.
pause
