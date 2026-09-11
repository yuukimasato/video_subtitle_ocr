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
        try:
            msg = self.format(record)
            self.new_record.emit(msg)
        except Exception:
            self.handleError(record)

# Module-level marker so repeated setup_logger() calls stay idempotent.
_logger_configured = False

def setup_logger():
    global _logger_configured

    logger = logging.getLogger()
    if not _logger_configured:
        logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] - %(message)s', datefmt='%H:%M:%S')
        _logger_configured = True

    # Return the already-installed handler if present; never attach a second one.
    for existing in logger.handlers:
        if isinstance(existing, QtLogHandler):
            return logger, existing

    qt_handler = QtLogHandler()
    logger.addHandler(qt_handler)

    return logger, qt_handler
