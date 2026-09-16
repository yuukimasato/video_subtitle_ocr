import copy
import math

import cv2
import numpy as np

from typing import Dict, Optional

from PySide6.QtCore import QPoint, QRect, QCoreApplication, Slot
from PySide6.QtGui import QPolygon
from PySide6.QtWidgets import QListWidgetItem, QMessageBox

from utils.time_utils import format_time


class RoiEditingMixin:
    """ROI 绘制回调、增删改、剪贴板与可见性刷新（原 main_window.py SubtitleOCRGUI L341-770）。"""

    def on_roi_drawn(self):
        # If user is drawing a NEW ROI (no list selection), reset per-ROI color settings to defaults.
        # Otherwise, keep showing the selected ROI's own settings for editing.
        if self.roi_list_widget.roi_list_widget.currentItem() is None:
            self.roi_def_widget.set_color_restrict_from_roi(None)
        self.update_ui_state()

    def on_draw_mode_changed(self, mode: str):
        self.video_display_widget.video_label.set_draw_mode(mode)
        self.update_ui_state()

    def add_roi(self):
        if not self.cap or not self.video_display_widget.video_label.is_roi_ready(): return
        roi_entry = self._create_roi_entry_from_ui()
        if roi_entry:
            self.roi_data.append(roi_entry)
            self.update_roi_list()
            self.roi_list_widget.roi_list_widget.setCurrentRow(len(self.roi_data) - 1)
            self.video_display_widget.video_label.clear_current_drawing()
            self.control_panel_widget.invalidate_color_gate_confirmation()
            self.update_ui_state()
            self.logger.info(QCoreApplication.translate("SubtitleOCRGUI", "已添加新 ROI，帧范围：{}-{}").format(roi_entry['start_frame'], roi_entry['end_frame']))

    def update_selected_roi(self):
        if not self.cap or not self.video_display_widget.video_label.is_roi_ready(): return
        current_item = self.roi_list_widget.roi_list_widget.currentItem()
        if not current_item: return
        selected_index = self.roi_list_widget.roi_list_widget.row(current_item)
        roi_entry = self._create_roi_entry_from_ui()
        if roi_entry:
            self.roi_data[selected_index] = roi_entry
            self.update_roi_list()
            self.roi_list_widget.roi_list_widget.setCurrentRow(selected_index)
            self.video_display_widget.video_label.clear_current_drawing()
            self.control_panel_widget.invalidate_color_gate_confirmation()
            self.update_ui_state()
            self.logger.info(QCoreApplication.translate("SubtitleOCRGUI", "已更新 ROI {}，新帧范围：{}-{}").format(selected_index, roi_entry['start_frame'], roi_entry['end_frame']))

    def delete_selected_roi(self):
        current_item = self.roi_list_widget.roi_list_widget.currentItem()
        if not current_item: return
        selected_index = self.roi_list_widget.roi_list_widget.row(current_item)
        self.roi_list_widget.confirm_and_delete(selected_index)

    @Slot(int)
    def delete_roi_by_index(self, index: int):
        if 0 <= index < len(self.roi_data):
            del self.roi_data[index]
            self.update_roi_list()
            # update_list 只在被删行仍合法时补发 selection_changed；删除最后一
            # 项时不会触发，必须无条件刷新画面覆盖层，否则被删 ROI 残留。
            self.update_all_rois_visibility()
            if self.roi_list_widget.roi_list_widget.currentItem() is None:
                self.roi_def_widget.set_color_restrict_from_roi(None)
            self.control_panel_widget.invalidate_color_gate_confirmation()
            self.update_ui_state()
            self.logger.info(QCoreApplication.translate("SubtitleOCRGUI", "已删除 ROI {}").format(index))

    @Slot(int)
    def toggle_roi_filter_policy(self, index: int):
        """在 keep_all（全部保留）与 auto（按场景预设过滤）之间切换。"""
        if not (0 <= index < len(self.roi_data)):
            return
        roi = self.roi_data[index]
        current = str(roi.get("text_filter_policy") or "keep_all")
        new_policy = "keep_all" if current == "auto" else "auto"
        roi["text_filter_policy"] = new_policy
        self.update_roi_list()
        self.logger.info(
            QCoreApplication.translate(
                "SubtitleOCRGUI", "ROI {} 文字过滤策略已切换为：{}"
            ).format(
                index,
                QCoreApplication.translate("SubtitleOCRGUI", "按场景预设过滤")
                if new_policy == "auto"
                else QCoreApplication.translate("SubtitleOCRGUI", "全部保留（不过滤）"),
            )
        )

    @Slot(bool)
    def on_pose_tags_toggled(self, checked: bool):
        """「写入画面位置标签」勾选变化：立即写回当前选中的 ROI。

        此前该设置只在 添加新 ROI / 更新选中 ROI 时随条目重建写入，勾选后
        直接开始识别会静默丢失；且回填（set_color_restrict_from_roi）会在
        重新选中时把勾选框重置回 ROI 保存值，状态看起来"勾了却不生效"。
        回填触发的 toggled 信号写入的是刚读出的同值，幂等无害。
        """
        current_item = self.roi_list_widget.roi_list_widget.currentItem()
        if not current_item:
            return
        index = self.roi_list_widget.roi_list_widget.row(current_item)
        if not (0 <= index < len(self.roi_data)):
            return
        roi = self.roi_data[index]
        roi["write_pose_tags"] = bool(checked)
        if checked:
            pose = self._compute_roi_pose(roi.get("type", "rect"), roi.get("points"))
            if pose:
                roi["pose"] = pose
            else:
                roi.pop("pose", None)
        self.logger.info(
            QCoreApplication.translate(
                "SubtitleOCRGUI", "ROI {} 画面位置标签已{}"
            ).format(index, QCoreApplication.translate("SubtitleOCRGUI", "开启") if checked
                     else QCoreApplication.translate("SubtitleOCRGUI", "关闭"))
        )

    @staticmethod
    def _compute_roi_pose(roi_type: str, points) -> Optional[Dict]:
        """Derive picture pose (position + in-plane tilt) from ROI geometry.

        Polygons: minimum-area rotated rectangle — its center is the pose
        position and its long edge tilt becomes \\frz (counterclockwise
        positive, matching ASS, folded into ±45° so text is never vertical).
        Rects: geometric center, zero rotation. \\frx/\\fry (out-of-plane
        perspective) cannot be recovered from a 2D polygon and are stored as 0
        for manual tuning.
        """
        try:
            if roi_type == "rect":
                if not (isinstance(points, list) and len(points) == 4):
                    return None
                x, y, w, h = [float(p) for p in points]
                return {"pos": [round(x + w / 2.0, 1), round(y + h / 2.0, 1)], "frz": 0.0, "frx": 0.0, "fry": 0.0}
            if roi_type != "poly":
                return None
            if not (isinstance(points, list) and len(points) >= 3):
                return None
            pts = np.array(points, dtype=np.float32)
            rect = cv2.minAreaRect(pts)
            (cx, cy), (rw, rh), _angle = rect
            box = cv2.boxPoints(rect)
            # Long edge of the box = text baseline direction; derive its
            # on-screen tilt ourselves so the result is independent of the
            # OpenCV version's angle convention.
            e1 = box[1] - box[0]
            e2 = box[2] - box[1]
            edge = e1 if (e1[0] ** 2 + e1[1] ** 2) >= (e2[0] ** 2 + e2[1] ** 2) else e2
            # Image Y points down, so visual counterclockwise tilt negates atan2.
            frz = -math.degrees(math.atan2(edge[1], edge[0]))
            while frz > 45.0:
                frz -= 90.0
            while frz < -45.0:
                frz += 90.0
            return {
                "pos": [round(float(cx), 1), round(float(cy), 1)],
                "frz": round(float(frz), 1),
                "frx": 0.0,
                "fry": 0.0,
            }
        except Exception:
            return None

    def _create_roi_entry_from_ui(self) -> Optional[Dict]:
        try:
            start_frame = self.parse_time_or_frame(self.roi_def_widget.start_time_edit.text())
            end_frame = self.parse_time_or_frame(self.roi_def_widget.end_time_edit.text())
            if start_frame > end_frame:
                QMessageBox.warning(self, 
                                    QCoreApplication.translate("SubtitleOCRGUI", "警告"), 
                                    QCoreApplication.translate("SubtitleOCRGUI", "开始时间不能晚于结束时间。"))
                return None
            
            video_label = self.video_display_widget.video_label
            roi_entry = {
                'start_time': format_time(start_frame / self.fps if self.fps > 0 else 0),
                'end_time': format_time(end_frame / self.fps if self.fps > 0 else 0),
                'start_frame': start_frame,
                'end_frame': end_frame,
                'type': video_label.get_draw_mode()
            }

            # Shapes from the video label are already in original video pixel
            # coordinates (mapping to widget space happens only at paint time),
            # so they stay correct regardless of window size / zoom.
            if not video_label.get_base_pixmap(): return None
            if roi_entry['type'] == 'rect':
                rect_coords = video_label.get_current_drawing_roi()
                if not rect_coords: return None
                x, y, w, h = rect_coords
                roi_entry['points'] = [x, y, w, h]
            elif roi_entry['type'] == 'poly':
                poly_points = video_label.get_current_drawing_poly()
                if not poly_points: return None
                roi_entry['points'] = [[p.x(), p.y()] for p in poly_points]
            else:
                return None

            cr = self.roi_def_widget.get_color_restrict_dict()
            if cr:
                roi_entry['color_restrict'] = cr
            else:
                roi_entry.pop('color_restrict', None)

            roi_entry["blur_enabled"] = bool(self.roi_def_widget.blur_checkbox.isChecked())
            roi_entry["fade_in_refine_enabled"] = bool(self.roi_def_widget.fade_in_refine_checkbox.isChecked())
            roi_entry["write_pose_tags"] = bool(self.roi_def_widget.pose_tags_checkbox.isChecked())
            # 轨迹字幕亮度自适应(pose + 四点多边形走轨迹管线时生效)。
            roi_entry["motion_auto_brightness"] = bool(
                self.roi_def_widget.motion_brightness_checkbox.isChecked())
            # 场景文字显示策略(仅作用于场景文字事件;缺省 overlap)。
            roi_entry["scene_text_policy"] = (
                self.roi_def_widget.scene_text_policy_combo.currentData() or "overlap")
            # 手动绘制的 ROI（矩形/多边形/全宽带）永不参与文字来源过滤：
            # 场景字是本项目的核心产出，只有自动检测的"主字幕带"ROI 才按
            # 场景预设过滤（text_filter_policy="auto"）。
            roi_entry["source"] = "manual"
            roi_entry["text_filter_policy"] = "keep_all"
            # Picture placement metadata (\pos / \frz / \frx / \fry source).
            pose = self._compute_roi_pose(roi_entry['type'], roi_entry['points'])
            if pose:
                roi_entry["pose"] = pose
            else:
                roi_entry.pop("pose", None)
            return roi_entry
        except Exception as e:
            self.logger.error(QCoreApplication.translate("SubtitleOCRGUI", "创建 ROI 条目时出错：{}").format(e))
            QMessageBox.critical(self, 
                                 QCoreApplication.translate("SubtitleOCRGUI", "错误"), 
                                 QCoreApplication.translate("SubtitleOCRGUI", "创建 ROI 时发生错误：{}").format(e))
            return None

    def on_roi_selection_changed(self, current: QListWidgetItem, previous: QListWidgetItem):
        if not current:
            self.video_display_widget.video_label.set_rois_to_draw([], -1)
            self.roi_def_widget.set_color_restrict_from_roi(None)
            return

        selected_index = self.roi_list_widget.roi_list_widget.row(current)
        if not (0 <= selected_index < len(self.roi_data)):
            self.roi_def_widget.set_color_restrict_from_roi(None)
            return

        selected_roi = self.roi_data[selected_index]
        self.roi_def_widget.start_time_edit.setText(selected_roi['start_time'])
        self.roi_def_widget.end_time_edit.setText(selected_roi['end_time'])
        self.roi_def_widget.set_color_restrict_from_roi(selected_roi)

        # 画布点击触发的选中不跳帧：用户正要在当前位置拖动该 ROI。
        if not getattr(self, "_suppress_selection_seek", False):
            self.seek_video(selected_roi['start_frame'])
        self.update_all_rois_visibility()
        self.update_ui_state()

    def update_roi_list(self):
        self.roi_list_widget.update_list(self.roi_data)
        self.update_ui_state()

    def update_all_rois_visibility(self):
        if not self.cap: return

        video_label = self.video_display_widget.video_label

        current_item = self.roi_list_widget.roi_list_widget.currentItem()
        selected_index = self.roi_list_widget.roi_list_widget.row(current_item) if current_item else -1

        # Shapes are kept in original video coordinates; the video label maps
        # them to the current widget size at paint time, so overlays follow
        # window resizes / zoom without recomputation here. Items carry the
        # roi_data index so the canvas can select/edit the right entry.
        items = []
        for i, roi in enumerate(self.roi_data):
            if roi['start_frame'] <= self.current_frame_pos <= roi['end_frame']:
                shape = None
                if roi['type'] == 'rect':
                    x, y, w, h = roi['points']
                    shape = QRect(x, y, w, h)
                elif roi['type'] == 'poly':
                    points = [QPoint(int(p[0]), int(p[1])) for p in roi['points']]
                    shape = QPolygon(points)

                if shape:
                    items.append((i, shape))

        video_label.set_rois_to_draw(items, selected_index)

    @Slot(int)
    def on_roi_canvas_clicked(self, index: int):
        """编辑模式下用户在画面上按中已有 ROI：同步列表选中，但不跳帧。"""
        if not (0 <= index < len(self.roi_data)):
            return
        self._suppress_selection_seek = True
        try:
            self.roi_list_widget.roi_list_widget.setCurrentRow(index)
        finally:
            self._suppress_selection_seek = False

    @Slot(int, str, list)
    def on_roi_geometry_edited(self, index: int, kind: str, points: list):
        """画布拖动编辑完成：把新几何写回 roi_data 并刷新覆盖层。"""
        if not (0 <= index < len(self.roi_data)):
            return
        roi = self.roi_data[index]
        if roi.get("type", "rect") != kind:
            return
        if kind == "rect":
            if not (isinstance(points, list) and len(points) == 4):
                return
        elif kind == "poly":
            if not (isinstance(points, list) and len(points) >= 3):
                return
        else:
            return
        roi["points"] = points
        # pose（\pos/\frz）由几何推导，几何变了必须重算；未启用位置标签的
        # 自动 ROI 保持无 pose 字段不变。
        if roi.get("write_pose_tags") or "pose" in roi:
            pose = self._compute_roi_pose(kind, points)
            if pose:
                roi["pose"] = pose
            else:
                roi.pop("pose", None)
        self.control_panel_widget.invalidate_color_gate_confirmation()
        self.update_all_rois_visibility()
        self.logger.info(
            QCoreApplication.translate("SubtitleOCRGUI", "已在画面上调整 ROI {} 的区域").format(index)
        )

    @Slot(int)
    def copy_roi(self, index: int):
        if 0 <= index < len(self.roi_data):
            self.clipboard_roi = copy.deepcopy(self.roi_data[index])
            self.roi_list_widget.update_clipboard_state(True)
            self.logger.info(QCoreApplication.translate("SubtitleOCRGUI", "已将 ROI {} 复制到剪贴板。").format(index))

    @Slot(int)
    def paste_roi_after(self, index: int):
        if self.clipboard_roi is None: return
        new_roi = copy.deepcopy(self.clipboard_roi)
        self.roi_data.insert(index + 1, new_roi)
        self.update_roi_list()
        self.roi_list_widget.roi_list_widget.setCurrentRow(index + 1)
        self.control_panel_widget.invalidate_color_gate_confirmation()
        self.logger.info(QCoreApplication.translate("SubtitleOCRGUI", "已粘贴到 ROI {} 之后。").format(index))

    @Slot()
    def paste_roi_at_end(self):
        if self.clipboard_roi is None: return
        new_roi = copy.deepcopy(self.clipboard_roi)
        self.roi_data.append(new_roi)
        self.update_roi_list()
        self.roi_list_widget.roi_list_widget.setCurrentRow(len(self.roi_data) - 1)
        self.control_panel_widget.invalidate_color_gate_confirmation()
        self.logger.info(QCoreApplication.translate("SubtitleOCRGUI", "已将 ROI 粘贴到列表末尾。"))
