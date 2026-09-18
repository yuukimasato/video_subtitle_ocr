# tests/test_control_panel_quick_mode.py
"""一键控制面板（quick mode）的 UI 默认状态与接口兼容性测试。

计划来源：docs/superpowers/plans/2026-09-14-one-click-control-panel.md。
覆盖：默认首屏（主按钮可用、高级/LLM 组默认折叠、自动引擎/自动模型/
来源过滤关闭/自动 ROI 开启），以及 get_pipeline_options() 字段兼容。

与 test_pipeline_progress_dialog_dismiss_race.py 相同：QApplication 必须在
conftest 的 QCoreApplication 之前创建（QWidget 需要 QApplication）。
"""

from __future__ import annotations

import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import (
    QApplication,
    QAbstractButton,
    QComboBox,
    QLabel,
)

_app = QApplication.instance() or QApplication(sys.argv[:1])

import pytest  # noqa: E402

from PySide6.QtCore import QSettings  # noqa: E402

from components.control_panel import ControlPanelWidget  # noqa: E402

# get_pipeline_options() 的历史字段集合：quick mode 改造不得删除任何一项。
EXPECTED_OPTION_KEYS = frozenset(
    {
        "template_path",
        "debug",
        "visualize",
        "in_memory",
        "save_intermediate_json",
        "time_slice_enabled",
        "time_slice_seconds",
        "merge_rois",
        "chunk_workers",
        "deepseek_polish",
        "deepseek_fragment_merge",
        "deepseek_api_key",
        "deepseek_api_base",
        "deepseek_model",
        "deepseek_strategy_review",
        "ocr_engine_id",
        "ocr_lang",
        "ocr_model_tier",
        "source_filter_config",
    }
)


@pytest.fixture
def panel():
    """Fresh control panel; restores the auto-ROI QSettings key afterwards."""
    settings = QSettings()
    saved_auto_roi = settings.value("pipeline/auto_roi_on_load")
    settings.setValue("pipeline/auto_roi_on_load", True)
    w = ControlPanelWidget()
    yield w
    w.shutdown_background_threads(timeout_ms=100)
    w.deleteLater()
    if saved_auto_roi is None:
        settings.remove("pipeline/auto_roi_on_load")
    else:
        settings.setValue("pipeline/auto_roi_on_load", saved_auto_roi)


def _visible_texts(widget) -> set:
    """All non-empty texts of the panel's labels/buttons/combos that are
    visible within the panel (no show() needed)."""
    texts = set()
    for child in widget.findChildren(QAbstractButton):
        if child.isVisibleTo(widget):
            texts.add(child.text())
    for child in widget.findChildren(QLabel):
        if child.isVisibleTo(widget):
            texts.add(child.text())
    for combo in widget.findChildren(QComboBox):
        if combo.isVisibleTo(widget):
            for i in range(combo.count()):
                texts.add(combo.itemText(i))
    return {t for t in texts if t}


def _find_text_target(panel, term: str):
    """Return the first visible-in-panel widget whose text contains term."""
    for child in panel.findChildren((QAbstractButton, QLabel, QComboBox)):
        candidates = [child.text()]
        if isinstance(child, QComboBox):
            candidates = [child.itemText(i) for i in range(child.count())]
        if any(term in t for t in candidates if t):
            return child
    return None


def test_main_button_present_and_enabled(panel):
    assert panel.run_pipeline_btn is not None
    assert panel.run_pipeline_btn.isVisibleTo(panel)
    assert panel.run_pipeline_btn.isEnabled()


def test_advanced_and_llm_groups_collapsed_by_default(panel):
    assert panel.advanced_group.isCheckable()
    assert panel.advanced_group.isChecked() is False
    assert panel.llm_group.isCheckable()
    assert panel.llm_group.isChecked() is False


def test_get_pipeline_options_keeps_all_fields(panel):
    opts = panel.get_pipeline_options()
    missing = EXPECTED_OPTION_KEYS - set(opts)
    assert not missing, f"get_pipeline_options() lost fields: {sorted(missing)}"


