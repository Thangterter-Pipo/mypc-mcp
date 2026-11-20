@echo off
cd /d "%~dp0"
set "PY=.venv\Scripts\python.exe"
if not exist "%PY%" set "PY=python"
set "PYW=.venv\Scripts\pythonw.exe"
if not exist "%PYW%" set "PYW=%PY%"

if /I "%~1"=="status" (
    "%PY%" start_mcp.py status
    exit /b %errorlevel%
)
if /I "%~1"=="stop" (
    "%PY%" start_mcp.py stop
    exit /b %errorlevel%
)
if /I "%~1"=="start" (
    start "" "%PYW%" start_mcp.py
    timeout /t 2 /nobreak >nul
    "%PY%" start_mcp.py status
    exit /b 0
)

echo Usage: start_mypc.bat [start ^| stop ^| status]
exit /b 1
