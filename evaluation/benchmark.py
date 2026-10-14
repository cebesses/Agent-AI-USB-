#!/usr/bin/env python3
"""
Agent IA USB Benchmark - ~1000 synthetic conversations to measure and improve the agent.

Five dimensions are scored, each 0-10, plus a global Agent IA USB Agent Score:
  conversation   instruction following, format, language, brevity
  reasoning      arithmetic, comparison, parity, counting, sequences
  agent          picking and sequencing tools (graded on the files on disk)
  coding         creating runnable Python
  memory         remembering and recalling across a reset

Everything is graded programmatically (no LLM judge needed), so a run is
objective and repeatable.

  python evaluation\\benchmark.py generate            build evaluation/datasets/scenarios.jsonl (~1000)
  python evaluation\\benchmark.py run                 run a stratified sample (default 40) on the active model
  python evaluation\\benchmark.py run --sample 100    bigger sample
  python evaluation\\benchmark.py run --all-scenarios run every scenario (an overnight job on CPU)
  python evaluation\\benchmark.py run --models all    run the sample on every installed model and compare
  python evaluation\\benchmark.py report              reprint the comparison table from saved results

Needs the model server (START-AGENT-IA-USB.bat). On a 3-5 tok/s CPU each scenario takes
~5-90 s, so the full 1000 is hours; the default sample gives a quick read.
"""
import argparse
import json
import os
import random
import re
import shutil
import subprocess
import sys
import tempfile
import time
import unicodedata
from collections import defaultdict
from datetime import datetime
from pathlib import Path

AGENT_IA_USB_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(AGENT_IA_USB_ROOT / "03_AGENT"))
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

from core.config import AgentIaUsbConfig       # noqa: E402
from core.llm_client import LLMClient    # noqa: E402
from eval_agent import Harness, make, files  # noqa: E402
from orchestrator.agent import wrong_language  # noqa: E402

DATASET = AGENT_IA_USB_ROOT / "evaluation" / "datasets" / "scenarios.jsonl"
RESULTS_DIR = AGENT_IA_USB_ROOT / "evaluation" / "results"
REPORTS_DIR = AGENT_IA_USB_ROOT / "evaluation" / "reports"
DIMENSIONS = ["conversation", "reasoning", "agent", "coding", "memory"]


def norm(t):
    t = unicodedata.normalize("NFD", (t or "").lower())
    return "".join(c for c in t if unicodedata.category(c) != "Mn")


def nums(t):
    return re.findall(r"-?\d+", t or "")


# ---------------------------------------------------------------- families
# Each family: gen(rng)->params, prompt(params)->str, grade(ctx)->0..1,
# optional setup(box, params) and interact(h, box, params)->(reply, turn).
FAMILIES = {}


def family(name, dim, count):
    def deco(cls):
        FAMILIES[name] = {"dim": dim, "count": count, "cls": cls()}
        return cls
    return deco


class Ctx:
    def __init__(self, h, box, params, reply, turn):
        self.h, self.box, self.params, self.reply, self.turn = h, box, params, reply, turn


# ---- conversation --------------------------------------------------------
_COLORS = {"el cielo despejado": "azul", "el pasto": "verde", "la nieve": "blanco",
           "el carbon": "negro", "el sol del mediodia": "amarillo", "la sangre": "rojo",
           "una naranja madura": "naranja", "el chocolate": "marron"}


@family("single_word", "conversation", 60)
class F:
    def gen(s, rng):
        k = rng.choice(list(_COLORS))
        return {"thing": k, "color": _COLORS[k]}
    def prompt(s, p):
        return f"Responde con UNA sola palabra, sin explicaciones: ¿de qué color es {p['thing']}?"
    def grade(s, c):
        r = norm(c.reply)
        return 1.0 if c.params["color"] in r and len(r.split()) <= 3 else 0.0


@family("list_n", "conversation", 50)
class F:
    def gen(s, rng):
        return {"n": rng.randint(3, 5), "cat": rng.choice(["frutas", "colores", "países", "animales", "ríos"])}
    def prompt(s, p):
        return f"Escribe exactamente {p['n']} {p['cat']} separados por comas. No escribas nada más."
    def grade(s, c):
        items = [x for x in re.split(r"[,\n;]", c.reply) if x.strip() and any(ch.isalpha() for ch in x)]
        n = c.params["n"]
        return 1.0 if len(items) == n else (0.5 if abs(len(items) - n) <= 1 else 0.0)


