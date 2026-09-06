from __future__ import annotations
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import shutil
import sys
import uuid
from . import __version__

from .transactions import atomic_json, backup_root, digest
from .native import assert_game_closed

MOD_NAME = "RazorbeamDisplay"
FILES = ("Mods/RazorbeamDisplay.tmod", "Mods/enabled.json",
         "ModConfigs/RazorbeamDisplay_DisplayConfig.json", "config.json")


def install_dir(raw) -> Path:
    if not str(raw).strip():
        raise ValueError("Select the tModLoader installation.")
    path = Path(os.path.expandvars(str(raw).strip().strip('"'))).expanduser()
    if path.is_file(): path = path.parent
    path = path.resolve()
    if not (path / "tModLoader.dll").is_file():
        raise ValueError("Choose the tModLoader folder, tModLoader.dll, or start-tModLoader.bat.")
    return path


def documents_dir() -> Path:
    try:
        import winreg
        key = r"Software\Microsoft\Windows\CurrentVersion\Explorer\User Shell Folders"
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key) as handle:
            value = winreg.QueryValueEx(handle, "Personal")[0]
            return Path(os.path.expandvars(value)).expanduser()
    except OSError:
        return Path(os.environ.get("USERPROFILE", Path.home())) / "Documents"


def save_dir(installation=None) -> Path:
    override = os.environ.get("RAZORBEAM_TML_SAVE")
    if override:
        return Path(override).resolve()
    if installation:
        log = Path(installation) / "tModLoader-Logs" / "client.log"
        try:
            text = log.read_text(encoding="utf-8-sig", errors="replace")
            matches = re.findall(r"Saves Are Located At:\s*(.+?)\s*$", text, re.MULTILINE)
            if matches:
                return Path(matches[-1].strip().strip('"')).expanduser().resolve()
        except OSError:
            pass
    return documents_dir() / "My Games" / "Terraria" / "tModLoader"


def bundled_mod() -> Path:
    root = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parents[1]))
    path = root / "tmod" / "RazorbeamDisplay.tmod"
    if not path.is_file(): raise RuntimeError("Bundled tModLoader display bridge is missing.")
    return path


def version(root: Path) -> str:
    try:
        text = (root / "tModLoader-Logs" / "client.log").read_text(encoding="utf-8-sig", errors="replace")
        matches = re.findall(r"Starting tModLoader client [^+\r\n]+\+([0-9.]+)", text)
        if matches:
            return ".".join(str(int(part)) for part in matches[-1].split("."))
    except (OSError, ValueError):
        pass
    try:
        text = (root / "tModLoader.deps.json").read_text(encoding="utf-8-sig")
        match = re.search(r'"tModLoader/([^"/]+)"', text)
        if match: return match.group(1)
    except OSError: pass
    return "installed"


def paths(installation=None):
    root = save_dir(installation)
    return root, {name: root / name for name in FILES}


def assert_closed(installation: Path):
    runtime = installation / "dotnet" / "dotnet.exe"
    if runtime.is_file():
        assert_game_closed(runtime, "tModLoader")


def analyze(raw):
    root = install_dir(raw); saves, files = paths(root)
    config = {}
    try: config = json.loads(files[FILES[2]].read_text(encoding="utf-8-sig"))
    except (OSError, ValueError): pass
    installed = files[FILES[0]].is_file()
    return {"state": "Display bridge installed" if installed else "Ready for display bridge",
            "version": version(root), "compatible": True, "verified": installed,
            "cap": int(config.get("Width", 8192)), "sha256": digest(files[FILES[0]]) if installed else "Not installed",
            "exe": str(root), "target": "tmodloader", "save_dir": str(saves)}


def _snapshot(raw_root, installation, operation, emit):
    base = backup_root(raw_root, installation / "tModLoader.dll")
    saves = save_dir(installation)
    if base == saves or saves in base.parents:
        raise ValueError("Choose a backup location outside the tModLoader save directory.")
    folder = base / "RazorbeamTModLoaderBackups" / (datetime.now().strftime("%Y-%m-%d_%H-%M-%S") + "_" + uuid.uuid4().hex[:12])
    folder.mkdir(parents=True, exist_ok=False)
    saves, current = paths(installation); records = []
    emit("Creating mandatory tModLoader configuration backup…")
    for name, source in current.items():
        record = {"path": name, "existed": source.is_file()}
        if source.is_file():
            target = folder / name; target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target); record["sha256"] = digest(target)
            if digest(source) != record["sha256"]: raise RuntimeError("tModLoader backup verification failed.")
        records.append(record)
    manifest = {"schema": 2, "patcher_version": __version__, "target": "tmodloader",
                "operation": operation, "created": datetime.now(timezone.utc).isoformat(),
                "original_path": str(installation), "original_state": "tModLoader configuration",
                "original_version": version(installation), "files": records, "status": "backup_verified"}
    atomic_json(folder / "manifest.json", manifest)
    emit("Backup verified: " + str(folder))
    return folder, manifest


def _load_enabled(path):
    if not path.is_file(): return []
    try: value = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError) as exc: raise RuntimeError("tModLoader enabled.json is unreadable; patch aborted.") from exc
    if not isinstance(value, list): raise RuntimeError("tModLoader enabled.json has an unsupported structure; patch aborted.")
    return value


def _load_object(path):
    if not path.is_file(): return {}
    try: value = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError) as exc: raise RuntimeError("tModLoader config.json is unreadable; patch aborted.") from exc
    if not isinstance(value, dict): raise RuntimeError("tModLoader config.json has an unsupported structure; patch aborted.")
    return value


