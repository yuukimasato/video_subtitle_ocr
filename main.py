# main.py
import sys
import traceback
from PySide6.QtWidgets import QApplication, QMessageBox
from PySide6.QtCore import Qt, QTranslator, QLocale, QCoreApplication
import os 

from main_window import SubtitleOCRGUI


def _excepthook(exc_type, exc_value, exc_tb):
    """Print uncaught exceptions to stderr, then surface them in a dialog when a GUI exists."""
    sys.__excepthook__(exc_type, exc_value, exc_tb)
    if QApplication.instance() is None:
        return
    try:
        details = "".join(traceback.format_exception(exc_type, exc_value, exc_tb))
        QMessageBox.critical(None, "Unhandled exception", details)
    except Exception:
        # Never let the error dialog itself take the process down.
        pass


if __name__ == '__main__':
    sys.excepthook = _excepthook
    QApplication.setHighDpiScaleFactorRoundingPolicy(Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)
    QCoreApplication.setOrganizationName("VideoSubtitleOCR")
    QCoreApplication.setApplicationName("VideoSubtitleOCR")
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

