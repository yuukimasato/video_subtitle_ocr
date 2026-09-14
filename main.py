# main.py
import sys
import traceback
from PySide6.QtWidgets import QApplication, QMessageBox
from PySide6.QtCore import Qt, QCoreApplication
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

    # 界面语言：读取持久化设置 ui/language（默认 auto 跟随系统），见 i18n/translator.py。
    from i18n.translator import Translator
    translator = Translator()
    applied = translator.load_startup_language()
    print(f"Language: {applied}" if applied else "Language: zh_CN (source; no translation loaded)")


    window = SubtitleOCRGUI()
    window.show()
    sys.exit(app.exec())

