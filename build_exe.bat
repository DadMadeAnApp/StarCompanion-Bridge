@echo off
cd /d "%~dp0"
echo ========================================
echo  StarCompanion Bridge -- Build EXE
echo ========================================
echo.

where python >nul 2>&1
if errorlevel 1 (
    echo ERROR: Python not found on PATH. Install Python 3.10+ first.
    pause
    exit /b 1
)

python -m pip install --quiet pyinstaller websockets pynput cryptography qrcode
if errorlevel 1 (
    echo ERROR: pip install failed. Make sure Python 3.10+ is installed.
    pause
    exit /b 1
)

python -m PyInstaller --onefile --console --clean --noconfirm ^
    --name StarCompanionBridge ^
    --hidden-import=pynput.keyboard._win32 ^
    --hidden-import=pynput.mouse._win32 ^
    bridge_server.py
if errorlevel 1 (
    echo ERROR: PyInstaller failed.
    pause
    exit /b 1
)

if not exist "dist\StarCompanionBridge.exe" (
    echo ERROR: build reported success but dist\StarCompanionBridge.exe is missing.
    pause
    exit /b 1
)

echo.
echo Done! Distribute:  dist\StarCompanionBridge.exe
echo.
pause
