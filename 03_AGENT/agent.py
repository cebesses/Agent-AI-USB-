#!/usr/bin/env python3
"""
Agent IA USB Agent - terminal interface.
Streams the reply live, shows every tool step, asks before risky actions, supports
undo, and lets you pick or switch the model with /modelos and /usar.
"""
import sys, os, json, argparse, time, threading
from pathlib import Path


class C:
    """ANSI color codes."""
    R = "\033[0m"
    B = "\033[1m"
    D = "\033[2m"
    RED = "\033[91m"
    GRN = "\033[92m"
    YLW = "\033[93m"
    CYN = "\033[96m"
    WHT = "\033[97m"


class Spinner:
    """'pensando...' animation while the model works (stopped before any output)."""
    def __init__(self):
        self._stop = threading.Event()
        self._thread = None
        self._lock = threading.Lock()

    def start(self, label="pensando"):
        with self._lock:
            if self._thread:
                return
            self._stop.clear()
            self._thread = threading.Thread(target=self._run, args=(label,), daemon=True)
            self._thread.start()

    def _run(self, label):
        frames, i, t0 = "|/-\\", 0, time.time()
        while not self._stop.is_set():
            sys.stdout.write(f"\r  {C.CYN}{frames[i % 4]}{C.R} {C.D}{label}... {int(time.time() - t0)}s{C.R}  ")
            sys.stdout.flush()
            i += 1
            self._stop.wait(0.15)
        sys.stdout.write("\r" + " " * 40 + "\r")
        sys.stdout.flush()

    def stop(self):
        with self._lock:
            if self._thread:
                self._stop.set()
                self._thread.join()
                self._thread = None


