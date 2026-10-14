@echo off
setlocal enabledelayedexpansion
title Agent IA USB - USB Setup
color 0B
chcp 65001 >nul

set "AGENT_IA_USB_ROOT=%~dp0"
set "AGENT_IA_USB_ROOT=!AGENT_IA_USB_ROOT:~0,-1!"
set "PYDIR=!AGENT_IA_USB_ROOT!\02_RUNTIME\python"
set "PYURL=https://www.python.org/ftp/python/3.12.10/python-3.12.10-embed-amd64.zip"

echo ============================================
echo    Agent IA USB - USB Setup  (run once, needs internet)
echo ============================================
echo  Puts everything Agent IA USB needs on this drive:
echo   - portable Python 3.12                (~25 MB)
echo   - llama.cpp runtime, CPU + Vulkan     (~150 MB)
echo   - Qwen3.5-9B + Qwen3.5-4B + vision    (~10 GB)
echo  Downloads resume if interrupted - just run it again.
echo ============================================
echo.

:: 1. Portable Python (lets Agent IA USB run on PCs without Python installed)
if exist "!PYDIR!\python.exe" goto HAVE_PYTHON
echo [1/3] Downloading portable Python...
if not exist "!PYDIR!" mkdir "!PYDIR!"
curl.exe -L --retry 5 --retry-delay 5 -o "!PYDIR!\python.zip" "!PYURL!"
if errorlevel 1 goto FAILED
tar -xf "!PYDIR!\python.zip" -C "!PYDIR!"
if errorlevel 1 goto FAILED
del /q "!PYDIR!\python.zip"
:: enable site-packages in the embeddable distribution
powershell -NoProfile -Command "$f='!PYDIR!\python312._pth'; (Get-Content $f) -replace '^#\s*import site','import site' | Set-Content -Encoding ascii $f"
curl.exe -L --retry 5 -o "!PYDIR!\get-pip.py" "https://bootstrap.pypa.io/get-pip.py"
if errorlevel 1 goto FAILED
"!PYDIR!\python.exe" "!PYDIR!\get-pip.py" --no-warn-script-location -q
if errorlevel 1 goto FAILED
del /q "!PYDIR!\get-pip.py"

:HAVE_PYTHON
echo [1/3] Portable Python ready.

:: 2-3. Runtimes and models (resumable downloads)
"!PYDIR!\python.exe" "!AGENT_IA_USB_ROOT!\03_AGENT\setup_usb.py" %*
if errorlevel 1 goto FAILED

:: Optional voice module (~250 MB): talk to Agent IA USB with whisper.cpp + Piper
choice /C SN /N /T 15 /D N /M "Install the VOICE module too? (talk to Agent IA USB, ~250 MB) [S/N]"
if errorlevel 2 goto NO_VOICE
"!PYDIR!\python.exe" "!AGENT_IA_USB_ROOT!\03_AGENT\setup_voice.py"
if errorlevel 1 echo [WARN] Voice setup did not finish - run SETUP-USB.bat again to retry.

:NO_VOICE

echo.
echo ============================================
echo    Setup complete. Start Agent IA USB with START-AGENT-IA-USB.bat
echo ============================================
pause
exit /b 0

:FAILED
echo.
echo [ERROR] Setup did not finish. Check the internet connection and run SETUP-USB.bat again.
pause
exit /b 1
