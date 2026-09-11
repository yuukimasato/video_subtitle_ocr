import json
import os
import tempfile

from typing import Optional

from PySide6.QtCore import QCoreApplication
from PySide6.QtWidgets import QFileDialog, QMessageBox


class RoiConfigIoMixin:
    """ROI 配置保存/自动备份/加载与 ASS 模板选择（原 main_window.py SubtitleOCRGUI L638-778）。"""

    def save_roi_config(self):
        if not self.video_path: return
        default_path = os.path.splitext(self.video_path)[0] + '_roi.json'
        file_path, _ = QFileDialog.getSaveFileName(self, 
                                                 QCoreApplication.translate("SubtitleOCRGUI", "保存 ROI 配置"), 
                                                 default_path, 
                                                 QCoreApplication.translate("SubtitleOCRGUI", "JSON 文件 (*.json)"))
        if file_path:
            try:
                config = {
                    "ocr_lang": self.control_panel_widget.get_selected_lang(),
                    "rois": self.roi_data,
                }
                with open(file_path, 'w', encoding='utf-8') as f:
                    json.dump(config, f, indent=4, ensure_ascii=False)
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
                    loaded = json.load(f)
                # New format: {"ocr_lang": ..., "rois": [...]}.
                # Backward compatible: old configs are a plain ROI list.
                if isinstance(loaded, dict):
                    self.control_panel_widget.set_selected_lang(
                        str(loaded.get("ocr_lang") or "")
                    )
                    rois = loaded.get("rois", [])
                    if not isinstance(rois, list):
                        raise ValueError("invalid 'rois' field in ROI config")
                    self.roi_data = rois
                elif isinstance(loaded, list):
                    self.roi_data = loaded
                else:
                    raise ValueError("unrecognized ROI config format")
                self.update_roi_list()
                self.update_all_rois_visibility()
                self.control_panel_widget.invalidate_color_gate_confirmation()
                self.logger.info(QCoreApplication.translate("SubtitleOCRGUI", "ROI 配置已从 {} 加载").format(file_path))
            except Exception as e:
                QMessageBox.critical(self, 
                                     QCoreApplication.translate("SubtitleOCRGUI", "错误"), 
                                     QCoreApplication.translate("SubtitleOCRGUI", "加载 ROI 配置失败：{}").format(e))
                self.logger.error(QCoreApplication.translate("SubtitleOCRGUI", "加载 ROI 配置失败：{}").format(e))

    def select_template_file(self):
        file_path, _ = QFileDialog.getOpenFileName(self, 
                                                 QCoreApplication.translate("SubtitleOCRGUI", "选择 ASS 模板文件"), 
                                                 "", 
                                                 QCoreApplication.translate("SubtitleOCRGUI", "ASS 字幕文件 (*.ass)"))
        if file_path:
            self.control_panel_widget.template_path_edit.setText(file_path)
