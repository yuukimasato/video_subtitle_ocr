# tests/test_translation_gui.py
"""AI 翻译选项组（T2.5）：GUI 控件、选项收集、TranslationConfig 组装、
QSettings 持久化与 i18n 四语言词表完整性。

与 test_control_panel_quick_mode.py 相同：QApplication 必须在 conftest 的
QCoreApplication 之前创建（QWidget 需要 QApplication）。

i18n 断言同 test_i18n_language.py 的 .qm 加载/内容断言风格；词表完整性
按「本轮新增 key 必须四语言齐备」断言（zh_CN 为基准源文案，其余三语
必须给出非空译文）。
"""

from __future__ import annotations

import os
import sys
import xml.etree.ElementTree as ET

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from PySide6.QtWidgets import (  # noqa: E402
    QApplication,
    QComboBox,
    QLineEdit,
    QPushButton,
    QGroupBox,
)

_app = QApplication.instance() or QApplication(sys.argv[:1])

import pytest  # noqa: E402

from PySide6.QtCore import QSettings, QTranslator  # noqa: E402

from components.control_panel import ControlPanelWidget  # noqa: E402
from font_intel.translation import (  # noqa: E402
    DEFAULT_SAKURA_MODEL,
    TranslationConfig,
)
from main_window.pipeline_control import build_translation_config  # noqa: E402

I18N_DIR = os.path.join(PROJECT_ROOT, "i18n")
LANGS = ("zh_CN", "zh_TW", "en", "ja_JP")

# T2.5 本轮新增的用户可见文案（源 = zh_CN 基准文案）。
NEW_CONTROL_PANEL_SOURCES = (
    "AI 翻译（可选）",
    "目标语言：",
    "翻译提供方：",
    "云端 API",
    "本地 Sakura",
    "VLM 兜底",
    "API Base URL（云端 / Sakura 共用）",
    "模型名（如 deepseek-chat / sakura-14b）",
    "术语表 JSON：",
    "选择术语表 JSON 文件",
    "术语表 JSON (*.json)",
    "上下文行数：",
    "最大行长：",
    "把识别出的字幕行交给大模型翻译成目标语言（在润色之后、写出之前执行）。"
    "云端提供方复用「大模型润色」区已保存的 API Key；某一级未配置或失败时"
    "自动降级到下一级，全部失败保留原文。",
    "云端 API：OpenAI 兼容端点（默认 DeepSeek）；本地 Sakura：本地部署的 "
    "Sakura 日中翻译模型（OpenAI 兼容 /v1 端点）；VLM 兜底：复用 "
    "VLM_REFINE_* 环境变量，零配置。",
    "可选。JSON 文件（{\"术语\": \"译名\"}），译文中术语强制一致；"
    "文件缺失或格式错误时自动忽略。",
)

NEW_MAIN_WINDOW_SOURCES = (
    "已启用 AI 翻译（云端 API），但未填写 API Key。"
    "请在大模型润色区填写 API Key，或设置环境变量 DEEPSEEK_API_KEY。",
    "已启用 AI 翻译（本地 Sakura），但未填写 Base URL。"
    "请填写本地 Sakura 服务器的 OpenAI 兼容端点地址（如 http://127.0.0.1:8080/v1）。",
    "已启用 AI 翻译，但翻译模块不可用（{0}）。请先安装 font_intel 依赖"
    "（requirements-fontintel.txt），或取消勾选「AI 翻译」后重试。",
    "[LLM] 已启用 AI 翻译 —— 翻译进度会显示在下方。",
)

TRANSLATION_SETTINGS_KEYS = (
    "translation/enabled",
    "translation/target_language",
    "translation/provider",
    "translation/base_url_cloud",
    "translation/model_cloud",
    "translation/base_url_sakura",
    "translation/model_sakura",
    "translation/glossary_path",
    "translation/context_lines",
    "translation/max_line_chars",
)


@pytest.fixture
def panel():
    """Fresh control panel; restores translation-related QSettings keys."""
    settings = QSettings()
    saved = {k: settings.value(k) for k in TRANSLATION_SETTINGS_KEYS}
    saved_auto_roi = settings.value("pipeline/auto_roi_on_load")
    settings.setValue("pipeline/auto_roi_on_load", True)
    w = ControlPanelWidget()
    yield w
    w.shutdown_background_threads(timeout_ms=100)
    w.deleteLater()
    for key, value in saved.items():
        if value is None:
            settings.remove(key)
        else:
            settings.setValue(key, value)
    if saved_auto_roi is None:
        settings.remove("pipeline/auto_roi_on_load")
    else:
        settings.setValue("pipeline/auto_roi_on_load", saved_auto_roi)


