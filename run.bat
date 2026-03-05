@echo off
echo =============================================
echo   School Bus Tracker — D205 District
echo =============================================
echo.

python --version >nul 2>&1
if %errorlevel% neq 0 (
    echo ERROR: Python not found. Install from https://python.org
    echo        Make sure to check "Add Python to PATH" during install.
    pause & exit /b 1
)

echo Installing dependencies...
python -m pip install -r requirements.txt --quiet
echo.
echo Starting server...
echo Access at: http://localhost:5000
echo Press Ctrl+C to stop.
echo.
python app.py
pause
