#!/usr/bin/env python3
"""
Agent IA USB Unit Test Suite (no model needed)
Tests paths safety, policy, every tool, undo journal (incl. Recycle Bin
restore), memory, skills, and the agent loop with a scripted fake model.
Run: python 03_AGENT\\eval_suite.py
"""
import sys, os, json, time, shutil, tempfile
from pathlib import Path

AGENT_IA_USB_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(AGENT_IA_USB_ROOT / "03_AGENT"))
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

PASS = FAIL = 0


def test(name, condition, detail=""):
    global PASS, FAIL
    if condition:
        PASS += 1
        print(f"  [PASS] {name}")
    else:
        FAIL += 1
        print(f"  [FAIL] {name} {detail}")


def section(title):
    print(f"\n--- {title} ---")


class FakeLLM:
    """Plays back scripted assistant messages, like a model would."""
    def __init__(self, script):
        self.script = list(script)
        self.requests = []

    def chat(self, messages, tools=None, **kw):
        self.requests.append(messages)
        msg = self.script.pop(0) if self.script else {"role": "assistant", "content": "Done."}
        if "_raw" in msg:  # a scripted server error
            return msg["_raw"]
        msg = dict(msg)
        finish = msg.pop("_finish", "stop")
        return {"choices": [{"message": msg, "finish_reason": finish}], "timings": {}}

    def is_server_running(self):
        return True


def tool_call(name, **args):
    return {"id": f"c{time.time_ns()}", "type": "function", "function": {"name": name, "arguments": json.dumps(args)}}


