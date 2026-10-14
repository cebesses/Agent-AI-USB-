import os
import re

NETWORK_MODES = ["OFFLINE", "LOCALHOST_ONLY", "APPROVED_DOMAINS", "FULL_INTERNET"]

ALWAYS_CONFIRM = {
    "delete_files",
    "run_python",
    "run_code",
    "web_search",
    "fetch_url",
    "quarantine",
    "remove_persistence",
    "harden",
}

NETWORK_TOOLS = {"web_search", "fetch_url"}

_READ_ONLY_VERBS = {
    "get", "test", "measure", "select", "where", "sort", "group", "format",
    "convertto", "convertfrom", "resolve", "split", "join", "compare", "find",
}

_READ_ONLY_COMMANDS = {
    "out-string", "out-null", "out-host", "write-output", "write-host", "foreach-object",
    "ls", "dir", "gci", "gi", "gc", "cat", "type", "sls", "ps", "gps", "gsv", "pwd",
    "gcim", "gwmi", "measure", "select", "where", "sort", "group", "ft", "fl", "fw",
    "gm", "echo", "foreach", "%", "?", "hostname", "whoami", "ipconfig", "systeminfo",
    "tasklist", "ping", "nslookup", "netstat", "where.exe", "ver", "if", "else",
    "elseif", "for", "while", "try", "catch", "finally", "return", "param", "true",
    "false", "null", "cd", "set-location"  # Añadidos cd y set-location como navegación segura
}

_WRITE_HINTS = re.compile(
    r"("
    r">>?"
    r"|Out-File|Set-Content|Add-Content|Clear-Content|New-Item|Remove-Item|Move-Item"
    r"|Copy-Item|Rename-Item|Rename-ItemProperty|Set-Item|Set-ItemProperty|New-ItemProperty"
    r"|Remove-ItemProperty|Clear-Item|Clear-ItemProperty|Start-Process|Stop-Process"
    r"|Suspend-Process|Resume-Process|Restart-Computer|Stop-Computer|Restart-Service"
    r"|Stop-Service|Start-Service|Set-Service|New-Service|Remove-Service"
    r"|Invoke-Expression|iex|Invoke-Command|Invoke-WebRequest|Invoke-RestMethod|iwr|curl|wget"
    r"|Start-BitsTransfer|bitsadmin|Expand-Archive|Compress-Archive|Add-Type"
    r"|Register-ScheduledTask|Unregister-ScheduledTask|Set-ScheduledTask|New-ScheduledTask"
    r"|Register-WmiEvent|Register-ObjectEvent|Unregister-Event|Set-ExecutionPolicy"
    r"|Set-MpPreference|Add-MpPreference|Remove-MpPreference|Set-NetFirewallProfile"
    r"|New-NetFirewallRule|Remove-NetFirewallRule|Enable-NetFirewallRule|Disable-NetFirewallRule"
    r"|Set-NetIPInterface|New-NetIPAddress|Remove-NetIPAddress|Set-DnsClientServerAddress"
    r"|Set-ComputerName|Add-Computer|Remove-Computer|Enable-ComputerRestore|Disable-ComputerRestore"
    r"|Checkpoint-Computer|Restore-Computer|Format-Volume|Clear-Disk|Initialize-Disk"
    r"|New-Partition|Remove-Partition|Resize-Partition|Set-Partition|Repair-Volume"
    r"|Mount-DiskImage|Dismount-DiskImage|Set-Volume|bcdedit|bcdboot|diskpart"
    r"|reg(\.exe)?\s+(add|delete|import|copy)|regsvr32|rundll32|msiexec|schtasks"
    r"|sc(\.exe)?\s+(create|delete|config|start|stop)|net(\.exe)?\s+(start|stop|user|localgroup|share|use)"
    r"|netsh|vssadmin|wbadmin|cipher|takeown|icacls|cacls|attrib|\$env:"
    r"|\.(Delete|MoveTo|CopyTo|Kill|Create|WriteAllText|WriteAllBytes|AppendAllText|Replace)\("
    r")",
    re.IGNORECASE,
)