@family("concise", "conversation", 50)
class F:
    def gen(s, rng):
        return {"topic": rng.choice(["una computadora", "el agua", "una estrella", "un volcán", "el internet", "una abeja"])}
    def prompt(s, p):
        return f"En una sola frase breve, explica qué es {p['topic']}."
    def grade(s, c):
        sents = [x for x in re.split(r"[.!?]+", c.reply) if x.strip()]
        return 1.0 if len(c.reply) > 12 and len(sents) <= 2 else (0.5 if len(sents) <= 3 else 0.0)


@family("language_es", "conversation", 50)
class F:
    def gen(s, rng):
        return {"q": rng.choice(["Say hello and briefly offer your help.",
                                 "In one sentence, tell me what you can do.",
                                 "Greet me and ask what I need."])}
    def prompt(s, p):
        return p["q"]
    def grade(s, c):
        return 1.0 if len(c.reply) > 3 and not wrong_language(c.reply, "es") else 0.0


@family("echo_code", "conversation", 40)
class F:
    def gen(s, rng):
        return {"code": "".join(rng.choice("ABCDEFGHJKLMNPQRSTUVWXYZ23456789") for _ in range(6))}
    def prompt(s, p):
        return f"Repite exactamente este código, sin nada más: {p['code']}"
    def grade(s, c):
        return 1.0 if c.params["code"] in c.reply.upper() else 0.0


# ---- reasoning -----------------------------------------------------------
@family("arith", "reasoning", 80)
class F:
    def gen(s, rng):
        a, b = rng.randint(2, 99), rng.randint(2, 50)
        op = rng.choice(["+", "-", "*"])
        ans = {"+": a + b, "-": a - b, "*": a * b}[op]
        return {"a": a, "b": b, "op": op, "ans": ans}
    def prompt(s, p):
        return f"¿Cuánto es {p['a']} {p['op']} {p['b']}? Responde solo con el número."
    def grade(s, c):
        return 1.0 if str(c.params["ans"]) in nums(c.reply) else 0.0


@family("compare", "reasoning", 50)
class F:
    def gen(s, rng):
        a = rng.randint(1, 999)
        b = rng.choice([x for x in [rng.randint(1, 999)] if x != a] or [a + 1])
        return {"a": a, "b": b, "big": max(a, b), "small": min(a, b)}
    def prompt(s, p):
        return f"¿Qué número es mayor, {p['a']} o {p['b']}? Responde solo con ese número."
    def grade(s, c):
        ns = nums(c.reply)
        if ns and ns[0] == str(c.params["big"]):
            return 1.0
        return 0.5 if str(c.params["big"]) in ns else 0.0


@family("parity", "reasoning", 40)
class F:
    def gen(s, rng):
        n = rng.randint(1, 9999)
        return {"n": n, "ans": "par" if n % 2 == 0 else "impar"}
    def prompt(s, p):
        return f"¿El número {p['n']} es par o impar? Responde con una sola palabra."
    def grade(s, c):
        r = norm(c.reply)
        if c.params["ans"] == "impar":
            return 1.0 if "impar" in r else 0.0
        return 1.0 if "par" in r and "impar" not in r else 0.0


@family("wordcount", "reasoning", 40)
class F:
    def gen(s, rng):
        words = ["gato", "perro", "casa", "sol", "mar", "luz", "flor", "pan", "rio", "pez", "mesa", "nube"]
        k = rng.randint(3, 7)
        return {"k": k, "sentence": " ".join(rng.choice(words) for _ in range(k))}
    def prompt(s, p):
        return f"¿Cuántas palabras tiene esta frase? '{p['sentence']}' Responde solo con el número."
    def grade(s, c):
        return 1.0 if str(c.params["k"]) in nums(c.reply) else 0.0


@family("sequence", "reasoning", 40)
class F:
    def gen(s, rng):
        start, step = rng.randint(1, 20), rng.randint(2, 9)
        terms = [start + i * step for i in range(4)]
        return {"terms": terms, "next": start + 4 * step}
    def prompt(s, p):
        t = ", ".join(str(x) for x in p["terms"])
        return f"Continúa la secuencia: {t}, ... ¿qué número sigue? Responde solo con el número."
    def grade(s, c):
        return 1.0 if str(c.params["next"]) in nums(c.reply) else 0.0


# ---- agent (graded on disk) ---------------------------------------------
_EXTS = ["txt", "jpg", "pdf", "log", "csv"]


