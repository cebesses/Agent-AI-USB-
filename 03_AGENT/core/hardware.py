#!/usr/bin/env python3
"""
Agent IA USB Hardware Detection
Detects all system hardware at runtime. Never hardcodes values.
"""
import os, sys, json, platform, subprocess, shutil
from pathlib import Path
from datetime import datetime
from typing import Dict, Any


def run_ps(cmd: str, timeout: int = 10) -> str:
    try:
        r = subprocess.run(
            ["powershell", "-NoProfile", "-Command", cmd],
            capture_output=True, text=True, timeout=timeout,
            encoding="utf-8", errors="replace"
        )
        return r.stdout.strip()
    except Exception:
        return ""


def run_cmd(cmd: str, timeout: int = 15) -> str:
    try:
        r = subprocess.run(
            cmd, shell=True, capture_output=True, text=True,
            timeout=timeout, encoding="utf-8", errors="replace"
        )
        return r.stdout.strip()
    except Exception:
        return ""


def detect_windows_version():
    info = {
        "platform": platform.system(),
        "hostname": platform.node(),
    }
    raw = run_ps("[System.Environment]::OSVersion.VersionString")
    if raw:
        info["full_version"] = raw.split("\n")[0].strip()
    return info


def detect_cpu():
    cpu = {"name": "Unknown", "cores_physical": 0, "cores_logical": 0, "max_freq_mhz": 0, "architecture": platform.machine()}
    raw = run_ps("(Get-CimInstance Win32_Processor).Name")
    if raw:
        cpu["name"] = raw.split("\n")[0].strip()
    cores = run_ps("[int](Get-CimInstance Win32_Processor).NumberOfCores")
    if cores and cores.isdigit():
        cpu["cores_physical"] = int(cores)
    logical = run_ps("[int](Get-CimInstance Win32_Processor).NumberOfLogicalProcessors")
    if logical and logical.isdigit():
        cpu["cores_logical"] = int(logical)
    freq = run_ps("[int](Get-CimInstance Win32_Processor).MaxClockSpeed")
    if freq and freq.isdigit():
        cpu["max_freq_mhz"] = int(freq)
    if cpu["cores_logical"] == 0:
        cpu["cores_logical"] = os.cpu_count() or 1
    if cpu["cores_physical"] == 0:
        cpu["cores_physical"] = cpu["cores_logical"]
    return cpu


def detect_ram():
    ram = {"total_mb": 0, "available_mb": 0, "free_mb": 0, "percent_used": 0}
    total = run_ps("[int]((Get-CimInstance Win32_ComputerSystem).TotalPhysicalMemory / 1MB)")
    if total and total.isdigit():
        ram["total_mb"] = int(total)
    free = run_ps("[int]((Get-CimInstance Win32_OperatingSystem).FreePhysicalMemory / 1KB)")
    if free and free.isdigit():
        ram["free_mb"] = int(free)
        ram["available_mb"] = int(free)
    if ram["total_mb"] > 0 and ram["free_mb"] > 0:
        ram["percent_used"] = round((1 - ram["free_mb"] / ram["total_mb"]) * 100, 1)
    return ram


def detect_gpu():
    gpu = {"available": False, "name": "None", "vram_total_mb": 0, "vram_free_mb": 0, "driver_version": "", "cuda_capability": "", "cuda_dll_exists": False}
    raw = run_cmd('nvidia-smi --query-gpu=name,memory.total,memory.free,driver_version,compute_cap --format=csv,noheader,nounits')
    if raw and "not found" not in raw.lower():
        for line in raw.split("\n"):
            line = line.strip()
            if not line:
                continue
            parts = [p.strip() for p in line.split(",")]
            if len(parts) >= 5 and parts[0]:
                gpu["available"] = True
                gpu["name"] = parts[0]
                try:
                    gpu["vram_total_mb"] = int(parts[1])
                    gpu["vram_free_mb"] = int(parts[2])
                except ValueError:
                    pass
                gpu["driver_version"] = parts[3]
                gpu["cuda_capability"] = parts[4]
                break
    if not gpu["available"]:
        ps_name = run_ps("(Get-CimInstance Win32_VideoController | Where-Object {$_.Name -like '*NVIDIA*'}).Name")
        if ps_name:
            gpu["name"] = ps_name.split("\n")[0].strip()
            gpu["available"] = True
    llama_dir = Path(__file__).resolve().parent.parent.parent / "02_RUNTIME" / "llama.cpp"
    gpu["cuda_dll_exists"] = (llama_dir / "ggml-cuda.dll").exists()
    return gpu


def detect_storage():
    storage = {"drives": [], "usb": None, "agent_ia_usb_root": str(Path(__file__).resolve().parent.parent.parent)}
    raw = run_ps(
        "Get-CimInstance Win32_LogicalDisk | "
        "Where-Object {$_.DriveType -eq 2 -or $_.DriveType -eq 3} | "
        'ForEach-Object { "$($_.DeviceID)|$($_.FileSystem)|$([int]($_.FreeSpace/1MB))|$([int]($_.Size/1MB))|$($_.VolumeName)|$($_.DriveType)" }'
    )
    for line in raw.split("\n"):
        line = line.strip()
        if not line or "|" not in line:
            continue
        parts = line.split("|")
        if len(parts) >= 6:
            drive = {
                "letter": parts[0].strip(),
                "filesystem": parts[1].strip(),
                "free_mb": int(parts[2].strip()) if parts[2].strip().isdigit() else 0,
                "size_mb": int(parts[3].strip()) if parts[3].strip().isdigit() else 0,
                "label": parts[4].strip(),
                "type": parts[5].strip(),
            }
            drive["free_gb"] = round(drive["free_mb"] / 1024, 2)
            drive["size_gb"] = round(drive["size_mb"] / 1024, 2)
            storage["drives"].append(drive)
            if drive["type"] == "2" and not storage["usb"]:
                storage["usb"] = drive
    return storage


