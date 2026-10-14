# Contributing to Agent IA USB

Thanks for wanting to help! Agent IA USB is designed so that **anyone can add their own
models, tools, skills and plugins** without touching the core. This guide shows
you the extension points and how to test your changes.

## Table of contents

1. [Development setup](#development-setup)
2. [Adding a model](#adding-a-model)
3. [Adding a tool](#adding-a-tool)
4. [Adding a skill](#adding-a-skill)
5. [Adding a plugin](#adding-a-plugin)
6. [Testing](#testing)
7. [Style](#style)
8. [Submitting a pull request](#submitting-a-pull-request)

## Development setup

You do not need the USB layout to develop. Clone the repo and run from source:

```bash
git clone <your-fork-url> agent-ia-usb
cd agent-ia-usb
python 03_AGENT/agent.py --agent-ia-usb-root .
```

To actually talk to a model, put a GGUF in `01_MODEL/` (see below) or run
`SETUP-USB.bat` once to download the defaults. On Windows the bundled
`02_RUNTIME/python/python.exe` is used automatically; on other platforms use
your own Python 3.10+.

## Adding a model

No code changes needed.

1. Drop a `.gguf` into `01_MODEL/` (flat or in a subfolder).
2. Agent IA USB auto-detects it on start (or after `/escanear`): id, label, size,
   context length and architecture are read from the GGUF header.
3. Companion files are paired by name:
   - vision projector → `01_MODEL/vision/mmproj-<base>-*.gguf` (or anywhere),
   - MTP draft variant → a file with `-mtp-` in its name.
4. Optional sidecar `01_MODEL/<name>.json` (or `<file>.json`) can
   override name, context, capabilities or minimum RAM:

```json
{
  "name": "My Model",
  "context": 32768,
  "min_ram_gb": 16,
  "capabilities": { "tools": true, "vision": true }
}
```

To change the download manifest for `SETUP-USB.bat`, edit the `tiers`
section of `06_CONFIG/models.json` (each entry needs an `hf_repo`,
`hf_model` and `size_bytes`).

## Adding a tool

Tools live in `03_AGENT/core/tools.py`. Register a handler with a JSON schema
(the model reads it) and a policy flag. Example:

```python
# inside _register_all(self):
self.register("echo", self._echo,
    "Repeat the text back (example tool).",
    {"text": prop(S, "Text to repeat.")},
    required=["text"], category="system", read_only=True)

# add a handler method:
def _echo(self, params):
    text = str(params.get("text", "")).strip()
    return ok(text) if text else fail("Empty text.")
```

The handler receives a `params` dict and must return `ok(output)` or
`fail(error)`. Registration flags:

- `read_only=True` — never asks and is never recorded in the undo journal.
- `destructive=True` — always asks for confirmation.
- `network=True` — marks it as needing internet.

Type helpers: `S` (string), `B` (boolean), `I` (integer),
`N` (number), `A` (array). `prop(type, description, **kw)` builds
a property (extra keys like `enum` are passed through).

If your tool changes files, use the journal helpers (`self.journal.begin/record/commit`)
so `/deshacer` can reverse it, and validate paths with `core.paths.check_write`.

## Adding a skill

Skills are procedures Agent IA USB learns automatically from successful multi-step tasks
and saves as JSON in `03_AGENT/skills/`. To add one by hand, create:

```json
{
  "name": "skill_backup_photos",
  "procedure": "For 'back up my photos': find_files(...) -> copy_files(...)",
  "conditions": ["fotos", "fotografias", "backup", "respaldar"],
  "success_count": 0,
  "version": 1
}
```

`conditions` are the trigger words that make Agent IA USB surface this procedure on
similar requests.

## Adding a plugin

Plugins declare structured, MCP-compatible metadata in `09_PLUGINS/`. Add a
folder with a `manifest.json`:

```json
{
  "name": "my-plugin",
  "version": "0.1.0",
  "description": "What it does",
  "author": "you",
  "capabilities": ["screenshot", "mouse"],
  "requirements": ["pyautogui"]
}
```

The bundled `voice` plugin (whisper.cpp + Piper, fully implemented) is a good
reference for adding real capabilities.

## Testing

Run the suite before submitting:

```bash
python 03_AGENT/eval_suite.py         # internal tests, no model needed (161)
python 03_AGENT/eval_agent.py         # 16 real tasks, verified on disk
```

## Style

- User-facing agent replies are in Spanish; code, schemas and comments are in English.
- Tool descriptions are read by the model — be precise about arguments and behavior.
- Keep the system prompt byte-stable (see `03_AGENT/core/prompts.py`); per-turn
  facts belong in the turn context, not the cached prompt.

## Submitting a pull request

1. Fork the repo and create a branch.
2. Make a focused change, add/update tests where it makes sense.
3. Run `eval_suite.py` and `eval_agent.py`.
4. Open a PR with a clear description of what and why.
