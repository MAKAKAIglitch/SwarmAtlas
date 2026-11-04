@echo off
setlocal
cd /d "%~dp0"

set "VENV_PY=%~dp0.venv\Scripts\python.exe"
set "WHEEL="

echo Close SwarmAtlas before installing the native module.
echo.

if not exist "%VENV_PY%" (
    echo [SwarmAtlas Rust] Run run_windows.bat once to prepare the Python environment.
    echo.
    pause
    exit /b 1
)

if not exist "%~dp0native\dist" (
    echo [SwarmAtlas Rust] Create native\dist and copy the downloaded wheel there.
    echo Expected: swarmatlas_native-*-cp311-abi3-win_amd64.whl
    echo.
    pause
    exit /b 1
)

pushd "%~dp0native\dist"
for %%W in (swarmatlas_native-*-cp311-abi3-win_amd64.whl) do (
    if exist "%%~fW" set "WHEEL=%%~fW"
)
popd

if not defined WHEEL (
    echo [SwarmAtlas Rust] No compatible Windows x64 stable-ABI wheel was found.
    echo Put the file from the GitHub Actions artifact into native\dist.
    echo.
    pause
    exit /b 1
)

echo [SwarmAtlas Rust] Installing the prebuilt local wheel:
echo %WHEEL%
echo.
set "PIP_DISABLE_PIP_VERSION_CHECK=1"
"%VENV_PY%" -m pip install --no-index --no-deps --force-reinstall "%WHEEL%"
if errorlevel 1 goto :failed

"%VENV_PY%" -c "import _swarmatlas_native as n; print('Rust core:', n.__version__); print('Features:', ', '.join(n.capabilities())); print('TCP rows:', len(n.list_tcp_connections(10))); print('UDP bindings:', len(n.list_udp_bindings(10)))"
if errorlevel 1 goto :failed

echo.
echo [SwarmAtlas Rust] Prebuilt native core installed successfully.
echo Restart SwarmAtlas to activate it.
echo This install used the local wheel; no Rust compiler was run.
echo.
pause
exit /b 0

:failed
echo.
echo [SwarmAtlas Rust] Wheel installation or verification failed.
echo Python fallback remains available after restarting the app.
echo.
pause
exit /b 1
