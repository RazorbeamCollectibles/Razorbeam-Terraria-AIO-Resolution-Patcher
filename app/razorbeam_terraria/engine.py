from __future__ import annotations
import json
from pathlib import Path
import subprocess
import sys
from .native import OwnedJob

def engine_path():
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parents[1]))
    return base / "engine" / "PatchEngine.exe"

def run_engine(*args, timeout=120):
    path = engine_path()
    if not path.is_file(): raise RuntimeError("Bundled patch engine is missing. Extract the complete release again.")
    job = OwnedJob()
    process = subprocess.Popen([str(path), *map(str, args)], stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                               creationflags=subprocess.CREATE_NO_WINDOW)
    try:
        job.assign(process.pid)
        out, err = process.communicate(timeout=timeout)
        try: result = json.loads(out.decode("utf-8-sig"))
        except ValueError: raise RuntimeError("Patch engine could not run. Windows .NET Framework 4.8 is required. " + err.decode(errors="replace")[-500:])
        if process.returncode or not result.get("compatible"):
            raise RuntimeError(result.get("error", "Executable is unsupported; no patch applied."))
        return result
    finally:
        job.close()
        if process.poll() is None: process.kill()
        process.wait(timeout=5)
