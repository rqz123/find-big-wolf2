@echo off
rem Launch SmartCam Watcher in the background (tray icon) and close this window.
rem First run (camera.device_index: -1) needs the console to pick a camera,
rem so it runs in the foreground until setup is done.
cd /d "%~dp0"

findstr /r /c:"^ *device_index: *-1" config.yaml >nul 2>&1
if not errorlevel 1 (
    .\venv\Scripts\python.exe main.py
    pause
    exit /b
)

start "" ".\venv\Scripts\pythonw.exe" main.py
exit /b
