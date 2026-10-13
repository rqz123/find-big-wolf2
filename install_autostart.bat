@echo off
rem Start SmartCam Watcher automatically when the current user logs on,
rem by placing a shortcut in the user's Startup folder (no admin needed).
cd /d "%~dp0"

powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "$s = (New-Object -ComObject WScript.Shell).CreateShortcut([Environment]::GetFolderPath('Startup') + '\SmartCam Watcher.lnk');" ^
  "$s.TargetPath = '%~dp0venv\Scripts\pythonw.exe';" ^
  "$s.Arguments = '\"%~dp0main.py\"';" ^
  "$s.WorkingDirectory = '%~dp0';" ^
  "$s.Description = 'SmartCam Watcher';" ^
  "$s.Save()"

if errorlevel 1 (
    echo Failed to create startup shortcut.
) else (
    echo Autostart enabled: "%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup\SmartCam Watcher.lnk"
)
pause
