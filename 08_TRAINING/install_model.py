#!/usr/bin/env python3
"""
Install a fine-tuned Agent IA USB model (.gguf from agent_ia_usb_finetune.ipynb) and adopt it
only if it does at least as well as the original on the agent evaluation.

  python 08_TRAINING\\install_model.py 08_TRAINING\\Qwen3.5-4B-AGENT-IA-USB-Q4_K_M.gguf
  python 08_TRAINING\\install_model.py <file.gguf> --base fast --no-eval
"""
import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path

AGENT_IA_USB_ROOT = Path(__file__).resolve().parent.parent
MODELS = AGENT_IA_USB_ROOT / "06_CONFIG" / "models.json"
AGENT_CFG = AGENT_IA_USB_ROOT / "06_CONFIG" / "agent.json"
PY = sys.executable


def run(*args):
    return subprocess.run([PY, *map(str, args)]).returncode


def last_score(label):
    path = AGENT_IA_USB_ROOT / "08_TRAINING" / "eval_results.jsonl"
    score = None
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            r = json.loads(line)
            if r["label"] == label:
                score = (r["passed"], r["total"])
    return score


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("gguf")
    ap.add_argument("--base", choices=["smart", "fast"], help="tier it was trained from (default: from the file name)")
    ap.add_argument("--no-eval", action="store_true", help="register without comparing (not adopted automatically)")
    args = ap.parse_args()

    src = Path(args.gguf).resolve()
    if not src.is_file():
        print(f"Not found: {src}")
        return 1
    base = args.base or ("smart" if "9b" in src.name.lower() else "fast")
    models = json.loads(MODELS.read_text(encoding="utf-8"))
    base_cfg = models["tiers"][base]
    tier = f"{base}-agent-ia-usb"

    dest = AGENT_IA_USB_ROOT / "01_MODEL" / src.name
    if src != dest:
        print(f"Moving {src.name} to 01_MODEL...")
        shutil.move(str(src), dest)
    entry = {k: v for k, v in base_cfg.items() if not k.startswith("hf_")}
    entry.update({"label": dest.stem.replace("-Q4_K_M", ""), "model_file": dest.name,
                  "size_bytes": dest.stat().st_size, "auto_select": False, "fine_tuned_from": base})
    models["tiers"][tier] = entry
    MODELS.write_text(json.dumps(models, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"Registered tier '{tier}' ({entry['label']}).")
    if args.no_eval:
        print(f"Try it in Agent IA USB with the command:  model {tier}")
        return 0

    launcher = AGENT_IA_USB_ROOT / "03_AGENT" / "core" / "launcher.py"
    evaluator = AGENT_IA_USB_ROOT / "03_AGENT" / "eval_agent.py"
    base_label = base_cfg["label"]
    if last_score(base_label) is None:
        print(f"\n=== Evaluating the original model ({base_label}) ===")
        run(launcher, "--start", "--tier", base, "--restart")
        run(evaluator, "--label", base_label, "--no-log")
    print(f"\n=== Evaluating the fine-tuned model ({entry['label']}) ===")
    run(launcher, "--start", "--tier", tier, "--restart")
    run(evaluator, "--label", entry["label"], "--no-log")
    run(evaluator, "--compare", base_label, entry["label"])

    old, new = last_score(base_label), last_score(entry["label"])
    if new and old and new[0] >= old[0]:
        cfg = json.loads(AGENT_CFG.read_text(encoding="utf-8"))
        cfg["model_tier"] = tier
        AGENT_CFG.write_text(json.dumps(cfg, indent=2), encoding="utf-8")
        print(f"\nAdopted: Agent IA USB now uses {entry['label']} ({new[0]}/{new[1]} vs {old[0]}/{old[1]}).")
    else:
        print(f"\nNot adopted: the original scored better ({old} vs {new}). "
              f"You can still try it with the command: model {tier}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
