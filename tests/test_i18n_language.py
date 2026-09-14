# tests/test_i18n_language.py
"""界面语言支持测试：locale 解析、翻译文件加载、语言选择器持久化。

与 test_pipeline_progress_dialog_dismiss_race.py 相同：QApplication 必须在
conftest 的 QCoreApplication 之前创建（QWidget 需要 QApplication）。
"""

from __future__ import annotations

import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QMessageBox

_app = QApplication.instance() or QApplication(sys.argv[:1])

import pytest  # noqa: E402

from PySide6.QtCore import QCoreApplication, QSettings, QTranslator  # noqa: E402

from i18n.translator import Translator, resolve_locale  # noqa: E402

I18N_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "i18n")


@pytest.mark.parametrize(
    ("locale", "expected"),
    [
        ("zh_CN", "zh_CN"),
        ("zh_SG", "zh_CN"),
        ("zh", "zh_CN"),
        ("zh_TW", "zh_TW"),
        ("zh_HK", "zh_TW"),
        ("zh_MO", "zh_TW"),
        ("ja_JP", "ja_JP"),
        ("ja", "ja_JP"),
        ("en", "en"),
        ("en_US", "en"),
        ("en_GB", "en"),
        ("fr_FR", ""),
        ("", ""),
    ],
)
def test_resolve_locale(locale, expected):
    assert resolve_locale(locale) == expected


@pytest.mark.parametrize("lang", ["zh_CN", "zh_TW", "en", "ja_JP"])
def test_translated_qm_files_exist_and_load(lang):
    translator = QTranslator()
    assert translator.load(os.path.join(I18N_DIR, f"app_{lang}.qm"))


def test_english_translation_content():
    translator = QTranslator()
    assert translator.load(os.path.join(I18N_DIR, "app_en.qm"))
    assert translator.translate("FileOperationsWidget", "加载视频") == "Load video"
    assert translator.translate("ControlPanelWidget", "开始识别并导出") == "Start recognition & export"


def test_zh_tw_translation_content():
    translator = QTranslator()
    assert translator.load(os.path.join(I18N_DIR, "app_zh_TW.qm"))
    assert translator.translate("FileOperationsWidget", "加载视频") == "載入影片"
    assert translator.translate("ControlPanelWidget", "开始识别并导出") == "開始辨識並匯出"


def test_load_language_switches_and_restores():
    tr = Translator()
    def _restore():
        # 卸载翻译器，避免影响后续测试（无翻译器时 translate 返回源文案）。
        assert tr.load_language("xx_XX") == ""
        assert tr.current_language == ""
        assert QCoreApplication.translate("FileOperationsWidget", "加载视频") == "加载视频"

    try:
        assert tr.load_language("en") == "en"
        assert tr.current_language == "en"
        assert QCoreApplication.translate("FileOperationsWidget", "加载视频") == "Load video"
        # 英文日志源串无需翻译：未收录的串经已装翻译器链回退到源文案。
        assert (
            QCoreApplication.translate("pipeline_worker", "ASS generation done in {:.2f}s.")
            == "ASS generation done in {:.2f}s."
        )

        assert tr.load_language("zh_TW") == "zh_TW"
        assert QCoreApplication.translate("FileOperationsWidget", "加载视频") == "載入影片"
    finally:
        _restore()


def test_startup_language_reads_settings():
    tr = Translator()
    settings = QSettings()
    saved = settings.value("ui/language")
    try:
        settings.setValue("ui/language", "en")
        assert tr.load_startup_language() == "en"
        assert QCoreApplication.translate("FileOperationsWidget", "加载视频") == "Load video"
    finally:
        if saved is None:
            settings.remove("ui/language")
        else:
            settings.setValue("ui/language", saved)
        tr.load_language("xx_XX")


def test_language_combo_reflects_and_persists(monkeypatch):
    """选择器随设置初始化；用户切换后持久化（重启提示在测试中被桩掉）。"""
    from components.file_operations import FileOperationsWidget

    settings = QSettings()
    saved = settings.value("ui/language")
    # 拦截重启确认对话框，返回 No（不重启）。
    monkeypatch.setattr(
        "components.file_operations.QMessageBox.question", lambda *a, **k: QMessageBox.No
    )
    try:
        settings.setValue("ui/language", "zh_TW")
        w = FileOperationsWidget()
        assert w.language_combo.currentData() == "zh_TW"

        # 用户切换到 English → 持久化到 QSettings。
        idx = w.language_combo.findData("en")
        w.language_combo.setCurrentIndex(idx)
        assert settings.value("ui/language") == "en"

        # 切换回相同值时不重复写（且无对话框）。
        settings.setValue("ui/language", "ja_JP")
        idx = w.language_combo.findData("ja_JP")
        w.language_combo.setCurrentIndex(idx)
        assert settings.value("ui/language") == "ja_JP"
    finally:
        if saved is None:
            settings.remove("ui/language")
        else:
            settings.setValue("ui/language", saved)
