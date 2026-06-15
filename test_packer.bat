@echo off
title Test CST packer build
echo ============================================
echo  Builds a .psarc from a .dlc.xml template
echo  using Custom Song Toolkit's packer.exe
echo ============================================
echo.
set /p TEMPLATE="Drag the .dlc.xml template here and press Enter: "
set TEMPLATE=%TEMPLATE:"=%
if not exist "%TEMPLATE%" (
    echo File not found: %TEMPLATE%
    pause & exit /b 1
)

set /p PACKER="Drag your CST packer.exe here and press Enter: "
set PACKER=%PACKER:"=%
if not exist "%PACKER%" (
    echo packer.exe not found: %PACKER%
    pause & exit /b 1
)

set OUT=%~dp0built_p.psarc
echo.
echo Running: packer.exe -b -t "%TEMPLATE%" -o "%OUT%" -f Pc -v RS2014
echo --------------------------------------------
"%PACKER%" -b -t "%TEMPLATE%" -o "%OUT%" -f Pc -v RS2014
echo --------------------------------------------
echo.
if exist "%OUT%" (
    echo SUCCESS: %OUT%
) else (
    echo Build did not produce a psarc - copy the messages above and send them back.
)
echo.
pause
