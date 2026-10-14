"""
Agent IA USB Configuration Management
Handles loading, saving, and merging configurations.
All paths are relative to Agent IA USB root (auto-detected).
"""
import json
import os
import sys
from pathlib import Path
from datetime import datetime
from typing import Any, Optional

# Auto-detect Agent IA USB root from this file's location
AGENT_IA_USB_ROOT = Path(__file__).resolve().parent.parent.parent
AGENT_DIR = AGENT_IA_USB_ROOT / "03_AGENT"
MODEL_DIR = AGENT_IA_USB_ROOT / "01_MODEL"
RUNTIME_DIR = AGENT_IA_USB_ROOT / "02_RUNTIME"
WORKSPACE_DIR = AGENT_IA_USB_ROOT / "04_WORKSPACE"
KNOWLEDGE_DIR = AGENT_IA_USB_ROOT / "05_KNOWLEDGE"
CONFIG_DIR = AGENT_IA_USB_ROOT / "06_CONFIG"
BACKUP_DIR = AGENT_IA_USB_ROOT / "07_BACKUPS"

# Server defaults
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8080

class AgentIaUsbConfig:
    """Main configuration manager for Agent IA USB."""
    
    def __init__(self, agent_ia_usb_root: Optional[Path] = None):
        self.root = Path(agent_ia_usb_root) if agent_ia_usb_root else AGENT_IA_USB_ROOT
        self._config_cache = {}
        self._load_all()
    
    def _load_all(self):
        """Load all configuration layers."""
        self.hardware = self._load_json(self.root / "06_CONFIG" / "hardware" / "detected.json", {})
        self.performance = self._load_json(self.root / "06_CONFIG" / "performance.json", {})
        self.tools_config = self._load_json(self.root / "06_CONFIG" / "tools" / "tools.json", {})
        self.permissions = self._load_json(self.root / "06_CONFIG" / "permissions" / "policy.json", {})
        self.network = self._load_json(self.root / "06_CONFIG" / "network" / "network.json", {})
        self.agent_config = self._load_json(self.root / "06_CONFIG" / "agent.json", {})
        self.session = self._load_json(self.root / "03_AGENT" / "sessions" / "current_session.json", {})
        self.models = self._load_json(self.root / "06_CONFIG" / "models.json", {})
        # Written by core/launcher.py when it starts llama-server
        self.runtime_state = self._load_json(self.root / "06_CONFIG" / "runtime_state.json", {})
        self._registry = None

        # Set defaults for agent config
        defaults = {
            "assistant_name": "Agent IA USB",
            "user_title": "",
            "language": "es",
            "model_tier": "auto",
            "runtime": "auto",
            "max_steps": 30,
            "max_reply_tokens": 6144,
            "web_mode": "OFFLINE",
            "vision_enabled": True,
            "learn_skills": True,
            "log_trajectories": True,
        }
        for key, value in defaults.items():
            self.agent_config.setdefault(key, value)
    
    def _load_json(self, path: Path, default: Any = None) -> Any:
        """Load a JSON file, returning default if not found or corrupt."""
        try:
            if path.exists():
                with open(path, 'r', encoding='utf-8') as f:
                    return json.load(f)
        except (json.JSONDecodeError, IOError):
            pass
        return default if default is not None else {}
    
    def save_json(self, path: Path, data: Any):
        """Save data to a JSON file."""
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=2, default=str)
    
    def save_hardware(self, data: dict):
        """Save hardware detection results."""
        self.hardware = data
        self.save_json(self.root / "06_CONFIG" / "hardware" / "detected.json", data)
    
    def save_performance(self, data: dict):
        """Save performance profile."""
        self.performance = data
        self.save_json(self.root / "06_CONFIG" / "performance.json", data)
    
    def save_agent_config(self):
        """Save current agent configuration."""
        self.save_json(self.root / "06_CONFIG" / "agent.json", self.agent_config)
    
    def save_session(self, data: dict):
        """Save current session state."""
        self.session = data
        self.save_json(self.root / "03_AGENT" / "sessions" / "current_session.json", data)
    
    @property
    def server_url(self) -> str:
        server = self.models.get("server", {})
        host = server.get("host", DEFAULT_HOST)
        port = server.get("port", DEFAULT_PORT)
        return f"http://{host}:{port}"

    @property
    def sampling(self) -> dict:
        s = {k: v for k, v in self.models.get("sampling", {}).items() if not k.startswith("_")}
        s.update(self.tier_config().get("sampling", {}))  # a model may need its own sampling
        return s

    # ------------------------------------------------------------- model registry
    def _aliases(self) -> dict:
        """alias -> model id. Explicit 'aliases' in models.json, else derived from the old 'tiers'."""
        from core.models import _slug
        aliases = dict(self.models.get("aliases", {}))
        for name, t in self.models.get("tiers", {}).items():
            aliases.setdefault(name, _slug(t.get("model_file", name)))
        return {k: str(v).lower() for k, v in aliases.items()}

    @property
    def model_registry(self):
        if self._registry is None:
            from core.models import ModelRegistry
            self._registry = ModelRegistry(self.root / "01_MODEL", self.root / "06_CONFIG", self._aliases())
            self._registry.discover()
        return self._registry

    def list_models(self):
        return self.model_registry.discover()

    def resolve_model_id(self, name: Optional[str]) -> Optional[str]:
        """Model id for an id/alias/index/prefix, or None."""
        info = self.model_registry.get(name)
        return info.id if info else None

    def tier_config(self, tier: Optional[str] = None) -> dict:
        """Settings of a model (defaults to the running one). Any GGUF in 01_MODEL is a
        valid 'tier' here; 'smart'/'fast' are aliases. Falls back to models.json tiers."""
        tier = tier or self.active_tier
        try:
            info = self.model_registry.get(tier)
        except Exception:
            info = None
        if info:
            cfg = info.as_tier()
            override = (self.models.get("overrides") or {}).get(info.id, {})
            cfg.update({k: v for k, v in override.items() if v is not None})
            return cfg
        return dict(self.models.get("tiers", {}).get(tier, {}))

    @property
    def active_tier(self) -> str:
        """Id of the running model, else the configured one, else the first available."""
        tier = self.runtime_state.get("tier")
        if tier:
            return tier
        configured = self.agent_config.get("model") or self.agent_config.get("model_tier", "auto")
        if configured and configured != "auto":
            rid = self.resolve_model_id(configured)
            if rid:
                return rid
        try:
            models = self.list_models()
            if models:
                return models[0].id
        except Exception:
            pass
        return "smart"

    @property
    def context_size(self) -> int:
        return int(self.runtime_state.get("context") or self.tier_config().get("context", 8192))

    @property
    def model_path(self) -> str:
        model = self.tier_config().get("model_file", "")
        return str(self.root / "01_MODEL" / model) if model else ""

    @property
    def mmproj_path(self) -> str:
        mmproj = self.tier_config().get("mmproj_file", "")
        return str(self.root / "01_MODEL" / mmproj) if mmproj else ""

    @property
    def server_exe(self) -> str:
        return str(self.root / "02_RUNTIME" / "llama.cpp" / "llama-server.exe")
    
    def get_memory_path(self, memory_type: str = "working") -> Path:
        return self.root / "03_AGENT" / "memory" / f"{memory_type}.json"


# Singleton
_config_instance = None

def get_config() -> AgentIaUsbConfig:
    global _config_instance
    if _config_instance is None:
        _config_instance = AgentIaUsbConfig()
    return _config_instance
