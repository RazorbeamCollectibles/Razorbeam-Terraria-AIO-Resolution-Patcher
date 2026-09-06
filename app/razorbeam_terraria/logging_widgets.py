from __future__ import annotations

import html as html_lib
import logging
import os
import re
import subprocess

from PySide6 import QtCore, QtGui, QtWidgets

from .color_theme import log_color


class HtmlFormatter(logging.Formatter):
    def __init__(self) -> None:
        super().__init__("%(asctime)s|%(levelname)s (%(module)s:%(lineno)d)|%(message)s", "%Y-%m-%d %H:%M:%S")

    def format(self, record: logging.LogRecord) -> str:
        text_color = log_color("text")
        record.message = record.getMessage()
        if self.usesTime():
            record.asctime = self.formatTime(record, self.datefmt)
        date_str, _, time_str = record.asctime.partition(" ")
        message = self._render_message(record.message, text_color)
        html = (
            f'<font color="{log_color("date")}">{date_str}</font> '
            f'<font color="{log_color("time")}">{time_str}</font> '
            f'<font color="{text_color}">{message}</font>'
        )
        if record.exc_info:
            record.exc_text = record.exc_text or self.formatException(record.exc_info)
        if record.exc_text:
            html += f'<br><font color="{log_color("error")}">{html_lib.escape(record.exc_text)}</font>'
        return html

    def _protect_filenames(self, message: str) -> tuple[str, list[tuple[str, bool]]]:
        protected: list[tuple[str, bool]] = []

        def protect(match: re.Match[str], wrap: bool = True) -> str:
            protected.append((match.group("filename"), wrap))
            return f"__RAZORBEAM_OUTPUT_FILENAME_{len(protected) - 1}__"

        message = re.sub(
            r"(?P<prefix>Download \(\d+\) )\((?P<filename>.+)\)(?= finished\.$)",
            lambda match: f"{match.group('prefix')}{protect(match, True)}",
            message,
        )
        message = re.sub(
            r"(?P<prefix>Saved batch component(?: \d+/\d+)?: )(?P<filename>.+)$",
            lambda match: f"{match.group('prefix')}{protect(match, False)}",
            message,
        )
        return message, protected

    def _render_message(self, message: str, base_color: str) -> str:
        protected_message, protected = self._protect_filenames(message)
        token_pattern = re.compile(
            r"__RAZORBEAM_OUTPUT_FILENAME_\d+__"
            r"|https?://[^\s<]+"
            r"|(?<![\w/])(?:[A-Za-z]:[\\/][^\n\r<>\"']+)"
            r"|\d+(?:\.\d+)?%"
            r"|ERROR:"
            r"|Unsupported URL"
            r"|not a valid URL"
            r"|(?i:\b(?:failed|unable|incomplete|rejected|not found|not installed|stop|stopped|stopping|delete|error 403|error 404)\b)"
            r"|(?i:\b(?:alert|skipping|testing|replacing|retrying|resuming|updating|update|installing|sleeping|move|continuing)\b)"
            r"|(?i:\b(?:successfully|successful|success|succeeded|finished|complete|completed|retrieved|detected|found|accepted|enabled|ready for use|restored)\b)"
        )
        output: list[str] = []
        pos = 0
        for match in token_pattern.finditer(protected_message):
            output.append(html_lib.escape(protected_message[pos : match.start()]))
            output.append(self._render_token(match.group(0), base_color, protected))
            pos = match.end()
        output.append(html_lib.escape(protected_message[pos:]))
        return "".join(output)

    def _render_token(self, token: str, base_color: str, protected: list[tuple[str, bool]]) -> str:
        filename_match = re.fullmatch(r"__RAZORBEAM_OUTPUT_FILENAME_(\d+)__", token)
        if filename_match:
            filename, wrap = protected[int(filename_match.group(1))]
            display_name = f"({filename})" if wrap else filename
            return self._font(display_name, log_color("metadata"), base_color)
        if token.startswith(("http://", "https://")):
            return self._render_url(token, base_color)
        if re.match(r"(?<![\w/])(?:[A-Za-z]:[\\/])", token):
            return self._render_windows_path(token, base_color)
        if re.fullmatch(r"\d+(?:\.\d+)?%", token):
            color = log_color("success") if token.startswith("100") else log_color("alert")
            return self._font(token, color, base_color)
        if self._is_error_term(token):
            return self._font(token, log_color("error"), base_color)
        if self._is_alert_term(token):
            return self._font(token, log_color("alert"), base_color)
        if self._is_success_term(token):
            return self._font(token, log_color("success"), base_color)
        return html_lib.escape(token)

    def _render_url(self, token: str, base_color: str) -> str:
        display_url, trailing = self._strip_link_trailing(token)
        href = html_lib.escape(html_lib.unescape(display_url), quote=True)
        return (
            f'</font><a href="{href}"><font color="{log_color("link")}"><u>{html_lib.escape(display_url)}</u></font></a>'
            f'<font color="{base_color}">{html_lib.escape(trailing)}'
        )

    def _render_windows_path(self, token: str, base_color: str) -> str:
        display_path, trailing = self._strip_link_trailing(token)
        href = QtCore.QUrl.fromLocalFile(os.path.normpath(display_path)).toString()
        return (
            f'</font><a href="{html_lib.escape(href, quote=True)}"><font color="{log_color("link")}">'
            f'<u>{html_lib.escape(display_path)}</u></font></a><font color="{base_color}">{html_lib.escape(trailing)}'
        )

    def _strip_link_trailing(self, token: str) -> tuple[str, str]:
        trailing = ""
        while token and token[-1] in ".,;:!?)]}'\"":
            trailing = token[-1] + trailing
            token = token[:-1]
        return token, trailing

    def _font(self, text: str, color: str, base_color: str) -> str:
        return f'</font><font color="{color}">{html_lib.escape(text)}</font><font color="{base_color}">'

    def _is_error_term(self, token: str) -> bool:
        return bool(
            re.fullmatch(
                r"ERROR:|Unsupported URL|not a valid URL|(?i:\b(?:failed|unable|incomplete|rejected|not found|not installed|stop|stopped|stopping|delete|error 403|error 404)\b)",
                token,
            )
        )

    def _is_alert_term(self, token: str) -> bool:
        return bool(
            re.fullmatch(
                r"(?i:\b(?:alert|skipping|testing|replacing|retrying|resuming|updating|update|installing|sleeping|move|continuing)\b)",
                token,
            )
        )

    def _is_success_term(self, token: str) -> bool:
        return bool(
            re.fullmatch(
                r"(?i:\b(?:successfully|successful|success|succeeded|finished|complete|completed|retrieved|detected|found|accepted|enabled|ready for use|restored)\b)",
                token,
            )
        )


