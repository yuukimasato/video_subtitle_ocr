# i18n/translator.py
import logging
import os

from PySide6.QtCore import QTranslator, QCoreApplication, QLocale, QSettings

logger = logging.getLogger(__name__)

# 界面源文案为中文；无翻译文件或未知语言时回退到中文源文案。
# 支持的语言与 i18n/app_<lang>.qm 一一对应。
SUPPORTED_LANGUAGES = ("zh_CN", "zh_TW", "en", "ja_JP")
_AUTO = "auto"


def resolve_locale(name: str) -> str:
    """把 locale 名（如 zh_TW、en_US，分隔符 - 或 _）解析到支持的语言。

    解析不到支持语言时返回空串（回退中文源文案）。
    """
    if not name:
        return ""
    name = name.replace("-", "_")
    parts = name.split("_")
    lang = parts[0].lower()
    region = parts[1].upper() if len(parts) > 1 else ""
    if lang == "zh":
        return "zh_TW" if region in {"TW", "HK", "MO"} else "zh_CN"
    if lang == "ja":
        return "ja_JP"
    if lang == "en":
        return "en"
    return ""


class Translator:
    """安装/切换应用翻译器。

    启动时调用 load_startup_language()（读取持久化设置 ui/language，
    默认 auto 跟随系统）；之后可随时用 load_language() 替换翻译器。
    注意：已创建控件的文案不会自动刷新，切换语言需重建界面（重启应用）。
    """

    def __init__(self):
        self.translator = QTranslator(QCoreApplication.instance())
        self.current_language = ""

    @staticmethod
    def system_language() -> str:
        """系统 locale 解析出的支持语言；不支持时为空串。"""
        return resolve_locale(QLocale.system().name())

    def load_startup_language(self) -> str:
        """按持久化设置安装翻译器，返回实际生效的语言（空串=中文源文案）。"""
        lang = QSettings().value("ui/language", _AUTO)
        if not isinstance(lang, str):
            lang = _AUTO
        return self.load_language(lang)

    def load_language(self, lang: str = "zh_CN") -> str:
        """安装指定语言；"auto"/"" 表示跟随系统。

        返回实际生效的语言代码；空串表示未安装翻译（使用中文源文案）。
        """
        app = QCoreApplication.instance()
        if not app:
            logger.error("Translator: QApplication instance not found.")
            self.current_language = ""
            return ""
        app.removeTranslator(self.translator)
        if not lang or lang == _AUTO:
            lang = self.system_language()
        if lang in SUPPORTED_LANGUAGES:
            qm_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), f"app_{lang}.qm")
            if os.path.exists(qm_file) and self.translator.load(qm_file):
                app.installTranslator(self.translator)
                self.current_language = lang
                logger.info("Translator: loaded '%s'.", qm_file)
                return lang
            logger.warning("Translator: translation file unavailable '%s'.", qm_file)
        self.current_language = ""
        return ""
