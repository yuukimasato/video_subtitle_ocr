# main.py
import sys
from PySide6.QtWidgets import QApplication
from PySide6.QtCore import Qt, QTranslator, QLocale
import os 

from main_window import SubtitleOCRGUI

if __name__ == '__main__':
    if hasattr(QApplication, 'setHighDpiScaleFactorRoundingPolicy'):
        QApplication.setHighDpiScaleFactorRoundingPolicy(Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)
    app = QApplication(sys.argv)
    translator = QTranslator()
    i18n_path = os.path.join(os.path.dirname(__file__), "i18n")
#     if translator.load("app_zh_CN", i18n_path):
#         app.installTranslator(translator)
#         print("Loaded Chinese translation.")
#     else:
#         print(f"Failed to load Chinese translation from {os.path.join(i18n_path, 'app_zh_CN.qm')}")


    current_locale = QLocale().system().name()
    if translator.load(QLocale(), "app", "_", i18n_path):
        app.installTranslator(translator)
        print(f"Loaded translation for locale: {current_locale}")
    else:
        print(f"No translation file found for locale {current_locale} or failed to load.")


    window = SubtitleOCRGUI()
    window.show()
    sys.exit(app.exec())

