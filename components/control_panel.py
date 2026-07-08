# components/control_panel.py
from __future__ import annotations

import copy
from functools import partial
from typing import Any, Dict, Optional

from PySide6.QtWidgets import (
    QWidget,
    QGroupBox,
    QVBoxLayout,
    QHBoxLayout,
    QGridLayout,
    QLineEdit,
    QPushButton,
    QRadioButton,
    QCheckBox,
    QLabel,
    QComboBox,
)
from PySide6.QtCore import Signal, QCoreApplication, Qt, QThread, QTimer

from utils.app_qsettings import clear_saved_api_key, load_saved_llm, save_llm

from core.subtitle_llm_polish import (
    DEFAULT_DEEPSEEK_BASE,
    DEFAULT_DEEPSEEK_MODEL,
    DEFAULT_OPENAI_BASE,
    fetch_openai_compatible_model_ids,
    pick_default_openai_compatible_model,
)


class _FetchOpenAIModelsThread(QThread):
    finished_ok = Signal(list)
    failed = Signal(str)

    def __init__(self, api_key: str, api_base: str, parent=None):
        super().__init__(parent)
        self._api_key = api_key
        self._api_base = api_base

    def run(self) -> None:
        try:
            models = fetch_openai_compatible_model_ids(self._api_key, self._api_base)
            self.finished_ok.emit(models)
        except Exception as e:
            self.failed.emit(str(e))


