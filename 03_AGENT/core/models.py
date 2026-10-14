"""
Agent IA USB Model Registry - the model is not wired into the code.

Drop any GGUF into 01_MODEL/ (flat or in a subfolder) and it shows up here.
For each base model the registry auto-detects:
  - its vision projector  (01_MODEL/vision/mmproj-<base>-*.gguf)
  - its MTP draft variant (<base>-MTP-*.gguf, used for speculative decoding)
  - its context length and architecture (read from the GGUF header)
  - a short id ("qwen3.5-9b") and a label ("Qwen3.5-9B")
An optional sidecar 01_MODEL/<file>.json or 01_MODEL/<id>.json overrides any of
these (name, context, capabilities, min_ram_gb) - never required.

Everything is cached in 06_CONFIG/models_cache.json (keyed by path + mtime) so
GGUF headers are parsed only once per PC.
"""
import json
import re
import struct
from pathlib import Path

AGENT_IA_USB_ROOT = Path(__file__).resolve().parent.parent.parent

# Practical default context. Modern models advertise a huge trained max (e.g. 262144)
# that no local CPU can hold in RAM; a sidecar override can raise it per model/PC.
DEFAULT_CONTEXT = 32768

# Quant suffixes stripped from a filename to get the model's base id
_QUANT = re.compile(r"[-_.](q\d[\w]*|iq\d[\w]*|f16|f32|bf16|fp16|mxfp\d+|q8_0|q4_k_m|q5_k_m|q6_k|k_[ms])$", re.I)
_MMPROJ_HINT = re.compile(r"mmproj|mm-proj|vision|clip|projector", re.I)
_MTP_HINT = re.compile(r"[-_.]mtp[-_.]", re.I)


def _slug(name):
    """A short, stable id from a model filename (no extension, no quant suffix)."""
    stem = Path(name).stem
    stem = _MTP_HINT.sub("-", stem)
    prev = None
    while prev != stem:                       # strip stacked suffixes: "...-Q4_K_M"
        prev = stem
        stem = _QUANT.sub("", stem)
    return re.sub(r"[^a-z0-9.]+", "-", stem.lower()).strip("-") or Path(name).stem.lower()


def _label(name):
    stem = Path(name).stem
    stem = _MTP_HINT.sub("-", stem)
    prev = None
    while prev != stem:
        prev = stem
        stem = _QUANT.sub("", stem)
    return stem.strip("-_.") or Path(name).stem


# ----------------------------------------------------------------- GGUF header
_GGUF_SKIP = {0: 1, 1: 1, 2: 2, 3: 2, 4: 4, 5: 4, 6: 4, 7: 1, 10: 8, 11: 8, 12: 8}  # value_type -> bytes


def _read_gguf_meta(path):
    """Read {'context': int|None, 'arch': str} from a GGUF file's header, or {} on any problem.
    Parses only the key/value metadata and skips tensor info; large arrays (the vocab) are seeked over."""
    try:
        with open(path, "rb") as f:
            if f.read(4) != b"GGUF":
                return {}
            version = struct.unpack("<I", f.read(4))[0]
            if version < 2:
                return {}
            struct.unpack("<Q", f.read(8))  # tensor_count
            kv_count = struct.unpack("<Q", f.read(8))[0]
            meta = {}
            arch = None
            for _ in range(min(kv_count, 5000)):
                klen = struct.unpack("<Q", f.read(8))[0]
                key = f.read(klen).decode("utf-8", "replace")
                vtype = struct.unpack("<I", f.read(4))[0]
                val = _read_gguf_value(f, vtype)
                if key == "general.architecture":
                    arch = val
                elif key.endswith(".context_length"):
                    meta["context"] = int(val) if isinstance(val, (int, float)) else None
                elif key == "general.name":
                    meta["name"] = val
            meta["arch"] = arch
            return meta
    except Exception:
        return {}


def _read_gguf_value(f, vtype):
    if vtype == 8:  # string
        n = struct.unpack("<Q", f.read(8))[0]
        return f.read(n).decode("utf-8", "replace")
    if vtype == 9:  # array
        elem_type = struct.unpack("<I", f.read(4))[0]
        n = struct.unpack("<Q", f.read(8))[0]
        if elem_type == 8:
            for _ in range(n):
                slen = struct.unpack("<Q", f.read(8))[0]
                f.seek(slen, 1)
        else:
            f.seek(_GGUF_SKIP.get(elem_type, 0) * n, 1)
        return None
    if vtype in (4, 5):  # uint32 / int32
        return struct.unpack("<i" if vtype == 5 else "<I", f.read(4))[0]
    if vtype in (10, 11):  # uint64 / int64
        return struct.unpack("<q" if vtype == 11 else "<Q", f.read(8))[0]
    if vtype in (2, 3):  # uint16 / int16
        return struct.unpack("<h" if vtype == 3 else "<H", f.read(2))[0]
    if vtype in (0, 1, 7):  # uint8 / int8 / bool
        return struct.unpack("<b" if vtype == 1 else "<B", f.read(1))[0]
    if vtype == 6:  # float32
        return struct.unpack("<f", f.read(4))[0]
    if vtype == 12:  # float64
        return struct.unpack("<d", f.read(8))[0]
    return None


