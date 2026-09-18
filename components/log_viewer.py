# components/log_viewer.py
import html

from PySide6.QtWidgets import QGroupBox, QVBoxLayout, QTextEdit
from PySide6.QtGui import QTextCursor
from PySide6.QtCore import Slot, QCoreApplication

class LogViewerWidget(QGroupBox):
    def __init__(self, parent=None):
        super().__init__(QCoreApplication.translate("LogViewerWidget", "日志与进度"), parent)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(8)
        self.log_display = QTextEdit()
        self.log_display.setReadOnly(True)
        # Cap the log document so long sessions don't grow it without bound.
        self.log_display.document().setMaximumBlockCount(5000)
        layout.addWidget(self.log_display)

    @Slot(str)
    def append_log(self, message: str):
        # QTextEdit.append treats input that "looks like" rich text as HTML;
        # OCR text and paths containing <...> would be swallowed or mangled.
        # Escape AND force rich-text interpretation with an explicit wrapper:
        # a bare escaped string without angle brackets is inserted as plain
        # text by Qt's mightBeRichText heuristic, which would display the
        # entities literally (log lines with dict reprs showed &#x27; for
        # every apostrophe). white-space: pre-wrap keeps multi-line messages
        # (tracebacks) and indentation under HTML parsing.
        self.log_display.append(
            '<div style="white-space: pre-wrap">'
            + html.escape(str(message))
            + "</div>"
        )
        self.log_display.moveCursor(QTextCursor.End)

