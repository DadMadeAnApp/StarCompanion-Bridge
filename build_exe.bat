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

if not exist "assets\bridge.ico" (
    python tools\make_icon.py
    if errorlevel 1 (
        echo ERROR: could not generate assets\bridge.ico
        pause
        exit /b 1
    )
)

rem Fail early and loudly if the checkout is incomplete. PyInstaller will
rem otherwise build a perfectly valid exe that dies at startup with
rem "No module named 'telemetry'", which looks like a build bug rather than
rem a missing folder.
for %%D in (telemetry core ui) do (
    if not exist "%%D\__init__.py" (
        echo ERROR: %%D\__init__.py is missing -- this checkout is incomplete.
        echo Run:  git status     and   git log --oneline -1
        echo Then re-pull the repository before building.
        pause
        exit /b 1
    )
)

rem --paths . and the explicit local-package imports are belt and braces:
rem PyInstaller normally finds these three by following the imports, but if it
rem is ever run against a stale spec or from an odd working directory, a
rem silently missing package only shows up when the user launches the exe.
python -m PyInstaller --onefile --windowed --clean --noconfirm ^
    --name StarCompanionBridge ^
    --icon assets\bridge.ico ^
    --paths . ^
    --add-data "assets\bridge.ico;assets" ^
    --hidden-import=pynput.keyboard._win32 ^
    --hidden-import=pynput.mouse._win32 ^
    --hidden-import=telemetry ^
    --hidden-import=telemetry.parsers ^
    --hidden-import=telemetry.session ^
    --hidden-import=telemetry.tailer ^
    --hidden-import=core ^
    --hidden-import=core.autostart ^
    --hidden-import=core.config ^
    --hidden-import=core.events ^
    --hidden-import=core.status ^
    --hidden-import=ui ^
    --hidden-import=ui.app ^
    --hidden-import=ui.log_handler ^
    --hidden-import=ui.settings ^
    --hidden-import=ui.theme ^
    --hidden-import=ui.widgets ^
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
