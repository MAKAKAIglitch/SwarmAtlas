@echo off
setlocal
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
    echo SwarmAtlas environment was not found.
    echo Create it with:
    echo   py -3.12 -m venv .venv
    echo   .venv\Scripts\python -m pip install -e ".[geoip]"
    echo.
    pause
    exit /b 1
)

".venv\Scripts\python.exe" -m swarmatlas