def _select_by_data(combo: QComboBox, data) -> bool:
    idx = combo.findData(data)
    if idx < 0:
        return False
    combo.setCurrentIndex(idx)
    return True


# ── 选项组存在性与默认状态 ───────────────────────────────────────


def test_translation_group_present_and_collapsed_by_default(panel):
    group = panel.translation_group
    assert isinstance(group, QGroupBox)
    assert group.isCheckable()
    # 默认收起 = 翻译关闭。
    assert group.isChecked() is False

    assert isinstance(panel.translation_target_lang_combo, QComboBox)
    assert isinstance(panel.translation_provider_combo, QComboBox)
    assert isinstance(panel.translation_base_url_edit, QLineEdit)
    assert isinstance(panel.translation_model_edit, QLineEdit)
    assert isinstance(panel.translation_glossary_edit, QLineEdit)
    assert isinstance(panel.translation_glossary_btn, QPushButton)
    assert isinstance(panel.translation_context_edit, QLineEdit)
    assert isinstance(panel.translation_max_chars_edit, QLineEdit)


def test_provider_combo_and_target_language_items(panel):
    provider = panel.translation_provider_combo
    datas = [provider.itemData(i) for i in range(provider.count())]
    assert datas == ["cloud", "sakura", "vlm"]
    assert provider.currentData() == "cloud"

    target = panel.translation_target_lang_combo
    t_datas = [target.itemData(i) for i in range(target.count())]
    # 简体中文默认；至少提供繁体/英/日常见项。
    assert t_datas[0] == "简体中文"
    for lang in ("繁體中文", "英语", "日语"):
        assert lang in t_datas


# ── 关闭时零行为变化 ─────────────────────────────────────────────


def test_disabled_by_default_options_and_none_config(panel):
    opts = panel.get_pipeline_options()
    assert opts["translation_enabled"] is False
    assert opts["translation_provider"] == "cloud"
    assert opts["translation_target_language"] == "简体中文"

    cfg, err = build_translation_config(opts)
    assert cfg is None
    assert err == ""


def test_unchecked_group_yields_none_config_even_with_values(panel):
    panel.translation_group.setChecked(False)
    panel.translation_base_url_edit.setText("https://example.com")
    panel.translation_model_edit.setText("some-model")
    opts = panel.get_pipeline_options()
    assert opts["translation_enabled"] is False
    cfg, err = build_translation_config(opts)
    assert cfg is None and err == ""


# ── 开启后按提供方组装 TranslationConfig ─────────────────────────


def test_cloud_provider_assembles_config(panel):
    panel.translation_group.setChecked(True)
    panel.deepseek_api_key_edit.setText("sk-test-123")
    panel.translation_base_url_edit.setText("https://api.example.com/v1")
    panel.translation_model_edit.setText("my-model")
    panel.translation_glossary_edit.setText("/tmp/glossary.json")
    panel.translation_context_edit.setText("3")
    panel.translation_max_chars_edit.setText("40")

    opts = panel.get_pipeline_options()
    assert opts["translation_enabled"] is True
    assert opts["translation_provider"] == "cloud"
    assert opts["translation_glossary_path"] == "/tmp/glossary.json"
    assert opts["translation_context_lines"] == 3
    assert opts["translation_max_line_chars"] == 40

    cfg, err = build_translation_config(opts)
    assert err == ""
    assert isinstance(cfg, TranslationConfig)
    assert cfg.cloud_api_key == "sk-test-123"
    assert cfg.cloud_base_url == "https://api.example.com/v1"
    assert cfg.cloud_model == "my-model"
    assert cfg.sakura_base_url == ""
    assert cfg.target_language == "简体中文"
    assert cfg.glossary_path == "/tmp/glossary.json"
    assert cfg.context_window_lines == 3
    assert cfg.max_line_chars == 40