class ControlPanelWidget(QWidget):
    draw_mode_changed = Signal(str)
    browse_template_requested = Signal()
    run_pipeline_requested = Signal()
    color_gate_preview_requested = Signal()
    auto_detection_requested = Signal()  # User requests re-analysis
    preset_changed_by_user = Signal(str)  # User manually switched preset

    def __init__(self, parent=None):
        super().__init__(parent)

        self._models_fetch_gen = 0
        self._fetch_thread: Optional[_FetchOpenAIModelsThread] = None
        self._color_gate_spec: Optional[Dict[str, Any]] = None
        self._gate_preview_allowed: bool = False
        self._loading_llm_settings: bool = False
        self._detection_result: Optional[object] = None  # DetectionResult
        self._user_overrode: bool = False
        self._suppress_preset_signal: bool = False

        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(10)

        template_group = QGroupBox(QCoreApplication.translate("ControlPanelWidget", "样式模板（可选）"))
        template_layout = QHBoxLayout(template_group)
        template_layout.setContentsMargins(10, 10, 10, 10)
        template_layout.setSpacing(8)
        self.template_path_edit = QLineEdit()
        self.template_path_edit.setPlaceholderText(QCoreApplication.translate("ControlPanelWidget", "点击“浏览”选择 .ass 模板文件"))
        self.browse_template_btn = QPushButton(QCoreApplication.translate("ControlPanelWidget", "浏览…"))
        self.browse_template_btn.setMinimumHeight(30)
        template_layout.addWidget(self.template_path_edit)
        template_layout.addWidget(self.browse_template_btn)
        main_layout.addWidget(template_group)

        # ── OCR 引擎选择 ──
        engine_group = QGroupBox(QCoreApplication.translate("ControlPanelWidget", "OCR 引擎"))
        engine_layout = QGridLayout(engine_group)
        engine_layout.setContentsMargins(10, 10, 10, 10)
        engine_layout.setSpacing(6)
        self.ocr_engine_label = QLabel(QCoreApplication.translate("ControlPanelWidget", "引擎选择："))
        self.ocr_engine_combo = QComboBox()
        self.ocr_engine_status = QLabel("")
        self.ocr_engine_detect_btn = QPushButton(QCoreApplication.translate("ControlPanelWidget", "检测可用引擎"))
        engine_layout.addWidget(self.ocr_engine_label, 0, 0)
        engine_layout.addWidget(self.ocr_engine_combo, 0, 1, 1, 2)
        engine_layout.addWidget(self.ocr_engine_status, 1, 0, 1, 3)
        engine_layout.addWidget(self.ocr_engine_detect_btn, 0, 3)
        main_layout.addWidget(engine_group)

        # ── 文字来源过滤 ──
        source_filter_group = QGroupBox(QCoreApplication.translate("ControlPanelWidget", "文字来源过滤（上下文语义分析）"))
        source_filter_layout = QVBoxLayout(source_filter_group)
        source_filter_layout.setContentsMargins(10, 10, 10, 10)
        source_filter_layout.setSpacing(6)

        # Enable/disable toggle for source filtering
        self.source_filter_enabled_checkbox = QCheckBox(
            QCoreApplication.translate(
                "ControlPanelWidget",
                "启用文字来源过滤（实验性）",
            )
        )
        self.source_filter_enabled_checkbox.setToolTip(
            QCoreApplication.translate(
                "ControlPanelWidget",
                "勾选后，OCR 识别结果将经过上下文语义分析，自动区分后期叠加字幕与实拍场景文字。"
                "未勾选时保留所有识别到的文字。",
            )
        )
        self.source_filter_enabled_checkbox.setChecked(False)
        source_filter_layout.addWidget(self.source_filter_enabled_checkbox)

        # Status label (shows auto-detection result)
        self.source_status_label = QLabel(QCoreApplication.translate("ControlPanelWidget", "🔄 加载视频后将自动分析..."))
        self.source_status_label.setWordWrap(True)
        self.source_status_label.setTextFormat(Qt.TextFormat.RichText)
        self.source_status_label.setOpenExternalLinks(False)
        self.source_status_label.linkActivated.connect(self._on_status_edit_clicked)
        source_filter_layout.addWidget(self.source_status_label)

        # Scene preset combo
        preset_row = QHBoxLayout()
        preset_row.setSpacing(8)
        self.scene_preset_label = QLabel(QCoreApplication.translate("ControlPanelWidget", "场景预设："))
        self.scene_preset_combo = QComboBox()
        self.scene_preset_combo.setToolTip(QCoreApplication.translate("ControlPanelWidget", "选择适合当前视频的场景类型，自动配置文字来源过滤规则"))
        preset_row.addWidget(self.scene_preset_label)
        preset_row.addWidget(self.scene_preset_combo, 1)
        source_filter_layout.addLayout(preset_row)

        # Three checkboxes for source filtering
        self.keep_overlay_checkbox = QCheckBox(
            QCoreApplication.translate(
                "ControlPanelWidget",
                "保留后期叠加文字 (OVERLAY)\n    字幕、标题、水印、UI 按钮、弹幕、特效文字",
            )
        )
        self.keep_scene_checkbox = QCheckBox(
            QCoreApplication.translate(
                "ControlPanelWidget",
                "保留实拍场景文字 (SCENE)\n    店铺招牌、路牌、宣传海报、书本、屏幕、标牌",
            )
        )
        self.keep_unknown_checkbox = QCheckBox(
            QCoreApplication.translate(
                "ControlPanelWidget",
                "保留无法判定文字 (UNKNOWN)\n    分类器置信度不足的边界情况",
            )
        )
        source_filter_layout.addWidget(self.keep_overlay_checkbox)
        source_filter_layout.addWidget(self.keep_scene_checkbox)
        source_filter_layout.addWidget(self.keep_unknown_checkbox)

        # Alternative presets row
        self.alternative_row = QHBoxLayout()
        self.alternative_row.setSpacing(6)
        self.alternative_label = QLabel(QCoreApplication.translate("ControlPanelWidget", "💡 备选方案:"))
        self.alternative_label.setVisible(False)
        self.alternative_row.addWidget(self.alternative_label)
        self.alternative_buttons: List[QPushButton] = []
        self.alternative_row.addStretch(1)
        source_filter_layout.addLayout(self.alternative_row)

        # Rationale label
        self.rationale_label = QLabel("")
        self.rationale_label.setWordWrap(True)
        self.rationale_label.setStyleSheet("color: palette(mid); font-size: 11px;")
        source_filter_layout.addWidget(self.rationale_label)

        # Action buttons row
        action_row = QHBoxLayout()
        action_row.setSpacing(8)
        self.reanalyze_btn = QPushButton(QCoreApplication.translate("ControlPanelWidget", "🔄 重新自动检测"))
        self.reanalyze_btn.setToolTip(QCoreApplication.translate("ControlPanelWidget", "重新采样分析当前视频并更新类型判定"))
        self.source_filter_reset_btn = QPushButton(QCoreApplication.translate("ControlPanelWidget", "重置为预设默认值"))
        action_row.addWidget(self.reanalyze_btn)
        action_row.addWidget(self.source_filter_reset_btn)
        action_row.addStretch(1)
        source_filter_layout.addLayout(action_row)

        main_layout.addWidget(source_filter_group)

        draw_mode_group = QGroupBox(QCoreApplication.translate("ControlPanelWidget", "绘制模式"))
        draw_mode_layout = QHBoxLayout(draw_mode_group)
        draw_mode_layout.setContentsMargins(10, 10, 10, 10)
        draw_mode_layout.setSpacing(12)
        self.rect_mode_radio = QRadioButton(QCoreApplication.translate("ControlPanelWidget", "矩形（拖动）"))
        self.poly_mode_radio = QRadioButton(QCoreApplication.translate("ControlPanelWidget", "多边形（点击）"))
        self.rect_mode_radio.setChecked(True)
        draw_mode_layout.addWidget(self.rect_mode_radio)
        draw_mode_layout.addWidget(self.poly_mode_radio)
        draw_mode_layout.addStretch(1)
        main_layout.addWidget(draw_mode_group)

        extract_group = QGroupBox(QCoreApplication.translate("ControlPanelWidget", "字幕生成"))
        extract_layout = QVBoxLayout(extract_group)
        extract_layout.setContentsMargins(10, 10, 10, 10)
        extract_layout.setSpacing(8)
        self.run_pipeline_btn = QPushButton(QCoreApplication.translate("ControlPanelWidget", "字幕 OCR 识别并导出"))
        self.run_pipeline_btn.setMinimumHeight(34)

        options_layout = QGridLayout()
        options_layout.setHorizontalSpacing(12)
        options_layout.setVerticalSpacing(6)
        self.debug_mode_checkbox = QCheckBox(QCoreApplication.translate("ControlPanelWidget", "调试模式"))
        self.visualize_checkbox = QCheckBox(QCoreApplication.translate("ControlPanelWidget", "可视化输出"))
        self.in_memory_mode_checkbox = QCheckBox(QCoreApplication.translate("ControlPanelWidget", "内存模式（实验性）"))
        self.save_intermediate_checkbox = QCheckBox(QCoreApplication.translate("ControlPanelWidget", "保存中间 JSON"))
        self.time_slice_checkbox = QCheckBox(QCoreApplication.translate("ControlPanelWidget", "按时间分片并行（可选）"))
        self.merge_roi_checkbox = QCheckBox(QCoreApplication.translate("ControlPanelWidget", "合并 ROI（每帧只 OCR 一次）"))
        self.time_slice_seconds_edit = QLineEdit()
        self.time_slice_seconds_edit.setPlaceholderText(QCoreApplication.translate("ControlPanelWidget", "秒"))
        self.time_slice_seconds_edit.setFixedWidth(60)
        self.time_slice_seconds_edit.setText("10")
        seconds_label = QLabel(QCoreApplication.translate("ControlPanelWidget", "秒"))
        self.visualize_checkbox.setChecked(False)
        self.debug_mode_checkbox.setChecked(False)
        self.in_memory_mode_checkbox.setChecked(True)
        self.save_intermediate_checkbox.setChecked(False)
        self.time_slice_checkbox.setChecked(False)
        self.merge_roi_checkbox.setChecked(False)
        options_layout.addWidget(self.debug_mode_checkbox, 0, 0)
        options_layout.addWidget(self.visualize_checkbox, 0, 1)
        options_layout.addWidget(self.in_memory_mode_checkbox, 1, 0)
        options_layout.addWidget(self.save_intermediate_checkbox, 1, 1)
        options_layout.addWidget(self.time_slice_checkbox, 2, 0)
        options_layout.addWidget(self.merge_roi_checkbox, 2, 1)
        options_layout.addWidget(self.time_slice_seconds_edit, 2, 2)
        options_layout.addWidget(seconds_label, 2, 3)
        options_layout.setColumnStretch(4, 1)

        self.color_gate_checkbox = QCheckBox(
            QCoreApplication.translate(
                "ControlPanelWidget",
                "半自动：按字幕颜色跳过疑似无字帧（默认关，需预览并确认后才生效）",
            )
        )
        self.color_gate_checkbox.setChecked(False)
        self.color_gate_checkbox.setToolTip(
            QCoreApplication.translate(
                "ControlPanelWidget",
                "在阶段一抽样 ROI 区间内若干帧并在当前画面上自动标定 HSV。"
                "仅当预览结果满意并在对话框中点击「采用」后，才会在本轮 OCR 启用；可随时关闭恢复默认逻辑。",
            )
        )
        self.color_gate_preview_btn = QPushButton(
            QCoreApplication.translate("ControlPanelWidget", "预览检测效果…")
        )
        self.color_gate_preview_btn.setEnabled(False)
        self.color_gate_status_label = QLabel(self._color_gate_status_text())
        self.color_gate_status_label.setWordWrap(True)
        gate_tip = QLabel(
            QCoreApplication.translate(
                "ControlPanelWidget",
                "若预览不满意或选择「不采用」，请保持勾选关闭或未确认——程序将按原版流程输出全部 ROI 帧。",
            )
        )
        gate_tip.setWordWrap(True)
        gate_tip.setStyleSheet("color: palette(mid); font-size: 11px;")
        gate_row = QHBoxLayout()
        gate_row.setSpacing(8)
        gate_row.addWidget(self.color_gate_checkbox, 0)
        gate_row.addWidget(self.color_gate_preview_btn, 0)
        gate_row.addStretch(1)

        llm_polish_grid = QGridLayout()
        llm_polish_grid.setHorizontalSpacing(12)
        llm_polish_grid.setVerticalSpacing(6)
        self.deepseek_polish_checkbox = QCheckBox(QCoreApplication.translate("ControlPanelWidget", "DeepSeek 字幕润色"))
        self.deepseek_fragment_merge_checkbox = QCheckBox(
            QCoreApplication.translate(
                "ControlPanelWidget",
                "DeepSeek 合并碎片字幕（选择最完整文本并合并时间范围）",
            )
        )
        llm_tip = QLabel(
            QCoreApplication.translate(
                "ControlPanelWidget",
                "兼容 OpenAI 的接口。默认提供方为 DeepSeek，会自动填充 Base URL；当你输入 API Key 后，"
                "应用会调用 /v1/models 拉取模型列表（也可手动编辑模型 ID）。",
            )
        )
        llm_tip.setWordWrap(True)
        llm_tip.setStyleSheet("color: palette(mid); font-size: 11px;")
        self.deepseek_api_key_edit = QLineEdit()
        self.deepseek_api_key_edit.setPlaceholderText(QCoreApplication.translate("ControlPanelWidget", "API Key"))
        self.deepseek_api_key_edit.setEchoMode(QLineEdit.Password)
        self.deepseek_api_base_edit = QLineEdit()
        self.deepseek_api_base_edit.setPlaceholderText(QCoreApplication.translate("ControlPanelWidget", "API Base URL"))
        self.deepseek_api_base_edit.setText(DEFAULT_DEEPSEEK_BASE)

        self.llm_provider_label = QLabel(QCoreApplication.translate("ControlPanelWidget", "大模型提供方"))
        self.llm_provider_combo = QComboBox()
        self.llm_provider_combo.addItem(QCoreApplication.translate("ControlPanelWidget", "DeepSeek"), DEFAULT_DEEPSEEK_BASE)
        self.llm_provider_combo.addItem(QCoreApplication.translate("ControlPanelWidget", "OpenAI"), DEFAULT_OPENAI_BASE)
        self.llm_provider_combo.addItem(QCoreApplication.translate("ControlPanelWidget", "自定义（手动 Base URL）"), None)
        self.llm_provider_combo.setCurrentIndex(0)

        self.deepseek_model_combo = QComboBox()
        self.deepseek_model_combo.setEditable(True)
        self.deepseek_model_combo.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        le = self.deepseek_model_combo.lineEdit()
        if le is not None:
            le.setPlaceholderText(QCoreApplication.translate("ControlPanelWidget", "模型 ID（可从 API 自动拉取）"))
        self.deepseek_model_combo.addItem(DEFAULT_DEEPSEEK_MODEL)
        self.deepseek_model_combo.setCurrentText(DEFAULT_DEEPSEEK_MODEL)

        self.refresh_models_btn = QPushButton(QCoreApplication.translate("ControlPanelWidget", "刷新模型列表"))
        self.refresh_models_btn.setToolTip(QCoreApplication.translate("ControlPanelWidget", "使用 API Key 和 Base URL 拉取 /v1/models。"))

        self.clear_saved_key_btn = QPushButton(QCoreApplication.translate("ControlPanelWidget", "清除已存密钥"))
        self.clear_saved_key_btn.setToolTip(
            QCoreApplication.translate(
                "ControlPanelWidget",
                "从本机配置中删除已保存的 API Key（输入框会清空）。Base URL 与模型仍会保留。",
            )
        )

        self.deepseek_strategy_checkbox = QCheckBox(
            QCoreApplication.translate(
                "ControlPanelWidget",
                "DeepSeek 合并策略复核（按更合适的阈值重新合并）",
            )
        )
        self.deepseek_polish_checkbox.setChecked(False)
        self.deepseek_fragment_merge_checkbox.setChecked(False)
        self.deepseek_strategy_checkbox.setChecked(False)
        llm_polish_grid.addWidget(self.deepseek_polish_checkbox, 0, 0, 1, 4)
        llm_polish_grid.addWidget(self.deepseek_fragment_merge_checkbox, 1, 0, 1, 4)
        llm_polish_grid.addWidget(llm_tip, 2, 0, 1, 4)
        llm_polish_grid.addWidget(self.llm_provider_label, 3, 0, 1, 1)
        llm_polish_grid.addWidget(self.llm_provider_combo, 3, 1, 1, 3)
        deepseek_key_row = QHBoxLayout()
        deepseek_key_row.setSpacing(8)
        deepseek_key_row.addWidget(self.deepseek_api_key_edit, 1)
        deepseek_key_row.addWidget(self.clear_saved_key_btn)
        llm_polish_grid.addLayout(deepseek_key_row, 4, 0, 1, 3)
        llm_polish_grid.addWidget(self.refresh_models_btn, 4, 3, 1, 1)
        llm_polish_grid.addWidget(self.deepseek_api_base_edit, 5, 0, 1, 2)
        llm_polish_grid.addWidget(self.deepseek_model_combo, 5, 2, 1, 2)
        llm_polish_grid.addWidget(self.deepseek_strategy_checkbox, 6, 0, 1, 4)

        extract_layout.addWidget(self.run_pipeline_btn)
        extract_layout.addLayout(options_layout)
        extract_layout.addLayout(gate_row)
        extract_layout.addWidget(self.color_gate_status_label)
        extract_layout.addWidget(gate_tip)
        extract_layout.addLayout(llm_polish_grid)
        main_layout.addWidget(extract_group)

        self.browse_template_btn.clicked.connect(self.browse_template_requested)
        self.run_pipeline_btn.clicked.connect(self.run_pipeline_requested)
        self.rect_mode_radio.toggled.connect(self._on_draw_mode_toggled)
        self.llm_provider_combo.currentIndexChanged.connect(self._on_llm_provider_changed)
        self.deepseek_api_key_edit.editingFinished.connect(self._on_deepseek_credentials_edited)
        self.deepseek_api_base_edit.editingFinished.connect(self._on_deepseek_credentials_edited)
        self.refresh_models_btn.clicked.connect(self._maybe_refresh_models)
        self.clear_saved_key_btn.clicked.connect(self._clear_saved_deepseek_key_clicked)
        m_edit = self.deepseek_model_combo.lineEdit()
        if m_edit is not None:
            m_edit.editingFinished.connect(self._save_llm_to_settings)
        self.color_gate_checkbox.toggled.connect(self._on_color_gate_toggled)
        self.color_gate_preview_btn.clicked.connect(self.color_gate_preview_requested.emit)

        # New: engine & source filter connections
        self.ocr_engine_detect_btn.clicked.connect(self._refresh_engine_list)
        self.source_filter_enabled_checkbox.toggled.connect(self._on_source_filter_enabled_toggled)
        self.scene_preset_combo.currentIndexChanged.connect(self._on_preset_combo_changed)
        self.keep_overlay_checkbox.toggled.connect(self._on_user_manual_adjust)
        self.keep_scene_checkbox.toggled.connect(self._on_user_manual_adjust)
        self.keep_unknown_checkbox.toggled.connect(self._on_user_manual_adjust)
        self.reanalyze_btn.clicked.connect(self.auto_detection_requested)
        self.source_filter_reset_btn.clicked.connect(self._on_preset_reset)

        self._load_llm_from_settings()
        self._populate_engine_combo()
        self._populate_preset_combo()

        # Apply initial source filter enabled state (disabled by default)
        self._on_source_filter_enabled_toggled(False)

    def _on_source_filter_enabled_toggled(self, checked: bool) -> None:
        """Enable/disable source filter child controls when the master toggle changes."""
        enabled = bool(checked)
        self.scene_preset_label.setEnabled(enabled)
        self.scene_preset_combo.setEnabled(enabled)
        self.keep_overlay_checkbox.setEnabled(enabled)
        self.keep_scene_checkbox.setEnabled(enabled)
        self.keep_unknown_checkbox.setEnabled(enabled)
        self.reanalyze_btn.setEnabled(enabled)
        self.source_filter_reset_btn.setEnabled(enabled)
        self.source_status_label.setEnabled(enabled)
        if not enabled:
            self.source_status_label.setText(
                QCoreApplication.translate("ControlPanelWidget", "🔒 文字来源过滤已关闭，将保留所有识别到的文字。")
            )
        else:
            self.source_status_label.setText(
                QCoreApplication.translate("ControlPanelWidget", "🔄 文字来源过滤已启用，加载视频后将自动分析...")
            )

    def _color_gate_status_text(self) -> str:
        if not self.color_gate_checkbox.isChecked():
            return QCoreApplication.translate("ControlPanelWidget", "颜色门控：已关闭（默认）。")
        if self._color_gate_spec is None:
            return QCoreApplication.translate(
                "ControlPanelWidget",
                "颜色门控：已勾选，尚未确认。请点击「预览检测效果」并在满意时选择「采用」。",
            )
        return QCoreApplication.translate(
            "ControlPanelWidget",
            "颜色门控：已确认，将用于下一轮「字幕 OCR 识别」阶段一截取。",
        )

    def _refresh_color_gate_status_label(self) -> None:
        self.color_gate_status_label.setText(self._color_gate_status_text())

    def _on_color_gate_toggled(self, checked: bool) -> None:
        if not checked:
            self._color_gate_spec = None
        self.set_color_gate_preview_allowed(self._gate_preview_allowed)
        self._refresh_color_gate_status_label()

    def reset_color_gate(self) -> None:
        self.color_gate_checkbox.blockSignals(True)
        self.color_gate_checkbox.setChecked(False)
        self.color_gate_checkbox.blockSignals(False)
        self._color_gate_spec = None
        self.set_color_gate_preview_allowed(self._gate_preview_allowed)
        self._refresh_color_gate_status_label()

    def invalidate_color_gate_confirmation(self) -> None:
        self._color_gate_spec = None
        self._refresh_color_gate_status_label()

    def adopt_color_gate_spec(self, spec: Dict[str, Any]) -> None:
        self._color_gate_spec = copy.deepcopy(spec)
        self.color_gate_checkbox.setChecked(True)
        self.set_color_gate_preview_allowed(self._gate_preview_allowed)
        self._refresh_color_gate_status_label()

    def abandon_color_gate_after_preview(self) -> None:
        self.reset_color_gate()

    def set_color_gate_preview_allowed(self, allowed: bool) -> None:
        self._gate_preview_allowed = bool(allowed)
        self.color_gate_preview_btn.setEnabled(
            bool(allowed and self.color_gate_checkbox.isChecked())
        )

    def get_color_presence_gate_spec(self) -> Optional[Dict[str, Any]]:
        if not self.color_gate_checkbox.isChecked():
            return None
        return self._color_gate_spec

    def _on_draw_mode_toggled(self, checked):
        if checked:
            self.draw_mode_changed.emit("rect")
        else:
            self.draw_mode_changed.emit("poly")

    def _save_llm_to_settings(self) -> None:
        if self._loading_llm_settings:
            return
        save_llm(
            api_key=self.deepseek_api_key_edit.text(),
            api_base=self.deepseek_api_base_edit.text().strip(),
            model=self.deepseek_model_combo.currentText().strip(),
            provider_index=self.llm_provider_combo.currentIndex(),
        )

    def _load_llm_from_settings(self) -> None:
        self._loading_llm_settings = True
        self.llm_provider_combo.blockSignals(True)
        self.deepseek_model_combo.blockSignals(True)
        try:
            key, base, model, prov = load_saved_llm()
            if key:
                self.deepseek_api_key_edit.setText(key)
            if base:
                self.deepseek_api_base_edit.setText(base)
            if model:
                self.deepseek_model_combo.setCurrentText(model)
            if 0 <= prov < self.llm_provider_combo.count():
                self.llm_provider_combo.setCurrentIndex(prov)
        finally:
            self.deepseek_model_combo.blockSignals(False)
            self.llm_provider_combo.blockSignals(False)
            self._loading_llm_settings = False
        if self.deepseek_api_key_edit.text().strip() and self.deepseek_api_base_edit.text().strip():
            QTimer.singleShot(0, self._maybe_refresh_models)

    def _on_deepseek_credentials_edited(self) -> None:
        self._save_llm_to_settings()
        self._maybe_refresh_models()

    def _clear_saved_deepseek_key_clicked(self) -> None:
        clear_saved_api_key()
        self.deepseek_api_key_edit.clear()
        self._save_llm_to_settings()

    def _on_llm_provider_changed(self, _index: int) -> None:
        preset = self.llm_provider_combo.currentData()
        if preset:
            self.deepseek_api_base_edit.setText(str(preset))
        self._save_llm_to_settings()
        self._maybe_refresh_models()

    def _maybe_refresh_models(self) -> None:
        key = self.deepseek_api_key_edit.text().strip()
        base = self.deepseek_api_base_edit.text().strip()
        if not key or not base:
            return
        self._models_fetch_gen += 1
        gen = self._models_fetch_gen
        self.refresh_models_btn.setEnabled(False)
        self.refresh_models_btn.setToolTip(
            QCoreApplication.translate("ControlPanelWidget", "使用 API Key 和 Base URL 拉取 /v1/models。")
        )

        t = _FetchOpenAIModelsThread(key, base, self)
        self._fetch_thread = t
        t.finished_ok.connect(
            partial(self._apply_models_fetch_result, gen),
            Qt.ConnectionType.SingleShotConnection,
        )
        t.failed.connect(
            partial(self._on_models_fetch_error, gen),
            Qt.ConnectionType.SingleShotConnection,
        )
        t.finished.connect(lambda: self.refresh_models_btn.setEnabled(True))
        t.start()

    def _apply_models_fetch_result(self, gen: int, model_ids: list) -> None:
        if gen != self._models_fetch_gen:
            return
        prev = self.deepseek_model_combo.currentText().strip()
        base_url = self.deepseek_api_base_edit.text().strip()
        self.deepseek_model_combo.blockSignals(True)
        self.deepseek_model_combo.clear()
        for m in model_ids:
            self.deepseek_model_combo.addItem(str(m))
        chosen = prev
        if chosen and self.deepseek_model_combo.findText(chosen, Qt.MatchFlag.MatchExactly) >= 0:
            self.deepseek_model_combo.setCurrentText(chosen)
        elif chosen:
            self.deepseek_model_combo.setEditText(chosen)
        elif model_ids:
            default_id = pick_default_openai_compatible_model([str(m) for m in model_ids], base_url)
            if default_id and self.deepseek_model_combo.findText(default_id, Qt.MatchFlag.MatchExactly) >= 0:
                self.deepseek_model_combo.setCurrentText(default_id)
            else:
                self.deepseek_model_combo.setCurrentIndex(0)
        else:
            self.deepseek_model_combo.setEditText(DEFAULT_DEEPSEEK_MODEL)
        self.deepseek_model_combo.blockSignals(False)
        self._save_llm_to_settings()

    def _on_models_fetch_error(self, gen: int, msg: str) -> None:
        if gen != self._models_fetch_gen:
            return
        self.refresh_models_btn.setToolTip(msg)

    def get_pipeline_options(self) -> dict:
        try:
            slice_seconds = float(self.time_slice_seconds_edit.text().strip() or "10")
        except Exception:
            slice_seconds = 10.0
        if slice_seconds <= 0:
            slice_seconds = 10.0
        model_text = self.deepseek_model_combo.currentText().strip()
        return {
            "template_path": self.template_path_edit.text(),
            "debug": self.debug_mode_checkbox.isChecked(),
            "visualize": self.visualize_checkbox.isChecked(),
            "in_memory": self.in_memory_mode_checkbox.isChecked(),
            "save_intermediate_json": self.save_intermediate_checkbox.isChecked(),
            "time_slice_enabled": self.time_slice_checkbox.isChecked(),
            "time_slice_seconds": slice_seconds,
            "merge_rois": self.merge_roi_checkbox.isChecked(),
            "deepseek_polish": self.deepseek_polish_checkbox.isChecked(),
            "deepseek_fragment_merge": self.deepseek_fragment_merge_checkbox.isChecked(),
            "deepseek_api_key": self.deepseek_api_key_edit.text(),
            "deepseek_api_base": self.deepseek_api_base_edit.text().strip(),
            "deepseek_model": model_text,
            "deepseek_strategy_review": self.deepseek_strategy_checkbox.isChecked(),
            # New: engine and source filter
            "ocr_engine_id": self.get_selected_engine_id(),
            "source_filter_config": self.get_source_filter_config(),
        }

    # ── OCR Engine ComboBox ─────────────────────────────────

    def _populate_engine_combo(self) -> None:
        """Populate the engine combo with available engines."""
        self.ocr_engine_combo.clear()
        try:
            from core.ocr_engine_base import OCREngineRegistry
            available = OCREngineRegistry.list_available()
            if not available:
                self.ocr_engine_combo.addItem(
                    QCoreApplication.translate("ControlPanelWidget", "（无可用引擎）"), ""
                )
                self.ocr_engine_status.setText(
                    QCoreApplication.translate("ControlPanelWidget", "⚠ 未检测到可用 OCR 引擎")
                )
                return
            for info in available:
                label = f"{info.name} ({info.engine_id})"
                self.ocr_engine_combo.addItem(label, info.engine_id)
            # Select default
            default_id = OCREngineRegistry.get_default()
            for i in range(self.ocr_engine_combo.count()):
                if self.ocr_engine_combo.itemData(i) == default_id:
                    self.ocr_engine_combo.setCurrentIndex(i)
                    break
            self.ocr_engine_status.setText(
                QCoreApplication.translate("ControlPanelWidget", "✅ 已就绪")
            )
        except Exception:
            self.ocr_engine_combo.addItem("PaddleOCR", "paddle")
            self.ocr_engine_status.setText("")

    def _refresh_engine_list(self) -> None:
        """Re-detect available engines."""
        self._populate_engine_combo()

    def get_selected_engine_id(self) -> str:
        """Get the currently selected engine ID."""
        idx = self.ocr_engine_combo.currentIndex()
        if idx >= 0:
            return self.ocr_engine_combo.itemData(idx) or ""
        return ""

    # ── Scene Preset ComboBox ───────────────────────────────

    def _populate_preset_combo(self) -> None:
        """Fill the preset combo with all 12 presets."""
        self.scene_preset_combo.clear()
        try:
            from core.scene_presets import PRESETS, ALL_PRESET_IDS
            for pid in ALL_PRESET_IDS:
                preset = PRESETS.get(pid)
                if preset:
                    self.scene_preset_combo.addItem(preset.name, pid)
        except Exception:
            pass

    def _on_preset_combo_changed(self, index: int) -> None:
        """Apply preset defaults when user selects a different preset."""
        if self._suppress_preset_signal or index < 0:
            return
        preset_id = self.scene_preset_combo.itemData(index)
        if not preset_id or preset_id == "custom":
            return
        try:
            from core.scene_presets import get_preset_by_id
            preset = get_preset_by_id(preset_id)
            if preset is None:
                return
            self._apply_preset_checkboxes(preset)
            self._user_overrode = False
            self.preset_changed_by_user.emit(preset_id)
        except Exception:
            pass

    def _apply_preset_checkboxes(self, preset) -> None:
        """Apply a preset's default checkbox states."""
        self.keep_overlay_checkbox.setChecked(preset.keep_overlay)
        self.keep_scene_checkbox.setChecked(preset.keep_scene)
        self.keep_unknown_checkbox.setChecked(preset.keep_unknown)

    def _on_preset_reset(self) -> None:
        """Reset checkboxes to current preset defaults."""
        self._on_preset_combo_changed(self.scene_preset_combo.currentIndex())

    def get_active_preset_id(self) -> str:
        """Get the currently active preset ID."""
        idx = self.scene_preset_combo.currentIndex()
        if idx >= 0:
            return self.scene_preset_combo.itemData(idx) or "custom"
        return "custom"

    # ── Auto-detection result handling ──────────────────────

    def show_detection_in_progress(self) -> None:
        """Show that auto-detection is running."""
        self.source_status_label.setText(
            QCoreApplication.translate("ControlPanelWidget", "🔄 正在分析视频类型...")
        )

    def apply_detection_result(self, result) -> None:
        """Apply auto-detection result to UI.

        Args:
            result: DetectionResult from VideoTypeDetector.
        """
        if self._user_overrode:
            return  # User has manually adjusted; don't override

        self._detection_result = result
        preset = result.recommended_preset

        # Confidence-based status styling
        if result.is_high_confidence:
            icon = "🎬"
            color = "green"
        elif result.is_medium_confidence:
            icon = "🔍"
            color = "#c9a000"
        else:
            icon = "⚠️"
            color = "#cc3300"

        self.source_status_label.setText(
            f'{icon} <span style="color:{color}">'
            f'{QCoreApplication.translate("ControlPanelWidget", "自动识别为：")}'
            f'{preset.name} ({result.confidence:.0%})</span>'
            f'  <a href="edit">✏️</a>'
        )

        # Update preset combo to match
        self._suppress_preset_signal = True
        for i in range(self.scene_preset_combo.count()):
            if self.scene_preset_combo.itemData(i) == preset.preset_id:
                self.scene_preset_combo.setCurrentIndex(i)
                break
        self._suppress_preset_signal = False

        # Apply preset checkboxes
        self._apply_preset_checkboxes(preset)

        # Update alternative buttons
        self._update_alternative_buttons(result.alternative_presets)

        # Update rationale
        self.rationale_label.setText(
            f'{QCoreApplication.translate("ControlPanelWidget", "💡 判定理由：")}{result.rationale}'
        )

    def _update_alternative_buttons(self, alternatives: list) -> None:
        """Show alternative preset buttons."""
        # Clear existing
        for btn in self.alternative_buttons:
            btn.deleteLater()
        self.alternative_buttons.clear()

        if not alternatives:
            self.alternative_label.setVisible(False)
            return

        self.alternative_label.setVisible(True)
        for alt in alternatives[:3]:
            btn = QPushButton(alt.name)
            btn.setToolTip(alt.description)
            btn.setFlat(True)
            btn.setStyleSheet("color: #2a6ab4; text-decoration: underline;")
            btn.clicked.connect(lambda checked, pid=alt.preset_id: self._on_alternative_clicked(pid))
            self.alternative_buttons.append(btn)
            self.alternative_row.insertWidget(
                self.alternative_row.count() - 1, btn
            )

    def _on_alternative_clicked(self, preset_id: str) -> None:
        """User clicked an alternative preset."""
        try:
            from core.scene_presets import get_preset_by_id
            preset = get_preset_by_id(preset_id)
            if preset is None:
                return
            self._suppress_preset_signal = True
            for i in range(self.scene_preset_combo.count()):
                if self.scene_preset_combo.itemData(i) == preset_id:
                    self.scene_preset_combo.setCurrentIndex(i)
                    break
            self._suppress_preset_signal = False
            self._apply_preset_checkboxes(preset)
            self._user_overrode = False
        except Exception:
            pass

    def _on_user_manual_adjust(self) -> None:
        """User manually toggled a checkbox — switch to custom mode."""
        self._user_overrode = True
        self.source_status_label.setText(
            '✏️ <span style="color:#666">'
            + QCoreApplication.translate("ControlPanelWidget", "手动模式（已自定义）")
            + '</span>'
        )
        # Switch to custom preset
        self._suppress_preset_signal = True
        for i in range(self.scene_preset_combo.count()):
            if self.scene_preset_combo.itemData(i) == "custom":
                self.scene_preset_combo.setCurrentIndex(i)
                break
        self._suppress_preset_signal = False

    def _on_status_edit_clicked(self, link: str) -> None:
        """User clicked the edit link on the status label."""
        if link == "edit":
            self._user_overrode = True
            self.source_status_label.setText(
                '✏️ <span style="color:#666">'
                + QCoreApplication.translate("ControlPanelWidget", "手动模式（已自定义）")
                + '</span>'
            )

    # ── Source filter config ─────────────────────────────────

    def get_source_filter_config(self) -> dict:
        """Return the current source filter configuration."""
        try:
            from core.scene_presets import get_preset_by_id
            preset = get_preset_by_id(self.get_active_preset_id())
        except Exception:
            preset = None

        # Build llm_config if the preset has LLM assist enabled
        llm_config = None
        if preset and preset.llm_assist_enabled:
            api_key = self.deepseek_api_key_edit.text().strip()
            if api_key:
                llm_config = {
                    "api_key": api_key,
                    "api_base": self.deepseek_api_base_edit.text().strip(),
                    "model": self.deepseek_model_combo.currentText().strip(),
                }

        return {
            "enabled": self.source_filter_enabled_checkbox.isChecked(),
            "preset_id": self.get_active_preset_id(),
            "keep_overlay": self.keep_overlay_checkbox.isChecked(),
            "keep_scene": self.keep_scene_checkbox.isChecked(),
            "keep_unknown": self.keep_unknown_checkbox.isChecked(),
            "classifier_weights": preset.classifier_weight_override if preset else None,
            "classification_bias": preset.classification_bias if preset else 0.0,
            "min_classification_confidence": preset.min_classification_confidence if preset else 0.35,
            "llm_assist_enabled": preset.llm_assist_enabled if preset else False,
            "llm_config": llm_config,
        }

    def restore_saved_config(self, config: dict) -> None:
        """Restore a previously saved source filter config."""
        if not config:
            return
        self._user_overrode = config.get("user_overrode", False)
        preset_id = config.get("preset_id", "custom")
        self._suppress_preset_signal = True
        for i in range(self.scene_preset_combo.count()):
            if self.scene_preset_combo.itemData(i) == preset_id:
                self.scene_preset_combo.setCurrentIndex(i)
                break
        self._suppress_preset_signal = False
        self.keep_overlay_checkbox.setChecked(config.get("keep_overlay", True))
        self.keep_scene_checkbox.setChecked(config.get("keep_scene", True))
        self.keep_unknown_checkbox.setChecked(config.get("keep_unknown", True))
        if self._user_overrode:
            self.source_status_label.setText(
                '✏️ <span style="color:#666">'
                + QCoreApplication.translate("ControlPanelWidget", "已恢复上次保存的手动设置")
                + '</span>'
            )
