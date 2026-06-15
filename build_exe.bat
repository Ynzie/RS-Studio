@echo off
title Build RS Studio
cd /d "%~dp0"

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
for %%F in (gp2rs.py gp2rs_studio.py) do (
    if not exist "%~dp0%%F" (
        echo ERROR: %%F is missing from this folder.
        pause & exit /b 1
    )
)

:: Optional source files (warn but don't abort)
for %%F in (cst_template.py wwise_convert.py) do (
    if not exist "%~dp0%%F" (
        echo WARNING: %%F not found - PSARC building will be skipped at runtime.
    )
)

:: Step 1: deps
echo.
echo Step 1/3: Installing PyInstaller + Pillow...
python -m pip install --upgrade pyinstaller pillow
if errorlevel 1 ( echo pip install failed. & pause & exit /b 1 )

:: Step 2: ffmpeg
echo.
echo Step 2/3: Checking for ffmpeg...
if exist "%~dp0ffmpeg.exe" (
    echo   ffmpeg.exe already present.
) else (
    echo   Downloading ffmpeg from gyan.dev...
    powershell -NoProfile -Command "try { Invoke-WebRequest -Uri 'https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip' -OutFile '$env:TEMP\ff.zip' -UseBasicParsing; Expand-Archive -Force '$env:TEMP\ff.zip' '$env:TEMP\ff'; $exe = Get-ChildItem -Recurse -Filter ffmpeg.exe '$env:TEMP\ff' | Select-Object -First 1; if ($exe) { Copy-Item $exe.FullName '%~dp0ffmpeg.exe'; Write-Host '  ffmpeg.exe downloaded.' } else { Write-Host '  ffmpeg.exe not found in archive.' } } catch { Write-Host ('  Download failed: ' + $_.Exception.Message) }"
    if not exist "%~dp0ffmpeg.exe" echo   ffmpeg missing - add ffmpeg.exe here manually before distributing.
)

:: Step 3: PyInstaller build
echo.
echo Step 3/3: Building RS STUDIO.exe...

set "BUILDSCRIPT=%TEMP%\rs_studio_build.py"
set "HEREDIR=%~dp0"
set "HEREDIR=%HEREDIR:~0,-1%"

(
echo import PyInstaller.__main__, os
echo here = os.path.normpath^(r"%HEREDIR%"^) + os.sep
echo.
echo # Required data files
echo args = [
echo     '--onefile',
echo     '--windowed',
echo     '--name=RS STUDIO',
echo     '--add-data=' + here + 'gp2rs.py' + os.pathsep + '.',
echo     '--add-data=' + here + 'gp2rs_studio.py' + os.pathsep + '.',
echo     '--hidden-import=gp2rs',
echo     '--hidden-import=PIL',
echo     '--hidden-import=PIL.Image',
echo     '--hidden-import=PIL.ImageTk',
echo     '--collect-submodules=PIL',
echo ]
echo.
echo # Optional modules - include if present
echo for mod_file, mod_name in [
echo     ^('cst_template.py', 'cst_template'^),
echo     ^('wwise_convert.py', 'wwise_convert'^),
echo ]:
echo     path = here + mod_file
echo     if os.path.exists^(path^):
echo         args += ['--add-data=' + path + os.pathsep + '.', '--hidden-import=' + mod_name]
echo.
echo # Optional bundled binaries
echo for fname in ['rs_studio.ico', 'rs_studio.png', 'ffmpeg.exe', 'ffplay.exe', 'yt-dlp.exe']:
echo     fpath = here + fname
echo     if os.path.exists^(fpath^):
echo         if fname.endswith^('.ico'^):
echo             args += ['--icon=' + fpath, '--add-data=' + fpath + os.pathsep + '.']
echo         elif fname.endswith^('.png'^):
echo             args += ['--add-data=' + fpath + os.pathsep + '.']
echo         else:
echo             args += ['--add-binary=' + fpath + os.pathsep + '.']
echo.
echo args += [
echo     '--distpath=' + here + 'dist',
echo     '--workpath=' + here + 'build',
echo     '--specpath=' + here,
echo     here + 'gp2rs_studio.py',
echo ]
echo.
echo print^('PyInstaller args:'^)
echo for a in args: print^(' ', a^)
echo PyInstaller.__main__.run^(args^)
) > "%BUILDSCRIPT%"

python "%BUILDSCRIPT%"

if errorlevel 1 (
    echo.
    echo !! Build failed - scroll up for the error !!
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
