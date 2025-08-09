# components/log_viewer.py
from PySide6.QtWidgets import QWidget, QGroupBox, QVBoxLayout, QTextEdit
from PySide6.QtGui import QTextCursor
from PySide6.QtCore import Slot, QCoreApplication 

class LogViewerWidget(QGroupBox):
    def __init__(self, parent=None):
        super().__init__(QCoreApplication.translate("LogViewerWidget", "Logs and Progress"), parent)
        
        layout = QVBoxLayout(self)
        self.log_display = QTextEdit()
        self.log_display.setReadOnly(True)
        layout.addWidget(self.log_display)

    @Slot(str)
    def append_log(self, message: str):
        self.log_display.append(message)
        self.log_display.moveCursor(QTextCursor.End)