_EXECUTABLE_EXT = (
    ".exe", ".bat", ".cmd", ".ps1", ".psm1", ".psd1", ".vbs", ".vbe", ".js",
    ".jse", ".wsf", ".wsh", ".msi", ".msp", ".com", ".scr", ".jar", ".hta",
    ".cpl", ".dll", ".ocx", ".reg", ".lnk",
)

_HIGH_RISK = [
    (r"bcdedit", "boot configuration"),
    (r"diskpart", "disk partitioning"),
    (r"format-volume|clear-disk|initialize-disk", "drive formatting"),
    (r"\bformat(\.exe)?(\.com)?\s+[a-z]:", "drive formatting"),
    (r"cipher(\.exe)? /w", "wiping free space"),
    (r"shutdown(\.exe)?", "shutting down"),
    (r"restart(\.exe)?", "restarting"),
    (r"taskkill(\.exe)?", "killing processes"),
    (r"powercfg(\.exe)?", "power settings"),
    (r"set-executionpolicy", "execution policy"),
    (r"regsvr32", "registering DLLs"),
    (r"rundll32", "running DLLs"),
    (r"msiexec", "installing MSI packages"),
    (r"schtasks(\.exe)?", "scheduled tasks"),
    (r"wbadmin(\.exe)?", "backups"),
    (r"vssadmin(\.exe)?", "shadow copies"),
    (r"gpupdate(\.exe)? /force", "group policy"),
    (r"netsh(\.exe)?", "network configuration"),
    (r"ipconfig(\.exe)? /release|/renew", "IP configuration"),
    (r"rename-computer(\.exe)?", "renaming computer"),
]

_SYSTEM = (
    r"system32",
    r"windows",
    r"program files",
    r"program files (x86)",
    r"windows\system32",
    r"windows\syswow64",
)

# Compilado de forma segura con re.IGNORECASE en vez de (?i) en el medio
_DANGEROUS_VERBS = re.compile(
    r"\s+(delete|remove|rm|del|rd|rmdir|erase|format|wipe)\b",
    re.IGNORECASE
)


def _is_high_risk_command(command):
    l = command.lower().strip()
    for pattern, _ in _HIGH_RISK:
        if re.search(pattern, l):
            return True
    return False


def _is_windows_system_path(path):
    l = path.lower().replace("/", "\\")
    for seg in _SYSTEM:
        if l.startswith(seg):
            return True
    return False


def _normalize_ps_path(p):
    if p.startswith("${"):
        return p
    if p.startswith("${env:"):
        return p
    return p.strip('"\'').replace("/", "\\")


def blocked_reason(command):
    """
    Genera razón de bloqueo para comandos de alto impacto.
    """
    if not command:
        return ""

    if _is_high_risk_command(command):
        for pattern, desc in _HIGH_RISK:
            if re.search(pattern, command, re.IGNORECASE):
                return f"Comando de alto impacto ({desc}): confirma que realmente deseas ejecutarlo."

    # Recursively deleting a whole drive can never be undone - block it outright
    m = re.search(r"(?i)\b(?:remove-item|ri|del|rd|erase)\b(.*)", command)
    if m and re.search(r"(?i)-recurse|/s\b", m.group(1)):
        for tok in m.group(1).split():
            tok = tok.strip("\"'")
            if re.fullmatch(r"[A-Za-z]:[/\\]?", tok):
                return f"Borrar una unidad completa ({tok}) está bloqueado."

    # Corrección: Uso de regex seguro sin global flag a mitad de expresión
    if _DANGEROUS_VERBS.search(command):
        parts = command.split()
        for i, part in enumerate(parts):
            if not part.startswith("-") and i > 0 and _normalize_ps_path(part):
                candidate = _normalize_ps_path(part)
                if os.path.exists(candidate):
                    if os.path.isfile(candidate) and candidate.lower().endswith(_EXECUTABLE_EXT):
                        return f"Intentas borrar un ejecutable crítico: {os.path.basename(candidate)}"
                    if _is_windows_system_path(candidate):
                        return f"Intentas borrar un archivo del sistema: {candidate}"
                    break

    return ""


