# components/roi_definition.py
from PySide6.QtWidgets import QWidget, QGroupBox, QFormLayout, QPushButton, QLineEdit, QHBoxLayout
from PySide6.QtCore import Signal, QCoreApplication

class RoiDefinitionWidget(QGroupBox):
    navigate_pressed = Signal(QLineEdit, int) 
    navigate_released = Signal()
    time_edit_finished = Signal(QLineEdit)
    set_time_requested = Signal(QLineEdit)
    add_roi_requested = Signal()
    update_roi_requested = Signal()
    delete_roi_requested = Signal()

    def __init__(self, parent=None):
        super().__init__(QCoreApplication.translate("RoiDefinitionWidget", "ROI Definition"), parent)
        
        roi_layout = QFormLayout(self)
        
        start_time_layout = QHBoxLayout()
        self.start_frame_backward = QPushButton("←")
        self.start_frame_backward.setToolTip(QCoreApplication.translate("RoiDefinitionWidget", "Go back 1 frame (short press) / Continuous (long press)"))
        self.start_frame_forward = QPushButton("→")
        self.start_frame_forward.setToolTip(QCoreApplication.translate("RoiDefinitionWidget", "Go forward 1 frame (short press) / Continuous (long press)"))
        self.start_time_edit = QLineEdit("00:00:00.000")
        self.start_time_edit.setToolTip(QCoreApplication.translate("RoiDefinitionWidget", "Enter time (hh:mm:ss.ms) or frame number"))
        start_time_layout.addWidget(self.start_frame_backward)
        start_time_layout.addWidget(self.start_time_edit)
        start_time_layout.addWidget(self.start_frame_forward)
        roi_layout.addRow(QCoreApplication.translate("RoiDefinitionWidget", "Start Time:"), start_time_layout)
        
        self.set_start_btn = QPushButton(QCoreApplication.translate("RoiDefinitionWidget", "Set as Start Time"))
        roi_layout.addRow(self.set_start_btn)
        
        end_time_layout = QHBoxLayout()
        self.end_frame_backward = QPushButton("←")
        self.end_frame_backward.setToolTip(QCoreApplication.translate("RoiDefinitionWidget", "Go back 1 frame (short press) / Continuous (long press)"))
        self.end_frame_forward = QPushButton("→")
        self.end_frame_forward.setToolTip(QCoreApplication.translate("RoiDefinitionWidget", "Go forward 1 frame (short press) / Continuous (long press)"))
        self.end_time_edit = QLineEdit("00:00:00.000")
        self.end_time_edit.setToolTip(QCoreApplication.translate("RoiDefinitionWidget", "Enter time (hh:mm:ss.ms) or frame number"))
        end_time_layout.addWidget(self.end_frame_backward)
        end_time_layout.addWidget(self.end_time_edit)
        end_time_layout.addWidget(self.end_frame_forward)
        roi_layout.addRow(QCoreApplication.translate("RoiDefinitionWidget", "End Time:"), end_time_layout)
        
        self.set_end_btn = QPushButton(QCoreApplication.translate("RoiDefinitionWidget", "Set as End Time"))
        roi_layout.addRow(self.set_end_btn)
        
        self.add_roi_btn = QPushButton(QCoreApplication.translate("RoiDefinitionWidget", "Add New ROI"))
        self.update_roi_btn = QPushButton(QCoreApplication.translate("RoiDefinitionWidget", "Update Selected ROI"))
        self.delete_roi_btn = QPushButton(QCoreApplication.translate("RoiDefinitionWidget", "Delete Selected ROI"))
        roi_layout.addRow(self.add_roi_btn)
        roi_layout.addRow(self.update_roi_btn)
        roi_layout.addRow(self.delete_roi_btn)

        self.start_frame_backward.pressed.connect(lambda: self.navigate_pressed.emit(self.start_time_edit, -1))
        self.start_frame_forward.pressed.connect(lambda: self.navigate_pressed.emit(self.start_time_edit, 1))
        self.end_frame_backward.pressed.connect(lambda: self.navigate_pressed.emit(self.end_time_edit, -1))
        self.end_frame_forward.pressed.connect(lambda: self.navigate_pressed.emit(self.end_time_edit, 1))
        
        self.start_frame_backward.released.connect(self.navigate_released)
        self.start_frame_forward.released.connect(self.navigate_released)
        self.end_frame_backward.released.connect(self.navigate_released)
        self.end_frame_forward.released.connect(self.navigate_released)

        self.start_time_edit.editingFinished.connect(lambda: self.time_edit_finished.emit(self.start_time_edit))
        self.end_time_edit.editingFinished.connect(lambda: self.time_edit_finished.emit(self.end_time_edit))

        self.set_start_btn.clicked.connect(lambda: self.set_time_requested.emit(self.start_time_edit))
        self.set_end_btn.clicked.connect(lambda: self.set_time_requested.emit(self.end_time_edit))

        self.add_roi_btn.clicked.connect(self.add_roi_requested)
        self.update_roi_btn.clicked.connect(self.update_roi_requested)
        self.delete_roi_btn.clicked.connect(self.delete_roi_requested)

