import hashlib
import json

from typing import Optional

from PySide6.QtCore import QCoreApplication
from PySide6.QtWidgets import QDialog, QMessageBox


class SourceConfigMixin:
    """颜色门控预览与文字来源过滤配置持久化（原 main_window.py SubtitleOCRGUI L780-1440）。"""

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
            dlg = ColorGatePreviewDialog(self, pack)
        except Exception as e:
            self.logger.warning(
                QCoreApplication.translate("SubtitleOCRGUI", "颜色门控预览失败：{}").format(e),
                exc_info=True,
            )
            QMessageBox.warning(
                self,
                QCoreApplication.translate("SubtitleOCRGUI", "预览失败"),
                str(e),
            )
            return
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
                "enabled": self.control_panel_widget.is_source_filter_enabled(),
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
