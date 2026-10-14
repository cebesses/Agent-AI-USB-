@echo off
setlocal enabledelayedexpansion
title Agent IA USB Recovery
color 0D
echo ============================================
echo    Agent IA USB Recovery
echo ============================================
echo.
set "AGENT_IA_USB_ROOT=%~dp0"
set "AGENT_IA_USB_ROOT=!AGENT_IA_USB_ROOT:~0,-1!"

echo This will repair common Agent IA USB issues:
echo   - Missing directories
echo   - Corrupted cache/runtime state
echo.
echo [NOTE] Your projects, memory and models will NOT be deleted.
echo.
set /p CONFIRM="Continue? (Y/N): "
if /i not "!CONFIRM!"=="Y" (
    echo Cancelled.
    pause
    exit /b 0
)
echo.

:: Recreate missing directories (only the ones the agent actually uses)
echo [1/3] Checking directories...
for %%D in ("!AGENT_IA_USB_ROOT!\03_AGENT\core" "!AGENT_IA_USB_ROOT!\03_AGENT\orchestrator" "!AGENT_IA_USB_ROOT!\03_AGENT\memory" "!AGENT_IA_USB_ROOT!\03_AGENT\skills" "!AGENT_IA_USB_ROOT!\03_AGENT\sessions" "!AGENT_IA_USB_ROOT!\03_AGENT\journal" "!AGENT_IA_USB_ROOT!\04_WORKSPACE" "!AGENT_IA_USB_ROOT!\05_KNOWLEDGE\documents" "!AGENT_IA_USB_ROOT!\05_KNOWLEDGE\indexes" "!AGENT_IA_USB_ROOT!\06_CONFIG\hardware" "!AGENT_IA_USB_ROOT!\07_BACKUPS" "!AGENT_IA_USB_ROOT!\08_TRAINING\data" "!AGENT_IA_USB_ROOT!\evaluation\results") do (
    if not exist "%%~D" mkdir "%%~D" 2>nul
)
echo [OK] Directories verified

:: Clear a stale runtime state / slot caches (safe: they are rebuilt on next start)
echo [2/3] Clearing stale runtime state...
if exist "!AGENT_IA_USB_ROOT!\06_CONFIG\runtime_state.json" del /q "!AGENT_IA_USB_ROOT!\06_CONFIG\runtime_state.json" 2>nul
if exist "!AGENT_IA_USB_ROOT!\06_CONFIG\models_cache.json" del /q "!AGENT_IA_USB_ROOT!\06_CONFIG\models_cache.json" 2>nul
echo [OK] Runtime state cleared (the model re-detects on next start)

:: Verify core files
echo [3/3] Verifying core files...
set /a "MISSING=0"
for %%F in ("!AGENT_IA_USB_ROOT!\03_AGENT\agent.py" "!AGENT_IA_USB_ROOT!\03_AGENT\orchestrator\agent.py" "!AGENT_IA_USB_ROOT!\03_AGENT\core\config.py" "!AGENT_IA_USB_ROOT!\03_AGENT\core\llm_client.py" "!AGENT_IA_USB_ROOT!\03_AGENT\core\tools.py" "!AGENT_IA_USB_ROOT!\03_AGENT\core\policy.py" "!AGENT_IA_USB_ROOT!\03_AGENT\core\launcher.py" "!AGENT_IA_USB_ROOT!\03_AGENT\core\models.py" "!AGENT_IA_USB_ROOT!\03_AGENT\memory\memory.py") do (
    if not exist "%%~F" (
        echo [ERROR] Missing: %%~nxF
        set /a "MISSING+=1"
    )
)
if !MISSING! equ 0 (
    echo [OK] All core agent files present
) else (
    echo [WARN] !MISSING! files missing - re-copy Agent IA USB from a good backup
)

echo.
echo ============================================
echo    Recovery Complete
echo ============================================
pause