def detect_tools():
    tools = {}
    for name, cmd in {"python": "python --version", "node": "node --version", "npm": "npm --version", "git": "git --version", "powershell": "powershell -Command $PSVersionTable.PSVersion.ToString()"}.items():
        raw = run_cmd(cmd, timeout=10)
        first = raw.split("\n")[0].strip() if raw else ""
        tools[name] = {"available": bool(first), "version": first}
    llama = Path(__file__).resolve().parent.parent.parent / "02_RUNTIME" / "llama.cpp" / "llama-server.exe"
    tools["llama_cpp"] = {"available": llama.exists(), "path": str(llama)}
    # Toolchains for run_code / pentest (presence only, via PATH lookup - fast, no subprocess).
    for name, exe in {"gcc": "gcc", "g++": "g++", "clang": "clang", "dotnet": "dotnet", "csc": "csc",
                      "java": "java", "javac": "javac", "go": "go", "rustc": "rustc", "cargo": "cargo",
                      "tsc": "tsc", "nmap": "nmap"}.items():
        path = shutil.which(exe)
        tools[name] = {"available": bool(path), "path": path or ""}
    return tools


def detect_usb_info():
    agent_ia_usb_root = Path(__file__).resolve().parent.parent.parent
    info = {"agent_ia_usb_root": str(agent_ia_usb_root), "drive_letter": str(agent_ia_usb_root.drive) if agent_ia_usb_root.drive else "", "free_gb": 0, "total_gb": 0}
    try:
        u = shutil.disk_usage(str(agent_ia_usb_root))
        info["total_gb"] = round(u.total / (1024**3), 2)
        info["free_gb"] = round(u.free / (1024**3), 2)
    except Exception:
        pass
    return info


def detect_all():
    hw = {
        "timestamp": datetime.now().isoformat(),
        "windows": detect_windows_version(),
        "cpu": detect_cpu(),
        "ram": detect_ram(),
        "gpu": detect_gpu(),
        "storage": detect_storage(),
        "tools": detect_tools(),
        "usb": detect_usb_info(),
    }
    vram = hw["gpu"]["vram_total_mb"]
    ram_total = hw["ram"]["total_mb"]
    hw["tier"] = "HIGH" if vram >= 8000 and ram_total >= 32000 else "MEDIUM" if vram >= 4000 and ram_total >= 16000 else "LOW" if vram >= 2000 and ram_total >= 8000 else "MINIMAL"
    hw["recommended_gpu_layers"] = 33 if vram >= 8000 else 25 if vram >= 6000 else 18 if vram >= 4000 else 8 if vram >= 2000 else 0
    hw["recommended_context"] = 8192 if ram_total >= 32000 else 4096 if ram_total >= 16000 else 2048 if ram_total >= 8000 else 1024
    return hw


def format_hardware_report(hw):
    lines = [
        "=" * 60,
        "Agent IA USB HARDWARE DETECTION REPORT",
        "=" * 60,
        f"  Detected: {hw.get('timestamp', '?')}",
        f"  Tier: {hw.get('tier', '?')}",
        "",
        "  Windows: " + hw["windows"].get("full_version", "?"),
        "  Hostname: " + hw["windows"].get("hostname", "?"),
        "",
        "  CPU: " + hw["cpu"].get("name", "?"),
        f"  Cores: {hw['cpu'].get('cores_physical', '?')} physical, {hw['cpu'].get('cores_logical', '?')} logical",
        f"  Freq: {hw['cpu'].get('max_freq_mhz', '?')} MHz",
        "",
        f"  RAM: {hw['ram'].get('total_mb', 0)} MB ({round(hw['ram'].get('total_mb', 0)/1024, 1)} GB)",
        f"  Free: {hw['ram'].get('free_mb', 0)} MB ({hw['ram'].get('percent_used', 0)}% used)",
        "",
        "  GPU: " + hw["gpu"].get("name", "None"),
        f"  VRAM: {hw['gpu'].get('vram_total_mb', 0)} MB ({hw['gpu'].get('vram_free_mb', 0)} free)",
        f"  Driver: {hw['gpu'].get('driver_version', '?')}",
        f"  CUDA Cap: {hw['gpu'].get('cuda_capability', '?')}",
        f"  CUDA DLL: {'YES' if hw['gpu'].get('cuda_dll_exists') else 'NO'}",
        "",
        f"  USB Free: {hw['usb'].get('free_gb', 0)} GB",
        "",
        f"  Recommended GPU Layers: {hw.get('recommended_gpu_layers', 0)}",
        f"  Recommended Context: {hw.get('recommended_context', 2048)}",
        "",
        "  Tools:",
    ]
    for name, info in hw.get("tools", {}).items():
        s = "[OK]" if info.get("available") else "[--]"
        lines.append(f"    {s} {name}: {info.get('version', '')[:50]}")
    lines.append("=" * 60)
    return "\n".join(lines)


if __name__ == "__main__":
    hw = detect_all()
    print(format_hardware_report(hw))
    config_path = Path(__file__).resolve().parent.parent.parent / "06_CONFIG" / "hardware" / "detected.json"
    config_path.parent.mkdir(parents=True, exist_ok=True)
    with open(config_path, "w") as f:
        json.dump(hw, f, indent=2, default=str)
    print(f"\nSaved to: {config_path}")
