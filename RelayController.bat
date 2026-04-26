@echo off
chcp 65001 >nul
setlocal

set "EXE=%~dp0dist\RelayController.exe"

:: ── If exe already exists, just run GUI ──────────────────────────────────────
if exist "%EXE%" (
    start "" "%EXE%"
    exit /b 0
)

echo ============================================================
echo   RelayController.exe not found. Building now...
echo ============================================================
echo.

:: ── Check Python ─────────────────────────────────────────────────────────────
python --version >nul 2>&1
if not errorlevel 1 goto :build

:: Python not installed — download installer
echo Python is not installed. Downloading Python 3.12 installer...
echo.

where curl >nul 2>&1
if errorlevel 1 (
    echo [ERROR] curl not found. Please install Python manually from https://python.org
    pause
    exit /b 1
)

set "PY_URL=https://www.python.org/ftp/python/3.12.9/python-3.12.9-amd64.exe"
set "PY_INST=%TEMP%\python_installer.exe"

curl -L -o "%PY_INST%" "%PY_URL%"
if errorlevel 1 (
    echo [ERROR] Download failed. Check your internet connection.
    pause
    exit /b 1
)

echo Installing Python (this may take a minute)...
"%PY_INST%" /quiet InstallAllUsers=0 PrependPath=1 Include_test=0
if errorlevel 1 (
    echo [ERROR] Python installation failed.
    pause
    exit /b 1
)

:: Refresh PATH
for /f "tokens=2*" %%a in ('reg query "HKCU\Environment" /v PATH 2^>nul') do set "PATH=%%b;%PATH%"
echo Python installed OK.
echo.

:build
:: ── Install deps and build ────────────────────────────────────────────────────
python -m pip install --upgrade pip --quiet
python -m pip install pyinstaller pyserial --quiet

python -m PyInstaller ^
    --onefile ^
    --console ^
    --name "RelayController" ^
    --add-data ".env;." ^
    --hidden-import pc_relay_script ^
    "%~dp0gui.py"

if errorlevel 1 (
    echo.
    echo [ERROR] Build failed.
    pause
    exit /b 1
)

echo.
echo Build complete! Launching...
start "" "%EXE%"
endlocal
