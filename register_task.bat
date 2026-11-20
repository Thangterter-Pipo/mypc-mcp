@echo off
cd /d "%~dp0"
set "TASK_NAME=MyPC MCP"
set "PYW=%~dp0.venv\Scripts\pythonw.exe"
set "SCRIPT=%~dp0start_mcp.py"

schtasks /Create /TN "%TASK_NAME%" /TR "\"%PYW%\" \"%SCRIPT%\"" /SC ONLOGON /RL LIMITED /F
if %errorlevel%==0 (
    echo Scheduled Task "%TASK_NAME%" created. It will start automatically at logon.
) else (
    echo Failed to create task. Try running Command Prompt as Administrator.
)
