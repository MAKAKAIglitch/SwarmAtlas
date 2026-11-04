@echo off
setlocal
cd /d "%~dp0"

set "VENV_PY=%~dp0.venv\Scripts\python.exe"
set "MATURIN=%~dp0.venv\Scripts\maturin.exe"

if not exist "%VENV_PY%" (
    echo [SwarmAtlas Rust] Run run_windows.bat once to create .venv first.
    echo.
    pause
    exit /b 1
)

where cargo >nul 2>&1
if errorlevel 1 (
    echo [SwarmAtlas Rust] Rust Cargo was not found.
    echo Install the stable Rust toolchain from https://rustup.rs/
    echo Then open a new terminal and run this file again.
    echo.
    pause
    exit /b 1
)

where rustc >nul 2>&1
if errorlevel 1 (
    echo [SwarmAtlas Rust] rustc was not found in PATH.
    echo Install the stable Rust toolchain from https://rustup.rs/
    echo.
    pause
    exit /b 1
)

echo [SwarmAtlas Rust] Installing the pinned maturin build frontend...
"%VENV_PY%" -m pip install "maturin==1.16.0"
if errorlevel 1 (
    echo [SwarmAtlas Rust] Could not install maturin.
    goto :failed
)

if not exist "%MATURIN%" (
    echo [SwarmAtlas Rust] maturin executable was not installed.
    goto :failed
)

set "VIRTUAL_ENV=%~dp0.venv"
set "PATH=%VIRTUAL_ENV%\Scripts;%PATH%"

echo [SwarmAtlas Rust] Compiling the read-only Windows network core...
pushd "%~dp0native"
"%MATURIN%" develop --release
set "BUILD_EXIT=%ERRORLEVEL%"
popd

if not "%BUILD_EXIT%"=="0" (
    echo [SwarmAtlas Rust] Native build failed.
    goto :failed
)

"%VENV_PY%" -c "import _swarmatlas_native as n; print('Rust core:', n.__version__); print('Features:', ', '.join(n.capabilities()))"
if errorlevel 1 goto :failed

echo.
echo [SwarmAtlas Rust] Native core is installed in this .venv.
echo Restart SwarmAtlas to use Rust TCP and UDP ownership tables.
echo.
pause
exit /b 0

:failed
echo.
echo [SwarmAtlas Rust] Build did not complete. Python fallback remains available.
echo.
pause
exit /b 1
