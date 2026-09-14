# components/file_operations.py
import os
import sys

from PySide6.QtWidgets import (
    QGroupBox,
    QVBoxLayout,
    QPushButton,
    QHBoxLayout,
    QComboBox,
    QLabel,
    QMessageBox,
)
from PySide6.QtCore import Signal, QCoreApplication, QSettings, QProcess


class FileOperationsWidget(QGroupBox):
    load_video_requested = Signal()
    save_roi_requested = Signal()
    load_roi_requested = Signal()
    auto_roi_requested = Signal()
    deep_scan_requested = Signal()

    def __init__(self, parent=None):
        super().__init__(QCoreApplication.translate("FileOperationsWidget", "文件操作（支持拖放）"), parent)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(8)

        self.load_video_btn = QPushButton(QCoreApplication.translate("FileOperationsWidget", "加载视频"))
        self.save_roi_btn = QPushButton(QCoreApplication.translate("FileOperationsWidget", "保存 ROI 配置"))
        self.load_roi_btn = QPushButton(QCoreApplication.translate("FileOperationsWidget", "加载 ROI 配置"))
        self.auto_roi_btn = QPushButton(QCoreApplication.translate("FileOperationsWidget", "自动检测字幕ROI"))
        self.deep_scan_btn = QPushButton(QCoreApplication.translate("FileOperationsWidget", "深度扫描（水印/场景字）…"))

        for b in (self.load_video_btn, self.save_roi_btn, self.load_roi_btn, self.auto_roi_btn, self.deep_scan_btn):
            b.setMinimumHeight(30)

        layout.addWidget(self.load_video_btn)
        layout.addWidget(self.save_roi_btn)
        layout.addWidget(self.load_roi_btn)
        layout.addWidget(self.auto_roi_btn)
        layout.addWidget(self.deep_scan_btn)

        self.load_video_btn.clicked.connect(self.load_video_requested)
        self.save_roi_btn.clicked.connect(self.save_roi_requested)
        self.load_roi_btn.clicked.connect(self.load_roi_requested)
        self.auto_roi_btn.clicked.connect(self.auto_roi_requested)
        self.deep_scan_btn.clicked.connect(self.deep_scan_requested)

        # ── 界面语言（切换后需重启应用生效）──
        # 语言显示名保持各语言原称（不翻译）；逐项字面量添加以保证 lupdate 提取。
        lang_row = QHBoxLayout()
        lang_row.setSpacing(8)
        lang_label = QLabel(QCoreApplication.translate("FileOperationsWidget", "语言"))
        self.language_combo = QComboBox()
        self.language_combo.addItem(
            QCoreApplication.translate("FileOperationsWidget", "自动（跟随系统）"), "auto")
        self.language_combo.addItem(QCoreApplication.translate("FileOperationsWidget", "简体中文"), "zh_CN")
        self.language_combo.addItem(QCoreApplication.translate("FileOperationsWidget", "繁體中文"), "zh_TW")
        self.language_combo.addItem(QCoreApplication.translate("FileOperationsWidget", "English"), "en")
        self.language_combo.addItem(QCoreApplication.translate("FileOperationsWidget", "日本語"), "ja_JP")
        self._loading_language = True
        saved = QSettings().value("ui/language", "auto")
        idx = self.language_combo.findData(saved if isinstance(saved, str) else "auto")
        self.language_combo.setCurrentIndex(idx if idx >= 0 else 0)
        self._loading_language = False
        lang_row.addWidget(lang_label)
        lang_row.addWidget(self.language_combo, 1)
        layout.addLayout(lang_row)

        self.language_combo.currentIndexChanged.connect(self._on_language_changed)

    def _on_language_changed(self, index: int) -> None:
        """保存语言偏好并询问是否立即重启（重启后才应用到全部界面）。"""
        if getattr(self, "_loading_language", False):
            return
        lang = self.language_combo.itemData(index)
        previous = QSettings().value("ui/language", "auto")
        if lang == previous:
            return
        QSettings().setValue("ui/language", lang)
        reply = QMessageBox.question(
            self,
            QCoreApplication.translate("FileOperationsWidget", "切换语言"),
            QCoreApplication.translate("FileOperationsWidget", "语言设置将在重启应用后生效。要立即重启吗？"),
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.Yes,
        )
        if reply == QMessageBox.Yes:
            self._restart_application()

    def _restart_application(self) -> None:
        app = QCoreApplication.instance()
        if getattr(sys, "frozen", False):
            args: list = []
        else:
            args = [os.path.abspath(sys.argv[0])]
        restarted = QProcess.startDetached(sys.executable, args)
        if restarted and app is not None:
            app.quit()
        else:
            QMessageBox.information(
                self,
                QCoreApplication.translate("FileOperationsWidget", "切换语言"),
                QCoreApplication.translate("FileOperationsWidget", "无法自动重启，请手动重启应用以应用新语言。"),
            )
