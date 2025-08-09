# components/control_panel.py
from PySide6.QtWidgets import (QWidget, QGroupBox, QVBoxLayout, QHBoxLayout, 
                             QLineEdit, QPushButton, QRadioButton, QCheckBox, QFileDialog)
from PySide6.QtCore import Signal, QCoreApplication
class ControlPanelWidget(QWidget):
    draw_mode_changed = Signal(str)
    browse_template_requested = Signal()
    run_pipeline_requested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(0, 0, 0, 0)

        template_group = QGroupBox(QCoreApplication.translate("ControlPanelWidget", "Style Template (Optional)"))
        template_layout = QHBoxLayout(template_group)
        self.template_path_edit = QLineEdit()
        self.template_path_edit.setPlaceholderText(QCoreApplication.translate("ControlPanelWidget", "Click browse to select .ass template file"))
        self.browse_template_btn = QPushButton(QCoreApplication.translate("ControlPanelWidget", "Browse..."))
        template_layout.addWidget(self.template_path_edit)
        template_layout.addWidget(self.browse_template_btn)
        main_layout.addWidget(template_group)

        draw_mode_group = QGroupBox(QCoreApplication.translate("ControlPanelWidget", "Drawing Mode"))
        draw_mode_layout = QHBoxLayout(draw_mode_group)
        self.rect_mode_radio = QRadioButton(QCoreApplication.translate("ControlPanelWidget", "Rectangle (Drag)"))
        self.poly_mode_radio = QRadioButton(QCoreApplication.translate("ControlPanelWidget", "Polygon (Click)"))
        self.rect_mode_radio.setChecked(True)
        draw_mode_layout.addWidget(self.rect_mode_radio)
        draw_mode_layout.addWidget(self.poly_mode_radio)
        main_layout.addWidget(draw_mode_group)

        extract_group = QGroupBox(QCoreApplication.translate("ControlPanelWidget", "Subtitle Generation"))
        extract_layout = QVBoxLayout(extract_group)
        self.run_pipeline_btn = QPushButton(QCoreApplication.translate("ControlPanelWidget", "Subtitle OCR Recognition"))
        
        options_layout = QHBoxLayout()
        self.debug_mode_checkbox = QCheckBox(QCoreApplication.translate("ControlPanelWidget", "Debug Mode"))
        self.visualize_checkbox = QCheckBox(QCoreApplication.translate("ControlPanelWidget", "Visualize Output"))
        self.in_memory_mode_checkbox = QCheckBox(QCoreApplication.translate("ControlPanelWidget", "In-Memory Mode (Experimental)"))
        self.visualize_checkbox.setChecked(False)
        self.debug_mode_checkbox.setChecked(False)
        self.in_memory_mode_checkbox.setChecked(True)
        options_layout.addWidget(self.debug_mode_checkbox)
        options_layout.addWidget(self.visualize_checkbox)
        options_layout.addWidget(self.in_memory_mode_checkbox)
        
        extract_layout.addWidget(self.run_pipeline_btn)
        extract_layout.addLayout(options_layout)
        main_layout.addWidget(extract_group)

        self.browse_template_btn.clicked.connect(self.browse_template_requested)
        self.run_pipeline_btn.clicked.connect(self.run_pipeline_requested)
        self.rect_mode_radio.toggled.connect(self._on_draw_mode_toggled)

    def _on_draw_mode_toggled(self, checked):
        if checked:
            self.draw_mode_changed.emit('rect')
        else:
            self.draw_mode_changed.emit('poly')

    def get_pipeline_options(self) -> dict:
        return {
            "template_path": self.template_path_edit.text(),
            "debug": self.debug_mode_checkbox.isChecked(),
            "visualize": self.visualize_checkbox.isChecked(),
            "in_memory": self.in_memory_mode_checkbox.isChecked()
        }

