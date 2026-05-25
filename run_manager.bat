@echo off
title Kinetics-700 Dataset Manager Launcher
cd /d "%~dp0"
echo ==============================================================
echo  Kinetics-700 High-Speed Downsampler ^& Manager Launcher
echo ==============================================================
echo.

REM Check if .venv exists
if not exist .venv\Scripts\python.exe (
    echo [INFO] Virtual environment not found. Creating one...
    python -m venv .venv
    if %ERRORLEVEL% neq 0 (
        echo [ERROR] Failed to create virtual environment. Ensure Python is installed and in PATH.
        pause
        exit /b %ERRORLEVEL%
    )
    echo [INFO] Virtual environment created successfully.
)

REM Check if requirements are installed (we check for PyQt6 as an indicator)
.venv\Scripts\python.exe -c "import PyQt6" >nul 2>&1
if %ERRORLEVEL% neq 0 (
    echo [INFO] Installing required dependencies...
    .venv\Scripts\python.exe -m pip install --upgrade pip
    .venv\Scripts\python.exe -m pip install -r requirements.txt
    if %ERRORLEVEL% neq 0 (
        echo [ERROR] Failed to install dependencies.
        pause
        exit /b %ERRORLEVEL%
    )
    echo [INFO] Dependencies installed successfully.
)

echo Active virtual environment: .venv
echo Launching GUI app.py...
echo.

.venv\Scripts\python.exe app.py

if %ERRORLEVEL% neq 0 (
    echo.
    echo [ERROR] Application exited with error code %ERRORLEVEL%.
    pause
)