def main():
    print("=" * 60)
    print("Agent IA USB UNIT TEST SUITE")
    print("=" * 60)

    section("Imports")
    from core.config import AgentIaUsbConfig
    from core import paths
    from core.policy import PolicyEngine, powershell_is_read_only, blocked_reason
    from core.journal import Journal
    from core.tools import ToolRegistry, DECLINED
    from core.prompts import build_system_prompt, build_turn_context
    from orchestrator.agent import AgentIaUsbAgent, redact_secrets, parse_text_tool_calls, wrong_language
    from memory.memory import MemorySystem
    from skills.skills import SkillLibrary, Skill
    test("core modules import", True)

    sandbox = Path(tempfile.mkdtemp(prefix="agent_ia_usb_unit_"))
    try:
        config = AgentIaUsbConfig(AGENT_IA_USB_ROOT)
        test("config loads models.json tiers", bool(config.models.get("tiers", {})))
        test("sampling has no comment keys", all(not k.startswith("_") for k in config.sampling))

        section("Paths")
        kf = paths.known_folders()
        test("known folders found", all(k in kf for k in ("Desktop", "Downloads", "Documents")))
        test("'descargas' -> Downloads", paths.resolve("descargas") == kf["Downloads"])
        test("'Escritorio\\x' -> Desktop\\x", paths.resolve("Escritorio\\x") == kf["Desktop"] / "x")
        test("%USERPROFILE% expands", paths.resolve("%USERPROFILE%") == Path.home())
        test("relative path uses cwd", paths.resolve("a\\b.txt", sandbox) == sandbox / "a" / "b.txt")
        test("D: -> D:\\", str(paths.resolve("D:")) == "D:\\")
        windir = Path(os.environ.get("SystemRoot", "C:\\Windows"))
        test("write to Windows refused", not paths.check_write(windir / "agent-ia-usb.txt")[0])
        test("write to Agent IA USB code refused", not paths.check_write(AGENT_IA_USB_ROOT / "03_AGENT" / "x.py")[0])
        test("write to Agent IA USB workspace allowed", paths.check_write(AGENT_IA_USB_ROOT / "04_WORKSPACE" / "x.txt")[0])
        test("write to temp allowed", paths.check_write(sandbox / "x.txt")[0])
        test("removing home refused", not paths.check_remove(Path.home())[0])
        test("removing Downloads itself refused", not paths.check_remove(kf["Downloads"])[0])
        test("removing a drive refused", not paths.check_remove(Path("C:\\"))[0])

        section("Policy")
        policy = PolicyEngine(config)
        test("datetime runs freely", policy.check("datetime") == {"allowed": True, "confirm": False, "reason": ""})
        test("delete asks", policy.check("delete_files", {"paths": ["x"]})["confirm"])
        test("move without overwrite runs freely", not policy.check("move_files", {"on_conflict": "skip"})["confirm"])
        test("move with overwrite asks", policy.check("move_files", {"on_conflict": "overwrite"})["confirm"])
        test("read-only PowerShell runs freely", powershell_is_read_only("Get-ChildItem C:\\ | Measure-Object | Select-Object Count"))
        test("Get-Process | Sort runs freely", powershell_is_read_only("Get-Process | Sort-Object CPU -Descending | Select-Object -First 5"))
        test("Remove-Item asks", not powershell_is_read_only("Remove-Item C:\\x.txt"))
        test("hidden Remove-Item in ForEach asks", not powershell_is_read_only("Get-ChildItem | ForEach-Object { Remove-Item $_ }"))
        test("redirect to file asks", not powershell_is_read_only("Get-Date > C:\\x.txt"))
        test("Format-Volume blocked", not policy.check("run_powershell", {"command": "Format-Volume -DriveLetter D"})["allowed"])
        test("Set-ExecutionPolicy blocked", bool(blocked_reason("Set-ExecutionPolicy Unrestricted")))
        test("rm drive root blocked", bool(blocked_reason("Remove-Item -Recurse -Force D:\\")))
        test("web search blocked offline", not policy.check("web_search", {"query": "x"})["allowed"])
        policy.set_network_mode("FULL_INTERNET")
        test("web search asks when online", policy.check("web_search", {"query": "x"})["confirm"])
        policy.set_network_mode("OFFLINE")
        test("opening an .exe asks", policy.check("open", {"target": "C:\\x\\setup.exe"})["confirm"])

        section("Tools")
        answers = []
        journal = Journal(sandbox / "journal.jsonl", sandbox / "backups", "unit")
        memory = MemorySystem(sandbox / "memory")
        plans = []
        reg = ToolRegistry(policy_engine=policy, journal=journal, memory=memory,
                           confirm=lambda tool, summary: answers.pop(0) if answers else False, cwd=sandbox,
                           on_plan=plans.append, knowledge_dir=sandbox / "kb")
        schemas = reg.schemas()
        test("36 tools registered", len(schemas) == 36, f"got {len(schemas)}")
        total_chars = len(json.dumps(schemas, separators=(",", ":")))
        test("tool schemas stay compact (< 16k chars)", total_chars < 16000, f"{total_chars}")
        test("tool names are API-safe", all(s["function"]["name"].replace("_", "").isalnum() for s in schemas))
        test("schemas serialize", len(json.dumps(schemas)) > 1000)

        src, dst = sandbox / "src", sandbox / "dst"
        src.mkdir()
        for i in range(100):
            (src / f"note_{i:03d}.txt").write_text(f"note {i}", encoding="utf-8")
        for i in range(5):
            (src / f"photo_{i}.jpg").write_bytes(b"\xff\xd8\xff" + b"\0" * 2048)
        (src / "música ñ.txt").write_text("acentos", encoding="utf-8")

        r = reg.execute("find_files", {"root": str(src), "pattern": "*.txt"})
        test("find_files counts 101 txt", r.success and r.output.startswith("Found 101 files"), r.output[:80])
        r = reg.execute("find_files", {"root": str(src), "pattern": "*.jpg", "min_size_kb": 1})
        test("find_files size filter", r.output.startswith("Found 5 files"), r.output[:60])
        r = reg.execute("list_dir", {"path": str(src), "pattern": "*.jpg"})
        test("list_dir with pattern", r.success and "5 files" in r.output, r.output[:80])
        r = reg.execute("search_text", {"root": str(src), "query": "note 42"})
        test("search_text finds content", r.success and "note_042.txt" in r.output, r.output[:120])
        r = reg.execute("read_file", {"path": str(src / "música ñ.txt")})
        test("read_file keeps accents", r.success and "acentos" in r.output)
        r = reg.execute("read_file", {"path": str(src / "photo_0.jpg")})
        test("read_file refuses binary", not r.success)
        r = reg.execute("file_info", {"path": str(src)})
        test("file_info on folder", r.success and "files: 106" in r.output, r.output)

        r = reg.execute("move_files", {"source_dir": str(src), "pattern": "*.txt", "destination_dir": str(dst)})
        moved = len(list(dst.glob("*.txt")))
        test("move_files moves 101 txt in one call", r.success and moved == 101 and not list(src.glob("*.txt")), r.output[:150])
        r = reg.execute("undo_last", {})
        test("undo restores the move", r.success and len(list(src.glob("*.txt"))) == 101 and not list(dst.glob("*.txt")), r.output[:150])
        test("undo removed created folder", not dst.exists())

        (dst).mkdir()
        (dst / "note_000.txt").write_text("existing", encoding="utf-8")
        r = reg.execute("copy_files", {"sources": [str(src / "note_000.txt"), str(src / "note_001.txt")], "destination_dir": str(dst)})
        test("copy skips existing by default", "Copied 1 of 2" in r.output and "Skipped 1" in r.output, r.output[:120])
        r = reg.execute("copy_files", {"sources": [str(src / "note_000.txt")], "destination_dir": str(dst), "on_conflict": "rename"})
        test("copy rename keeps both", (dst / "note_000 (1).txt").exists(), r.output[:120])
        (src / "deep").mkdir(exist_ok=True)
        (src / "deep" / "inner.jpg").write_bytes(b"x" * 2048)
        r = reg.execute("copy_files", {"source_dir": str(src), "pattern": "*.jpg", "destination_dir": str(sandbox / "jpgs")})
        test("copy tells what the non-recursive call left out", "NOTE: 1 more matching file" in r.output and "inner.jpg" in r.output, r.output[-250:])
        r = reg.execute("copy_files", {"source_dir": str(src), "pattern": "*", "min_size_kb": 1, "destination_dir": str(sandbox / "big")})
        big = sorted(p.name for p in (sandbox / "big").glob("*"))
        test("batch size filter copies only big files", big == [f"photo_{i}.jpg" for i in range(5)], f"{big} | {r.output[:100]}")
        answers[:] = [False]
        r = reg.execute("copy_files", {"sources": [str(src / "note_000.txt")], "destination_dir": str(dst), "on_conflict": "overwrite"})
        test("overwrite declined leaves file", r.error == DECLINED and (dst / "note_000.txt").read_text() == "existing")
        answers[:] = [True]
        r = reg.execute("copy_files", {"sources": [str(src / "note_000.txt")], "destination_dir": str(dst), "on_conflict": "overwrite"})
        test("overwrite approved replaces", (dst / "note_000.txt").read_text() == "note 0", r.output[:100])
        reg.execute("undo_last", {})
        test("undo restores overwritten file", (dst / "note_000.txt").read_text() == "existing")

        r = reg.execute("move_files", {"items": [{"source": str(src / "note_002.txt"), "destination": str(sandbox / "renamed" / "two.txt")}]})
        test("move with items renames", (sandbox / "renamed" / "two.txt").exists(), r.output[:100])
        r = reg.execute("rename", {"path": str(sandbox / "renamed" / "two.txt"), "new_name": "dos.txt"})
        test("rename works", (sandbox / "renamed" / "dos.txt").exists(), r.output)
        r = reg.execute("make_dir", {"path": str(sandbox / "a" / "b" / "c")})
        test("make_dir nested", (sandbox / "a" / "b" / "c").is_dir())

        f = sandbox / "doc.txt"
        r = reg.execute("write_file", {"path": str(f), "content": "hello world"})
        test("write_file creates", f.read_text() == "hello world")
        r = reg.execute("write_file", {"path": str(f), "content": "x"})
        test("write_file won't silently overwrite", not r.success and f.read_text() == "hello world")
        r = reg.execute("write_file", {"path": str(f), "content": "!", "append": True})
        test("write_file append", f.read_text() == "hello world!")
        r = reg.execute("edit_file", {"path": str(f), "old_text": "world", "new_text": "Agent IA USB"})
        test("edit_file", f.read_text() == "hello Agent IA USB!", r.output)
        reg.execute("undo_last", {})
        test("undo edit", f.read_text() == "hello world!")
        r = reg.execute("write_file", {"path": str(windir / "agent_ia_usb_test.txt"), "content": "x"})
        test("write into Windows refused", not r.success and not (windir / "agent_ia_usb_test.txt").exists())

        answers[:] = [False]
        r = reg.execute("delete_files", {"source_dir": str(src), "pattern": "*.jpg"})
        test("delete declined keeps files", r.error == DECLINED and len(list(src.glob("*.jpg"))) == 5)
        answers[:] = [True]
        r = reg.execute("delete_files", {"source_dir": str(src), "pattern": "*.jpg"})
        test("delete sends to Recycle Bin", r.success and not list(src.glob("*.jpg")), r.output[:150])
        r = reg.execute("undo_last", {})
        test("undo restores from Recycle Bin", len(list(src.glob("*.jpg"))) == 5, r.output[:200])

        r = reg.execute("run_powershell", {"command": "Get-Date -Format yyyy"})
        test("read-only PowerShell runs without asking", r.success and r.output.strip().isdigit(), r.output[:80] + r.error[:80])
        answers[:] = [True]
        r = reg.execute("run_powershell", {"command": "Write-Output 'Música ñ'; Set-Variable -Name x -Value 1"})
        test("PowerShell UTF-8 output", r.success and "Música ñ" in r.output, r.output[:80] + r.error[:80])
        answers[:] = [True]
        r = reg.execute("run_python", {"code": "print('ok', 6*7)"})
        test("run_python", r.success and "ok 42" in r.output, r.output[:80] + r.error[:80])
        r = reg.execute("disk_usage", {})
        test("disk_usage lists drives", r.success and "C:" in r.output)
        r = reg.execute("system_info", {})
        test("system_info", r.success and "RAM:" in r.output)
        r = reg.execute("datetime", {})
        test("datetime", r.success and str(time.localtime().tm_year) in r.output)
        r = reg.execute("memory", {"action": "remember", "text": "My work folder is D:\\Work"})
        r2 = reg.execute("memory", {"action": "recall", "text": "work folder"})
        test("memory remember/recall", r.success and "D:\\Work" in r2.output, r2.output)
        r = reg.execute("count_files", {"root": str(src)})
        expected = sum(1 for p in src.rglob("*") if p.is_file())
        test("count_files counts a folder", r.success and f"{expected:,} files" in r.output, r.output[:120])
        r = reg.execute("count_files", {"root": str(src), "pattern": "*.jpg"})
        n_jpg = len(list(src.rglob("*.jpg")))
        test("count_files with a pattern counts only those", r.success and f"{n_jpg} files" in r.output and f".jpg {n_jpg}" in r.output, r.output[:160])
        proj = sandbox / "web"
        r = reg.execute("create_project", {"folder": str(proj), "files": [
            {"path": "index.html", "content": "<html><body>hola</body></html>"},
            {"path": "css/style.css", "content": "body{color:red}"}, {"path": "app.js", "content": "console.log(1)"}]})
        test("create_project writes several files", r.success and (proj / "css" / "style.css").exists() and (proj / "app.js").exists(), r.output[:100])
        r = reg.execute("create_project", {"folder": str(proj), "files": [{"path": "..\\escape.txt", "content": "x"}]})
        test("create_project can't escape its folder", not r.success)
        reg.execute("undo_last", {})
        r = reg.execute("security_audit", {"checks": ["firewall", "users"]})
        test("security_audit runs read-only", r.success and "FIREWALL" in r.output, r.output[:150])
        # --- planning
        r = reg.execute("plan", {"action": "create", "steps": ["buscar", "mover", "informar"]})
        test("plan create", r.success and "1. [ ] buscar" in r.output and len(plans) == 1)
        r = reg.execute("plan", {"action": "update", "step": 2, "status": "done"})
        test("plan update", "2. [x] mover" in r.output and plans[-1][1]["status"] == "done")
        test("plan rejects a bad step", not reg.execute("plan", {"action": "update", "step": 9}).success)

        # --- documents (BM25 + docx)
        import zipfile as _zip
        docs = sandbox / "docs"
        docs.mkdir()
        (docs / "contrato.txt").write_text("El contrato de alquiler vence el 31 de marzo de 2027 según la cláusula quinta.", encoding="utf-8")
        (docs / "receta.md").write_text("Receta de arepas: harina de maíz, agua y sal. Amasar y cocinar en la plancha.", encoding="utf-8")
        with _zip.ZipFile(docs / "informe.docx", "w") as z:
            z.writestr("word/document.xml", "<w:document><w:body><w:p><w:r><w:t>Informe trimestral: las ventas subieron un 12 por ciento en Medellín.</w:t></w:r></w:p></w:body></w:document>")
        r = reg.execute("documents", {"action": "index", "path": str(docs)})
        test("documents index (txt, md, docx)", r.success and "Indexed 3 new" in r.output, r.output[:120])
        r = reg.execute("documents", {"action": "ask", "question": "¿cuándo vence el contrato de alquiler?"})
        test("documents ask finds the right file", r.success and "contrato.txt" in r.output.split("\n\n")[1] and "2027" in r.output, r.output[:200])
        r = reg.execute("documents", {"action": "ask", "question": "ventas de Medellin"})
        test("documents reads .docx and ignores accents", "informe.docx" in r.output, r.output[:200])

        # --- defensive security
        scan_dir = sandbox / "scan"
        scan_dir.mkdir()
        bad = scan_dir / "update_helper.exe"
        bad.write_text("not a real program", encoding="utf-8")
        r = reg.execute("security_scan", {"folders": [str(scan_dir)], "days": 3})
        test("security_scan flags unsigned recent executables", r.success and "UNSIGNED" in r.output and "update_helper.exe" in r.output
             and "sha256=" in r.output and "nothing was changed" in r.output, r.output[:300])
        test("security_scan never runs or moves the file", bad.exists())
        test("security_scan reports protection state", "PROTECTION STATE" in r.output and "Firewall:" in r.output, r.output[:80])
        answers[:] = [False]
        r = reg.execute("quarantine", {"paths": [str(bad)]})
        test("quarantine declined keeps the file", r.error == DECLINED and bad.exists())
        answers[:] = [True]
        marker = time.time()
        r = reg.execute("quarantine", {"paths": [str(bad)]})
        q_root = AGENT_IA_USB_ROOT / "07_BACKUPS" / "quarantine"
        moved_q = [f for f in q_root.rglob("update_helper.exe.agent-ia-usb-q")] if q_root.exists() else []
        test("quarantine isolates (renamed, not deleted)", r.success and not bad.exists() and len(moved_q) == 1, r.output[:150])
        r = reg.execute("undo_last", {})
        test("undo restores a quarantined file", bad.exists() and bad.read_text(encoding="utf-8") == "not a real program", r.output[:150])
        for f in moved_q:
            shutil.rmtree(f.parent, ignore_errors=True)
        answers[:] = [True]
        r = reg.execute("quarantine", {"paths": [str(windir / "notepad.exe")]})
        test("quarantine refuses system files", not r.success and (windir / "notepad.exe").exists(), r.error[:100])
        answers[:] = [True]
        r = reg.execute("remove_persistence", {"kind": "task", "name": "x'; Remove-Item C:\\ -Recurse #"})
        test("remove_persistence rejects dangerous names", not r.success and "characters" in r.error, r.error[:100])
        answers[:] = [True]
        r = reg.execute("remove_persistence", {"kind": "task", "name": "AgentIaUsbNoSuchTask12345"})
        test("remove_persistence: unknown task", not r.success and "No scheduled task" in r.error, r.error[:100])
        run_key = "HKCU:\\Software\\Microsoft\\Windows\\CurrentVersion\\Run"
        reg._ps(f"Set-ItemProperty -Path '{run_key}' -Name AgentIaUsbUnitTest -Value 'notepad.exe'")
        answers[:] = [True]
        r = reg.execute("remove_persistence", {"kind": "registry", "name": "AgentIaUsbUnitTest"})
        gone = "AgentIaUsbUnitTest" not in reg._ps(f"(Get-ItemProperty '{run_key}').PSObject.Properties.Name -join ','").output
        test("remove_persistence disables a Run entry", r.success and gone, r.output[:100] + r.error[:100])
        r = reg.execute("undo_last", {})
        back = "AgentIaUsbUnitTest" in reg._ps(f"(Get-ItemProperty '{run_key}').PSObject.Properties.Name -join ','").output
        test("undo re-enables the Run entry", back, r.output[:150])
        reg._ps(f"Remove-ItemProperty -Path '{run_key}' -Name AgentIaUsbUnitTest -ErrorAction SilentlyContinue")
        answers[:] = [False]
        r = reg.execute("harden", {"check": "firewall"})
        test("harden asks first and respects 'no'", r.error == DECLINED)

        r = reg.execute("fetch_url", {"url": "https://example.com"})
        test("fetch_url blocked while offline", not r.success and "red on" in r.error, r.error[:100])
        r = reg.execute("nonexistent", {})
        test("unknown tool rejected", not r.success)
        r = reg.execute("move_files", {})
        test("bad arguments explained", not r.success and "destination_dir" in r.error)

        section("Prompts")
        system = build_system_prompt(config, "test-model")
        test("system prompt: Spanish replies + rules", "Responde SIEMPRE en español" in system and str(kf["Downloads"]) in system)
        test("system prompt stable", build_system_prompt(config, "test-model") == system)
        ctx = build_turn_context(memory, None, "where is my work folder")
        test("turn context has date + memory", "Ahora:" in ctx and "D:\\Work" in ctx, ctx[:200])

        section("Agent helpers")
        test("password redacted", "REDACTED" in redact_secrets("password=secret123"))
        test("normal text untouched", redact_secrets("hello world") == "hello world")
        test("plain path not redacted", "REDACTED" not in redact_secrets("C:\\Users\\h\\Downloads\\usb-ai-main\\usb-ai-main\\README.txt"))
        calls = parse_text_tool_calls('<tool_call>{"name": "list_dir", "arguments": {"path": "C:\\\\x"}}</tool_call>')
        test("parse JSON tool call text", calls and calls[0]["name"] == "list_dir")
        calls = parse_text_tool_calls("<tool_call>\n<function=find_files>\n<parameter=root>\nC:\\x\n</parameter>\n</function>\n</tool_call>")
        test("parse XML tool call text", calls and calls[0]["arguments"].get("root") == "C:\\x")
        test("English reply flagged (es)", wrong_language("Done - moved 100 files from Downloads to the backup folder.", "es"))
        test("Spanish reply passes (es)", not wrong_language("Listo: moví 100 archivos de Descargas a la carpeta de respaldo.", "es"))
        test("Spanish reply flagged (en)", wrong_language("Claro, ya moví los archivos a la carpeta que me pediste para que estén listos.", "en"))
        from orchestrator.agent import spanish_preview, repair_json
        test("repair_json closes cut JSON", repair_json('{"path": "C:\\\\a.txt", "content": "hola mun') == {"path": "C:\\a.txt", "content": "hola mun"})
        test("repair_json trailing comma", repair_json('{"a": 1,}') == {"a": 1})
        test("repair_json rejects garbage", repair_json("no es json") is None)
        test("step preview shown in Spanish", spanish_preview(
            "Moved 100 of 100 item(s) to D:\\X. Skipped 0 (name already taken), failed 0.").startswith("Movidos 100 de 100 elemento(s) a D:\\X"))
        test("delete preview shown in Spanish", spanish_preview(
            "Deleted 5 of 5 item(s) (25 B). They are in the Recycle Bin. undo_last restores them.").startswith("Eliminados 5 de 5"))

        section("Agent loop (scripted model)")
        skills = SkillLibrary(sandbox / "skills")
        (src / "extra").mkdir()
        fake = FakeLLM([
            {"role": "assistant", "content": "", "tool_calls": [tool_call("find_files", root=str(src), pattern="*.txt", recursive=False)]},
            {"role": "assistant", "content": "", "tool_calls": [tool_call("move_files", source_dir=str(src), pattern="*.txt", destination_dir=str(sandbox / "texts"))]},
            {"role": "assistant", "content": "Listo: moví 100 archivos de texto a la carpeta texts."},
        ])
        events = []
        agent = AgentIaUsbAgent(fake, reg, policy, memory, on_event=lambda k, d: events.append(k), skills=skills, config=config)
        agent.log_trajectories = False
        reply = agent.chat("mueve los txt de src a texts")
        test("agent runs tools then replies", reply.startswith("Listo") and len(list((sandbox / "texts").glob("*.txt"))) >= 99, reply)
        test("agent emitted step events", events.count("step_start") == 2 and events.count("step_end") == 2, str(events))
        sent = fake.requests[-1]
        test("tool results fed back to model", any(m.get("role") == "tool" and "Moved" in m.get("content", "") for m in sent))
        test("messages only appended (cache friendly)", fake.requests[1][:2] == fake.requests[2][:2])
        test("skill learned from multi-step success", len(skills.skills) == 1)
        test("skill recalled for similar request", bool(skills.recall("mueve los txt de descargas")))
        reply = agent.chat("deshaz")
        test("'deshaz' command restores", "Deshecho" in reply and len(list(src.glob("*.txt"))) >= 99, reply)

        fake2 = FakeLLM([{"role": "assistant", "content": "Sure, here is the information you asked for about the files."},
                         {"role": "assistant", "content": "Claro, aquí está la información que pediste sobre los archivos."}])
        agent2 = AgentIaUsbAgent(fake2, reg, policy, memory, config=config)
        agent2.log_trajectories = False
        reply = agent2.chat("explícame qué es un archivo zip, por favor")
        test("English reply is rewritten in Spanish", reply.startswith("Claro"), reply)

        fake3 = FakeLLM([{"_raw": {"error": "HTTP 400: request (10914 tokens) exceeds the available context size (8192 tokens)"}},
                         {"role": "assistant", "content": "Listo, ya está."}])
        agent3 = AgentIaUsbAgent(fake3, reg, policy, memory, config=config)
        agent3.log_trajectories = False
        agent3.messages = [{"role": "user", "content": "lista"}, {"role": "assistant", "content": "", "tool_calls": []},
                           {"role": "tool", "tool_call_id": "x", "name": "find_files", "content": "C:\\x\\file.txt\n" * 400}]
        reply = agent3.chat("y ahora?")
        test("context overflow: compacts and retries", reply == "Listo, ya está." and len(agent3.messages[2]["content"]) < 700, reply)

        fake4 = FakeLLM([{"role": "assistant", "content": "Anotado, lo tendré presente."}])
        agent4 = AgentIaUsbAgent(fake4, reg, policy, memory, config=config)
        agent4.log_trajectories = False
        agent4.chat("please remember that my backup folder is E:\\Respaldos")
        test("remember safety net stores the fact", any("E:\\Respaldos" in e.content for e in memory.preferences))

        cut = {"role": "assistant", "content": "", "_finish": "length",
               "tool_calls": [tool_call("write_file", path=str(sandbox / "big.html"), content="<html>...")]}
        fake5 = FakeLLM([cut, {"role": "assistant", "content": "Lo escribiré por partes."}])
        agent5 = AgentIaUsbAgent(fake5, reg, policy, memory, config=config)
        agent5.log_trajectories = False
        agent5.chat("hazme una pagina")
        fed_back = [m for m in fake5.requests[-1] if m.get("role") == "tool"]
        test("cut-off reply is not executed", not (sandbox / "big.html").exists() and fed_back and "por partes" in fed_back[0]["content"])
        test("'modo codigo' switches mode", agent5.chat("modo codigo").startswith("Modo código") and agent5.mode == "codigo")
        fake6 = FakeLLM([{"role": "assistant", "content": "Hecho."}])
        agent6 = AgentIaUsbAgent(fake6, reg, policy, memory, config=config)
        agent6.mode = "seguridad"
        agent6.chat("revisa mi pc")
        test("mode guidance sent to the model", "MODO SEGURIDAD" in fake6.requests[0][-1]["content"])
        test("network state sent to the model", "Internet: APAGADO" in fake6.requests[0][-1]["content"])
        agent6.set_network(True)
        test("'red on' enables web tools", policy.check("fetch_url", {"url": "https://x.y"})["allowed"])
        agent6.set_network(False)
        disk_cfg = json.loads((AGENT_IA_USB_ROOT / "06_CONFIG" / "agent.json").read_text(encoding="utf-8"))
        test("internet is never saved as ON in agent.json", disk_cfg.get("web_mode", "OFFLINE") == "OFFLINE", disk_cfg.get("web_mode"))
        test("a new session starts offline", PolicyEngine(config).network_mode == "OFFLINE")

        from orchestrator.agent import instant_answer
        from datetime import datetime as _dt
        at = _dt(2026, 10, 3, 17, 5)
        test("instant: time", instant_answer("¿Qué hora es?", at) == "Son las 17:05.")
        test("instant: date", instant_answer("que dia es hoy", at) == "Hoy es sábado 3 de octubre de 2026.", str(instant_answer("que dia es hoy", at)))
        test("instant: free space", (instant_answer("cuanto espacio me queda", at) or "").startswith("Espacio en tus unidades"))
        test("instant: greeting", instant_answer("hola") == "¡Hola! ¿Qué hago por ti?")
        test("instant: real requests go to the model", instant_answer("mueve los pdf de descargas a documentos") is None
             and instant_answer("hola, mueve los pdf") is None)
        fake8 = FakeLLM([])
        agent8 = AgentIaUsbAgent(fake8, reg, policy, memory, config=config)
        agent8.log_trajectories = False
        test("instant answer needs no model call", agent8.chat("hora").startswith("Son las") and not fake8.requests)

        from orchestrator.agent import unsupported_claim
        test("claim without action detected", unsupported_claim("Listo: moví 20 archivos.", []) == "moví")
        test("claim backed by a tool passes", unsupported_claim("Listo: moví 20 archivos.", [{"tool": "move_files", "success": True}]) is None)
        test("descriptions don't trigger", unsupported_claim("Hay 300 archivos creados por Windows.", []) is None)
        test("'he creado' without a file is caught", unsupported_claim("He creado un script útil.", [{"tool": "run_python", "success": False}]) == "he creado")
        test("'he leído' without reading is caught", unsupported_claim("He leído el archivo y dice hola.", []) is not None)
        from orchestrator.agent import mentioned_files, missing_action
        test("requested quarantine without the tool is caught",
             missing_action("revisa la carpeta y pon en cuarentena los que no tengan firma", [{"tool": "security_scan"}]) is not None)
        test("quarantine already called is fine",
             missing_action("pon en cuarentena el archivo", [{"tool": "quarantine", "success": False}]) is None)
        test("a question about quarantine is not an order", missing_action("¿qué es la cuarentena?", []) is None)
        test("file paths found in the request", mentioned_files(f"resume {src / 'note_000.txt'} por favor") == [str(src / "note_000.txt")])
        (sandbox / "orden").mkdir()
        (sandbox / "orden" / "a.jpg").write_text("x")
        fake7 = FakeLLM([{"role": "assistant", "content": "Listo: ordené todo en carpetas."},
                         {"role": "assistant", "content": "", "tool_calls": [tool_call(
                             "move_files", source_dir=str(sandbox / "orden"), pattern="*.jpg", destination_dir=str(sandbox / "orden" / "Imagenes"))]},
                         {"role": "assistant", "content": "Ahora sí: moví 1 imagen a Imagenes."}])
        agent7 = AgentIaUsbAgent(fake7, reg, policy, memory, config=config)
        agent7.log_trajectories = False
        reply = agent7.chat("ordena esa carpeta")
        test("false claim is challenged until done", (sandbox / "orden" / "Imagenes" / "a.jpg").exists() and reply.startswith("Ahora sí"), reply)

        section("Model registry")
        from core.models import _slug
        reg_m = config.model_registry
        ids = [m.id for m in config.list_models()]
        smart_id = config.resolve_model_id("smart")
        test("registry finds the smart model", bool(smart_id) and "qwen" in smart_id, str(ids))
        test("alias smart -> a qwen variant", bool(smart_id) and smart_id in ids, str(smart_id))
        test("index picks a model", reg_m.get("1") is not None)
        test("unknown model -> None", reg_m.get("no-such-model-xyz") is None)
        gem = reg_m.get("gemma")
        test("gemma pairs its vision projector", bool(gem and gem.vision and gem.mmproj))
        test("gguf context is read", bool(gem and gem.context and gem.context > 0))
        tc = config.tier_config("smart")
        test("any model yields a tier dict", bool(tc.get("model_file") and tc.get("context")))
        test("_slug strips the quant suffix", _slug("Qwen3.5-9B-Q4_K_M.gguf") == "qwen3.5-9b")
        test("_slug maps MTP to the base id", _slug("Qwen3.5-9B-MTP-Q4_K_M.gguf") == "qwen3.5-9b")

        section("Autonomous mode & slash commands")
        pol2 = PolicyEngine(config)
        reg2 = ToolRegistry(policy_engine=pol2, memory=memory, confirm=lambda *a: False)
        ag = AgentIaUsbAgent(FakeLLM([]), reg2, pol2, memory, config=config)
        ag.log_trajectories = False
        test("by default a delete would be blocked", not reg2._ask("borrar"))
        ag._handle_command("/libre")
        test("autonomous mode approves without asking", pol2.auto_approve and reg2._ask("borrar"))
        ag._handle_command("/libre off")
        test("autonomous off restores confirmations", not pol2.auto_approve)
        test("/plan with no active plan", "No hay" in ag._cmd_plan())
        test("leading slash is tolerated", ag._handle_command("/estado") is not None)

        section("Live model (optional)")
        from core.llm_client import LLMClient
        llm = LLMClient(config.server_url)
        if os.environ.get("AGENT_IA_USB_LIVE") != "1":
            print("  [SKIP] set AGENT_IA_USB_LIVE=1 to ping the model (it would disturb a running Agent IA USB's cache)")
        elif llm.is_server_running():
            r = llm.chat([{"role": "user", "content": "Say OK"}], max_tokens=5)
            test("model server answers", "choices" in r, str(r)[:100])
        else:
            print("  [SKIP] model server not running (run eval_agent.py for model tests)")
    finally:
        shutil.rmtree(sandbox, ignore_errors=True)

    print(f"\n{'=' * 60}\nUNIT TESTS: {PASS}/{PASS + FAIL} PASSED, {FAIL} FAILED\n{'=' * 60}")
    return FAIL == 0


if __name__ == "__main__":
    sys.exit(0 if main() else 1)
