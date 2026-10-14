"""
Agent IA USB Undo Journal
Every file-changing tool call is recorded as one group of reversible
operations, so 'undo' can put things back the way they were - even after
Agent IA USB restarts.

Operation kinds (stored in 03_AGENT/journal/journal.jsonl):
  move    {src, dst}      undo: move dst back to src
  copy    {dst}           undo: remove the copy
  create  {path}          undo: remove the new file
  mkdir   {path}          undo: remove the folder if still empty
  backup  {path, backup}  undo: restore the saved copy over path
  recycle {path}          undo: restore from the Windows Recycle Bin
  trash   {path, trash}   undo: move back from <drive>\\.agent_ia_usb_trash
"""
import base64
import json
import os
import shutil
import struct
import subprocess
import uuid
import ctypes
from ctypes import wintypes
from datetime import datetime
from pathlib import Path

AGENT_IA_USB_ROOT = Path(__file__).resolve().parent.parent.parent
MAX_BACKUP_BYTES = 500 * 1024 * 1024
MAX_GROUPS = 300


class Journal:
    def __init__(self, journal_file=None, backup_dir=None, session_id=""):
        self.file = Path(journal_file or AGENT_IA_USB_ROOT / "03_AGENT" / "journal" / "journal.jsonl")
        self.backup_dir = Path(backup_dir or AGENT_IA_USB_ROOT / "07_BACKUPS" / "undo")
        self.session_id = session_id
        self._group = None

    # ------------------------------------------------------------------ record
    def begin(self, tool, summary=""):
        self._group = {
            "id": uuid.uuid4().hex[:10],
            "time": datetime.now().isoformat(timespec="seconds"),
            "session": self.session_id,
            "tool": tool,
            "summary": summary,
            "ops": [],
        }
        return self._group["id"]

    def record(self, op, **fields):
        if self._group is not None:
            self._group["ops"].append({"op": op, **{k: str(v) for k, v in fields.items()}})

    def record_mkdirs(self, folder):
        """Create `folder` (and missing parents), recording each new level."""
        folder = Path(folder)
        missing = []
        p = folder
        while not p.exists() and p.parent != p:
            missing.append(p)
            p = p.parent
        for d in reversed(missing):
            d.mkdir(exist_ok=True)
            self.record("mkdir", path=d)

    def backup(self, path):
        """Save a copy of `path` before it gets overwritten; returns the copy's path or None."""
        path = Path(path)
        if self._group is None or not path.is_file():
            return None
        try:
            if path.stat().st_size > MAX_BACKUP_BYTES:
                return None
            dest = self.backup_dir / self._group["id"] / f"{len(self._group['ops'])}_{path.name}"
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, dest)
            self.record("backup", path=path, backup=dest)
            return dest
        except OSError:
            return None

    def commit(self):
        group, self._group = self._group, None
        if not group or not group["ops"]:
            return
        self.file.parent.mkdir(parents=True, exist_ok=True)
        with open(self.file, "a", encoding="utf-8") as f:
            f.write(json.dumps(group, ensure_ascii=False) + "\n")
        self._prune()

    # -------------------------------------------------------------------- read
    def _entries(self):
        if not self.file.exists():
            return []
        entries = []
        for line in self.file.read_text(encoding="utf-8", errors="replace").splitlines():
            try:
                entries.append(json.loads(line))
            except ValueError:
                pass
        return entries

    def _prune(self):
        entries = self._entries()
        if len(entries) > MAX_GROUPS * 2:
            keep = entries[-MAX_GROUPS:]
            self.file.write_text("".join(json.dumps(e, ensure_ascii=False) + "\n" for e in keep), encoding="utf-8")

    def last_undoable(self):
        entries = self._entries()
        undone = {e["undo_of"] for e in entries if "undo_of" in e}
        for e in reversed(entries):
            if "ops" in e and e["id"] not in undone:
                return e
        return None

    def recent(self, n=10):
        return [e for e in self._entries() if "ops" in e][-n:]

    # -------------------------------------------------------------------- undo
    def undo_last(self):
        """Undo the most recent change group. Returns (ok, message)."""
        group = self.last_undoable()
        if not group:
            return False, "No hay nada que deshacer."
        restored, problems = 0, []
        recycled = [op["path"] for op in group["ops"] if op["op"] == "recycle"]
        if recycled:
            back, missing = restore_from_recycle_bin(recycled)
            restored += back
            problems += [f"ya no está en la Papelera: {m}" for m in missing]
        for op in reversed(group["ops"]):
            kind = op["op"]
            try:
                if kind == "move":
                    src, dst = Path(op["src"]), Path(op["dst"])
                    if not dst.exists():
                        problems.append(f"no existe: {dst}")
                    elif src.exists():
                        problems.append(f"la ubicación original vuelve a estar ocupada: {src}")
                    else:
                        src.parent.mkdir(parents=True, exist_ok=True)
                        shutil.move(str(dst), str(src))
                        restored += 1
                elif kind in ("copy", "create"):
                    target = Path(op.get("dst") or op.get("path"))
                    if target.is_dir():
                        shutil.rmtree(target)
                    elif target.exists():
                        target.unlink()
                    restored += 1
                elif kind == "mkdir":
                    d = Path(op["path"])
                    if d.is_dir() and not any(d.iterdir()):
                        d.rmdir()
                elif kind == "backup":
                    src = Path(op["backup"])
                    if src.exists():
                        Path(op["path"]).parent.mkdir(parents=True, exist_ok=True)
                        shutil.copy2(src, op["path"])
                        restored += 1
                    else:
                        problems.append(f"falta la copia de respaldo de {op['path']}")
                elif kind == "ps":  # an internally built PowerShell command that reverses a system change
                    script = "$ProgressPreference='SilentlyContinue'; " + op["undo"]
                    r = subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
                                        "-EncodedCommand", base64.b64encode(script.encode("utf-16-le")).decode("ascii")],
                                       capture_output=True, timeout=120, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
                    if r.returncode == 0:
                        restored += 1
                    else:
                        problems.append(f"no se pudo {op.get('label', 'revertir')}: {r.stderr.decode(errors='replace')[:120]}")
                elif kind == "trash":
                    src, trash = Path(op["path"]), Path(op["trash"])
                    if trash.exists() and not src.exists():
                        src.parent.mkdir(parents=True, exist_ok=True)
                        shutil.move(str(trash), str(src))
                        restored += 1
                    else:
                        problems.append(f"no se pudo restaurar {src}")
            except OSError as e:
                problems.append(f"{kind} {op.get('path') or op.get('dst')}: {e}")
        self.file.parent.mkdir(parents=True, exist_ok=True)
        with open(self.file, "a", encoding="utf-8") as f:
            f.write(json.dumps({"undo_of": group["id"], "time": datetime.now().isoformat(timespec="seconds")}) + "\n")
        msg = f"Deshecho {group['tool']} ({group.get('summary') or group['time']}): restauré {restored} elemento(s)."
        if problems:
            msg += f" {len(problems)} problema(s): " + "; ".join(problems[:5])
        return not problems or restored > 0, msg


