from __future__ import annotations
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import uuid
from . import __version__
from .engine import run_engine
from .native import assert_game_closed, GameLock

def game_config_path():
    override = os.environ.get("RAZORBEAM_TERRARIA_CONFIG")
    if override: return Path(override).expanduser().resolve()
    try:
        import winreg
        key=r"Software\Microsoft\Windows\CurrentVersion\Explorer\User Shell Folders"
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER,key) as handle:documents=Path(os.path.expandvars(winreg.QueryValueEx(handle,"Personal")[0])).expanduser()
    except OSError:documents=Path(os.environ.get("USERPROFILE",Path.home()))/"Documents"
    return documents / "My Games" / "Terraria" / "config.json"

def digest(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""): h.update(chunk)
    return h.hexdigest()

def atomic_json(path, data):
    temp = Path(path).with_name(Path(path).name + ".tmp")
    with temp.open("w", encoding="utf-8") as stream:
        json.dump(data, stream, indent=2); stream.write("\n"); stream.flush(); os.fsync(stream.fileno())
    os.replace(temp, path)

def game_exe(raw):
    if not str(raw).strip(): raise ValueError("Select the Terraria installation.")
    path = Path(os.path.expandvars(str(raw).strip().strip('"'))).expanduser()
    if path.is_dir(): path /= "Terraria.exe"
    path = path.resolve()
    if not path.is_file() or path.name.casefold() != "terraria.exe":
        raise ValueError("Choose Terraria.exe or its installation folder.")
    return path

def backup_root(raw, exe=None):
    if not str(raw).strip(): raise ValueError("A backup location is mandatory. Nothing has been patched.")
    path = Path(os.path.expandvars(str(raw).strip().strip('"'))).expanduser().resolve()
    if not path.is_dir(): raise ValueError("Backup location must be an existing folder.")
    if exe:
        game_dir = Path(exe).resolve().parent
        if path == game_dir or game_dir in path.parents:
            raise ValueError("Choose a backup location outside the Terraria installation so Steam updates cannot remove it.")
    probe = path / (".razorbeam-write-test-" + uuid.uuid4().hex + ".tmp")
    try:
        with probe.open("xb") as stream:
            stream.write(b"backup-location-check"); stream.flush(); os.fsync(stream.fileno())
    except OSError as exc:
        raise ValueError("Backup location is not writable: " + str(exc)) from exc
    finally:
        try: probe.unlink(missing_ok=True)
        except OSError: pass
    return path

def _backup_base(root):
    return Path(root) / "RazorbeamTerrariaBackups"

def _installation_key(exe):
    return os.path.normcase(str(Path(exe).resolve()))

def _clean_manifest(manifest):
    return bool(manifest.get("clean_executable")) or manifest.get("original_state") == "Vanilla"

def _registry_path(root):
    return _backup_base(root) / "clean-backups.json"

def _load_registry(root):
    path = _registry_path(root)
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
        if data.get("schema") == 1 and isinstance(data.get("installations"), dict): return data
    except (OSError, ValueError, AttributeError): pass
    return {"schema": 1, "installations": {}}

def _remember_clean_backup(root, exe, folder, manifest):
    base = _backup_base(root);base.mkdir(parents=True, exist_ok=True)
    registry = _load_registry(root)
    registry["installations"][_installation_key(exe)] = {
        "folder": str(Path(folder).resolve()), "sha256": manifest["original_sha256"],
        "version": manifest.get("original_version", "Unknown"), "created": manifest.get("created", "")}
    atomic_json(_registry_path(root), registry)

def find_clean_backup(root, exe, sha256=None):
    root = Path(root);exe = Path(exe).resolve();registry = _load_registry(root)
    entry = registry["installations"].get(_installation_key(exe), {})
    candidates = []
    if entry.get("folder"): candidates.append(Path(entry["folder"]))
    base = _backup_base(root)
    if base.is_dir():
        candidates.extend(path.parent for path in sorted(base.glob("*/manifest.json"), key=lambda item: item.stat().st_mtime_ns, reverse=True))
    seen = set()
    for folder in candidates:
        key = os.path.normcase(str(folder.resolve()))
        if key in seen: continue
        seen.add(key)
        try:
            manifest = read_backup(folder, exe)
            if not _clean_manifest(manifest): continue
            if sha256 and manifest.get("original_sha256") != sha256: continue
            _remember_clean_backup(root, exe, folder, manifest)
            return folder.resolve(), manifest
        except (OSError, ValueError, KeyError): continue
    return None

