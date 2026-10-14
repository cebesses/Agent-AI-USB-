#!/usr/bin/env python3
"""
Agent IA USB Launcher - picks the right model for this PC and runs llama-server.

  python launcher.py --start [--tier auto|smart|fast] [--restart] [--no-warmup]
  python launcher.py --stop
  python launcher.py --status

Tier choice (06_CONFIG/models.json): 'smart' (Qwen3.5-9B) when the PC has
enough RAM, otherwise 'fast' (Qwen3.5-4B). A forced tier comes from --tier or
model_tier in 06_CONFIG/agent.json.
"""
import argparse
import ctypes
import hashlib
import json
import os
import re
import subprocess
import sys
import time
import urllib.request
from datetime import datetime
from pathlib import Path

AGENT_IA_USB_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(AGENT_IA_USB_ROOT / "03_AGENT"))

from core.config import AgentIaUsbConfig  # noqa: E402

STATE_FILE = AGENT_IA_USB_ROOT / "06_CONFIG" / "runtime_state.json"
LOG_FILE = AGENT_IA_USB_ROOT / "02_RUNTIME" / "server.log"
SLOT_DIR = AGENT_IA_USB_ROOT / "02_RUNTIME" / "slots"
RUNTIMES = {"cpu": AGENT_IA_USB_ROOT / "02_RUNTIME" / "llama.cpp", "vulkan": AGENT_IA_USB_ROOT / "02_RUNTIME" / "llama.cpp-vulkan"}

# Don't let a missing dependency DLL (e.g. vulkan-1.dll on a PC without GPU
# drivers) pop a blocking Windows "DLL not found" dialog. Child processes
# inherit this error mode, so llama binaries fail quietly with an exit code.
try:
    if os.name == "nt":
        ctypes.windll.kernel32.SetErrorMode(0x0001 | 0x0002)  # SEM_FAILCRITICALERRORS | SEM_NOGPFAULTERRORBOX
except Exception:
    pass

_DLL_NOT_FOUND = 3221225781  # 0xC0000135 STATUS_DLL_NOT_FOUND (a Vulkan build with no vulkan-1.dll)


def say(msg):
    print(f"[Agent IA USB] {msg}", flush=True)


_vulkan_loader = None


def vulkan_loader_present():
    """True only if the Vulkan loader (vulkan-1.dll) can actually be loaded.
    Without it the Vulkan llama.cpp build cannot run, and probing it would make
    Windows show a 'vulkan-1.dll was not found' error, so we skip Vulkan entirely
    and use the CPU (this is what happens on PCs without GPU drivers installed)."""
    global _vulkan_loader
    if _vulkan_loader is None:
        try:
            ctypes.WinDLL("vulkan-1.dll")
            _vulkan_loader = True
        except Exception:
            _vulkan_loader = False
    return _vulkan_loader


# ----------------------------------------------------------------- hardware
def total_ram_gb():
    class MEMSTAT(ctypes.Structure):
        _fields_ = [("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong),
                    ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
                    ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
                    ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong),
                    ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]
    m = MEMSTAT()
    m.dwLength = ctypes.sizeof(m)
    ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(m))
    return m.ullTotalPhys / 1024 ** 3


def hardware(refresh=False):
    """Detected hardware (cached in 06_CONFIG/hardware/detected.json for this PC)."""
    hw_file = AGENT_IA_USB_ROOT / "06_CONFIG" / "hardware" / "detected.json"
    try:
        hw = json.loads(hw_file.read_text(encoding="utf-8"))
        same_pc = hw.get("windows", {}).get("hostname") == os.environ.get("COMPUTERNAME", "")
        fresh = time.time() - datetime.fromisoformat(hw["timestamp"]).timestamp() < 7 * 86400
        if same_pc and fresh and not refresh:
            return hw
    except Exception:
        pass
    say("Detecting hardware...")
    from core.hardware import detect_all
    hw = detect_all()
    hw_file.parent.mkdir(parents=True, exist_ok=True)
    hw_file.write_text(json.dumps(hw, indent=2, default=str), encoding="utf-8")
    return hw


