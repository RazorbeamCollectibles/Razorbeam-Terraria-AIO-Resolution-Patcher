from __future__ import annotations
from PySide6 import QtCore, QtWidgets
from . import color_theme
from .color_schemes import ColorSchemesDialog
from .widgets import CollapsibleSection, HexColorControl, NoWheelComboBox, CursorTooltipFilter

class AppearanceMixin:
    def build_options(self):
        page = QtWidgets.QWidget(); outer = QtWidgets.QVBoxLayout(page)
        scroll = QtWidgets.QScrollArea(); scroll.setWidgetResizable(True); scroll.setFrameShape(QtWidgets.QFrame.Shape.NoFrame)
        content = QtWidgets.QWidget(); layout = QtWidgets.QVBoxLayout(content); scroll.setWidget(content); outer.addWidget(scroll)
        general = CollapsibleSection("General"); layout.addWidget(general)
        self.diagnostics_mode = QtWidgets.QCheckBox("Diagnostics mode")
        self.diagnostics_mode.setChecked(bool(self.state.get("diagnostics_mode", False)))
        self.tooltip(self.diagnostics_mode, "Exported log contains verbose analytics.")
        general.content_layout.addWidget(self.diagnostics_mode)
        self.prevent_minimize = QtWidgets.QCheckBox("Keep game visible when focus changes")
        self.prevent_minimize.setChecked(bool(self.state.get("prevent_minimize", False)))
        general.content_layout.addWidget(self.prevent_minimize)
        log_options = CollapsibleSection("Log"); layout.addWidget(log_options)
        self.hide_log = QtWidgets.QCheckBox("Hide log tab"); self.hide_log.setChecked(bool(self.state.get("hide_log", False)))
        self.tooltip(self.hide_log, "Hides the Log tab from the main view. Logging continues and can be restored here.")
        self.hide_log.toggled.connect(self.set_log_hidden); log_options.content_layout.addWidget(self.hide_log)
        appearance = CollapsibleSection("Appearance"); layout.addWidget(appearance); layout.addStretch()
        self.color_controls = {}
        for name, defs in (("ui", color_theme.UI_COLOR_DEFS), ("log", color_theme.LOG_COLOR_DEFS)):
            section = CollapsibleSection(name.upper() if name == "ui" else "Log")
            appearance.content_layout.addWidget(section)
            for key, label, default in defs:
                row = QtWidgets.QHBoxLayout(); row.addWidget(QtWidgets.QLabel(label)); row.addStretch()
                control = HexColorControl(color_theme.ACTIVE_COLORS[name][key]); row.addWidget(control)
                section.content_layout.addLayout(row); self.color_controls[(name, key)] = control
                control.color_changed.connect(lambda value, n=name, k=key: self.change_color(n, k, value))
        custom = CollapsibleSection("Custom Color Schemes"); appearance.content_layout.addWidget(custom)
        for text, fn in (("Saved color schemes", self.open_schemes), ("Reset all colors", lambda: self.reset_colors()),
                         ("Reset UI colors", lambda: self.reset_colors("ui")), ("Reset log colors", lambda: self.reset_colors("log"))):
            button = QtWidgets.QPushButton(text); button.clicked.connect(fn); custom.content_layout.addWidget(button, 0, QtCore.Qt.AlignmentFlag.AlignLeft)
        self.show_all = QtWidgets.QPushButton("Show all"); self.show_all.clicked.connect(self.toggle_options)
        self.option_sections = [general, log_options, appearance, *appearance.findChildren(CollapsibleSection)]
        return page

    def toggle_options(self):
        expand = any(not s.toggle_button.isChecked() for s in self.option_sections)
        for s in self.option_sections: s.set_expanded(expand)
        self.show_all.setText("Hide all" if expand else "Show all")

    def change_color(self, section, key, value):
        color_theme.ACTIVE_COLORS[section][key] = value
        self.save_colors(); self.refresh_theme()

    def save_colors(self):
        from .utils import load_app_state, save_app_state
        state = load_app_state(); state["custom_colors"] = color_theme.ACTIVE_COLORS
        save_app_state(state)

    def reset_colors(self, section=None):
        color_theme.reset_colors({"colors": color_theme.ACTIVE_COLORS}, section)
        self.save_colors(); self.refresh_theme()

    def open_schemes(self):
        dialog = ColorSchemesDialog(self)
        dialog.scheme_selected.connect(self.apply_scheme)
        dialog.exec()

    def apply_scheme(self, data):
        from .utils import load_app_state, save_app_state
        color_theme.sync_active_colors(data)
        state = load_app_state(); state.pop("custom_colors", None); save_app_state(state)
        self.refresh_theme()

    def refresh_theme(self):
        color_theme.apply_application_theme()
        for section in self.findChildren(CollapsibleSection): section.apply_theme()
        for combo in self.findChildren(NoWheelComboBox): combo.apply_theme()
        for control in self.findChildren(HexColorControl): control.apply_theme()
        for (section, key), control in self.color_controls.items(): control.set_color(color_theme.ACTIVE_COLORS[section][key], emit=False)
        for tip in self.findChildren(CursorTooltipFilter): tip.apply_theme()
        if hasattr(self, "log_handler"): self.log_handler.rerender(self.log_widget)
        if hasattr(self, "monitor_map"): self.monitor_map.update()
        self.refresh_status_styles()

    def tooltip(self, widget, text):
        for old in getattr(widget, "_razorbeam_tooltip_filters", []):
            widget.removeEventFilter(old); old.deleteLater()
        widget.setMouseTracking(True); widget.setAttribute(QtCore.Qt.WidgetAttribute.WA_Hover, True)
        tip = CursorTooltipFilter(text, widget); widget.installEventFilter(tip)
        widget._razorbeam_tooltip_filters = [tip]
        if isinstance(widget, (QtWidgets.QLabel, QtWidgets.QCheckBox)):
            selector = "QCheckBox" if isinstance(widget, QtWidgets.QCheckBox) else "QLabel"
            widget.setStyleSheet(f"{selector} {{ border-bottom: 1px dotted {color_theme.ui_color('strong_border')}; }}")
