# main_window.py
import sys
import os
import hashlib
import tempfile
import cv2
import json
import numpy as np
import copy
from typing import Optional, List, Dict, Tuple, Union

from PySide6.QtWidgets import (
    QMainWindow,
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QFileDialog,
    QMessageBox,
    QListWidgetItem,
    QLineEdit,
    QProgressDialog,
    QSplitter,
    QScrollArea,
    QSizePolicy,
    QDialog,
)
from PySide6.QtGui import QImage, QPixmap, QTextCursor, QDesktopServices, QPolygon
from PySide6.QtCore import Qt, QRect, QPoint, QUrl, QTimer, QThread, Slot, QCoreApplication 

from utils.logger import setup_logger
from utils.time_utils import format_time, parse_time
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from core.pipeline_worker import PipelineWorker
from components.video_display import VideoDisplayWidget
from components.file_operations import FileOperationsWidget
from components.roi_definition import RoiDefinitionWidget
from components.roi_list import RoiListWidget
from components.control_panel import ControlPanelWidget
from components.log_viewer import LogViewerWidget
from components.deepseek_progress_panel import DeepSeekProgressPanel

class SubtitleOCRGUI(QMainWindow):
    def __init__(self):
        super().__init__()
        self.video_path: Optional[str] = None
        self.cap: Optional[cv2.VideoCapture] = None
        self.fps: float = 0.0
        self.total_frames: int = 0
        self.video_width: int = 0
        self.video_height: int = 0
        self.current_frame_pos: int = 0
        self.current_time_sec: float = 0.0
        self.roi_data: List[Dict] = []
        self.clipboard_roi: Optional[Dict] = None
        
        self.pipeline_worker: Optional["PipelineWorker"] = None
        self.progress_dialog: Optional[QProgressDialog] = None
        self._pipeline_llm_active: bool = False
        self._detector_thread: Optional[QThread] = None
        
        self.logger, self.log_handler = setup_logger()
        
        self._long_press_timer = QTimer(self)
        self._long_press_timer.setSingleShot(True)
        self._long_press_timer.setInterval(250)
        self._continuous_nav_timer = QTimer(self)
        self._continuous_nav_timer.setInterval(80)
        self._nav_target_edit: Optional[QLineEdit] = None
        self._nav_step: int = 0
        self._is_in_continuous_mode: bool = False

        self.setAcceptDrops(True)
        self.setup_ui()
        self.setup_connections()
        self.update_ui_state()

    def setup_ui(self):
        self.setWindowTitle(QCoreApplication.translate("SubtitleOCRGUI", "视频字幕 OCR 工具"))
        self.setGeometry(100, 100, 1300, 900)
        
        central_widget = QWidget()
        self.setCentralWidget(central_widget)

        # Balanced & clean spacing.
        main_layout = QHBoxLayout(central_widget)
        main_layout.setContentsMargins(10, 10, 10, 10)
        main_layout.setSpacing(10)

        splitter = QSplitter(Qt.Horizontal, central_widget)
        splitter.setChildrenCollapsible(False)
        splitter.setHandleWidth(6)
        main_layout.addWidget(splitter)

        # Left side: video + logs.
        left_panel = QWidget()
        left_panel_layout = QVBoxLayout(left_panel)
        left_panel_layout.setContentsMargins(0, 0, 0, 0)
        left_panel_layout.setSpacing(10)

        self.video_display_widget = VideoDisplayWidget()
        self.log_viewer_widget = LogViewerWidget()
        # Keep logs readable but not dominating the video area.
        self.log_viewer_widget.setMinimumHeight(120)
        self.log_viewer_widget.setMaximumHeight(220)
        self.log_viewer_widget.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Maximum)

        self.deepseek_progress_panel = DeepSeekProgressPanel()
        self.deepseek_progress_panel.set_panel_visible(False)

        left_panel_layout.addWidget(self.video_display_widget, 1)
        left_panel_layout.addWidget(self.log_viewer_widget, 0)
        left_panel_layout.addWidget(self.deepseek_progress_panel, 0)

        # Right side: a scrollable stack of control groups.
        right_panel_container = QWidget()
        right_panel_layout = QVBoxLayout(right_panel_container)
        right_panel_layout.setContentsMargins(0, 0, 0, 0)
        right_panel_layout.setSpacing(10)

        self.file_ops_widget = FileOperationsWidget()
        self.control_panel_widget = ControlPanelWidget()
        self.roi_def_widget = RoiDefinitionWidget()
        self.roi_list_widget = RoiListWidget()

        right_panel_layout.addWidget(self.file_ops_widget)
        right_panel_layout.addWidget(self.roi_def_widget)
        right_panel_layout.addWidget(self.roi_list_widget)
        right_panel_layout.addWidget(self.control_panel_widget)
        right_panel_layout.addStretch(1)

        right_scroll = QScrollArea()
        right_scroll.setWidgetResizable(True)
        right_scroll.setFrameShape(QScrollArea.NoFrame)
        right_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        right_scroll.setWidget(right_panel_container)

        splitter.addWidget(left_panel)
        splitter.addWidget(right_scroll)
        splitter.setStretchFactor(0, 7)
        splitter.setStretchFactor(1, 3)

    def setup_connections(self):
        self.file_ops_widget.load_video_requested.connect(self.load_video)
        self.file_ops_widget.save_roi_requested.connect(self.save_roi_config)
        self.file_ops_widget.load_roi_requested.connect(self.load_roi_config)

        self.video_display_widget.timeline_slider.sliderMoved.connect(self.seek_video)
        self.video_display_widget.timeline_slider.valueChanged.connect(self.seek_video)
        self.video_display_widget.video_label.roi_drawn.connect(self.on_roi_drawn)

        self.control_panel_widget.draw_mode_changed.connect(self.on_draw_mode_changed)
        self.control_panel_widget.browse_template_requested.connect(self.select_template_file)
        self.control_panel_widget.run_pipeline_requested.connect(self.run_ocr_pipeline)
        self.control_panel_widget.color_gate_preview_requested.connect(self.on_color_gate_preview)
        self.control_panel_widget.auto_detection_requested.connect(self._start_auto_detection)
        self.control_panel_widget.preset_changed_by_user.connect(self._on_preset_changed_by_user)

        self.roi_def_widget.navigate_pressed.connect(self._handle_nav_press)
        self.roi_def_widget.navigate_released.connect(self._handle_nav_release)
        self._long_press_timer.timeout.connect(self._start_continuous_mode)
        self._continuous_nav_timer.timeout.connect(self._navigate_continuously)
        self.roi_def_widget.time_edit_finished.connect(self.handle_time_edit)
        self.roi_def_widget.set_time_requested.connect(self.set_time_from_video)
        self.roi_def_widget.add_roi_requested.connect(self.add_roi)
        self.roi_def_widget.update_roi_requested.connect(self.update_selected_roi)
        self.roi_def_widget.delete_roi_requested.connect(self.delete_selected_roi)

        self.roi_list_widget.selection_changed.connect(self.on_roi_selection_changed)
        self.roi_list_widget.copy_requested.connect(self.copy_roi)
        self.roi_list_widget.paste_after_requested.connect(self.paste_roi_after)
        self.roi_list_widget.paste_at_end_requested.connect(self.paste_roi_at_end)
        self.roi_list_widget.delete_requested.connect(self.delete_roi_by_index)

        self.log_handler.new_record.connect(self.log_viewer_widget.append_log)

    def update_ui_state(self):
        video_loaded = self.cap is not None and self.cap.isOpened()
        roi_drawn = self.video_display_widget.video_label.is_roi_ready()
        item_selected = self.roi_list_widget.roi_list_widget.currentItem() is not None

        self.video_display_widget.timeline_slider.setEnabled(video_loaded)
        self.file_ops_widget.save_roi_btn.setEnabled(video_loaded and bool(self.roi_data))
        self.file_ops_widget.load_roi_btn.setEnabled(video_loaded)
        
        self.roi_def_widget.add_roi_btn.setEnabled(video_loaded and roi_drawn)
        self.roi_def_widget.update_roi_btn.setEnabled(video_loaded and roi_drawn and item_selected)
        self.roi_def_widget.delete_roi_btn.setEnabled(video_loaded and item_selected)
        
        for btn in [self.roi_def_widget.start_frame_backward, self.roi_def_widget.start_frame_forward,
                    self.roi_def_widget.end_frame_backward, self.roi_def_widget.end_frame_forward,
                    self.roi_def_widget.set_start_btn, self.roi_def_widget.set_end_btn]:
            btn.setEnabled(video_loaded)

        self.roi_def_widget.set_color_restrict_controls_enabled(video_loaded)

        self.control_panel_widget.run_pipeline_btn.setEnabled(video_loaded and bool(self.roi_data))
        self.control_panel_widget.set_color_gate_preview_allowed(video_loaded and bool(self.roi_data))

    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dropEvent(self, event):
        urls = event.mimeData().urls()
        for url in urls:
            file_path = url.toLocalFile()
            ext = os.path.splitext(file_path)[1].lower()
            
            if ext in ['.mp4', '.avi', '.mov', '.mkv']:
                self.load_video(file_path)
            elif ext == '.json':
                self.load_roi_config(file_path)
            elif ext == '.ass':
                self.control_panel_widget.template_path_edit.setText(file_path)
                self.logger.info(QCoreApplication.translate("SubtitleOCRGUI", "已通过拖放加载 ASS 模板：{}").format(file_path))

    def load_video(self, file_path=None):
        if not file_path:
            file_path, _ = QFileDialog.getOpenFileName(self, 
                                                     QCoreApplication.translate("SubtitleOCRGUI", "选择视频文件"), 
                                                     "", 
                                                     QCoreApplication.translate("SubtitleOCRGUI", "视频文件 (*.mp4 *.avi *.mov *.mkv)"))
            if not file_path: return
        if self.cap: self.cap.release()
        self.video_path = file_path
        self.cap = cv2.VideoCapture(self.video_path)
        if not self.cap.isOpened():
            QMessageBox.critical(self, 
                                 QCoreApplication.translate("SubtitleOCRGUI", "错误"), 
                                 QCoreApplication.translate("SubtitleOCRGUI", "无法打开视频文件"))
            self.cap = None; self.video_path = None
            return
        
        self.video_display_widget.video_label.setScaledContents(False)
        self.video_display_widget.video_label.setStyleSheet("background-color: black;")
        self.fps = self.cap.get(cv2.CAP_PROP_FPS)
        self.total_frames = int(self.cap.get(cv2.CAP_PROP_FRAME_COUNT))
        self.video_width = int(self.cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        self.video_height = int(self.cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        
        self.video_display_widget.timeline_slider.setRange(0, self.total_frames - 1 if self.total_frames > 0 else 0)
        
        self.roi_data.clear()
        self.update_roi_list()
        self.video_display_widget.video_label.clear_all_rois()
        # When no ROI is selected, the color-restrict UI should show defaults
        # (avoid "global" feeling when creating a new ROI).
        self.roi_def_widget.set_color_restrict_from_roi(None)
        self.log_viewer_widget.log_display.clear()
        
        self.current_frame_pos = 0
        self.seek_video(0)
        self.update_ui_state()
        self.setWindowTitle(QCoreApplication.translate("SubtitleOCRGUI", "视频字幕 OCR 工具 - {}").format(os.path.basename(self.video_path)))
        self.logger.info(QCoreApplication.translate("SubtitleOCRGUI", "视频已加载：{}").format(self.video_path))
        self.logger.info(QCoreApplication.translate("SubtitleOCRGUI", "分辨率：{}x{}，帧率：{:.2f}，总帧数：{}").format(self.video_width, self.video_height, self.fps, self.total_frames))
        self.control_panel_widget.reset_color_gate()

        # Start auto-detection of video type in background
        self._start_auto_detection()

        # Auto-set the end time edit to the last frame so that a newly drawn ROI
        # covers the full video by default (users can still adjust via the buttons).
        if self.total_frames > 0 and self.fps > 0:
            end_seconds = (self.total_frames - 1) / self.fps
            self.roi_def_widget.end_time_edit.setText(format_time(end_seconds))

        # Auto-load ROI autosave file if present (same naming as _autosave_roi_config_before_pipeline)
        autosave_path = os.path.splitext(os.path.abspath(self.video_path))[0] + "_roi_autosave.json"
        if os.path.isfile(autosave_path):
            try:
                with open(autosave_path, 'r', encoding='utf-8') as f:
                    self.roi_data = json.load(f)
                self.update_roi_list()
                self.update_all_rois_visibility()
                self.control_panel_widget.invalidate_color_gate_confirmation()
                self.logger.info(
                    QCoreApplication.translate("SubtitleOCRGUI", "已自动加载 ROI 配置：{}").format(autosave_path)
                )
            except Exception as e:
                self.logger.warning(
                    QCoreApplication.translate("SubtitleOCRGUI", "自动加载 ROI 配置失败：{}").format(e)
                )

        self.update_ui_state()

    def update_frame(self):
        if not self.cap or not self.cap.isOpened(): return
        self.cap.set(cv2.CAP_PROP_POS_FRAMES, self.current_frame_pos)
        ret, frame = self.cap.read()
        if not ret: return
        
        rgb_image = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        h, w, ch = rgb_image.shape
        bytes_per_line = ch * w
        q_img = QImage(rgb_image.data, w, h, bytes_per_line, QImage.Format_RGB888)
        self.video_display_widget.video_label.set_base_pixmap(QPixmap.fromImage(q_img))
        
        # Prefer OpenCV-provided playback timestamp when available (helps VFR / FPS mismatch).
        try:
            pos_msec = float(self.cap.get(cv2.CAP_PROP_POS_MSEC))
        except Exception:
            pos_msec = 0.0
        if pos_msec > 0:
            self.current_time_sec = pos_msec / 1000.0
        else:
            self.current_time_sec = self.current_frame_pos / self.fps if self.fps > 0 else 0.0

        current_time_sec = self.current_time_sec
        total_time_sec = self.total_frames / self.fps if self.fps > 0 else 0
        self.video_display_widget.frame_label.setText(f"{self.current_frame_pos}/{self.total_frames}")
        self.video_display_widget.time_label.setText(f"{format_time(current_time_sec)} / {format_time(total_time_sec)}")
        self.video_display_widget.timeline_slider.setValue(self.current_frame_pos)
        self.update_all_rois_visibility()

    def seek_video(self, frame_pos):
        self.current_frame_pos = frame_pos
        self.update_frame()

    def on_roi_drawn(self):
        # If user is drawing a NEW ROI (no list selection), reset per-ROI color settings to defaults.
        # Otherwise, keep showing the selected ROI's own settings for editing.
        if self.roi_list_widget.roi_list_widget.currentItem() is None:
            self.roi_def_widget.set_color_restrict_from_roi(None)
        self.update_ui_state()

    def on_draw_mode_changed(self, mode: str):
        self.video_display_widget.video_label.set_draw_mode(mode)
        self.update_ui_state()

    def set_time_from_video(self, time_edit: QLineEdit):
        if not self.cap: return
        time_edit.setText(format_time(self.current_time_sec))
    
    def _handle_nav_press(self, time_edit: QLineEdit, step: int):
        if not self.cap: return
        self._nav_target_edit = time_edit
        self._nav_step = step
        self._is_in_continuous_mode = False
        self._long_press_timer.start()

    def _handle_nav_release(self):
        self._long_press_timer.stop()
        self._continuous_nav_timer.stop()
        if not self._is_in_continuous_mode:
            self.navigate_frame(self._nav_target_edit, self._nav_step)
        self._nav_target_edit = None
        self._nav_step = 0
        self._is_in_continuous_mode = False

    def _start_continuous_mode(self):
        self._is_in_continuous_mode = True
        self._navigate_continuously()
        self._continuous_nav_timer.start()

    def _navigate_continuously(self):
        if self._nav_target_edit and self._nav_step != 0:
            self.navigate_frame(self._nav_target_edit, self._nav_step)

    def navigate_frame(self, time_edit: QLineEdit, step: int):
        if not self.cap or self.total_frames <= 0 or not time_edit: return
        new_frame = self.current_frame_pos + step
        new_frame = max(0, min(new_frame, self.total_frames - 1))
        if new_frame == self.current_frame_pos:
            if self._is_in_continuous_mode: self._continuous_nav_timer.stop()
            return
        self.current_frame_pos = new_frame
        self.update_frame()
        time_edit.setText(format_time(self.current_time_sec))

    def handle_time_edit(self, time_edit: QLineEdit):
        if not self.cap or self.total_frames <= 0: return
        try:
            frame_num = self.parse_time_or_frame(time_edit.text())
            self.current_frame_pos = frame_num
            self.update_frame()
            time_edit.setText(format_time(self.current_time_sec))
        except (ValueError, IndexError):
            time_edit.setText(format_time(self.current_time_sec))
            self.logger.warning(QCoreApplication.translate("SubtitleOCRGUI", "无效的时间/帧号输入：'{}'").format(time_edit.text()))

    def parse_time_or_frame(self, text: str) -> int:
        text = text.strip()
        if text.isdigit():
            frame_num = int(text)
        else:
            total_seconds = parse_time(text)
            frame_num = int(total_seconds * self.fps)
        return max(0, min(frame_num, self.total_frames - 1))

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
            self.control_panel_widget.invalidate_color_gate_confirmation()
            self.update_ui_state()
            self.logger.info(QCoreApplication.translate("SubtitleOCRGUI", "已删除 ROI {}").format(index))

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
            
            if video_label.get_scaled_pixmap_size().width() == 0: return None
            scale_x = self.video_width / video_label.get_scaled_pixmap_size().width()
            scale_y = self.video_height / video_label.get_scaled_pixmap_size().height()
            offset_x = (video_label.width() - video_label.get_scaled_pixmap_size().width()) // 2
            offset_y = (video_label.height() - video_label.get_scaled_pixmap_size().height()) // 2
            
            if roi_entry['type'] == 'rect':
                rect_coords = video_label.get_current_drawing_roi()
                if not rect_coords: return None
                x, y, w, h = rect_coords
                orig_x = int((x - offset_x) * scale_x)
                orig_y = int((y - offset_y) * scale_y)
                orig_w = int(w * scale_x)
                orig_h = int(h * scale_y)
                roi_entry['points'] = [orig_x, orig_y, orig_w, orig_h]
            elif roi_entry['type'] == 'poly':
                poly_points = video_label.get_current_drawing_poly()
                if not poly_points: return None
                orig_points = [[int((p.x() - offset_x) * scale_x), int((p.y() - offset_y) * scale_y)] for p in poly_points]
                roi_entry['points'] = orig_points
            else:
                return None

            cr = self.roi_def_widget.get_color_restrict_dict()
            if cr:
                roi_entry['color_restrict'] = cr
            else:
                roi_entry.pop('color_restrict', None)

            roi_entry["blur_enabled"] = bool(self.roi_def_widget.blur_checkbox.isChecked())
            roi_entry["fade_in_refine_enabled"] = bool(self.roi_def_widget.fade_in_refine_checkbox.isChecked())
            return roi_entry
        except Exception as e:
            self.logger.error(QCoreApplication.translate("SubtitleOCRGUI", "创建 ROI 条目时出错：{}").format(e))
            QMessageBox.critical(self, 
                                 QCoreApplication.translate("SubtitleOCRGUI", "错误"), 
                                 QCoreApplication.translate("SubtitleOCRGUI", "创建 ROI 时发生错误：{}").format(e))
            return None

    def on_roi_selection_changed(self, current: QListWidgetItem, previous: QListWidgetItem):
        if not current:
            self.video_display_widget.video_label.set_rois_to_draw([], None)
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
        
        self.seek_video(selected_roi['start_frame'])
        self.update_all_rois_visibility()
        self.update_ui_state()

    def update_roi_list(self):
        self.roi_list_widget.update_list(self.roi_data)
        self.update_ui_state()

    def update_all_rois_visibility(self):
        if not self.cap: return
        
        video_label = self.video_display_widget.video_label
        shapes_to_draw = []
        selected_shape = None
        
        current_item = self.roi_list_widget.roi_list_widget.currentItem()
        selected_index = self.roi_list_widget.roi_list_widget.row(current_item) if current_item else -1
        
        if video_label.get_scaled_pixmap_size().width() == 0: return
        scale_x = video_label.get_scaled_pixmap_size().width() / self.video_width
        scale_y = video_label.get_scaled_pixmap_size().height() / self.video_height
        offset_x = (video_label.width() - video_label.get_scaled_pixmap_size().width()) // 2
        offset_y = (video_label.height() - video_label.get_scaled_pixmap_size().height()) // 2

        for i, roi in enumerate(self.roi_data):
            if roi['start_frame'] <= self.current_frame_pos <= roi['end_frame']:
                shape = None
                if roi['type'] == 'rect':
                    x, y, w, h = roi['points']
                    scaled_x = int(x * scale_x + offset_x)
                    scaled_y = int(y * scale_y + offset_y)
                    scaled_w = int(w * scale_x)
                    scaled_h = int(h * scale_y)
                    shape = QRect(scaled_x, scaled_y, scaled_w, scaled_h)
                elif roi['type'] == 'poly':
                    points = [QPoint(int(p[0] * scale_x + offset_x), int(p[1] * scale_y + offset_y)) for p in roi['points']]
                    shape = QPolygon(points)
                
                if shape:
                    shapes_to_draw.append(shape)
                    if i == selected_index:
                        selected_shape = shape
                        
        video_label.set_rois_to_draw(shapes_to_draw, selected_shape)

    def save_roi_config(self):
        if not self.video_path: return
        default_path = os.path.splitext(self.video_path)[0] + '_roi.json'
        file_path, _ = QFileDialog.getSaveFileName(self, 
                                                 QCoreApplication.translate("SubtitleOCRGUI", "保存 ROI 配置"), 
                                                 default_path, 
                                                 QCoreApplication.translate("SubtitleOCRGUI", "JSON 文件 (*.json)"))
        if file_path:
            try:
                with open(file_path, 'w', encoding='utf-8') as f:
                    json.dump(self.roi_data, f, indent=4, ensure_ascii=False)
                self.logger.info(QCoreApplication.translate("SubtitleOCRGUI", "ROI 配置已保存到：{}").format(file_path))
            except Exception as e:
                QMessageBox.critical(self, 
                                     QCoreApplication.translate("SubtitleOCRGUI", "错误"), 
                                     QCoreApplication.translate("SubtitleOCRGUI", "保存 ROI 配置失败：{}").format(e))
                self.logger.error(QCoreApplication.translate("SubtitleOCRGUI", "保存 ROI 配置失败：{}").format(e))

    def _autosave_roi_config_before_pipeline(self) -> Optional[str]:
        """在启动识别前将当前 ROI 写入视频同目录，避免死机或进程异常导致配置丢失。"""
        if not self.video_path or not self.roi_data:
            return None
        video_dir = os.path.dirname(os.path.abspath(self.video_path))
        if not video_dir:
            video_dir = "."
        dest_path = os.path.splitext(os.path.abspath(self.video_path))[0] + "_roi_autosave.json"
        tmp_path: Optional[str] = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                dir=video_dir,
                prefix=".roi_autosave_",
                suffix=".tmp",
                delete=False,
            ) as f:
                tmp_path = f.name
                json.dump(self.roi_data, f, indent=4, ensure_ascii=False)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp_path, dest_path)
            tmp_path = None
        except Exception as e:
            if tmp_path and os.path.isfile(tmp_path):
                try:
                    os.remove(tmp_path)
                except OSError:
                    pass
            self.logger.warning(
                QCoreApplication.translate(
                    "SubtitleOCRGUI",
                    "自动备份 ROI 配置失败（识别仍会继续）：{}",
                ).format(e)
            )
            return None
        self.logger.info(
            QCoreApplication.translate(
                "SubtitleOCRGUI",
                "为防意外中断，已自动备份 ROI 配置到：{}",
            ).format(dest_path)
        )
        return dest_path

    def load_roi_config(self, file_path=None):
        if not self.video_path:
            QMessageBox.warning(self, 
                                QCoreApplication.translate("SubtitleOCRGUI", "警告"), 
                                QCoreApplication.translate("SubtitleOCRGUI", "请先加载视频文件。"))
            return
        if not file_path:
            default_dir = os.path.dirname(self.video_path)
            file_path, _ = QFileDialog.getOpenFileName(self, 
                                                     QCoreApplication.translate("SubtitleOCRGUI", "加载 ROI 配置"), 
                                                     default_dir, 
                                                     QCoreApplication.translate("SubtitleOCRGUI", "JSON 文件 (*.json)"))
        if file_path:
            try:
                with open(file_path, 'r', encoding='utf-8') as f:
                    self.roi_data = json.load(f)
                self.update_roi_list()
                self.update_all_rois_visibility()
                self.control_panel_widget.invalidate_color_gate_confirmation()
                self.logger.info(QCoreApplication.translate("SubtitleOCRGUI", "ROI 配置已从 {} 加载").format(file_path))
            except Exception as e:
                QMessageBox.critical(self, 
                                     QCoreApplication.translate("SubtitleOCRGUI", "错误"), 
                                     QCoreApplication.translate("SubtitleOCRGUI", "加载 ROI 配置失败：{}").format(e))
                self.logger.error(QCoreApplication.translate("SubtitleOCRGUI", "加载 ROI 配置失败：{}").format(e))

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

    def select_template_file(self):
        file_path, _ = QFileDialog.getOpenFileName(self, 
                                                 QCoreApplication.translate("SubtitleOCRGUI", "选择 ASS 模板文件"), 
                                                 "", 
                                                 QCoreApplication.translate("SubtitleOCRGUI", "ASS 字幕文件 (*.ass)"))
        if file_path:
            self.control_panel_widget.template_path_edit.setText(file_path)

    def on_color_gate_preview(self):
        if not self.video_path or not self.roi_data or not self.cap:
            QMessageBox.warning(
                self,
                QCoreApplication.translate("SubtitleOCRGUI", "警告"),
                QCoreApplication.translate("SubtitleOCRGUI", "请先加载视频并至少定义一个 ROI。"),
            )
            return
        covered = False
        for r in self.roi_data:
            if r["start_frame"] <= self.current_frame_pos <= r["end_frame"]:
                covered = True
                break
        if not covered:
            QMessageBox.warning(
                self,
                QCoreApplication.translate("SubtitleOCRGUI", "警告"),
                QCoreApplication.translate(
                    "SubtitleOCRGUI",
                    "当前帧不在任一 ROI 的时间范围内。请将时间轴移到含字幕的典型帧上，用于自动标定颜色。",
                ),
            )
            return
        from core.color_presence_gate import run_preview
        from components.color_gate_preview_dialog import ColorGatePreviewDialog

        try:
            pack = run_preview(
                self.video_path,
                self.roi_data,
                self.total_frames,
                float(self.fps or 0.0),
                int(self.current_frame_pos),
                0.008,
            )
        except RuntimeError as e:
            QMessageBox.warning(
                self,
                QCoreApplication.translate("SubtitleOCRGUI", "预览失败"),
                str(e),
            )
            return
        dlg = ColorGatePreviewDialog(self, pack)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            spec = dlg.selected_spec()
            if spec is not None:
                self.control_panel_widget.adopt_color_gate_spec(spec)
                self.logger.info(
                    QCoreApplication.translate(
                        "SubtitleOCRGUI",
                        "颜色门控已确认；下次运行「字幕 OCR 识别」时将在阶段一启用。",
                    )
                )
            return
        self.control_panel_widget.abandon_color_gate_after_preview()
        self.update_ui_state()

    def run_ocr_pipeline(self):
        # Lazy import to keep UI startup lightweight and allow opening the GUI
        # without OCR dependencies installed yet.
        from core.pipeline_worker import PipelineWorker
        if not self.video_path or not self.roi_data:
            QMessageBox.warning(self, 
                                QCoreApplication.translate("SubtitleOCRGUI", "警告"), 
                                QCoreApplication.translate("SubtitleOCRGUI", "请先加载视频并至少定义一个 ROI。"))
            return

        options = self.control_panel_widget.get_pipeline_options()

        gate_spec = self.control_panel_widget.get_color_presence_gate_spec()
        if self.control_panel_widget.color_gate_checkbox.isChecked() and gate_spec is None:
            QMessageBox.warning(
                self,
                QCoreApplication.translate("SubtitleOCRGUI", "警告"),
                QCoreApplication.translate(
                    "SubtitleOCRGUI",
                    "已勾选「按字幕颜色跳过疑似无字帧」，但尚未通过预览确认。"
                    "请点击「预览检测效果」并在满意时选择「采用」，或取消勾选以使用默认流程。",
                ),
            )
            return

        subtitle_polisher = None
        if options.get("deepseek_polish") or options.get("deepseek_strategy_review") or options.get("deepseek_fragment_merge"):
            from core.subtitle_llm_polish import DEFAULT_DEEPSEEK_BASE, DEFAULT_DEEPSEEK_MODEL, SubtitlePolisherConfig

            api_key = (options.get("deepseek_api_key") or "").strip() or (
                os.environ.get("DEEPSEEK_API_KEY") or ""
            ).strip()
            if not api_key:
                QMessageBox.warning(
                    self,
                    QCoreApplication.translate("SubtitleOCRGUI", "警告"),
                    QCoreApplication.translate(
                        "SubtitleOCRGUI",
                        "已启用 DeepSeek 功能（润色/碎片合并/策略复核），但未填写 API Key。"
                        "请填写 API Key，或设置环境变量 DEEPSEEK_API_KEY。",
                    ),
                )
                return
            base = (options.get("deepseek_api_base") or "").strip() or DEFAULT_DEEPSEEK_BASE
            model = (options.get("deepseek_model") or "").strip() or DEFAULT_DEEPSEEK_MODEL
            subtitle_polisher = SubtitlePolisherConfig(
                api_key=api_key,
                api_base_url=base.rstrip("/"),
                model=model,
                text_polish_enabled=bool(options.get("deepseek_polish")),
                fragment_merge_enabled=bool(options.get("deepseek_fragment_merge")),
                strategy_review_enabled=bool(options.get("deepseek_strategy_review")),
            )
        
        video_dir = os.path.dirname(self.video_path)
        video_filename = os.path.splitext(os.path.basename(self.video_path))[0]
        output_ass_path = os.path.join(video_dir, f"{video_filename}.ass")

        output_ass_path, _ = QFileDialog.getSaveFileName(
            self, 
            QCoreApplication.translate("SubtitleOCRGUI", "保存字幕文件"), 
            output_ass_path, 
            QCoreApplication.translate("SubtitleOCRGUI", "ASS 字幕 (*.ass)")
        )
        if not output_ass_path:
            self.logger.info(QCoreApplication.translate("SubtitleOCRGUI", "用户取消保存，OCR 任务已中止。"))
            return

        self._autosave_roi_config_before_pipeline()

        self._pipeline_llm_active = subtitle_polisher is not None
        if self._pipeline_llm_active:
            self.deepseek_progress_panel.clear()
            self.deepseek_progress_panel.set_panel_visible(True)
            self.deepseek_progress_panel.append_line(
                QCoreApplication.translate("SubtitleOCRGUI", "[LLM] 已启用 DeepSeek —— 第 4 步的大模型进度会显示在下方。")
            )
        else:
            self.deepseek_progress_panel.set_panel_visible(False)

        dlg_title = (
            QCoreApplication.translate("SubtitleOCRGUI", "字幕 OCR + DeepSeek")
            if subtitle_polisher
            else QCoreApplication.translate("SubtitleOCRGUI", "字幕 OCR")
        )
        self.progress_dialog = QProgressDialog(QCoreApplication.translate("SubtitleOCRGUI", "正在处理视频..."), 
                                               QCoreApplication.translate("SubtitleOCRGUI", "取消"), 
                                               0, 100, self)
        self.progress_dialog.setWindowTitle(dlg_title)
        self.progress_dialog.setMinimumDuration(0)
        self.progress_dialog.setWindowModality(Qt.WindowModal)
        self.progress_dialog.setAutoClose(True)
        self.progress_dialog.show()

        self.pipeline_worker = PipelineWorker(
            video_path=self.video_path,
            roi_data=self.roi_data,
            total_frames=self.total_frames,
            fps=self.fps,
            video_width=self.video_width,
            video_height=self.video_height,
            output_ass_path=output_ass_path,
            debug_mode=options["debug"],
            template_path=options["template_path"],
            in_memory_ocr=options["in_memory"],
            visualize=options["visualize"],
            save_intermediate_json=options.get("save_intermediate_json", False),
            time_slice_enabled=options.get("time_slice_enabled", False),
            time_slice_seconds=float(options.get("time_slice_seconds", 10.0)),
            merge_rois=bool(options.get("merge_rois", False)),
            subtitle_polisher=subtitle_polisher,
            color_presence_gate_spec=gate_spec,
            ocr_engine_id=options.get("ocr_engine_id", ""),
            source_filter_config=options.get("source_filter_config"),
        )
        
        self.pipeline_worker.progress_updated.connect(self._on_pipeline_progress)
        self.pipeline_worker.llm_detail.connect(self.deepseek_progress_panel.append_line)
        self.pipeline_worker.finished.connect(self._on_pipeline_finished)
        self.pipeline_worker.error.connect(self._on_pipeline_error)
        self.progress_dialog.canceled.connect(self.pipeline_worker.cancel)
        
        self.pipeline_worker.start()

    def _on_pipeline_progress(self, value: int, message: str):
        if self.progress_dialog:
            self.progress_dialog.setValue(value)
            self.progress_dialog.setLabelText(message)
        if getattr(self, "_pipeline_llm_active", False):
            m = (message or "").strip()
            if not m:
                return
            low = m.lower()
            keys = (
                "deepseek",
                "step 4/4",
                "fragment",
                "polish",
                "strategy",
                "post-processing",
                "llm",
            )
            if any(k in low for k in keys):
                self.deepseek_progress_panel.append_line(f"[{int(value)}%] {m}")

    def _on_pipeline_finished(self, output_path: str):
        if self.progress_dialog:
            self.progress_dialog.setValue(100)
        if getattr(self, "_pipeline_llm_active", False):
            self.deepseek_progress_panel.append_line(
                QCoreApplication.translate("SubtitleOCRGUI", "[LLM] 完成 —— 已写入 ASS 文件。")
            )
        self._pipeline_llm_active = False
        self._save_source_config()
        QMessageBox.information(self, 
                                QCoreApplication.translate("SubtitleOCRGUI", "完成"), 
                                QCoreApplication.translate("SubtitleOCRGUI", "字幕文件已生成：{}").format(output_path))
        reply = QMessageBox.question(self, 
                                     QCoreApplication.translate("SubtitleOCRGUI", "打开目录"), 
                                     QCoreApplication.translate("SubtitleOCRGUI", "是否打开包含该文件的文件夹？"),
                                     QMessageBox.Yes | QMessageBox.No, QMessageBox.Yes)
        if reply == QMessageBox.Yes:
            QDesktopServices.openUrl(QUrl.fromLocalFile(os.path.dirname(output_path)))
        self.pipeline_worker = None

    def _on_pipeline_error(self, error_message: str):
        if self.progress_dialog:
            self.progress_dialog.close()
        if getattr(self, "_pipeline_llm_active", False):
            self.deepseek_progress_panel.append_line(
                QCoreApplication.translate("SubtitleOCRGUI", "[LLM] 已中止或出错 —— 请查看弹窗提示。")
            )
        self._pipeline_llm_active = False
        QMessageBox.critical(self, 
                             QCoreApplication.translate("SubtitleOCRGUI", "错误"), 
                             QCoreApplication.translate("SubtitleOCRGUI", "处理过程中发生错误：\n{}").format(error_message))
        self.pipeline_worker = None

    # ── Auto-detection ───────────────────────────────────────

    def _start_auto_detection(self):
        """Start background thread for video type auto-detection."""
        if not self.video_path or not self.cap:
            return

        # Check for saved config first
        saved_config = self._load_saved_source_config(self.video_path)
        if saved_config:
            self.control_panel_widget.restore_saved_config(saved_config)
            self.logger.info(
                QCoreApplication.translate("SubtitleOCRGUI", "已恢复上次保存的文字来源过滤设置。")
            )
            return

        from components.video_detector_thread import VideoDetectorThread

        self._detector_thread = VideoDetectorThread(
            video_path=self.video_path,
            roi_data=self.roi_data,
            fps=self.fps,
            total_frames=self.total_frames,
            video_width=self.video_width,
            video_height=self.video_height,
        )
        self._detector_thread.detection_done.connect(self._on_auto_detection_done)
        self._detector_thread.detection_error.connect(self._on_auto_detection_error)
        self._detector_thread.finished.connect(lambda: setattr(self, '_detector_thread', None))
        self._detector_thread.start()

        self.control_panel_widget.show_detection_in_progress()

    def _on_auto_detection_done(self, result) -> None:
        """Handle completed auto-detection."""
        self.control_panel_widget.apply_detection_result(result)
        self.logger.info(
            QCoreApplication.translate(
                "SubtitleOCRGUI",
                "视频类型自动检测完成：{} (置信度 {:.0%})"
            ).format(result.detected_type, result.confidence)
        )

    def _on_auto_detection_error(self, error_msg: str) -> None:
        """Handle auto-detection error."""
        self.logger.warning(
            QCoreApplication.translate(
                "SubtitleOCRGUI",
                "视频类型自动检测失败：{}"
            ).format(error_msg)
        )
        self.control_panel_widget.source_status_label.setText(
            '⚠️ <span style="color:#cc3300">'
            + QCoreApplication.translate("SubtitleOCRGUI", "自动分析失败，请手动选择场景类型")
            + '</span>'
        )

    def _on_preset_changed_by_user(self, preset_id: str) -> None:
        """User switched preset via alternative buttons."""
        self._save_source_config()

    # ── Source config persistence ────────────────────────────

    def _save_source_config(self) -> None:
        """Persist user's source filter choices via QSettings."""
        if not self.video_path:
            return
        try:
            from PySide6.QtCore import QSettings
            video_hash = hashlib.md5(self.video_path.encode()).hexdigest()
            config = {
                "preset_id": self.control_panel_widget.get_active_preset_id(),
                "keep_overlay": self.control_panel_widget.keep_overlay_checkbox.isChecked(),
                "keep_scene": self.control_panel_widget.keep_scene_checkbox.isChecked(),
                "keep_unknown": self.control_panel_widget.keep_unknown_checkbox.isChecked(),
                "user_overrode": self.control_panel_widget._user_overrode,
            }
            settings = QSettings()
            settings.setValue(f"source_filter/{video_hash}", json.dumps(config))
        except Exception as e:
            self.logger.debug(f"Failed to save source filter config: {e}")

    def _load_saved_source_config(self, video_path: str) -> Optional[dict]:
        """Load previously saved source filter choices."""
        try:
            from PySide6.QtCore import QSettings
            video_hash = hashlib.md5(video_path.encode()).hexdigest()
            settings = QSettings()
            raw = settings.value(f"source_filter/{video_hash}")
            if raw:
                return json.loads(raw)
        except Exception:
            pass
        return None

    def closeEvent(self, event):
        if self.pipeline_worker and self.pipeline_worker.isRunning():
            reply = QMessageBox.question(self, 
                                         QCoreApplication.translate("SubtitleOCRGUI", "确认退出"), 
                                         QCoreApplication.translate("SubtitleOCRGUI", "后台任务仍在运行，确定要退出吗？"),
                                         QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
            if reply == QMessageBox.Yes:
                self.pipeline_worker.cancel()
                event.accept()
            else:
                event.ignore()
        else:
            event.accept()

