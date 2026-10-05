@echo off
REM Host: starts the server on this PC (its own window) and one Dolphin for you.
REM Needs Python 3 (https://www.python.org/downloads/ - tick "Add python.exe to PATH").
REM Friends join with JOIN A FRIEND.bat and your public IP. Forward UDP port 5030
REM on your router to this PC first.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0start-smg2.ps1" -Players 1
echo.
pause