def main():
    parser = argparse.ArgumentParser(description="Agent IA USB - Portable AI Agent")
    parser.add_argument("--agent-ia-usb-root", type=str, help="Path to Agent IA USB root")
    parser.add_argument("--chat", type=str, help="Single message mode")
    parser.add_argument("--yes", action="store_true", help="Approve risky actions without asking (single message mode)")
    parser.add_argument("--model", type=str, help="Start with this model id/alias (skips the picker)")
    parser.add_argument("--pick", action="store_true", help="Always show the model picker at startup")
    parser.add_argument("--no-color", action="store_true")
    parser.add_argument("--continue", dest="continue_", action="store_true", help="Resume the most recent conversation")
    parser.add_argument("--resume", action="store_true", help="Pick a past conversation to resume")
    args = parser.parse_args()

    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stdin.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    if os.name == "nt":
        os.system("")  # enable ANSI colors in the Windows console
    if args.no_color:
        for attr in dir(C):
            if attr.isupper() and not attr.startswith("_"):
                setattr(C, attr, "")

    AGENT_IA_USB_ROOT = Path(args.agent_ia_usb_root) if args.agent_ia_usb_root else Path(__file__).resolve().parent.parent
    sys.path.insert(0, str(AGENT_IA_USB_ROOT / "03_AGENT"))

    from core.config import AgentIaUsbConfig
    from core.llm_client import LLMClient
    from core.tools import ToolRegistry
    from core.policy import PolicyEngine
    from core.vision import VisionPipeline
    from core import launcher
    from orchestrator.agent import AgentIaUsbAgent
    from memory.memory import MemorySystem
    from skills.skills import SkillLibrary

    spinner = Spinner()
    # per-turn display state (reset before each request)
    turn = {"tokens": False, "in_text": False}

    def on_event(kind, data):
        if kind == "llm_start":
            spinner.start()
        elif kind == "llm_end":
            spinner.stop()
        elif kind == "token":
            spinner.stop()
            if not turn["in_text"]:
                sys.stdout.write(f"\n  {C.GRN}{name}>{C.R} ")
                turn["in_text"] = True
                turn["tokens"] = True
            sys.stdout.write(data.get("text", ""))
            sys.stdout.flush()
            if voice_cfg.get("stream"):
                voice_cfg["stream"].feed(data.get("text", ""))
        elif kind == "step_start":
            spinner.stop()
            if turn["in_text"]:
                sys.stdout.write("\n")
                turn["in_text"] = False
            print(f"  {C.CYN}>{C.R} {C.B}{data['tool']}{C.R} {C.D}{data.get('summary', '')}{C.R}")
        elif kind == "step_end":
            mark = f"{C.GRN}ok{C.R}" if data["success"] else f"{C.RED}x{C.R}"
            print(f"    {mark} {C.D}{data.get('preview', '')}{C.R}")
        elif kind == "plan":
            spinner.stop()
            if turn["in_text"]:
                sys.stdout.write("\n")
                turn["in_text"] = False
            box = {"pending": " ", "doing": ">", "done": "x", "skipped": "-"}
            print(f"  {C.CYN}Plan:{C.R}")
            for i, st in enumerate(data["plan"], 1):
                color = C.GRN if st["status"] == "done" else (C.YLW if st["status"] == "doing" else C.D)
                print(f"    {color}{i}. [{box.get(st['status'], ' ')}] {st['text']}{C.R}")

    def confirm(tool, summary):
        spinner.stop()
        if turn["in_text"]:
            sys.stdout.write("\n")
            turn["in_text"] = False
        if args.chat:
            return args.yes
        print(f"\n  {C.YLW}{C.B}Necesito tu permiso{C.R} {C.D}({tool}){C.R}")
        for line in summary.splitlines():
            print(f"  {C.YLW}|{C.R} {line}")
        while True:
            try:
                answer = input(f"  ¿Continuar? {C.B}[s]{C.R}í / {C.B}[n]{C.R}o / {C.B}[t]{C.R}odo en esta sesión: ").strip().lower()
            except EOFError:
                return False
            if answer in ("s", "si", "sí", "y", "yes"):
                return True
            if answer in ("n", "no", ""):
                return False
            if answer in ("t", "todo", "siempre", "a", "always"):
                policy.auto_approve = True
                return True

    def build():
        config = AgentIaUsbConfig(AGENT_IA_USB_ROOT)
        llm = LLMClient(config.server_url)
        memory = MemorySystem(AGENT_IA_USB_ROOT / "03_AGENT" / "memory")
        skills = SkillLibrary(AGENT_IA_USB_ROOT / "03_AGENT" / "skills")
        tools = ToolRegistry(policy_engine=policy, memory=memory, vision=VisionPipeline(config.server_url))
        agent = AgentIaUsbAgent(llm, tools, policy, memory, on_event=on_event, confirm=confirm,
                          skills=skills, config=config)
        return config, llm, memory, skills, tools, agent

    config = AgentIaUsbConfig(AGENT_IA_USB_ROOT)
    policy = PolicyEngine(config)

    def print_models(cfg):
        active = cfg.runtime_state.get("model_file", "")
        models = cfg.list_models()
        if not models:
            print(f"  {C.YLW}No hay modelos en 01_MODEL. Ejecuta SETUP-USB.bat.{C.R}")
            return models
        print(f"\n  {C.B}Modelos instalados:{C.R}")
        for i, m in enumerate(models, 1):
            tags = []
            if m.vision:
                tags.append("visión")
            if m.mtp:
                tags.append("MTP")
            here = f" {C.GRN}(activo){C.R}" if Path(m.filename).name == Path(active).name else ""
            extra = f" {C.D}[{', '.join(tags)}]{C.R}" if tags else ""
            print(f"    {C.CYN}{i}{C.R}. {m.label}  {C.D}{m.ram_need_gb():.1f} GB · ctx {m.context}{C.R}{extra}{here}")
        print(f"  {C.D}Cambia con: /usar <nº o nombre>{C.R}")
        return models

    # Pick the model before the server starts (only when it isn't already running).
    forced = args.model
    if not launcher.is_running(config) and (args.pick or (not forced and config.agent_config.get("model", "auto") in ("", "auto", None))):
        models = print_models(config)
        if models and (len(models) > 1 or args.pick):
            try:
                choice = input(f"  Elige un modelo [Enter = automático]: ").strip()
            except (EOFError, KeyboardInterrupt):
                choice = ""
            if choice:
                info = config.model_registry.get(choice)
                if info:
                    forced = info.id
                else:
                    print(f"  {C.YLW}No reconocí '{choice}'; uso el automático.{C.R}")

    if not launcher.is_running(config):
        if not any(launcher.slot_dir().glob("agent_ia_usb_*.bin")):
            print(f"  {C.D}Primera vez en este PC: prepararé el modelo (1-3 min). Las próximas veces será rápido.{C.R}")
        print(f"  {C.YLW}Iniciando el servidor del modelo...{C.R}")
        if not launcher.ensure_server(tier=forced):
            print(f"  {C.RED}No pude iniciar el servidor del modelo. Revisa 02_RUNTIME\\server.log{C.R}")
            return 1
    elif forced:
        launcher.ensure_server(tier=forced)

    config, llm, memory, skills, tools, agent = build()
    name = config.agent_config.get("assistant_name", "Agent IA")

    # Voice plugin (09_PLUGINS/voice): off until the user says /voz on.
    # voice_cfg["stream"] is the current reply being spoken (token events feed it).
    voice_cfg = {"manager": None, "stream": None, "pause": False}
    try:
        sys.path.insert(0, str(AGENT_IA_USB_ROOT / "09_PLUGINS"))
        import voice as voice_plugin
        voice_cfg["manager"] = voice_plugin.VoiceManager(AGENT_IA_USB_ROOT, config.agent_config.get("voice"))
    except Exception:
        voice_cfg["manager"] = None
    voice = voice_cfg["manager"]

    def save_voice_cfg():
        if not voice:
            return
        config.agent_config["voice"] = {
            "mode": voice.mode,
            "tts_voice": voice.tts.voice_name,
            "stt_model": voice.stt.model,
            "stt_language": voice.stt.language,
            "mic_device": voice.mic_device,
        }
        config.save_agent_config()

    def speak_done():
        """Close the spoken reply and wait until Agent IA USB finishes talking."""
        h = voice_cfg["stream"]
        voice_cfg["stream"] = None
        if h:
            h.finish()
            h.wait()

    def listen_voice(continuous):
        heard = voice.listen(continuous=continuous)
        if voice.last_error:
            print(f"  {C.RED}{voice.last_error}{C.R}")
            voice.last_error = ""
        return (heard or "").strip()

    def banner():
        st = agent.get_status()
        rs = config.runtime_state
        engine = str(rs.get("runtime", "")).upper() + (" + MTP" if rs.get("mtp") else "")
        line = f"{st['model']} · modo {agent.mode} · internet {'on' if st['network'] != 'OFFLINE' else 'off'}"
        if engine.strip():
            line += f" · {engine}"
        if voice and voice.enabled:
            line += f" · voz {voice.mode}"
        print(f"\n  {C.CYN}{C.B}{name}{C.R} {C.D}| {line} | {st['tools_count']} herramientas | {st['session_id']}{C.R}")
        if config.runtime_state.get("tools_ok") is False:
            print(f"  {C.YLW}Aviso: este modelo no soporta herramientas; funcionaré solo como chat.{C.R}")

    sessions_dir = AGENT_IA_USB_ROOT / "03_AGENT" / "sessions"

    def list_sessions(limit=15):
        out = []
        try:
            files = sorted(sessions_dir.glob("AGENT-IA-USB-*.json"), key=lambda f: f.stat().st_mtime, reverse=True)
        except Exception:
            files = []
        for f in files[:limit]:
            try:
                s = json.loads(f.read_text(encoding="utf-8"))
            except Exception:
                continue
            first = next((m.get("content", "") for m in (s.get("history") or []) if m.get("role") == "user"), "")
            out.append((s, first))
        return out

    def resume_session(state):
        agent.load_session(state)
        print(f"  {C.GRN}Reanudada{C.R} {agent.session_id} {C.D}({len(agent.history)} mensajes){C.R}")
        for m in agent.history[-6:]:
            who = f"{C.B}Tú>{C.R}" if m.get("role") == "user" else f"{C.GRN}{name}>{C.R}"
            print(f"    {who} {str(m.get('content', ''))[:100]}")

    if args.continue_ or args.resume:
        sess = list_sessions(20)
        if not sess:
            print(f"  {C.YLW}No hay conversaciones guardadas todavía.{C.R}")
        elif args.continue_:
            resume_session(sess[0][0])
        else:
            print(f"  {C.B}Conversaciones recientes:{C.R}")
            for i, (s, first) in enumerate(sess, 1):
                print(f"   {i:2}. {s.get('session_id', '?')}  {C.D}{s.get('model', '')}  {first[:48]}{C.R}")
            try:
                pick = input("  Número a reanudar [Enter = la más reciente]: ").strip()
            except (EOFError, KeyboardInterrupt):
                pick = ""
            idx = int(pick) - 1 if pick.isdigit() and 1 <= int(pick) <= len(sess) else 0
            resume_session(sess[idx][0])

    banner()

    if args.chat:
        turn.update(tokens=False, in_text=False)
        response = agent.chat(args.chat)
        spinner.stop()
        if turn["in_text"]:
            print()
        if not turn["tokens"]:
            print(f"\n{C.GRN}{name}:{C.R} {response}")
        return 0

    print(f"  {C.D}Háblame en español (o en inglés). '/ayuda' para ver comandos, '/voz on' para hablarle, '/salir' para terminar.{C.R}\n")

    def switch_model(choice):
        """Switch the active model, keeping the whole conversation."""
        nonlocal config, llm, memory, skills, tools, agent
        info = config.model_registry.get(choice)
        if not info:
            print(f"  {C.RED}No encontré el modelo '{choice}'.{C.R} Usa /modelos para ver la lista.")
            return
        saved = {"history": agent.history, "messages": agent.messages, "mode": agent.mode,
                 "session_id": agent.session_id}
        config.agent_config["model"] = info.id
        config.save_agent_config()
        print(f"  {C.YLW}Cambiando a {info.label}... (la conversación se conserva){C.R}")
        if not launcher.ensure_server(tier=info.id):
            print(f"  {C.RED}No pude cargar {info.label}.{C.R}")
            return
        config, llm, memory, skills, tools, agent = build()
        agent.history, agent.messages = saved["history"], saved["messages"]
        agent.mode, agent.session_id = saved["mode"], saved["session_id"]
        banner()

    while True:
        if voice and voice.enabled and voice.mode == "continuo" and not voice_cfg["pause"]:
            try:
                print(f"  {C.D}escuchando... (Ctrl+C = teclado){C.R}")
                heard = listen_voice(continuous=True)
            except KeyboardInterrupt:
                voice_cfg["pause"] = True
                print(f"\n  {C.YLW}Escucha en pausa: escribe tu mensaje o '/voz off' para volver a la voz.{C.R}")
                continue
            if not heard:
                continue
            if heard.lower().strip("¡!¿? .") in ("voz off", "voz apagar", "apaga la voz", "desactiva la voz"):
                user_input = "/voz off"
            else:
                print(f"  {C.B}Tú (voz)>{C.R} {heard}")
                user_input = heard
        else:
            try:
                user_input = input(f"{C.B}Tú>{C.R} ").strip()
            except (EOFError, KeyboardInterrupt):
                break
            if not user_input:
                if voice and voice.enabled and voice.mode == "push":
                    print(f"  {C.D}grabando... (Enter para terminar){C.R}")
                    heard = listen_voice(continuous=False)
                    if not heard:
                        continue
                    print(f"  {C.B}Tú (voz)>{C.R} {heard}")
                    user_input = heard
                else:
                    continue
            voice_cfg["pause"] = False
        cmd = user_input.lower().lstrip("/").strip()
        turn.update(tokens=False, in_text=False)
        try:
            if cmd in ("exit", "quit", "q", "bye", "salir", "adios", "adiós"):
                break
            elif cmd in ("clear", "cls", "limpiar"):
                os.system("cls" if os.name == "nt" else "clear")
                banner()
            elif cmd in ("new", "reset", "nueva", "nuevo"):
                agent.reset()
                print(f"  {C.GRN}Conversación nueva.{C.R} (la memoria y las habilidades se conservan)")
            elif cmd in ("history", "historial"):
                for msg in agent.history[-20:]:
                    who = f"{C.B}Tú>{C.R}" if msg["role"] == "user" else f"{C.GRN}{name}>{C.R}"
                    print(f"    {who} {msg['content'][:100]}")
            elif cmd in ("sesiones", "sessions", "resumir", "resume") or \
                    cmd.startswith(("sesiones ", "resumir ", "resume ", "sessions ")):
                arg = user_input.split(" ", 1)[1].strip() if " " in user_input.strip() else ""
                sess = list_sessions(20)
                if not sess:
                    print(f"  {C.YLW}No hay conversaciones guardadas.{C.R}")
                elif not arg:
                    print(f"  {C.B}Conversaciones recientes (usa /sesiones <nº> para reanudar):{C.R}")
                    for i, (s, first) in enumerate(sess, 1):
                        print(f"   {i:2}. {s.get('session_id', '?')}  {C.D}{s.get('model', '')}  {first[:48]}{C.R}")
                else:
                    if arg.isdigit() and 1 <= int(arg) <= len(sess):
                        pick = sess[int(arg) - 1][0]
                    else:
                        pick = next((s for s, _ in sess if s.get("session_id") == arg), None)
                    resume_session(pick) if pick else print(f"  {C.RED}No encontré esa sesión.{C.R}")
            elif cmd in ("skills", "habilidades"):
                found = list(skills.skills.values())
                for s in found[-15:]:
                    print(f"  {C.D}-{C.R} {s.procedure[:110]}")
                if not found:
                    print(f"  {C.D}Todavía no aprendí habilidades.{C.R}")
            elif cmd in ("models", "modelos"):
                print_models(config)
            elif cmd in ("current", "actual"):
                print(f"  Modelo activo: {C.B}{agent.get_status()['model']}{C.R}")
            elif cmd in ("scan", "escanear", "models scan", "rescan"):
                config.model_registry.discover(refresh=True)
                print_models(config)
            elif cmd.startswith("usar ") or cmd.startswith("use ") or cmd.startswith("modelo ") or cmd.startswith("model "):
                switch_model(user_input.split(" ", 1)[1].strip())
            elif cmd in ("ajustar", "tune"):
                print(f"  {C.YLW}Midiendo la configuración más rápida para este PC (unos minutos)...{C.R}")
                saved = {"history": agent.history, "messages": agent.messages, "mode": agent.mode}
                if launcher.ensure_server(tune=True):
                    config, llm, memory, skills, tools, agent = build()
                    agent.history, agent.messages, agent.mode = saved["history"], saved["messages"], saved["mode"]
                    banner()
            elif cmd == "voz" or cmd.startswith("voz "):
                if not voice:
                    print(f"  {C.RED}El plugin de voz no está disponible en esta instalación.{C.R}")
                else:
                    arg = user_input.split(" ", 1)[1].strip() if " " in user_input.strip() else ""
                    print(f"  {voice.apply_command(arg)}")
                    save_voice_cfg()
                    if voice.enabled:
                        banner()
            elif cmd == "voces" or cmd.startswith("voces "):
                if voice:
                    arg = user_input.split(" ", 1)[1].strip() if " " in user_input.strip() else ""
                    print(f"  {voice.pick_tts_voice(arg)}")
                    save_voice_cfg()
            elif cmd == "micro" or cmd.startswith("micro "):
                if voice:
                    arg = user_input.split(" ", 1)[1].strip() if " " in user_input.strip() else ""
                    print(f"  {voice.pick_mic(arg)}")
                    save_voice_cfg()
            else:
                if voice and voice.enabled:
                    voice_cfg["stream"] = voice.tts.speak_stream(None)
                response = agent.chat(user_input)
                spinner.stop()
                if turn["in_text"]:
                    print("\n")
                elif not turn["tokens"]:
                    print(f"\n  {C.GRN}{name}>{C.R} {response}\n")
                else:
                    print()
                if voice and voice.enabled:
                    h = voice_cfg["stream"]
                    if h and not h.full_text and response:
                        h.feed(response)  # the reply arrived without token events
                    speak_done()
                    voice_cfg["pause"] = False  # ready to listen again
        except KeyboardInterrupt:
            spinner.stop()
            h = voice_cfg.get("stream")
            voice_cfg["stream"] = None
            if h:
                h.stop()
            print(f"\n  {C.YLW}Interrumpido.{C.R}")

    spinner.stop()
    h = voice_cfg.get("stream")
    if h:
        h.stop()
    agent._save_session()
    print(f"\n  {C.GRN}Sesión guardada.{C.R} ¡Hasta luego!\n")
    return 0


if __name__ == "__main__":
    sys.exit(main() or 0)
