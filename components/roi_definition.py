# components/roi_definition.py
from typing import Dict, List, Optional

from PySide6.QtWidgets import (
    QGroupBox,
    QFormLayout,
    QPushButton,
    QLineEdit,
    QHBoxLayout,
    QCheckBox,
    QComboBox,
    QSpinBox,
    QColorDialog,
    QLabel,
    QVBoxLayout,
    QWidget,
)
from PySide6.QtGui import QColor
from PySide6.QtCore import Signal, QCoreApplication


class RoiDefinitionWidget(QGroupBox):
    navigate_pressed = Signal(QLineEdit, int)
    navigate_released = Signal()
    time_edit_finished = Signal(QLineEdit)
    set_time_requested = Signal(QLineEdit)
    add_roi_requested = Signal()
    update_roi_requested = Signal()
    delete_roi_requested = Signal()
    # 「写入画面位置标签」勾选变化(立即写回当前选中的 ROI,见
    # RoiEditingMixin.on_pose_tags_toggled;此前只有 添加/更新 按钮会保存,
    # 勾选后直接开始识别会静默丢失设置)。
    pose_tags_toggled = Signal(bool)
    # 轨迹字幕的配套选项同样即时写回(与 pose 同契约):亮度自适应、
    # 遮挡蒙版(\iclip)、场景文字显示策略——勾选/切换后直接开始识别
    # 不再静默丢失。
    motion_brightness_toggled = Signal(bool)
    motion_occlusion_toggled = Signal(bool)
    scene_policy_changed = Signal(str)

    def __init__(self, parent=None):
        super().__init__(QCoreApplication.translate("RoiDefinitionWidget", "ROI 定义"), parent)

        self._text_bgr: List[int] = [255, 255, 255]
        self._outline_bgr: List[int] = [0, 0, 0]
        self._shadow_bgr: List[int] = [0, 0, 0]

        roi_layout = QFormLayout(self)
        roi_layout.setContentsMargins(10, 10, 10, 10)
        roi_layout.setHorizontalSpacing(10)
        roi_layout.setVerticalSpacing(8)

        start_time_layout = QHBoxLayout()
        start_time_layout.setSpacing(6)
        self.start_frame_backward = QPushButton("←")
        self.start_frame_backward.setFixedWidth(34)
        self.start_frame_backward.setToolTip(
            QCoreApplication.translate("RoiDefinitionWidget", "后退 1 帧（短按）/ 连续（长按）")
        )
        self.start_frame_forward = QPushButton("→")
        self.start_frame_forward.setFixedWidth(34)
        self.start_frame_forward.setToolTip(
            QCoreApplication.translate("RoiDefinitionWidget", "前进 1 帧（短按）/ 连续（长按）")
        )
        self.start_time_edit = QLineEdit("00:00:00.000")
        self.start_time_edit.setToolTip(QCoreApplication.translate("RoiDefinitionWidget", "输入时间（时:分:秒.毫秒）或帧号"))
        start_time_layout.addWidget(self.start_frame_backward)
        start_time_layout.addWidget(self.start_time_edit)
        start_time_layout.addWidget(self.start_frame_forward)
        roi_layout.addRow(QCoreApplication.translate("RoiDefinitionWidget", "开始时间："), start_time_layout)

        self.set_start_btn = QPushButton(QCoreApplication.translate("RoiDefinitionWidget", "设为开始时间"))
        self.set_start_btn.setMinimumHeight(30)
        roi_layout.addRow(self.set_start_btn)

        end_time_layout = QHBoxLayout()
        end_time_layout.setSpacing(6)
        self.end_frame_backward = QPushButton("←")
        self.end_frame_backward.setFixedWidth(34)
        self.end_frame_backward.setToolTip(
            QCoreApplication.translate("RoiDefinitionWidget", "后退 1 帧（短按）/ 连续（长按）")
        )
        self.end_frame_forward = QPushButton("→")
        self.end_frame_forward.setFixedWidth(34)
        self.end_frame_forward.setToolTip(
            QCoreApplication.translate("RoiDefinitionWidget", "前进 1 帧（短按）/ 连续（长按）")
        )
        self.end_time_edit = QLineEdit("00:00:00.000")
        self.end_time_edit.setToolTip(QCoreApplication.translate("RoiDefinitionWidget", "输入时间（时:分:秒.毫秒）或帧号"))
        end_time_layout.addWidget(self.end_frame_backward)
        end_time_layout.addWidget(self.end_time_edit)
        end_time_layout.addWidget(self.end_frame_forward)
        roi_layout.addRow(QCoreApplication.translate("RoiDefinitionWidget", "结束时间："), end_time_layout)

        self.set_end_btn = QPushButton(QCoreApplication.translate("RoiDefinitionWidget", "设为结束时间"))
        self.set_end_btn.setMinimumHeight(30)
        roi_layout.addRow(self.set_end_btn)

        self.color_restrict_checkbox = QCheckBox(
            QCoreApplication.translate("RoiDefinitionWidget", "按文字/描边/阴影颜色限制 OCR（不匹配像素将被遮罩）")
        )
        self.color_restrict_checkbox.setToolTip(
            QCoreApplication.translate(
                "RoiDefinitionWidget",
                "将与所选颜色不接近的像素在 OCR 前置为白色，减少字幕笔画之外的误检。",
            )
        )
        roi_layout.addRow(self.color_restrict_checkbox)

        self.blur_checkbox = QCheckBox(QCoreApplication.translate("RoiDefinitionWidget", "OCR 前对 ROI 轻微模糊（降低锯齿/噪声）"))
        self.blur_checkbox.setToolTip(
            QCoreApplication.translate(
                "RoiDefinitionWidget",
                "在 OCR 前对 ROI 裁剪图应用轻微高斯模糊。对噪声大/压缩重的字幕更有帮助。",
            )
        )
        roi_layout.addRow(self.blur_checkbox)

        self.fade_in_refine_checkbox = QCheckBox(
            QCoreApplication.translate(
                "RoiDefinitionWidget",
                "淡入/淡出微调（在检测到文字边界附近逐帧 OCR，找更精确的起止时间）",
            )
        )
        self.fade_in_refine_checkbox.setChecked(True)
        self.fade_in_refine_checkbox.setToolTip(
            QCoreApplication.translate(
                "RoiDefinitionWidget",
                "开启后，会只在文字出现/消失的边界附近逐帧 OCR 用于校准时间；ROI 其余部分仍可使用跳帧优化。",
            )
        )
        roi_layout.addRow(self.fade_in_refine_checkbox)

        self.pose_tags_checkbox = QCheckBox(
            QCoreApplication.translate(
                "RoiDefinitionWidget",
                "写入画面位置标签（\\pos \\frz \\frx \\fry，多边形自动计算位置与倾角）",
            )
        )
        self.pose_tags_checkbox.setChecked(False)
        self.pose_tags_checkbox.setToolTip(
            QCoreApplication.translate(
                "RoiDefinitionWidget",
                "开启后，该 ROI 识别出的字幕事件将附带位置与旋转标签：多边形 ROI 自动计算中心点(\\pos)与"
                "长边倾角(\\frz)；\\frx/\\fry 保存为 0，可手动微调透视。适合实拍场景文字的原位重铺。"
                "四点多边形 ROI 会走移动文字轨迹管线：逐帧跟踪平面并合成 \\move 运动字幕，静态标签跟不动的画面由轨迹跟随。",
            )
        )
        roi_layout.addRow(self.pose_tags_checkbox)

        # 轨迹字幕亮度自适应:仅在 pose 勾选且 ROI 为四点多边形(轨迹模式)
        # 时生效;随 ROI json(motion_auto_brightness 键)持久化。
        self.motion_brightness_checkbox = QCheckBox(
            QCoreApplication.translate(
                "RoiDefinitionWidget",
                "亮度自适应（轨迹字幕跟随屏幕明暗）",
            )
        )
        self.motion_brightness_checkbox.setChecked(False)
        self.motion_brightness_checkbox.setEnabled(False)
        self.motion_brightness_checkbox.setToolTip(
            QCoreApplication.translate(
                "RoiDefinitionWidget",
                "需勾选「写入画面位置标签」且 ROI 为四点多边形（此时走移动文字轨迹管线）。"
                "开启后逐帧测量文字平面亮度，为轨迹事件追加 \\1c/\\alpha \\t 标签链，"
                "字幕颜色与透明度忠实跟随屏幕变暗/变亮（如手机息屏）。默认关闭。",
            )
        )
        self.pose_tags_checkbox.toggled.connect(
            self.motion_brightness_checkbox.setEnabled)
        roi_layout.addRow(self.motion_brightness_checkbox)

        # 轨迹字幕 \iclip 遮挡蒙版(FR-9):仅 pose 勾选(轨迹模式)时生效;
        # 随 ROI json(motion_occlusion_clip 键)持久化。
        self.motion_occlusion_checkbox = QCheckBox(
            QCoreApplication.translate(
                "RoiDefinitionWidget",
                "遮挡蒙版（\\iclip，手部遮挡时不渲染字幕）",
            )
        )
        self.motion_occlusion_checkbox.setChecked(False)
        self.motion_occlusion_checkbox.setEnabled(False)
        self.motion_occlusion_checkbox.setToolTip(
            QCoreApplication.translate(
                "RoiDefinitionWidget",
                "需勾选「写入画面位置标签」（轨迹模式）。开启后检测手部等遮挡物"
                "覆盖文字平面的帧，为受影响的事件追加 \\iclip 逆向蒙版，"
                "字幕不再渲染到遮挡物上（遮挡结束时自动恢复显示）。默认关闭。",
            )
        )
        self.pose_tags_checkbox.toggled.connect(
            self.motion_occlusion_checkbox.setEnabled)
        roi_layout.addRow(self.motion_occlusion_checkbox)

        # 场景文字显示策略(每 ROI 独立):仅作用于场景文字(画面文字)事件;
        # 所选模式不可用时自动回退(空白区 → 遮罩 → 外置;仅遮罩 → 外置)。
        self.scene_text_policy_combo = QComboBox()
        for text, data in (
            (QCoreApplication.translate("RoiDefinitionWidget", "叠加（默认）"), "overlap"),
            (QCoreApplication.translate("RoiDefinitionWidget", "遮罩原文字"), "mask"),
            (QCoreApplication.translate("RoiDefinitionWidget", "仅遮罩（供排版覆写）"), "mask_only"),
            (QCoreApplication.translate("RoiDefinitionWidget", "外置展示框"), "external"),
            (QCoreApplication.translate("RoiDefinitionWidget", "空白区放置"), "whitespace"),
        ):
            self.scene_text_policy_combo.addItem(text, data)
        self.scene_text_policy_combo.setToolTip(
            QCoreApplication.translate(
                "RoiDefinitionWidget",
                "仅作用于场景文字（画面文字）事件；所选模式不可用时自动回退（空白区→遮罩→外置；仅遮罩→外置）。"
                "「仅遮罩」把遮罩下方的识别文本写成 Comment 注释行（播放器不渲染），"
                "便于在遮罩上自行排版覆写（如绘制译文或 \\p 矢量字）。",
            )
        )
        roi_layout.addRow(
            QCoreApplication.translate("RoiDefinitionWidget", "场景文字显示："),
            self.scene_text_policy_combo,
        )

        # Color-restrict details live in their own container so the form row
        # collapses (hidden) until the checkbox is ticked — keeps the panel short.
        self._color_details_widget = QWidget()
        color_details_layout = QVBoxLayout(self._color_details_widget)
        color_details_layout.setContentsMargins(0, 0, 0, 0)
        color_details_layout.setSpacing(8)

        color_row = QHBoxLayout()
        color_row.setSpacing(8)
        self.text_color_btn = QPushButton(QCoreApplication.translate("RoiDefinitionWidget", "文字颜色…"))
        self.outline_color_btn = QPushButton(QCoreApplication.translate("RoiDefinitionWidget", "描边颜色…"))
        self.shadow_color_btn = QPushButton(QCoreApplication.translate("RoiDefinitionWidget", "阴影颜色…"))
        self.text_color_btn.setMinimumHeight(30)
        self.outline_color_btn.setMinimumHeight(30)
        self.shadow_color_btn.setMinimumHeight(30)
        color_row.addWidget(self.text_color_btn)
        color_row.addWidget(self.outline_color_btn)
        color_row.addWidget(self.shadow_color_btn)
        color_details_layout.addLayout(color_row)

        tol_row = QHBoxLayout()
        tol_row.setSpacing(8)
        tol_row.addWidget(QLabel(QCoreApplication.translate("RoiDefinitionWidget", "RGB 容差：")))
        self.color_tolerance_spin = QSpinBox()
        self.color_tolerance_spin.setRange(1, 120)
        self.color_tolerance_spin.setValue(40)
        self.color_tolerance_spin.setToolTip(
            QCoreApplication.translate("RoiDefinitionWidget", "单通道距离 0–255；数值越大，包含越多相近色阶（抗锯齿/渐变更稳）。")
        )
        tol_row.addWidget(self.color_tolerance_spin)
        tol_row.addWidget(QLabel(QCoreApplication.translate("RoiDefinitionWidget", "形态学：")))
        self.color_morph_spin = QSpinBox()
        self.color_morph_spin.setRange(1, 15)
        self.color_morph_spin.setValue(3)
        self.color_morph_spin.setToolTip(
            QCoreApplication.translate("RoiDefinitionWidget", "闭运算核大小（奇数）；可在遮罩后连接断裂笔画。")
        )
        tol_row.addWidget(self.color_morph_spin)
        tol_row.addStretch()
        color_details_layout.addLayout(tol_row)

        roi_layout.addRow(self._color_details_widget)

        self.add_roi_btn = QPushButton(QCoreApplication.translate("RoiDefinitionWidget", "添加新 ROI"))
        self.update_roi_btn = QPushButton(QCoreApplication.translate("RoiDefinitionWidget", "更新选中 ROI"))
        self.delete_roi_btn = QPushButton(QCoreApplication.translate("RoiDefinitionWidget", "删除选中 ROI"))
        for b in (self.add_roi_btn, self.update_roi_btn, self.delete_roi_btn):
            b.setMinimumHeight(32)
        roi_layout.addRow(self.add_roi_btn)
        roi_layout.addRow(self.update_roi_btn)
        roi_layout.addRow(self.delete_roi_btn)

        self.start_frame_backward.pressed.connect(lambda: self.navigate_pressed.emit(self.start_time_edit, -1))
        self.start_frame_forward.pressed.connect(lambda: self.navigate_pressed.emit(self.start_time_edit, 1))
        self.end_frame_backward.pressed.connect(lambda: self.navigate_pressed.emit(self.end_time_edit, -1))
        self.end_frame_forward.pressed.connect(lambda: self.navigate_pressed.emit(self.end_time_edit, 1))

        self.start_frame_backward.released.connect(self.navigate_released)
        self.start_frame_forward.released.connect(self.navigate_released)
        self.end_frame_backward.released.connect(self.navigate_released)
        self.end_frame_forward.released.connect(self.navigate_released)

        self.start_time_edit.editingFinished.connect(lambda: self.time_edit_finished.emit(self.start_time_edit))
        self.end_time_edit.editingFinished.connect(lambda: self.time_edit_finished.emit(self.end_time_edit))

        self.set_start_btn.clicked.connect(lambda: self.set_time_requested.emit(self.start_time_edit))
        self.set_end_btn.clicked.connect(lambda: self.set_time_requested.emit(self.end_time_edit))

        self.add_roi_btn.clicked.connect(self.add_roi_requested)
        self.update_roi_btn.clicked.connect(self.update_roi_requested)
        self.delete_roi_btn.clicked.connect(self.delete_roi_requested)
        self.pose_tags_checkbox.toggled.connect(self.pose_tags_toggled.emit)
        self.motion_brightness_checkbox.toggled.connect(
            self.motion_brightness_toggled.emit)
        self.motion_occlusion_checkbox.toggled.connect(
            self.motion_occlusion_toggled.emit)
        self.scene_text_policy_combo.currentIndexChanged.connect(
            lambda _idx: self.scene_policy_changed.emit(
                self.scene_text_policy_combo.currentData() or "overlap"))

        self.color_restrict_checkbox.toggled.connect(self._on_color_restrict_toggled)
        self.text_color_btn.clicked.connect(self._pick_text_color)
        self.outline_color_btn.clicked.connect(self._pick_outline_color)
        self.shadow_color_btn.clicked.connect(self._pick_shadow_color)

        self._on_color_restrict_toggled(self.color_restrict_checkbox.isChecked())
        self._refresh_color_button_styles()

    @staticmethod
    def _bgr_to_qcolor(bgr: List[int]) -> QColor:
        return QColor(int(bgr[2]), int(bgr[1]), int(bgr[0]))

    @staticmethod
    def _qcolor_to_bgr(c: QColor) -> List[int]:
        return [c.blue(), c.green(), c.red()]

    def _refresh_color_button_styles(self) -> None:
        def style_btn(btn: QPushButton, bgr: List[int]) -> None:
            qc = self._bgr_to_qcolor(bgr)
            btn.setStyleSheet(
                f"background-color: {qc.name()}; color: {'#000' if qc.lightness() > 128 else '#fff'}; "
                f"border: 1px solid #888; min-height: 1.5em;"
            )

        style_btn(self.text_color_btn, self._text_bgr)
        style_btn(self.outline_color_btn, self._outline_bgr)
        style_btn(self.shadow_color_btn, self._shadow_bgr)

    def _on_color_restrict_toggled(self, checked: bool) -> None:
        self._color_details_widget.setVisible(bool(checked))
        for w in (
            self.text_color_btn,
            self.outline_color_btn,
            self.shadow_color_btn,
            self.color_tolerance_spin,
            self.color_morph_spin,
        ):
            w.setEnabled(checked)

    def _pick_text_color(self) -> None:
        c = QColorDialog.getColor(
            self._bgr_to_qcolor(self._text_bgr),
            self,
            QCoreApplication.translate("RoiDefinitionWidget", "字幕文字颜色"),
        )
        if c.isValid():
            self._text_bgr = self._qcolor_to_bgr(c)
            self._refresh_color_button_styles()

    def _pick_outline_color(self) -> None:
        c = QColorDialog.getColor(
            self._bgr_to_qcolor(self._outline_bgr),
            self,
            QCoreApplication.translate("RoiDefinitionWidget", "字幕描边颜色"),
        )
        if c.isValid():
            self._outline_bgr = self._qcolor_to_bgr(c)
            self._refresh_color_button_styles()

    def _pick_shadow_color(self) -> None:
        c = QColorDialog.getColor(
            self._bgr_to_qcolor(self._shadow_bgr),
            self,
            QCoreApplication.translate("RoiDefinitionWidget", "字幕阴影颜色"),
        )
        if c.isValid():
            self._shadow_bgr = self._qcolor_to_bgr(c)
            self._refresh_color_button_styles()

    def set_color_restrict_controls_enabled(self, enabled: bool) -> None:
        self.color_restrict_checkbox.setEnabled(enabled)
        self._on_color_restrict_toggled(enabled and self.color_restrict_checkbox.isChecked())
        self.blur_checkbox.setEnabled(enabled)
        self.fade_in_refine_checkbox.setEnabled(enabled)
        self.pose_tags_checkbox.setEnabled(enabled)
        # 亮度自适应/遮挡蒙版随 pose 联动:总开关关闭时必然不可用;
        # pose 关闭时也不可用。
        self.motion_brightness_checkbox.setEnabled(
            enabled and self.pose_tags_checkbox.isChecked())
        self.motion_occlusion_checkbox.setEnabled(
            enabled and self.pose_tags_checkbox.isChecked())
        self.scene_text_policy_combo.setEnabled(enabled)

    def get_color_restrict_dict(self) -> Optional[Dict]:
        if not self.color_restrict_checkbox.isChecked():
            return None
        return {
            "enabled": True,
            "text_bgr": list(self._text_bgr),
            "outline_bgr": list(self._outline_bgr),
            "shadow_bgr": list(self._shadow_bgr),
            "tolerance": int(self.color_tolerance_spin.value()),
            "morph_kernel": int(self.color_morph_spin.value()),
        }

    def set_color_restrict_from_roi(self, roi: Optional[Dict]) -> None:
        if not roi:
            self.color_restrict_checkbox.setChecked(False)
            self.blur_checkbox.setChecked(False)
            # Default for a NEW ROI: boundary refinement on (frame-accurate timing).
            self.fade_in_refine_checkbox.setChecked(True)
            self.pose_tags_checkbox.setChecked(False)
            # 新 ROI 复位:亮度自适应/遮挡蒙版回到默认关(pose 联动会同时禁用)。
            self.motion_brightness_checkbox.setChecked(False)
            self.motion_occlusion_checkbox.setChecked(False)
            # 新 ROI 复位:场景文字显示策略回到默认「叠加」。
            self.scene_text_policy_combo.setCurrentIndex(0)
            self._text_bgr = [255, 255, 255]
            self._outline_bgr = [0, 0, 0]
            self._shadow_bgr = [0, 0, 0]
            self.color_tolerance_spin.setValue(40)
            self.color_morph_spin.setValue(3)
            self._refresh_color_button_styles()
            self._on_color_restrict_toggled(False)
            return
        spec = roi.get("color_restrict")
        self.blur_checkbox.setChecked(bool(roi.get("blur_enabled", False)))
        # 缺省视为开启（与 pipeline 的边界精修默认一致），旧配置未写该字段时
        # 复选框状态也反映实际行为。
        self.fade_in_refine_checkbox.setChecked(bool(roi.get("fade_in_refine_enabled", True)))
        self.pose_tags_checkbox.setChecked(bool(roi.get("write_pose_tags", False)))
        # 回填轨迹亮度自适应(旧配置缺省关;勾选状态受 pose 联动启用)。
        self.motion_brightness_checkbox.setChecked(
            bool(roi.get("motion_auto_brightness", False)))
        self.motion_brightness_checkbox.setEnabled(
            self.pose_tags_checkbox.isChecked())
        # 回填轨迹遮挡蒙版(旧配置缺省关;勾选状态受 pose 联动启用)。
        self.motion_occlusion_checkbox.setChecked(
            bool(roi.get("motion_occlusion_clip", False)))
        self.motion_occlusion_checkbox.setEnabled(
            self.pose_tags_checkbox.isChecked())
        # 回填场景文字显示策略(旧配置缺省/未知值 → 叠加)。
        policy_idx = self.scene_text_policy_combo.findData(
            str(roi.get("scene_text_policy") or "overlap"))
        self.scene_text_policy_combo.setCurrentIndex(
            policy_idx if policy_idx >= 0 else 0)
        if not isinstance(spec, dict) or not spec.get("enabled"):
            self.color_restrict_checkbox.setChecked(False)
            tb = spec.get("text_bgr") if isinstance(spec, dict) else None
            ob = spec.get("outline_bgr") if isinstance(spec, dict) else None
            sb = spec.get("shadow_bgr") if isinstance(spec, dict) else None
            if isinstance(tb, (list, tuple)) and len(tb) == 3:
                self._text_bgr = [int(x) for x in tb]
            else:
                self._text_bgr = [255, 255, 255]
            if isinstance(ob, (list, tuple)) and len(ob) == 3:
                self._outline_bgr = [int(x) for x in ob]
            else:
                self._outline_bgr = [0, 0, 0]
            if isinstance(sb, (list, tuple)) and len(sb) == 3:
                self._shadow_bgr = [int(x) for x in sb]
            else:
                self._shadow_bgr = [0, 0, 0]
            self._refresh_color_button_styles()
            self._on_color_restrict_toggled(False)
            return
        self.color_restrict_checkbox.setChecked(True)
        tb = spec.get("text_bgr")
        ob = spec.get("outline_bgr")
        sb = spec.get("shadow_bgr")
        if isinstance(tb, (list, tuple)) and len(tb) == 3:
            self._text_bgr = [int(x) for x in tb]
        else:
            self._text_bgr = [255, 255, 255]
        if isinstance(ob, (list, tuple)) and len(ob) == 3:
            self._outline_bgr = [int(x) for x in ob]
        else:
            self._outline_bgr = [0, 0, 0]
        if isinstance(sb, (list, tuple)) and len(sb) == 3:
            self._shadow_bgr = [int(x) for x in sb]
        else:
            self._shadow_bgr = [0, 0, 0]
        self.color_tolerance_spin.setValue(int(spec.get("tolerance", 40)))
        self.color_morph_spin.setValue(int(spec.get("morph_kernel", 3)))
        self._refresh_color_button_styles()
        self._on_color_restrict_toggled(True)