def _restore_snapshot(folder, manifest, current):
    for record in manifest["files"]:
        target = current[record["path"]]
        if record["existed"]:
            saved = folder / record["path"]
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(saved, target)
            if digest(target) != record["sha256"]: raise RuntimeError("tModLoader rollback verification failed.")
        else:
            target.unlink(missing_ok=True)


def patch(raw, raw_root, settings, emit=lambda message: None):
    installation = install_dir(raw)
    assert_closed(installation)
    folder, manifest = _snapshot(raw_root, installation, "patch", emit)
    saves, files = paths(installation)
    stage=files[FILES[0]].with_suffix(".tmod.tmp")
    try:
        emit("Installing client-side tModLoader display bridge…")
        enabled = _load_enabled(files[FILES[1]])
        startup = _load_object(files[FILES[3]])
        (saves / "Mods").mkdir(parents=True, exist_ok=True)
        (saves / "ModConfigs").mkdir(parents=True, exist_ok=True)
        shutil.copy2(bundled_mod(),stage)
        if digest(stage)!=digest(bundled_mod()):raise RuntimeError("Display bridge staging verification failed.")
        os.replace(stage,files[FILES[0]])
        if MOD_NAME not in enabled: enabled.append(MOD_NAME)
        atomic_json(files[FILES[1]], enabled)
        config = {"Enabled": True, "Width": settings["Width"], "Height": settings["Height"],
                  "WindowX": settings["WindowX"], "WindowY": settings["WindowY"],
                  "WindowMode": settings["Mode"], "StableTitle": bool(settings["StableTitle"]),
                  "CenteredUi": bool(settings["UiEnabled"]), "UiX": settings["UiX"],
                  "UiY": settings["UiY"], "UiWidth": settings["UiWidth"],
                  "UiHeight": settings["UiHeight"], "PreventMinimize": bool(settings["PreventMinimize"]),
                  "SkipSplash": bool(settings.get("SkipSplash", False))}
        atomic_json(files[FILES[2]], config)
        startup.update({"DisplayWidth": settings["Width"], "DisplayHeight": settings["Height"],
                        "DisplayScreen": settings.get("DisplayScreen", ""),
                        "Fullscreen": settings["Mode"] == 2, "WindowMaximized": False,
                        "WindowBorderless": False, "QuickLaunch": bool(settings.get("SkipSplash", False)),
                        "ThrottleWhenInactive": False, "RemoveForcedMinimumZoom": True,
                        "Zoom": 1.0, "UIScale": 1.0, "ResetDefaultUIScale": False})
        atomic_json(files[FILES[3]], startup)
        manifest.update(status="completed", requested_settings=settings,
                        installed_sha256=digest(files[FILES[0]]))
        atomic_json(folder / "manifest.json", manifest)
    except BaseException:
        stage.unlink(missing_ok=True)
        _restore_snapshot(folder, manifest, files)
        raise
    finally: stage.unlink(missing_ok=True)
    emit("tModLoader display configuration installed and verified.")
    result = analyze(installation)
    result.update(backup=str(folder), settings=settings, centered=bool(settings["UiEnabled"]))
    return result


def list_backups(raw_root, raw=None):
    base = Path(raw_root) / "RazorbeamTModLoaderBackups"; records=[]
    installation = install_dir(raw) if raw else None
    if not base.is_dir(): return records
    for path in base.glob("*/manifest.json"):
        try:
            data=json.loads(path.read_text(encoding="utf-8-sig"))
            if data.get("schema") != 2: continue
            if installation and os.path.normcase(data["original_path"]) != os.path.normcase(str(installation)): continue
            records.append({**data,"folder":str(path.parent),"original_sha256":"multiple files"})
        except (OSError,ValueError,KeyError): pass
    return sorted(records,key=lambda d:d.get("created",""),reverse=True)


def restore(raw, raw_root, selected, emit=lambda message: None):
    installation=install_dir(raw); source=Path(selected).resolve()
    assert_closed(installation)
    manifest=json.loads((source/"manifest.json").read_text(encoding="utf-8-sig"))
    if manifest.get("schema") != 2 or os.path.normcase(manifest["original_path"]) != os.path.normcase(str(installation)):
        raise ValueError("This backup belongs to a different tModLoader installation.")
    saves,current=paths(installation)
    for record in manifest["files"]:
        if record["existed"]:
            saved=source/record["path"]
            if not saved.is_file() or digest(saved)!=record["sha256"]:raise ValueError("Backup hash mismatch. Restore refused.")
    folder,current_manifest=_snapshot(raw_root,installation,"restore",emit)
    try:
        _restore_snapshot(source,manifest,current)
        current_manifest["status"]="completed"
        atomic_json(folder/"manifest.json",current_manifest)
    except BaseException:
        _restore_snapshot(folder,current_manifest,current)
        raise
    emit("tModLoader configuration restored and verified.")
    return {"backup":str(folder),"sha256":"multiple files","exe":str(installation),"restored":True,"target":"tmodloader"}


def launch_check(raw, raw_root):
    installation=install_dir(raw); backup_root(raw_root)
    if not list_backups(raw_root,installation):
        raise ValueError("No verified backup for this tModLoader configuration. Use Patch & Launch tModLoader first.")
    return {"exe":str(installation),"launch":True,"target":"tmodloader"}