def test_quick_mode_toggle_keeps_controls_and_values(panel):
    """set_quick_mode 只切换可见性：不重建控件、不清空用户设置。"""
    assert panel.quick_mode is True
    assert not panel.source_filter_group.isVisibleTo(panel)

    panel.set_quick_mode(False)
    assert panel.quick_mode is False
    assert panel.source_filter_group.isVisibleTo(panel)
    assert panel.draw_mode_group.isVisibleTo(panel)
    # 完整视图展开引擎详情容器。
    assert panel.engine_group.isChecked() is True

    panel.template_path_edit.setText("/tmp/demo.ass")
    panel.set_quick_mode(True)
    assert panel.template_path_edit.text() == "/tmp/demo.ass"
    assert not panel.source_filter_group.isVisibleTo(panel)
    assert panel.engine_group.isChecked() is False


def test_quick_mode_keeps_roi_drawing_modes_visible(panel):
    assert panel.quick_mode is True
    assert panel.draw_mode_group.isVisibleTo(panel)
    assert panel.rect_mode_radio.isVisibleTo(panel)
    assert panel.poly_mode_radio.isVisibleTo(panel)
    assert panel.edit_mode_radio.isVisibleTo(panel)


def test_action_wording(panel):
    """主/次操作按钮使用用户任务文案（测试进程未装载翻译器，即源文案）。"""
    assert panel.run_pipeline_btn.text() == "开始识别并导出"
    assert panel.adjust_roi_btn.text() == "调整字幕区域"
    assert panel.redetect_btn.text() == "重新检测"


def test_secondary_actions_trigger_existing_signals(panel):
    """次操作复用既有信号：调整字幕区域→编辑模式；重新检测→auto_detection_requested。"""
    modes = []
    panel.draw_mode_changed.connect(modes.append)
    panel.adjust_roi_btn.click()
    assert modes == ["edit"]

    detections = []
    panel.auto_detection_requested.connect(lambda: detections.append(1))
    panel.redetect_btn.click()
    assert detections == [1]


def test_default_view_hides_implementation_terms(panel):
    """默认界面不出现实现术语（进程分片、颜色门控等）。"""
    terms = (
        "进程分片",
        "颜色门控",
        "内存模式",
        "按时间分片并行",
        "场景预设",
        "OVERLAY",
        "SCENE",
        "UNKNOWN",
        "检测可用引擎",
        "刷新模型列表",
        "预览检测效果",
    )
    texts = _visible_texts(panel)
    for term in terms:
        assert not any(term in t for t in texts), f"实现术语泄漏到默认界面: {term}"


def test_full_mode_still_exposes_advanced_controls(panel):
    """完整设置（含展开高级组）下仍能找到全部高级控件。"""
    panel.set_quick_mode(False)
    panel.advanced_group.setChecked(True)
    panel.llm_group.setChecked(True)
    texts = _visible_texts(panel)
    joined = " ".join(texts)
    for term in ("进程分片", "颜色门控", "检测可用引擎", "内存模式"):
        assert term in joined, f"完整设置缺少: {term}"
    assert panel.chunk_workers_combo.isVisibleTo(panel)
    assert panel.chunk_workers_combo.isEnabled()
    assert panel.ocr_engine_detect_btn.isVisibleTo(panel)


def test_source_filter_combo_maps_to_existing_fields(panel):
    """文字保留三选一映射到 keep_overlay/keep_scene/keep_unknown 与 enabled。"""
    combo = panel.source_filter_combo
    assert combo.currentIndex() == 0  # 默认：保留全部文字（过滤关闭）

    combo.setCurrentIndex(1)  # 只保留字幕
    cfg = panel.get_pipeline_options()["source_filter_config"]
    assert cfg["enabled"] is True
    assert cfg["keep_overlay"] is True
    assert cfg["keep_scene"] is False
    assert cfg["keep_unknown"] is False

    combo.setCurrentIndex(2)  # 字幕和画面文字
    cfg = panel.get_pipeline_options()["source_filter_config"]
    assert cfg["enabled"] is True
    assert cfg["keep_overlay"] is True
    assert cfg["keep_scene"] is True
    assert cfg["keep_unknown"] is False

    combo.setCurrentIndex(0)  # 保留全部文字
    assert panel.get_pipeline_options()["source_filter_config"]["enabled"] is False


