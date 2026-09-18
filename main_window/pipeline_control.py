import os

from PySide6.QtCore import Qt, QUrl, QCoreApplication
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import QFileDialog, QMessageBox, QProgressDialog


class PipelineControlMixin:
    """OCR 流水线启动与进度/完成/错误回调（原 main_window.py SubtitleOCRGUI L841-1022）。"""

    def _set_panel_busy(self, busy: bool, stage: str = "") -> None:
        """统一锁定/恢复控制面板（扫描与 OCR 运行共用）。

        getattr 防御：轻量宿主（如 dismiss-race 回归测试的 _Host 桩）没有
        控制面板或 update_ui_state 时安全跳过。
        """
        self._panel_busy = bool(busy)
        panel = getattr(self, "control_panel_widget", None)
        if panel is not None:
            panel.set_pipeline_running(self._panel_busy, stage)
        update_ui_state = getattr(self, "update_ui_state", None)
        if callable(update_ui_state):
            update_ui_state()

    def run_ocr_pipeline(self):
        # Lazy import to keep UI startup lightweight and allow opening the GUI
        # without OCR dependencies installed yet.
        from core.pipeline_worker import PipelineWorker
        if not self.video_path or not self.roi_data:
            QMessageBox.warning(self,
                                QCoreApplication.translate("SubtitleOCRGUI", "警告"),
                                QCoreApplication.translate("SubtitleOCRGUI", "请先加载视频并至少定义一个 ROI。"))
            return

        if self.pipeline_worker is not None and self.pipeline_worker.isRunning():
            QMessageBox.warning(self,
                                QCoreApplication.translate("SubtitleOCRGUI", "警告"),
                                QCoreApplication.translate("SubtitleOCRGUI", "已有 OCR 任务在运行中，请等待其完成或先取消。"))
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

        # 逐 ROI 开关以面板所见为准写回选中 ROI（兜底任何信号缝隙），随后
        # 落盘自动备份——保证"备份文件、识别管线、面板显示"三者一致。
        self._sync_selected_roi_panel_flags()
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
            chunk_workers=int(options.get("chunk_workers", 0) or 0),
            motion_auto_detect=bool(options.get("motion_auto_detect", False)),
            subtitle_polisher=subtitle_polisher,
            color_presence_gate_spec=gate_spec,
            ocr_engine_id=options.get("ocr_engine_id", ""),
            source_filter_config=options.get("source_filter_config"),
            watermark_filter_config=getattr(self, "_watermark_filter_config", None),
            engine_options={
                "lang": options.get("ocr_lang", "ch"),
                "model_tier": options.get("ocr_model_tier", "auto"),
            },
        )
        
        self.pipeline_worker.progress_updated.connect(self._on_pipeline_progress)
        self.pipeline_worker.llm_detail.connect(self.deepseek_progress_panel.append_line)
        self.pipeline_worker.pipeline_finished.connect(self._on_pipeline_finished)
        self.pipeline_worker.error.connect(self._on_pipeline_error)
        self.progress_dialog.canceled.connect(self.pipeline_worker.cancel)
        # Free the worker only after run() has fully returned: clearing the
        # reference from pipeline_finished/error handlers can destroy a
        # QThread that is still executing its cleanup (qFatal abort).
        self.pipeline_worker.finished.connect(self.pipeline_worker.deleteLater)
        self.pipeline_worker.finished.connect(self._on_pipeline_worker_thread_done)

        self.pipeline_worker.start()
        # 运行期间锁定控制面板，主按钮显示当前阶段；完成/失败/取消时恢复。
        self._set_panel_busy(True, QCoreApplication.translate("SubtitleOCRGUI", "正在识别…"))

    def _on_pipeline_worker_thread_done(self):
        if self.sender() is self.pipeline_worker:
            self.pipeline_worker = None
            # 兜底恢复：finished/error 回调解锁时 worker 线程可能尚未退出，
            # update_ui_state 会按 _panel_busy 维持锁定；线程真正结束后统一恢复。
            self._set_panel_busy(False)
            # 取消路径没有 finished/error 回调（worker 静默返回），在此兜底：
            # 销毁进度对话框（autoClose 只隐藏，不销毁，会跨运行累积），
            # 复位 LLM 面板状态，避免取消后残留过期内容。正常完成/失败路径
            # 的处理器先于本回调执行并已清理，此处重复调用是幂等的。
            if getattr(self, "_pipeline_llm_active", False):
                self._pipeline_llm_active = False
                llm_panel = getattr(self, "deepseek_progress_panel", None)
                if llm_panel is not None:
                    llm_panel.append_line(
                        QCoreApplication.translate(
                            "SubtitleOCRGUI",
                            "[LLM] 已取消 —— 未生成字幕文件。",
                        )
                    )
                    llm_panel.set_panel_visible(False)
            self._dismiss_progress_dialog()

    def _on_pipeline_progress(self, value: int, message: str):
        panel = getattr(self, "control_panel_widget", None)
        if panel is not None:
            panel.set_pipeline_stage(message)
        dlg = self.progress_dialog
        if dlg is not None:
            # 模态 QProgressDialog 的 setValue() 会抽取事件队列（Qt6
            # qprogressdialog.cpp: isModal 且 shownOnce 后每次都
            # processEvents()）。排在后面的 finished/error 信号会在这次抽取中
            # 被投递，其处理器最终调用 _dismiss_progress_dialog() 把
            # progress_dialog 置 None。因此 setValue() 之后必须重新判空，
            # 否则下一行就是对 None 调 setLabelText（用户截图的崩溃）。
            dlg.setValue(value)
            dlg = self.progress_dialog
            if dlg is not None:
                dlg.setLabelText(message)
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
        self._set_panel_busy(False)
        if self.progress_dialog:
            self.progress_dialog.setValue(100)
        # 在弹模态框之前就销毁进度对话框：后续进来的进度回调会因判空跳过，
        # 不再可能在 setValue() 的事件抽取里重入本处理器。
        self._dismiss_progress_dialog()
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

    def _on_pipeline_error(self, error_message: str):
        # 同 _on_pipeline_finished：先销毁进度对话框再弹模态错误框。
        self._set_panel_busy(False)
        self._dismiss_progress_dialog()
        if getattr(self, "_pipeline_llm_active", False):
            self.deepseek_progress_panel.append_line(
                QCoreApplication.translate("SubtitleOCRGUI", "[LLM] 已中止或出错 —— 请查看弹窗提示。")
            )
        self._pipeline_llm_active = False
        QMessageBox.critical(self,
                             QCoreApplication.translate("SubtitleOCRGUI", "错误"),
                             QCoreApplication.translate("SubtitleOCRGUI", "处理过程中发生错误：\n{}").format(error_message))

    def _dismiss_progress_dialog(self):
        """Destroy the progress dialog so hidden instances don't accumulate
        across runs (autoClose only hides it)."""
        dlg = getattr(self, "progress_dialog", None)
        self.progress_dialog = None
        if dlg is not None:
            dlg.close()
            dlg.deleteLater()
