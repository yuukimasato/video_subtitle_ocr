# main_window.py
import sys
import os
import cv2
import json
import numpy as np
import copy
from typing import Optional, List, Dict, Tuple, Union

from PySide6.QtWidgets import (QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QFileDialog, 
                             QMessageBox, QListWidgetItem, QLineEdit, QProgressDialog)
from PySide6.QtGui import QImage, QPixmap, QTextCursor, QDesktopServices, QPolygon
from PySide6.QtCore import Qt, QRect, QPoint, QUrl, QTimer, QThread, Slot, QCoreApplication 

from utils.logger import setup_logger
from utils.time_utils import format_time, parse_time
from core.pipeline_worker import PipelineWorker
from components.video_display import VideoDisplayWidget
from components.file_operations import FileOperationsWidget
from components.roi_definition import RoiDefinitionWidget
from components.roi_list import RoiListWidget
from components.control_panel import ControlPanelWidget
from components.log_viewer import LogViewerWidget

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
        self.roi_data: List[Dict] = []
        self.clipboard_roi: Optional[Dict] = None
        
        self.pipeline_worker: Optional[PipelineWorker] = None
        self.progress_dialog: Optional[QProgressDialog] = None
        
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
        self.setWindowTitle(QCoreApplication.translate("SubtitleOCRGUI", "Video Subtitle OCR Tool"))
        self.setGeometry(100, 100, 1300, 900)
        
        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        main_layout = QHBoxLayout(central_widget)

        left_panel_layout = QVBoxLayout()
        self.video_display_widget = VideoDisplayWidget()
        self.log_viewer_widget = LogViewerWidget()
        left_panel_layout.addWidget(self.video_display_widget, 1)
        left_panel_layout.addWidget(self.log_viewer_widget, 0)
        main_layout.addLayout(left_panel_layout, 7)

        right_panel_layout = QVBoxLayout()
        self.file_ops_widget = FileOperationsWidget()
        self.control_panel_widget = ControlPanelWidget()
        self.roi_def_widget = RoiDefinitionWidget()
        self.roi_list_widget = RoiListWidget()
        
        right_panel_layout.addWidget(self.file_ops_widget)
        right_panel_layout.addWidget(self.roi_def_widget)
        right_panel_layout.addWidget(self.roi_list_widget)
        right_panel_layout.addWidget(self.control_panel_widget)
        right_panel_layout.addStretch()
        main_layout.addLayout(right_panel_layout, 3)

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
            
        self.control_panel_widget.run_pipeline_btn.setEnabled(video_loaded and bool(self.roi_data))

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
                self.logger.info(QCoreApplication.translate("SubtitleOCRGUI", "ASS template file loaded via drag and drop: {}").format(file_path))

    def load_video(self, file_path=None):
        if not file_path:
            file_path, _ = QFileDialog.getOpenFileName(self, 
                                                     QCoreApplication.translate("SubtitleOCRGUI", "Select Video File"), 
                                                     "", 
                                                     QCoreApplication.translate("SubtitleOCRGUI", "Video Files (*.mp4 *.avi *.mov *.mkv)"))
            if not file_path: return
        if self.cap: self.cap.release()
        self.video_path = file_path
        self.cap = cv2.VideoCapture(self.video_path)
        if not self.cap.isOpened():
            QMessageBox.critical(self, 
                                 QCoreApplication.translate("SubtitleOCRGUI", "Error"), 
                                 QCoreApplication.translate("SubtitleOCRGUI", "Unable to open video file"))
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
        self.log_viewer_widget.log_display.clear()
        
        self.current_frame_pos = 0
        self.seek_video(0)
        self.update_ui_state()
        self.setWindowTitle(QCoreApplication.translate("SubtitleOCRGUI", "Video Subtitle OCR Tool - {}").format(os.path.basename(self.video_path)))
        self.logger.info(QCoreApplication.translate("SubtitleOCRGUI", "Video loaded: {}").format(self.video_path))
        self.logger.info(QCoreApplication.translate("SubtitleOCRGUI", "Resolution: {}x{}, FPS: {:.2f}, Total frames: {}").format(self.video_width, self.video_height, self.fps, self.total_frames))

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
        
        current_time_sec = self.current_frame_pos / self.fps if self.fps > 0 else 0
        total_time_sec = self.total_frames / self.fps if self.fps > 0 else 0
        self.video_display_widget.frame_label.setText(f"{self.current_frame_pos}/{self.total_frames}")
        self.video_display_widget.time_label.setText(f"{format_time(current_time_sec)} / {format_time(total_time_sec)}")
        self.video_display_widget.timeline_slider.setValue(self.current_frame_pos)
        self.update_all_rois_visibility()

    def seek_video(self, frame_pos):
        self.current_frame_pos = frame_pos
        self.update_frame()

    def on_roi_drawn(self):
        self.update_ui_state()

    def on_draw_mode_changed(self, mode: str):
        self.video_display_widget.video_label.set_draw_mode(mode)
        self.update_ui_state()

    def set_time_from_video(self, time_edit: QLineEdit):
        if not self.cap: return
        current_time = self.current_frame_pos / self.fps if self.fps > 0 else 0
        time_edit.setText(format_time(current_time))
    
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
        current_time_sec = self.current_frame_pos / self.fps
        time_edit.setText(format_time(current_time_sec))

    def handle_time_edit(self, time_edit: QLineEdit):
        if not self.cap or self.total_frames <= 0: return
        try:
            frame_num = self.parse_time_or_frame(time_edit.text())
            self.current_frame_pos = frame_num
            self.update_frame()
            current_time_sec = self.current_frame_pos / self.fps if self.fps > 0 else 0
            time_edit.setText(format_time(current_time_sec))
        except (ValueError, IndexError):
            current_time_sec = self.current_frame_pos / self.fps if self.fps > 0 else 0
            time_edit.setText(format_time(current_time_sec))
            self.logger.warning(QCoreApplication.translate("SubtitleOCRGUI", "Invalid time/frame number input: '{}'").format(time_edit.text()))

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
            self.update_ui_state()
            self.logger.info(QCoreApplication.translate("SubtitleOCRGUI", "Added new ROI, frame range: {}-{}").format(roi_entry['start_frame'], roi_entry['end_frame']))

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
            self.update_ui_state()
            self.logger.info(QCoreApplication.translate("SubtitleOCRGUI", "Updated ROI {}, new frame range: {}-{}").format(selected_index, roi_entry['start_frame'], roi_entry['end_frame']))

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
            self.update_ui_state()
            self.logger.info(QCoreApplication.translate("SubtitleOCRGUI", "Deleted ROI {}").format(index))

    def _create_roi_entry_from_ui(self) -> Optional[Dict]:
        try:
            start_frame = self.parse_time_or_frame(self.roi_def_widget.start_time_edit.text())
            end_frame = self.parse_time_or_frame(self.roi_def_widget.end_time_edit.text())
            if start_frame > end_frame:
                QMessageBox.warning(self, 
                                    QCoreApplication.translate("SubtitleOCRGUI", "Warning"), 
                                    QCoreApplication.translate("SubtitleOCRGUI", "Start time cannot be later than end time."))
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
            return roi_entry
        except Exception as e:
            self.logger.error(QCoreApplication.translate("SubtitleOCRGUI", "Error creating ROI entry: {}").format(e))
            QMessageBox.critical(self, 
                                 QCoreApplication.translate("SubtitleOCRGUI", "Error"), 
                                 QCoreApplication.translate("SubtitleOCRGUI", "An error occurred while creating ROI: {}").format(e))
            return None

    def on_roi_selection_changed(self, current: QListWidgetItem, previous: QListWidgetItem):
        if not current:
            self.video_display_widget.video_label.set_rois_to_draw([], None)
            return
        
        selected_index = self.roi_list_widget.roi_list_widget.row(current)
        if not (0 <= selected_index < len(self.roi_data)): return
        
        selected_roi = self.roi_data[selected_index]
        self.roi_def_widget.start_time_edit.setText(selected_roi['start_time'])
        self.roi_def_widget.end_time_edit.setText(selected_roi['end_time'])
        
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
                                                 QCoreApplication.translate("SubtitleOCRGUI", "Save ROI Configuration"), 
                                                 default_path, 
                                                 QCoreApplication.translate("SubtitleOCRGUI", "JSON Files (*.json)"))
        if file_path:
            try:
                with open(file_path, 'w', encoding='utf-8') as f:
                    json.dump(self.roi_data, f, indent=4, ensure_ascii=False)
                self.logger.info(QCoreApplication.translate("SubtitleOCRGUI", "ROI configuration saved to: {}").format(file_path))
            except Exception as e:
                QMessageBox.critical(self, 
                                     QCoreApplication.translate("SubtitleOCRGUI", "Error"), 
                                     QCoreApplication.translate("SubtitleOCRGUI", "Failed to save ROI configuration: {}").format(e))
                self.logger.error(QCoreApplication.translate("SubtitleOCRGUI", "Failed to save ROI configuration: {}").format(e))

    def load_roi_config(self, file_path=None):
        if not self.video_path:
            QMessageBox.warning(self, 
                                QCoreApplication.translate("SubtitleOCRGUI", "Warning"), 
                                QCoreApplication.translate("SubtitleOCRGUI", "Please load a video file first."))
            return
        if not file_path:
            default_dir = os.path.dirname(self.video_path)
            file_path, _ = QFileDialog.getOpenFileName(self, 
                                                     QCoreApplication.translate("SubtitleOCRGUI", "Load ROI Configuration"), 
                                                     default_dir, 
                                                     QCoreApplication.translate("SubtitleOCRGUI", "JSON Files (*.json)"))
        if file_path:
            try:
                with open(file_path, 'r', encoding='utf-8') as f:
                    self.roi_data = json.load(f)
                self.update_roi_list()
                self.update_all_rois_visibility()
                self.logger.info(QCoreApplication.translate("SubtitleOCRGUI", "ROI configuration loaded from {}").format(file_path))
            except Exception as e:
                QMessageBox.critical(self, 
                                     QCoreApplication.translate("SubtitleOCRGUI", "Error"), 
                                     QCoreApplication.translate("SubtitleOCRGUI", "Failed to load ROI configuration: {}").format(e))
                self.logger.error(QCoreApplication.translate("SubtitleOCRGUI", "Failed to load ROI configuration: {}").format(e))

    @Slot(int)
    def copy_roi(self, index: int):
        if 0 <= index < len(self.roi_data):
            self.clipboard_roi = copy.deepcopy(self.roi_data[index])
            self.roi_list_widget.update_clipboard_state(True)
            self.logger.info(QCoreApplication.translate("SubtitleOCRGUI", "Copied ROI {} to clipboard.").format(index))

    @Slot(int)
    def paste_roi_after(self, index: int):
        if self.clipboard_roi is None: return
        new_roi = copy.deepcopy(self.clipboard_roi)
        self.roi_data.insert(index + 1, new_roi)
        self.update_roi_list()
        self.roi_list_widget.roi_list_widget.setCurrentRow(index + 1)
        self.logger.info(QCoreApplication.translate("SubtitleOCRGUI", "Pasted after ROI {}.").format(index))

    @Slot()
    def paste_roi_at_end(self):
        if self.clipboard_roi is None: return
        new_roi = copy.deepcopy(self.clipboard_roi)
        self.roi_data.append(new_roi)
        self.update_roi_list()
        self.roi_list_widget.roi_list_widget.setCurrentRow(len(self.roi_data) - 1)
        self.logger.info(QCoreApplication.translate("SubtitleOCRGUI", "Pasted ROI at the end of the list."))

    def select_template_file(self):
        file_path, _ = QFileDialog.getOpenFileName(self, 
                                                 QCoreApplication.translate("SubtitleOCRGUI", "Select ASS Template File"), 
                                                 "", 
                                                 QCoreApplication.translate("SubtitleOCRGUI", "ASS Subtitle Files (*.ass)"))
        if file_path:
            self.control_panel_widget.template_path_edit.setText(file_path)

    def run_ocr_pipeline(self):
        if not self.video_path or not self.roi_data:
            QMessageBox.warning(self, 
                                QCoreApplication.translate("SubtitleOCRGUI", "Warning"), 
                                QCoreApplication.translate("SubtitleOCRGUI", "Please load a video and define at least one ROI first."))
            return

        options = self.control_panel_widget.get_pipeline_options()
        
        video_dir = os.path.dirname(self.video_path)
        video_filename = os.path.splitext(os.path.basename(self.video_path))[0]
        output_ass_path = os.path.join(video_dir, f"{video_filename}.ass")

        output_ass_path, _ = QFileDialog.getSaveFileName(
            self, 
            QCoreApplication.translate("SubtitleOCRGUI", "Save Subtitle File"), 
            output_ass_path, 
            QCoreApplication.translate("SubtitleOCRGUI", "ASS Subtitles (*.ass)")
        )
        if not output_ass_path:
            self.logger.info(QCoreApplication.translate("SubtitleOCRGUI", "User canceled save operation, OCR task aborted."))
            return

        self.progress_dialog = QProgressDialog(QCoreApplication.translate("SubtitleOCRGUI", "Processing video..."), 
                                               QCoreApplication.translate("SubtitleOCRGUI", "Cancel"), 
                                               0, 100, self)
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
            visualize=options["visualize"]
        )
        
        self.pipeline_worker.progress_updated.connect(self._on_pipeline_progress)
        self.pipeline_worker.finished.connect(self._on_pipeline_finished)
        self.pipeline_worker.error.connect(self._on_pipeline_error)
        self.progress_dialog.canceled.connect(self.pipeline_worker.cancel)
        
        self.pipeline_worker.start()

    def _on_pipeline_progress(self, value: int, message: str):
        if self.progress_dialog:
            self.progress_dialog.setValue(value)
            self.progress_dialog.setLabelText(message)

    def _on_pipeline_finished(self, output_path: str):
        if self.progress_dialog:
            self.progress_dialog.setValue(100)
        QMessageBox.information(self, 
                                QCoreApplication.translate("SubtitleOCRGUI", "Complete"), 
                                QCoreApplication.translate("SubtitleOCRGUI", "Subtitle file generated: {}").format(output_path))
        reply = QMessageBox.question(self, 
                                     QCoreApplication.translate("SubtitleOCRGUI", "Open Directory"), 
                                     QCoreApplication.translate("SubtitleOCRGUI", "Do you want to open the folder containing the file?"),
                                     QMessageBox.Yes | QMessageBox.No, QMessageBox.Yes)
        if reply == QMessageBox.Yes:
            QDesktopServices.openUrl(QUrl.fromLocalFile(os.path.dirname(output_path)))
        self.pipeline_worker = None

    def _on_pipeline_error(self, error_message: str):
        if self.progress_dialog:
            self.progress_dialog.close()
        QMessageBox.critical(self, 
                             QCoreApplication.translate("SubtitleOCRGUI", "Error"), 
                             QCoreApplication.translate("SubtitleOCRGUI", "An error occurred during processing:\n{}").format(error_message))
        self.pipeline_worker = None

    def closeEvent(self, event):
        if self.pipeline_worker and self.pipeline_worker.isRunning():
            reply = QMessageBox.question(self, 
                                         QCoreApplication.translate("SubtitleOCRGUI", "Confirm Exit"), 
                                         QCoreApplication.translate("SubtitleOCRGUI", "Background task is still running, are you sure you want to exit?"),
                                         QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
            if reply == QMessageBox.Yes:
                self.pipeline_worker.cancel()
                event.accept()
            else:
                event.ignore()
        else:
            event.accept()

