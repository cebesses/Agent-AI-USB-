"""
Agent IA USB Orchestrator - the agent loop.
The model sees the user's request plus JSON schemas of every tool, decides
which tools to call, sees their results, and keeps going until the job is
done - like Claude Code, but fully local.

Note for the llama.cpp prompt cache: earlier messages are never rewritten
(only appended), so each step only processes the new tokens.
"""
import json
import re
import uuid
import hashlib
from pathlib import Path
from datetime import datetime

AGENT_IA_USB_ROOT = Path(__file__).resolve().parent.parent.parent
MAX_RESULT_CHARS = 6000

SECRET_PATTERNS = [
    r'(?i)(password|passwd|pwd)\s*[:=]\s*\S+',
    r'(?i)(api[_-]?key|apikey)\s*[:=]\s*\S+',
    r'(?i)(secret|token|auth)\s*[:=]\s*\S+',
    r'(?i)(access[_-]?key)\s*[:=]\s*\S+',
    r'-----BEGIN\s+(RSA\s+)?PRIVATE\s+KEY-----',
]


def redact_secrets(text):
    if not text:
        return text
    for pattern in SECRET_PATTERNS:
        text = re.sub(pattern, lambda m: m.group().split(':')[0].split('=')[0] + ': [REDACTED]', text)
    text = re.sub(r'\b(?=[A-Za-z0-9_-]*\d)(?=[A-Za-z0-9_-]*[A-Za-z])[A-Za-z0-9_-]{40,}\b', '[REDACTED_TOKEN]', text)
    return text


_REASON_HINTS = re.compile(
    r"\b(analiz|razon|explica|explicar|por\s*qu[eé]|porqu[eé]|compar|calcul|resuelv|resolver|deduc|"
    r"demuestr|optimiz|algoritmo|depura|debug|dise[nñ]|estrateg|evalu|exploit|vulnerab|payload|"
    r"paso\s+a\s+paso|cu[aá]l\s+es\s+mejor|c[oó]mo\s+(hago|funciona|puedo|implement|hacer))", re.IGNORECASE)


def wants_reasoning(user_input, mode="normal", setting="auto"):
    """Decide whether to turn on the model's thinking mode for this turn.
    setting: 'off' never, 'always' always, 'auto' = hard tasks only."""
    setting = (setting or "auto").lower()
    if setting == "off":
        return False
    if setting == "always":
        return True
    text = user_input or ""
    if mode in ("pentest", "codigo", "seguridad"):
        return True
    if len(text) > 160 or "```" in text or (text.count("?") + text.count("¿")) >= 2:
        return True
    return bool(_REASON_HINTS.search(text))


def clean_thinking(text):
    """Remove reasoning/tool-call artifacts the model may leave in its text."""
    if not text:
        return ""
    text = re.sub(r'<think>.*?</think>', '', text, flags=re.DOTALL)
    text = re.sub(r'<thinking>.*?</thinking>', '', text, flags=re.DOTALL)
    text = re.sub(r'<tool_call>.*?</tool_call>', '', text, flags=re.DOTALL)
    text = re.sub(r'</?(think|tool_call|tool_response)>', '', text)
    # Plain text for the terminal/web: drop Markdown bold and headings the model may still use
    text = re.sub(r'\*\*(.+?)\*\*', r'\1', text)
    text = re.sub(r'(?m)^#{1,6}\s+', '', text)
    text = re.sub(r'\n{3,}', '\n\n', text)
    return text.strip()


_SPANISH = {"que", "los", "las", "para", "con", "por", "una", "está", "están", "archivos", "carpeta", "puedo",
            "ayudarte", "hola", "gracias", "también", "pero", "como", "cómo", "aquí", "listo", "hecho", "movido",
            "del", "tus", "su", "sus", "esto", "eso", "señor", "claro", "necesitas", "el", "la", "de", "en", "y",
            "es", "son", "moví", "copié", "eliminé", "ya", "se", "lo", "al", "tu", "hay", "quieres"}
_ENGLISH = {"the", "and", "to", "of", "is", "you", "your", "files", "folder", "i", "it", "in", "done", "moved",
            "with", "for", "are", "that", "this", "from", "have", "can", "what", "here", "there", "were", "was",
            "copied", "deleted", "all", "into", "my", "would", "like"}


def _language_counts(text):
    words = re.findall(r"[a-záéíóúñü]+", (text or "").lower())
    return sum(w in _SPANISH for w in words), sum(w in _ENGLISH for w in words)


def looks_non_english(text):
    es, en = _language_counts(text)
    return es >= 3 and es > en


def looks_non_spanish(text):
    es, en = _language_counts(text)
    return en >= 3 and en > es


def wrong_language(text, lang="es"):
    """True when a reply is clearly not in Agent IA USB's configured language."""
    return looks_non_english(text) if lang == "en" else looks_non_spanish(text)


def repair_json(text):
    """Best-effort fix of slightly broken tool arguments (trailing commas, unclosed
    quotes/brackets). Returns a dict or None."""
    s = (text or "").strip()
    if not s:
        return {}
    for candidate in (s, re.sub(r",\s*([}\]])", r"\1", s)):
        try:
            value = json.loads(candidate)
            return value if isinstance(value, dict) else None
        except ValueError:
            pass
    fixed, stack, in_str, esc = [], [], False, False
    for ch in s:
        fixed.append(ch)
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
        elif ch == '"':
            in_str = True
        elif ch in "{[":
            stack.append("}" if ch == "{" else "]")
        elif ch in "}]" and stack:
            stack.pop()
    tail = ('"' if in_str else "") + "".join(reversed(stack))
    try:
        value = json.loads(re.sub(r",\s*([}\]])", r"\1", "".join(fixed) + tail))
        return value if isinstance(value, dict) else None
    except ValueError:
        return None


