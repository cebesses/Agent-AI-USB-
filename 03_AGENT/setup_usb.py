#!/usr/bin/env python3
"""
Agent IA USB USB setup (called by SETUP-USB.bat): checks the drive, downloads the
llama.cpp runtimes and the models listed in 06_CONFIG/models.json.
Every download resumes if interrupted.

  python setup_usb.py [--no-vulkan] [--only-tier smart|fast] [--update-llama]
"""
import argparse
import ctypes
import json
import re
import shutil
import subprocess
import sys
import urllib.request
import zipfile
from pathlib import Path

AGENT_IA_USB_ROOT = Path(__file__).resolve().parent.parent
RUNTIME = AGENT_IA_USB_ROOT / "02_RUNTIME"
MODEL_DIR = AGENT_IA_USB_ROOT / "01_MODEL"
GITHUB = "https://api.github.com/repos/ggml-org/llama.cpp/releases"


def say(msg):
    print(f"  {msg}", flush=True)


def check_drive():
    root = AGENT_IA_USB_ROOT.anchor
    fs = ctypes.create_unicode_buffer(64)
    ctypes.windll.kernel32.GetVolumeInformationW(ctypes.c_wchar_p(root), None, 0, None, None, None, fs, 64)
    free_gb = shutil.disk_usage(root).free / 1024 ** 3
    say(f"Drive {root} - {fs.value or '?'}, {free_gb:.1f} GB free")
    if fs.value.upper().startswith("FAT"):
        say("[ERROR] This drive is FAT32: it cannot store files bigger than 4 GB (the 9B model is 5.7 GB).")
        say("        Back up the drive, then format it as exFAT (right-click the drive > Format > exFAT).")
        return False
    return True


def download(url, dest, expected=None):
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists() and expected and dest.stat().st_size == expected:
        say(f"[OK] {dest.name} already downloaded")
        return True
    say(f"Downloading {dest.name}" + (f" ({expected / 1024 ** 3:.2f} GB)" if expected else "") + "...")
    r = subprocess.run(["curl.exe", "-L", "-C", "-", "--retry", "8", "--retry-delay", "5",
                        "--progress-bar", "-o", str(dest), url])
    if r.returncode not in (0, 33):  # 33 = server can't resume (file already complete)
        say(f"[ERROR] download failed (curl exit {r.returncode})")
        return False
    if expected and dest.stat().st_size != expected:
        say(f"[ERROR] {dest.name} is incomplete ({dest.stat().st_size} of {expected} bytes). Run setup again.")
        return False
    return True


def llama_build(folder):
    exe = folder / "llama-server.exe"
    if not exe.exists():
        return None
    try:
        out = subprocess.run([str(exe), "--version"], capture_output=True, text=True, timeout=30)
        m = re.search(r"build (\d+)", out.stdout + out.stderr)
        return int(m.group(1)) if m else None
    except Exception:
        return None


def release_asset(kind, build=None):
    """URL of the llama.cpp Windows zip for kind 'cpu' or 'vulkan' (same build if given)."""
    url = f"{GITHUB}/tags/b{build}" if build else f"{GITHUB}?per_page=10"
    with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "AGENT-IA-USB-setup"}), timeout=30) as r:
        data = json.loads(r.read().decode())
    releases = [data] if build else data
    pattern = re.compile(rf"^llama-b\d+-bin-win-{kind}-x64\.zip$")
    for rel in releases:
        for asset in rel.get("assets", []):
            if pattern.match(asset["name"]):
                return asset["browser_download_url"], asset["size"]
    return None, None


def install_runtime(kind, folder, build=None):
    url, size = release_asset(kind, build)
    if not url:
        say(f"[WARN] No llama.cpp {kind} build found" + (f" for b{build}" if build else ""))
        return False
    zip_path = RUNTIME / f"llama-{kind}.zip"
    if not download(url, zip_path, size):
        return False
    folder.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(zip_path) as z:
        z.extractall(folder)
    zip_path.unlink()
    say(f"[OK] llama.cpp {kind} runtime installed in {folder.name}")
    return True


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-vulkan", action="store_true", help="skip the GPU (Vulkan) runtime")
    ap.add_argument("--only-tier", choices=["smart", "fast"])
    ap.add_argument("--update-llama", action="store_true", help="replace the CPU runtime with the latest build")
    ap.add_argument("--no-mtp", action="store_true", help="skip the faster MTP model variants (~8.7 GB)")
    args = ap.parse_args()

    print("\n[2/3] Runtime")
    if not check_drive():
        return 1
    cpu_dir, vk_dir = RUNTIME / "llama.cpp", RUNTIME / "llama.cpp-vulkan"
    build = llama_build(cpu_dir)
    if build is None or args.update_llama:
        if not install_runtime("cpu", cpu_dir):
            return 1
        build = llama_build(cpu_dir)
    else:
        say(f"[OK] llama.cpp CPU runtime (build {build})")
    if not args.no_vulkan:
        if llama_build(vk_dir) == build:
            say(f"[OK] llama.cpp Vulkan runtime (build {build})")
        else:
            install_runtime("vulkan", vk_dir, build) or say("[WARN] Vulkan runtime skipped - Agent IA USB will use the CPU.")

    print("\n[3/3] Models")
    models = json.loads((AGENT_IA_USB_ROOT / "06_CONFIG" / "models.json").read_text(encoding="utf-8"))
    ok = True
    for name, t in models["tiers"].items():
        if args.only_tier and name != args.only_tier:
            continue
        if "hf_repo" not in t:  # fine-tuned tiers are installed by 08_TRAINING\install_model.py
            continue
        base = f"https://huggingface.co/{t['hf_repo']}/resolve/main"
        ok &= download(f"{base}/{t['hf_model']}", MODEL_DIR / t["model_file"], t.get("size_bytes"))
        if t.get("mmproj_file"):
            ok &= download(f"{base}/{t['hf_mmproj']}", MODEL_DIR / t["mmproj_file"], t.get("mmproj_size_bytes"))
        if t.get("mtp_file") and not args.no_mtp:  # faster variant (multi-token prediction); autotune decides
            mtp_url = f"https://huggingface.co/{t['hf_mtp_repo']}/resolve/main/{t['hf_mtp_model']}"
            ok &= download(mtp_url, MODEL_DIR / t["mtp_file"], t.get("mtp_size_bytes"))
    if not ok:
        return 1

    sys.path.insert(0, str(AGENT_IA_USB_ROOT / "03_AGENT"))
    from core.hardware import detect_all
    hw = detect_all()
    (AGENT_IA_USB_ROOT / "06_CONFIG" / "hardware").mkdir(parents=True, exist_ok=True)
    (AGENT_IA_USB_ROOT / "06_CONFIG" / "hardware" / "detected.json").write_text(json.dumps(hw, indent=2, default=str), encoding="utf-8")
    say(f"[OK] This PC: {hw['cpu']['name']}, {hw['ram']['total_mb'] // 1024} GB RAM, GPU: {hw['gpu']['name']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
