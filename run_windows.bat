@echo off
setlocal
cd /d "%~dp0"

where py >nul 2>&1
if errorlevel 1 (
    echo [SwarmAtlas] Python Launcher "py" was not found.
    echo Install Python 3.11 or newer and enable the Python Launcher.
    echo.
    pause
    exit /b 1
)

echo [SwarmAtlas] Starting launcher. Output is also saved to a log file.
py -3 -u "%~dp0run_windows.py"
set "RUN_EXIT=%ERRORLEVEL%"

echo.
if not "%RUN_EXIT%"=="0" (
    echo [SwarmAtlas] Launcher or application exited with code %RUN_EXIT%.
) else (
    echo [SwarmAtlas] Application has closed.
)
echo Logs are stored in %%LOCALAPPDATA%%\SwarmAtlas\logs
echo.
pause
exit /b %RUN_EXIT%