# Verbs that claim an action, and the tools that would have to have run for the claim to be true
_CLAIMS = [  # first-person past tense only, so descriptions like "archivos creados por Windows" don't trigger
    (r"\b(moví|traslad[eé]|organic[eé]|orden[eé]|he (movido|trasladado|organizado|ordenado)|i moved|i organized|i sorted)\b",
     {"move_files", "rename"}),
    (r"\b(copi[eé]|respald[eé]|he (copiado|respaldado)|i copied|i backed up)\b", {"copy_files"}),
    (r"\b(elimin[eé]|borr[eé]|he (eliminado|borrado)|i deleted|i removed)\b", {"delete_files"}),
    (r"\b(creé|escribí|he (creado|escrito|guardado)|i created|i wrote|i saved)\b",
     {"make_dir", "write_file", "create_project", "move_files", "copy_files", "edit_file"}),
    (r"\b(renombr[eé]|he renombrado|i renamed)\b", {"rename", "move_files"}),
    (r"\b(el archivo (contiene|dice|trata|es)|he leído|leí el|i read|the file (contains|says))\b",
     {"read_file", "search_text", "file_info"}),
]


# Requests that name a specific action: if the tool was never called, the model just talked about it
_INTENTS = [
    (r"\b(pon\w*|mete\w*|manda\w*|a[ií]sla\w*|pasa\w*|put|move|send)\b[^.?!]*\b(cuarentena|quarantine)\b",
     {"quarantine"}, "poner archivos en cuarentena",
     "Si el escaneo encontró archivos que cumplen lo pedido, llama ahora a quarantine con sus rutas completas; "
     "si no encontró ninguno, dilo claramente."),
]


def missing_action(text, trace):
    """(description, hint) of an explicitly requested action whose tool was never called, or None."""
    called = {t["tool"] for t in trace}
    for pattern, tools, what, hint in _INTENTS:
        if re.search(pattern, (text or "").lower()) and not called & tools:
            return what, hint
    return None


def mentioned_files(text):
    """Existing files whose Windows path appears in the user's message."""
    found = []
    for m in re.finditer(r"[A-Za-z]:\\[^\n\"'<>|?*]+?\.\w{1,5}\b", text or ""):
        p = Path(m.group(0).strip())
        if p.is_file():
            found.append(str(p))
    return found


def unsupported_claim(reply, trace):
    """Name of an action the reply claims but no successful tool call backs up, or None."""
    done = {t["tool"] for t in trace if t.get("success")}
    text = (reply or "").lower()
    for pattern, tools in _CLAIMS:
        m = re.search(pattern, text)
        if m and not done & tools:
            return m.group(0)
    return None


def instant_answer(text, now=None):
    """Reply to trivial requests without the model (under a second instead of ~20 s).
    Returns None when the request needs real thinking."""
    from core import paths
    from core.prompts import DAYS, MONTHS
    raw = (text or "").strip()
    if not raw or len(raw) > 48:
        return None
    s = re.sub(r"[¿?¡!.,;:]", "", raw.lower()).strip()
    s = s.translate(str.maketrans("áéíóú", "aeiou"))
    s = re.sub(r"\s+", " ", s)
    now = now or datetime.now()
    if re.fullmatch(r"(dime )?(la )?hora( actual| ahora)?|que hora es( ahora| ahorita)?|what time is it|hora", s):
        return f"Son las {now:%H:%M}."
    if re.fullmatch(r"(dime )?(la )?fecha( de hoy| actual)?|que (dia|fecha) es( hoy)?|what (day|date) is it|en que dia estamos", s):
        return f"Hoy es {DAYS[now.weekday()]} {now.day} de {MONTHS[now.month - 1]} de {now.year}."
    if re.fullmatch(r"(cuanto )?espacio (libre |en disco )?(me queda|tengo|hay|libre|en disco)?( en (el )?(disco|pc))?"
                    r"|cuanto espacio (libre )?(me queda|tengo|hay)( en (el )?(disco|pc))?|how much (free )?space( do i have)?", s):
        rows = [f"{d['letter']} {d['label'] or ''}".strip() + f": {paths.human_size(d['free'])} libres de {paths.human_size(d['total'])}"
                for d in paths.list_drives() if d["type"] in ("fixed", "removable", "network") and d["total"]]
        return "Espacio en tus unidades:\n" + "\n".join(f"- {r}" for r in rows) if rows else None
    greetings = {"hola": "¡Hola! ¿Qué hago por ti?", "buenas": "¡Buenas! ¿Qué necesitas?", "hey": "Aquí estoy. ¿Qué necesitas?",
                 "buenos dias": "Buenos días. Lista cuando tú digas.", "buenas tardes": "Buenas tardes. ¿Qué hago por ti?",
                 "buenas noches": "Buenas noches. ¿Qué necesitas?", "gracias": "Cuando quieras.", "muchas gracias": "Con gusto.",
                 "thanks": "Un placer.", "hello": "¡Hola! ¿Qué hago por ti?", "hi": "¡Hola! ¿Qué hago por ti?"}
    return greetings.get(s)