def choose_tier(cfg, requested=None):
    """Pick a model id. A specific id/alias is honored; 'auto' picks the biggest
    discovered model whose RAM need fits this PC (smallest if none fit)."""
    requested = requested or cfg.agent_config.get("model") or cfg.agent_config.get("model_tier", "auto")
    models = cfg.list_models()
    if not models:
        return None
    if requested and requested != "auto":
        info = cfg.model_registry.get(requested)
        if info:
            return info.id
        say(f"Model '{requested}' not found in 01_MODEL - choosing automatically.")
    ram = total_ram_gb()
    need = lambda m: max(m.ram_need_gb(), cfg.tier_config(m.id).get("min_ram_gb") or 0)
    fits = [m for m in models if ram >= need(m)]
    if fits:
        return fits[0].id                       # list() is biggest-first
    return min(models, key=lambda m: m.ram_need_gb()).id


_SOFTWARE_GPUS = re.compile(r"llvmpipe|swiftshader|basic render|lavapipe", re.IGNORECASE)
_INTEGRATED_NAMES = re.compile(r"radeon\(tm\) graphics|radeon graphics|vega \d+ graphics|"
                               r"intel\(r\) (uhd|iris|hd)? ?graphics|intel\(r\) arc\(tm\) graphics", re.IGNORECASE)


