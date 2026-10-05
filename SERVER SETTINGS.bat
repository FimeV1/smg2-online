@echo off
REM Opens the server settings (port, player limit, shared world, what is shared)
REM in Notepad. Save the file, then restart the server for changes to apply.
if not exist "%~dp0server\server-settings.ini" (
    echo The settings file is created the first time the server runs.
    echo Start the server once with HOST A GAME.bat, then run this again.
    pause
    goto :eof
)
start "" notepad "%~dp0server\server-settings.ini"