@family("move", "agent", 60)
class F:
    def gen(s, rng):
        return {"ext": rng.choice(_EXTS), "n": rng.randint(8, 40), "decoy": rng.choice([e for e in _EXTS])}
    def setup(s, box, p):
        make(box / "A", [f"f{i}.{p['ext']}" for i in range(p["n"])] + [f"d{i}.{p['decoy']}" for i in range(3)])
    def prompt(s, p):
        return f"Mueve todos los archivos .{p['ext']} de {{box}}\\A a {{box}}\\B."
    def grade(s, c):
        return 1.0 if len(files(c.box / "B", f"*.{c.params['ext']}")) == c.params["n"] \
            and not files(c.box / "A", f"*.{c.params['ext']}") else 0.0


@family("copy", "agent", 50)
class F:
    def gen(s, rng):
        return {"ext": rng.choice(_EXTS), "n": rng.randint(6, 30)}
    def setup(s, box, p):
        make(box / "A", [f"f{i}.{p['ext']}" for i in range(p["n"])] + ["other.md"])
    def prompt(s, p):
        return f"Copia todos los archivos .{p['ext']} de {{box}}\\A a {{box}}\\B."
    def grade(s, c):
        return 1.0 if len(files(c.box / "B", f"*.{c.params['ext']}")) == c.params["n"] \
            and len(files(c.box / "A", f"*.{c.params['ext']}")) == c.params["n"] else 0.0


@family("delete_tmp", "agent", 40)
class F:
    def gen(s, rng):
        return {"n": rng.randint(4, 15), "keep": rng.randint(2, 4)}
    def setup(s, box, p):
        make(box / "F", [f"c{i}.tmp" for i in range(p["n"])] + [f"keep{i}.txt" for i in range(p["keep"])])
    def prompt(s, p):
        return f"Borra los archivos .tmp de {{box}}\\F."
    def grade(s, c):
        f = c.box / "F"
        ok = not files(f, "*.tmp") and len(files(f, "*.txt")) == c.params["keep"] and "delete_files" in c.h.confirmations
        if not files(f, "*.tmp"):
            c.h.journal.undo_last()
        return 1.0 if ok else 0.0


@family("count", "agent", 50)
class F:
    def gen(s, rng):
        return {"ext": rng.choice(_EXTS), "n": rng.randint(3, 19), "decoy": rng.randint(2, 6)}
    def setup(s, box, p):
        make(box / "J", [f"f{i}.{p['ext']}" for i in range(p["n"])] + [f"d{i}.md" for i in range(p["decoy"])])
    def prompt(s, p):
        return f"¿Cuántos archivos .{p['ext']} hay en {{box}}\\J?"
    def grade(s, c):
        return 1.0 if str(c.params["n"]) in nums(c.reply) else 0.0


@family("rename", "agent", 25)
class F:
    def gen(s, rng):
        return {"old": f"viejo_{rng.randint(1, 99)}.txt", "new": f"nuevo_{rng.randint(1, 99)}.txt"}
    def setup(s, box, p):
        make(box, [p["old"]])
    def prompt(s, p):
        return f"Renombra el archivo {{box}}\\{p['old']} a {p['new']}."
    def grade(s, c):
        return 1.0 if (c.box / c.params["new"]).exists() and not (c.box / c.params["old"]).exists() else 0.0


@family("mkdir_write", "agent", 25)
class F:
    def gen(s, rng):
        return {"sub": rng.choice(["notas", "trabajo", "ideas"]), "txt": f"agent-ia-usb-{rng.randint(100, 999)}"}
    def prompt(s, p):
        return f"Crea una carpeta {{box}}\\{p['sub']} y dentro un archivo notas.txt con el texto {p['txt']}."
    def grade(s, c):
        f = c.box / c.params["sub"] / "notas.txt"
        return 1.0 if f.exists() and c.params["txt"] in f.read_text(encoding="utf-8", errors="replace") else 0.0


# ---- coding --------------------------------------------------------------
@family("py_print", "coding", 70)
class F:
    def gen(s, rng):
        return {"word": "Agent IA USB" + str(rng.randint(1000, 9999))}
    def prompt(s, p):
        return f"Crea un archivo {{box}}\\p.py que, al ejecutarse, imprima exactamente: {p['word']}"
    def grade(s, c):
        f = c.box / "p.py"
        if not f.exists():
            return 0.0
        try:
            r = subprocess.run([sys.executable, str(f)], capture_output=True, text=True, timeout=20)
            if c.params["word"] in (r.stdout or ""):
                return 1.0
        except Exception:
            pass
        try:
            compile(f.read_text(encoding="utf-8", errors="replace"), "p.py", "exec")
            return 0.5
        except Exception:
            return 0.0


