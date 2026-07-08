# core/ocr_engine_unlimited.py
"""
Unlimited-OCR engine adapter.

Unlimited-OCR is a high-speed OCR engine designed for batch video processing.
It claims significantly faster throughput than PaddleOCR while maintaining
competitive accuracy for Chinese/English text recognition.

This adapter wraps Unlimited-OCR into the BaseOCREngine interface.
If the unlimited_ocr package is not installed, is_available() returns False
and the engine is silently excluded from the UI.
"""

from __future__ import annotations

import logging
import threading
from typing import List, Dict, Any, Optional

import numpy as np

from core.ocr_engine_base import BaseOCREngine, OCREngineInfo

logger = logging.getLogger(__name__)


class UnlimitedOCREngine(BaseOCREngine):
    """Unlimited-OCR engine adapter.

    Provides a high-speed alternative to PaddleOCR. Best suited for:
    - Long video batch processing where speed matters
    - CPU-only environments (if GPU is unavailable)
    - Scenarios where PaddlePaddle's large dependency footprint is undesirable

    Falls back gracefully if the unlimited_ocr package is not installed.
    """

    _instance: Optional["UnlimitedOCREngine"] = None
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
            engine_id="unlimited",
            name="Unlimited-OCR",
            version="latest",
            description=(
                "Unlimited-OCR — 高速 OCR 引擎，适合长视频批量处理，"
                "速度优势明显。需要单独安装 unlimited-ocr 包。"
            ),
            supports_gpu=True,
            supports_languages=[
                "ch", "en", "japan", "korean",
                "french", "german", "italian", "spanish",
                "portuguese", "russian", "arabic",
            ],
            estimated_speed_rank=1,  # Fastest
        )

    @classmethod
    def is_available(cls) -> bool:
        """Check whether unlimited_ocr is installed."""
        try:
            import unlimited_ocr  # noqa: F401
            return True
        except ImportError:
            return False

    def __init__(self):
        self._ocr: Any = None
        self._initialized: bool = False

    def _get_device_mode(self) -> str:
        """Detect GPU/CPU mode with multiple fallback strategies.

        Priority order:
          1. MODE environment variable (set by launcher scripts)
          2. .gpu_mode file (written by postinst during installation)
          3. Runtime GPU availability check
          4. Default: "cpu"
        """
        import os as _os
        import re as _re

        # Strategy 1: Environment variable
        env_mode = _os.environ.get("MODE", "").strip().lower()
        if env_mode in ("gpu", "cpu"):
            return env_mode

        # Strategy 2: .gpu_mode file
        gpu_mode_file = _os.path.join(_os.path.dirname(__file__), "..", ".gpu_mode")
        try:
            if _os.path.exists(gpu_mode_file):
                with open(gpu_mode_file, "r") as f:
                    for line in f:
                        line = line.strip()
                        if not line or line.startswith("#"):
                            continue
                        m = _re.search(r'(?:export\s+)?MODE\s*=\s*["\']?(\w+)["\']?', line)
                        if m:
                            file_mode = m.group(1).strip().lower()
                            if file_mode in ("gpu", "cpu"):
                                return file_mode
        except Exception:
            pass

        # Strategy 3: Runtime check
        try:
            import paddle
            if paddle.is_compiled_with_cuda() and paddle.device.cuda.device_count() > 0:
                return "gpu"
            if hasattr(paddle, 'device'):
                if getattr(paddle.device, 'is_compiled_with_rocm', lambda: False)():
                    return "gpu"
                if getattr(paddle.device, 'is_compiled_with_xpu', lambda: False)():
                    return "gpu"
        except Exception:
            pass

        return "cpu"

    def initialize(self, **kwargs) -> None:
        """Initialize Unlimited-OCR engine.

        Args:
            lang: Language code (default "ch").
            device: "cpu" or "gpu".
        """
        if self._initialized:
            return

        try:
            from unlimited_ocr import OCR
        except ImportError:
            raise ImportError(
                "Unlimited-OCR is not installed. "
                "Install it according to the Unlimited-OCR documentation, "
                "or switch to PaddleOCR / RapidOCR in the engine selector."
            )

        lang = kwargs.get("lang", "ch")
        device = kwargs.get("device") or self._get_device_mode()

        logger.info(f"Initializing Unlimited-OCR (lang={lang}, device={device})...")
        self._ocr = OCR(
            language=lang,
            use_gpu=(str(device).lower() == "gpu"),
        )
        self._initialized = True
        logger.info("Unlimited-OCR initialized successfully.")

    def predict(self, img_input):
        """Run Unlimited-OCR prediction (thread-safe).

        Args:
            img_input: Image file path (str) or numpy ndarray (BGR).

        Returns:
            Raw OCR results from the engine.
        """
        if self._ocr is None:
            raise RuntimeError(
                "Unlimited-OCR engine not initialized. Call initialize() first."
            )

        with self._predict_lock:
            # Unlimited-OCR may accept file paths or numpy arrays
            result = self._ocr.recognize(img_input)
        return result if result else []

    def normalize_result(self, raw_result: List[Any]) -> Dict[str, Any]:
        """Convert Unlimited-OCR output to the unified format.

        Expected Unlimited-OCR output format (list of dicts):
        [
            {"text": "...", "confidence": 0.95, "box": [x1, y1, x2, y2]},
            ...
        ]

        If the actual API differs, this method adapts to the observed format.
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
            try:
                if isinstance(item, dict):
                    # Dict format: {"text", "confidence", "box"}
                    text = str(item.get("text", ""))
                    score = float(item.get("confidence", item.get("score", 0.0)))
                    box = item.get("box", [])

                    if not text.strip():
                        continue

                    # Normalize box to [x1, y1, x2, y2] if it's a polygon
                    if isinstance(box, list) and len(box) >= 4:
                        if len(box) == 4:
                            # Axis-aligned: [x1, y1, x2, y2]
                            bbox = [int(v) for v in box]
                            poly = [
                                [float(box[0]), float(box[1])],
                                [float(box[2]), float(box[1])],
                                [float(box[2]), float(box[3])],
                                [float(box[0]), float(box[3])],
                            ]
                        elif len(box) >= 8:
                            # Polygon: [x1, y1, x2, y2, ...]
                            xs = box[0::2]
                            ys = box[1::2]
                            bbox = [
                                int(min(xs)), int(min(ys)),
                                int(max(xs)), int(max(ys)),
                            ]
                            poly = [[float(box[i]), float(box[i + 1])]
                                    for i in range(0, len(box) - 1, 2)]
                        else:
                            continue

                        ocr_data["dt_polys"].append(poly)
                        ocr_data["rec_polys"].append(poly)
                        ocr_data["rec_texts"].append(text)
                        ocr_data["rec_scores"].append(score)
                        ocr_data["rec_boxes"].append(bbox)

                elif isinstance(item, (list, tuple)) and len(item) >= 2:
                    # Tuple format: ([[x,y],...], "text", score)
                    if len(item) == 3:
                        box_or_poly, text, score = item
                    else:
                        box_or_poly, text = item
                        score = 1.0

                    text = str(text or "")
                    score = float(score) if score else 0.0

                    if not text.strip():
                        continue

                    if isinstance(box_or_poly, np.ndarray):
                        box_or_poly = box_or_poly.tolist()

                    if isinstance(box_or_poly, list) and len(box_or_poly) >= 4:
                        xs = [p[0] for p in box_or_poly]
                        ys = [p[1] for p in box_or_poly]
                        bbox = [int(min(xs)), int(min(ys)), int(max(xs)), int(max(ys))]
                        poly = [[float(p[0]), float(p[1])] for p in box_or_poly]

                        ocr_data["dt_polys"].append(poly)
                        ocr_data["rec_polys"].append(poly)
                        ocr_data["rec_texts"].append(text)
                        ocr_data["rec_scores"].append(score)
                        ocr_data["rec_boxes"].append(bbox)

                else:
                    logger.debug(
                        f"Unlimited-OCR: unrecognized result item format: {type(item)}"
                    )

            except Exception as e:
                logger.warning(f"Unlimited-OCR: error normalizing result item: {e}")
                continue

        return ocr_data

    def cleanup(self) -> None:
        """Release Unlimited-OCR resources."""
        if self._ocr is not None:
            del self._ocr
            self._ocr = None
            self._initialized = False
            logger.info("Unlimited-OCR engine cleaned up.")
