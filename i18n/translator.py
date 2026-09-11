# i18n/translator.py
import logging
import os
from PySide6.QtCore import QTranslator, QCoreApplication, QLocale

logger = logging.getLogger(__name__)

# 界面源文案为中文；以下 locale 直接使用源文案，无需加载 .qm 文件。
# 其余 locale 按 app_<lang>.qm 查找翻译（当前提供 app_ja_JP.qm / app_zh_CN.qm），
# 未提供翻译的 locale 同样回退到中文源文案。
_ENGLISH_LOCALES = frozenset({"en", "en_US", "en_GB", "en_CA", "en_AU", "en_NZ"})


class Translator:
    def __init__(self):
        self.translator = QTranslator(QCoreApplication.instance())
    def load_language(self, lang: str = "zh_CN"):
        """
        加载指定的语言文件。
        :param lang: 语言代码，例如 "zh_CN", "en_US"。
        """
        app = QCoreApplication.instance()
        if not app:
            logger.error("Translator: QApplication instance not found.")
            return
        app.removeTranslator(self.translator)
        i18n_dir = os.path.dirname(os.path.abspath(__file__))
        
        if not lang or lang in _ENGLISH_LOCALES:
            logger.info("Translator: Using default language (English).")
            return
        qm_file = os.path.join(i18n_dir, f"app_{lang}.qm")
        if os.path.exists(qm_file):
            if self.translator.load(qm_file):
                app.installTranslator(self.translator)
                logger.info("Translator: Successfully loaded and installed '%s'", qm_file)
            else:
                logger.error("Translator: Failed to load translation file '%s'", qm_file)
        else:
            logger.warning("Translator: Translation file not found '%s'", qm_file)
