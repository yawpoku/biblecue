@echo off
echo ================================================
echo  BibleCue v2.0 - Setup
echo ================================================
echo.

REM --- Check Node.js ---
echo [1/4] Checking Node.js...
node --version >nul 2>&1
if %errorlevel% neq 0 (
    echo  ERROR: Node.js is not installed or not in PATH.
    echo  Please download and install Node.js from https://nodejs.org/
    echo  Then re-run this setup.
    pause
    exit /b 1
) else (
    for /f "tokens=*" %%v in ('node --version') do echo  OK: Node.js %%v found.
)
echo.

REM --- Check Python ---
echo [2/4] Checking Python...
python --version >nul 2>&1
if %errorlevel% equ 0 (
    for /f "tokens=*" %%v in ('python --version') do echo  OK: %%v found.
    set PYTHON_CMD=python
) else (
    python3 --version >nul 2>&1
    if %errorlevel% equ 0 (
        for /f "tokens=*" %%v in ('python3 --version') do echo  OK: %%v found.
        set PYTHON_CMD=python3
    ) else (
        echo  ERROR: Python is not installed or not in PATH.
        echo  Please download and install Python from https://www.python.org/
        echo  Make sure to check "Add Python to PATH" during installation.
        pause
        exit /b 1
    )
)
echo.

REM --- npm install ---
echo [3/4] Installing Node.js dependencies (npm install)...
cd /d "%~dp0"
npm install
if %errorlevel% neq 0 (
    echo  ERROR: npm install failed. Check the output above for details.
    pause
    exit /b 1
)
echo  OK: Node.js dependencies installed.
echo.

REM --- Python dependencies ---
echo [4/4] Installing Python dependencies...
echo  Installing core packages: websockets requests python-scriptures
pip install websockets requests python-scriptures
if %errorlevel% neq 0 (
    echo  WARNING: Some Python packages may not have installed correctly.
    echo  Try running: pip install websockets requests python-scriptures
)
echo.
echo  NOTE: Optional packages for audio/transcription:
echo    faster-whisper, sounddevice, scipy are only needed for Whisper mode.
echo    To install them run: install-deps.bat
echo.

echo ================================================
echo  Setup complete!
echo ================================================
echo.
echo  To launch BibleCue, run:
echo    start.bat
echo  or:
echo    npm start
echo.
pause
