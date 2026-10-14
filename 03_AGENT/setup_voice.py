#!/usr/bin/env python3
"""
Agent IA USB voice module setup (called by SETUP-USB.bat, or standalone):

  python setup_voice.py            install whisper.cpp + Piper + Spanish voice
  python setup_voice.py --small    use the more accurate whisper 'small' model
  python setup_voice.py --remove   delete the voice components

Downloads (~250 MB total, resumable):
  - whisper.cpp Windows binaries          (~22 MB)
  - whisper ggml model, base or small     (~148 / ~488 MB)
  - Piper TTS Windows binaries            (~21 MB)
  - Spanish voice es_MX-ald-medium        (~63 MB)
  - sounddevice (pip, mic access)         (~1 MB)

Everything lands in 02_RUNTIME, so Agent IA USB stays portable: no installs on the PC.
"""
import argparse
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
WHISPER_DIR = RUNTIME / "whisper"
PIPER_DIR = RUNTIME / "piper"

sys.path.insert(0, str(Path(__file__).resolve().parent))
from setup_usb import say, download  # same resumable curl download


def github_asset(repo, asset_name, per_page=8):
    """(url, size) of `asset_name` in the repo's recent releases, or (None, None)."""
    url = f"https://api.github.com/repos/{repo}/releases?per_page={per_page}"
    with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "AGENT-IA-USB-setup"}), timeout=30) as r:
        releases = json.loads(r.read().decode())
    for rel in releases:
        for asset in rel.get("assets", []):
            if asset["name"] == asset_name:
                return asset["browser_download_url"], asset["size"]
    return None, None


def extract_zip_members(zip_path, dest, strip):
    """Extract a zip into `dest`, dropping the leading `strip` folder level(s)."""
    dest.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(zip_path) as z:
        for info in z.infolist():
            if info.is_dir():
                continue
            parts = info.filename.split("/")
            if len(parts) <= strip:
                continue
            out = dest.joinpath(*parts[strip:])
            out.parent.mkdir(parents=True, exist_ok=True)
            with z.open(info) as src, open(out, "wb") as dst:
                shutil.copyfileobj(src, dst)


def install_whisper(stt_model):
    url, size = github_asset("ggml-org/whisper.cpp", "whisper-bin-x64.zip")
    if not url:
        say("[ERROR] No whisper.cpp Windows build found in its releases.")
        return False
    say("[1/4] whisper.cpp binaries...")
    if not (WHISPER_DIR / "whisper-cli.exe").exists():
        zip_path = RUNTIME / "whisper-bin-x64.zip"
        if not download(url, zip_path, size):
            return False
        # the zip packs everything under Release/ - flatten it into 02_RUNTIME/whisper
        extract_zip_members(zip_path, WHISPER_DIR, strip=1)
        zip_path.unlink()
    say("[OK] whisper.cpp ready")
    say(f"[2/4] whisper '{stt_model}' model (Spanish + English)...")
    model_url = f"https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-{stt_model}.bin"
    return download(model_url, WHISPER_DIR / "models" / f"ggml-{stt_model}.bin")


def install_piper():
    url, size = github_asset("rhasspy/piper", "piper_windows_amd64.zip")
    if not url:
        say("[ERROR] No Piper Windows build found in its releases.")
        return False
    say("[3/4] Piper TTS binaries...")
    if not (PIPER_DIR / "piper.exe").exists():
        zip_path = RUNTIME / "piper.zip"
        if not download(url, zip_path, size):
            return False
        # the zip packs everything under piper/ - flatten it into 02_RUNTIME/piper
        extract_zip_members(zip_path, PIPER_DIR, strip=1)
        zip_path.unlink()
    say("[OK] Piper ready")
    say("[4/4] Spanish voice (es_MX-ald-medium)...")
    base = "https://huggingface.co/rhasspy/piper-voices/resolve/v1.0.0/es/es_MX/ald/medium/es_MX-ald-medium"
    voices = PIPER_DIR / "voices"
    if not download(f"{base}.onnx", voices / "es_MX-ald-medium.onnx"):
        return False
    return download(f"{base}.onnx.json", voices / "es_MX-ald-medium.onnx.json")


def install_sounddevice():
    if sys.executable and Path(sys.executable).name.lower().startswith("python"):
        r = subprocess.run([sys.executable, "-m", "pip", "install", "--quiet", "sounddevice"],
                           capture_output=True, timeout=600)
        if r.returncode == 0:
            say("[OK] sounddevice installed (microphone access)")
            return True
    say("[WARN] Could not pip-install sounddevice; Agent IA USB will start fine without voice.")
    return False


def remove_voice():
    for d in (WHISPER_DIR, PIPER_DIR):
        if d.exists():
            shutil.rmtree(d, ignore_errors=True)
            say(f"[OK] removed {d}")
    subprocess.run([sys.executable, "-m", "pip", "uninstall", "-y", "-q", "sounddevice"],
                   capture_output=True, timeout=300)
    say("[OK] voice module removed")


def verify():
    sys.path.insert(0, str(AGENT_IA_USB_ROOT / "09_PLUGINS"))
    import voice
    vm = voice.VoiceManager(AGENT_IA_USB_ROOT, {})
    probs = vm.problems()
    if probs:
        for p in probs:
            say(f"[WARN] {p}")
        return False
    say("[OK] voice module verified: whisper + Piper + microphone detected")
    return True


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--small", action="store_true", help="whisper 'small' model (more accurate, +340 MB)")
    ap.add_argument("--remove", action="store_true", help="uninstall the voice components")
    args = ap.parse_args()

    if args.remove:
        remove_voice()
        return 0

    stt_model = "small" if args.small else "base"
    ok = install_whisper(stt_model) and install_piper() and install_sounddevice()
    if not ok:
        say("[ERROR] Voice setup did not finish. Run it again - downloads resume.")
        return 1
    print()
    verify()
    say("Done. Start Agent IA USB and type /voz on to talk to it.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
