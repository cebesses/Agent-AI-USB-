#!/usr/bin/env python3
"""
Convert HuggingFace models (safetensors) to GGUF for Agent IA USB.

Usage:
  python convert_model.py                        # scan 01_MODEL/ and convert all found
  python convert_model.py path/to/model-folder   # convert a specific HF model
  python convert_model.py --quant Q5_K_M          # choose quantization (default Q4_K_M)
  python convert_model.py --list                  # just list what's convertible

Supported input formats:
  .safetensors  — full auto-conversion (recommended)
  .pt / .bin    — needs PyTorch installed (~2 GB); will warn if missing
  .onnx         — NOT supported (different paradigm, no conversion path)

The script:
  1. Detects HuggingFace model folders (config.json + weight files)
  2. Installs required Python packages if missing
  3. Downloads convert_hf_to_gguf.py from llama.cpp (cached)
  4. Converts to F16 GGUF
  5. Quantizes with llama-quantize.exe to the chosen level
  6. Moves the original folder to 01_MODEL/_originals/
"""
import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MODEL_DIR = ROOT / "01_MODEL"
RUNTIME_DIR = ROOT / "02_RUNTIME"
QUANTIZE_EXE = RUNTIME_DIR / "llama.cpp" / "llama-quantize.exe"
CONVERTER_CACHE = RUNTIME_DIR / "convert_hf_to_gguf.py"
ORIGINALS_DIR = MODEL_DIR / "_originals"

LLAMA_CPP_CONVERTER_URL = (
    "https://raw.githubusercontent.com/ggml-org/llama.cpp/master/convert_hf_to_gguf.py"
)

REQUIRED_PACKAGES = ["numpy", "sentencepiece", "gguf", "safetensors"]
TORCH_PACKAGES = ["torch", "transformers"]

VALID_QUANTS = [
    "Q2_K", "Q3_K_S", "Q3_K_M", "Q3_K_L",
    "Q4_0", "Q4_K_S", "Q4_K_M",
    "Q5_0", "Q5_K_S", "Q5_K_M",
    "Q6_K", "Q8_0", "F16", "F32",
]


def say(msg):
    print(f"[convert] {msg}", flush=True)


def python_exe():
    embedded = RUNTIME_DIR / "python" / "python.exe"
    return str(embedded) if embedded.exists() else sys.executable


def pip_install(packages):
    py = python_exe()
    say(f"Installing: {', '.join(packages)}")
    r = subprocess.run(
        [py, "-m", "pip", "install", "--no-warn-script-location", "-q"] + packages,
        capture_output=True, text=True,
    )
    if r.returncode != 0:
        say(f"pip install failed:\n{r.stderr[:500]}")
        return False
    return True


def check_packages(need_torch=False):
    py = python_exe()
    missing = []
    pkgs = REQUIRED_PACKAGES + (TORCH_PACKAGES if need_torch else [])
    for pkg in pkgs:
        r = subprocess.run(
            [py, "-c", f"import {pkg.replace('-', '_')}"],
            capture_output=True,
        )
        if r.returncode != 0:
            missing.append(pkg)
    if missing:
        if not pip_install(missing):
            return False
    return True


def download_converter():
    if CONVERTER_CACHE.exists() and CONVERTER_CACHE.stat().st_size > 10000:
        say("Using cached convert_hf_to_gguf.py")
        return True
    say("Downloading convert_hf_to_gguf.py from llama.cpp...")
    try:
        urllib.request.urlretrieve(LLAMA_CPP_CONVERTER_URL, str(CONVERTER_CACHE))
        say(f"Downloaded ({CONVERTER_CACHE.stat().st_size // 1024} KB)")
        return True
    except Exception as e:
        say(f"Download failed: {e}")
        return False


def find_hf_models(scan_dir=None):
    scan_dir = Path(scan_dir) if scan_dir else MODEL_DIR
    models = []
    if scan_dir.is_dir() and (scan_dir / "config.json").exists():
        models.append(scan_dir)
        return models
    if not scan_dir.is_dir():
        return models
    for entry in sorted(scan_dir.iterdir()):
        if not entry.is_dir() or entry.name.startswith(("_", ".")):
            continue
        if entry.name == "vision":
            continue
        config = entry / "config.json"
        if config.exists():
            has_weights = (
                list(entry.glob("*.safetensors"))
                or list(entry.glob("*.bin"))
                or list(entry.glob("*.pt"))
            )
            if has_weights:
                models.append(entry)
    return models


def detect_format(model_dir):
    if list(model_dir.glob("*.safetensors")):
        return "safetensors"
    if list(model_dir.glob("*.bin")) or list(model_dir.glob("*.pt")):
        return "pytorch"
    if list(model_dir.glob("*.onnx")):
        return "onnx"
    return None


def model_name_from_config(model_dir):
    try:
        cfg = json.loads((model_dir / "config.json").read_text(encoding="utf-8"))
        name = cfg.get("_name_or_path", "") or cfg.get("model_type", "") or model_dir.name
        return Path(name).name
    except Exception:
        return model_dir.name


