# SYNC-USB.ps1 - copies Agent IA USB (code, runtime, voice, models) to the USB drive.
# Never deletes anything on the USB. Keeps the USB's own memory/skills/sessions.
#
#   powershell -ExecutionPolicy Bypass -File SYNC-USB.ps1 -Drive D
#   powershell -ExecutionPolicy Bypass -File SYNC-USB.ps1 -Drive D -ListOnly   (dry run)

param(
    [Parameter(Mandatory = $true)][string]$Drive,
    [switch]$ListOnly,
    [string]$Source = ''
)

$ErrorActionPreference = 'Stop'
if (-not $Source) { $Source = $PSScriptRoot }
$letter = $Drive.TrimEnd(':', '\')
$dest = "$letter`:\agent-ia-usb"

# 1. The drive must be present
$vol = Get-CimInstance Win32_LogicalDisk -Filter "DeviceID='$letter`:'" -ErrorAction SilentlyContinue
if (-not $vol) { throw "Drive $letter`: is not connected. Plug the USB in and run again." }
if ($vol.DriveType -ne 2 -and -not $ListOnly) {
    throw "Drive $letter`: is not a removable drive (type $($vol.DriveType)). Refusing to copy there."
}

# 2. Enough free space (models are ~19 GB)
$needGB = [math]::Round(((Get-ChildItem (Join-Path $Source '01_MODEL') -Recurse -File | Measure-Object Length -Sum).Sum +
                          (Get-ChildItem (Join-Path $Source '02_RUNTIME') -Recurse -File | Measure-Object Length -Sum).Sum +
                          (Get-ChildItem (Join-Path $Source '03_AGENT') -Recurse -File | Measure-Object Length -Sum).Sum) / 1GB, 1)
$freeGB = [math]::Round($vol.FreeSpace / 1GB, 1)
Write-Host "Source : $Source"
Write-Host "Dest   : $dest  (free $freeGB GB, need about $needGB GB)"
if (-not $ListOnly -and $freeGB -lt ($needGB + 1)) { throw "Not enough free space on $letter`:." }

# 3. What to skip (generated per PC, runtime junk, git, or the USB's own user data)
$xd = @('.git', '.zcode', '__pycache__', 'pip-*', '04_WORKSPACE', '07_BACKUPS', 'checkpoints',
        '03_AGENT\sessions', '03_AGENT\journal', '03_AGENT\cache', '06_CONFIG\hardware',
        '02_RUNTIME\slots')
$xf = @('agent-ia-usb-v*.zip', '*.pyc', '*.pyo', '*.log', 'models_cache.json', 'autotune_results.json',
        'runtime_state.json', 'active_profile.txt', 'episodic.json', 'skill_*.json', '.gitignore')

$flags = @('/E', '/R:2', '/W:2', '/NP', '/NDL', '/NFL')
if ($ListOnly) { $flags += '/L' }
$args2 = @($Source, $dest) + $flags + @('/XD') + $xd + @('/XF') + $xf
& robocopy @args2 | Out-Host
$code = $LASTEXITCODE

# robocopy exit codes 0-7 are success (1 = files copied, 2/3 = extras), 8+ = failure
if ($code -ge 8) { throw "robocopy failed (code $code)." }
Write-Host ""
if ($ListOnly) { Write-Host "Dry run finished. Nothing was copied." }
else { Write-Host "Done. Agent IA USB is on $letter`:\agent-ia-usb (robocopy code $code)." }
