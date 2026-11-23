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
    echo "Please download and install Python 3.9+ from https://www.python.org/"
    echo ""
    read -p "Press Enter to exit..."
    exit 1
fi

# 2. Check everything the tracker needs, installing missing packages and the browser
echo "[1/2] Checking that everything is ready..."
if ! python3 preflight.py --fix --dashboard; then
    echo ""
    echo -e "\033[1;31m[ERROR] Fix the problems marked FAIL above, then run this again.\033[0m"
    read -p "Press Enter to exit..."
    exit 1
fi

# 3. Launch the application
echo "[2/2] Launching FastAPI Dashboard Server..."
echo ""
echo -e "\033[1;32mDashboard will open automatically at http://localhost:8000\033[0m"
echo "Keep this terminal window open while using the tracker."
echo ""
python3 api.py
