@echo off
title GP2RS - Auto-Detect Lead-in Offset
cd /d "%~dp0"
echo ================================================
echo  GP2Rocksmith - Auto-Detect Lead-in Offset
echo ================================================
echo.
echo This compares your GP chart's rhythm against the
echo audio file to figure out where bar 1 lands in
echo the audio (i.e. how many seconds of intro to skip).
echo.
echo Use your ORIGINAL un-padded audio here, NOT the
echo *_48k.wav that RS Studio already processed.
echo.
echo Requires: numpy (pip install numpy)
echo           ffmpeg.exe next to this script (or on PATH)
echo.

set /p GPFILE=Path to .gp file: 
if "%GPFILE%"=="" (
    echo No file given. Exiting.
    pause
    exit /b 1
)

set /p AUDIOFILE=Path to audio file: 
if "%AUDIOFILE%"=="" (
    echo No audio file given. Exiting.
    pause
    exit /b 1
)

python align_audio.py "%GPFILE%" "%AUDIOFILE%"

if errorlevel 1 (
    echo.
    echo Something went wrong. Make sure numpy is installed:
    echo   pip install numpy
    echo.
    echo Also make sure ffmpeg.exe is in this folder or on PATH.
)
echo.
pause
