# utils/logger.py
import logging
from PySide6.QtCore import QObject, Signal

class QtLogHandler(logging.Handler, QObject):
    new_record = Signal(str)

    def __init__(self, parent=None):
        super().__init__()
        QObject.__init__(self, parent)

        self.setFormatter(logging.Formatter('%(asctime)s [%(levelname)s] - %(message)s', datefmt='%H:%M:%S'))

    def emit(self, record):
        msg = self.format(record)
        self.new_record.emit(msg)

def setup_logger():

    qt_handler = QtLogHandler()

    logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] - %(message)s', datefmt='%H:%M:%S')
    logger = logging.getLogger()
    logger.addHandler(qt_handler)
    
    return logger, qt_handler
