@echo off
cd /d "%~dp0..\.."
call .venv\Scripts\activate.bat
echo Installing viz dependencies...
pip install -e ".[viz]"
echo.
echo Install complete. You can now run:
echo   streamlit run scripts\viz.py
pause
