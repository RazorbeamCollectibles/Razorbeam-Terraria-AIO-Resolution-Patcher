from __future__ import annotations
import logging
from pathlib import Path
import sys
from PySide6 import QtGui, QtWidgets
from .utils import APP_NAME, WINDOW_TITLE, DEBUG_LOG_PATH, ensure_runtime_dirs
from .color_schemes import load_active_or_default_scheme
from . import color_theme
from .native import SingleInstance, activate_existing

def main():
    ensure_runtime_dirs()
    from logging.handlers import RotatingFileHandler
    logging.basicConfig(level=logging.INFO, handlers=[RotatingFileHandler(DEBUG_LOG_PATH, maxBytes=2_000_000, backupCount=3, encoding="utf-8")],
                        format="%(asctime)s %(levelname)s %(message)s")
    instance = SingleInstance()
    if instance.already_running:
        activate_existing(WINDOW_TITLE); instance.close(); return 0
    app = QtWidgets.QApplication(sys.argv)
    app.setApplicationName(WINDOW_TITLE)
    resource_root = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parents[1]))
    icon = QtGui.QIcon(str(resource_root / "assets" / "aiorp_icon.ico"))
    if not icon.isNull():
        app.setWindowIcon(icon)
    app.setStyle("Fusion")
    load_active_or_default_scheme()
    color_theme.apply_application_theme(app)
    from .window import MainWindow
    window = MainWindow()
    window.show()
    def error(kind, value, trace):
        logging.exception("Unexpected application error", exc_info=(kind, value, trace))
        QtWidgets.QMessageBox.critical(window, WINDOW_TITLE, str(value))
    sys.excepthook = error
    code = app.exec(); instance.close(); return code
