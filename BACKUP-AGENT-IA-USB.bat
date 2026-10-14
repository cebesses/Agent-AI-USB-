@echo off
setlocal enabledelayedexpansion
title Agent IA USB - Backup
color 0B
chcp 65001 >nul

:: Agent IA USB root = folder of this script (works from any drive letter)
set "AGENT_IA_USB_ROOT=%~dp0"
set "AGENT_IA_USB_ROOT=!AGENT_IA_USB_ROOT:~0,-1!"

:: Locale-independent timestamp
for /f %%i in ('powershell -NoProfile -Command "Get-Date -Format yyyyMMdd-HHmmss"') do set "STAMP=%%i"

set "DEST=!AGENT_IA_USB_ROOT!\07_BACKUPS\backup-!STAMP!"

echo ============================================
echo    Agent IA USB - Backup
echo ============================================
echo  Copies your Agent IA USB data (config, memory, skills,
echo  sessions, journal, training data) to a timestamped
echo  folder. Skips the large model and runtime folders
echo  (01_MODEL, 02_RUNTIME).
echo.
echo  Destination: !DEST!
echo.

robocopy "!AGENT_IA_USB_ROOT!" "!DEST!" /E /XD "!AGENT_IA_USB_ROOT!\01_MODEL" "!AGENT_IA_USB_ROOT!\02_RUNTIME" "!AGENT_IA_USB_ROOT!\07_BACKUPS" "__pycache__" /XF "*.pyc" /NFL /NDL /NJH /NP >nul

if errorlevel 8 (
    echo [ERROR] Backup failed. Check the available disk space.
) else (
    echo [OK] Backup complete.
    echo       To restore: copy the contents of !DEST! back over Agent IA USB.
)
echo.
pause
