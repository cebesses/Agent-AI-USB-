@echo off
setlocal enabledelayedexpansion
title Agent IA USB - Convert Model
color 0B
chcp 65001 >nul

set "AGENT_IA_USB_ROOT=%~dp0"
set "AGENT_IA_USB_ROOT=!AGENT_IA_USB_ROOT:~0,-1!"
set "PY=!AGENT_IA_USB_ROOT!\02_RUNTIME\python\python.exe"

echo ============================================
echo    Agent IA USB - Model Converter
echo ============================================
echo  Converts HuggingFace models (safetensors)
echo  to GGUF format for use with Agent IA USB.
echo ============================================
echo.

if not exist "!PY!" (
    echo [ERROR] Embedded Python not found. Run SETUP-USB.bat first.
    pause
    exit /b 1
)

if "%~1"=="" (
    echo Scanning 01_MODEL\ for convertible models...
    echo.
    "!PY!" -I "!AGENT_IA_USB_ROOT!\03_AGENT\convert_model.py" %*
) else (
    "!PY!" -I "!AGENT_IA_USB_ROOT!\03_AGENT\convert_model.py" %*
)

echo.
pause
exit /b %errorlevel%
