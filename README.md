# Agent IA USB — Portable AI Agent on a USB stick

A fully offline, J.A.R.V.I.S.-style personal AI agent that runs from a USB stick on any
Windows PC — and actually **acts** on the machine: organize files, write and run code,
audit security, answer questions about your documents, and more. No internet, no install,
no GPU required.

Speak to it in Spanish or English; it replies in Spanish (configurable). **Talk to it out
loud** — `/voz on` enables fully offline voice (whisper.cpp speech-to-text + Piper
text-to-speech); Agent IA USB starts answering out loud while it's still writing.

> Demo GIF: `docs/demo.gif` *(record one with [ScreenToGif](https://www.screentogif.com/):
> START-AGENT-IA-USB.bat → `/voz on` → "Agent IA USB, mueve los PDF de Descargas a Documentos")*

## Highlights

- **100% offline** — models run locally with llama.cpp (CPU and Vulkan GPU).
- **Voice, offline** — dictate in Spanish, hear the answer: whisper.cpp + Piper, ~250 MB,
  installed by SETUP-USB.bat in one question.
- **Truly portable** — everything lives on the USB: a bundled Python 3.12, the llama.cpp
  runtimes and the models. Nothing is installed on the host PC.
- **It acts, not just chats** — **36 tools** for files, code, system, security, documents,
  vision, memory and web.
- **Bring your own model** — drop any GGUF into `01_MODEL/` and it is auto-detected
  (vision projector and MTP variant paired by name, context read from the file). No code changes.
- **Self-tuning** — the first run on a PC measures the fastest setup (model, CPU vs GPU,
  multi-token prediction) once, then restores it in seconds.
- **Safe by default** — asks before risky actions, blocks the irreversible outright
  (disk formatting, drive deletion), never touches Windows/agent-ia-usb's own files, and every
  file change is reversible with `undo`.
- **Learns and remembers** — persistent memory, reusable skills, and a fine-tuning pipeline
  to train the model on your own conversations.

## Quick start

1. **Format** the USB as exFAT or NTFS (FAT32 cannot store files over 4 GB).
2. **Run `SETUP-USB.bat` once** (needs internet): downloads portable Python, the
   llama.cpp runtimes (CPU + Vulkan) and the default models (~13 GB). Re-run it if
   interrupted — downloads resume.
3. **Run `START-AGENT-IA-USB.bat`** every day. On a new PC it self-tunes once (a few minutes);
   after that it starts in seconds.
4. Run `STOP-AGENT-IA-USB.bat` before unplugging the USB.

## Requirements

- Windows 10/11 x64.
- A USB stick (exFAT/NTFS) with ~20 GB free.
- Host PC with at least 8 GB RAM (Agent IA USB automatically picks a smaller model on low-RAM PCs;
  a dedicated NVIDIA/AMD GPU is used automatically when available).

## What Agent IA USB can do (36 tools)

- **Files** — list, find (by name, size or date), search inside text, read, inspect,
  move/copy/rename/delete hundreds of files at once (with filters), create, edit, and count
  files across a whole disk.
- **Code** — create multi-file projects, run Python / PowerShell / compiled code, open
  apps and websites, and fix errors.
- **System** — PowerShell, disk usage, clipboard, date/time, system info.
- **Security** — read-only audit and scan (Defender, firewall, startup items, ports,
  unsigned processes), quarantine, remove persistence, harden Windows (with permission;
  everything reversible).
- **Documents (RAG)** — index a folder and ask questions ("when does the contract expire?"),
  citing the source file. Reads txt, md, docx, csv and code — all local.
- **Vision** — look at an image or your screen.
- **Memory** — remembers facts and preferences across sessions.
- **Web** — search and fetch pages (off by default; enable per-session with `red on`).
- **Plans & undo** — visible task lists for long jobs; every file change is reversible.

## Bring your own model

Agent IA USB is not tied to any model. Drop a GGUF into `01_MODEL/` (flat or in a subfolder)
and it appears on the next start:

- the vision projector and the MTP variant are paired automatically by name;
- context length and architecture are read from the GGUF header;
- an optional sidecar `01_MODEL/<name>.json` can override name, context, capabilities
  or minimum RAM.

Only GGUF files run. Hugging Face models in safetensors (a folder with `config.json` and
`*.safetensors`) can be converted: put the folder in `01_MODEL/` and run `CONVERT-MODEL.bat`
(it produces a quantized GGUF, Q4_K_M by default, and moves the original to `01_MODEL/_originals/`).
PyTorch `.bin`/`.pt` also converts but downloads PyTorch (~2 GB). ONNX cannot be converted.

Commands: `/modelos` (list installed), `/usar <n|name>` (switch model without
losing the conversation), `/escanear` (rescan), `/ajustar` (re-measure the
fastest setup).

## Talk to Agent IA USB (voice, 100% offline)

Install once (SETUP-USB.bat asks; ~250 MB): whisper.cpp + `ggml-base` for speech-to-text,
Piper + a Spanish voice for text-to-speech, `sounddevice` for the microphone.

```
/voz on          push-to-talk: Enter to start talking, Enter to stop
/voz continuo    Agent IA USB listens after each answer (Ctrl+C back to keyboard)
/voz off         back to keyboard only
/voces           list/pick the TTS voice
/micro           list/pick the microphone
```

Agent IA USB speaks the reply **sentence by sentence while the model is still generating**,
so the answer starts almost immediately. Any typed text still works with voice on —
the microphone is just another input.

## Commands

```
/modelos            list installed models and the active one
/usar <n|name>      switch model (keeps the conversation)
/escanear           rescan 01_MODEL/ for new models
/voz on|off|continuo    voice input and spoken replies
/voces  /micro       pick TTS voice and microphone
/modo normal|codigo|seguridad|pentest    change mode
/red on|off         internet for this session
/libre              autonomous mode (stop asking for confirmation this session)
/plan               show the current task list
/deshacer           undo the last file change
/estado             status (model, mode, engine, hardware)
/herramientas       list tools
/memoria            memory stats
/recuerda <dato>    remember something
/busca en memoria <texto>   search memory
/ajustar            re-measure the fastest setup for this PC
/historial  /habilidades  /nueva  /limpiar  /ayuda  /salir
```

## Modes

- `/modo normal` — general use.
- `/modo codigo` — build and test complete programs (web, Python...).
- `/modo seguridad` — defensive, read-only audit of this PC.
- `/modo pentest` — assisted pentesting of targets you own or are authorized to test.
- `/red on` — enable web search/fetch for this session (off by default).

## Safety

- **Runs freely:** reading, searching, copying, moving/renaming without overwriting, and
  creating new files.
- **Asks before [s/n/t]:** deleting, overwriting, state-changing PowerShell, running Python,
  writing to the clipboard, and web searches.
- **Never:** formats/partitions disks, touches boot config, security settings, C:\Windows,
  Program Files, or Agent IA USB's own files.
- Deleted files go to the Recycle Bin (or `<drive>\.agent_ia_usb_trash` on USB); `undo`
  restores them. File and web page contents are treated as data, never as instructions.

## Project structure

```
01_MODEL/        GGUF models + vision/ projectors (drop any GGUF here)
02_RUNTIME/      portable Python 3.12 + llama.cpp + whisper.cpp + Piper (voice)
03_AGENT/        the agent (core/, orchestrator/, memory/, skills/, sessions/)
04_WORKSPACE/    scratch space for the agent
05_KNOWLEDGE/    indexed documents (RAG)
06_CONFIG/       agent.json, models.json, hardware profiles, policy
07_BACKUPS/      undo journal, quarantine, backups
08_TRAINING/     fine-tuning pipeline (Colab/Kaggle)
09_PLUGINS/      plugin system (voice: whisper.cpp + Piper, implemented)
docs/            publishing guide + demo assets
evaluation/      benchmark suite (~1000 conversations)
```

## Extending Agent IA USB

- **Plugins** — add a folder with a `manifest.json` under `09_PLUGINS/` to
  declare new capabilities (MCP-compatible metadata).
- **Tools** — register a handler in `03_AGENT/core/tools.py` with a JSON schema and
  policy flags; Agent IA USB discovers it automatically.
- **Skills** — multi-step procedures Agent IA USB learns from successful tasks and reuses on similar
  requests (`03_AGENT/skills/`).
- **Memory** — persistent, typed memory (preferences, episodic, semantic, project).
- **Fine-tuning** — `08_TRAINING/` generates training data from your real conversations
  and fine-tunes the model for free in Google Colab/Kaggle; the result is adopted only if it
  beats the original.

## How Agent IA USB compares

| | Agent IA USB | LM Studio | Jan / GPT4All |
|---|---|---|---|
| Runs from a USB, nothing installed | ✅ | ❌ | ❌ |
| Acts on your PC (files, code, security) | ✅ 36 tools | ❌ chat-centric | partial |
| Offline voice in/out | ✅ whisper.cpp + Piper | ❌ | ❌ |
| Safety: undo journal, asks first, hard blocks | ✅ | n/a | n/a |
| Bring any GGUF model | ✅ auto-detected | ✅ | ✅ |
| GPU required | ❌ CPU-only fine | preferred | preferred |

## Testing

```
python 03_AGENT/eval_suite.py         # internal tests, no model needed (161)
python 03_AGENT/eval_agent.py         # 16 real tasks, verified on disk
python evaluation/benchmark.py run    # ~1000-conversation benchmark (sample by default)
```

## Contributing

Pull requests are welcome. See [CONTRIBUTING.md](CONTRIBUTING.md) for how to add your own
models, tools, skills and plugins.

## License

[MIT](LICENSE) — © 2026 Agent IA USB contributors.