def test_cloud_defaults_fill_module_constants(panel):
    panel.translation_group.setChecked(True)
    panel.deepseek_api_key_edit.setText("sk-test-123")
    # 用户未改默认值时 base/model 即翻译模块的 DeepSeek 常量。
    opts = panel.get_pipeline_options()
    cfg, err = build_translation_config(opts)
    assert err == ""
    from font_intel.translation import DEFAULT_TARGET_LANGUAGE
    from core.subtitle_llm_polish import DEFAULT_DEEPSEEK_BASE, DEFAULT_DEEPSEEK_MODEL

    assert cfg.cloud_base_url == DEFAULT_DEEPSEEK_BASE
    assert cfg.cloud_model == DEFAULT_DEEPSEEK_MODEL
    assert cfg.target_language == DEFAULT_TARGET_LANGUAGE


def test_cloud_without_key_reports_error(panel):
    panel.translation_group.setChecked(True)
    panel.deepseek_api_key_edit.clear()
    import os as _os

    env_saved = _os.environ.pop("DEEPSEEK_API_KEY", None)
    try:
        opts = panel.get_pipeline_options()
        cfg, err = build_translation_config(opts)
        assert cfg is None
        assert "API Key" in err
    finally:
        if env_saved is not None:
            _os.environ["DEEPSEEK_API_KEY"] = env_saved


def test_sakura_provider_assembles_config(panel):
    panel.translation_group.setChecked(True)
    assert _select_by_data(panel.translation_provider_combo, "sakura")
    # Sakura 通常无鉴权：base URL 必填，模型留空用模块默认。
    panel.translation_base_url_edit.setText("http://127.0.0.1:8080/v1")
    panel.translation_model_edit.clear()

    opts = panel.get_pipeline_options()
    assert opts["translation_provider"] == "sakura"
    cfg, err = build_translation_config(opts)
    assert err == ""
    assert cfg.sakura_base_url == "http://127.0.0.1:8080/v1"
    assert cfg.sakura_model == DEFAULT_SAKURA_MODEL
    assert cfg.cloud_api_key == ""


def test_sakura_without_base_url_reports_error(panel):
    panel.translation_group.setChecked(True)
    _select_by_data(panel.translation_provider_combo, "sakura")
    panel.translation_base_url_edit.clear()
    opts = panel.get_pipeline_options()
    cfg, err = build_translation_config(opts)
    assert cfg is None
    assert "Base URL" in err


def test_vlm_provider_needs_no_configuration(panel):
    panel.translation_group.setChecked(True)
    _select_by_data(panel.translation_provider_combo, "vlm")
    panel.translation_base_url_edit.clear()
    panel.translation_model_edit.clear()

    cfg, err = build_translation_config(panel.get_pipeline_options())
    assert err == ""
    assert isinstance(cfg, TranslationConfig)
    assert cfg.cloud_api_key == ""
    assert cfg.sakura_base_url == ""


def test_target_language_selection_flows_into_config(panel):
    panel.translation_group.setChecked(True)
    panel.deepseek_api_key_edit.setText("sk-test")
    assert _select_by_data(panel.translation_target_lang_combo, "日语")
    opts = panel.get_pipeline_options()
    assert opts["translation_target_language"] == "日语"
    cfg, _ = build_translation_config(opts)
    assert cfg.target_language == "日语"


def test_numeric_fields_fall_back_on_garbage(panel):
    panel.translation_group.setChecked(True)
    panel.translation_context_edit.setText("abc")
    panel.translation_max_chars_edit.setText("-5")
    opts = panel.get_pipeline_options()
    assert opts["translation_context_lines"] == 2
    assert opts["translation_max_line_chars"] == 42
    # 0 = 关闭行长约束（CLI --translate-max-chars 同语义）。
    panel.translation_max_chars_edit.setText("0")
    assert panel.get_pipeline_options()["translation_max_line_chars"] == 0


def test_missing_translation_module_reports_error(monkeypatch):
    """font_intel 缺失时给出可读错误，不抛异常击穿调用方。"""
    import builtins

    real_import = builtins.__import__

    def _fail_font_intel(name, *a, **k):
        if name.startswith("font_intel"):
            raise ImportError("No module named 'font_intel'")
        return real_import(name, *a, **k)

    monkeypatch.setattr(builtins, "__import__", _fail_font_intel)
    cfg, err = build_translation_config({"translation_enabled": True})
    assert cfg is None
    assert "font_intel" in err


# ── 提供方切换：共用一行输入按提供方记忆/恢复 ────────────────────


