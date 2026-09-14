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
    QLineEdit,
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
        texts.add(child.text())
    for child in widget.findChildren(QLabel):
        texts.add(child.text())
    for combo in widget.findChildren(QComboBox):
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