def powershell_is_read_only(command):
    if _WRITE_HINTS.search(command):
        return False

    for cmdlet in re.findall(
        r"(?<![\w$.-])([A-Za-z]+-[A-Za-z]+)(?![\w-])",
        command,
    ):
        verb, _, _ = cmdlet.lower().partition("-")

        if (
            verb not in _READ_ONLY_VERBS
            and cmdlet.lower() not in _READ_ONLY_COMMANDS
        ):
            return False

    for segment in re.split(r"[|;\n{}()]|&&|\|\|", command):
        words = segment.strip().split()

        if not words:
            continue

        first = words[0].lower()

        if (
            first.startswith("$")
            or first.startswith("-")
            or first.startswith("[")
            or first.startswith("'")
            or first.startswith('"')
        ):
            continue

        if "-" in first and first.split("-")[0] in _READ_ONLY_VERBS:
            continue

        if (
            first not in _READ_ONLY_COMMANDS
            and not re.fullmatch(r"[\d.,]+", first)
        ):
            return False

    return True


class PolicyEngine:

    def __init__(self, config=None):
        self.config = config
        self.network_mode = "OFFLINE"
        self.auto_approve = False  # never default to approving risky actions

        if config:
            self.network_mode = getattr(config, "network_mode", "OFFLINE")
            self.auto_approve = getattr(config, "auto_approve", False)

    def check(self, tool_name, params=None):
        params = params or {}

        if tool_name == "run_powershell":
            command = str(params.get("command", ""))
            reason = blocked_reason(command)
            if reason:
                return {
                    "allowed": False,
                    "confirm": False,
                    "reason": reason,
                }
            return {
                "allowed": True,
                "confirm": not powershell_is_read_only(command),
                "reason": "",
            }

        if tool_name in NETWORK_TOOLS:
            if self.network_mode == "OFFLINE":
                return {
                    "allowed": False,
                    "confirm": False,
                    "reason": "Internet está desactivado: usa '/red on' para habilitarlo esta sesión.",
                }
            return {
                "allowed": True,
                "confirm": True,
                "reason": "",
            }

        if tool_name in ("run_python", "run_code"):
            return {
                "allowed": True,
                "confirm": True,
                "reason": "",
            }

        if tool_name == "network":
            reaches_out = (
                str(params.get("action", "")).lower()
                in (
                    "ping",
                    "dns",
                    "traceroute",
                    "portscan",
                    "http_headers",
                )
            )
            return {
                "allowed": True,
                "confirm": reaches_out,
                "reason": "",
            }

        if tool_name in ALWAYS_CONFIRM:
            return {
                "allowed": True,
                "confirm": True,
                "reason": "",
            }

        if (
            tool_name in ("move_files", "copy_files")
            and str(params.get("on_conflict", "")).lower() == "overwrite"
        ):
            return {
                "allowed": True,
                "confirm": True,
                "reason": "",
            }

        if tool_name == "write_file" and _truthy(params.get("overwrite")):
            return {
                "allowed": True,
                "confirm": True,
                "reason": "",
            }

        if (
            tool_name == "clipboard"
            and str(params.get("action", "")).lower() == "write"
        ):
            return {
                "allowed": True,
                "confirm": True,
                "reason": "",
            }

        if (
            tool_name == "open"
            and str(params.get("target", ""))
            .strip()
            .strip('"')
            .lower()
            .endswith(_EXECUTABLE_EXT)
        ):
            return {
                "allowed": True,
                "confirm": True,
                "reason": "",
            }

        return {
            "allowed": True,
            "confirm": False,
            "reason": "",
        }

    def check_permission(self, tool_name, params=None):
        r = self.check(tool_name, params)
        return {
            "allowed": r["allowed"],
            "requires_confirmation": r["confirm"],
            "reason": r["reason"],
        }

    def set_network_mode(self, mode):
        if mode in NETWORK_MODES:
            self.network_mode = mode


def _truthy(value):
    return value is True or str(value).lower() in (
        "true",
        "1",
        "yes",
    )