def make_backup(exe, root, analysis, operation, emit):
    root = backup_root(root, exe)
    if shutil.disk_usage(root).free < exe.stat().st_size * 2 + 1024 * 1024:
        raise RuntimeError("Insufficient free space at the backup location.")
    folder = root / "RazorbeamTerrariaBackups" / (datetime.now().strftime("%Y-%m-%d_%H-%M-%S") + "_" + uuid.uuid4().hex[:12])
    folder.mkdir(parents=True, exist_ok=False)
    original = digest(exe)
    emit("Creating verified clean Terraria backup…")
    with exe.open("rb") as source, (folder / "Terraria.exe").open("xb") as target:
        shutil.copyfileobj(source, target, 1024 * 1024)
        target.flush(); os.fsync(target.fileno())
    shutil.copystat(exe, folder / "Terraria.exe")
    if digest(folder / "Terraria.exe") != original or digest(exe) != original:
        raise RuntimeError("Backup verification failed or Terraria changed while being copied. Original left unchanged.")
    manifest = {"schema": 1, "patcher_version": __version__, "operation": operation,
                "created": datetime.now(timezone.utc).isoformat(), "original_path": str(exe),
                "original_sha256": original, "original_state": analysis.get("state", "Unknown"),
                "original_version": analysis.get("version", "Unknown"), "backup_file": "Terraria.exe",
                "clean_executable": True, "status": "backup_verified"}
    config_path = game_config_path()
    manifest.update(config_path=str(config_path), config_existed=config_path.is_file())
    if config_path.is_file():
        config_hash = digest(config_path); shutil.copy2(config_path, folder / "config.json")
        if digest(folder / "config.json") != config_hash: raise RuntimeError("Terraria configuration backup verification failed.")
        manifest.update(config_backup_file="config.json", config_sha256=config_hash)
    atomic_json(folder / "manifest.json", manifest)
    emit("Backup verified: " + str(folder))
    return folder, manifest

def ensure_clean_backup(exe, root, analysis, emit):
    current = digest(exe)
    existing = find_clean_backup(root, exe, current if analysis.get("state") == "Vanilla" else None)
    if existing:
        emit("Using verified clean backup: " + str(existing[0]))
        return existing[0], existing[1], False
    if analysis.get("state") != "Vanilla":
        raise ValueError("No verified clean Terraria executable backup exists at this location. Restore a clean backup or verify Terraria in Steam once, then patch again.")
    folder, manifest = make_backup(exe, root, analysis, "clean", emit)
    _remember_clean_backup(root, exe, folder, manifest)
    return folder, manifest, True

def read_backup(folder, exe=None):
    folder = Path(folder).resolve()
    manifest = json.loads((folder / "manifest.json").read_text(encoding="utf-8-sig"))
    if manifest.get("schema") != 1 or manifest.get("backup_file") != "Terraria.exe":
        raise ValueError("Unsupported backup manifest.")
    if exe and os.path.normcase(str(Path(manifest["original_path"]).resolve())) != os.path.normcase(str(exe.resolve())):
        raise ValueError("This backup belongs to a different installation. Select its original Terraria folder.")
    backup = folder / "Terraria.exe"
    if backup.is_symlink() or not backup.is_file() or digest(backup) != manifest.get("original_sha256"):
        raise ValueError("Backup hash mismatch. Restore refused; current game left unchanged.")
    if manifest.get("config_existed"):
        config_backup = folder / manifest.get("config_backup_file", "config.json")
        if not config_backup.is_file() or digest(config_backup) != manifest.get("config_sha256"):
            raise ValueError("Configuration backup hash mismatch. Restore refused.")
    return manifest

def configured_values(settings):
    mode = int(settings["Mode"])
    return {"DisplayWidth": int(settings["Width"]), "DisplayHeight": int(settings["Height"]),
            "DisplayScreen": str(settings.get("DisplayScreen", "")),
            "Fullscreen": mode == 2, "WindowMaximized": False,
            # The patched executable applies exact popup bounds itself. Leaving this
            # false prevents unpatched Terraria's one-monitor borderless clamp first.
            "WindowBorderless": False,
            "QuickLaunch": bool(settings.get("SkipSplash", False)),
            "ThrottleWhenInactive": False}

def write_game_config(settings):
    path = game_config_path(); path.parent.mkdir(parents=True, exist_ok=True)
    try: data = json.loads(path.read_text(encoding="utf-8-sig")) if path.is_file() else {}
    except (OSError, ValueError): raise RuntimeError("Terraria config.json is unreadable; patch aborted before executable replacement.")
    if not isinstance(data, dict): raise RuntimeError("Terraria config.json has an unsupported structure.")
    data.update(configured_values(settings)); atomic_json(path, data)
    check = json.loads(path.read_text(encoding="utf-8-sig"))
    for key, value in configured_values(settings).items():
        if check.get(key) != value: raise RuntimeError("Terraria configuration verification failed: " + key)
    return path

def restore_game_config(manifest):
    path = Path(manifest.get("config_path") or game_config_path())
    path.parent.mkdir(parents=True, exist_ok=True)
    if manifest.get("config_existed"):
        source = Path(manifest["folder"]) / manifest.get("config_backup_file", "config.json")
        temp = path.with_name(path.name + ".razorbeam-restore.tmp"); shutil.copy2(source, temp); os.replace(temp, path)
        if digest(path) != manifest.get("config_sha256"): raise RuntimeError("Restored configuration hash mismatch.")
    elif "config_existed" in manifest:
        path.unlink(missing_ok=True)

