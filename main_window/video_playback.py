import json
import os

import cv2

from PySide6.QtCore import QCoreApplication
from PySide6.QtGui import QImage, QPixmap
from PySide6.QtWidgets import QFileDialog, QLineEdit, QMessageBox

from utils.time_utils import format_time, parse_time


class VideoPlaybackMixin:
    """视频加载与时间轴导航（原 main_window.py SubtitleOCRGUI L233-410）。"""

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

        # A scan still running for the previous video must not deliver its
        # report into the freshly cleared roi_data.
        self._cancel_active_scan()
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

        # 加载后仍无 ROI（未加载自动保存且用户未禁用）时，后台快速扫描生成
        # 初始字幕带 ROI，免去首次手动绘制。
        if not self.roi_data:
            self._start_auto_roi_suggest(on_load=True)

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