# --------------------------------------------------------------- Recycle Bin
class _SHFILEOPSTRUCTW(ctypes.Structure):
    _fields_ = [
        ("hwnd", wintypes.HWND),
        ("wFunc", wintypes.UINT),
        ("pFrom", wintypes.LPCWSTR),
        ("pTo", wintypes.LPCWSTR),
        ("fFlags", ctypes.c_uint16),
        ("fAnyOperationsAborted", wintypes.BOOL),
        ("hNameMappings", ctypes.c_void_p),
        ("lpszProgressTitle", wintypes.LPCWSTR),
    ]


_FO_DELETE = 3
_FOF_SILENT = 0x0004
_FOF_NOCONFIRMATION = 0x0010
_FOF_ALLOWUNDO = 0x0040
_FOF_NOERRORUI = 0x0400
_FOF_WANTNUKEWARNING = 0x4000  # warn instead of silently destroying files too big for the bin


def send_to_recycle_bin(paths):
    """Recycle files/folders on fixed drives. Returns the list of paths that are gone."""
    paths = [str(p) for p in paths]
    for start in range(0, len(paths), 200):
        chunk = paths[start:start + 200]
        buf = ctypes.create_unicode_buffer("\0".join(chunk) + "\0\0")
        op = _SHFILEOPSTRUCTW()
        op.wFunc = _FO_DELETE
        op.pFrom = ctypes.cast(buf, wintypes.LPCWSTR)
        op.fFlags = _FOF_ALLOWUNDO | _FOF_NOCONFIRMATION | _FOF_SILENT | _FOF_NOERRORUI | _FOF_WANTNUKEWARNING
        ctypes.windll.shell32.SHFileOperationW(ctypes.byref(op))
    return [p for p in paths if not os.path.lexists(p)]


def _read_recycle_info(ifile):
    """Original path stored in a $I metadata file (Windows Vista+ format)."""
    data = ifile.read_bytes()
    version = struct.unpack_from("<q", data, 0)[0]
    deleted = struct.unpack_from("<q", data, 16)[0]
    if version == 1:
        name = data[24:24 + 520].decode("utf-16-le").split("\0", 1)[0]
    else:
        length = struct.unpack_from("<i", data, 24)[0]
        name = data[28:28 + length * 2].decode("utf-16-le").rstrip("\0")
    return name, deleted


def restore_from_recycle_bin(paths):
    """Put recycled items back at their original paths. Returns (count, missing)."""
    wanted = {os.path.normcase(str(p)): str(p) for p in paths}
    best = {}  # normcase path -> (deleted_time, $I path, $R path)
    for drive in {Path(p).anchor for p in paths}:
        bin_root = Path(drive) / "$Recycle.Bin"
        try:
            sid_dirs = [d for d in bin_root.iterdir() if d.is_dir()]
        except OSError:
            continue
        for sid_dir in sid_dirs:
            try:
                infos = list(sid_dir.glob("$I*"))
            except OSError:
                continue
            for ifile in infos:
                try:
                    original, deleted = _read_recycle_info(ifile)
                except (OSError, struct.error, UnicodeDecodeError):
                    continue
                key = os.path.normcase(original)
                if key in wanted and (key not in best or deleted > best[key][0]):
                    best[key] = (deleted, ifile, sid_dir / ("$R" + ifile.name[2:]))
    restored, missing = 0, []
    for key, original in wanted.items():
        if key not in best or not best[key][2].exists() or os.path.lexists(original):
            missing.append(original)
            continue
        _, ifile, rfile = best[key]
        try:
            Path(original).parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(rfile), original)
            ifile.unlink()
            restored += 1
        except OSError:
            missing.append(original)
    return restored, missing
