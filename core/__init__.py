# core/__init__.py
"""
Core module for Video Subtitle OCR.

Provides the OCR engine abstraction layer, text source classification,
video type auto-detection, and the subtitle generation pipeline.

Engine auto-registration:
  All known OCR engine adapters are attempted on import and registered
  with the OCREngineRegistry. Engines whose dependencies are not installed
  are silently skipped.
"""

from __future__ import annotations

import logging

from core.ocr_engine_base import OCREngineRegistry, BaseOCREngine, OCREngineInfo

logger = logging.getLogger(__name__)

# ═══════════════════════════════════════════════════════════════
# Auto-register all available OCR engines on module import.
# Each engine adapter is imported conditionally — if the underlying
# library isn't installed, the import fails and the engine is skipped.
# ═══════════════════════════════════════════════════════════════

_engine_classes = []

# PaddleOCR (default, most stable)
try:
    from core.ocr_engine_paddle import PaddleOCREngine
    _engine_classes.append(PaddleOCREngine)
except ImportError:
    pass

# RapidOCR (ONNX-based, CPU-friendly)
try:
    from core.ocr_engine_rapid import RapidOCREngine
    _engine_classes.append(RapidOCREngine)
except ImportError:
    pass

# Unlimited-OCR (high-speed alternative)
try:
    from core.ocr_engine_unlimited import UnlimitedOCREngine
    _engine_classes.append(UnlimitedOCREngine)
except ImportError:
    pass

for eng_cls in _engine_classes:
    try:
        OCREngineRegistry.register(eng_cls)
    except Exception as e:
        logger.warning(f"Failed to register engine {eng_cls.__name__}: {e}")

# Convenience exports
__all__ = [
    "BaseOCREngine",
    "OCREngineInfo",
    "OCREngineRegistry",
]
