from __future__ import annotations

from PySide6 import QtCore, QtGui, QtWidgets

from .color_theme import normalize_hex, ui_color


class HexColorControl(QtWidgets.QWidget):
    color_changed = QtCore.Signal(str)

    def __init__(self, color: str = "#ffffff", parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__(parent)
        self.setFixedWidth(114)
        self._color = normalize_hex(color, "#ffffff")

        layout = QtWidgets.QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)

        self.swatch = QtWidgets.QPushButton()
        self.swatch.setFixedSize(26, 20)
        self.hex_edit = QtWidgets.QLineEdit()
        self.hex_edit.setFixedWidth(82)
        self.hex_edit.setMaxLength(7)

        layout.addWidget(self.swatch)
        layout.addWidget(self.hex_edit)
        self.swatch.clicked.connect(self.choose_color)
        self.hex_edit.editingFinished.connect(self.commit_hex)
        self.set_color(self._color, emit=False)

    def color(self) -> str:
        return self._color

    def set_color(self, color: str, emit: bool = False) -> None:
        normalized = normalize_hex(color, self._color)
        changed = normalized != self._color
        self._color = normalized
        self.hex_edit.setText(normalized)
        self.swatch.setStyleSheet(
            f"background-color: {normalized}; border: 1px solid {ui_color('strong_border')};"
        )
        if emit and changed:
            self.color_changed.emit(normalized)

    def apply_theme(self) -> None:
        self.set_color(self._color, emit=False)

    def choose_color(self) -> None:
        selected = QtWidgets.QColorDialog.getColor(QtGui.QColor(self._color), self, "Choose color")
        if selected.isValid():
            self.set_color(selected.name(), emit=True)

    def commit_hex(self) -> None:
        self.set_color(self.hex_edit.text(), emit=True)


class CursorTooltipFilter(QtCore.QObject):
    def __init__(self, tooltip_text: str, parent: QtCore.QObject | None = None) -> None:
        super().__init__(parent)
        self.tooltip_text = f" {tooltip_text} "
        self.popup: QtWidgets.QLabel | None = None

    def ensure_popup(self) -> None:
        if self.popup is not None:
            return
        self.popup = QtWidgets.QLabel()
        self.popup.setWindowFlags(QtCore.Qt.WindowType.ToolTip)
        self.popup.setAttribute(QtCore.Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        self.popup.setAttribute(QtCore.Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.popup.setText(self.tooltip_text)
        self.apply_theme()

    def apply_theme(self) -> None:
        if self.popup is None:
            return
        self.popup.setStyleSheet(
            f"QLabel {{ color: {ui_color('text')}; background-color: {ui_color('input_background')}; "
            f"border: 1px solid {ui_color('strong_border')}; padding: 2px 0px; }}"
        )
        self.popup.adjustSize()

    def hide_tooltip(self) -> None:
        if self.popup is not None:
            self.popup.hide()

    def eventFilter(self, watched: QtCore.QObject, event: QtCore.QEvent) -> bool:
        if event.type() in (QtCore.QEvent.Type.Enter, QtCore.QEvent.Type.MouseMove):
            self.ensure_popup()
            if self.popup is None or not isinstance(watched, QtWidgets.QWidget):
                return False
            if event.type() == QtCore.QEvent.Type.MouseMove and isinstance(event, QtGui.QMouseEvent):
                global_pos = watched.mapToGlobal(event.position().toPoint())
            else:
                global_pos = QtGui.QCursor.pos()
            self.popup.move(global_pos + QtCore.QPoint(12, 18))
            self.popup.adjustSize()
            if not self.popup.isVisible():
                self.popup.show()
            return False
        if event.type() in (
            QtCore.QEvent.Type.Leave,
            QtCore.QEvent.Type.HoverLeave,
            QtCore.QEvent.Type.FocusOut,
        ):
            self.hide_tooltip()
            return False
        return False


class CollapsibleSection(QtWidgets.QWidget):
    def __init__(self, title: str, parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__(parent)
        self.title = title
        self.toggle_button = QtWidgets.QToolButton()
        self.toggle_button.setCheckable(True)
        self.toggle_button.setChecked(False)
        self.toggle_button.setToolButtonStyle(QtCore.Qt.ToolButtonStyle.ToolButtonTextOnly)

        self.content = QtWidgets.QWidget()
        self.content_layout = QtWidgets.QVBoxLayout(self.content)
        self.content_layout.setContentsMargins(16, 2, 0, 8)
        self.content_layout.setSpacing(6)
        self.content.setVisible(False)

        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(self.toggle_button)
        layout.addWidget(self.content)

        self.toggle_button.toggled.connect(self.set_expanded)
        self.apply_theme()
        self.set_expanded(False)

    def set_expanded(self, expanded: bool) -> None:
        self.toggle_button.setChecked(expanded)
        marker = "▾" if expanded else "▸"
        self.toggle_button.setText(f"{marker} {self.title}")
        self.content.setVisible(expanded)

    def apply_theme(self) -> None:
        self.toggle_button.setStyleSheet(
            f"QToolButton {{ border: none; color: {ui_color('text')}; font-weight: bold; "
            "padding: 4px 0px; text-align: left; }}"
        )


class NoWheelComboBox(QtWidgets.QComboBox):
    def __init__(self, parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__(parent)
        self.apply_theme()

    def apply_theme(self) -> None:
        self.setStyleSheet(
            f"QComboBox {{ color: {ui_color('text')}; selection-color: {ui_color('text')}; "
            f"selection-background-color: {ui_color('accent')}; }}"
            f"QComboBox QAbstractItemView {{ color: {ui_color('text')}; selection-color: {ui_color('text')}; "
            f"selection-background-color: {ui_color('accent')}; }}"
        )

    def wheelEvent(self, event: QtGui.QWheelEvent) -> None:
        event.ignore()

