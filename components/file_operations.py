# components/file_operations.py
from PySide6.QtWidgets import QGroupBox, QVBoxLayout, QPushButton
from PySide6.QtCore import Signal, QCoreApplication

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

