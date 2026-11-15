@echo off
cd /d "%~dp0..\.."
if not exist ".venv\Scripts\python.exe" (
    echo ERROR: .venv not found in %CD%
    pause
    exit /b 1
)
echo Starting Music Data Visualizer...
".venv\Scripts\python.exe" -m streamlit run scripts\viz.py
if errorlevel 1 (
    echo.
    echo Streamlit failed. If it is not installed, run scripts\windows\install_viz.bat first.
    pause
)
