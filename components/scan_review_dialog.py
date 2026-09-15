# components/scan_review_dialog.py
"""深度扫描结果复核对话框。

展示 fullframe_scanner.ScanReport 的三类发现，由用户逐项勾选：

- 字幕带 ROI：默认全部勾选导入（自动 ROI，按场景预设过滤）；
- 水印候选：默认勾选剔除（生成 watermark_filter_config 供流水线使用）；
- 场景文字候选：默认不导入（场景字以手动多边形精修为主），勾选后以
  keep_all 策略导入为 ROI。

``get_result()`` 返回供 main_window 应用的决策 dict。
"""

from __future__ import annotations

from typing import Any, Dict, List

from PySide6.QtCore import QCoreApplication, Qt
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)


class _CheckRow(QCheckBox):
    """带快照缩略图与说明文本的勾选行。"""

    def __init__(self, text: str, snapshot_jpeg: bytes = b"", parent=None):
        super().__init__(text, parent)
        self._thumb_label: QLabel | None = None
        if snapshot_jpeg:
            pixmap = QPixmap()
            if pixmap.loadFromData(snapshot_jpeg):
                self._thumb_label = QLabel()
                self._thumb_label.setPixmap(
                    pixmap.scaledToWidth(160, Qt.SmoothTransformation)
                )

    def attach_to_layout(self, layout: QVBoxLayout) -> None:
        row = QHBoxLayout()
        row.addWidget(self, 1)
        if self._thumb_label is not None:
            row.addWidget(self._thumb_label, 0, Qt.AlignTop)
        layout.addLayout(row)


