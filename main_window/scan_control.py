from typing import Dict

from PySide6.QtCore import QCoreApplication, Slot
from PySide6.QtWidgets import QDialog, QMessageBox

from components.scan_worker_thread import ScanWorkerThread


class ScanControlMixin:
    """字幕快速/深度扫描、扫描线程管理与结果应用（原 main_window.py SubtitleOCRGUI L1106-1400）。"""

    # ── Auto subtitle ROI detection (fullframe scan) ──────────

    def _read_auto_roi_settings(self):
        """(enabled, samples) from QSettings; samples clamped to 4–60."""
        from PySide6.QtCore import QSettings
        settings = QSettings()
        enabled = settings.value("pipeline/auto_roi_on_load", True, type=bool)
        samples = settings.value("pipeline/auto_roi_samples", 12, type=int)
        return bool(enabled), max(4, min(60, int(samples)))

    def _on_scan_thread_finished(self) -> None:
        if self.sender() is self._scan_thread:
            self._scan_thread = None
            self.update_ui_state()
        # Scan threads are parented to the window; without deleteLater they
        # accumulate until the window is destroyed.
        sender = self.sender()
        if sender is not None:
            sender.deleteLater()
        # 换视频时被取消的扫描已收尾：为新视频补跑自动 ROI 扫描
        # （load_video 里第一次调用会因 isRunning() 静默跳过）；
        # 自动加载的 ROI 配置已就位时无需再扫。
        if getattr(self, "_pending_auto_roi_scan", False):
            self._pending_auto_roi_scan = False
            if not self.roi_data:
                self._start_auto_roi_suggest(on_load=True)

    def _cancel_active_scan(self) -> None:
        """Cancel a running scan and detach its result signals so a stale
        report cannot be applied to the newly loaded video.

        cancel() 只置位标记，线程要到下一个进度回调点（当次全帧 OCR 结束后）
        才真正退出；期间 isRunning() 仍为 True。若调用方随后要为换装后的
        新视频补一次自动扫描，可置 ``_pending_auto_roi_scan`` 并由
        :meth:`_on_scan_thread_finished` 在线程收尾后触发——与旧线程保持
        串行，避免两个扫描并发争用全局 OCR 引擎单例。
        """
        t = getattr(self, "_scan_thread", None)
        if t is None:
            return
        was_running = t.isRunning()
        t.cancel()
        self._discard_thread_signals(
            t, ("scan_progress", "scan_finished", "scan_error")
        )
        self._pending_auto_roi_scan = was_running

    def _begin_scan_thread(self, scan_thread: "ScanWorkerThread") -> None:
        """Install a new scan thread, discarding any stale one first."""
        if self._scan_thread is not None:
            self._discard_thread_signals(
                self._scan_thread, ("scan_progress", "scan_finished", "scan_error")
            )
            self._scan_thread = None
        self._scan_thread = scan_thread
        scan_thread.scan_progress.connect(self._on_scan_progress)
        scan_thread.scan_finished.connect(self._on_scan_ready)
        scan_thread.scan_error.connect(self._on_scan_error)
        scan_thread.finished.connect(self._on_scan_thread_finished)

    def _start_auto_roi_suggest(self, on_load: bool = False, replace_existing: bool = False):
        """后台快速扫描；on_load 时静默（无弹窗），结果直接填充 ROI 列表。"""
        if not self.video_path or not self.cap:
            if not on_load:
                QMessageBox.warning(
                    self,
                    QCoreApplication.translate("SubtitleOCRGUI", "未加载视频"),
                    QCoreApplication.translate("SubtitleOCRGUI", "请先加载视频文件。"),
                )
            return
        if self._scan_thread is not None and self._scan_thread.isRunning():
            return
        enabled, samples = self._read_auto_roi_settings()
        if on_load and not enabled:
            return

        options = self.control_panel_widget.get_pipeline_options()
        self._auto_roi_mode_replace = bool(replace_existing)
        self._scan_is_silent = bool(on_load)
        self._scan_mode = "quick"
        self._begin_scan_thread(
            ScanWorkerThread(
                self.video_path,
                sample_count=samples,
                lang=options.get("ocr_lang", "ch"),
                model_tier=options.get("ocr_model_tier", "auto"),
                with_snapshots=False,
                parent=self,
            )
        )
        self.file_ops_widget.auto_roi_btn.setEnabled(False)
        self._scan_thread.start()
        self.logger.info(
            QCoreApplication.translate("SubtitleOCRGUI", "正在后台扫描字幕分布（采样 {} 帧）…").format(samples)
        )

    @Slot()
    def auto_detect_rois(self):
        """按钮触发：询问追加/替换后做一次快速全片扫描。"""
        if not self.video_path or not self.cap:
            QMessageBox.warning(
                self,
                QCoreApplication.translate("SubtitleOCRGUI", "未加载视频"),
                QCoreApplication.translate("SubtitleOCRGUI", "请先加载视频文件。"),
            )
            return
        if self._scan_thread is not None:
            return

        # Yes = append, No = replace all, Cancel = abort. Default button is
        # Cancel so an accidental Enter cannot destroy existing ROI data.
        reply = QMessageBox.question(
            self,
            QCoreApplication.translate("SubtitleOCRGUI", "自动检测字幕ROI"),
            QCoreApplication.translate(
                "SubtitleOCRGUI",
                "请选择自动检测到的字幕区域的处理方式：\n\n"
                "「是」：追加到现有 ROI 列表末尾（保留现有 ROI）；\n"
                "「否」：替换全部现有 ROI（现有 ROI 将被清除，不可恢复）；\n"
                "「取消」：中止本次检测。",
            ),
            QMessageBox.Yes | QMessageBox.No | QMessageBox.Cancel,
            QMessageBox.Cancel,
        )
        if reply == QMessageBox.Cancel:
            return
        replace_existing = (reply == QMessageBox.No)
        if replace_existing and self.roi_data:
            confirm = QMessageBox.question(
                self,
                QCoreApplication.translate("SubtitleOCRGUI", "替换现有 ROI？"),
                QCoreApplication.translate(
                    "SubtitleOCRGUI",
                    "即将清除现有 {} 个 ROI 并用检测结果替换，此操作不可恢复。是否继续？",
                ).format(len(self.roi_data)),
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No,
            )
            if confirm != QMessageBox.Yes:
                return
        self._start_auto_roi_suggest(on_load=False, replace_existing=replace_existing)

    @Slot()
    def _start_deep_scan(self):
        """深度扫描：更多采样 + 快照，扫描完成后弹出复核对话框。"""
        if not self.video_path or not self.cap:
            QMessageBox.warning(
                self,
                QCoreApplication.translate("SubtitleOCRGUI", "未加载视频"),
                QCoreApplication.translate("SubtitleOCRGUI", "请先加载视频文件。"),
            )
            return
        if self._scan_thread is not None:
            return

        from PySide6.QtCore import QSettings
        settings = QSettings()
        samples = settings.value("pipeline/deep_scan_samples", 24, type=int)
        samples = max(4, min(60, int(samples)))

        options = self.control_panel_widget.get_pipeline_options()
        self._auto_roi_mode_replace = False
        self._scan_is_silent = False
        self._scan_mode = "deep"
        self._begin_scan_thread(
            ScanWorkerThread(
                self.video_path,
                sample_count=samples,
                lang=options.get("ocr_lang", "ch"),
                model_tier=options.get("ocr_model_tier", "auto"),
                with_snapshots=True,
                parent=self,
            )
        )
        self.file_ops_widget.auto_roi_btn.setEnabled(False)
        self.file_ops_widget.deep_scan_btn.setEnabled(False)
        self._scan_thread.start()
        self.logger.info(
            QCoreApplication.translate("SubtitleOCRGUI", "正在深度扫描（采样 {} 帧，含水印/场景字统计）…").format(samples)
        )

    def _show_scan_review_dialog(self, report) -> None:
        from components.scan_review_dialog import ScanReviewDialog

        band_count = len(getattr(report, "band_rois", []) or [])
        watermark_count = len(getattr(report, "watermarks", []) or [])
        scene_count = len(getattr(report, "scene_candidates", []) or [])
        if not (band_count or watermark_count or scene_count):
            QMessageBox.information(
                self,
                QCoreApplication.translate("SubtitleOCRGUI", "深度扫描"),
                QCoreApplication.translate("SubtitleOCRGUI", "扫描完成：未检测到字幕带、水印或场景文字。"),
            )
            return

        dialog = ScanReviewDialog(report, parent=self)
        if dialog.exec() != QDialog.Accepted:
            return
        self._apply_scan_result(dialog.get_result())

    def _apply_scan_result(self, result: Dict) -> None:
        band_rois = list(result.get("band_rois") or [])
        scene_rois = list(result.get("scene_rois") or [])
        if result.get("replace_existing") and band_rois:
            self.roi_data = list(band_rois)
        else:
            self.roi_data.extend(band_rois)
        # 场景字候选一律以 keep_all 策略追加（识别时永不过滤）。
        self.roi_data.extend(scene_rois)

        self._watermark_filter_config = result.get("watermark_config")
        if self._watermark_filter_config:
            entries = self._watermark_filter_config.get("entries") or []
            self.logger.info(
                QCoreApplication.translate(
                    "SubtitleOCRGUI", "将在识别时剔除 {} 处水印文本。"
                ).format(len(entries))
            )
        else:
            self.logger.info(
                QCoreApplication.translate("SubtitleOCRGUI", "未启用水印剔除。")
            )
        self.logger.info(
            QCoreApplication.translate(
                "SubtitleOCRGUI", "已导入 {} 个字幕带 ROI、{} 个场景字 ROI。"
            ).format(len(band_rois), len(scene_rois))
        )

        self.update_roi_list()
        self.update_all_rois_visibility()
        self.control_panel_widget.invalidate_color_gate_confirmation()
        self.update_ui_state()

    @Slot(int, int)
    def _on_scan_progress(self, done: int, total: int):
        self.logger.info(
            QCoreApplication.translate(
                "SubtitleOCRGUI", "字幕扫描进度：{}/{}（采样帧）"
            ).format(done, total)
        )

    @Slot(object)
    def _on_scan_ready(self, report):
        self._last_scan_report = report
        silent = getattr(self, "_scan_is_silent", False)
        self.update_ui_state()

        if getattr(self, "_scan_mode", "quick") == "deep":
            self._show_scan_review_dialog(report)
            return

        band_rois = list(getattr(report, "band_rois", []) or [])
        watermarks = list(getattr(report, "watermarks", []) or [])
        scene_candidates = list(getattr(report, "scene_candidates", []) or [])

        if not band_rois:
            if not silent:
                QMessageBox.information(
                    self,
                    QCoreApplication.translate("SubtitleOCRGUI", "自动检测字幕ROI"),
                    QCoreApplication.translate(
                        "SubtitleOCRGUI",
                        "未检测到字幕区域。请确认视频中确实存在字幕，或调整识别语言后重试。",
                    ),
                )
            else:
                self.logger.info(
                    QCoreApplication.translate("SubtitleOCRGUI", "后台扫描未检测到字幕带，可手动绘制 ROI。")
                )
            return

        if self._auto_roi_mode_replace:
            self.roi_data = band_rois
            self.logger.info(
                QCoreApplication.translate(
                    "SubtitleOCRGUI", "已用 {} 个自动检测的 ROI 替换全部现有 ROI。"
                ).format(len(band_rois))
            )
        else:
            self.roi_data.extend(band_rois)
            self.logger.info(
                QCoreApplication.translate(
                    "SubtitleOCRGUI", "已追加 {} 个自动检测的 ROI 到列表末尾。"
                ).format(len(band_rois))
            )
        self.update_roi_list()
        self.update_all_rois_visibility()
        self.control_panel_widget.invalidate_color_gate_confirmation()
        self.update_ui_state()

        if watermarks:
            self.logger.info(
                QCoreApplication.translate(
                    "SubtitleOCRGUI",
                    "检测到 {} 处疑似水印（恒定文本/位置）。可运行深度扫描复核后自动剔除。",
                ).format(len(watermarks))
            )
        if scene_candidates:
            self.logger.info(
                QCoreApplication.translate(
                    "SubtitleOCRGUI",
                    "检测到 {} 处画面中部文字（场景字候选）。可在深度扫描复核中导入为 ROI。",
                ).format(len(scene_candidates))
            )
        if not silent:
            # format_time strings are zero-padded, so lexicographic order
            # matches chronological order.
            start_time = min(str(r.get("start_time", "")) for r in band_rois)
            end_time = max(str(r.get("end_time", "")) for r in band_rois)
            QMessageBox.information(
                self,
                QCoreApplication.translate("SubtitleOCRGUI", "自动检测字幕ROI"),
                QCoreApplication.translate(
                    "SubtitleOCRGUI",
                    "自动检测完成：检测到 {} 个字幕区域，时间范围 {} ～ {}",
                ).format(len(band_rois), start_time, end_time),
            )

    def _on_scan_error(self, error_msg: str):
        self.update_ui_state()
        self.logger.error(
            QCoreApplication.translate("SubtitleOCRGUI", "自动检测失败：{}").format(error_msg)
        )
        if not getattr(self, "_scan_is_silent", False):
            QMessageBox.critical(
                self,
                QCoreApplication.translate("SubtitleOCRGUI", "错误"),
                QCoreApplication.translate("SubtitleOCRGUI", "自动检测失败：{}").format(error_msg),
            )
