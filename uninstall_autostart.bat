@echo off
rem Remove the SmartCam Watcher shortcut from the user's Startup folder.
set "LNK=%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup\SmartCam Watcher.lnk"

if exist "%LNK%" (
    del "%LNK%"
    echo Autostart disabled.
) else (
    echo Autostart was not enabled.
)
pause