class ScanReviewDialog(QDialog):
    """深度扫描结果复核；accept 后经 ``get_result()`` 取回决策。"""

    def __init__(self, report, parent=None):
        super().__init__(parent)
        self.setWindowTitle(QCoreApplication.translate("ScanReviewDialog", "深度扫描复核"))
        self.resize(720, 640)
        self.setModal(True)

        self._band_checks: List[_CheckRow] = []
        self._watermark_checks: List[_CheckRow] = []
        self._scene_checks: List[_CheckRow] = []
        self._scene_entries: List[Dict[str, Any]] = []
        self._watermark_entries: List[Dict[str, Any]] = []
        self._band_entries: List[Dict[str, Any]] = []
        # Only created when band ROIs exist; declared here so it's never a dynamic attribute.
        self._replace_checkbox: QCheckBox | None = None

        band_rois = list(getattr(report, "band_rois", []) or [])
        watermarks = list(getattr(report, "watermarks", []) or [])
        scene_candidates = list(getattr(report, "scene_candidates", []) or [])
        self._band_entries = band_rois

        content = QWidget()
        content_layout = QVBoxLayout(content)
        content_layout.setSpacing(10)

        # ── 字幕带 ───────────────────────────────────────────
        band_group = QGroupBox(QCoreApplication.translate("ScanReviewDialog", "字幕带 ROI（自动检测的主字幕区）"))
        band_layout = QVBoxLayout(band_group)
        if band_rois:
            for roi in band_rois:
                region = str(roi.get("band_region", ""))
                region_text = (
                    QCoreApplication.translate("ScanReviewDialog", "底部") if region == "bottom"
                    else QCoreApplication.translate("ScanReviewDialog", "顶部") if region == "top" else QCoreApplication.translate("ScanReviewDialog", "中部")
                )
                row = _CheckRow(
                    QCoreApplication.translate("ScanReviewDialog", "{0}  帧 [{1}-{2}]").format(
                        region_text, roi.get("start_frame"), roi.get("end_frame")
                    )
                )
                row.setChecked(True)
                row.attach_to_layout(band_layout)
                self._band_checks.append(row)
            self._replace_checkbox = QCheckBox(QCoreApplication.translate("ScanReviewDialog", "用所选字幕带替换现有 ROI 列表（否则追加）"))
            self._replace_checkbox.setChecked(True)
            band_layout.addWidget(self._replace_checkbox)
        else:
            band_layout.addWidget(QLabel(QCoreApplication.translate("ScanReviewDialog", "未检测到字幕带。")))
        content_layout.addWidget(band_group)

        # ── 水印 ─────────────────────────────────────────────
        watermark_group = QGroupBox(QCoreApplication.translate("ScanReviewDialog", "疑似水印（恒定文本 + 恒定位置，勾选=识别时剔除）"))
        watermark_layout = QVBoxLayout(watermark_group)
        if watermarks:
            for candidate in watermarks:
                presence = float(candidate.get("presence", 0.0))
                row = _CheckRow(
                    QCoreApplication.translate("ScanReviewDialog", "「{0}」 出现率 {1:.0%}").format(
                        str(candidate.get("text", ""))[:40], presence
                    ),
                    bytes(candidate.get("snapshot_jpeg") or b""),
                )
                row.setChecked(True)
                row.attach_to_layout(watermark_layout)
                self._watermark_checks.append(row)
                self._watermark_entries.append(candidate)
        else:
            watermark_layout.addWidget(QLabel(QCoreApplication.translate("ScanReviewDialog", "未检测到疑似水印。")))
        content_layout.addWidget(watermark_group)

        # ── 场景文字 ─────────────────────────────────────────
        scene_group = QGroupBox(QCoreApplication.translate("ScanReviewDialog", "画面中部文字（场景字候选，勾选=导入为 ROI，全部保留）"))
        scene_layout = QVBoxLayout(scene_group)
        if scene_candidates:
            for candidate in scene_candidates[:50]:  # 防止极端视频条目爆炸
                row = _CheckRow(
                    QCoreApplication.translate("ScanReviewDialog", "「{0}」 出现 {1} 次（帧 {2}-{3}）").format(
                        str(candidate.get("text", ""))[:40],
                        candidate.get("hit_count", 0),
                        candidate.get("first_frame"),
                        candidate.get("last_frame"),
                    ),
                    bytes(candidate.get("snapshot_jpeg") or b""),
                )
                row.setChecked(False)
                row.attach_to_layout(scene_layout)
                self._scene_checks.append(row)
                self._scene_entries.append(candidate)
            if len(scene_candidates) > len(self._scene_checks):
                scene_layout.addWidget(
                    QLabel(QCoreApplication.translate("ScanReviewDialog", "（其余 {} 处低频文字未列出）").format(
                        len(scene_candidates) - len(self._scene_checks)
                    ))
                )
        else:
            scene_layout.addWidget(QLabel(QCoreApplication.translate("ScanReviewDialog", "未检测到画面中部文字。")))
        content_layout.addWidget(scene_group)

        content_layout.addStretch(1)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(content)

        buttons = QDialogButtonBox(
            QDialogButtonBox.Ok | QDialogButtonBox.Cancel, parent=self
        )
        buttons.button(QDialogButtonBox.Ok).setText(QCoreApplication.translate("ScanReviewDialog", "应用"))
        buttons.button(QDialogButtonBox.Cancel).setText(QCoreApplication.translate("ScanReviewDialog", "取消"))
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)

        outer = QVBoxLayout(self)
        outer.addWidget(scroll, 1)
        outer.addWidget(buttons, 0)

    def get_result(self) -> Dict[str, Any]:
        """收集勾选结果，供 main_window 应用。"""
        band_rois = [
            roi for roi, check in zip(self._band_entries, self._band_checks) if check.isChecked()
        ]
        replace_existing = self._replace_checkbox is not None and self._replace_checkbox.isChecked()

        watermark_entries = [
            candidate
            for candidate, check in zip(self._watermark_entries, self._watermark_checks)
            if check.isChecked()
        ]
        watermark_config = None
        if watermark_entries:
            watermark_config = {
                "enabled": True,
                "entries": [
                    {"text": c.get("text", ""), "bbox": c.get("bbox")}
                    for c in watermark_entries
                ],
            }

        scene_rois = [
            candidate["roi_entry"]
            for candidate, check in zip(self._scene_entries, self._scene_checks)
            if check.isChecked() and candidate.get("roi_entry")
        ]

        return {
            "band_rois": band_rois,
            "scene_rois": scene_rois,
            "replace_existing": replace_existing,
            "watermark_config": watermark_config,
        }
