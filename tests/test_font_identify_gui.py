# tests/test_font_identify_gui.py
"""字体识别选项组（T3.5）：GUI 控件、选项收集、FontIdentifyConfig 组装、
QSettings 持久化、运行锁、主流水线结束后的更新建议复核流与 i18n 四语言。

与 test_translation_gui.py（T2.5）同一套模式：QApplication 必须在 conftest
的 QCoreApplication 之前创建（QWidget 需要 QApplication）；i18n 断言按
「本轮新增 key 必须四语言齐备」（zh_CN 为基准源文案，其余三语非空译文）。

复核流（pipeline_control._maybe_review_font_suggestions）用 _Host 桩 +
假对话框验证：有建议才弹、用户可取消、采纳项经 db_loader 写覆盖层、
包损坏时降级为警告不崩溃。
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
    QDialog,
    QGroupBox,
    QLineEdit,
    QPushButton,
    QWidget,
)

_app = QApplication.instance() or QApplication(sys.argv[:1])

import pytest  # noqa: E402

from PySide6.QtCore import QSettings, QTranslator  # noqa: E402

from components.control_panel import ControlPanelWidget  # noqa: E402
from font_intel.closure import (  # noqa: E402
    build_update_suggestions_package,
    suggestions_path_for,
    write_update_suggestions_package,
)
from font_intel.fonts_db import FontsDB  # noqa: E402
from main_window.pipeline_control import (  # noqa: E402
    PipelineControlMixin,
    build_font_identify_config,
)

I18N_DIR = os.path.join(PROJECT_ROOT, "i18n")
LANGS = ("zh_CN", "zh_TW", "en", "ja_JP")

# T3.5 本轮新增的用户可见文案（源 = zh_CN 基准文案）。
NEW_CONTROL_PANEL_SOURCES = (
    "字体识别（可选）",
    "Top-N 候选数：",
    "置信度阈值：",
    "字体库目录：",
    "fonts.db 路径：",
    "留空 = 仅系统字体目录",
    "留空 = 默认字体库路径",
    "选择字体库目录",
    "选择 fonts.db 文件",
    "SQLite 字体库 (*.db)",
    "对识别出的字幕行做字体识别（本地计算），输出 Top-N 候选并查询许可类别；"
    "识别完成后可在复核对话框中逐条确认字体库更新建议。",
    "低于阈值的候选只展示、不参与自动替换；0 = 不标记。",
    "每组字幕字块输出的候选字体数量。",
    "额外扫描的字体文件目录（可选）；留空 = 仅扫描系统字体目录。",
    "字体许可/映射查询库；留空使用默认 XDG 数据目录下的 fonts.db。",
)

NEW_MAIN_WINDOW_SOURCES = (
    "已启用字体识别，但字体识别模块不可用（{0}）。请先安装 font_intel 依赖"
    "（requirements-fontintel.txt），或取消勾选「字体识别」后重试。",
    "已启用字体识别，但字体库目录不存在：{0}。请检查路径，或清空后仅使用"
    "系统字体目录。",
    "字体更新建议写入失败：\n{0}",
    "字体更新建议读取失败，本轮复核已跳过：\n{0}",
    "已把 {0} 条字体更新建议写入用户覆盖层。",
)

FONT_IDENTIFY_SETTINGS_KEYS = (
    "font_identify/enabled",
    "font_identify/top_n",
    "font_identify/confidence_threshold",
    "font_identify/font_dir",
    "font_identify/db_path",
)


@pytest.fixture
def panel():
    """Fresh control panel; restores font-identify QSettings keys after test."""
    settings = QSettings()
    saved = {k: settings.value(k) for k in FONT_IDENTIFY_SETTINGS_KEYS}
    w = ControlPanelWidget()
    yield w
    w.shutdown_background_threads(timeout_ms=100)
    w.deleteLater()
    for key, value in saved.items():
        if value is None:
            settings.remove(key)
        else:
            settings.setValue(key, value)


# ── 选项组存在性与默认状态 ───────────────────────────────────────


def test_font_identify_group_present_and_collapsed_by_default(panel):
    group = panel.font_identify_group
    assert isinstance(group, QGroupBox)
    assert group.isCheckable()
    # 默认收起 = 字体识别关闭（零开销直通）。
    assert group.isChecked() is False

    assert isinstance(panel.font_identify_top_n_edit, QLineEdit)
    assert panel.font_identify_top_n_edit.text() == "5"
    assert isinstance(panel.font_identify_threshold_edit, QLineEdit)
    assert panel.font_identify_threshold_edit.text() == "0"
    assert isinstance(panel.font_identify_font_dir_edit, QLineEdit)
    assert isinstance(panel.font_identify_font_dir_btn, QPushButton)
    assert isinstance(panel.font_identify_db_edit, QLineEdit)
    assert isinstance(panel.font_identify_db_btn, QPushButton)


# ── 关闭时零行为变化 ─────────────────────────────────────────────


def test_disabled_by_default_options_and_none_config(panel):
    opts = panel.get_pipeline_options()
    assert opts["font_identify_enabled"] is False

    cfg, err = build_font_identify_config(opts)
    assert cfg is None
    assert err == ""


def test_unchecked_group_yields_none_config_even_with_values(panel):
    panel.font_identify_group.setChecked(False)
    panel.font_identify_font_dir_edit.setText("/tmp/fonts")
    panel.font_identify_db_edit.setText("/tmp/fonts.db")
    opts = panel.get_pipeline_options()
    assert opts["font_identify_enabled"] is False
    cfg, err = build_font_identify_config(opts)
    assert cfg is None and err == ""


# ── 开启后组装 FontIdentifyConfig ────────────────────────────────


def test_enabled_assembles_font_identify_config(panel, tmp_path):
    font_dir = tmp_path / "fonts"
    font_dir.mkdir()
    db_file = tmp_path / "fonts.db"

    panel.font_identify_group.setChecked(True)
    panel.font_identify_top_n_edit.setText("8")
    panel.font_identify_threshold_edit.setText("0.5")
    panel.font_identify_font_dir_edit.setText(str(font_dir))
    panel.font_identify_db_edit.setText(str(db_file))

    opts = panel.get_pipeline_options()
    assert opts["font_identify_enabled"] is True
    assert opts["font_identify_top_n"] == 8
    assert opts["font_identify_confidence_threshold"] == pytest.approx(0.5)
    assert opts["font_identify_font_dir"] == str(font_dir)
    assert opts["font_identify_db_path"] == str(db_file)

    cfg, err = build_font_identify_config(opts)
    assert err == ""
    from font_intel.identify_stage import FontIdentifyConfig

    assert isinstance(cfg, FontIdentifyConfig)
    assert cfg.enabled is True
    assert cfg.top_n == 8
    assert cfg.confidence_threshold == pytest.approx(0.5)
    assert cfg.extra_font_dirs == [str(font_dir)]
    assert cfg.db_path == str(db_file)


def test_db_path_empty_maps_to_none_default_db(panel):
    panel.font_identify_group.setChecked(True)
    opts = panel.get_pipeline_options()
    cfg, err = build_font_identify_config(opts)
    assert err == ""
    # db_path=None → 运行时落 default_db_path()（XDG 数据目录）。
    assert cfg.db_path is None
    assert cfg.extra_font_dirs == []


def test_numeric_fields_fall_back_on_garbage(panel):
    panel.font_identify_group.setChecked(True)
    panel.font_identify_top_n_edit.setText("abc")
    panel.font_identify_threshold_edit.setText("xyz")
    opts = panel.get_pipeline_options()
    assert opts["font_identify_top_n"] == 5
    assert opts["font_identify_confidence_threshold"] == 0.0

    panel.font_identify_top_n_edit.setText("-3")
    panel.font_identify_threshold_edit.setText("-0.5")
    opts = panel.get_pipeline_options()
    assert opts["font_identify_top_n"] == 5
    assert opts["font_identify_confidence_threshold"] == 0.0


def test_missing_font_dir_reports_error(panel, tmp_path):
    panel.font_identify_group.setChecked(True)
    panel.font_identify_font_dir_edit.setText(str(tmp_path / "nope"))
    cfg, err = build_font_identify_config(panel.get_pipeline_options())
    assert cfg is None
    assert "字体库目录不存在" in err


def test_missing_font_intel_module_reports_error(monkeypatch):
    """font_intel 缺失时给出可读错误，不抛异常击穿调用方。"""
    import builtins

    real_import = builtins.__import__

    def _fail_font_intel(name, *a, **k):
        if name.startswith("font_intel"):
            raise ImportError("No module named 'font_intel'")
        return real_import(name, *a, **k)

    monkeypatch.setattr(builtins, "__import__", _fail_font_intel)
    cfg, err = build_font_identify_config({"font_identify_enabled": True})
    assert cfg is None
    assert "font_intel" in err


# ── QSettings 持久化 ─────────────────────────────────────────────


def test_settings_persistence_roundtrip(panel, tmp_path):
    font_dir = tmp_path / "fonts"
    font_dir.mkdir()
    panel.font_identify_group.setChecked(True)
    panel.font_identify_top_n_edit.setText("3")
    panel.font_identify_threshold_edit.setText("0.4")
    panel.font_identify_font_dir_edit.setText(str(font_dir))
    panel.font_identify_db_edit.setText(str(tmp_path / "f.db"))

    second = ControlPanelWidget()
    try:
        assert second.font_identify_group.isChecked() is True
        assert second.font_identify_top_n_edit.text() == "3"
        assert second.font_identify_threshold_edit.text() == "0.4"
        assert second.font_identify_font_dir_edit.text() == str(font_dir)
        assert second.font_identify_db_edit.text() == str(tmp_path / "f.db")
        cfg, err = build_font_identify_config(second.get_pipeline_options())
        assert err == ""
        assert cfg.top_n == 3
        assert cfg.extra_font_dirs == [str(font_dir)]
    finally:
        second.shutdown_background_threads(timeout_ms=100)
        second.deleteLater()


# ── 运行锁 ───────────────────────────────────────────────────────


def test_font_identify_widgets_lock_while_pipeline_running(panel):
    panel.font_identify_group.setChecked(True)
    panel.set_pipeline_running(True, "正在识别…")
    assert not panel.font_identify_top_n_edit.isEnabled()
    assert not panel.font_identify_threshold_edit.isEnabled()
    assert not panel.font_identify_font_dir_btn.isEnabled()
    assert not panel.font_identify_db_btn.isEnabled()

    panel.set_pipeline_running(False)
    assert panel.font_identify_top_n_edit.isEnabled()
    assert panel.font_identify_threshold_edit.isEnabled()
    assert panel.font_identify_font_dir_btn.isEnabled()
    assert panel.font_identify_db_btn.isEnabled()


# ── 主流水线结束后的更新建议复核流 ───────────────────────────────


class _MessageBoxStub:
    """记录弹窗调用的 QMessageBox 替身（复核流不打断测试）。"""

    def __init__(self):
        self.calls = []

    def warning(self, *a, **k):
        self.calls.append(("warning", a))

    def information(self, *a, **k):
        self.calls.append(("information", a))


class _FakeReviewDialog:
    """记录实例化参数的 FontUpdateReviewDialog 替身。"""

    instances = []
    result = {"adopted": [], "rejected": []}
    code = QDialog.Accepted

    def __init__(self, items, parent=None):
        self.items = items
        self.parent = parent
        type(self).instances.append(self)

    def exec(self):
        return type(self).code

    def get_result(self):
        return dict(type(self).result)


class _Host(QWidget, PipelineControlMixin):
    """只提供复核流触碰的属性（同 test_pipeline_progress_dialog_dismiss_race）。"""

    def __init__(self, db_path=""):
        super().__init__()
        self.progress_dialog = None
        self._pipeline_llm_active = False
        self._pipeline_font_db_path = db_path


def _suggestion_items():
    return [
        {
            "type": "new_font_record",
            "font_name": "TestFont CI",
            "candidate": None,
            "reason": "识别字体未收录本地字体库",
            "evidence": "font_identify Top-1 score=0.9",
            "method": "llm_inferred",
            "confidence": 0.9,
            "suggestion": {"kind": "font", "canonical_name": "TestFont CI",
                           "license_category": "unknown"},
        },
        {
            "type": "new_mapping",
            "font_name": "A1明朝",
            "candidate": "华文宋体",
            "reason": "同风格类别兜底命中",
            "evidence": "chain level 3 basis=category_match",
            "method": "llm_inferred",
            "confidence": 0.6,
            "suggestion": {"kind": "mapping", "jp_name": "A1明朝",
                           "cn_name": "华文宋体"},
        },
    ]


def _write_suggestions(ass_path, items):
    write_update_suggestions_package(
        build_update_suggestions_package(items), suggestions_path_for(ass_path))
    return str(suggestions_path_for(ass_path))


@pytest.fixture
def review_env(monkeypatch, tmp_path):
    """桩掉对话框/弹窗；返回 (host, db_path, ass_path, boxes, fakes)。"""
    import components.font_update_review_dialog as dialog_mod
    import main_window.pipeline_control as pc

    boxes = _MessageBoxStub()
    fakes = _FakeReviewDialog
    fakes.instances = []
    fakes.result = {"adopted": [], "rejected": []}
    fakes.code = QDialog.Accepted
    monkeypatch.setattr(dialog_mod, "FontUpdateReviewDialog", fakes)
    monkeypatch.setattr(pc.QMessageBox, "warning", boxes.warning)
    monkeypatch.setattr(pc.QMessageBox, "information", boxes.information)

    db_path = str(tmp_path / "fonts.db")
    ass_path = str(tmp_path / "video.ass")
    host = _Host(db_path=db_path)
    yield host, db_path, ass_path, boxes, fakes
    host.deleteLater()


def test_review_skipped_when_no_suggestions_file(review_env):
    host, _db, ass_path, _boxes, fakes = review_env
    host._maybe_review_font_suggestions(ass_path)
    assert fakes.instances == []


def test_review_empty_items_no_dialog(review_env):
    host, _db, ass_path, _boxes, fakes = review_env
    _write_suggestions(ass_path, [])
    host._maybe_review_font_suggestions(ass_path)
    assert fakes.instances == []


def test_review_dialog_shown_and_adopted_written(review_env):
    host, db_path, ass_path, boxes, fakes = review_env
    items = _suggestion_items()
    _write_suggestions(ass_path, items)
    # 用户只采纳第一条（字体记录）；第二条（映射）不勾 = 否决。
    fakes.result = {"adopted": [items[0]], "rejected": [items[1]]}

    host._maybe_review_font_suggestions(ass_path)

    assert len(fakes.instances) == 1
    assert fakes.instances[0].items == items
    db = FontsDB(db_path)
    try:
        rec = db.lookup_font("TestFont CI")
        assert rec is not None
        assert rec.get("license_category") == "unknown"
        # 未采纳的映射不写库。
        assert db.lookup_jp_cn("A1明朝") in (None, [])
    finally:
        db.close()
    assert ("information",) in [(c[0],) for c in boxes.calls]


def test_review_cancel_writes_nothing(review_env):
    host, db_path, ass_path, _boxes, fakes = review_env
    items = _suggestion_items()
    _write_suggestions(ass_path, items)
    fakes.code = QDialog.Rejected  # 用户取消

    host._maybe_review_font_suggestions(ass_path)

    assert len(fakes.instances) == 1
    db = FontsDB(db_path)
    try:
        assert db.lookup_font("TestFont CI") is None
    finally:
        db.close()


def test_review_adopt_all_writes_mapping(review_env):
    host, db_path, ass_path, _boxes, fakes = review_env
    items = _suggestion_items()
    _write_suggestions(ass_path, items)
    fakes.result = {"adopted": list(items), "rejected": []}

    host._maybe_review_font_suggestions(ass_path)

    db = FontsDB(db_path)
    try:
        assert db.lookup_font("TestFont CI") is not None
        rows = db.lookup_jp_cn("A1明朝")
        assert rows and any(
            getattr(r, "get", lambda k: None)("cn_name") == "华文宋体"
            or (isinstance(r, dict) and r.get("cn_name") == "华文宋体")
            for r in (rows if isinstance(rows, list) else [rows])
        )
    finally:
        db.close()


def test_review_broken_package_degrades_to_warning(review_env):
    host, _db, ass_path, boxes, fakes = review_env
    # schema 不符的文件（防错导他类文件）→ 警告降级，不弹对话框、不崩溃。
    path = suggestions_path_for(ass_path)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write('{"schema": "something/else", "items": []}')
    host._maybe_review_font_suggestions(ass_path)
    assert fakes.instances == []
    assert any(c[0] == "warning" for c in boxes.calls)


def test_review_write_failure_degrades_to_warning(review_env, monkeypatch):
    host, db_path, ass_path, boxes, fakes = review_env
    items = _suggestion_items()
    _write_suggestions(ass_path, items)
    fakes.result = {"adopted": [items[0]], "rejected": []}

    import font_intel.etl.db_loader as db_loader_mod

    def _boom(*a, **k):
        raise RuntimeError("disk full")

    monkeypatch.setattr(db_loader_mod, "apply_font_update_decisions", _boom)
    host._maybe_review_font_suggestions(ass_path)
    assert any(c[0] == "warning" for c in boxes.calls)


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


def test_qm_translates_new_strings():
    """重编译后的 .qm 实际生效（同 test_translation_gui.py 的内容断言风格）。"""
    expected = {
        "en": ("字体识别（可选）", "Font recognition (optional)"),
        "zh_TW": ("字体识别（可选）", "字型辨識（可選）"),
        "ja_JP": ("字体识别（可选）", "フォント識別（任意）"),
    }
    for lang, (source, translated) in expected.items():
        translator = QTranslator()
        assert translator.load(os.path.join(I18N_DIR, f"app_{lang}.qm"))
        assert translator.translate("ControlPanelWidget", source) == translated

    translator = QTranslator()
    assert translator.load(os.path.join(I18N_DIR, "app_en.qm"))
    assert translator.translate(
        "FontUpdateReviewDialog", "应用"
    ) == "Apply"
