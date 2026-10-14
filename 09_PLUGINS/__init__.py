"""
Agent IA USB Plugin Architecture
Plugins extend Agent IA USB with new tools, skills, or capabilities.
MCP-compatible: each plugin declares structured metadata.
"""
import json
from pathlib import Path

PLUGIN_DIR = Path(__file__).resolve().parent.parent.parent / "09_PLUGINS"


class Plugin:
    """Base class for all Agent IA USB plugins."""
    name = "base"
    version = "1.0.0"
    description = "Base plugin"
    author = "Agent IA USB"
    permissions = []
    risk_level = "safe"

    def __init__(self, config=None):
        self.config = config
        self.enabled = True

    def on_load(self):
        pass

    def on_unload(self):
        pass

    def get_tools(self):
        return []

    def get_metadata(self):
        return {
            "name": self.name,
            "version": self.version,
            "description": self.description,
            "author": self.author,
            "permissions": self.permissions,
            "risk_level": self.risk_level,
            "enabled": self.enabled,
        }


class PluginManager:
    """Discovers and manages plugins."""

    def __init__(self, config=None):
        self.config = config
        self.plugins = {}
        self._discover()

    def _discover(self):
        PLUGIN_DIR.mkdir(parents=True, exist_ok=True)
        registry_file = PLUGIN_DIR / "registry.json"
        registry = {}
        if registry_file.exists():
            try:
                registry = json.loads(registry_file.read_text(encoding="utf-8"))
            except:
                pass

        for d in PLUGIN_DIR.iterdir():
            if d.is_dir() and not d.name.startswith("_"):
                manifest = d / "manifest.json"
                if manifest.exists():
                    try:
                        meta = json.loads(manifest.read_text(encoding="utf-8"))
                        name = meta.get("name", d.name)
                        enabled = registry.get(name, {}).get("enabled", True)
                        self.plugins[name] = {"meta": meta, "enabled": enabled, "path": str(d)}
                    except:
                        pass

    def list_plugins(self):
        return [{**p["meta"], "enabled": p["enabled"]} for p in self.plugins.values()]

    def enable(self, name):
        if name in self.plugins:
            self.plugins[name]["enabled"] = True
            self._save_registry()
            return True
        return False

    def disable(self, name):
        if name in self.plugins:
            self.plugins[name]["enabled"] = False
            self._save_registry()
            return True
        return False

    def _save_registry(self):
        registry = {n: {"enabled": p["enabled"]} for n, p in self.plugins.items()}
        (PLUGIN_DIR / "registry.json").write_text(json.dumps(registry, indent=2), encoding="utf-8")
