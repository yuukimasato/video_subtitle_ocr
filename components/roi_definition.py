# components/roi_definition.py
from PySide6.QtWidgets import QWidget, QGroupBox, QFormLayout, QPushButton, QLineEdit, QHBoxLayout
from PySide6.QtCore import Signal

class RoiDefinitionWidget(QGroupBox):
    navigate_pressed = Signal(QLineEdit, int) 
    navigate_released = Signal()
    time_edit_finished = Signal(QLineEdit)
    set_time_requested = Signal(QLineEdit)
    add_roi_requested = Signal()
    update_roi_requested = Signal()
    delete_roi_requested = Signal()

    def __init__(self, parent=None):
        super().__init__("ROI 定义", parent)
        
        roi_layout = QFormLayout(self)
        
        start_time_layout = QHBoxLayout()
        self.start_frame_backward = QPushButton("←")
        self.start_frame_backward.setToolTip("向前1帧 (短按) / 连续 (长按)")
        self.start_frame_forward = QPushButton("→")
        self.start_frame_forward.setToolTip("向后1帧 (短按) / 连续 (长按)")
        self.start_time_edit = QLineEdit("00:00:00.000")
        self.start_time_edit.setToolTip("输入时间 (hh:mm:ss.ms) 或帧号")
        start_time_layout.addWidget(self.start_frame_backward)
        start_time_layout.addWidget(self.start_time_edit)
        start_time_layout.addWidget(self.start_frame_forward)
        roi_layout.addRow("开始时间:", start_time_layout)
        
        self.set_start_btn = QPushButton("设为开始时间")
        roi_layout.addRow(self.set_start_btn)
        
        end_time_layout = QHBoxLayout()
        self.end_frame_backward = QPushButton("←")
        self.end_frame_backward.setToolTip("向前1帧 (短按) / 连续 (长按)")
        self.end_frame_forward = QPushButton("→")
        self.end_frame_forward.setToolTip("向后1帧 (短按) / 连续 (长按)")
        self.end_time_edit = QLineEdit("00:00:00.000")
        self.end_time_edit.setToolTip("输入时间 (hh:mm:ss.ms) 或帧号")
        end_time_layout.addWidget(self.end_frame_backward)
        end_time_layout.addWidget(self.end_time_edit)
        end_time_layout.addWidget(self.end_frame_forward)
        roi_layout.addRow("结束时间:", end_time_layout)
        
        self.set_end_btn = QPushButton("设为结束时间")
        roi_layout.addRow(self.set_end_btn)
        
        self.add_roi_btn = QPushButton("添加新ROI")
        self.update_roi_btn = QPushButton("更新选中ROI")
        self.delete_roi_btn = QPushButton("删除选中ROI")
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

