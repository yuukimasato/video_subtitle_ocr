# main.py
import sys
from PySide6.QtWidgets import QApplication
from PySide6.QtCore import Qt
from main_window import SubtitleOCRGUI

if __name__ == '__main__':
    if hasattr(QApplication, 'setHighDpiScaleFactorRoundingPolicy'):
        QApplication.setHighDpiScaleFactorRoundingPolicy(Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)
    
    app = QApplication(sys.argv)
    window = SubtitleOCRGUI()
    window.show()
    sys.exit(app.exec())

