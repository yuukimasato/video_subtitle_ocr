import os
import cv2

from typing import Dict, List, Optional, Tuple, TYPE_CHECKING

from PySide6.QtCore import Qt, QThread, QTimer, QCoreApplication
from PySide6.QtWidgets import (
    QMainWindow,
    QHBoxLayout,
    QLineEdit,
    QMessageBox,
    QProgressDialog,
    QScrollArea,
    QSizePolicy,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from utils.logger import setup_logger

from components.video_display import VideoDisplayWidget
from components.file_operations import FileOperationsWidget
from components.roi_definition import RoiDefinitionWidget
from components.roi_list import RoiListWidget
from components.control_panel import ControlPanelWidget
from components.log_viewer import LogViewerWidget
from components.deepseek_progress_panel import DeepSeekProgressPanel
from components.scan_worker_thread import ScanWorkerThread

if TYPE_CHECKING:
    from core.pipeline_worker import PipelineWorker

from .auto_detection import AutoDetectionMixin
from .pipeline_control import PipelineControlMixin
from .roi_config_io import RoiConfigIoMixin
from .roi_editing import RoiEditingMixin
from .scan_control import ScanControlMixin
from .source_config import SourceConfigMixin
from .video_playback import VideoPlaybackMixin

# 原 main_window.py 中 SubtitleOCRGUI 仅继承 QMainWindow，其 dir() 不含普通
# Mixin 类自带的 __weakref__ 描述符。在此删除以保持拆分前后的结构等价；
# 实例的弱引用能力不受影响（仍由 QMainWindow 继承链提供）。
for _mixin_cls in (VideoPlaybackMixin, RoiEditingMixin, RoiConfigIoMixin,
                   PipelineControlMixin, AutoDetectionMixin, ScanControlMixin,
                   SourceConfigMixin):
    del _mixin_cls.__weakref__


class SubtitleOCRGUI(QMainWindow, VideoPlaybackMixin, RoiEditingMixin, RoiConfigIoMixin, PipelineControlMixin, AutoDetectionMixin, ScanControlMixin, SourceConfigMixin):
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
        self._panel_busy: bool = False
        self._detector_thread: Optional[QThread] = None
        self._scan_thread: Optional[ScanWorkerThread] = None
        self._auto_roi_mode_replace: bool = False
        self._scan_is_silent: bool = False
        self._scan_mode: str = "quick"  # "quick" | "deep"
        self._last_scan_report = None
        # 深度扫描复核产出的水印剔除清单，随流水线传给 ASS 生成器。
        self._watermark_filter_config: Optional[Dict] = None
        
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
        self.file_ops_widget.auto_roi_requested.connect(self.auto_detect_rois)
        self.file_ops_widget.deep_scan_requested.connect(self._start_deep_scan)

        self.video_display_widget.timeline_slider.valueChanged.connect(self.seek_video)
        self.video_display_widget.video_label.roi_drawn.connect(self.on_roi_drawn)
        self.video_display_widget.video_label.roi_clicked.connect(self.on_roi_canvas_clicked)
        self.video_display_widget.video_label.roi_geometry_edited.connect(self.on_roi_geometry_edited)

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
        self.roi_def_widget.pose_tags_toggled.connect(self.on_pose_tags_toggled)
        # 轨迹配套选项同样即时写回(与 pose 同契约;勾选后直接开始识别
        # 不再静默丢失)。
        self.roi_def_widget.motion_brightness_toggled.connect(
            self.on_motion_brightness_toggled)
        self.roi_def_widget.motion_occlusion_toggled.connect(
            self.on_motion_occlusion_toggled)
        self.roi_def_widget.scene_policy_changed.connect(
            self.on_scene_policy_changed)

        self.roi_list_widget.selection_changed.connect(self.on_roi_selection_changed)
        self.roi_list_widget.copy_requested.connect(self.copy_roi)
        self.roi_list_widget.paste_after_requested.connect(self.paste_roi_after)
        self.roi_list_widget.paste_at_end_requested.connect(self.paste_roi_at_end)
        self.roi_list_widget.delete_requested.connect(self.delete_roi_by_index)
        self.roi_list_widget.toggle_policy_requested.connect(self.toggle_roi_filter_policy)

        self.log_handler.new_record.connect(self.log_viewer_widget.append_log)

    def _begin_scan_thread(self, scan_thread: "ScanWorkerThread") -> None:
        # 扫描开始：锁定控制面板并在主按钮上显示当前阶段。
        super()._begin_scan_thread(scan_thread)
        if getattr(self, "_scan_mode", "quick") == "deep":
            stage = QCoreApplication.translate("SubtitleOCRGUI", "正在深度扫描…")
        else:
            stage = QCoreApplication.translate("SubtitleOCRGUI", "正在检测字幕区域…")
        self._set_panel_busy(True, stage)

    def _on_scan_thread_finished(self) -> None:
        # 扫描结束（完成/失败/取消的最终路径）：统一恢复控制面板。
        super()._on_scan_thread_finished()
        if self._scan_thread is None:
            self._set_panel_busy(False)

    def update_ui_state(self):
        video_loaded = self.cap is not None and self.cap.isOpened()
        roi_drawn = self.video_display_widget.video_label.is_roi_ready()
        item_selected = self.roi_list_widget.roi_list_widget.currentItem() is not None

        # 扫描或 OCR 运行期间统一锁定主按钮；结束（完成/失败/取消）后
        # 由 _set_panel_busy(False) 恢复。
        panel_busy = getattr(self, "_panel_busy", False)
        if self.control_panel_widget.is_pipeline_running() != panel_busy:
            self.control_panel_widget.set_pipeline_running(panel_busy)

        self.video_display_widget.timeline_slider.setEnabled(video_loaded)
        self.file_ops_widget.save_roi_btn.setEnabled(video_loaded and bool(self.roi_data))
        self.file_ops_widget.load_roi_btn.setEnabled(video_loaded)
        self.file_ops_widget.auto_roi_btn.setEnabled(video_loaded and not panel_busy)
        self.file_ops_widget.deep_scan_btn.setEnabled(video_loaded and not panel_busy)

        self.roi_def_widget.add_roi_btn.setEnabled(video_loaded and roi_drawn)
        self.roi_def_widget.update_roi_btn.setEnabled(video_loaded and roi_drawn and item_selected)
        self.roi_def_widget.delete_roi_btn.setEnabled(video_loaded and item_selected)

        for btn in [self.roi_def_widget.start_frame_backward, self.roi_def_widget.start_frame_forward,
                    self.roi_def_widget.end_frame_backward, self.roi_def_widget.end_frame_forward,
                    self.roi_def_widget.set_start_btn, self.roi_def_widget.set_end_btn]:
            btn.setEnabled(video_loaded)

        self.roi_def_widget.set_color_restrict_controls_enabled(video_loaded)

        self.control_panel_widget.run_pipeline_btn.setEnabled(
            video_loaded and bool(self.roi_data) and not panel_busy)
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

    def closeEvent(self, event):
        # Collect every background thread this window owns; destroying a
        # running QThread aborts the process ("QThread: Destroyed while
        # thread is still running").
        busy: List[QThread] = []
        if self.pipeline_worker is not None and self.pipeline_worker.isRunning():
            busy.append(self.pipeline_worker)
        if self._scan_thread is not None and self._scan_thread.isRunning():
            busy.append(self._scan_thread)
        if self._detector_thread is not None and self._detector_thread.isRunning():
            busy.append(self._detector_thread)
        # Retired (result-discarded) threads still abort the process if they
        # are running when their last Python reference dies at exit.
        for t in getattr(self, "_retired_threads", []):
            if t.isRunning() and t not in busy:
                busy.append(t)
        try:
            fetch_busy = self.control_panel_widget.shutdown_background_threads(timeout_ms=3000)
        except Exception:
            fetch_busy = False

        if busy or fetch_busy:
            reply = QMessageBox.question(
                self,
                QCoreApplication.translate("SubtitleOCRGUI", "确认退出"),
                QCoreApplication.translate("SubtitleOCRGUI", "后台任务仍在运行，确定要退出吗？"),
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No,
            )
            if reply != QMessageBox.Yes:
                event.ignore()
                return
            for t in busy:
                cancel = getattr(t, "cancel", None)
                if callable(cancel):
                    cancel()
                t.wait(5000)
            for t in busy:
                if t.isRunning():
                    # Last resort at shutdown: force-kill rather than abort
                    # the process by destroying a live QThread.
                    t.terminate()
                    t.wait(1000)
        event.accept()

    @staticmethod
    def _discard_thread_signals(thread: QThread, signal_names: Tuple[str, ...]) -> None:
        """Detach a stale background thread's result signals so its late
        completion cannot apply to the newly loaded video."""
        for name in signal_names:
            sig = getattr(thread, name, None)
            if sig is None:
                continue
            try:
                sig.disconnect()
            except (RuntimeError, TypeError):
                pass
