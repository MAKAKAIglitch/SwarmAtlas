@echo off
setlocal
cd /d "%~dp0"

where py >nul 2>&1
if errorlevel 1 (
    echo [SwarmAtlas Updater] Python Launcher "py" was not found.
    echo Install Python 3.11 or newer to run the updater.
    echo.
    pause
    exit /b 1
)

echo.
echo SwarmAtlas Updater
echo Close SwarmAtlas before updating.
echo The updater preserves .venv, local MMDB files, settings, databases and logs.
echo.
py -3 -u "%~dp0update_project.py"
set "UPDATE_EXIT=%ERRORLEVEL%"

echo.
if "%UPDATE_EXIT%"=="0" (
    echo [SwarmAtlas Updater] Done. Start run_windows.bat.
) else (
    echo [SwarmAtlas Updater] Update failed with code %UPDATE_EXIT%.
)
echo.
pause
exit /b %UPDATE_EXIT%
