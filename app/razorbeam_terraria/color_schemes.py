from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any

from PySide6 import QtCore, QtWidgets

from . import color_theme
from .utils import APP_RUNTIME_DIR, SHARED_COLOR_SCHEMES_DIR, ensure_runtime_dirs

SCHEMES_DIR = SHARED_COLOR_SCHEMES_DIR
DEFAULT_SCHEME_NAME = "Default"
ACTIVE_SCHEME_STATE_PATH = APP_RUNTIME_DIR / "active_scheme.json"


def scheme_files() -> list[Path]:
    ensure_runtime_dirs()
    return sorted(SCHEMES_DIR.glob("*.json"), key=lambda path: path.stem.lower())


def scheme_path(name: str) -> Path:
    safe = "".join(ch for ch in name if ch not in '<>:"/\\|?*').strip() or DEFAULT_SCHEME_NAME
    return SCHEMES_DIR / f"{safe}.json"


def read_scheme(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8-sig") as handle:
        data = json.load(handle)
    if "colors" not in data and ("ui" in data or "log" in data):
        data = {"colors": data}
    return color_theme.sync_active_colors(data)


def write_scheme(path: Path, data: dict[str, Any]) -> None:
    ensure_runtime_dirs()
    path.parent.mkdir(parents=True, exist_ok=True)
    normalized = color_theme.sync_active_colors(data)
    fd, temp_name = tempfile.mkstemp(suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(normalized, handle, indent=2)
            handle.write("\n")
        os.replace(temp_name, path)
    finally:
        if os.path.exists(temp_name):
            os.unlink(temp_name)


def ensure_default_scheme() -> Path:
    ensure_runtime_dirs()
    path = scheme_path(DEFAULT_SCHEME_NAME)
    if not path.exists():
        write_scheme(path, {"name": DEFAULT_SCHEME_NAME, "colors": color_theme.default_colors()})
    return path


def current_active_scheme_path() -> Path | None:
    try:
        with ACTIVE_SCHEME_STATE_PATH.open("r", encoding="utf-8-sig") as handle:
            data = json.load(handle)
    except (OSError, json.JSONDecodeError):
        return None
    raw_path = data.get("path") if isinstance(data, dict) else None
    if not isinstance(raw_path, str):
        return None
    path = Path(raw_path)
    return path if path.exists() and path.suffix.lower() == ".json" else None


def set_active_scheme_path(path: Path) -> None:
    ensure_runtime_dirs()
    ACTIVE_SCHEME_STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    data = {"path": str(path.resolve()), "name": path.stem}
    fd, temp_name = tempfile.mkstemp(suffix=".tmp", dir=ACTIVE_SCHEME_STATE_PATH.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(data, handle, indent=2)
            handle.write("\n")
        os.replace(temp_name, ACTIVE_SCHEME_STATE_PATH)
    finally:
        if os.path.exists(temp_name):
            os.unlink(temp_name)


def load_active_or_default_scheme() -> dict[str, Any]:
    path = current_active_scheme_path() or ensure_default_scheme()
    return read_scheme(path)


def file_snapshot() -> tuple[tuple[str, int, int], ...]:
    snapshot = []
    for path in scheme_files():
        try:
            stat = path.stat()
        except OSError:
            continue
        snapshot.append((str(path.resolve()), int(stat.st_mtime_ns), int(stat.st_size)))
    return tuple(snapshot)


class ColorSchemesDialog(QtWidgets.QDialog):
    scheme_selected = QtCore.Signal(dict)

    def __init__(self, parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Saved color schemes")
        self.resize(420, 360)

        self._snapshot: tuple[tuple[str, int, int], ...] = ()
        self._refresh_timer = QtCore.QTimer(self)
        self._refresh_timer.setSingleShot(True)
        self._refresh_timer.timeout.connect(self.refresh_if_changed)

        self._scheme_watcher = QtCore.QFileSystemWatcher(self)
        self._scheme_watcher.directoryChanged.connect(self.queue_refresh)
        self._scheme_watcher.fileChanged.connect(self.queue_refresh)

        self.list_widget = QtWidgets.QListWidget()
        self.apply_button = QtWidgets.QPushButton("Apply")
        self.close_button = QtWidgets.QPushButton("Close")

        buttons = QtWidgets.QHBoxLayout()
        buttons.addStretch(1)
        buttons.addWidget(self.apply_button)
        buttons.addWidget(self.close_button)

        layout = QtWidgets.QVBoxLayout(self)
        layout.addWidget(self.list_widget)
        layout.addLayout(buttons)

        self.apply_button.clicked.connect(self.apply_selected)
        self.close_button.clicked.connect(self.close)

        ensure_default_scheme()
        self.refresh(select_path=current_active_scheme_path())

    def queue_refresh(self, _path: str | None = None) -> None:
        self._refresh_timer.start(150)

    def update_watcher(self) -> None:
        ensure_default_scheme()
        old_paths = self._scheme_watcher.files() + self._scheme_watcher.directories()
        if old_paths:
            self._scheme_watcher.removePaths(old_paths)
        paths = [str(SCHEMES_DIR)]
        paths.extend(str(path) for path in scheme_files())
        self._scheme_watcher.addPaths(paths)

    def refresh_if_changed(self) -> None:
        current = file_snapshot()
        if current != self._snapshot:
            self.refresh()
        else:
            self.update_watcher()

    def refresh(self, select_path: Path | None = None) -> None:
        current_item = self.list_widget.currentItem()
        selected = str(select_path or current_item.data(QtCore.Qt.UserRole)) if current_item else None

        self.list_widget.clear()
        matched_item = None
        for path in scheme_files():
            item = QtWidgets.QListWidgetItem(path.stem)
            item.setData(QtCore.Qt.UserRole, str(path.resolve()))
            self.list_widget.addItem(item)
            if selected and str(path.resolve()) == selected:
                matched_item = item

        if matched_item is not None:
            self.list_widget.setCurrentItem(matched_item)
        elif self.list_widget.count():
            self.list_widget.setCurrentRow(0)

        self._snapshot = file_snapshot()
        self.update_watcher()

    def apply_selected(self) -> None:
        item = self.list_widget.currentItem()
        if item is None:
            return
        path = Path(item.data(QtCore.Qt.UserRole))
        data = read_scheme(path)
        set_active_scheme_path(path)
        self.scheme_selected.emit(data)
