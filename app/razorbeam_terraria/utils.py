from __future__ import annotations
import json
import os
from pathlib import Path

APP_NAME = "Razorbeam Terraria Patcher"
WINDOW_TITLE = "Razorbeam All-in-One Resolution Patcher for Terraria"
APP_SLUG = "RazorbeamTerrariaAIORP"
COLLECTIBLES_DIR = Path(os.environ.get("LOCALAPPDATA", Path.home())) / "RazorbeamCollectibles"
APP_RUNTIME_DIR = Path(os.environ.get("RAZORBEAM_TEST_STATE", COLLECTIBLES_DIR / APP_SLUG))
SHARED_COLOR_SCHEMES_DIR = Path(os.environ.get("RAZORBEAM_TEST_SCHEMES", COLLECTIBLES_DIR / "RazorbeamColorSchemes"))
APP_STATE_PATH = APP_RUNTIME_DIR / "app_state.json"
DEBUG_LOG_PATH = APP_RUNTIME_DIR / "debug.log"

def default_output_dir() -> Path:
    return Path(os.environ.get("RAZORBEAM_TEST_OUTPUT", COLLECTIBLES_DIR / "RazorbeamTerrariaAIORP"))

def ensure_runtime_dirs():
    APP_RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
    SHARED_COLOR_SCHEMES_DIR.mkdir(parents=True, exist_ok=True)
    default_output_dir().mkdir(parents=True, exist_ok=True)

def load_app_state() -> dict:
    try:
        value = json.loads(APP_STATE_PATH.read_text(encoding="utf-8-sig"))
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}

def save_app_state(data: dict):
    ensure_runtime_dirs()
    temp = APP_STATE_PATH.with_suffix(".tmp")
    temp.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    os.replace(temp, APP_STATE_PATH)

def desktop_dir() -> Path:
    # Qt uses the Windows known folder, including redirected/OneDrive desktops.
    from PySide6.QtCore import QStandardPaths
    value = QStandardPaths.writableLocation(QStandardPaths.StandardLocation.DesktopLocation)
    return Path(value) if value else Path(os.environ.get("USERPROFILE", Path.home())) / "Desktop"
