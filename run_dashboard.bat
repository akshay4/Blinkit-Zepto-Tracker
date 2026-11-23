@echo off
setlocal enabledelayedexpansion

echo ====================================================================
echo   Blinkit & Zepto Stock Tracker Setup & Launcher 🛒 🚀
echo ====================================================================
echo.

:: 1. Verify Python Installation
python --version >nul 2>&1
if %errorlevel% neq 0 (
    echo [ERROR] Python is not installed or not in your system PATH.
    echo Please install Python 3.9+ from https://www.python.org/ and make sure
    echo to check the box "Add Python to PATH" during installation.
    echo.
    pause
    exit /b 1
)

:: 2. Check everything the tracker needs, installing missing packages and the browser
echo [1/2] Checking that everything is ready...
python preflight.py --fix --dashboard
if %errorlevel% neq 0 (
    echo.
    echo [ERROR] Fix the problems marked FAIL above, then run this again.
    pause
    exit /b 1
)

:: 3. Launch the Dashboard
echo [2/2] Launching FastAPI Dashboard Server...
echo.
echo Dashboard will open automatically at http://localhost:8000
echo Keep this window open while using the tracker.
echo.
python api.py

pause