def test_source_filter_combo_reflects_manual_state(panel):
    """高级设置中的手动组合反馈为「自定义」。"""
    panel.set_quick_mode(False)
    panel.source_filter_enabled_checkbox.setChecked(True)
    # 构造时默认应用第一个预设（keep_overlay 勾选）→「只保留字幕」。
    assert panel.source_filter_combo.currentIndex() == 1
    panel.keep_unknown_checkbox.setChecked(True)
    assert panel.source_filter_combo.currentIndex() == 3  # 未匹配预设组合
    panel.keep_unknown_checkbox.setChecked(False)
    assert panel.source_filter_combo.currentIndex() == 1
    panel.keep_scene_checkbox.setChecked(True)
    assert panel.source_filter_combo.currentIndex() == 2  # 字幕和画面文字


def test_refresh_models_button_conditional(panel):
    """「刷新模型列表」仅在启用 LLM 且填写 API Key 后显示。"""
    panel.deepseek_api_key_edit.clear()
    panel.llm_group.setChecked(True)  # 展开 LLM 组
    assert not panel.refresh_models_btn.isVisibleTo(panel)
    assert not panel.llm_credentials_container.isVisibleTo(panel)

    panel.deepseek_polish_checkbox.setChecked(True)
    assert panel.llm_credentials_container.isVisibleTo(panel)
    assert not panel.refresh_models_btn.isVisibleTo(panel)  # 尚无 API Key

    panel.deepseek_api_key_edit.setText("sk-test")
    assert panel.refresh_models_btn.isVisibleTo(panel)

    panel.deepseek_polish_checkbox.setChecked(False)
    assert not panel.llm_credentials_container.isVisibleTo(panel)
    assert not panel.refresh_models_btn.isVisibleTo(panel)


def test_color_gate_preview_button_conditional(panel):
    """「预览检测效果」仅在启用颜色门控后显示（需先展开高级组）。"""
    panel.advanced_group.setChecked(True)
    assert not panel.color_gate_preview_btn.isVisibleTo(panel)
    panel.color_gate_checkbox.setChecked(True)
    assert panel.color_gate_preview_btn.isVisibleTo(panel)
    # 未经过「允许预览」确认前仍不可点击。
    assert not panel.color_gate_preview_btn.isEnabled()
    panel.color_gate_checkbox.setChecked(False)
    assert not panel.color_gate_preview_btn.isVisibleTo(panel)


def test_mode_switch_entry_buttons(panel):
    """「完整设置」进入完整视图，「简洁界面」返回，且入口互斥显示。"""
    assert panel.full_settings_btn.isVisibleTo(panel)
    assert not panel.simple_ui_btn.isVisibleTo(panel)

    panel.full_settings_btn.click()
    assert panel.quick_mode is False
    assert not panel.full_settings_btn.isVisibleTo(panel)
    assert panel.simple_ui_btn.isVisibleTo(panel)

    panel.simple_ui_btn.click()
    assert panel.quick_mode is True
    assert panel.full_settings_btn.isVisibleTo(panel)
    assert not panel.simple_ui_btn.isVisibleTo(panel)


def test_pipeline_running_locks_and_restores_panel(panel):
    """运行期间禁用主按钮与输入控件并显示阶段；结束后统一恢复。"""
    assert panel.run_pipeline_btn.text() == "开始识别并导出"
    assert panel.is_pipeline_running() is False

    panel.set_pipeline_running(True, "正在检测字幕区域…")
    assert panel.is_pipeline_running() is True
    assert not panel.run_pipeline_btn.isEnabled()
    assert "正在检测字幕区域" in panel.run_pipeline_btn.text()
    # 输入控件一并禁用。
    assert not panel.ocr_lang_combo.isEnabled()
    assert not panel.adjust_roi_btn.isEnabled()
    assert not panel.redetect_btn.isEnabled()
    assert not panel.chunk_workers_combo.isEnabled()

    # 运行中更新阶段文本。
    panel.set_pipeline_stage("阶段 2/4：OCR 识别中（33%）")
    assert "OCR 识别中" in panel.run_pipeline_btn.text()
    # 未运行时阶段更新应被忽略。
    panel.set_pipeline_running(False)
    panel.set_pipeline_stage("不应生效")
    assert panel.run_pipeline_btn.text() == "开始识别并导出"

    # 恢复后输入控件重新可用，主按钮文案复位。
    assert panel.ocr_lang_combo.isEnabled()
    assert panel.adjust_roi_btn.isEnabled()
    # chunk_workers 在可折叠的高级组内：展开后应可用（组未勾选时被 Qt 禁用）。
    panel.advanced_group.setChecked(True)
    assert panel.chunk_workers_combo.isEnabled()
    assert panel.is_pipeline_running() is False


