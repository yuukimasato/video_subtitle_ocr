# core/ocr_engine_rapid.py
"""
RapidOCR engine adapter.

RapidOCR (package name `rapidocr`, v3.x) is an ONNX-based OCR engine that
does not require PaddlePaddle. Inference runs on ONNX Runtime by default
and can be switched to other backends such as OpenVINO. The package ships
with PP-OCRv6 ONNX models, offers good CPU performance, and is a practical
alternative for users who cannot or prefer not to install the full
PaddlePaddle stack.

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

    Uses ONNX Runtime for inference (switchable to OpenVINO and other
    backends). No PaddlePaddle dependency required. Falls back gracefully
    if the `rapidocr` package is not installed.
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
            version="3.9.x",
            description="RapidOCR — 基于 ONNX Runtime（可切换 OpenVINO 等后端），无需 PaddlePaddle，已集成 PP-OCRv6 模型，CPU 友好，适合批量处理",
            supports_gpu=False,
            supports_languages=["ch", "en", "japan", "korean"],
            estimated_speed_rank=2,
        )

    @classmethod
    def is_available(cls) -> bool:
        try:
            import rapidocr  # noqa: F401
            return True
        except ImportError:
            return False

    def __init__(self):
        # Singleton guard: engine_cls() on the already-initialized singleton
        # must not silently unload the loaded models.
        if getattr(self, "_initialized", False):
            return
        self._ocr: Any = None
        self._initialized: bool = False
        self.text_score: float = 0.5

    def initialize(self, **kwargs) -> None:
        """Initialize RapidOCR engine."""
        if self._initialized:
            return

        try:
            from rapidocr import RapidOCR
        except ImportError:
            raise ImportError(
                "RapidOCR is not installed. "
                "Install it with: pip install rapidocr"
            )

        logger.info("Initializing RapidOCR (v3, ONNX Runtime backend)...")
        # v3 has a different constructor parameter system than 1.x;
        # construct it without arguments to stay API-agnostic.
        self._ocr = RapidOCR()
        # The score threshold is applied in normalize_result() because the
        # v3 runtime configuration API is not stable across versions.
        self.text_score = float(kwargs.get("text_score", 0.5))
        self._initialized = True
        logger.info("RapidOCR initialized successfully.")

    def predict(self, img_input):
        """Run RapidOCR prediction (thread-safe)."""
        if self._ocr is None:
            raise RuntimeError("RapidOCR engine not initialized. Call initialize() first.")

        # RapidOCR accepts file path (str), numpy array, or bytes.
        with self._predict_lock:
            out = self._ocr(img_input)
        return self._to_triples(out)

    def predict_batch(self, images: List[Any]) -> List[Any]:
        """Batch prediction (thread-safe).

        RapidOCR has no native list API, so this is sequential single-image
        prediction; each element of the returned list is one image's flat
        [box, text, score] triples list (see normalize_batch_result).
        """
        if self._ocr is None:
            raise RuntimeError("RapidOCR engine not initialized. Call initialize() first.")
        results = []
        with self._predict_lock:
            for img in images:
                results.append(self._to_triples(self._ocr(img)))
        return results

    def normalize_batch_result(self, raw_results: List[Any]) -> List[Dict[str, Any]]:
        """Normalize predict_batch() output (per-image triples lists)."""
        return [self.normalize_result(list(r)) for r in raw_results]

    @staticmethod
    def _to_triples(out: Any) -> List[List[Any]]:
        """Normalize rapidocr output to a list of [box, text, score] triples.

        Supports two output shapes:
        - v3 RapidOCROutput object with attributes ``boxes`` (numpy array,
          roughly (N, 4, 2) or (N, 4, 4)), ``txts`` and ``scores``;
        - legacy list of [box, text, score] items, passed through as-is.
        """
        if out is None:
            return []

        # Legacy style: list/tuple of [box, text, score] items.
        if isinstance(out, (list, tuple)):
            return list(out) if out else []

        # v3 style: RapidOCROutput object.
        if not any(hasattr(out, name) for name in ("boxes", "txts", "scores")):
            logger.warning(f"Unrecognized RapidOCR output type: {type(out)}")
            return []

        boxes = getattr(out, "boxes", None)
        txts = getattr(out, "txts", None)
        scores = getattr(out, "scores", None)

        # txts/scores being None means no usable text result.
        if boxes is None or txts is None or scores is None:
            return []

        box_list = np.asarray(boxes).tolist()
        triples: List[List[Any]] = []
        for i, text in enumerate(txts):
            box = box_list[i] if i < len(box_list) else []
            score = scores[i] if i < len(scores) else None
            triples.append(
                [box, str(text or ""), float(score) if score is not None else 1.0]
            )
        return triples

    def normalize_result(self, raw_result: List[Any]) -> Dict[str, Any]:
        """Convert RapidOCR output to unified format.

        RapidOCR returns: [[[x1,y1], [x2,y2], [x3,y3], [x4,y4]], "text", score], ...
        Lines with a recognition score below ``self.text_score`` are dropped.
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

        # Fall back to the default threshold if initialize() was never run.
        text_score = float(getattr(self, "text_score", 0.5))

        for item in raw_result:
            try:
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
                score = float(score) if score is not None else 0.0

                if score < text_score:
                    continue

                # Convert box to polygon format
                if isinstance(box, np.ndarray):
                    box = box.tolist()
                if not isinstance(box, (list, tuple)) or len(box) < 3:
                    logger.warning(f"Unexpected RapidOCR box format: {box!r}")
                    continue

                poly = [[float(p[0]), float(p[1])] for p in box]

                # Compute axis-aligned bounding box
                xs = [p[0] for p in box]
                ys = [p[1] for p in box]
                bbox = [
                    int(min(xs)), int(min(ys)),
                    int(max(xs)), int(max(ys)),
                ]
            except (TypeError, ValueError, IndexError) as e:
                logger.warning(f"Skipping malformed RapidOCR result item {item!r}: {e}")
                continue

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
