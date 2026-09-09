@echo off
cd /d "%~dp0"
echo Creating virtual environment...
python -m venv .venv
".venv\Scripts\python.exe" -m pip install --upgrade pip
".venv\Scripts\python.exe" -m pip install -r requirements.txt
echo Done. Run run.bat
pause
