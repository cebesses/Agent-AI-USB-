@echo off
setlocal
set "AGENT_IA_USB_ROOT=%~dp0"
set "AGENT_IA_USB_ROOT=%AGENT_IA_USB_ROOT:~0,-1%"
set "PY=%AGENT_IA_USB_ROOT%\02_RUNTIME\python\python.exe"
if not exist "%PY%" set "PY=python"
echo Starting the model server only (no agent)...
"%PY%" "%AGENT_IA_USB_ROOT%\03_AGENT\core\launcher.py" --start --no-warmup
"%PY%" "%AGENT_IA_USB_ROOT%\03_AGENT\core\launcher.py" --status
echo Server log: %AGENT_IA_USB_ROOT%\02_RUNTIME\server.log
pause
