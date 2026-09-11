from PySide6.QtCore import QCoreApplication


class AutoDetectionMixin:
    """视频类型后台自动检测（原 main_window.py SubtitleOCRGUI L1039-1102）。"""

    # ── Auto-detection ───────────────────────────────────────

    def _on_detector_thread_finished(self) -> None:
        if self.sender() is self._detector_thread:
            self._detector_thread = None

    def _retire_detector_thread(self, thread) -> None:
        """Keep a still-running detector thread referenced until it finishes.

        Dropping the last Python reference to a parentless running QThread
        destroys the C++ object and aborts the process
        ("QThread: Destroyed while thread is still running").
        """
        retired = getattr(self, "_retired_threads", None)
        if retired is None:
            retired = []
            self._retired_threads = retired

        def _release():
            thread.deleteLater()
            if thread in retired:
                retired.remove(thread)

        thread.finished.connect(_release)
        retired.append(thread)

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

        if self._detector_thread is not None:
            self._discard_thread_signals(
                self._detector_thread, ("detection_done", "detection_error")
            )
            if self._detector_thread.isRunning():
                self._retire_detector_thread(self._detector_thread)
            else:
                self._detector_thread.deleteLater()
            self._detector_thread = None

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
        self._detector_thread.finished.connect(self._on_detector_thread_finished)
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
