@echo off
echo ========================================
echo  StarCompanion Bridge -- Build EXE
echo ========================================
echo.

pip install --quiet pyinstaller websockets pynput cryptography
if errorlevel 1 (
    echo ERROR: pip install failed. Make sure Python 3.10+ is installed.
    pause
    exit /b 1
)

pyinstaller --onefile --name StarCompanionBridge --hidden-import=pynput.keyboard._win32 --hidden-import=pynput.mouse._win32 bridge_server.py
if errorlevel 1 (
    echo ERROR: PyInstaller failed.
    pause
    exit /b 1
)

echo.
echo Done! Distribute:  dist\StarCompanionBridge.exe
echo.
pause
