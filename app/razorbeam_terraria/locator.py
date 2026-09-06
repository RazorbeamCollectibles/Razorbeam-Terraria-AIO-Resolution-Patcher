from __future__ import annotations
import ctypes
from pathlib import Path
import re
import time

ALIASES = ("Program Files (x86)/Steam", "Program Files/Steam", "Steam", "SteamLibrary",
           "Steam Games", "Games/Steam", "Games/SteamLibrary", "Games/Steam Games")

def library_paths(text: str) -> list[str]:
    # VDF quoted values escape backslashes. Support old numeric-path and new path-key formats.
    pairs = re.findall(r'"([^"\\]+)"\s+"((?:\\.|[^"\\])*)"', text)
    return [v.replace("\\\\", "\\").replace('\\"', '"') for k, v in pairs
            if k.casefold() == "path" or (k.isdecimal() and re.match(r"^[A-Za-z]:", v))]

def fixed_drives() -> list[Path]:
    mask = ctypes.windll.kernel32.GetLogicalDrives()
    roots = [Path(f"{chr(65+i)}:/") for i in range(26) if mask & (1 << i)
             and ctypes.windll.kernel32.GetDriveTypeW(f"{chr(65+i)}:\\") == 3]
    return sorted(roots, key=lambda p: (p.drive.casefold() != "c:", p.drive))

def locate_all(emit=lambda value: None) -> dict[str, str | None]:
    seen = set()
    found = {"terraria": None, "tmodloader": None}
    def check(root):
        key = str(root).casefold()
        if key in seen: return
        seen.add(key)
        candidates = (("terraria", root / "steamapps/common/Terraria/Terraria.exe"),
                      ("terraria", root / "Terraria.exe"),
                      ("tmodloader", root / "steamapps/common/tModLoader/tModLoader.dll"),
                      ("tmodloader", root / "tModLoader.dll"))
        for kind, candidate in candidates:
            if not found[kind] and candidate.is_file():
                found[kind] = str(candidate.parent.resolve() if kind == "tmodloader" else candidate.resolve())
        vdf = root / "steamapps/libraryfolders.vdf"
        try:
            if vdf.is_file():
                for path in library_paths(vdf.read_text(encoding="utf-8-sig")):
                    check(Path(path))
        except (OSError, UnicodeError): pass
    # Explicit C: aliases first. All probing stays in this disposable worker process.
    for alias in ALIASES:
        check(Path("C:/") / alias)
    import winreg
    for hive, key, value in ((winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam", "SteamPath"),
                             (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Valve\Steam", "InstallPath")):
        try:
            with winreg.OpenKey(hive, key) as handle:
                check(Path(winreg.QueryValueEx(handle, value)[0]))
        except OSError: pass
    drives = fixed_drives()
    for drive in drives:
        for alias in ALIASES:
            check(drive / alias)
        if all(found.values()): return found
    # Bounded shallow search for renamed library containers; never crawl an entire disk.
    deadline = time.monotonic() + 25
    skip = {"windows", "users", "$recycle.bin", "system volume information", "programdata"}
    for drive in drives:
        try:
            for child in drive.iterdir():
                if time.monotonic() > deadline: return found
                if child.name.casefold() in skip or not child.is_dir() or child.is_symlink(): continue
                check(child)
                for alias in ("Steam", "SteamLibrary", "Steam Games", "Games/Steam"):
                    check(child / alias)
                if all(found.values()): return found
        except OSError: continue
    return found
