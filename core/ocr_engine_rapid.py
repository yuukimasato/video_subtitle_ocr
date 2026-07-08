# core/ocr_engine_rapid.py
"""
RapidOCR engine adapter.

RapidOCR is an ONNX-based OCR engine that does not require PaddlePaddle.
It offers good CPU performance and is a practical alternative for users
who cannot or prefer not to install the full PaddlePaddle stack.

RapidOCR uses PaddleOCR models converted to ONNX format, so accuracy is
comparable to PaddleOCR with the same models.
"""

from __future__ import annotations

import logging
import threading
from typing import List, Dict, Any, Optional

import numpy as np

from core.ocr_engine_base import BaseOCREngine, OCREngineInfo

logger = logging.getLogger(__name__)


class RapidOCREngine(BaseOCREngine):
    """RapidOCR engine adapter (ONNX-based, CPU-friendly).

    Uses ONNX Runtime for inference. No PaddlePaddle dependency required.
    Falls back gracefully if rapidocr_onnxruntime is not installed.
    """

    _instance: Optional["RapidOCREngine"] = None
    _init_lock = threading.Lock()
    _predict_lock = threading.Lock()

    def __new__(cls):
        if cls._instance is None:
            with cls._init_lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
        return cls._instance

    @classmethod
    def get_engine_info(cls) -> OCREngineInfo:
        return OCREngineInfo(
            engine_id="rapid",
            name="RapidOCR (ONNX)",
            version="1.3.x",
            description="RapidOCR — 基于 ONNX Runtime，无需 PaddlePaddle，CPU 友好，适合批量处理",
            supports_gpu=False,
            supports_languages=["ch", "en", "japan", "korean"],
            estimated_speed_rank=2,
        )

    @classmethod
    def is_available(cls) -> bool:
        try:
            import rapidocr_onnxruntime  # noqa: F401
            return True
        except ImportError:
            return False

    def __init__(self):
        self._ocr: Any = None
        self._initialized: bool = False

    def initialize(self, **kwargs) -> None:
        """Initialize RapidOCR engine."""
        if self._initialized:
            return

        try:
            from rapidocr_onnxruntime import RapidOCR
        except ImportError:
            raise ImportError(
                "RapidOCR (ONNX) is not installed. "
                "Install it with: pip install rapidocr-onnxruntime"
            )

        logger.info("Initializing RapidOCR (ONNX Runtime)...")
        self._ocr = RapidOCR(
            text_score=kwargs.get("text_score", 0.5),
        )
        self._initialized = True
        logger.info("RapidOCR initialized successfully.")

    def predict(self, img_input):
        """Run RapidOCR prediction (thread-safe)."""
        if self._ocr is None:
            raise RuntimeError("RapidOCR engine not initialized. Call initialize() first.")

        # RapidOCR accepts file path (str), numpy array, or bytes.
        with self._predict_lock:
            result, _ = self._ocr(img_input)
        return result if result else []

    def normalize_result(self, raw_result: List[Any]) -> Dict[str, Any]:
        """Convert RapidOCR output to unified format.

        RapidOCR returns: [[[x1,y1], [x2,y2], [x3,y3], [x4,y4]], "text", score], ...
        """
        ocr_data: Dict[str, Any] = {
            "dt_polys": [],
            "rec_polys": [],
            "rec_texts": [],
            "rec_scores": [],
            "rec_boxes": [],
        }

        if not raw_result:
            return ocr_data

        for item in raw_result:
            # RapidOCR output format: [box_points, text, score]
            if len(item) == 3:
                box, text, score = item
            elif len(item) == 2:
                box, text = item
                score = 1.0
            else:
                logger.warning(f"Unexpected RapidOCR result format: {item}")
                continue

            text = str(text or "")
            score = float(score) if score else 0.0

            # Convert box to polygon format
            if isinstance(box, np.ndarray):
                box = box.tolist()

            poly = [[float(p[0]), float(p[1])] for p in box]

            # Compute axis-aligned bounding box
            xs = [p[0] for p in box]
            ys = [p[1] for p in box]
            bbox = [
                int(min(xs)), int(min(ys)),
                int(max(xs)), int(max(ys)),
            ]

            ocr_data["dt_polys"].append(poly)
            ocr_data["rec_polys"].append(poly)
            ocr_data["rec_texts"].append(text)
            ocr_data["rec_scores"].append(score)
            ocr_data["rec_boxes"].append(bbox)

        return ocr_data

    def cleanup(self) -> None:
        """Release RapidOCR resources."""
        if self._ocr is not None:
            del self._ocr
            self._ocr = None
            self._initialized = False
            logger.info("RapidOCR engine cleaned up.")
