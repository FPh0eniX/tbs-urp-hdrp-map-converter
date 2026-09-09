@echo off
cd /d "%~dp0"
".venv\Scripts\python.exe" -m PyInstaller --noconfirm --clean build.spec
echo.
echo Result: dist\URP Map Converter.exe
pause