class ModelInfo:
    __slots__ = ("id", "label", "filename", "path", "mmproj", "mmproj_bytes", "mtp", "mtp_bytes",
                 "context", "vision", "tools", "size_bytes", "arch", "min_ram_gb", "source")

    def __init__(self, **kw):
        for k in self.__slots__:
            setattr(self, k, kw.get(k))

    def to_dict(self):
        return {k: getattr(self, k) for k in self.__slots__}

    # The shape launcher.py / config.py expect (a "tier" dict), so the rest of the
    # code treats a discovered model exactly like one of the old fixed tiers.
    def as_tier(self):
        return {"label": self.label, "model_file": self.filename, "mmproj_file": self.mmproj,
                "mtp_file": self.mtp, "context": self.context or 8192,
                "size_bytes": self.size_bytes or 0, "mmproj_size_bytes": self.mmproj_bytes or 0,
                "mtp_size_bytes": self.mtp_bytes or 0, "min_ram_gb": self.min_ram_gb or 0,
                "capabilities": {"tools": self.tools, "vision": self.vision}}

    def ram_need_gb(self):
        """Rough RAM (GB) to run this model: weights + vision + KV/overhead."""
        return (self.size_bytes or 0) / 1024 ** 3 + (self.mmproj_bytes or 0) / 1024 ** 3 + 2.0


