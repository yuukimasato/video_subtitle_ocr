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
    toggle_policy_requested = Signal(int)

    def __init__(self, parent=None):
        super().__init__(QCoreApplication.translate("RoiListWidget", "ROI 列表"), parent)

        self._has_clipboard_content: bool = False
        # index -> text_filter_policy（update_list 时随 roi_data 刷新）。
        self._policy_hints: Dict[int, str] = {}

        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(8)
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
        self._policy_hints = {
            i: str(roi.get("text_filter_policy") or "keep_all")
            for i, roi in enumerate(roi_data)
        }
        for i, roi in enumerate(roi_data):
            start_t = roi.get('start_time', 'N/A')
            end_t = roi.get('end_time', 'N/A')
            start_f = roi.get('start_frame', 'N/A')
            end_f = roi.get('end_frame', 'N/A')
            item_text = QCoreApplication.translate("RoiListWidget", "ROI {}：帧[{}-{}] 时间[{} - {}]").format(i, start_f, end_f, start_t, end_t)
            cr = roi.get("color_restrict")
            if isinstance(cr, dict) and cr.get("enabled"):
                item_text += " " + QCoreApplication.translate("RoiListWidget", "[颜色掩膜]")
            if roi.get("blur_enabled"):
                item_text += " " + QCoreApplication.translate("RoiListWidget", "[模糊]")
            # 与 pipeline 一致：缺省视为开启（显式 False 才关闭）。
            if roi.get("fade_in_refine_enabled", True):
                item_text += " " + QCoreApplication.translate("RoiListWidget", "[淡入淡出微调]")
            if roi.get("text_filter_policy") == "auto":
                item_text += " " + QCoreApplication.translate("RoiListWidget", "[自动过滤]")
            self.roi_list_widget.addItem(QListWidgetItem(item_text))
        
        restored_row = False
        if 0 <= current_row < self.roi_list_widget.count():
            self.roi_list_widget.setCurrentRow(current_row)
            restored_row = True
        self.roi_list_widget.blockSignals(False)
        if restored_row:
            # setCurrentRow ran with signals blocked, so the selection_changed
            # signal was swallowed; re-emit it so the main window detail panel
            # stays in sync with the restored selection.
            self.selection_changed.emit(self.roi_list_widget.currentItem(), None)


    def show_roi_context_menu(self, pos: QPoint):
        menu = QMenu()
        item = self.roi_list_widget.itemAt(pos)
        
        if item:
            index = self.roi_list_widget.row(item)
            
            copy_action = QAction(QCoreApplication.translate("RoiListWidget", "复制"), self)
            copy_action.triggered.connect(lambda: self.copy_requested.emit(index))
            menu.addAction(copy_action)
            
            if self._has_clipboard_content:
                paste_action = QAction(QCoreApplication.translate("RoiListWidget", "粘贴到此项之后"), self)
                paste_action.triggered.connect(lambda: self.paste_after_requested.emit(index))
                menu.addAction(paste_action)
            
            delete_action = QAction(QCoreApplication.translate("RoiListWidget", "删除"), self)
            delete_action.triggered.connect(lambda: self.confirm_and_delete(index))
            menu.addAction(delete_action)

            policy_action = QAction(
                QCoreApplication.translate(
                    "RoiListWidget", "切换文字过滤策略（当前：{}）"
                ).format(
                    QCoreApplication.translate("RoiListWidget", "自动过滤")
                    if self._policy_at(index) == "auto"
                    else QCoreApplication.translate("RoiListWidget", "全部保留")
                ),
                self,
            )
            policy_action.triggered.connect(lambda: self.toggle_policy_requested.emit(index))
            menu.addAction(policy_action)

            menu.addSeparator()
        
        if self._has_clipboard_content:
            paste_end_action = QAction(QCoreApplication.translate("RoiListWidget", "粘贴到末尾"), self)
            paste_end_action.triggered.connect(self.paste_at_end_requested)
            menu.addAction(paste_end_action)
        
        if menu.actions():
            menu.exec(self.roi_list_widget.mapToGlobal(pos))

    def _policy_at(self, index: int) -> str:
        return self._policy_hints.get(index, "keep_all")

    def confirm_and_delete(self, index: int):
        reply = QMessageBox.question(self, 
                                     QCoreApplication.translate("RoiListWidget", "确认删除"), 
                                     QCoreApplication.translate("RoiListWidget", "确定要删除 ROI {} 吗？").format(index),
                                     QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if reply == QMessageBox.Yes:
            self.delete_requested.emit(index)

