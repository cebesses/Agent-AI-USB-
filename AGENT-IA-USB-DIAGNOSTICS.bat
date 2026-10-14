@echo off
setlocal
title Agent IA USB Diagnostics
color 0E
chcp 65001 >nul
set "AGENT_IA_USB_ROOT=%~dp0"
set "AGENT_IA_USB_ROOT=%AGENT_IA_USB_ROOT:~0,-1%"
set "PY=%AGENT_IA_USB_ROOT%\02_RUNTIME\python\python.exe"
if not exist "%PY%" set "PY=python"

echo ============================================
echo    Agent IA USB System Diagnostics
echo ============================================
echo.
echo --- Python ---
"%PY%" --version 2>nul || echo [ERROR] Python not found - run SETUP-USB.bat
echo.
echo --- Hardware ---
"%PY%" "%AGENT_IA_USB_ROOT%\03_AGENT\core\hardware.py"
echo.
echo --- Models ---
dir /b "%AGENT_IA_USB_ROOT%\01_MODEL\*.gguf" 2>nul || echo [ERROR] No models - run SETUP-USB.bat
dir /b "%AGENT_IA_USB_ROOT%\01_MODEL\vision\*.gguf" 2>nul
echo.
echo --- Runtime ---
if exist "%AGENT_IA_USB_ROOT%\02_RUNTIME\llama.cpp\llama-server.exe" (echo [OK] CPU runtime) else (echo [ERROR] CPU runtime missing - run SETUP-USB.bat)
if exist "%AGENT_IA_USB_ROOT%\02_RUNTIME\llama.cpp-vulkan\llama-server.exe" (echo [OK] Vulkan GPU runtime) else (echo [INFO] No Vulkan runtime - CPU only)
echo.
echo --- Runtime launch test ---
"%AGENT_IA_USB_ROOT%\02_RUNTIME\llama.cpp\llama-server.exe" --version >nul 2>&1
if errorlevel 1 (
  echo [WARN] No se pudo ejecutar llama-server.exe.
  echo        Suele ser el antivirus/SmartScreen bloqueando un .exe sin firma en el USB.
  echo        Soluciones: Unblock-File, excluir la carpeta en el antivirus, o copiar Agent IA USB al disco C:.
) else (
  echo [OK] llama-server.exe se ejecuta correctamente
)
echo.
echo --- Model server ---
"%PY%" "%AGENT_IA_USB_ROOT%\03_AGENT\core\launcher.py" --status
echo.
echo --- Voice module (optional) ---
if exist "%AGENT_IA_USB_ROOT%\02_RUNTIME\whisper\whisper-cli.exe" (echo [OK] whisper.cpp STT) else (echo [INFO] No whisper.cpp - no voice input. SETUP-USB.bat installs it.)
if exist "%AGENT_IA_USB_ROOT%\02_RUNTIME\whisper\models\ggml-base.bin" (echo [OK] whisper model base) else (echo [INFO] No whisper model)
if exist "%AGENT_IA_USB_ROOT%\02_RUNTIME\piper\piper.exe" (echo [OK] Piper TTS) else (echo [INFO] No Piper - no spoken replies.)
dir /b "%AGENT_IA_USB_ROOT%\02_RUNTIME\piper\voices\*.onnx" 2>nul || echo [INFO] No TTS voices installed
"%PY%" -c "import sounddevice" 2>nul && (echo [OK] sounddevice \(microphone\)) || (echo [INFO] sounddevice not installed)
echo.
echo --- Unit tests (no model needed) ---
"%PY%" "%AGENT_IA_USB_ROOT%\03_AGENT\eval_suite.py"
echo.
echo ============================================
echo    Diagnostics complete
echo ============================================
pause