def test_provider_switch_swaps_shared_fields(panel):
    panel.translation_group.setChecked(True)
    panel.translation_base_url_edit.setText("https://api.example.com/v1")
    panel.translation_model_edit.setText("my-model")

    _select_by_data(panel.translation_provider_combo, "sakura")
    # 切到 sakura：共用行换成本地端（空 base，等待用户填写）。
    assert panel.translation_base_url_edit.text() == ""
    panel.translation_base_url_edit.setText("http://127.0.0.1:8080/v1")
    panel.translation_model_edit.setText("sakura-7b")

    _select_by_data(panel.translation_provider_combo, "cloud")
    # 切回 cloud：云端值恢复，不丢用户已填内容。
    assert panel.translation_base_url_edit.text() == "https://api.example.com/v1"
    assert panel.translation_model_edit.text() == "my-model"

    _select_by_data(panel.translation_provider_combo, "sakura")
    assert panel.translation_base_url_edit.text() == "http://127.0.0.1:8080/v1"
    assert panel.translation_model_edit.text() == "sakura-7b"


# ── 术语表浏览 ───────────────────────────────────────────────────


def test_glossary_browse_fills_path(panel, monkeypatch):
    from components import control_panel as cp

    # 组未勾选时子控件整体禁用，click() 是 no-op；先启用组。
    panel.translation_group.setChecked(True)
    monkeypatch.setattr(
        cp.QFileDialog,
        "getOpenFileName",
        staticmethod(lambda *a, **k: ("/tmp/glossary.json", "JSON (*.json)")),
    )
    panel.translation_glossary_btn.click()
    assert panel.translation_glossary_edit.text() == "/tmp/glossary.json"


def test_glossary_browse_cancel_keeps_path(panel, monkeypatch):
    from components import control_panel as cp

    panel.translation_group.setChecked(True)
    panel.translation_glossary_edit.setText("/tmp/keep.json")
    monkeypatch.setattr(
        cp.QFileDialog, "getOpenFileName", staticmethod(lambda *a, **k: ("", ""))
    )
    panel.translation_glossary_btn.click()
    assert panel.translation_glossary_edit.text() == "/tmp/keep.json"


# ── QSettings 持久化 ─────────────────────────────────────────────


def test_settings_persistence_roundtrip(panel):
    panel.translation_group.setChecked(True)
    _select_by_data(panel.translation_target_lang_combo, "英语")
    _select_by_data(panel.translation_provider_combo, "sakura")
    panel.translation_base_url_edit.setText("http://127.0.0.1:8080/v1")
    panel.translation_model_edit.setText("sakura-7b")
    panel.translation_glossary_edit.setText("/tmp/g.json")
    panel.translation_context_edit.setText("4")
    panel.translation_max_chars_edit.setText("30")

    second = ControlPanelWidget()
    try:
        assert second.translation_group.isChecked() is True
        assert second.translation_target_lang_combo.currentData() == "英语"
        assert second.translation_provider_combo.currentData() == "sakura"
        assert second.translation_base_url_edit.text() == "http://127.0.0.1:8080/v1"
        assert second.translation_model_edit.text() == "sakura-7b"
        assert second.translation_glossary_edit.text() == "/tmp/g.json"
        assert second.translation_context_edit.text() == "4"
        assert second.translation_max_chars_edit.text() == "30"
        # 恢复后的选项能原样组装。
        cfg, err = build_translation_config(second.get_pipeline_options())
        assert err == ""
        assert cfg.sakura_base_url == "http://127.0.0.1:8080/v1"
        assert cfg.sakura_model == "sakura-7b"
        assert cfg.target_language == "英语"
    finally:
        second.shutdown_background_threads(timeout_ms=100)
        second.deleteLater()


# ── 运行锁与面板联动 ─────────────────────────────────────────────


def test_translation_widgets_lock_while_pipeline_running(panel):
    panel.translation_group.setChecked(True)
    panel.set_pipeline_running(True, "正在识别…")
    assert not panel.translation_provider_combo.isEnabled()
    assert not panel.translation_base_url_edit.isEnabled()
    assert not panel.translation_glossary_btn.isEnabled()

    panel.set_pipeline_running(False)
    assert panel.translation_provider_combo.isEnabled()
    assert panel.translation_base_url_edit.isEnabled()
    assert panel.translation_glossary_btn.isEnabled()


