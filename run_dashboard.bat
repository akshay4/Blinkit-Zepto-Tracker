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
    echo Please install Python 3.8+ from https://www.python.org/ and make sure
    echo to check the box "Add Python to PATH" during installation.
    echo.
    pause
    exit /b 1
)

:: 2. Install Python Dependencies
echo [1/3] Checking and installing Python dependencies...
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
if %errorlevel% neq 0 (
    echo [ERROR] Failed to install dependencies from requirements.txt.
    pause
    exit /b 1
)

:: 3. Install Playwright Chromium Browser
echo [2/3] Checking and installing Playwright Chromium browser...
python -m playwright install chromium
if %errorlevel% neq 0 (
    echo [ERROR] Failed to install Playwright browser.
    pause
    exit /b 1
)

:: 4. Launch the Dashboard
echo [3/3] Launching FastAPI Dashboard Server...
echo.
echo Dashboard will open automatically at http://localhost:8000
echo Keep this window open while using the tracker.
echo.
python api.py

pause
