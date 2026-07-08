# i18n/translator.py
import os
from PySide6.QtCore import QTranslator, QCoreApplication, QLocale
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
            print("Error: QApplication instance not found.")
            return
        app.removeTranslator(self.translator)
        i18n_dir = os.path.dirname(os.path.abspath(__file__))
        
        if not lang or lang.startswith("en"):
            print("Translator: Using default language (English).")
            return
        qm_file = os.path.join(i18n_dir, f"app_{lang}.qm")
        if os.path.exists(qm_file):
            if self.translator.load(qm_file):
                app.installTranslator(self.translator)
                print(f"Translator: Successfully loaded and installed '{qm_file}'")
            else:
                print(f"Error: Failed to load translation file '{qm_file}'")
        else:
            print(f"Warning: Translation file not found '{qm_file}'")
_translator_instance = None
def initialize_translator():
    global _translator_instance
    if _translator_instance is None:
        _translator_instance = Translator()
    return _translator_instance
def _t(context: str, text: str, disambiguation: str = None, n: int = -1) -> str:
    return QCoreApplication.translate(context, text, disambiguation, n)
