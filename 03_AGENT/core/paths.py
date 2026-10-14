"""
Agent IA USB Path Helpers
Resolves user-friendly paths ("Downloads", "%USERPROFILE%\\x", "descargas\\fotos")
to absolute Windows paths and decides which paths tools may read or change.
"""
import os
import re
import ctypes
import string
from functools import lru_cache
from pathlib import Path

AGENT_IA_USB_ROOT = Path(__file__).resolve().parent.parent.parent

# Windows Known Folder IDs (these follow OneDrive redirection, unlike ~/Desktop)
_KNOWN_FOLDER_IDS = {
    "Desktop": "{B4BFCC3A-DB2C-424C-B029-7FE99A87C641}",
    "Downloads": "{374DE290-123F-4565-9164-39C4925E467B}",
    "Documents": "{FDD39AD0-238F-46AF-ADB4-6C85480369C7}",
    "Pictures": "{33E28130-4E1E-4676-835A-98395C3BC3BB}",
    "Music": "{4BD8D571-6D19-48D3-BE97-422220080E43}",
    "Videos": "{18989B1D-99B5-455B-841C-AB7C74E4DDFC}",
}

# Words the user may type (English and Spanish) -> known folder
FOLDER_ALIASES = {
    "desktop": "Desktop", "escritorio": "Desktop",
    "downloads": "Downloads", "download": "Downloads", "descargas": "Downloads",
    "documents": "Documents", "documentos": "Documents", "my documents": "Documents",
    "mis documentos": "Documents",
    "pictures": "Pictures", "imagenes": "Pictures", "imágenes": "Pictures", "fotos": "Pictures",
    "music": "Music", "musica": "Music", "música": "Music",
    "videos": "Videos", "vídeos": "Videos",
}

DRIVE_TYPES = {0: "unknown", 1: "invalid", 2: "removable", 3: "fixed", 4: "network", 5: "cdrom", 6: "ramdisk"}


class _GUID(ctypes.Structure):
    _fields_ = [("Data1", ctypes.c_ulong), ("Data2", ctypes.c_ushort),
                ("Data3", ctypes.c_ushort), ("Data4", ctypes.c_ubyte * 8)]


def _known_folder(guid_str):
    """Ask Windows (SHGetKnownFolderPath) for a known folder path."""
    try:
        guid = _GUID()
        ctypes.windll.ole32.CLSIDFromString(ctypes.c_wchar_p(guid_str), ctypes.byref(guid))
        out = ctypes.c_wchar_p()
        if ctypes.windll.shell32.SHGetKnownFolderPath(ctypes.byref(guid), 0, None, ctypes.byref(out)) == 0:
            value = out.value
            ctypes.windll.ole32.CoTaskMemFree(out)
            return value
    except Exception:
        pass
    return None


@lru_cache(maxsize=1)
def known_folders():
    """Return {"Home": ..., "Desktop": ..., "Downloads": ...} with real paths."""
    home = Path.home()
    folders = {"Home": home}
    for name, guid in _KNOWN_FOLDER_IDS.items():
        found = _known_folder(guid) if os.name == "nt" else None
        folders[name] = Path(found) if found else home / name
    return folders


def list_drives():
    """List drives as dicts: letter, type, label, total/free bytes."""
    drives = []
    if os.name != "nt":
        return drives
    kernel32 = ctypes.windll.kernel32
    mask = kernel32.GetLogicalDrives()
    for i, letter in enumerate(string.ascii_uppercase):
        if not mask & (1 << i):
            continue
        root = f"{letter}:\\"
        dtype = DRIVE_TYPES.get(kernel32.GetDriveTypeW(ctypes.c_wchar_p(root)), "unknown")
        info = {"letter": f"{letter}:", "root": root, "type": dtype, "label": "", "total": 0, "free": 0}
        if dtype in ("fixed", "removable", "network", "ramdisk"):
            label = ctypes.create_unicode_buffer(261)
            fs = ctypes.create_unicode_buffer(261)
            if kernel32.GetVolumeInformationW(ctypes.c_wchar_p(root), label, 261, None, None, None, fs, 261):
                info["label"] = label.value
                info["filesystem"] = fs.value
            free = ctypes.c_ulonglong()
            total = ctypes.c_ulonglong()
            if kernel32.GetDiskFreeSpaceExW(ctypes.c_wchar_p(root), None, ctypes.byref(total), ctypes.byref(free)):
                info["total"] = total.value
                info["free"] = free.value
        drives.append(info)
    return drives


