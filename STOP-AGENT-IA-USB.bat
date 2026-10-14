@echo off
setlocal
title Agent IA USB - Stop
color 0B
chcp 65001 >nul

:: Agent IA USB root = folder of this script (works from any drive letter)
set "AGENT_IA_USB_ROOT=%~dp0"
set "AGENT_IA_USB_ROOT=%AGENT_IA_USB_ROOT:~0,-1%"

:: Portable Python on the USB first, then the PC's Python
set "PY=%AGENT_IA_USB_ROOT%\02_RUNTIME\python\python.exe"
if not exist "%PY%" set "PY=python"

echo ============================================
echo    Agent IA USB - Stop (safe to remove the USB)
echo ============================================
echo.

"%PY%" "%AGENT_IA_USB_ROOT%\03_AGENT\core\launcher.py" --stop

echo.
echo Agent IA USB stopped. You can now safely remove the USB drive.
pause
