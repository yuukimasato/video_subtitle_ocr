# core/ocr_engine_paddle.py
"""
PaddleOCR engine adapter.

Wraps the existing PaddleOCR integration into the BaseOCREngine interface.
This adapter extracts and consolidates PaddleOCR-specific logic that was
previously scattered in ocr_processor.py.
"""

from __future__ import annotations

import os
import logging
import threading
from typing import List, Dict, Any, Optional

import numpy as np

from core.ocr_engine_base import BaseOCREngine, OCREngineInfo

logger = logging.getLogger(__name__)

# Paddle/PaddleX compatibility: must set env vars BEFORE importing paddle/paddleocr
os.environ.setdefault("FLAGS_use_mkldnn", "0")
os.environ.setdefault("FLAGS_use_onednn", "0")
os.environ.setdefault("FLAGS_enable_onednn", "0")
os.environ.setdefault("FLAGS_use_new_executor", "0")
os.environ.setdefault("FLAGS_enable_pir_api", "0")


class PaddleOCREngine(BaseOCREngine):
    """PaddleOCR engine adapter.

    Wraps PaddleOCR with the unified BaseOCREngine interface.
    Maintains backward compatibility with all existing ocr_data_dict consumers.
    """

    _instance: Optional["PaddleOCREngine"] = None
    _init_lock = threading.Lock()
    _predict_lock = threading.Lock()

    def __new__(cls):
        # Process-level singleton via __new__ (compatible with registry pattern).
        if cls._instance is None:
            with cls._init_lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
        return cls._instance

    @classmethod
    def get_engine_info(cls) -> OCREngineInfo:
        return OCREngineInfo(
            engine_id="paddle",
            name="PaddleOCR",
            version="3.1.0",
            description="PaddleOCR — 百度 PaddleOCR，支持中/日/韩/英等多语言，准确率高，最稳定",
            supports_gpu=True,
            supports_languages=[
                "ch", "en", "japan", "korean", "french", "german",
                "italian", "spanish", "portuguese", "russian", "arabic",
            ],
            estimated_speed_rank=3,
        )

    @classmethod
    def is_available(cls) -> bool:
        try:
            import paddleocr  # noqa: F401
            return True
        except ImportError:
            return False

    def __init__(self):
        # __init__ is called after __new__, but we use initialize() for heavy lifting.
        self._ocr: Any = None
        self._initialized: bool = False

    def _get_device_mode(self) -> str:
        """Detect GPU/CPU mode with multiple fallback strategies.

        Priority order:
          1. MODE environment variable (set by launcher scripts)
          2. .gpu_mode file (written by postinst during installation)
          3. Runtime GPU availability check via PaddlePaddle
          4. Default: "cpu"
        """
        # ── Strategy 1: Check environment variable ─────────
        env_mode = os.environ.get("MODE", "").strip().lower()
        if env_mode in ("gpu", "cpu"):
            logger.debug(f"Device mode from env MODE: {env_mode}")
            return env_mode

        # ── Strategy 2: Read .gpu_mode file ────────────────
        # Supports both formats:
        #   MODE=gpu              (written by postinst v2.0)
        #   export MODE="gpu"     (legacy format)
        gpu_mode_file = os.path.join(os.path.dirname(__file__), "..", ".gpu_mode")
        try:
            if os.path.exists(gpu_mode_file):
                with open(gpu_mode_file, "r") as f:
                    for line in f:
                        line = line.strip()
                        if not line or line.startswith("#"):
                            continue
                        # Match: MODE=gpu, export MODE="gpu", export MODE=gpu, MODE="gpu"
                        import re
                        m = re.search(r'(?:export\s+)?MODE\s*=\s*["\']?(\w+)["\']?', line)
                        if m:
                            file_mode = m.group(1).strip().lower()
                            if file_mode in ("gpu", "cpu"):
                                logger.debug(f"Device mode from .gpu_mode file: {file_mode}")
                                return file_mode
        except Exception as e:
            logger.debug(f"Failed to read .gpu_mode file: {e}")

        # ── Strategy 3: Runtime GPU availability ───────────
        # Try to detect if PaddlePaddle GPU is actually usable
        try:
            import paddle
            if paddle.is_compiled_with_cuda():
                gpu_count = paddle.device.cuda.device_count()
                if gpu_count > 0:
                    logger.info(f"Runtime GPU check: {gpu_count} CUDA device(s) available via PaddlePaddle.")
                    return "gpu"
            # Also check for other device types
            if hasattr(paddle, 'device'):
                try:
                    if paddle.device.is_compiled_with_rocm():
                        logger.info("Runtime GPU check: ROCm device available via PaddlePaddle.")
                        return "gpu"
                except Exception:
                    pass
                try:
                    if paddle.device.is_compiled_with_xpu():
                        logger.info("Runtime GPU check: XPU device available via PaddlePaddle.")
                        return "gpu"
                except Exception:
                    pass
        except ImportError:
            logger.debug("PaddlePaddle not importable during GPU check.")
        except Exception as e:
            logger.debug(f"Runtime GPU check failed: {e}")

        # ── Default: CPU ───────────────────────────────────
        logger.debug("Device mode defaulting to: cpu")
        return "cpu"

    def initialize(self, **kwargs) -> None:
        """Initialize PaddleOCR model (lazy, called once)."""
        if self._initialized:
            return

        from paddleocr import PaddleOCR

        device = kwargs.get("device") or self._get_device_mode()
        lang = kwargs.get("lang", "ch")

        logger.info(f"Initializing PaddleOCR (lang={lang}, device={device})...")
        self._ocr = PaddleOCR(
            use_doc_orientation_classify=False,
            use_doc_unwarping=False,
            use_textline_orientation=False,
            lang=lang,
            device=device,
        )
        self._initialized = True
        logger.info("PaddleOCR initialized successfully.")

    def predict(self, img_input):
        """Run PaddleOCR prediction (thread-safe)."""
        if self._ocr is None:
            raise RuntimeError("PaddleOCR engine not initialized. Call initialize() first.")
        with self._predict_lock:
            return self._ocr.predict(img_input)

    def normalize_result(self, raw_result: List[Any]) -> Dict[str, Any]:
        """Convert PaddleOCR output to unified format.

        Handles both PaddleOCR output formats:
        1. Document-level: [{rec_texts, rec_scores, rec_polys, rec_boxes}]
        2. Line-level: [([[x,y],...], ("text", score)), ...]
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

        # Document-level result format (newer PaddleOCR)
        if (
            isinstance(raw_result, list)
            and len(raw_result) == 1
            and isinstance(raw_result[0], dict)
        ):
            single_dict = raw_result[0]
            texts = single_dict.get("rec_texts", [])
            scores = single_dict.get("rec_scores", [])
            polys = single_dict.get("rec_polys", [])
            boxes = single_dict.get("rec_boxes", [])

            min_len = min(len(texts), len(scores), len(polys), len(boxes))
            for i in range(min_len):
                ocr_data["dt_polys"].append(
                    polys[i].tolist() if isinstance(polys[i], np.ndarray) else polys[i]
                )
                ocr_data["rec_polys"].append(
                    polys[i].tolist() if isinstance(polys[i], np.ndarray) else polys[i]
                )
                ocr_data["rec_texts"].append(texts[i])
                ocr_data["rec_scores"].append(float(scores[i]))
                ocr_data["rec_boxes"].append(
                    boxes[i].tolist() if isinstance(boxes[i], np.ndarray) else boxes[i]
                )
            return ocr_data

        # Line-level result format (classic PaddleOCR)
        for line_result in raw_result:
            if len(line_result) == 2 and isinstance(line_result[0], list) and isinstance(line_result[1], tuple):
                box_polygon = line_result[0]
                text, score = line_result[1]

                ocr_data["dt_polys"].append(box_polygon)
                ocr_data["rec_polys"].append(box_polygon)
                ocr_data["rec_texts"].append(text)
                ocr_data["rec_scores"].append(float(score))

                np_poly = np.array(box_polygon, dtype=np.int32)
                x_min, y_min = np.min(np_poly[:, 0]), np.min(np_poly[:, 1])
                x_max, y_max = np.max(np_poly[:, 0]), np.max(np_poly[:, 1])
                ocr_data["rec_boxes"].append([int(x_min), int(y_min), int(x_max), int(y_max)])
            else:
                logger.warning(f"Unexpected PaddleOCR line result format: {line_result}")

        return ocr_data

    def cleanup(self) -> None:
        """Release PaddleOCR resources."""
        if self._ocr is not None:
            del self._ocr
            self._ocr = None
            self._initialized = False
            logger.info("PaddleOCR engine cleaned up.")
