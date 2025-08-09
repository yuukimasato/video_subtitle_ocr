# components/file_operations.py
from PySide6.QtWidgets import QGroupBox, QFormLayout, QPushButton
from PySide6.QtCore import Signal, QCoreApplication

class FileOperationsWidget(QGroupBox):
    load_video_requested = Signal()
    save_roi_requested = Signal()
    load_roi_requested = Signal()

    def __init__(self, parent=None):
        super().__init__(QCoreApplication.translate("FileOperationsWidget", "File Operations (Drag & Drop Supported)"), parent)
        
        layout = QFormLayout(self)
        
        self.load_video_btn = QPushButton(QCoreApplication.translate("FileOperationsWidget", "Load Video"))
        self.save_roi_btn = QPushButton(QCoreApplication.translate("FileOperationsWidget", "Save ROI Config"))
        self.load_roi_btn = QPushButton(QCoreApplication.translate("FileOperationsWidget", "Load ROI Config"))
        
        layout.addRow(self.load_video_btn)
        layout.addRow(self.save_roi_btn)
        layout.addRow(self.load_roi_btn)
        
        self.load_video_btn.clicked.connect(self.load_video_requested)
        self.save_roi_btn.clicked.connect(self.save_roi_requested)
        self.load_roi_btn.clicked.connect(self.load_roi_requested)

