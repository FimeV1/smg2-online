@echo off
REM Set or change where Dolphin.exe and your game file are. They are remembered
REM for your Windows user, so you are not asked again - even after replacing
REM this folder with a newer version of the mod.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0start-smg2.ps1" -SetPaths
echo.
pause