def list_backups(root, exe=None):
    base = Path(root) / "RazorbeamTerrariaBackups"
    records = []
    if not base.is_dir(): return records
    for path in base.glob("*/manifest.json"):
        try:
            data = json.loads(path.read_text(encoding="utf-8-sig"))
            if exe and os.path.normcase(str(Path(data["original_path"]).resolve())) != os.path.normcase(str(exe.resolve())): continue
            if not _clean_manifest(data): continue
            records.append({**data, "folder": str(path.parent)})
        except (OSError, ValueError, KeyError): continue
    return sorted(records, key=lambda d: d.get("created", ""), reverse=True)

def snapshot_game_config():
    path = game_config_path()
    return path, path.is_file(), path.read_bytes() if path.is_file() else b""

def restore_config_snapshot(snapshot):
    path, existed, content = snapshot;path.parent.mkdir(parents=True, exist_ok=True)
    if existed:
        temp = path.with_name(path.name + ".razorbeam-restore.tmp")
        with temp.open("wb") as stream: stream.write(content);stream.flush();os.fsync(stream.fileno())
        os.replace(temp, path)
    else: path.unlink(missing_ok=True)

def install_stage(exe, stage, expected, source_sha256, recovery_file, recovery_sha256, emit):
    assert_game_closed(exe)
    if digest(exe) != source_sha256: raise RuntimeError("Terraria changed during staging. Commit aborted; retry analysis.")
    if digest(stage) != expected: raise RuntimeError("Staging verification failed; original left unchanged.")
    emit("COMMIT: verified clean backup ready; installing one atomic executable replacement…")
    installed = False
    try:
        os.replace(stage, exe)
        installed = True
        if digest(exe) != expected: raise RuntimeError("Installed executable hash mismatch.")
    except BaseException:
        # If replacement happened but verification failed, recover to the verified clean executable.
        if installed and exe.exists() and digest(exe) != recovery_sha256:
            recovery = exe.with_name(".razorbeam-recovery-" + uuid.uuid4().hex + ".tmp")
            shutil.copy2(recovery_file, recovery)
            if digest(recovery) == recovery_sha256: os.replace(recovery, exe)
        raise

def patch(raw_exe, raw_root, settings, emit=lambda message: None):
    exe = game_exe(raw_exe); root = backup_root(raw_root, exe)
    assert_game_closed(exe)
    with GameLock(exe):
        emit("Inspecting Terraria IL…")
        analysis = run_engine("analyze", exe)
        folder, manifest, backup_created = ensure_clean_backup(exe, root, analysis, emit)
        source_sha = digest(exe)
        stage = exe.with_name(".razorbeam-stage-" + uuid.uuid4().hex + ".exe")
        config = exe.with_name(".razorbeam-settings-" + uuid.uuid4().hex + ".json")
        atomic_json(config, settings)
        config_snapshot = snapshot_game_config()
        try:
            clean_exe = folder / "Terraria.exe"
            if digest(clean_exe) != manifest["original_sha256"]:
                raise RuntimeError("Verified clean backup changed before staging. Patch refused.")
            emit("Building custom resolution patch from the verified clean executable; installed game still unchanged…")
            result = run_engine("patch", clean_exe, stage, config)
            if not result.get("verified"): raise RuntimeError("Engine did not verify the staged patch.")
            new_hash = digest(stage)
            emit("COMMIT: applying verified Terraria display configuration…")
            write_game_config(settings)
            try: install_stage(exe, stage, new_hash, source_sha, folder / "Terraria.exe", manifest["original_sha256"], emit)
            except BaseException:
                restore_config_snapshot(config_snapshot)
                raise
            result.update(backup=str(folder), clean_backup_created=backup_created, sha256=new_hash, exe=str(exe))
            emit("Successfully patched and verified Terraria.")
            return result
        finally:
            stage.unlink(missing_ok=True)
            config.unlink(missing_ok=True)

def restore(raw_exe, raw_root, selected, emit=lambda message: None):
    exe = game_exe(raw_exe); root = backup_root(raw_root, exe)
    assert_game_closed(exe)
    with GameLock(exe):
        source = read_backup(selected, exe)
        if not _clean_manifest(source): raise ValueError("Only a verified clean Terraria executable can be restored.")
        source_sha = digest(exe);config_snapshot = snapshot_game_config()
        stage = exe.with_name(".razorbeam-restore-" + uuid.uuid4().hex + ".exe")
        try:
            shutil.copy2(Path(selected) / "Terraria.exe", stage)
            selected_manifest = {**source, "folder": str(Path(selected).resolve())}
            emit("COMMIT: restoring the selected Terraria configuration snapshot…")
            restore_game_config(selected_manifest)
            try: install_stage(exe, stage, source["original_sha256"], source_sha, Path(selected) / "Terraria.exe", source["original_sha256"], emit)
            except BaseException:
                restore_config_snapshot(config_snapshot)
                raise
            emit("Successfully restored and verified the selected backup.")
            return {"backup": str(Path(selected).resolve()), "sha256": source["original_sha256"], "exe": str(exe), "restored": True}
        finally: stage.unlink(missing_ok=True)
