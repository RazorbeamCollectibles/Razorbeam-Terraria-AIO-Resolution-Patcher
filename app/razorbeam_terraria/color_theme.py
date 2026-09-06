from __future__ import annotations

import re
from copy import deepcopy
from typing import Any

from PySide6 import QtGui, QtWidgets

UI_COLOR_DEFS = (
    ("window_background", "Window background", "#202020"),
    ("input_background", "Input background", "#151515"),
    ("panel_background", "Panel background", "#1e1e1e"),
    ("panel_hover", "Panel hover", "#2a2a2a"),
    ("border", "Borders and tooltip underline", "#303030"),
    ("strong_border", "Strong borders", "#404040"),
    ("text", "Primary text", "#ffffff"),
    ("accent", "Primary accent", "#6802a7"),
    ("button_background", "Button background", "#6802a7"),
    ("button_hover", "Button hover", "#911bff"),
    ("button_border", "Button border", "#8a00c4"),
    ("progress", "Progress bars", "#911bff"),
    ("success", "Success status", "#00ff00"),
    ("error", "Error status", "#ff0000"),
    ("warning", "Warning status", "#ffff00"),
    ("dialog_background", "Dialog background", "#1e1e2e"),
    ("dialog_panel", "Dialog panel", "#313244"),
    ("dialog_border", "Dialog border", "#45475a"),
    ("dialog_text", "Dialog text", "#cdd6f4"),
    ("dialog_muted_text", "Dialog muted text", "#a6adc8"),
    ("dialog_accent", "Dialog accent", "#7b42ff"),
    ("dialog_accent_hover", "Dialog accent hover", "#925cff"),
)

LOG_COLOR_DEFS = (
    ("date", "Date stamps", "#ffffff"),
    ("time", "Time stamps", "#adadad"),
    ("metadata", "Metadata and filenames", "#636363"),
    ("text", "Standard log text", "#8200f0"),
    ("link", "Clickable links", "#4754ff"),
    ("search_highlight", "Search highlight", "#ef0fff"),
    ("error", "Critical text", "#ff0000"),
    ("alert", "Attention text", "#ffaa00"),
    ("success", "Positive text", "#00e52b"),
)

DEFAULT_COLORS: dict[str, dict[str, str]] = {
    "ui": {key: default for key, _label, default in UI_COLOR_DEFS},
    "log": {key: default for key, _label, default in LOG_COLOR_DEFS},
}

ACTIVE_COLORS = deepcopy(DEFAULT_COLORS)
_HEX_RE = re.compile(r"^#[0-9a-fA-F]{6}$")


def default_colors() -> dict[str, dict[str, str]]:
    return deepcopy(DEFAULT_COLORS)


def normalize_hex(value: Any, fallback: str) -> str:
    text = str(value or "").strip()
    if not text.startswith("#"):
        text = f"#{text}"
    return text.lower() if _HEX_RE.fullmatch(text) else fallback.lower()


def merged_known_colors(raw: dict[str, Any] | None) -> dict[str, dict[str, str]]:
    merged = default_colors()
    if not isinstance(raw, dict):
        return merged
    for section, defaults in DEFAULT_COLORS.items():
        raw_section = raw.get(section, {})
        if not isinstance(raw_section, dict):
            continue
        for key, fallback in defaults.items():
            value = normalize_hex(raw_section.get(key), fallback)
            if section == "ui" and key == "button_background" and value == "#8a00c4":
                value = fallback.lower()
            merged[section][key] = value
    return merged


def sync_active_colors(config: dict[str, Any]) -> dict[str, Any]:
    colors = config.setdefault("colors", {})
    if not isinstance(colors, dict):
        colors = {}
        config["colors"] = colors

    known = merged_known_colors(colors)
    ACTIVE_COLORS.clear()
    ACTIVE_COLORS.update(known)

    for section, values in known.items():
        target = colors.setdefault(section, {})
        if not isinstance(target, dict):
            target = {}
            colors[section] = target
        for key, value in values.items():
            target.setdefault(key, value)

    return config


def reset_colors(config: dict[str, Any], section: str | None = None) -> None:
    colors = config.setdefault("colors", {})
    defaults = default_colors()
    sections = (section,) if section else ("ui", "log")
    for name in sections:
        colors[name] = defaults[name].copy()
    sync_active_colors(config)


