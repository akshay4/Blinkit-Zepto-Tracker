#!/bin/bash

# Make sure we are in the script's directory
cd "$(dirname "$0")"

echo "========================================================"
echo -e "\033[1;31m  Reset Blinkit & Zepto Stock Tracker 🛑 ♻️\033[0m"
echo "========================================================"
echo ""

# 1. Stop background processes
echo "Closing any running python tracker instances..."
pkill -f "python3 api.py"
pkill -f "python api.py"
echo "Done."
echo ""

# 2. Run reset python script
python3 reset_tracker.py
