#!/usr/bin/env bash

echo "=============================================================="
echo " Kinetics-700 High-Speed Downsampler & Manager Launcher"
echo "=============================================================="
echo ""

# Change to the directory of the script
cd "$(dirname "$0")"

# Check if Python 3 is installed
if ! command -v python3 &> /dev/null
then
    echo "[ERROR] python3 could not be found. Please install Python 3."
    exit 1
fi

# Check if .venv exists
if [ ! -d ".venv" ]; then
    echo "[INFO] Virtual environment not found. Creating one..."
    python3 -m venv .venv
    if [ $? -ne 0 ]; then
        echo "[ERROR] Failed to create virtual environment. Ensure python3-venv is installed."
        exit 1
    fi
    echo "[INFO] Virtual environment created successfully."
fi

# Activate virtual environment
source .venv/bin/activate

# Check if dependencies are installed by checking for PyQt6
python -c "import PyQt6" >/dev/null 2>&1
if [ $? -ne 0 ]; then
    echo "[INFO] Installing required dependencies..."
    pip install --upgrade pip
    pip install -r requirements.txt
    if [ $? -ne 0 ]; then
        echo "[ERROR] Failed to install dependencies."
        exit 1
    fi
    echo "[INFO] Dependencies installed successfully."
fi

echo "Active virtual environment: .venv"
echo "Launching GUI app.py..."
echo ""

python app.py

if [ $? -ne 0 ]; then
    echo ""
    echo "[ERROR] Application exited with error code $?."
fi
