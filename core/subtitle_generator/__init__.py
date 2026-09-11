# core/subtitle_generator/__init__.py
"""Package split of the former core/subtitle_generator.py module (pure structural refactor, zero behavior change)."""
from .generator import OCRToASSOptimizer
from .models import FrameData, SubtitleGroup, TextLine

__all__ = ["OCRToASSOptimizer", "TextLine", "FrameData", "SubtitleGroup"]