def drive_type(path):
    """'fixed', 'removable', 'network'... for the drive holding `path`."""
    if os.name != "nt":
        return "fixed"
    anchor = Path(path).anchor or str(Path.cwd().anchor)
    return DRIVE_TYPES.get(ctypes.windll.kernel32.GetDriveTypeW(ctypes.c_wchar_p(anchor)), "unknown")


def resolve(path, cwd=None):
    """Turn whatever the model/user wrote into an absolute, normalized Path."""
    text = str(path or "").strip().strip('"').strip("'").strip()
    if not text:
        return Path(cwd) if cwd else Path.home()
    text = os.path.expandvars(os.path.expanduser(text))
    text = text.replace("/", "\\") if os.name == "nt" else text
    if re.fullmatch(r"[A-Za-z]:", text):
        text += "\\"
    p = Path(text)
    if not p.is_absolute():
        parts = p.parts
        alias = FOLDER_ALIASES.get(parts[0].lower()) if parts else None
        if alias:
            p = known_folders()[alias].joinpath(*parts[1:])
        elif p.anchor:  # "\Users\x" -> current drive
            p = Path(os.path.abspath(text))
        else:
            p = Path(cwd or Path.home()) / p
    return Path(os.path.normpath(str(p)))


def _within(path, parent):
    try:
        return path == parent or parent in path.parents
    except Exception:
        return False


def _system_dirs():
    dirs = []
    for var in ("SystemRoot", "ProgramFiles", "ProgramFiles(x86)", "ProgramW6432", "ProgramData"):
        value = os.environ.get(var)
        if value:
            dirs.append(Path(value))
    for drive in list_drives():
        root = Path(drive["root"])
        dirs += [root / "$Recycle.Bin", root / "System Volume Information", root / "Recovery"]
    return dirs


# Parts of Agent IA USB itself that tools must never change. Everything else under the
# Agent IA USB root stays usable, because on the USB the root may be the drive itself.
_PROTECTED_AGENT_IA_USB = ["01_MODEL", "02_RUNTIME", "03_AGENT", "06_CONFIG", "07_BACKUPS", "08_TRAINING"]
_PROTECTED_ROOT_FILES = {"readme.txt", "improvement_plan.md", ".gitignore"}


def _is_agent_ia_usb_file(p):
    if any(_within(p, AGENT_IA_USB_ROOT / d) for d in _PROTECTED_AGENT_IA_USB):
        return True
    return p.parent == AGENT_IA_USB_ROOT and (p.suffix.lower() == ".bat" or p.name.lower() in _PROTECTED_ROOT_FILES)


def check_read(path):
    """(ok, reason) for reading/listing a path."""
    return True, ""


def check_write(path):
    """(ok, reason) for creating/changing something at `path`."""
    p = Path(path)
    for d in _system_dirs():
        if _within(p, d):
            return False, f"Refusing to change Windows system location: {d}"
    if _is_agent_ia_usb_file(p):
        return False, f"Refusing to change Agent IA USB's own files ({p}). Use 04_WORKSPACE for scratch work."
    return True, ""


def check_remove(path):
    """(ok, reason) for deleting/moving away/renaming `path` itself."""
    p = Path(path)
    ok, why = check_write(p)
    if not ok:
        return ok, why
    if p.parent == p:
        return False, f"Refusing to remove a whole drive ({p})."
    protected = [Path.home(), Path.home().parent, AGENT_IA_USB_ROOT] + list(known_folders().values())
    for q in protected:
        if p == q:
            return False, f"Refusing to remove or move the folder {p} itself (its contents are fine)."
        if _within(q, p):
            return False, f"Refusing to remove {p}: it contains {q}."
    return True, ""


def human_size(n):
    n = float(n or 0)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or unit == "TB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024
