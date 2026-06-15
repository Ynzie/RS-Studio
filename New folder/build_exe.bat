@echo off
title Build GP2Rocksmith Studio
echo ==================================================
echo  Builds GP2Rocksmith.exe (the GUI app)
echo  and fetches ffmpeg for audio conversion.
echo ==================================================
echo.
where python >nul 2>nul
if errorlevel 1 (
    echo ERROR: Python is not installed or not on PATH.
    echo Install from https://www.python.org/downloads/ and CHECK
    echo "Add python.exe to PATH" on the first installer screen.
    pause & exit /b 1
)
for %%F in (gp2rs.py gp2rs_studio.py cst_template.py wwise_convert.py) do (
    if not exist "%~dp0%%F" ( echo ERROR: %%F must be in this folder. & pause & exit /b 1 )
)

echo Step 1/3: Installing PyInstaller...
python -m pip install --upgrade pyinstaller
if errorlevel 1 ( echo pip install failed. & pause & exit /b 1 )

echo.
echo Step 2/3: Getting ffmpeg (for mp3 -> wav)...
if exist "%~dp0ffmpeg.exe" (
    echo   ffmpeg.exe already here - good.
) else (
    echo   Downloading ffmpeg...
    powershell -Command "try { Invoke-WebRequest -Uri 'https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip' -OutFile '%TEMP%\ff.zip'; Expand-Archive -Force '%TEMP%\ff.zip' '%TEMP%\ff'; $exe = Get-ChildItem -Recurse -Filter ffmpeg.exe '%TEMP%\ff' | Select-Object -First 1; Copy-Item $exe.FullName '%~dp0ffmpeg.exe' } catch { Write-Host 'ffmpeg download failed - you can add ffmpeg.exe here manually later.' }"
)

echo.
echo Step 3/3: Building the app (bundles modules + ffmpeg if present)...
set ADDFF=
if exist "%~dp0ffmpeg.exe" set ADDFF=--add-binary "%~dp0ffmpeg.exe;."
python -m PyInstaller --onefile --windowed --name "RS STUDIO" --icon "%~dp0rs_studio.ico" ^
  --add-data "%~dp0rs_studio.ico;." --add-data "%~dp0rs_studio.png;." ^
  --add-data "%~dp0cst_template.py;." --add-data "%~dp0wwise_convert.py;." %ADDFF% ^
  "%~dp0gp2rs_studio.py"
if errorlevel 1 ( echo build failed. & pause & exit /b 1 )

echo.
echo ==================================================
echo  DONE! App: %~dp0dist\RS STUDIO.exe
echo  Tick the .psarc box, pick your packer.exe once,
echo  and mp3 audio is converted automatically.
echo ==================================================
pause