def test_pipeline_restore_reapplies_conditional_states(panel):
    """恢复时重新套用各条件化启用状态（颜色门控、来源过滤子项）。"""
    panel.advanced_group.setChecked(True)
    panel.color_gate_checkbox.setChecked(True)
    panel.set_quick_mode(False)

    panel.set_pipeline_running(True)
    assert not panel.color_gate_preview_btn.isEnabled()
    panel.set_pipeline_running(False)
    # 颜色门控仍勾选：预览按钮可见且恢复「允许前禁用」状态。
    assert panel.color_gate_preview_btn.isVisibleTo(panel)
    assert not panel.color_gate_preview_btn.isEnabled()

    # 来源过滤关闭时，其子控件恢复为禁用（由主开关决定）。
    assert not panel.keep_overlay_checkbox.isEnabled()
    assert not panel.reanalyze_btn.isEnabled()


def test_window_panel_busy_lock_roundtrip():
    """窗口级集成：_set_panel_busy 锁定/恢复面板；不加载视频时主按钮仍受业务门控。"""
    from main_window.window import SubtitleOCRGUI

    window = SubtitleOCRGUI()
    try:
        panel = window.control_panel_widget
        assert window._panel_busy is False

        window._set_panel_busy(True, "正在检测字幕区域…")
        assert window._panel_busy is True
        assert panel.is_pipeline_running() is True
        assert not panel.run_pipeline_btn.isEnabled()
        assert not window.file_ops_widget.auto_roi_btn.isEnabled()

        window._set_panel_busy(False)
        assert window._panel_busy is False
        assert panel.is_pipeline_running() is False
        # 未加载视频：主按钮仍按原业务规则禁用（视频 + ROI 才可用）。
        assert not panel.run_pipeline_btn.isEnabled()

        # 扫描线程收尾路径统一恢复（模拟线程结束后回调触发）。
        window._panel_busy = True
        panel.set_pipeline_running(True, "测试阶段")
        window._scan_thread = None
        window._on_scan_thread_finished()
        assert window._panel_busy is False
        assert panel.is_pipeline_running() is False
    finally:
        window.close()
        window.deleteLater()


def test_default_options(panel):
    opts = panel.get_pipeline_options()
    # 自动引擎：默认不绑定具体引擎，由引擎管理器在运行时自动选择。
    assert opts["ocr_engine_id"] == "auto"
    assert opts["ocr_model_tier"] == "auto"
    assert opts["in_memory"] is True
    assert opts["chunk_workers"] == 0
    # 来源过滤默认关闭，保留所有识别到的文字。
    assert panel.source_filter_enabled_checkbox.isChecked() is False
    assert opts["source_filter_config"]["enabled"] is False
    # 加载视频后自动检测 ROI 默认开启。
    assert panel.auto_roi_on_load_checkbox.isChecked() is True


def test_ocr_lang_combo_offers_traditional_chinese(panel):
    """识别语言下拉框提供繁体中文（chinese_cht），紧跟简体之后；
    set/get_selected_lang 按 data 往返。"""
    combo = panel.ocr_lang_combo
    datas = [combo.itemData(i) for i in range(combo.count())]
    texts = [combo.itemText(i) for i in range(combo.count())]
    assert "chinese_cht" in datas
    assert datas.index("chinese_cht") == datas.index("ch") + 1
    assert texts[datas.index("chinese_cht")] == "中文繁體"

    panel.set_selected_lang("chinese_cht")
    assert panel.get_selected_lang() == "chinese_cht"
    panel.set_selected_lang("ch")
    assert panel.get_selected_lang() == "ch"

    # 意/西/葡必须是 paddleocr 3.7 认识的 ISO 代码 it/es/pt——旧式名
    # italian/spanish/portuguese 不在任何语言族里，引擎初始化必然失败。
    assert "italian" not in datas and "spanish" not in datas and "portuguese" not in datas
    for code in ("it", "es", "pt"):
        assert code in datas