class LogStringHandler(logging.Handler, QtCore.QObject):
    log_signal = QtCore.Signal(str)
    VISIBLE_MESSAGE_LIMIT = 500

    def __init__(self) -> None:
        logging.Handler.__init__(self)
        QtCore.QObject.__init__(self)
        self.records: list[logging.LogRecord] = []
        self._segmented_progress: dict[str, int] = {}

    def emit(self, record: logging.LogRecord) -> None:
        message = record.getMessage()
        if " ETA " in message:
            return
        if re.match(r"^Sleeping \d+(?:\.\d+)? seconds \((?:download|extractor|request)\)$", message):
            return
        if "googlevideo.com" in message or "manifest.googlevideo.com" in message:
            return
        display_record = logging.makeLogRecord(record.__dict__.copy())
        visible_message = self._summarize_segmented_proxy_message(message)
        if visible_message is None:
            return
        visible_message = self._summarize_visible_message(visible_message)
        if visible_message != message:
            display_record.msg = visible_message
            display_record.args = ()
            display_record.exc_info = None
            display_record.exc_text = None
            message = visible_message
        if len(message) > self.VISIBLE_MESSAGE_LIMIT:
            display_record.msg = (
                message[: self.VISIBLE_MESSAGE_LIMIT].rstrip()
                + " ... [truncated; print log to output for full line]"
            )
            display_record.args = ()
        self.records.append(logging.makeLogRecord(display_record.__dict__.copy()))
        self.log_signal.emit(self.format(display_record))

    def _summarize_segmented_proxy_message(self, message: str) -> str | None:
        if "Segmented proxy" not in message:
            return message
        prefix_match = re.match(r"^(Download \(\d+\) )", message)
        prefix = prefix_match.group(1) if prefix_match else ""
        body = re.sub(r"^Download \(\d+\) ", "", message)
        rules = (
            (r"manifest detected\. Testing|manifest candidate rejected", "Segmented proxy: testing manifests..."),
            (r"page selected-id URL samples|selected manifest body snippet|selected manifest references|linked asset references", "Segmented proxy: scanning page data..."),
            (r"exact chunk candidates|inferred \d+ exact chunk sequence|discovered init segment|chunk sequence detected|chunk init URL|first chunk URL|init segment unavailable|chunk sequence rejected", "Segmented proxy: testing media chunks..."),
            (r"CDP saw request|browser capture|automatic network capture", "Segmented proxy: scanning browser network..."),
            (r"media candidate detected|media candidate rejected by|media candidate accepted", "Segmented proxy: testing media candidates..."),
        )
        for pattern, summary in rules:
            if re.search(pattern, body, re.I):
                key = f"{prefix}{summary}"
                previous = self._segmented_progress.get(key, 0)
                current = min(95, previous + 5)
                if current == previous:
                    return None
                self._segmented_progress[key] = current
                return f"{prefix}{summary} {current}%"
        return message

    @staticmethod
    def _truncate_filename(filename: str, limit: int = 72) -> str:
        if len(filename) <= limit:
            return filename
        stem, dot, suffix = filename.rpartition(".")
        suffix_text = f".{suffix}" if dot and suffix else ""
        keep = max(12, limit - len(suffix_text) - 3)
        return f"{filename[:keep].rstrip()}...{suffix_text}"

    def _summarize_visible_message(self, message: str) -> str:
        lower = message.lower()
        if "traceback (most recent call last)" in lower and "gallery_dl" in lower:
            if "instagram" in lower and ("authrequired" in lower or "authenticated cookies" in lower):
                return "Instagram requires authenticated browser cookies."
            return "Extractor traceback suppressed in visible log. Print log to output for details."
        preview_match = re.match(r"^(Download \(\d+\) )Preview frame: replacing first frame with frame from .+$", message)
        if preview_match:
            return f"{preview_match.group(1)}Replacing first frame to avoid black thumbnail..."
        batch_match = re.match(r"^(Download \(\d+\) Saved batch component(?: \d+/\d+)?: )(.+)$", message)
        if batch_match:
            return f"{batch_match.group(1)}{self._truncate_filename(batch_match.group(2))}"
        check_match = re.match(r"^(Download \(\d+\) Preview frame check started for )(.+)(\.)$", message)
        if check_match:
            return f"{check_match.group(1)}{self._truncate_filename(check_match.group(2))}{check_match.group(3)}"
        return message

    def rerender(self, widget: "LogTextBrowser") -> None:
        widget.clear()
        for record in self.records:
            widget.appendHtml(self.format(logging.makeLogRecord(record.__dict__.copy())))