def vulkan_devices():
    """[(name, total_mib, free_mib, integrated)] as seen by the Vulkan build.
    llama-bench also reports 'uma: 1' for integrated GPUs that share system RAM.
    Returns [] (and runs nothing) when there is no Vulkan loader on this PC."""
    if not vulkan_loader_present():
        return []
    folder = RUNTIMES["vulkan"]
    exe = folder / "llama-bench.exe"
    if not exe.exists():
        exe = folder / "llama-server.exe"
    if not exe.exists():
        return []
    try:
        r = subprocess.run([str(exe), "--list-devices"], capture_output=True, text=True, errors="replace",
                           timeout=60, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except Exception:
        return []
    if r.returncode == _DLL_NOT_FOUND:  # Vulkan build started but a DLL (vulkan-1) is missing
        return []
    out = r.stdout + r.stderr
    uma = {int(i): flag == "1" for i, flag in re.findall(r"ggml_vulkan:\s*(\d+)\s*=.*?\|\s*uma:\s*(\d)", out)}
    devices = []
    for idx, name, total, free in re.findall(r"Vulkan(\d+):\s*(.+?)\s*\((\d+) MiB, (\d+) MiB free\)", out):
        if _SOFTWARE_GPUS.search(name):
            continue
        integrated = uma.get(int(idx), bool(_INTEGRATED_NAMES.search(name)))
        devices.append((name, int(total), int(free), integrated))
    return devices


def choose_runtime(cfg, tier_cfg):
    """Returns (runtime, reason). Vulkan only for a dedicated GPU that can hold the
    whole model. Integrated GPUs share the CPU's RAM bandwidth: on a Ryzen 5 5600G
    the Radeon iGPU made real requests slower than the CPU (51 s vs 58-74 s for the
    same task) because it processes new prompt tokens at ~10 tok/s vs ~31 on CPU."""
    wanted = cfg.agent_config.get("runtime", "auto")
    if wanted == "cpu":
        return "cpu", "set to cpu in agent.json"
    if not vulkan_loader_present():
        return "cpu", "no Vulkan driver on this PC (using the CPU)"
    devices = vulkan_devices()
    if not devices:
        return "cpu", "no Vulkan GPU found"
    if wanted == "vulkan":
        name = max(devices, key=lambda d: d[2])[0]
        return "vulkan", f"set to vulkan in agent.json ({name})"
    dedicated = [d for d in devices if not d[3]]
    if not dedicated:
        return "cpu", f"{devices[0][0]} is an integrated GPU (the CPU is faster for Agent IA USB)"
    name, _, free, _ = max(dedicated, key=lambda d: d[2])
    need = (tier_cfg.get("size_bytes", 0) + tier_cfg.get("mmproj_size_bytes", 0)) / 1024 ** 2 + 1024
    if free >= need:
        return "vulkan", f"{name}, {free} MiB free"
    return "cpu", f"{name} has {free} MiB free, the model needs ~{int(need)} MiB"


# ------------------------------------------------------------------- server
def _get(url, timeout=3):
    with urllib.request.urlopen(url, timeout=timeout) as r:
        return json.loads(r.read().decode())


def _post(url, payload, timeout=900):
    req = urllib.request.Request(url, data=json.dumps(payload).encode(), headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode())


def server_url(cfg):
    return cfg.server_url


def is_running(cfg):
    try:
        return _get(f"{server_url(cfg)}/health").get("status") == "ok"
    except Exception:
        return False


def _serves(cfg, model_name):
    """True if the server answering on the port is running this model file."""
    try:
        props = _get(f"{server_url(cfg)}/props", timeout=5)
        path = str(props.get("model_path") or props.get("default_generation_settings", {}).get("model", ""))
        return not path or Path(path).name.lower() == model_name
    except Exception:
        return False


def read_state():
    try:
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def model_file(cfg, tier, mtp=False):
    t = cfg.tier_config(tier)
    name = t.get("mtp_file") if mtp else t.get("model_file")
    return AGENT_IA_USB_ROOT / "01_MODEL" / name if name else None


def has_mtp(cfg, tier):
    f = model_file(cfg, tier, mtp=True)
    return bool(f and f.is_file())


def build_command(cfg, tier, runtime, hw, mtp=False, light=False):
    """llama-server command line. light=True is the quick autotune setup (no vision, small context)."""
    t = cfg.tier_config(tier)
    server = cfg.models.get("server", {})
    exe = RUNTIMES[runtime] / "llama-server.exe"
    # Generation is memory-bound (physical cores are best); prompt processing is
    # compute-bound (all logical cores help) - measured on a Ryzen 5 5600G.
    threads = max(1, int(hw.get("cpu", {}).get("cores_physical") or (os.cpu_count() or 4) // 2))
    threads_batch = max(threads, int(hw.get("cpu", {}).get("cores_logical") or os.cpu_count() or threads))
    cache_type = server.get("cache_type", "q8_0")
    context = 8192 if light else t.get("context", 8192)
    # Host-RAM prompt cache: lets the conversation survive side requests (e.g. vision)
    # without re-reading everything. A long conversation's state is ~1.3 GB on the 9B.
    cache_ram = int(min(4096, max(1024, total_ram_gb() * 1024 * 0.15)))
    # Reasoning/thinking: agent.json "reasoning" (off|auto|always) controls the server-level switch
    # (the only place that reliably turns thinking off on always-thinking models). When on, thoughts
    # go to message.reasoning_content (kept out of the visible reply) and are capped by reasoning_budget.
    reasoning = str(cfg.agent_config.get("reasoning", "auto")).lower()
    reason_budget = int(cfg.agent_config.get("reasoning_budget", 2048))
    reason_flags = (["-rea", "off"] if reasoning == "off" or light else
                    ["-rea", "auto", "--reasoning-format", "deepseek", "--reasoning-budget", str(reason_budget)])
    cmd = [str(exe), "-m", str(model_file(cfg, tier, mtp)),
           "--host", server.get("host", "127.0.0.1"), "--port", str(server.get("port", 8080)),
           "-c", str(context), "-np", "1", "-t", str(threads), "-tb", str(threads_batch),
           "--jinja", *reason_flags,
           "-ctk", cache_type, "-ctv", cache_type,
           "-cram", str(cache_ram), "-cms", "256", "--slot-save-path", str(slot_dir()), "--no-webui"]
    if mtp:
        # Multi-token prediction: the model drafts a few tokens per step and verifies them
        cmd += ["--spec-type", "draft-mtp", "--spec-draft-n-max", "3"]
    mmproj = AGENT_IA_USB_ROOT / "01_MODEL" / (t.get("mmproj_file") or "")
    if not light and server.get("load_vision", True) and cfg.agent_config.get("vision_enabled", True) \
            and t.get("mmproj_file") and mmproj.is_file():
        cmd += ["--mmproj", str(mmproj), "--image-max-tokens", "1024"]
    # GPU layers: llama.cpp's default (-ngl auto + --fit on) offloads as much as fits
    return cmd


def slot_dir():
    """Saved prompt caches are per PC (and per runtime/model through their file names)."""
    return SLOT_DIR / re.sub(r"[^\w-]", "_", os.environ.get("COMPUTERNAME", "pc"))


def stop_server(cfg=None):
    cfg = cfg or AgentIaUsbConfig(AGENT_IA_USB_ROOT)
    state = read_state()
    pid = state.get("pid")
    if pid:
        subprocess.run(["taskkill", "/F", "/PID", str(pid)], capture_output=True)
    subprocess.run(["taskkill", "/F", "/IM", "llama-server.exe"], capture_output=True)
    for _ in range(30):  # wait until the port is really free
        if not is_running(cfg):
            break
        time.sleep(0.5)
    if STATE_FILE.exists():
        state.update({"stopped_at": datetime.now().isoformat(timespec="seconds"), "pid": None})
        STATE_FILE.write_text(json.dumps(state, indent=2), encoding="utf-8")
    say("Model server stopped.")


def start_server(cfg, tier, runtime, hw, warmup=True, reason="", mtp=False, light=False):
    t = cfg.tier_config(tier)
    cmd = build_command(cfg, tier, runtime, hw, mtp=mtp, light=light)
    if not Path(cmd[0]).exists():
        say(f"ERROR: {cmd[0]} not found. Run SETUP-USB.bat first.")
        return False
    slot_dir().mkdir(parents=True, exist_ok=True)
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    if is_running(cfg):
        stop_server(cfg)
        if is_running(cfg):
            say("ERROR: another program is using the model server port and could not be stopped.")
            return False
    expected_model = Path(cmd[2]).name.lower()
    if not light:
        say(f"Starting {t.get('label', tier)}{' + MTP' if mtp else ''} ({tier} tier, {runtime.upper()}"
            f"{': ' + reason if reason else ''}, context {t.get('context')})...")
    log = open(LOG_FILE, "w", encoding="utf-8", errors="replace")
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    try:
        proc = subprocess.Popen(cmd, stdout=log, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                                creationflags=flags, close_fds=True)
    except (PermissionError, OSError) as e:
        # Windows bloqueó el lanzamiento del .exe (antivirus/SmartScreen suelen
        # hacerlo con un ejecutable sin firma en una unidad extraíble -> WinError 5).
        log.close()
        say(f"ERROR: Windows no dejó ejecutar el motor del modelo ({cmd[0]}): {e}")
        say("Suele ser el antivirus/SmartScreen bloqueando un .exe sin firma en el USB. Prueba una de estas:")
        say("  1) Desbloquéalo: en PowerShell -> Unblock-File \"" + cmd[0] + "\"")
        say("  2) Añade la carpeta de Agent IA USB como exclusión en tu antivirus.")
        say("  3) Ejecuta START-AGENT-IA-USB.bat como administrador (clic derecho -> Ejecutar como administrador).")
        say("  4) Copia la carpeta Agent IA USB a una carpeta del disco C: y arráncala desde ahí.")
        return False
    start = time.time()
    last_note = 0
    while True:
        if proc.poll() is not None:
            log.close()
            tail = LOG_FILE.read_text(encoding="utf-8", errors="replace").splitlines()[-12:]
            say(f"ERROR: the model server stopped (exit code {proc.returncode}). Last log lines:")
            for line in tail:
                print("    " + line)
            return False
        if is_running(cfg) and _serves(cfg, expected_model):
            break
        waited = time.time() - start
        if waited > 900:
            say("ERROR: the model server took more than 15 minutes to load.")
            proc.kill()
            return False
        if waited - last_note >= 15:
            say(f"Loading the model into memory... {int(waited)}s")
            last_note = waited
        time.sleep(1)
    say(f"Model loaded in {int(time.time() - start)}s.")
    if light:
        STATE_FILE.write_text(json.dumps({"pid": proc.pid, "light": True}), encoding="utf-8")
        return True
    state = {"tier": tier, "label": t.get("label", tier) + (" + MTP" if mtp else ""),
             "model_file": Path(cmd[2]).name, "mtp": mtp,
             "context": t.get("context", 8192), "runtime": runtime, "runtime_reason": reason, "pid": proc.pid,
             "server_url": server_url(cfg), "started_at": datetime.now().isoformat(timespec="seconds")}
    STATE_FILE.write_text(json.dumps(state, indent=2), encoding="utf-8")
    if warmup:
        warm_up(AgentIaUsbConfig(AGENT_IA_USB_ROOT))
    detect_capabilities(cfg)
    return True


def detect_capabilities(cfg):
    """Does the running model support tool calling? Read it from its chat template so
    Agent IA USB can warn and fall back to chat-only instead of silently failing (the Gemma lesson)."""
    try:
        props = _get(f"{server_url(cfg)}/props", timeout=5)
        tmpl = str(props.get("chat_template") or "").lower()
        tools_ok = ("tool" in tmpl) or ("function" in tmpl)
        state = read_state()
        state["tools_ok"] = tools_ok
        STATE_FILE.write_text(json.dumps(state, indent=2), encoding="utf-8")
        if not tools_ok:
            say("WARNING: this model has no tool-calling in its chat template; Agent IA USB will work in chat-only mode.")
        return tools_ok
    except Exception:
        return None


# ------------------------------------------------------------------- warm-up
def _prefix_messages(cfg):
    """The exact start of every conversation: system prompt + tools."""
    from core.prompts import build_system_prompt
    from core.tools import ToolRegistry
    system = build_system_prompt(cfg, cfg.tier_config().get("label", ""))
    return system, ToolRegistry().schemas()


def _shared_prefix_tokens(url, system, tools):
    """Token ids every conversation starts with: system prompt + tools, up to the
    '<|im_start|>' that opens the first user turn. Qwen3.5 is a hybrid model whose
    recurrent state can't be rolled back, so the cache must end exactly here for
    any later request to continue from it."""
    sentinel = "AGENT_IA_USB_PREFIX_SENTINEL"
    rendered = _post(f"{url}/apply-template", {
        "messages": [{"role": "system", "content": system}, {"role": "user", "content": sentinel}],
        "tools": tools, "chat_template_kwargs": {"enable_thinking": False}})["prompt"]
    cut = rendered.index(sentinel)
    marker = "<|im_start|>"
    end = rendered.rfind(marker, 0, cut)
    prefix = rendered[:end + len(marker)] if end >= 0 else rendered[:cut]
    return _post(f"{url}/tokenize", {"content": prefix, "add_special": False, "parse_special": True})["tokens"]


def warm_up(cfg):
    """Pre-process the system prompt + tool list so the first request is fast.
    The processed state is saved to disk and restored on later starts."""
    system, tools = _prefix_messages(cfg)
    st = read_state()
    ident = f"prefix-v3|{st.get('model_file')}|{st.get('runtime')}|{st.get('context')}"
    key = hashlib.md5((ident + system + json.dumps(tools)).encode()).hexdigest()[:16]
    slot_file = f"agent_ia_usb_{key}.bin"
    url = server_url(cfg)
    folder = slot_dir()
    if st.get("model_file") and not _serves(cfg, str(st["model_file"]).lower()):
        say("Warm-up skipped: the server on the port is not the expected model.")
        return
    if (folder / slot_file).exists():
        try:
            t0 = time.time()
            _post(f"{url}/slots/0?action=restore", {"filename": slot_file}, timeout=300)
            say(f"Restored Agent IA USB's working memory in {time.time() - t0:.1f}s.")
            return
        except Exception as e:
            say(f"Could not restore the saved prompt cache ({e}); rebuilding it.")
    say("Preparing Agent IA USB's instructions and tools (one-time step per PC, a few minutes)...")
    t0 = time.time()
    try:
        tokens = _shared_prefix_tokens(url, system, tools)
        _post(f"{url}/completion", {"prompt": tokens, "n_predict": 0, "cache_prompt": True}, timeout=1800)
        say(f"Ready ({time.time() - t0:.0f}s, {len(tokens)} tokens cached).")
        stale = sorted(folder.glob("agent_ia_usb_*.bin"), key=lambda f: f.stat().st_mtime)[:-3]  # keep a few recent ones
        for old in stale:
            old.unlink()
        _post(f"{url}/slots/0?action=save", {"filename": slot_file}, timeout=300)  # saved in slot_dir()
    except Exception as e:
        say(f"Warm-up skipped ({e}). The first request will be slower.")


# ------------------------------------------------------------------ autotune
def profile_file():
    name = re.sub(r"[^\w-]", "_", os.environ.get("COMPUTERNAME", "pc"))
    return AGENT_IA_USB_ROOT / "06_CONFIG" / "hardware" / f"profile_{name}.json"


def load_profile():
    try:
        return json.loads(profile_file().read_text(encoding="utf-8"))
    except Exception:
        return {}


def save_profile(profile):
    profile_file().parent.mkdir(parents=True, exist_ok=True)
    profile_file().write_text(json.dumps(profile, indent=2), encoding="utf-8")


BENCH_TASK = ("Escribe en Python una función que reciba una lista de diccionarios con las claves 'nombre' y 'edad', "
              "devuelva los mayores de 18 ordenados por edad, y explica cada línea con un comentario corto. "
              "Incluye además un ejemplo de uso con cinco personas.")


def bench_combo(cfg, tier, runtime, mtp, hw):
    """Time one realistic agent step: system prompt cached, then a new request
    (prompt processing) and ~128 generated tokens. Returns a result dict."""
    stop_server(cfg)
    time.sleep(1)
    t0 = time.time()
    if not start_server(cfg, tier, runtime, hw, warmup=False, mtp=mtp, light=True):
        return {"ok": False}
    load_s = time.time() - t0
    url = server_url(cfg)
    system, _ = _prefix_messages(cfg)
    base = [{"role": "system", "content": system[:6000]}]
    try:
        _post(f"{url}/v1/chat/completions", {"messages": base + [{"role": "user", "content": "Hola"}],
                                              "max_tokens": 1, "cache_prompt": True,
                                              "chat_template_kwargs": {"enable_thinking": False}}, timeout=900)
        t1 = time.time()
        r = _post(f"{url}/v1/chat/completions", {
            "messages": base + [{"role": "user", "content": "Hola"}, {"role": "assistant", "content": "Hola. ¿Qué necesitas?"},
                                {"role": "user", "content": BENCH_TASK}],
            "max_tokens": 200, "temperature": 0.6, "top_k": 20, "top_p": 0.8, "seed": 7, "cache_prompt": True,
            "chat_template_kwargs": {"enable_thinking": False}}, timeout=900)
        tm = r.get("timings", {})
        gen_tps = tm.get("predicted_per_second", 0) or 0.01
        prompt_tps = tm.get("prompt_per_second", 0) or 0.01
        # Score a *typical* agent step from the measured speeds (reply lengths vary run to run):
        # ~150 new prompt tokens + ~120 generated tokens
        step_s = 150 / prompt_tps + 120 / gen_tps
        return {"ok": True, "step_s": round(step_s, 1), "load_s": round(load_s),
                "gen_tps": round(gen_tps, 2), "prompt_tps": round(prompt_tps, 1),
                "measured_s": round(time.time() - t1, 1),
                "draft_accept": tm.get("draft_n_accepted"), "draft_n": tm.get("draft_n")}
    except Exception as e:
        return {"ok": False, "error": str(e)[:200]}
    finally:
        stop_server(cfg)


def autotune(cfg, tier, hw):
    """Measure which runtime / model variant is really fastest on THIS PC."""
    label = cfg.tier_config(tier).get("label", tier)
    say(f"First start of {label} on this PC: measuring the fastest setup (one time, a few minutes)...")
    runtime, reason = choose_runtime(cfg, cfg.tier_config(tier))
    runtimes = ["cpu"] + (["vulkan"] if runtime == "vulkan" else [])
    results = {}
    for rt in runtimes:
        say(f"  testing {rt.upper()}...")
        results[f"{rt}"] = bench_combo(cfg, tier, rt, False, hw)
    ok = {k: v for k, v in results.items() if v.get("ok")}
    best_rt = min(ok, key=lambda k: ok[k]["step_s"]) if ok else "cpu"
    if has_mtp(cfg, tier):
        say(f"  testing {best_rt.upper()} + MTP...")
        results[f"{best_rt}+mtp"] = bench_combo(cfg, tier, best_rt, True, hw)
    ok = {k: v for k, v in results.items() if v.get("ok")}
    if not ok:
        say("  could not measure (all tests failed) - using the plain CPU setup this time.")
        return None
    best = min(ok, key=lambda k: ok[k]["step_s"])
    for k, v in results.items():
        say(f"    {k:12s} " + (f"typical step ~{v['step_s']}s, generation {v['gen_tps']} tok/s, prompt {v['prompt_tps']} tok/s"
                               if v.get("ok") else f"failed {v.get('error', '')}"))
    choice = {"runtime": best.split("+")[0], "mtp": best.endswith("+mtp"),
              "gen_tps": ok.get(best, {}).get("gen_tps", 0), "results": results,
              "tuned_at": datetime.now().isoformat(timespec="seconds"), "reason": reason}
    say(f"  -> using {best.upper()} for {label}.")
    return choice


def _smaller_model(cfg, model_id):
    """Id of the next-smaller discovered model (for the too-slow fallback), or None."""
    cur = cfg.model_registry.get(model_id)
    if not cur:
        return None
    smaller = [m for m in cfg.list_models() if m.id != cur.id and (m.size_bytes or 0) < (cur.size_bytes or 0)]
    return max(smaller, key=lambda m: m.size_bytes or 0).id if smaller else None


def ensure_server(tier=None, restart=False, warmup=True, tune=False):
    """Make sure the right model is being served. Returns True when ready."""
    cfg = AgentIaUsbConfig(AGENT_IA_USB_ROOT)
    hw = hardware()
    profile = load_profile()
    forced_cfg = cfg.agent_config.get("model") or cfg.agent_config.get("model_tier", "auto")
    auto_tier = tier is None and (not forced_cfg or forced_cfg == "auto")
    tier = choose_tier(cfg, tier)
    if not tier:
        say("ERROR: no model found in 01_MODEL. Run SETUP-USB.bat first.")
        return False
    # Migrate profiles saved by older versions, keyed by alias ("smart"/"fast") instead of model id
    if tier not in profile:
        for alias, mid in cfg._aliases().items():
            if mid == tier and alias in profile:
                profile[tier] = profile.pop(alias)
                if isinstance(profile.get("prefer"), dict):
                    profile["prefer"] = {cfg.resolve_model_id(k) or k: v for k, v in profile["prefer"].items()}
                save_profile(profile)
                break
    state = read_state()
    if is_running(cfg) and not restart and not tune:
        if state.get("tier") == tier and state.get("pid") and not state.get("light"):
            say(f"{state.get('label', tier)} is already running.")
            return True
        say("A different model is running - restarting.")
    if is_running(cfg):
        stop_server(cfg)
        time.sleep(2)

    forced = cfg.agent_config.get("runtime", "auto")
    if forced == "auto" and tune:
        result = autotune(cfg, tier, hw)
        if result:
            profile[tier] = result
            # A very slow big model is worse than a quick smaller one
            if auto_tier and 0 < result.get("gen_tps", 0) < 2.0:
                smaller = _smaller_model(cfg, tier)
                if smaller:
                    profile.setdefault("prefer", {})[tier] = smaller
                    say(f"  {cfg.tier_config(tier).get('label', tier)} is too slow on this PC (< 2 tok/s): "
                        f"using {cfg.tier_config(smaller).get('label', smaller)} instead.")
            save_profile(profile)
    if auto_tier:
        pref = (profile.get("prefer") or {}).get(tier)
        if pref and cfg.model_registry.get(pref):
            tier = pref
            if tier not in profile and forced == "auto":
                result = autotune(cfg, tier, hw)
                if result:
                    profile[tier] = result
                    save_profile(profile)

    if forced == "auto" and tier in profile:
        runtime, mtp = profile[tier]["runtime"], bool(profile[tier].get("mtp")) and has_mtp(cfg, tier)
        reason = f"measured fastest on this PC ({profile[tier].get('gen_tps', '?')} tok/s)"
    else:
        runtime, reason = choose_runtime(cfg, cfg.tier_config(tier))
        # Sin perfil medido para este PC: no hacemos autotune (son 2-3 cargas del
        # modelo desde el USB). Usamos MTP por defecto cuando existe su archivo, que
        # es lo que el autotune terminaba eligiendo en CPU; se desactiva con "mtp": false.
        mtp = bool(cfg.agent_config.get("mtp", has_mtp(cfg, tier))) and has_mtp(cfg, tier)
    pending = cfg.model_registry.pending_conversions()
    if pending:
        say(f"Found {len(pending)} model(s) in 01_MODEL/ that need conversion to GGUF:")
        for p in pending:
            sz = p['size_bytes'] / 1024**3
            note = "run CONVERT-MODEL.bat" if p['convertible'] else "NOT convertible (ONNX)"
            say(f"  {Path(p['dir']).name}/  ({p['format']}, {sz:.1f} GB) — {note}")
    if start_server(cfg, tier, runtime, hw, warmup=warmup, reason=reason, mtp=mtp):
        return True
    if mtp or runtime == "vulkan":  # MTP or GPU trouble: fall back to the plain CPU setup
        say("That setup failed to start - retrying with the plain CPU setup.")
        stop_server(cfg)
        time.sleep(2)
        if tier in profile:
            profile[tier].update({"runtime": "cpu", "mtp": False})
            save_profile(profile)
        return start_server(cfg, tier, "cpu", hw, warmup=warmup, reason="fallback")
    return False


def main():
    ap = argparse.ArgumentParser(description="Agent IA USB model server launcher")
    ap.add_argument("--start", action="store_true")
    ap.add_argument("--stop", action="store_true")
    ap.add_argument("--status", action="store_true")
    ap.add_argument("--restart", action="store_true")
    ap.add_argument("--tier", default=None, help="auto, smart or fast")
    ap.add_argument("--no-warmup", action="store_true")
    ap.add_argument("--tune", action="store_true", help="measure the fastest setup again for this PC")
    args = ap.parse_args()
    cfg = AgentIaUsbConfig(AGENT_IA_USB_ROOT)
    if args.stop:
        stop_server(cfg)
        return 0
    if args.status:
        state = read_state()
        print(json.dumps({"running": is_running(cfg), **state}, indent=2))
        return 0
    tier = None if args.tier in (None, "auto") else args.tier
    return 0 if ensure_server(tier, restart=args.restart, warmup=not args.no_warmup, tune=args.tune) else 1


if __name__ == "__main__":
    sys.exit(main())
