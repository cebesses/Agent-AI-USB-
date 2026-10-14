@echo off
setlocal
title Agent IA USB - Portable AI Agent
color 0B
chcp 65001 >nul

:: Agent IA USB root = folder of this script (works from any drive letter)
set "AGENT_IA_USB_ROOT=%~dp0"
set "AGENT_IA_USB_ROOT=%AGENT_IA_USB_ROOT:~0,-1%"
cd /d "%AGENT_IA_USB_ROOT%"

:: Portable Python on the USB first, then the PC's Python
set "PY=%AGENT_IA_USB_ROOT%\02_RUNTIME\python\python.exe"
if not exist "%PY%" set "PY=python"
"%PY%" --version >nul 2>&1
if errorlevel 1 goto NO_PYTHON

echo ============================================
echo    Agent IA USB - Portable AI Agent
echo ============================================
echo.

:: Agent IA USB detects the hardware, lets you pick a model (if more than one) and
:: starts the model server itself, then opens the terminal chat.
"%PY%" "%AGENT_IA_USB_ROOT%\03_AGENT\agent.py" --agent-ia-usb-root "%AGENT_IA_USB_ROOT%"
if errorlevel 1 goto FAILED

echo.
echo Stopping the model server...
"%PY%" "%AGENT_IA_USB_ROOT%\03_AGENT\core\launcher.py" --stop
exit /b 0

:NO_PYTHON
echo [ERROR] Python was not found.
echo         Run SETUP-USB.bat once (needs internet) to put a portable Python on the USB.
pause
exit /b 1

:FAILED
echo.
echo [ERROR] Agent IA USB could not start the model. See 02_RUNTIME\server.log or run AGENT-IA-USB-DIAGNOSTICS.bat
pause
exit /b 1
