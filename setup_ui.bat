@echo off
cd /d "%~dp0"
if exist ".venv\Scripts\python.exe" (
    ".venv\Scripts\python.exe" setup_ui.py
) else (
    python setup_ui.py
)
pause