@family("py_func", "coding", 55)
class F:
    def gen(s, rng):
        t = rng.choice([("suma", "sume dos números y devuelva el resultado"),
                        ("cuadrado", "devuelva el cuadrado de un número"),
                        ("es_par", "devuelva True si un número es par"),
                        ("invierte", "devuelva una cadena invertida")])
        return {"fn": t[0], "desc": t[1]}
    def prompt(s, p):
        return (f"Crea un archivo {{box}}\\m.py con una función llamada {p['fn']} que {p['desc']}. "
                f"Incluye al final un ejemplo que la llame con print.")
    def grade(s, c):
        f = c.box / "m.py"
        if not f.exists():
            return 0.0
        src = f.read_text(encoding="utf-8", errors="replace")
        try:
            compile(src, "m.py", "exec")
        except Exception:
            return 0.0
        return 1.0 if f"def {c.params['fn']}" in src else 0.5


# ---- memory (two turns) --------------------------------------------------
@family("remember_recall", "memory", 65)
class F:
    def gen(s, rng):
        pairs = [("color favorito", rng.choice(["turquesa", "magenta", "ocre", "lila"])),
                 ("ciudad natal", rng.choice(["Pasto", "Cuenca", "Tarija", "Mérida"])),
                 ("número de la suerte", str(rng.randint(10, 99)))]
        k, v = rng.choice(pairs)
        return {"k": k, "v": v}
    def prompt(s, p):
        return f"Recuerda que mi {p['k']} es {p['v']}, y luego te preguntaré cuál es."
    def interact(s, h, box, p):
        h.ask(f"Recuerda que mi {p['k']} es {p['v']}.")
        h.agent.reset()
        return h.ask(f"¿Cuál es mi {p['k']}?")
    def grade(s, c):
        return 1.0 if norm(c.params["v"]) in norm(c.reply) else 0.0


@family("preference", "memory", 60)
class F:
    def gen(s, rng):
        return {"path": rng.choice([r"D:\Respaldo", r"E:\Trabajo\2026", r"D:\Proyectos\agent-ia-usb"])}
    def prompt(s, p):
        return f"Recuerda que mi carpeta de trabajo es {p['path']}, y luego te preguntaré dónde está."
    def interact(s, h, box, p):
        h.ask(f"Recuerda que mi carpeta de trabajo es {p['path']}.")
        h.agent.reset()
        return h.ask("¿Dónde tengo mi carpeta de trabajo?")
    def grade(s, c):
        want = norm(c.params["path"]).replace("\\\\", "\\")
        return 1.0 if want in norm(c.reply).replace("\\\\", "\\") else 0.0


# -------------------------------------------------------------- generation
def generate(seed=7):
    rng = random.Random(seed)
    DATASET.parent.mkdir(parents=True, exist_ok=True)
    out = []
    for name, fam in FAMILIES.items():
        for i in range(fam["count"]):
            params = fam["cls"].gen(rng)
            out.append({"id": f"{name}-{i:04d}", "family": name, "dim": fam["dim"],
                        "prompt": fam["cls"].prompt(params), "params": params})
    rng.shuffle(out)
    with open(DATASET, "w", encoding="utf-8") as f:
        for s in out:
            f.write(json.dumps(s, ensure_ascii=False) + "\n")
    by_dim = defaultdict(int)
    for s in out:
        by_dim[s["dim"]] += 1
    print(f"Wrote {len(out)} scenarios to {DATASET.relative_to(AGENT_IA_USB_ROOT)}")
    for d in DIMENSIONS:
        print(f"  {d:14s} {by_dim[d]}")
    return out


def load_scenarios():
    if not DATASET.exists():
        generate()
    return [json.loads(l) for l in DATASET.read_text(encoding="utf-8").splitlines() if l.strip()]