def convert_one(model_dir, quant="Q4_K_M"):
    fmt = detect_format(model_dir)
    name = model_name_from_config(model_dir)
    say(f"Model: {name} ({model_dir.name})")
    say(f"Format: {fmt}")

    if fmt == "onnx":
        say("ONNX models cannot be converted to GGUF. There is no conversion path.")
        say("Look for a GGUF version on https://huggingface.co (search: model name + GGUF)")
        return False

    if fmt == "pytorch":
        say("PyTorch (.pt/.bin) models need the 'torch' package (~2 GB download).")
        if not check_packages(need_torch=True):
            say("Could not install torch. Try: pip install torch --index-url https://download.pytorch.org/whl/cpu")
            return False
    elif fmt == "safetensors":
        if not check_packages(need_torch=False):
            return False
    else:
        say(f"No weight files found in {model_dir}")
        return False

    if not download_converter():
        return False

    if not QUANTIZE_EXE.exists():
        say(f"llama-quantize.exe not found at {QUANTIZE_EXE}")
        return False

    slug = re.sub(r"[^a-zA-Z0-9._-]", "-", name).strip("-")
    f16_path = MODEL_DIR / f"{slug}-F16.gguf"
    final_path = MODEL_DIR / f"{slug}-{quant}.gguf"

    if final_path.exists():
        say(f"Already exists: {final_path.name} — skipping")
        return True

    py = python_exe()

    say(f"Converting to F16 GGUF (this may take several minutes)...")
    r = subprocess.run(
        [py, "-I", str(CONVERTER_CACHE), str(model_dir),
         "--outfile", str(f16_path), "--outtype", "f16"],
        capture_output=True, text=True, timeout=3600,
    )
    if r.returncode != 0 or not f16_path.exists():
        say(f"Conversion failed (exit {r.returncode})")
        if r.stderr:
            for line in r.stderr.strip().splitlines()[-10:]:
                print(f"    {line}")
        if r.stdout:
            for line in r.stdout.strip().splitlines()[-5:]:
                print(f"    {line}")
        return False

    f16_size = f16_path.stat().st_size / 1024**3
    say(f"F16 GGUF created: {f16_path.name} ({f16_size:.1f} GB)")

    if quant == "F16":
        final_path = f16_path
    else:
        say(f"Quantizing to {quant}...")
        r = subprocess.run(
            [str(QUANTIZE_EXE), str(f16_path), str(final_path), quant],
            capture_output=True, text=True, timeout=3600,
        )
        if r.returncode != 0 or not final_path.exists():
            say(f"Quantization failed (exit {r.returncode})")
            if r.stderr:
                for line in r.stderr.strip().splitlines()[-10:]:
                    print(f"    {line}")
            return False
        f16_path.unlink()
        say(f"F16 intermediate deleted.")

    final_size = final_path.stat().st_size / 1024**3
    say(f"Done: {final_path.name} ({final_size:.1f} GB)")

    ORIGINALS_DIR.mkdir(parents=True, exist_ok=True)
    dest = ORIGINALS_DIR / model_dir.name
    if dest.exists():
        say(f"Original folder already in _originals/, leaving {model_dir.name} in place.")
    else:
        shutil.move(str(model_dir), str(dest))
        say(f"Original moved to {dest.relative_to(ROOT)}")

    return True


def list_convertible(scan_dir=None):
    models = find_hf_models(scan_dir)
    if not models:
        say("No HuggingFace models found in 01_MODEL/.")
        say("To convert: download a model from HuggingFace and put the folder in 01_MODEL/")
        return
    for m in models:
        fmt = detect_format(m)
        name = model_name_from_config(m)
        weights = list(m.glob("*.safetensors")) + list(m.glob("*.bin")) + list(m.glob("*.pt"))
        total = sum(f.stat().st_size for f in weights) / 1024**3
        status = "convertible" if fmt in ("safetensors", "pytorch") else "NOT supported"
        print(f"  {m.name}/  ({name}, {fmt}, {total:.1f} GB) — {status}")


def main():
    ap = argparse.ArgumentParser(description="Convert HuggingFace models to GGUF")
    ap.add_argument("model", nargs="?", help="Path to HF model folder (default: scan 01_MODEL/)")
    ap.add_argument("--quant", default="Q4_K_M", choices=VALID_QUANTS,
                    help="Quantization level (default: Q4_K_M)")
    ap.add_argument("--list", action="store_true", help="List convertible models without converting")
    args = ap.parse_args()

    if args.list:
        list_convertible(args.model)
        return 0

    if args.model:
        path = Path(args.model).resolve()
        if not path.is_dir():
            say(f"Not a directory: {path}")
            return 1
        return 0 if convert_one(path, args.quant) else 1

    models = find_hf_models()
    if not models:
        say("No HuggingFace models found in 01_MODEL/.")
        say("")
        say("How to use:")
        say("  1. Download a model from HuggingFace (the folder with config.json + .safetensors)")
        say("  2. Put the folder inside 01_MODEL/")
        say("  3. Run this script again")
        say("")
        say("Example: huggingface-cli download Qwen/Qwen3-8B --local-dir 01_MODEL/Qwen3-8B")
        return 0

    say(f"Found {len(models)} model(s) to convert:")
    for m in models:
        fmt = detect_format(m)
        name = model_name_from_config(m)
        print(f"  {m.name}/  ({name}, {fmt})")
    print()

    ok = 0
    for m in models:
        if convert_one(m, args.quant):
            ok += 1
        print()

    say(f"Converted {ok}/{len(models)} model(s).")
    return 0 if ok == len(models) else 1


if __name__ == "__main__":
    sys.exit(main())
