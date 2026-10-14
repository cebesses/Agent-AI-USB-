@echo off
setlocal enabledelayedexpansion
title Agent IA USB - Make GitHub release zip
color 0B
chcp 65001 >nul

set "AGENT_IA_USB_ROOT=%~dp0"
set "AGENT_IA_USB_ROOT=%AGENT_IA_USB_ROOT:~0,-1%"
set "PY=%AGENT_IA_USB_ROOT%\02_RUNTIME\python\python.exe"
if not exist "%PY%" set "PY=python"

set "VERSION=v1.0.0"
if not "%~1"=="" set "VERSION=%~1"
set "OUT=%AGENT_IA_USB_ROOT%\AGENT-IA-USB-%VERSION%.zip"

echo ============================================
echo    Building AGENT-IA-USB-%VERSION%.zip
echo    Code only - models are downloaded by SETUP-USB.bat
echo    (private data - sessions, memory, journal - is excluded)
echo ============================================
echo.

"%PY%" -c "
import sys, zipfile
from pathlib import Path
root, out = Path(sys.argv[1]), Path(sys.argv[2])

# mirrors .gitignore: big binaries, per-machine state and private user data
SKIP_DIRS = {
    '01_MODEL', '02_RUNTIME', '07_BACKUPS', '__pycache__', '.git', '.zcode',
    '04_WORKSPACE', '05_KNOWLEDGE', '06_CONFIG/hardware',
    '03_AGENT/sessions', '03_AGENT/journal', '03_AGENT/cache', '03_AGENT/checkpoints',
    '08_TRAINING/data',
}
SKIP_FILES = {
    '06_CONFIG/models_cache.json', '06_CONFIG/autotune_results.json',
    '06_CONFIG/runtime_state.json', '06_CONFIG/active_profile.txt',
    '03_AGENT/memory/episodic.json', '03_AGENT/skills/test_skill.json',
    '08_TRAINING/eval_results.jsonl',
}

def skip(rel):
    s = rel.as_posix()
    if any(s == d or s.startswith(d + '/') for d in SKIP_DIRS):
        return True
    if s in SKIP_FILES:
        return True
    if rel.name.startswith('agent-ia-usb-v') and rel.suffix == '.zip':
        return True
    if rel.suffix in ('.zip', '.log', '.pyc'):
        return True
    return False

count = 0
with zipfile.ZipFile(out, 'w', zipfile.ZIP_DEFLATED, compresslevel=9) as z:
    for p in sorted(root.rglob('*')):
        if not p.is_file():
            continue
        rel = p.relative_to(root)
        if skip(rel):
            continue
        z.write(p, rel)
        count += 1
print(f'[OK] {out.name}: {count} files, {out.stat().st_size / 1024 / 1024:.1f} MB')
" "%AGENT_IA_USB_ROOT%" "%OUT%"
if errorlevel 1 (
  echo [ERROR] Could not build the zip.
  pause
  exit /b 1
)

echo.
echo Next steps:
echo   1. git init ^&^& git add . ^&^& git commit -m "%VERSION%"
echo   2. Create the GitHub repo, push, and upload %OUT% as a release asset.
echo      ^(see docs\PUBLISH.md^)
echo.
pause