def test_vlm_provider_disables_endpoint_fields(panel):
    panel.translation_group.setChecked(True)
    assert panel.translation_base_url_edit.isEnabled()
    _select_by_data(panel.translation_provider_combo, "vlm")
    # VLM 走环境变量零配置：共用行禁用以免误导。
    assert not panel.translation_base_url_edit.isEnabled()
    assert not panel.translation_model_edit.isEnabled()
    _select_by_data(panel.translation_provider_combo, "cloud")
    assert panel.translation_base_url_edit.isEnabled()


# ── i18n 四语言词表完整性 ────────────────────────────────────────


def _load_ts_contexts(lang: str) -> dict:
    """{context_name: {source: translation_text_or_None}}。"""
    path = os.path.join(I18N_DIR, f"app_{lang}.ts")
    root = ET.parse(path).getroot()
    ctx_map: dict = {}
    for ctx in root.findall("context"):
        name = ctx.findtext("name", "")
        messages: dict = {}
        for msg in ctx.findall("message"):
            source = msg.findtext("source", "")
            tr = msg.find("translation")
            text = tr.text if tr is not None else None
            if tr is not None and tr.get("type") == "unfinished":
                text = None
            messages[source] = text
        ctx_map.setdefault(name, {}).update(messages)
    return ctx_map


def test_new_sources_present_in_all_four_ts():
    for lang in LANGS:
        ctx_map = _load_ts_contexts(lang)
        cp_sources = ctx_map.get("ControlPanelWidget", {})
        gui_sources = ctx_map.get("SubtitleOCRGUI", {})
        for src in NEW_CONTROL_PANEL_SOURCES:
            assert src in cp_sources, f"app_{lang}.ts 缺少 ControlPanelWidget 源: {src}"
        for src in NEW_MAIN_WINDOW_SOURCES:
            assert src in gui_sources, f"app_{lang}.ts 缺少 SubtitleOCRGUI 源: {src}"


def test_new_sources_translated_in_three_languages():
    # zh_CN 是基准源文案（条目可保持 unfinished=回退源文案）；
    # zh_TW / en / ja_JP 必须给出非空译文。
    for lang in ("zh_TW", "en", "ja_JP"):
        ctx_map = _load_ts_contexts(lang)
        merged = {}
        merged.update(ctx_map.get("ControlPanelWidget", {}))
        merged.update(ctx_map.get("SubtitleOCRGUI", {}))
        for src in (*NEW_CONTROL_PANEL_SOURCES, *NEW_MAIN_WINDOW_SOURCES):
            tr = merged.get(src)
            assert tr, f"app_{lang}.ts 缺少译文: {src}"


def test_font_map_review_dialog_context_translated():
    """T1.5 复核对话框的全部文案补入四语言词表（T2.5 补欠账）。"""
    baseline = _load_ts_contexts("zh_CN").get("FontMapReviewDialog", {})
    assert baseline, "app_zh_CN.ts 缺少 FontMapReviewDialog 上下文"
    assert "字体映射复核" in baseline
    for lang in ("zh_TW", "en", "ja_JP"):
        ctx = _load_ts_contexts(lang).get("FontMapReviewDialog", {})
        for src in baseline:
            assert src in ctx, f"app_{lang}.ts FontMapReviewDialog 缺少源: {src}"
            assert ctx[src], f"app_{lang}.ts FontMapReviewDialog 缺少译文: {src}"


def test_qm_translates_new_strings():
    """重编译后的 .qm 实际生效（同 test_i18n_language.py 的内容断言风格）。"""
    expected = {
        "en": ("AI 翻译（可选）", "AI translation (optional)"),
        "zh_TW": ("AI 翻译（可选）", "AI 翻譯（可選）"),
        "ja_JP": ("AI 翻译（可选）", "AI 翻訳（任意）"),
    }
    for lang, (source, translated) in expected.items():
        translator = QTranslator()
        assert translator.load(os.path.join(I18N_DIR, f"app_{lang}.qm"))
        assert translator.translate("ControlPanelWidget", source) == translated

    translator = QTranslator()
    assert translator.load(os.path.join(I18N_DIR, "app_en.qm"))
    assert translator.translate("FontMapReviewDialog", "字体映射复核") == (
        "Font mapping review"
    )