MODE_GUIDANCE = {
    "codigo": ("MODO CÓDIGO activo: para crear programas o webs usa create_project con TODOS los archivos completos "
               "y funcionales (nada de 'TODO' ni partes por completar). Si un archivo es muy largo, escríbelo por partes "
               "con write_file y luego append=true. Después pruébalo (run_python o run_powershell con cwd en la carpeta, "
               "u open para abrir la web), lee los errores y corrígelos. Si no te dan carpeta, usa "
               "{documents}\\AGENT-IA-USB-Proyectos\\<nombre>. Termina diciendo qué archivos creaste y cómo usarlo."),
    "seguridad": ("MODO SEGURIDAD activo: actúas como auditora defensiva de ESTE PC. Usa security_scan o security_audit "
                  "(y comandos de solo lectura) para revisar. Informe breve (máximo 6 líneas): riesgos ordenados (alto, medio, "
                  "bajo), la evidencia y cómo corregirlo. Solo cambias algo si el usuario lo pide; si pide limpiar, aislar o "
                  "poner en cuarentena, llama quarantine / remove_persistence tú mismo (el sistema ya pide su confirmación). "
                  "Este modo es defensivo (auditoría); para pruebas ofensivas autorizadas el usuario puede pedir 'modo pentest'. "
                  "Califica con criterio. ALTO: firewall o antivirus apagados, "
                  "detecciones de Defender, entradas de inicio con motivos sospechosos. MEDIO: RDP o SMBv1 activos sin "
                  "necesidad, firmas de antivirus viejas. BAJO: archivos sin firma por sí solos (muchas herramientas legítimas "
                  "no la tienen; solo suben de nivel si están en Temp con nombre raro o Defender los marca). Los puertos 135, "
                  "445, 5040 y 49664-49680 (RPC de Windows) son normales. Para un chequeo general del PC, security_scan ya "
                  "incluye el estado de Defender y firewall."),
    "pentest": ("MODO PENTEST activo: asistes a un ingeniero de ciberseguridad sobre objetivos que posee o tiene permiso "
                "de testear. Trabaja por fases y propón el siguiente paso en cada una: (1) RECONOCIMIENTO con la herramienta "
                "network (interfaces, dns, ping, portscan...) y lectura de código/config; (2) ANÁLISIS: identifica posibles "
                "fallas y su probable causa, cita la evidencia; (3) EXPLOTACIÓN CONTROLADA: escribe el PoC o exploit con "
                "run_code (o run_python/run_powershell) para TESTEAR, muéstralo y el sistema pedirá confirmación antes de "
                "ejecutarlo; (4) DOCUMENTACIÓN: resume hallazgos, impacto y cómo mitigarlos. Muestra siempre la salida real "
                "de las herramientas, nunca inventada. No lances nada masivo ni de denegación de servicio (DoS), no borres ni "
                "destruyas datos, no desactives defensas, y no crees malware para dañar o evadir detección con fines maliciosos."),
}


def parse_text_tool_calls(text):
    """Fallback when the server didn't parse tool calls: read them from the text."""
    calls = []
    for block in re.findall(r"<tool_call>(.*?)</tool_call>", text or "", re.DOTALL):
        block = block.strip()
        if block.startswith("{"):
            try:
                obj = json.loads(block)
                calls.append({"name": obj.get("name"), "arguments": obj.get("arguments") or obj.get("parameters") or {}})
                continue
            except ValueError:
                pass
        m = re.search(r"<function=([\w.-]+)>(.*?)(?:</function>|$)", block, re.DOTALL)
        if m:
            args = {k: v.strip() for k, v in re.findall(r"<parameter=([\w-]+)>(.*?)</parameter>", m.group(2), re.DOTALL)}
            for k, v in args.items():
                if v[:1] in "[{":
                    try:
                        args[k] = json.loads(v)
                    except ValueError:
                        pass
            calls.append({"name": m.group(1), "arguments": args})
    return [c for c in calls if c.get("name")]


_PREVIEW_ES = [
    (r"^(Moved|Copied) (\d+) of (\d+) item\(s\)(.*?)\. Skipped (\d+) \(name already taken\), failed (\d+)\.",
     lambda m: f"{'Movidos' if m[1] == 'Moved' else 'Copiados'} {m[2]} de {m[3]} elemento(s){m[4].replace(' to ', ' a ', 1)}. "
               f"Omitidos {m[5]} (el nombre ya existía), fallidos {m[6]}."),
    (r"^Deleted (\d+) of (\d+) item\(s\) \(([^)]*)\)\..*", lambda m: f"Eliminados {m[1]} de {m[2]} elemento(s) ({m[3]}); están en la Papelera."),
    (r"^Found (\d+) (files|folders|items) in (.*)", lambda m: f"Encontrados {m[1]} {dict(files='archivos', folders='carpetas', items='elementos')[m[2]]} en {m[3]}"),
    (r"^(.*): (\d+) folders, (\d+) files (.*)", lambda m: f"{m[1]}: {m[2]} carpetas, {m[3]} archivos {m[4]}"),
    (r"^Created folder (.*)", lambda m: f"Carpeta creada: {m[1]}"),
    (r"^Already exists: (.*)", lambda m: f"Ya existe: {m[1]}"),
    (r"^Renamed (.*)", lambda m: f"Renombrado {m[1]}"),
    (r"^Created (.*) \((\d+) chars\)", lambda m: f"Creado {m[1]} ({m[2]} caracteres)"),
    (r"^Overwrote (.*) \((\d+) chars\)", lambda m: f"Sobrescrito {m[1]} ({m[2]} caracteres)"),
    (r"^Appended (.*) \((\d+) chars\)", lambda m: f"Añadido a {m[1]} ({m[2]} caracteres)"),
    (r"^Edited (.*?): replaced (\d+) occurrence\(s\)\.", lambda m: f"Editado {m[1]}: {m[2]} reemplazo(s)."),
    (r"^Stored in long-term memory \(id (\w+)\)\.", lambda m: f"Guardado en la memoria (id {m[1]})."),
    (r"^Nothing to (\w+): no items matched\.", lambda m: "Nada que hacer: ningún elemento coincide."),
    (r"^(Opened|Launched) (.*)", lambda m: f"Abierto: {m[2]}"),
    (r"^ERROR: The user declined this action.*", lambda m: "Cancelado: no lo aprobaste."),
    (r"^ERROR: Not allowed: (.*)", lambda m: f"No permitido: {m[1]}"),
    (r"^ERROR: (.*)", lambda m: f"Error: {m[1]}"),
    (r"^No matches for '(.*)' in (\d+) text files under (.*)\.", lambda m: f"Sin coincidencias para '{m[1]}' en {m[2]} archivos de texto de {m[3]}."),
    (r"^(\d+) match\(es\) \(searched (\d+) files\):", lambda m: f"{m[1]} coincidencia(s) (revisé {m[2]} archivos):"),
    (r"^Copied (\d+) characters to the clipboard\.", lambda m: f"Copiados {m[1]} caracteres al portapapeles."),
]


