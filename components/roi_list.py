# components/roi_list.py
import copy
from typing import List, Dict, Optional

from PySide6.QtWidgets import QWidget, QGroupBox, QVBoxLayout, QListWidget, QListWidgetItem, QMenu, QMessageBox
from PySide6.QtGui import QAction
from PySide6.QtCore import Signal, Qt, QPoint, Slot, QCoreApplication

class RoiListWidget(QGroupBox):
    selection_changed = Signal(QListWidgetItem, QListWidgetItem)
    copy_requested = Signal(int)
    paste_after_requested = Signal(int)
    paste_at_end_requested = Signal()
    delete_requested = Signal(int)

    def __init__(self, parent=None):
        super().__init__(QCoreApplication.translate("RoiListWidget", "ROI List"), parent)
        
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
            item_text = QCoreApplication.translate("RoiListWidget", "ROI {}: F[{}-{}] T[{} - {}]").format(i, start_f, end_f, start_t, end_t)
            self.roi_list_widget.addItem(QListWidgetItem(item_text))
        
        if 0 <= current_row < self.roi_list_widget.count():
            self.roi_list_widget.setCurrentRow(current_row)
        self.roi_list_widget.blockSignals(False)


    def show_roi_context_menu(self, pos: QPoint):
        menu = QMenu()
        item = self.roi_list_widget.itemAt(pos)
        
        if item:
            index = self.roi_list_widget.row(item)
            
            copy_action = QAction(QCoreApplication.translate("RoiListWidget", "Copy"), self)
            copy_action.triggered.connect(lambda: self.copy_requested.emit(index))
            menu.addAction(copy_action)
            
            if self._has_clipboard_content:
                paste_action = QAction(QCoreApplication.translate("RoiListWidget", "Paste After This Item"), self)
                paste_action.triggered.connect(lambda: self.paste_after_requested.emit(index))
                menu.addAction(paste_action)
            
            delete_action = QAction(QCoreApplication.translate("RoiListWidget", "Delete"), self)
            delete_action.triggered.connect(lambda: self.confirm_and_delete(index))
            menu.addAction(delete_action)
            
            menu.addSeparator()
        
        if self._has_clipboard_content:
            paste_end_action = QAction(QCoreApplication.translate("RoiListWidget", "Paste to End"), self)
            paste_end_action.triggered.connect(self.paste_at_end_requested)
            menu.addAction(paste_end_action)
        
        if menu.actions():
            menu.exec(self.roi_list_widget.mapToGlobal(pos))

    def confirm_and_delete(self, index: int):
        reply = QMessageBox.question(self, 
                                     QCoreApplication.translate("RoiListWidget", "Confirm Deletion"), 
                                     QCoreApplication.translate("RoiListWidget", "Are you sure you want to delete ROI {}?").format(index),
                                     QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if reply == QMessageBox.Yes:
            self.delete_requested.emit(index)

