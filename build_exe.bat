@echo off
chcp 65001 >nul
echo ============================================================
echo  Building RelayController.exe (single-file)
echo ============================================================
echo.

:: Check Python
python --version >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Python not found. Please install Python from https://python.org
    pause
    exit /b 1
)

:: Install / upgrade pip
python -m pip install --upgrade pip --quiet

:: Install PyInstaller
python -m pip install pyinstaller --quiet
if errorlevel 1 (
    echo [ERROR] Failed to install PyInstaller.
    pause
    exit /b 1
)

:: Install runtime deps
python -m pip install pyserial --quiet

:: Build single-file exe (--console so worker subprocess can use stdout pipe)
python -m PyInstaller ^
    --onefile ^
    --console ^
    --name "RelayController" ^
    --add-data ".env;." ^
    --hidden-import pc_relay_script ^
    gui.py

if errorlevel 1 (
    echo.
    echo [ERROR] Build failed. See output above.
    pause
    exit /b 1
)

echo.
echo ============================================================
echo  Done!  dist\RelayController.exe
echo ============================================================
pause
