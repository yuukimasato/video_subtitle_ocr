# components/roi_list.py
import copy
from typing import List, Dict, Optional

from PySide6.QtWidgets import QWidget, QGroupBox, QVBoxLayout, QListWidget, QListWidgetItem, QMenu, QMessageBox
from PySide6.QtGui import QAction
from PySide6.QtCore import Signal, Qt, QPoint, Slot

class RoiListWidget(QGroupBox):
    selection_changed = Signal(QListWidgetItem, QListWidgetItem)
    copy_requested = Signal(int)
    paste_after_requested = Signal(int)
    paste_at_end_requested = Signal()
    delete_requested = Signal(int)

    def __init__(self, parent=None):
        super().__init__("ROI 列表", parent)
        
        self._has_clipboard_content: bool = False

        layout = QVBoxLayout(self)
        self.roi_list_widget = QListWidget()
        self.roi_list_widget.setContextMenuPolicy(Qt.CustomContextMenu)
        layout.addWidget(self.roi_list_widget)

        self.roi_list_widget.currentItemChanged.connect(self.selection_changed)
        self.roi_list_widget.customContextMenuRequested.connect(self.show_roi_context_menu)

    @Slot(bool)
    def update_clipboard_state(self, has_content: bool):
        self._has_clipboard_content = has_content

    def update_list(self, roi_data: List[Dict]):
        self.roi_list_widget.blockSignals(True)
        current_row = self.roi_list_widget.currentRow()
        self.roi_list_widget.clear()
        for i, roi in enumerate(roi_data):
            start_t = roi.get('start_time', 'N/A')
            end_t = roi.get('end_time', 'N/A')
            start_f = roi.get('start_frame', 'N/A')
            end_f = roi.get('end_frame', 'N/A')
            item_text = f"ROI {i}: F[{start_f}-{end_f}] T[{start_t} - {end_t}]"
            self.roi_list_widget.addItem(QListWidgetItem(item_text))
        
        if 0 <= current_row < self.roi_list_widget.count():
            self.roi_list_widget.setCurrentRow(current_row)
        self.roi_list_widget.blockSignals(False)


    def show_roi_context_menu(self, pos: QPoint):
        menu = QMenu()
        item = self.roi_list_widget.itemAt(pos)
        
        if item:
            index = self.roi_list_widget.row(item)
            
            copy_action = QAction("复制", self)
            copy_action.triggered.connect(lambda: self.copy_requested.emit(index))
            menu.addAction(copy_action)
            
            if self._has_clipboard_content:
                paste_action = QAction("粘贴到此项之后", self)
                paste_action.triggered.connect(lambda: self.paste_after_requested.emit(index))
                menu.addAction(paste_action)
            
            delete_action = QAction("删除", self)
            delete_action.triggered.connect(lambda: self.confirm_and_delete(index))
            menu.addAction(delete_action)
            
            menu.addSeparator()
        
        if self._has_clipboard_content:
            paste_end_action = QAction("粘贴到末尾", self)
            paste_end_action.triggered.connect(self.paste_at_end_requested)
            menu.addAction(paste_end_action)
        
        if menu.actions():
            menu.exec(self.roi_list_widget.mapToGlobal(pos))

    def confirm_and_delete(self, index: int):
        reply = QMessageBox.question(self, '确认删除', f'确定要删除ROI {index} 吗?',
                                     QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if reply == QMessageBox.Yes:
            self.delete_requested.emit(index)

