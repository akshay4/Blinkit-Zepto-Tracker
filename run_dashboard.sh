#!/bin/bash

# Make sure we are in the script's directory
cd "$(dirname "$0")"

echo "===================================================================="
# Output title in bold blue color
echo -e "\033[1;34m  Blinkit & Zepto Stock Tracker Setup & Launcher 🛒 🚀\033[0m"
echo "===================================================================="
echo ""

# 1. Verify Python 3
if ! command -v python3 &> /dev/null; then
    echo -e "\033[1;31m[ERROR] python3 is not installed or not in your system PATH.\033[0m"
    echo "Please download and install Python 3.8+ from https://www.python.org/"
    echo ""
    read -p "Press Enter to exit..."
    exit 1
fi

# 2. Check and Install Dependencies
echo "[1/3] Checking and installing Python dependencies..."
python3 -m pip install --upgrade pip
python3 -m pip install -r requirements.txt
if [ $? -ne 0 ]; then
    echo -e "\033[1;31m[ERROR] Failed to install dependencies.\033[0m"
    read -p "Press Enter to exit..."
    exit 1
fi

# 3. Install Playwright browser
echo "[2/3] Checking and installing Playwright Chromium browser..."
python3 -m playwright install chromium
if [ $? -ne 0 ]; then
    echo -e "\033[1;31m[ERROR] Failed to install Playwright browser.\033[0m"
    read -p "Press Enter to exit..."
    exit 1
fi

# 4. Launch the application
echo "[3/3] Launching FastAPI Dashboard Server..."
echo ""
echo -e "\033[1;32mDashboard will open automatically at http://localhost:8000\033[0m"
echo "Keep this terminal window open while using the tracker."
echo ""
python3 api.py
