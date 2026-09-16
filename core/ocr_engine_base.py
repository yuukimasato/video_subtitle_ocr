# core/ocr_engine_base.py
"""
OCR Engine abstraction layer.

Defines the BaseOCREngine abstract base class that all OCR engines must implement,
along with the OCREngineRegistry for engine discovery and management.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Dict, Any, Optional, List
from dataclasses import dataclass, field
import logging

logger = logging.getLogger(__name__)


@dataclass
class OCREngineInfo:
    """Engine metadata for UI display and registry management."""

    engine_id: str  # Unique identifier: "paddle", "rapid", etc.
    name: str  # Display name: "PaddleOCR", "RapidOCR", etc.
    version: str  # Engine version string
    description: str  # Brief description for tooltips
    supports_gpu: bool  # Whether GPU acceleration is supported
    supports_languages: List[str] = field(default_factory=lambda: ["ch", "en"])
    estimated_speed_rank: int = 3  # Estimated speed rank (1 = fastest)
    # Model tiers selectable via engine options ("tiny"/"small"/"medium").
    # Empty list = engine has no tier selection.
    supported_model_tiers: List[str] = field(default_factory=list)


class BaseOCREngine(ABC):
    """Abstract base class for OCR engines.

    All OCR engines must implement this interface. Engine instances should be
    process-level singletons, obtained via the engine manager.
    """

    @classmethod
    @abstractmethod
    def get_engine_info(cls) -> OCREngineInfo:
        """Return engine metadata."""
        ...

    @classmethod
    @abstractmethod
    def is_available(cls) -> bool:
        """Check whether engine dependencies are available.

        Returns True if required Python packages are installed and models
        are downloaded.
        """
        ...

    @abstractmethod
    def initialize(self, **kwargs) -> None:
        """Initialize the engine and load models. Called once per process."""
        ...

    def is_initialized(self) -> bool:
        """True when this instance is ready for predict() calls.

        The engine manager consults this to detect a cached instance that was
        cleaned up behind its back and rebuild it instead of returning a dead
        engine. Default True keeps unknown engine implementations behaving as
        before.
        """
        return True

    @abstractmethod
    def predict(self, img_input):
        """Run OCR on a single image.

        Args:
            img_input: Image file path (str) or numpy ndarray (BGR).

        Returns:
            Raw OCR results list. Format is engine-specific, but will be
            converted to unified format by normalize_result().
        """
        ...

    def predict_batch(self, images: List[Any]) -> List[Any]:
        """Batch prediction. Default: sequential single-image calls. Returns list aligned with inputs.

        Engines with native batch support (e.g. PaddleOCR 3.x) should override
        this method for better throughput. The returned list must be aligned
        with the input list (one raw result per input image).
        """
        return [self.predict(img) for img in images]

    def normalize_batch_result(self, raw_results: List[Any]) -> List[Dict[str, Any]]:
        """Normalize a predict_batch() output into per-image unified dicts.

        Default assumes PaddleOCR semantics: each element of ``raw_results``
        is one per-image result object, wrapped in a single-element list for
        normalize_result(). Engines whose predict() returns a different shape
        (e.g. RapidOCR's flat list of [box, text, score] triples) must
        override this method.
        """
        return [self.normalize_result([r]) for r in raw_results]

    @abstractmethod
    def normalize_result(self, raw_result: List[Any]) -> Dict[str, Any]:
        """Convert engine-specific raw output to unified format.

        Unified format (compatible with existing ocr_data_dict):
        {
            'dt_polys': List[List[float]],    # Detection polygons
            'rec_polys': List[List[float]],   # Recognition polygons
            'rec_texts': List[str],           # Recognized text
            'rec_scores': List[float],        # Confidence scores
            'rec_boxes': List[List[int]],     # Axis-aligned bounding boxes [x1, y1, x2, y2]
            # Optional extended fields:
            'char_scores': List[List[float]], # Per-character confidence (if available)
            'engine_meta': Dict[str, Any],    # Engine-specific metadata
        }
        """
        ...

    @abstractmethod
    def cleanup(self) -> None:
        """Release engine resources (GPU memory, etc.)."""
        ...


class OCREngineRegistry:
    """OCR engine registry.

    Manages discovery, registration, and querying of all available OCR engines.
    """

    _engines: Dict[str, type] = {}

    @classmethod
    def register(cls, engine_cls: type) -> None:
        """Register an OCR engine class."""
        if not issubclass(engine_cls, BaseOCREngine):
            raise TypeError(f"{engine_cls} must inherit from BaseOCREngine")
        info = engine_cls.get_engine_info()
        cls._engines[info.engine_id] = engine_cls
        logger.debug(f"Registered OCR engine: {info.engine_id} ({info.name})")

    @classmethod
    def list_available(cls) -> List[OCREngineInfo]:
        """List all engines whose dependencies are installed."""
        return [
            eng.get_engine_info()
            for eng in cls._engines.values()
            if eng.is_available()
        ]

    @classmethod
    def list_all(cls) -> List[OCREngineInfo]:
        """List all registered engines (including those with missing dependencies)."""
        return [eng.get_engine_info() for eng in cls._engines.values()]

    @classmethod
    def get(cls, engine_id: str) -> Optional[type]:
        """Get an engine class by ID."""
        return cls._engines.get(engine_id)

    @classmethod
    def get_default(cls) -> str:
        """Return the default engine ID.

        Priority: PaddleOCR (most stable) > first available > empty string.
        """
        available = cls.list_available()
        # Prefer PaddleOCR as default — it's the most mature and well-tested.
        if any(e.engine_id == "paddle" for e in available):
            return "paddle"
        if available:
            return available[0].engine_id
        return ""


# Auto-register all available engines on module import.
def _auto_register_engines():
    """Attempt to import and register all known engine adapters."""
    _engine_classes = []

    # Try PaddleOCR
    try:
        from core.ocr_engine_paddle import PaddleOCREngine
        _engine_classes.append(PaddleOCREngine)
    except ImportError:
        pass

    # Try RapidOCR
    try:
        from core.ocr_engine_rapid import RapidOCREngine
        _engine_classes.append(RapidOCREngine)
    except ImportError:
        pass

    for eng_cls in _engine_classes:
        try:
            OCREngineRegistry.register(eng_cls)
        except Exception as e:
            logger.warning(f"Failed to register engine {eng_cls.__name__}: {e}")


_auto_register_engines()