def spanish_preview(text):
    """Spanish version of a tool result's first line, for what the user sees.
    (The model keeps getting the original text, which is what it was trained on.)"""
    for pattern, repl in _PREVIEW_ES:
        m = re.match(pattern, text)
        if m:
            return repl(m)
    return text


def summarize_args(args, limit=140):
    parts = []
    for k, v in (args or {}).items():
        if isinstance(v, list):
            v = f"[{len(v)} items]"
        s = str(v).replace("\n", " ")
        parts.append(f"{k}={s[:60] + '…' if len(s) > 60 else s}")
    text = "  ".join(parts)
    return text[:limit] + ("…" if len(text) > limit else "")


class AgentIaUsbAgent:
    """Tool-using agent loop on top of a local llama.cpp server."""

    def __init__(self, llm_client, tool_registry, policy_engine, memory=None, on_tool_call=None,
                 on_event=None, confirm=None, skills=None, config=None):
        self.llm = llm_client
        self.tools = tool_registry
        self.policy = policy_engine
        self.memory = memory
        self.skills = skills
        self.config = config
        self.on_tool_call = on_tool_call
        self.on_event = on_event
        self.session_id = datetime.now().strftime("AGENT-IA-USB-%Y%m%d-%H%M%S")
        self.history = []        # what the user saw: [{"role", "content"}]
        self.messages = []       # what the model saw (incl. tool calls), without the system prompt
        self.max_history = 20
        cfg = config.agent_config if config else {}
        self.max_steps = int(cfg.get("max_steps", 30))
        self.max_reply_tokens = int(cfg.get("max_reply_tokens", 1024))
        self.reasoning = str(cfg.get("reasoning", "auto")).lower()  # auto | off | always
        self._think = False        # whether to use the model's thinking mode this turn
        self.learn_skills = bool(cfg.get("learn_skills", True))
        self.log_trajectories = bool(cfg.get("log_trajectories", True))
        self.context_size = config.context_size if config else 8192
        self.sampling = config.sampling if config else {}
        self.model_label = config.tier_config().get("label", "") if config else ""
        if confirm is not None:
            self.tools.confirm = confirm
        if getattr(self.tools, "on_plan", 1) is None:
            self.tools.on_plan = lambda plan: self._emit("plan", plan=plan)
        if getattr(self.tools, "journal", None) is not None:
            self.tools.journal.session_id = self.session_id
        from core.prompts import build_system_prompt, language_of
        self.language = language_of(config)
        self.system_prompt = build_system_prompt(config, self.model_label)
        self.last_trace = []
        self.mode = "normal"        # normal | codigo | seguridad
        self._finish = None
        self._known_tokens = None
        self.stream = bool(cfg.get("stream", True))   # stream the reply token-by-token for fluency

    MODES = {"normal": "normal", "codigo": "codigo", "código": "codigo", "code": "codigo",
             "seguridad": "seguridad", "security": "seguridad", "ciberseguridad": "seguridad",
             "pentest": "pentest", "pentesting": "pentest", "ofensivo": "pentest", "offensive": "pentest"}

    def set_mode(self, name):
        mode = self.MODES.get((name or "").strip().lower())
        if not mode:
            return "Modos disponibles: normal, codigo, seguridad, pentest."
        self.mode = mode
        return {"normal": "Modo normal.",
                "codigo": "Modo código activado: crearé, probaré y corregiré programas completos.",
                "seguridad": "Modo seguridad activado: puedo auditar este PC (solo lectura) y darte un informe.",
                "pentest": "Modo pentest activado: reconocimiento, análisis y pruebas sobre objetivos autorizados; "
                           "propongo el siguiente paso y pido confirmación antes de ejecutar."}[mode]

    def set_network(self, on, config=None):
        """Internet is switched per session on purpose: every start is OFFLINE (nothing is saved to disk)."""
        if self.policy:
            self.policy.set_network_mode("FULL_INTERNET" if on else "OFFLINE")
        return ("Internet activado solo para esta sesión (buscar y leer webs pedirá tu permiso)." if on
                else "Internet apagado.")

    # ------------------------------------------------------------------ events
    def _emit(self, kind, **data):
        if self.on_event:
            try:
                self.on_event(kind, data)
            except Exception:
                pass

    # -------------------------------------------------------------------- chat
    def chat(self, user_input):
        user_input = (user_input or "").strip()
        if not user_input:
            return ""
        local = self._handle_command(user_input)
        if local is None:
            local = instant_answer(user_input)
        if local is not None:
            self._record(user_input, local)
            return local
        if hasattr(self.tools, "reset_plan"):
            self.tools.reset_plan()

        self._think = wants_reasoning(user_input, self.mode, self.reasoning)

        from core.prompts import build_turn_context, CONTEXT_HEADER
        context = build_turn_context(self.memory, self.skills, user_input)
        online = bool(self.policy) and self.policy.network_mode != "OFFLINE"
        context += ("\n\nInternet: ACTIVADO (puedes usar web_search y fetch_url; piden permiso)." if online else
                    "\n\nInternet: APAGADO. Si piden algo de internet, di que se activa con el comando: red on")
        if self.mode in MODE_GUIDANCE:
            from core import paths
            context += "\n\n" + MODE_GUIDANCE[self.mode].replace("{documents}", str(paths.known_folders()["Documents"]))
        content = f"{user_input}\n\n{CONTEXT_HEADER}\n{context}"
        turn_start = len(self.messages)
        self.messages.append({"role": "user", "content": content})
        self._fit_context()

        trace, seen, failures = [], {}, 0
        final = ""
        self._last_error = ""
        challenges = 0
        for step in range(1, self.max_steps + 1):
            self._fit_context()
            msg = self._call_llm()
            if msg is None:
                final = self._last_error
                break
            calls = self._tool_calls(msg)
            text = clean_thinking(msg.get("content") or "")
            if not calls:
                final = text
                self.messages.append({"role": "assistant", "content": text})
                claim = unsupported_claim(final, trace) if challenges < 2 else None
                gap = missing_action(user_input, trace) if challenges < 2 and not claim else None
                if gap:
                    challenges += 1
                    self._emit("step_start", step=step, tool="verificación", args={},
                               summary=f"pediste {gap[0]} y aún no se hizo")
                    self._emit("step_end", step=step, tool="verificación", success=False,
                               preview="Le recuerdo que debe actuar")
                    self.messages.append({"role": "user", "content": (
                        f"[Agent IA USB: el usuario pidió {gap[0]}, pero todavía no llamaste a la herramienta. {gap[1]}]")})
                    continue
                if claim:
                    # The model said it did something it didn't do: make it act (at most twice)
                    challenges += 1
                    self._emit("step_start", step=step, tool="verificación", args={},
                               summary=f"dijo '{claim}' sin haberlo hecho")
                    self._emit("step_end", step=step, tool="verificación", success=False,
                               preview="Le pido que lo haga de verdad")
                    hint = ""
                    files = mentioned_files(user_input)
                    if files:
                        hint = f" Por ejemplo, llama ahora read_file con path=\"{files[0]}\"."
                    self.messages.append({"role": "user", "content": (
                        f"[Agent IA USB: dijiste '{claim}', pero en este pedido no se ejecutó ninguna herramienta que lo haga. "
                        "No afirmes acciones que no hiciste ni describas archivos sin leerlos. Hazlo ahora llamando "
                        f"a las herramientas necesarias (o di claramente que no lo hiciste).{hint}]")})
                    continue
                if challenges >= 2 and unsupported_claim(final, trace):
                    final = ("No pude completar esto de forma verificable: no llegué a ejecutar las acciones necesarias. "
                             "¿Me lo pides de nuevo, quizá con más detalle?")
                    self.messages[-1]["content"] = final
                break
            self.messages.append({"role": "assistant", "content": text,
                                  "tool_calls": [c["raw"] for c in calls]})
            truncated = self._finish == "length"
            for c in calls:
                if truncated:
                    c["truncated"] = True
                result_text, success = self._run_tool(step, c)
                trace.append({"tool": c["name"], "args": c["args"], "success": success, "result": result_text[:300]})
                if not success:
                    failures += 1
                sig = hashlib.md5(f"{c['name']}{json.dumps(c['args'], sort_keys=True)}{result_text}".encode()).hexdigest()
                seen[sig] = seen.get(sig, 0) + 1
                if seen[sig] >= (2 if not success else 3):
                    result_text += ("\n[Agent IA USB: este mismo intento ya falló igual. No lo repitas: cambia de estrategia "
                                    "o explícale el problema al usuario.]" if not success else
                                    "\n[Agent IA USB: esta llamada devolvió lo mismo 3 veces. Deja de repetirla y responde con lo que tienes.]")
                self.messages.append({"role": "tool", "tool_call_id": c["id"], "name": c["name"], "content": result_text})
        else:
            final = self._final_without_tools(
                "[Agent IA USB: llegaste al límite de pasos. Resume para el usuario qué se hizo y qué falta. No llames herramientas.]",
                "Llegué a mi límite de pasos antes de terminar. Dime cómo quieres que siga.")

        if final and wrong_language(final, self.language):
            final = self._rewrite_in_language(final)
        self._ensure_remembered(user_input, trace)
        if not final:
            final = "Listo." if trace and failures == 0 else "No estoy segura de cómo responder a eso."
            if self.messages and self.messages[-1]["role"] == "assistant" and not self.messages[-1].get("tool_calls"):
                self.messages[-1]["content"] = final
            else:
                self.messages.append({"role": "assistant", "content": final})
        self.last_trace = trace
        self._record(user_input, final)
        self._learn(user_input, trace, failures, turn_start)
        self._emit("final", text=final)
        return final

    def _call_llm(self):
        # Tools are always sent: the chat template renders them at the top of the
        # prompt, so leaving them out would invalidate the whole prompt cache.
        for attempt in range(2):
            messages = [{"role": "system", "content": self.system_prompt}] + self.messages
            self._emit("llm_start")
            use_stream = bool(self.stream and attempt == 0)
            on_tok = (lambda t: self._emit("token", text=t)) if use_stream else None
            sampling = dict(self.sampling)
            if self._think:
                sampling["top_p"] = 0.95        # Qwen thinking-mode sampling (temp/top_k kept)
            raw = self.llm.chat(messages, tools=self.tools.schemas(),
                                max_tokens=self.max_reply_tokens, stream=use_stream, on_token=on_tok,
                                enable_thinking=self._think, **sampling)
            timings = raw.get("timings") if isinstance(raw, dict) else None
            self._emit("llm_end", timings=timings)
            if timings:
                # Real size of the conversation so far (prompt + the reply about to be appended)
                self._known_tokens = sum(int(timings.get(k) or 0) for k in ("cache_n", "prompt_n", "predicted_n"))
                self._known_msgs = len(self.messages) + 1
            too_long = isinstance(raw, dict) and "exceed" in str(raw.get("error", "")).lower()
            if not too_long or attempt or not self._compact_tool_results():
                break
        if not isinstance(raw, dict) or "error" in raw:
            err = raw.get("error") if isinstance(raw, dict) else raw
            self._last_error = (f"No pude comunicarme con mi modelo de lenguaje ({str(err)[:200]}). "
                                "¿Está corriendo el servidor del modelo? Prueba con START-AGENT-IA-USB.bat.")
            return None
        choices = raw.get("choices") or []
        if not choices:
            self._last_error = "El modelo devolvió una respuesta vacía."
            return None
        self._finish = choices[0].get("finish_reason")
        return choices[0].get("message") or {}

    def _tool_calls(self, msg):
        calls = []
        raw_calls = msg.get("tool_calls") or []
        if not raw_calls:
            raw_calls = [{"id": None, "type": "function",
                          "function": {"name": c["name"], "arguments": json.dumps(c["arguments"], ensure_ascii=False)}}
                         for c in parse_text_tool_calls(msg.get("content") or "")]
        for rc in raw_calls:
            fn = rc.get("function") or {}
            name = fn.get("name") or ""
            args = fn.get("arguments") or {}
            bad_json = False
            if isinstance(args, str):
                repaired = repair_json(args)
                bad_json = repaired is None
                args = repaired or {}
            call_id = rc.get("id") or f"call_{uuid.uuid4().hex[:12]}"
            raw = {"id": call_id, "type": "function",
                   "function": {"name": name, "arguments": json.dumps(args, ensure_ascii=False)}}
            calls.append({"id": call_id, "name": name, "args": args if isinstance(args, dict) else {},
                          "bad_json": bad_json, "raw": raw})
        return calls

    def _run_tool(self, step, call):
        name, args = call["name"], call["args"]
        self._emit("step_start", step=step, tool=name, args=args, summary=summarize_args(args))
        if call.get("truncated"):
            result_text, success = ("ERROR: tu respuesta se cortó porque era demasiado larga, así que no ejecuté nada. "
                                    "Divide el trabajo: escribe archivos grandes por partes (write_file con la primera "
                                    "parte, luego write_file con append=true para el resto) o crea menos archivos por "
                                    "llamada."), False
        elif call["bad_json"]:
            result_text, success = ("ERROR: los argumentos no eran JSON válido. Vuelve a llamar la herramienta con JSON "
                                    "válido; si el contenido es largo, escríbelo por partes con append=true."), False
        else:
            try:
                result = self.tools.execute(name, args)
                success = bool(result.success)
                body = result.output if success else (result.error + ("\n" + result.output if result.output else ""))
            except Exception as e:
                success, body = False, f"{type(e).__name__}: {e}"
            body = redact_secrets(body or "")
            if len(body) > MAX_RESULT_CHARS:
                body = body[:MAX_RESULT_CHARS - 1200] + f"\n... [{len(body) - MAX_RESULT_CHARS + 1200} chars cut] ...\n" + body[-1000:]
            result_text = body if success else f"ERROR: {body}"
        preview = result_text.strip().splitlines()[0] if result_text.strip() else ""
        if self.language == "es":
            preview = spanish_preview(preview)
        preview = preview[:160]
        self._emit("step_end", step=step, tool=name, success=success, preview=preview)
        if self.on_tool_call:
            try:
                self.on_tool_call(name, success, preview)
            except Exception:
                pass
        return result_text, success

    def _final_without_tools(self, instruction, fallback):
        """Ask for a plain-text answer; tool calls in the reply are ignored."""
        self.messages.append({"role": "user", "content": instruction})
        msg = self._call_llm() or {}
        text = clean_thinking(msg.get("content") or "")
        if not text and msg.get("reasoning_content"):
            text = clean_thinking(msg["reasoning_content"])
        text = text or fallback
        self.messages.append({"role": "assistant", "content": text})
        return text

    def _rewrite_in_language(self, text):
        if self.language == "en":
            note = "[Agent IA USB: your last reply was not in English. Repeat it in English only. Do not call tools.]"
        else:
            note = "[Agent IA USB: tu última respuesta no estaba en español. Repítela solo en español. No llames herramientas.]"
        return self._final_without_tools(note, text)

    # ----------------------------------------------------------------- context
    def _estimate_tokens(self):
        """Tokens the next request will use: the server's real count for what it has
        already seen, plus an estimate (3 chars/token) for messages added since."""
        known, upto = getattr(self, "_known_tokens", None), getattr(self, "_known_msgs", 0)
        if known and upto <= len(self.messages):
            return known + sum(len(json.dumps(m, ensure_ascii=False)) for m in self.messages[upto:]) // 3
        chars = len(self.system_prompt) + len(json.dumps(self.tools.schemas())) + \
                sum(len(json.dumps(m, ensure_ascii=False)) for m in self.messages)
        return chars // 3

    def _fit_context(self):
        """Drop the oldest whole turns when the conversation outgrows the context window.
        (This forces one full re-read of the prompt, so it only happens when needed.)"""
        budget = int(self.context_size * 0.85) - min(self.max_reply_tokens, 3072)
        while self._estimate_tokens() > budget:
            self._known_tokens = None  # messages are about to change: fall back to estimates
            starts = [i for i, m in enumerate(self.messages) if m["role"] == "user" and not m["content"].startswith("[Agent IA USB:")]
            if len(starts) < 2:
                # a single huge turn: shorten its biggest tool results
                biggest = max((m for m in self.messages if m["role"] == "tool"), key=lambda m: len(m["content"]), default=None)
                if not biggest or len(biggest["content"]) < 500:
                    break
                biggest["content"] = biggest["content"][:400] + "\n... [cut to save memory]"
                continue
            self.messages = self.messages[starts[1]:]

    def _compact_tool_results(self):
        """Last resort when the server says the prompt is too long: shorten every
        long tool result. Returns True if anything changed."""
        changed = False
        for m in self.messages:
            if m["role"] == "tool" and len(m["content"]) > 700:
                m["content"] = m["content"][:500] + "\n... [cut to save memory]"
                changed = True
        if changed:
            self._known_tokens = None
        return changed

    # ---------------------------------------------------------------- learning
    _REMEMBER = re.compile(r"\b(recuerda|recuérdalo|no olvides|acuérdate|anota que|remember|don't forget|note that)\b",
                           re.IGNORECASE)

    def _ensure_remembered(self, user_input, trace):
        """Safety net: if the user asked to remember something and the model
        didn't store it, store the request itself."""
        if not self.memory or not self._REMEMBER.search(user_input) or user_input.strip().endswith("?"):
            return  # questions like "¿recuerdas...?" / "do you remember...?" are not facts
        if any(t["tool"] == "memory" and str(t["args"].get("action", "")).lower() == "remember" for t in trace):
            return
        try:
            self.memory.remember(user_input.strip(), "preferences", "user")
        except Exception:
            pass

    def _learn(self, user_input, trace, failures, turn_start):
        if not trace or failures:
            return
        tools_used = [t["tool"] for t in trace]
        if self.learn_skills and self.skills and len(trace) >= 2 and "undo_last" not in tools_used:
            try:
                from skills.skills import Skill
                words = [w for w in re.findall(r"[a-záéíóúñ]+", user_input.lower()) if len(w) > 3][:8]
                steps = " -> ".join(f"{t['tool']}({summarize_args(t['args'], 80)})" for t in trace[:6])
                name = "skill_" + hashlib.md5(" ".join(tools_used).encode()).hexdigest()[:8] + "_" + "_".join(words[:3])
                self.skills.save(Skill(name, f"For '{user_input[:80]}': {steps}", conditions=words))
            except Exception:
                pass
        if self.log_trajectories:
            try:
                out = AGENT_IA_USB_ROOT / "08_TRAINING" / "data" / "trajectories.jsonl"
                out.parent.mkdir(parents=True, exist_ok=True)
                turn = self.messages[turn_start:]
                record = {"time": datetime.now().isoformat(timespec="seconds"), "model": self.model_label,
                          "messages": [{"role": "user", "content": user_input}] + turn[1:]}
                with open(out, "a", encoding="utf-8") as f:
                    f.write(json.dumps(record, ensure_ascii=False) + "\n")
            except Exception:
                pass

    # ---------------------------------------------------------- local commands
    def _handle_command(self, message):
        """Built-in commands answered without the model. A leading '/' is optional."""
        body = message.strip()
        if body.startswith("/"):
            body = body[1:].strip()
        msg = body.lower().rstrip("!.")
        if msg in ("status", "system status", "estado"):
            return self._cmd_status()
        if msg in ("help", "ayuda", "?", "comandos", "commands"):
            return self._cmd_help()
        if msg in ("tools", "list tools", "herramientas"):
            return self._cmd_tools()
        if msg in ("plan", "plan actual"):
            return self._cmd_plan()
        if msg in ("undo", "deshacer", "deshaz"):
            ok, text = self.tools.journal.undo_last()
            return text
        if msg in ("libre", "autonomo", "autónomo", "modo libre", "libre on", "autonomo on"):
            return self._cmd_autonomous(True)
        if msg in ("libre off", "autonomo off", "cuidado", "normal autonomia"):
            return self._cmd_autonomous(False)
        if msg.startswith("modo ") or msg.startswith("mode "):
            return self.set_mode(msg.split(" ", 1)[1])
        if msg in ("red on", "red activada", "internet on", "network on"):
            return self.set_network(True, self.config)
        if msg in ("red off", "red apagada", "internet off", "network off"):
            return self.set_network(False, self.config)
        if msg in ("memory", "show memory", "memoria"):
            return self._cmd_memory()
        for prefix in ("remember ", "recuerda "):
            if msg.startswith(prefix):
                return self._cmd_remember(body[len(prefix):])
        for prefix in ("recall ", "busca en memoria "):
            if msg.startswith(prefix):
                return self._cmd_recall(body[len(prefix):])
        return None

    def _cmd_status(self):
        s = self.get_status()
        lines = [f"Sesión: {s['session_id']}", f"Modelo: {s['model']} (contexto de {s['context']} tokens)",
                 f"Modo: {s['mode']}", f"Internet: {'activado' if s['network'] != 'OFFLINE' else 'apagado'}",
                 f"Historial: {s['history_length']} mensajes", f"Herramientas: {s['tools_count']}"]
        if self.config and self.config.runtime_state:
            rs = self.config.runtime_state
            lines.append(f"Motor: {str(rs.get('runtime', '?')).upper()}{' + MTP' if rs.get('mtp') else ''}")
        hw_file = AGENT_IA_USB_ROOT / "06_CONFIG" / "hardware" / "detected.json"
        if hw_file.exists():
            try:
                hw = json.loads(hw_file.read_text(encoding="utf-8"))
                lines.append(f"CPU: {hw.get('cpu', {}).get('name', '?')}")
                lines.append(f"RAM: {hw.get('ram', {}).get('total_mb', '?')} MB")
                lines.append(f"GPU: {hw.get('gpu', {}).get('name', 'ninguna')}")
            except Exception:
                pass
        return "\n".join(lines)

    def _cmd_help(self):
        return (
            "Dime lo que necesitas, en español o en inglés. Por ejemplo:\n"
            "- Mueve todos los .txt de Descargas a D:\\Respaldo\\Textos\n"
            "- Ordena mi escritorio por tipo de archivo\n"
            "- Busca los PDF de más de 5 MB en Documentos\n"
            "- ¿Qué está ocupando espacio en C:?\n"
            "- (modo codigo) Hazme una página web con HTML, CSS y JS\n"
            "- (modo seguridad) Revisa la seguridad de este PC\n\n"
            "Comandos (con o sin '/'):\n"
            "  /modelos            ver los modelos instalados y cuál está activo\n"
            "  /usar <id|nº>       cambiar de modelo (conserva la conversación)\n"
            "  /escanear           volver a detectar modelos de 01_MODEL\n"
            "  /modo normal|codigo|seguridad    cambiar de modo\n"
            "  /red on|off         activar o apagar internet\n"
            "  /voz on|off|continuo   hablar con Agent IA USB (micro y respuesta hablada)\n"
            "  /voces  /micro      elegir voz TTS y micrófono\n"
            "  /libre              modo autónomo: deja de pedir confirmación esta sesión\n"
            "  /plan               ver la lista de tareas actual\n"
            "  /deshacer           deshacer el último cambio de archivos\n"
            "  /estado  /herramientas  /memoria\n"
            "  /recuerda <dato>    /busca en memoria <texto>\n"
            "  /ajustar            volver a medir la configuración más rápida\n"
            "  /nueva  /limpiar  /ayuda  /salir"
        )

    def _cmd_plan(self):
        plan = getattr(self.tools, "task_plan", None) or []
        if not plan:
            return "No hay ninguna lista de tareas activa ahora mismo."
        box = {"pending": " ", "doing": ">", "done": "x", "skipped": "-"}
        lines = ["Plan actual:"]
        for i, st in enumerate(plan, 1):
            lines.append(f"  {i}. [{box.get(st.get('status'), ' ')}] {st.get('text', '')}")
        return "\n".join(lines)

    def _cmd_autonomous(self, on):
        if self.policy:
            self.policy.auto_approve = bool(on)
        if on:
            return ("Modo autónomo ACTIVADO para esta sesión: no te pediré confirmación. Lo irreversible "
                    "(borrar, sobrescribir) sigue registrado y 'deshaz' lo revierte. Escribe '/libre off' para volver a preguntar.")
        return "Modo autónomo apagado: volveré a pedir confirmación antes de lo riesgoso."

    def _cmd_tools(self):
        tl = self.tools.list_tools()
        lines = [f"Herramientas ({len(tl)}):"]
        for t in tl:
            tags = [x for x, on in (("solo lectura", t.get("read_only")), ("destructiva", t.get("destructive")),
                                    ("red", t.get("network"))) if on]
            lines.append(f"  {t['name']}" + (f" [{', '.join(tags)}]" if tags else ""))
        return "\n".join(lines)

    def _cmd_remember(self, content):
        if self.memory:
            mid = self.memory.remember(content.strip(), "preferences", "user")
            return f"Anotado. Lo recordaré (id {mid})."
        return "La memoria no está disponible."

    def _cmd_recall(self, query):
        if self.memory:
            results = self.memory.recall(query.strip())
            if results:
                return "\n".join([f"Resultados para '{query}':"] + [f"  [{r['id']}] {r['content'][:100]}" for r in results])
            return f"No encontré nada en memoria sobre '{query}'."
        return "La memoria no está disponible."

    def _cmd_memory(self):
        if self.memory:
            stats = self.memory.get_stats()
            lines = [f"Memoria ({sum(stats.values())} entradas):"]
            for k, v in stats.items():
                lines.append(f"  {k:12s} {v}")
            return "\n".join(lines)
        return "La memoria no está disponible."

    # ----------------------------------------------------------------- session
    def reset(self):
        """Start a fresh conversation (keeps memory and skills)."""
        self.messages, self.history = [], []
        self._known_tokens = None

    def _record(self, user_input, response):
        self.history.append({"role": "user", "content": user_input})
        self.history.append({"role": "assistant", "content": response})
        if len(self.history) > self.max_history * 2:
            self.history = self.history[-self.max_history * 2:]
        self._save_session()

    def _save_session(self):
        session_dir = AGENT_IA_USB_ROOT / "03_AGENT" / "sessions"
        session_dir.mkdir(parents=True, exist_ok=True)
        state = {"session_id": self.session_id, "model": self.model_label, "mode": self.mode,
                 "cwd": str(getattr(self.tools, "cwd", "") or ""),
                 "history": self.history[-40:], "messages": self.messages[-80:],
                 "timestamp": datetime.now().isoformat()}
        try:
            (session_dir / f"{self.session_id}.json").write_text(
                json.dumps(state, indent=2, ensure_ascii=False), encoding="utf-8")
        except Exception:
            pass

    def load_session(self, state):
        """Restore a saved conversation (visible history + model context + mode) and keep writing to it."""
        if not isinstance(state, dict):
            return False
        self.history = state.get("history") or []
        self.messages = state.get("messages") or []
        if state.get("mode") in set(self.MODES.values()):
            self.mode = state["mode"]
        if state.get("session_id"):
            self.session_id = str(state["session_id"])
            if getattr(self.tools, "journal", None) is not None:
                self.tools.journal.session_id = self.session_id
        self._known_tokens = None
        return True

    def get_status(self):
        return {
            "session_id": self.session_id,
            "history_length": len(self.history),
            "tools_count": len(self.tools.list_tools()),
            "model": self.model_label or "unknown",
            "context": self.context_size,
            "mode": self.mode,
            "network": self.policy.network_mode if self.policy else "OFFLINE",
        }
