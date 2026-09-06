from __future__ import annotations
import json
from pathlib import Path
import sys
import traceback

_events = None

def send(kind, **data):
    line=json.dumps({"kind": kind, **data}, ensure_ascii=True)
    # File IPC also works in PyInstaller's windowed build, where stdout is None.
    if _events:
        with _events.open("a",encoding="utf-8") as stream:stream.write(line+"\n");stream.flush()
    if sys.stdout is not None:print(line, flush=True)

def main(request_file):
    global _events
    _events=Path(str(request_file)+".events")
    try:
        request = json.loads(Path(request_file).read_text(encoding="utf-8"))
        action = request["action"]
        emit = lambda message: send("progress", message=message)
        from . import transactions as tx
        target = request.get("target", "terraria")
        backend = tx
        if target == "tmodloader":
            from . import tmod as backend
        if action == "locate":
            from .locator import locate_all
            result = locate_all()
        elif action == "analyze":
            if target == "tmodloader": result = backend.analyze(request["exe"])
            else:
                exe = tx.game_exe(request["exe"])
                result = tx.run_engine("analyze", exe)
                result.update(exe=str(exe), sha256=tx.digest(exe))
        elif action == "backups":
            if target == "tmodloader": result = {"backups":backend.list_backups(request["backup_root"],request["exe"])}
            else: result = {"backups":tx.list_backups(request["backup_root"],tx.game_exe(request["exe"]))}
        elif action == "patch":
            result = backend.patch(request["exe"], request["backup_root"], request["settings"], emit)
        elif action == "restore":
            result = backend.restore(request["exe"], request["backup_root"], request["selected"], emit)
        elif action == "launch_check":
            if target == "tmodloader": result=backend.launch_check(request["exe"],request["backup_root"])
            else:
                exe = tx.game_exe(request["exe"])
                root = tx.backup_root(request["backup_root"], exe)
                tx.assert_game_closed(exe)
                report = tx.run_engine("analyze", exe)
                current = tx.digest(exe)
                clean = tx.find_clean_backup(root, exe)
                if not clean: raise ValueError("No verified clean backup for this installation at the chosen location. Use Patch & Launch Terraria from a clean Steam executable first.")
                tx.read_backup(clean[0], exe)
                result = {"exe": str(exe), "sha256": current, "launch": True,
                          "state": report.get("state", "Unknown")}
        else: raise ValueError("Unknown worker action")
        send("result", result=result)
        return 0
    except Exception as exc:
        send("error", message=str(exc), details=traceback.format_exc(), permission=isinstance(exc, PermissionError))
        return 1
