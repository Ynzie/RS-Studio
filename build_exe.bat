@echo off
title Build RS Studio
cd /d "%~dp0"

:: Close any running RS Studio so dist\\RS STUDIO.exe is not locked
taskkill /F /IM "RS STUDIO.exe" >nul 2>nul

echo ==================================================
echo  RS Studio - Build EXE
echo ==================================================
echo.

:: Python check
where python >nul 2>nul
if errorlevel 1 (
    echo ERROR: Python not found on PATH.
    echo Install from https://www.python.org/downloads/
    echo Check "Add python.exe to PATH" on the first screen.
    pause & exit /b 1
)

:: Required source files
for %%F in (gp2rs.py gp2rs_studio.py _build_helper.py) do (
    if not exist "%~dp0%%F" (
        echo ERROR: %%F is missing from this folder.
        pause & exit /b 1
    )
)

:: Step 1: deps
echo.
echo Step 1/3: Installing PyInstaller + Pillow...
python -m pip install --upgrade pyinstaller pillow pyguitarpro opencv-python pikepdf pdfplumber pdfminer.six
if errorlevel 1 ( echo pip install failed. & pause & exit /b 1 )

:: Step 2: ffmpeg
echo.
echo Step 2/3: Checking for ffmpeg...
if exist "%~dp0ffmpeg.exe" (
    echo   ffmpeg.exe already present.
) else (
    echo   Add ffmpeg.exe + ffplay.exe next to this script if needed - RS Studio also downloads them on first run.
)

:: Step 3: PyInstaller build
echo.
echo Step 3/3: Building RS STUDIO.exe...

:: Force a fully clean build every time. PyInstaller's incremental caching
:: (the old build\ folder + the RS STUDIO.spec it reuses) can silently merge
:: stale data from a previous build instead of using freshly computed args -
:: that's what caused pdf2gp's .npz template files to be dropped from a
:: build that otherwise looked successful. Deleting both first means there's
:: nothing stale left to merge in.
if exist "%~dp0build" rmdir /s /q "%~dp0build"
if exist "%~dp0RS STUDIO.spec" del /q "%~dp0RS STUDIO.spec"

if not exist "%~dp0_build_helper.py" (
    echo ERROR: _build_helper.py is missing from this folder.
    pause & exit /b 1
)

:: Full PyInstaller output is saved to build_log.txt next to this script (in
:: addition to being shown below) so it can be reviewed after the window
:: closes, or shared for troubleshooting if the build fails.
python -u "%~dp0_build_helper.py" > "%~dp0build_log.txt" 2>&1
set "BUILD_RC=%ERRORLEVEL%"
type "%~dp0build_log.txt"

if not "%BUILD_RC%"=="0" (
    echo.
    echo !! Build failed - scroll up for the error, or open build_log.txt !!
    echo.
    pause
    exit /b 1
)

echo.
echo ==================================================
echo  DONE!  %~dp0dist\RS STUDIO.exe
echo ==================================================
echo.
pause