class SessionTextLogHandler(logging.Handler):
    def __init__(self) -> None:
        super().__init__()
        self.lines: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        message = record.getMessage()
        if " ETA " in message:
            return
        if re.match(r"^Sleeping \d+(?:\.\d+)? seconds \((?:download|extractor|request)\)$", message):
            return
        self.lines.append(self.format(record))


class LogTextBrowser(QtWidgets.QTextBrowser):
    find_requested = QtCore.Signal()

    def __init__(self) -> None:
        super().__init__()
        self.setOpenLinks(False)
        self.setOpenExternalLinks(False)

    def appendHtml(self, html: str) -> None:
        bar = self.verticalScrollBar()
        should_scroll = bar.value() >= bar.maximum() - 8
        self.append(html)
        if should_scroll:
            QtCore.QTimer.singleShot(0, lambda: bar.setValue(bar.maximum()))

    def contextMenuEvent(self, event: QtGui.QContextMenuEvent) -> None:
        menu = self.createStandardContextMenu(event.pos())
        url = QtCore.QUrl(self.anchorAt(event.pos()))
        if url.isLocalFile():
            path = os.path.normpath(url.toLocalFile())
            action = menu.addAction("Open file location")
            action.triggered.connect(lambda _checked=False, p=path: self.open_file_location(p))
        menu.exec(event.globalPos())

    def open_file_location(self, path: str) -> None:
        if os.path.isfile(path):
            subprocess.Popen(["explorer", "/select,", path])
            return
        if os.path.isdir(path):
            QtGui.QDesktopServices.openUrl(QtCore.QUrl.fromLocalFile(path))

    def mouseDoubleClickEvent(self, event: QtGui.QMouseEvent) -> None:
        url = self.anchorAt(event.position().toPoint())
        if url:
            QtGui.QDesktopServices.openUrl(QtCore.QUrl(url))
            return
        super().mouseDoubleClickEvent(event)

    def keyPressEvent(self, event: QtGui.QKeyEvent) -> None:
        if event.key() == QtCore.Qt.Key.Key_F and event.modifiers() & QtCore.Qt.KeyboardModifier.ControlModifier:
            self.find_requested.emit()
            event.accept()
            return
        super().keyPressEvent(event)