def stratified_sample(scenarios, n, seed=1):
    rng = random.Random(seed)
    by_dim = defaultdict(list)
    for s in scenarios:
        by_dim[s["dim"]].append(s)
    per = max(1, n // len(DIMENSIONS))
    picked = []
    for d in DIMENSIONS:
        pool = by_dim[d]
        rng.shuffle(pool)
        picked += pool[:per]
    rng.shuffle(picked)
    return picked[:n]


# ----------------------------------------------------------------- running
def run_scenarios(config, scenarios, label, verbose=False):
    scores = defaultdict(list)       # dim -> [0..1]
    steps = defaultdict(list)        # dim -> [tool-step count per conversation]  (efficiency)
    latency = defaultdict(list)      # dim -> [seconds per conversation]          (efficiency)
    t_start = time.time()
    for i, sc in enumerate(scenarios, 1):
        fam = FAMILIES.get(sc["family"])
        if not fam:
            continue
        cls, params = fam["cls"], sc["params"]
        box = Path(tempfile.mkdtemp(prefix="agent_ia_usb_bench_"))
        h, sc_t0 = None, time.time()
        try:
            h = Harness(config, box, verbose=False)
            if hasattr(cls, "setup"):
                cls.setup(box, params)
            prompt = sc["prompt"].replace("{box}", str(box))
            if hasattr(cls, "interact"):
                reply, turn = cls.interact(h, box, params)
            else:
                reply, turn = h.ask(prompt)
            score = 0.0 if h.errors else float(cls.grade(Ctx(h, box, params, reply, turn)))
        except Exception as e:
            reply, score = f"crashed: {type(e).__name__}: {e}", 0.0
        finally:
            sc_secs = time.time() - sc_t0
            sc_steps = getattr(h, "steps", 0) if h is not None else 0
            shutil.rmtree(box, ignore_errors=True)
        scores[sc["dim"]].append(score)
        steps[sc["dim"]].append(sc_steps)
        latency[sc["dim"]].append(sc_secs)
        if verbose or i % 10 == 0 or i == len(scenarios):
            done = sum(len(v) for v in scores.values())
            avg = sum(sum(v) for v in scores.values()) / max(done, 1)
            print(f"  [{i}/{len(scenarios)}] {sc['dim']:12s} {sc['family']:14s} "
                  f"score={score:.1f} steps={sc_steps} {sc_secs:.0f}s  running avg={avg * 10:.1f}/10")
    elapsed = time.time() - t_start
    dim_scores = {d: (sum(scores[d]) / len(scores[d]) * 10 if scores[d] else None) for d in DIMENSIONS}
    steps_by_dim = {d: (round(sum(steps[d]) / len(steps[d]), 2) if steps[d] else None) for d in DIMENSIONS}
    lat_by_dim = {d: (round(sum(latency[d]) / len(latency[d]), 1) if latency[d] else None) for d in DIMENSIONS}
    graded = [s for v in scores.values() for s in v]
    all_steps = [s for v in steps.values() for s in v]
    all_lat = [s for v in latency.values() for s in v]
    overall = sum(graded) / len(graded) * 10 if graded else 0.0
    prof = config.runtime_state
    result = {"label": label, "time": datetime.now().isoformat(timespec="seconds"),
              "model_file": prof.get("model_file"), "runtime": prof.get("runtime"), "mtp": prof.get("mtp"),
              "n": len(graded), "seconds": round(elapsed), "overall": round(overall, 2),
              "dimensions": {d: (round(v, 2) if v is not None else None) for d, v in dim_scores.items()},
              "steps_avg": round(sum(all_steps) / len(all_steps), 2) if all_steps else None,
              "latency_avg": round(sum(all_lat) / len(all_lat), 1) if all_lat else None,
              "steps_by_dim": steps_by_dim, "latency_by_dim": lat_by_dim,
              "gen_tps": _profile_tps(config)}
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    tag = re.sub(r"[^\w.-]", "_", label)
    (RESULTS_DIR / f"{tag}_{datetime.now():%Y%m%d-%H%M%S}.json").write_text(
        json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
    return result


def _profile_tps(config):
    try:
        prof = json.loads((AGENT_IA_USB_ROOT / "06_CONFIG" / "hardware" /
                           f"profile_{os.environ.get('COMPUTERNAME', 'pc')}.json").read_text(encoding="utf-8"))
        tier = config.runtime_state.get("tier")
        return prof.get(tier, {}).get("gen_tps")
    except Exception:
        return None


def print_result(r):
    print(f"\n{'=' * 64}\n  Agent IA USB Agent Score: {r['overall']:.2f}/10   (model: {r['label']}, n={r['n']}, {r['seconds']}s)")
    for d in DIMENSIONS:
        v = r["dimensions"].get(d)
        st = (r.get("steps_by_dim") or {}).get(d)
        la = (r.get("latency_by_dim") or {}).get(d)
        extra = f"   {st} pasos/conv, {la}s/conv" if st is not None else ""
        print(f"    {d:14s} {('%.2f/10' % v) if v is not None else '  -   '}{extra}")
    eff = []
    if r.get("steps_avg") is not None:
        eff.append(f"{r['steps_avg']} pasos/conv")
    if r.get("latency_avg") is not None:
        eff.append(f"{r['latency_avg']}s/conv")
    if eff:
        print("  eficiencia: " + ", ".join(eff))
    if r.get("gen_tps"):
        print(f"  speed: ~{r['gen_tps']} tok/s")
    print("=" * 64)


def write_report(results):
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    out = REPORTS_DIR / f"report_{datetime.now():%Y%m%d-%H%M%S}.md"
    lines = [f"# Agent IA USB Benchmark report — {datetime.now():%Y-%m-%d %H:%M}", "",
             f"| modelo | score | " + " | ".join(DIMENSIONS) + " | n | pasos/conv | s/conv | tok/s |",
             "|" + "---|" * (len(DIMENSIONS) + 6)]
    for r in results:
        dims = " | ".join(f"{r['dimensions'].get(d):.1f}" if r['dimensions'].get(d) is not None else "-" for d in DIMENSIONS)
        steps_avg = r.get("steps_avg")
        lat_avg = r.get("latency_avg")
        lines.append(f"| {r['label']} | **{r['overall']:.2f}** | {dims} | {r['n']} | "
                     f"{steps_avg if steps_avg is not None else '-'} | {lat_avg if lat_avg is not None else '-'} | {r.get('gen_tps') or '-'} |")
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"\nReport written to {out.relative_to(AGENT_IA_USB_ROOT)}")


def load_results():
    out = []
    if RESULTS_DIR.exists():
        for f in sorted(RESULTS_DIR.glob("*.json")):
            try:
                out.append(json.loads(f.read_text(encoding="utf-8")))
            except Exception:
                pass
    return out


def cmd_run(args):
    from core import launcher
    config = AgentIaUsbConfig(AGENT_IA_USB_ROOT)
    if not LLMClient(config.server_url).is_server_running():
        print("Model server is not running. Start it with: python 03_AGENT\\core\\launcher.py --start")
        return 1
    scenarios = load_scenarios()
    chosen = scenarios if args.all_scenarios else stratified_sample(scenarios, args.sample)
    if args.models and args.models.lower() == "all":
        model_ids = [m.id for m in config.list_models()]
    elif args.models:
        model_ids = [x.strip() for x in args.models.split(",")]
    else:
        model_ids = [None]       # whatever is loaded now
    results = []
    for mid in model_ids:
        if mid is not None:
            print(f"\n### Loading model '{mid}' ...")
            if not launcher.ensure_server(tier=mid):
                print(f"  could not load '{mid}', skipping.")
                continue
            config = AgentIaUsbConfig(AGENT_IA_USB_ROOT)
        label = args.label or config.runtime_state.get("label", "unknown")
        print(f"\n{'=' * 64}\nAgent IA USB BENCHMARK — {label} — {len(chosen)} scenarios\n{'=' * 64}")
        r = run_scenarios(config, chosen, label, verbose=args.verbose)
        print_result(r)
        results.append(r)
    write_report(results)
    return 0


def cmd_report(args):
    results = load_results()
    if not results:
        print("No results yet. Run: python evaluation\\benchmark.py run")
        return 1
    latest = {}
    for r in results:
        latest[r["label"]] = r       # keep the most recent per label
    rows = sorted(latest.values(), key=lambda r: -r["overall"])
    for r in rows:
        print_result(r)
    write_report(rows)
    return 0


def main():
    ap = argparse.ArgumentParser(description="Agent IA USB benchmark")
    sub = ap.add_subparsers(dest="cmd")
    g = sub.add_parser("generate")
    g.add_argument("--seed", type=int, default=7)
    r = sub.add_parser("run")
    r.add_argument("--sample", type=int, default=40, help="stratified sample size (default 40)")
    r.add_argument("--all-scenarios", action="store_true", help="run all ~1000 (slow)")
    r.add_argument("--models", help="'all', or comma-separated ids; default: the running model")
    r.add_argument("--label", help="name for this run")
    r.add_argument("--verbose", action="store_true")
    sub.add_parser("report")
    args = ap.parse_args()
    if args.cmd == "generate":
        generate(args.seed)
        return 0
    if args.cmd == "run":
        return cmd_run(args)
    if args.cmd == "report":
        return cmd_report(args)
    ap.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main() or 0)