class ModelRegistry:
    def __init__(self, model_dir=None, config_dir=None, aliases=None):
        self.model_dir = Path(model_dir) if model_dir else AGENT_IA_USB_ROOT / "01_MODEL"
        self.cache_file = (Path(config_dir) if config_dir else AGENT_IA_USB_ROOT / "06_CONFIG") / "models_cache.json"
        self.aliases = dict(aliases or {})      # e.g. {"smart": "qwen3.5-9b", "fast": "qwen3.5-4b"}
        self.models = {}                        # id -> ModelInfo
        self._cache = self._load_cache()

    # ---------------------------------------------------------------- caching
    def _load_cache(self):
        try:
            return json.loads(self.cache_file.read_text(encoding="utf-8"))
        except Exception:
            return {}

    def _save_cache(self):
        try:
            self.cache_file.parent.mkdir(parents=True, exist_ok=True)
            self.cache_file.write_text(json.dumps(self._cache, indent=2), encoding="utf-8")
        except Exception:
            pass

    def _meta_for(self, path):
        """GGUF metadata for a file, cached by path+size+mtime."""
        try:
            st = path.stat()
            key = str(path)
            token = f"{st.st_size}:{int(st.st_mtime)}"
            hit = self._cache.get(key)
            if hit and hit.get("token") == token:
                return hit["meta"]
            meta = _read_gguf_meta(path)
            self._cache[key] = {"token": token, "meta": meta}
            return meta
        except Exception:
            return {}

    # -------------------------------------------------------------- discovery
    def discover(self, refresh=False):
        """Scan the models folder. Returns the list of ModelInfo (bases only)."""
        if self.models and not refresh:
            return self.list()
        self.models = {}
        self.unconverted = []
        if not self.model_dir.is_dir():
            return []
        ggufs = [p for p in self.model_dir.rglob("*.gguf") if p.is_file()]
        mmprojs = [p for p in ggufs if _MMPROJ_HINT.search(p.name)]
        bases = [p for p in ggufs if p not in mmprojs and not _MTP_HINT.search(p.name)]
        mtps = [p for p in ggufs if _MTP_HINT.search(p.name) and p not in mmprojs]
        dirty = False
        for path in sorted(bases):
            mid = _slug(path.name)
            if mid in self.models:                 # same id twice: keep the bigger file
                if path.stat().st_size <= (self.model_dir / self.models[mid].filename).stat().st_size:
                    continue
            meta = self._meta_for(path)
            dirty = True
            mm = self._pair(path, mmprojs)
            mtp = self._pair(path, mtps)
            info = ModelInfo(
                id=mid, label=_label(path.name),
                filename=str(path.relative_to(self.model_dir)).replace("/", "\\"),
                path=str(path),
                mmproj=(str(mm.relative_to(self.model_dir)).replace("/", "\\") if mm else None),
                mmproj_bytes=(mm.stat().st_size if mm else 0),
                mtp=(str(mtp.relative_to(self.model_dir)).replace("/", "\\") if mtp else None),
                mtp_bytes=(mtp.stat().st_size if mtp else 0),
                context=min(meta.get("context") or DEFAULT_CONTEXT, DEFAULT_CONTEXT), arch=meta.get("arch"),
                vision=bool(mm), tools=None,
                size_bytes=path.stat().st_size, min_ram_gb=0, source="auto")
            self._apply_override(info, path)
            self.models[mid] = info
        self.unconverted = self._find_unconverted()
        if dirty:
            self._save_cache()
        return self.list()

    def _find_unconverted(self):
        """Detect HuggingFace model folders (safetensors/pt/bin) that need conversion."""
        found = []
        for entry in sorted(self.model_dir.iterdir()):
            if not entry.is_dir() or entry.name.startswith(("_", ".")) or entry.name == "vision":
                continue
            config = entry / "config.json"
            if not config.exists():
                continue
            safetensors = list(entry.glob("*.safetensors"))
            pytorch = list(entry.glob("*.bin")) + list(entry.glob("*.pt"))
            onnx = list(entry.glob("*.onnx"))
            if safetensors:
                fmt = "safetensors"
                files = safetensors
            elif pytorch:
                fmt = "pytorch"
                files = pytorch
            elif onnx:
                fmt = "onnx"
                files = onnx
            else:
                continue
            total = sum(f.stat().st_size for f in files)
            try:
                cfg = json.loads(config.read_text(encoding="utf-8"))
                name = cfg.get("_name_or_path", "") or cfg.get("model_type", "") or entry.name
            except Exception:
                name = entry.name
            found.append({"dir": str(entry), "name": name, "format": fmt,
                          "size_bytes": total, "convertible": fmt != "onnx"})
        return found

    def _pair(self, base_path, candidates):
        """Best matching companion (mmproj or MTP) for a base model, by shared id."""
        bid = _slug(base_path.name)
        matches = [c for c in candidates if _slug(c.name) == bid]
        if not matches:
            # looser match: companion filename contains the base id with separators removed
            key = re.sub(r"[^a-z0-9]", "", bid)
            matches = [c for c in candidates if key and key in re.sub(r"[^a-z0-9]", "", c.name.lower())]
        return sorted(matches, key=lambda c: c.stat().st_size, reverse=True)[0] if matches else None

    def _apply_override(self, info, path):
        for cand in (path.with_suffix(".json"), self.model_dir / f"{info.id}.json"):
            try:
                if not cand.is_file():
                    continue
                o = json.loads(cand.read_text(encoding="utf-8"))
                info.label = o.get("name") or o.get("label") or info.label
                if o.get("context"):
                    info.context = int(o["context"])
                if o.get("min_ram_gb") is not None:
                    info.min_ram_gb = float(o["min_ram_gb"])
                caps = o.get("capabilities") or {}
                if "tools" in caps:
                    info.tools = bool(caps["tools"])
                if "vision" in caps:
                    info.vision = bool(caps["vision"])
                info.source = "override"
                return
            except Exception:
                pass

    # -------------------------------------------------------------- accessors
    def list(self):
        return sorted(self.models.values(), key=lambda m: (-(m.size_bytes or 0), m.id))

    def get(self, name):
        """Look up by id, alias (smart/fast), 1-based index or unique prefix.
        Alias targets match any installed variant of the tier (the 'qwen3.5-9b'
        alias answers to 'huihui-qwen3.5-9bq5' too): any quant on disk is fine."""
        if not name:
            return None
        self.discover()
        name = str(name).strip().lower()
        if name in self.models:
            return self.models[name]
        target = self.aliases.get(name, name)
        if target in self.models:
            return self.models[target]
        if name.isdigit():
            lst = self.list()
            i = int(name) - 1
            if 0 <= i < len(lst):
                return lst[i]
        hits = [m for m in self.models.values() if m.id.startswith(target)]
        if len(hits) == 1:
            return hits[0]
        key = re.sub(r"[^a-z0-9]", "", target)
        hits = [m for m in self.models.values() if key and key in re.sub(r"[^a-z0-9]", "", m.id)]
        return hits[0] if len(hits) == 1 else None

    def pending_conversions(self):
        """HuggingFace model folders in 01_MODEL/ that are not GGUF yet."""
        if not hasattr(self, "unconverted"):
            self.discover()
        return list(self.unconverted)

    def set_tools_capability(self, model_id, supported):
        """Record a runtime finding (does the model actually tool-call) so the UI can adapt."""
        m = self.models.get(model_id)
        if m:
            m.tools = bool(supported)
