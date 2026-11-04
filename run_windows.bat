@echo off
setlocal
cd /d "%~dp0"

set "VENV_PY=%~dp0.venv\Scripts\python.exe"

if not exist "%VENV_PY%" (
    echo [SwarmAtlas] Creating Python 3.12 environment...
    where py >nul 2>&1
    if errorlevel 1 (
        echo Python Launcher "py" was not found.
        echo Install Python 3.12 with the Python Launcher enabled.
        goto :setup_failed
    )

    py -3.12 -m venv ".venv"
    if errorlevel 1 (
        echo Could not create the virtual environment.
        echo Make sure Python 3.12 is installed: py -0p
        goto :setup_failed
    )
)

if not exist "%VENV_PY%" (
    echo The virtual environment was not created correctly.
    goto :setup_failed
)

"%VENV_PY%" -c "import PySide6, psutil, geoip2, swarmatlas" >nul 2>&1
if errorlevel 1 (
    echo [SwarmAtlas] Installing application dependencies...
    "%VENV_PY%" -m pip install -e ".[geoip]"
    if errorlevel 1 (
        echo Dependency installation failed.
        echo Check your internet connection and try again.
        goto :setup_failed
    )
)

echo [SwarmAtlas] Starting...
"%VENV_PY%" -m swarmatlas
set "APP_EXIT=%ERRORLEVEL%"

if not "%APP_EXIT%"=="0" (
    echo SwarmAtlas exited with code %APP_EXIT%.
    pause
)

exit /b %APP_EXIT%

:setup_failed
echo.
echo SwarmAtlas setup did not complete.
pause
exit /b 1
