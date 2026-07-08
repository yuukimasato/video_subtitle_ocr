# components/color_gate_preview_dialog.py
from __future__ import annotations

from typing import Any, Dict, List, Optional

import cv2
import numpy as np
from PySide6.QtCore import Qt, QCoreApplication
from PySide6.QtGui import QImage, QPixmap
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFormLayout,
    QGridLayout,
    QGroupBox,
    QLabel,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from core.color_presence_gate import ColorGatePreviewRow, build_gate_spec, recount_preview_rows


def _bgr_to_pixmap(bgr: np.ndarray) -> QPixmap:
    if bgr is None or bgr.size == 0:
        return QPixmap()
    rgb = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)
    h, w = rgb.shape[:2]
    qimg = QImage(rgb.data, w, h, w * 3, QImage.Format.Format_RGB888)
    return QPixmap.fromImage(qimg.copy())


class ColorGatePreviewDialog(QDialog):
    """
    Lets the user visually confirm color-based presence heuristic before committing.
    Accepted -> returns gate spec dict via selected_spec(); rejected -> runs default (no gate).
    """

    def __init__(self, parent, preview_pack: Dict[str, Any]):
        super().__init__(parent)
        self.setWindowTitle(QCoreApplication.translate("ColorGatePreviewDialog", "颜色门控：预览"))
        self.setModal(True)
        self.resize(900, 640)

        self._pack = preview_pack
        self._rows: List[ColorGatePreviewRow] = list(preview_pack.get("rows") or [])
        bounds = preview_pack.get("bounds")
        if not isinstance(bounds, dict):
            raise RuntimeError("invalid preview pack")
        self._bounds: Dict[str, np.ndarray] = bounds  # type: ignore[assignment]
        planned = int(preview_pack.get("planned_roi_frames") or 1)
        self._planned_roi_frames = max(1, planned)
        self._cal_frame = int(preview_pack.get("calibration_frame") or 0)

        self._accepted_spec: Optional[Dict[str, Any]] = None

        root = QVBoxLayout(self)
        intro = QLabel(
            QCoreApplication.translate(
                "ColorGatePreviewDialog",
                "以下缩略来自 ROI 区间内均匀采样。"
                "请确认「判定为保留」与您期望一致。"
                "若不满意，请选择「取消」或直接关闭对话框，并保持主界面选项关闭或未确认。",
            )
        )
        intro.setWordWrap(True)
        root.addWidget(intro)

        stats_box = QGroupBox(QCoreApplication.translate("ColorGatePreviewDialog", "阈值与预估"))
        fl = QFormLayout(stats_box)
        self.min_ratio_spin = QDoubleSpinBox()
        self.min_ratio_spin.setRange(0.0005, 0.2)
        self.min_ratio_spin.setDecimals(5)
        self.min_ratio_spin.setSingleStep(0.001)
        self.min_ratio_spin.setValue(float(preview_pack.get("min_ratio") or 0.01))
        self.min_ratio_spin.valueChanged.connect(self._refresh_stats_and_thumbs)
        fl.addRow(QCoreApplication.translate("ColorGatePreviewDialog", "命中面积占比下限："), self.min_ratio_spin)

        self.stats_label = QLabel("")
        self.stats_label.setWordWrap(True)
        fl.addRow(self.stats_label)

        root.addWidget(stats_box)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        panel = QWidget()
        self._grid = QGridLayout(panel)
        self._grid.setHorizontalSpacing(10)
        self._grid.setVerticalSpacing(10)
        scroll.setWidget(panel)
        root.addWidget(scroll, 1)

        btns = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Cancel | QDialogButtonBox.StandardButton.Ok
        )
        btns.button(QDialogButtonBox.StandardButton.Ok).setText(
            QCoreApplication.translate("ColorGatePreviewDialog", "采用（用于本次识别）")
        )
        btns.button(QDialogButtonBox.StandardButton.Cancel).setText(
            QCoreApplication.translate("ColorGatePreviewDialog", "不采用")
        )
        btns.accepted.connect(self._on_adopt)
        btns.rejected.connect(self.reject)
        root.addWidget(btns)

        self._thumb_labels: List[QLabel] = []
        self._caption_labels: List[QLabel] = []
        self._rebuild_thumbnails()
        self._refresh_stats_and_thumbs()

    def selected_spec(self) -> Optional[Dict[str, Any]]:
        return self._accepted_spec

    def _current_min_ratio(self) -> float:
        return float(self.min_ratio_spin.value())

    def _refresh_stats_and_thumbs(self) -> None:
        mr = self._current_min_ratio()
        kept = recount_preview_rows(self._rows, mr)
        sampled = max(1, len(self._rows))
        kr = kept / float(sampled)
        est = max(1, int(round(self._planned_roi_frames * kr)))

        self.stats_label.setText(
            QCoreApplication.translate(
                "ColorGatePreviewDialog",
                "采样帧数：{}；在当前阈值下「保留」帧数：{}（约 {:.1f}%）；"
                "按此比例粗略估计全流程 ROI 图数量约：{} / {}（原计划）。",
            ).format(sampled, kept, kr * 100.0, est, self._planned_roi_frames)
        )

        lim = min(len(self._caption_labels), len(self._rows))
        for i in range(lim):
            row = self._rows[i]
            ok = row.max_ratio >= mr
            status = (
                QCoreApplication.translate("ColorGatePreviewDialog", "保留")
                if ok
                else QCoreApplication.translate("ColorGatePreviewDialog", "跳过")
            )
            tint = "#1b5e20" if ok else "#b71c1c"
            line = QCoreApplication.translate("ColorGatePreviewDialog", "帧 {} · {}").format(
                int(row.frame_index), status
            )
            sub = QCoreApplication.translate("ColorGatePreviewDialog", "max 占比：{ratio:.4f}").format(
                ratio=float(row.max_ratio)
            )
            self._caption_labels[i].setText(f"{line}<br/><span style='color:{tint};'>{sub}</span>")

    def _rebuild_thumbnails(self) -> None:
        # Clear existing widgets from grid
        while self._grid.count():
            item = self._grid.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()
        self._thumb_labels.clear()
        self._caption_labels.clear()

        cols = 3
        for i, row in enumerate(self._rows[:24]):
            cell = QWidget()
            v = QVBoxLayout(cell)
            v.setContentsMargins(0, 0, 0, 0)
            im = QLabel()
            im.setAlignment(Qt.AlignmentFlag.AlignCenter)
            im.setPixmap(_bgr_to_pixmap(row.thumb_bgr).scaled(320, 200, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation))
            cap = QLabel("")
            cap.setWordWrap(True)
            v.addWidget(im)
            v.addWidget(cap)
            r, c = divmod(i, cols)
            self._grid.addWidget(cell, r, c)
            self._thumb_labels.append(im)
            self._caption_labels.append(cap)

    def _on_adopt(self) -> None:
        mr = self._current_min_ratio()
        kept = recount_preview_rows(self._rows, mr)
        sampled = max(1, len(self._rows))
        kr = kept / float(sampled)
        est = max(1, int(round(self._planned_roi_frames * kr)))
        spec = build_gate_spec(self._bounds, mr, est, kr, self._cal_frame)
        self._accepted_spec = spec
        self.accept()