def ui_color(key: str) -> str:
    return ACTIVE_COLORS["ui"][key]


def log_color(key: str) -> str:
    return ACTIVE_COLORS["log"][key]


def apply_application_theme(app: QtWidgets.QApplication | None = None) -> None:
    app = app or QtWidgets.QApplication.instance()
    if app is None:
        return
    palette = QtGui.QPalette()
    palette.setColor(QtGui.QPalette.ColorRole.Window, QtGui.QColor(ui_color("window_background")))
    palette.setColor(QtGui.QPalette.ColorRole.WindowText, QtGui.QColor(ui_color("text")))
    palette.setColor(QtGui.QPalette.ColorRole.Base, QtGui.QColor(ui_color("input_background")))
    palette.setColor(QtGui.QPalette.ColorRole.AlternateBase, QtGui.QColor(ui_color("window_background")))
    palette.setColor(QtGui.QPalette.ColorRole.ToolTipBase, QtGui.QColor(ui_color("input_background")))
    palette.setColor(QtGui.QPalette.ColorRole.ToolTipText, QtGui.QColor(ui_color("text")))
    palette.setColor(QtGui.QPalette.ColorRole.Text, QtGui.QColor(ui_color("text")))
    palette.setColor(QtGui.QPalette.ColorRole.Button, QtGui.QColor(ui_color("window_background")))
    palette.setColor(QtGui.QPalette.ColorRole.ButtonText, QtGui.QColor(ui_color("text")))
    palette.setColor(QtGui.QPalette.ColorRole.BrightText, QtGui.QColor(ui_color("error")))
    palette.setColor(QtGui.QPalette.ColorRole.Link, QtGui.QColor(log_color("link")))
    palette.setColor(QtGui.QPalette.ColorRole.Highlight, QtGui.QColor(ui_color("accent")))
    palette.setColor(QtGui.QPalette.ColorRole.HighlightedText, QtGui.QColor(ui_color("text")))
    app.setPalette(palette)
    app.setStyleSheet(app_stylesheet())


def app_stylesheet(colors: dict[str, dict[str, str]] | None = None) -> str:
    palette = colors or ACTIVE_COLORS
    ui = palette["ui"]
    return f"""
QLineEdit, QTextEdit, QPlainTextEdit, QSpinBox, QDoubleSpinBox {{
    background: {ui["input_background"]};
    color: {ui["text"]};
    border: 1px solid {ui["border"]};
    border-radius: 4px;
    padding: 6px;
}}
QPushButton {{
    background: {ui["button_background"]};
    color: {ui["text"]};
    border: 1px solid {ui["button_border"]};
    border-radius: 4px;
    padding: 7px 12px;
    font-weight: normal;
}}
QPushButton:hover {{
    background: {ui["button_hover"]};
}}
QPushButton:disabled {{
    background: {ui["button_background"]};
    color: {ui["text"]};
    border-color: {ui["button_border"]};
}}
QTabWidget::pane {{
    border: 1px solid {ui["border"]};
    top: -1px;
}}
QTabBar {{
    qproperty-drawBase: 0;
    min-height: 28px;
    max-height: 28px;
}}
QTabBar::tab {{
    min-height: 28px;
    max-height: 28px;
    padding: 0px 16px;
    margin: 0px;
    border: 1px solid {ui["border"]};
    background-color: {ui["input_background"]};
    color: {ui["text"]};
}}
QTabBar::tab:selected {{
    background-color: {ui["button_hover"]};
    border-color: {ui["button_hover"]};
    color: {ui["text"]};
}}
QTabBar::tab:hover:!selected {{
    background-color: {ui["input_background"]};
    border-color: {ui["border"]};
    color: {ui["text"]};
}}
QCheckBox:disabled {{
    color: #777777;
}}
QTreeWidget, QListWidget, QTableWidget {{
    background: {ui["panel_background"]};
    alternate-background-color: {ui["input_background"]};
    border: 1px solid {ui["border"]};
}}
QHeaderView::section {{
    background: {ui["panel_hover"]};
    color: {ui["text"]};
    border: 1px solid {ui["border"]};
    padding: 5px;
}}
QToolTip {{
    background: {ui["dialog_panel"]};
    color: {ui["dialog_text"]};
    border: 1px solid {ui["dialog_border"]};
}}
"""
