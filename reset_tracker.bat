@echo off
echo ========================================================
echo   Reset Blinkit & Zepto Stock Tracker 🛑 ♻️
echo ========================================================
echo.

:: 1. Force kill any lingering python/uvicorn/tracker instances to release file locks
echo Closing any running Python tracker or dashboard instances...
taskkill /F /FI "IMAGENAME eq python*" >nul 2>&1
taskkill /F /FI "IMAGENAME eq pythonw*" >nul 2>&1
echo Done.
echo.

:: 2. Run the reset Python script
python reset_tracker.py

pause
