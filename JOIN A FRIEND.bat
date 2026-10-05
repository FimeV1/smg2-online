@echo off
REM Join someone else's SMG2 Online server with one local player.
REM Asks for the server address; pressing Enter reuses the last one.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0start-smg2.ps1" -Players 1 -Join
echo.
pause
