# components/deepseek_progress_panel.py
from __future__ import annotations

from PySide6.QtWidgets import QWidget, QVBoxLayout, QGroupBox, QTextEdit, QPushButton
from PySide6.QtGui import QTextCursor
from PySide6.QtCore import QCoreApplication


class DeepSeekProgressPanel(QGroupBox):
    """Shows step-by-step LLM (DeepSeek) progress during OCR pipeline."""

    def __init__(self, parent=None):
        super().__init__(QCoreApplication.translate("DeepSeekProgressPanel", "DeepSeek / 大模型处理进度"), parent)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(8, 8, 8, 8)
        lay.setSpacing(6)

        self._log = QTextEdit()
        self._log.setReadOnly(True)
        self._log.setMinimumHeight(140)
        self._log.setMaximumHeight(320)
        self._log.setPlaceholderText(
            QCoreApplication.translate(
                "DeepSeekProgressPanel",
                "启用 DeepSeek 后，此处显示处理进度，以及各批次润色前后的字幕对比、"
                "碎片合并候选与结果、策略复核说明等，便于核对模型具体改动了哪些字。",
            )
        )
        self._log.setStyleSheet("font-family: monospace; font-size: 11px;")
        lay.addWidget(self._log)

        self._clear_btn = QPushButton(QCoreApplication.translate("DeepSeekProgressPanel", "清空日志"))
        self._clear_btn.clicked.connect(self.clear)
        lay.addWidget(self._clear_btn)

    def append_line(self, text: str) -> None:
        s = (text or "").rstrip()
        if not s:
            return
        self._log.moveCursor(QTextCursor.End)
        self._log.insertPlainText(s + "\n")
        self._log.moveCursor(QTextCursor.End)

    def clear(self) -> None:
        self._log.clear()

    def set_panel_visible(self, visible: bool) -> None:
        self.setVisible(bool(visible))
