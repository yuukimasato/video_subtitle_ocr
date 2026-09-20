# core/ocr_engine_paddle.py
"""
PaddleOCR engine adapter.

Wraps the existing PaddleOCR integration into the BaseOCREngine interface.
This adapter extracts and consolidates PaddleOCR-specific logic that was
previously scattered in ocr_processor.py.

Model selection (PP-OCRv6 first):
  - ch / chinese_cht / en / japan / latin languages (french, german, it,
    es, pt) → PP-OCRv6 unified models (tier: tiny/small/medium or auto).
  - Other languages (korean/russian/arabic, ...) → conservative fallback to
    PP-OCRv5 (multilingual rec model via V5_FALLBACK_REC when known).
"""

from __future__ import annotations

import os
import logging
import threading
from typing import List, Dict, Any, Optional

import numpy as np

from core.ocr_engine_base import BaseOCREngine, OCREngineInfo

logger = logging.getLogger(__name__)

# Languages covered by the PP-OCRv6 unified models. chinese_cht (繁體中文)
# is a first-class PP-OCRv6 language in paddleocr 3.7 (_PPOCRV6_LANGS), so
# it rides the same v6 fast path as ch — no extra model download. The latin
# script languages are v6-capable too (_PPOCRV6_LANGS = {ch, chinese_cht,
# en, japan} | LATIN_LANGS), so they reuse the cached v6 models instead of
# pulling PP-OCRv5 server det + latin rec. NOTE: paddleocr only knows the
# ISO codes it/es/pt for Italian/Spanish/Portuguese — the legacy names
# italian/spanish/portuguese are unknown langs and fail initialization.
V6_LANGS = frozenset({
    "ch", "chinese_cht", "en", "japan",
    "french", "german", "it", "es", "pt",
})

# Recognized model tiers (PP-OCRv6_{tier}_det / PP-OCRv6_{tier}_rec).
VALID_MODEL_TIERS = ("tiny", "small", "medium")

# PP-OCRv5 multilingual recognition fallback models for languages NOT covered
# by PP-OCRv6. NOTE: model names follow the paddleocr 3.7 package model list —
# verify against the installed paddleocr during integration (T1.2).
# Languages without an entry rely on paddleocr's own lang-based model mapping.
V5_FALLBACK_REC: Dict[str, str] = {
    "korean": "korean_PP-OCRv5_mobile_rec",
    "russian": "cyrillic_PP-OCRv5_mobile_rec",
    "arabic": "arabic_PP-OCRv5_mobile_rec",
}


def resolve_model_selection(lang: str, model_tier: Optional[str] = None) -> Dict[str, Any]:
    """Map (lang, model_tier) to PaddleOCR constructor model-selection kwargs.

    Returns a dict containing either:
      - ``ocr_version`` ("PP-OCRv6" / "PP-OCRv5") to use the default models of
        that version, or
      - explicit ``text_detection_model_name`` / ``text_recognition_model_name``
        when a concrete PP-OCRv6 tier is requested.

    Shared by the engine adapter and preload_models.py so the language routing
    logic lives in exactly one place.
    """
    lang = str(lang or "ch")
    tier = str(model_tier).strip().lower() if model_tier else ""
    if tier in ("", "auto", "none", "null"):
        tier = ""

    if lang in V6_LANGS:
        if tier in VALID_MODEL_TIERS:
            return {
                "text_detection_model_name": f"PP-OCRv6_{tier}_det",
                "text_recognition_model_name": f"PP-OCRv6_{tier}_rec",
            }
        return {"ocr_version": "PP-OCRv6"}

    # Non ch/en/japan (korean/russian/arabic, latin languages, ...):
    # conservatively fall back to PP-OCRv5.
    rec_name = V5_FALLBACK_REC.get(lang)
    if rec_name:
        return {"ocr_version": "PP-OCRv5", "text_recognition_model_name": rec_name}
    return {"ocr_version": "PP-OCRv5"}


class PaddleOCREngine(BaseOCREngine):
    """PaddleOCR engine adapter.

    Wraps PaddleOCR with the unified BaseOCREngine interface.
    Maintains backward compatibility with all existing ocr_data_dict consumers.
    """

    # NOTE: deliberately NOT a __new__-level singleton. The process-level
    # singleton lives in ocr_engine_manager._engine_instance; build_standalone_engine()
    # must be able to create genuinely independent instances (per refine thread),
    # which a __new__ singleton silently defeats — cleanup() on the "standalone"
    # instance would then unload the shared engine out from under the manager.
    # _predict_lock is likewise per-INSTANCE (created in __init__): a class-level
    # lock would re-serialize the per-thread standalone engines on one shared
    # lock, defeating the parallel refine executor (see refine_executor docs).

    # initialize(lang=...) swaps det/rec models (PP-OCRv6/v5 per-language
    # routing) — the per-ROI language override relies on this to build one
    # engine instance per requested language (see ocr_engine_manager).
    supports_lang_override = True

    @classmethod
    def get_engine_info(cls) -> OCREngineInfo:
        return OCREngineInfo(
            engine_id="paddle",
            name="PaddleOCR",
            version="3.7.0",
            description="PaddleOCR — 百度 PaddleOCR（PP-OCRv6，支持模型档位 tiny/small/medium），支持中/日/韩/英等多语言，准确率高，最稳定",
            supports_gpu=True,
            supports_languages=[
                "ch", "chinese_cht", "en", "japan", "korean", "french",
                "german", "italian", "spanish", "portuguese", "russian",
                "arabic",
            ],
            estimated_speed_rank=3,
            supported_model_tiers=["tiny", "small", "medium"],
        )

    @classmethod
    def is_available(cls) -> bool:
        try:
            import paddleocr  # noqa: F401
            return True
        except ImportError:
            return False

    def __init__(self):
        # Heavy lifting happens in initialize(); a fresh instance starts unloaded.
        self._ocr: Any = None
        self._initialized: bool = False
        # Instance-level lock: guards THIS predictor only (Paddle inference
        # recommends one predictor per thread; sharing one predictor across
        # threads is the unsafe pattern). One predictor per refine thread —
        # see the class-level NOTE above.
        self._predict_lock = threading.Lock()

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
            # Check the paddle.device module BEFORE using it: old PaddlePaddle
            # builds without it would otherwise raise here and skip the whole
            # ROCm/XPU detection below.
            has_device_mod = hasattr(paddle, "device") and hasattr(paddle.device, "cuda")
            if has_device_mod and paddle.is_compiled_with_cuda():
                gpu_count = paddle.device.cuda.device_count()
                if gpu_count > 0:
                    logger.info(f"Runtime GPU check: {gpu_count} CUDA device(s) available via PaddlePaddle.")
                    return "gpu"
            # Also check for other device types
            if has_device_mod:
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
        """Initialize PaddleOCR model (lazy, called once).

        Supported kwargs (delivered via ocr_engine_manager.set_engine(options)):
          lang (str): UI language code ("ch"/"en"/"japan"/"korean"/...). Default "ch".
          model_tier (str|None): "tiny"/"small"/"medium", or None/"auto" for the
              version default models.
          enable_mkldnn (bool): CPU MKLDNN acceleration. Default True.
          cpu_threads (int): CPU inference threads; 0/None means "not passed"
              (let Paddle decide).
          ocr_version (str): Explicit model generation override, e.g.
              "PP-OCRv5"/"PP-OCRv6"; wins over the lang/tier routing.
          device (str): "cpu"/"gpu"; defaults to _get_device_mode().
          det_max_side_px (int): 检测(DBNet)工作分辨率的长边封顶,经
              ``text_det_limit_side_len``/``text_det_limit_type="max"`` 传给
              PaddleOCR。paddleocr 3.7 的 OCR 管线实际生效的 det 缩放是
              64/'min'(对 ≥64px 的输入不缩放),4K 全幅输入会让检测在全
              分辨率上跑、单张推理瞬时 ~2.2GB(实测 3476MB 峰值,1080p 为
              1672MB);封顶后检测输入有界,峰值回落到 1080p 量级,而识别
              裁剪仍取自原图、识别分辨率不受影响。输入长边 ≤ 封顶值时不
              缩放(1080p 全幅 1920、条带裁剪均不变)。默认 2560;0 关闭
              (不传参,保持 paddleocr 缺省行为)。
        """
        if self._initialized:
            return

        from paddleocr import PaddleOCR

        device = kwargs.get("device") or self._get_device_mode()
        lang = str(kwargs.get("lang") or "ch")
        model_tier = kwargs.get("model_tier")
        enable_mkldnn_raw = kwargs.get("enable_mkldnn", True)
        enable_mkldnn = True if enable_mkldnn_raw is None else bool(enable_mkldnn_raw)
        try:
            cpu_threads = int(kwargs.get("cpu_threads") or 0)
        except (TypeError, ValueError):
            cpu_threads = 0
        try:
            det_max_side_px = int(kwargs.get("det_max_side_px", 2560) or 0)
        except (TypeError, ValueError):
            det_max_side_px = 2560

        explicit_version = str(kwargs.get("ocr_version") or "").strip()
        if explicit_version:
            # Explicit override (e.g. benchmark --ocr-version PP-OCRv5): wins over
            # the language routing, still protected by the degradation chain.
            selection = {"ocr_version": explicit_version}
            fallback_version = explicit_version
        else:
            selection = resolve_model_selection(lang, model_tier)
            # Version used by the simplified fallback path (matches language routing).
            fallback_version = "PP-OCRv6" if lang in V6_LANGS else "PP-OCRv5"

        logger.info(
            f"Initializing PaddleOCR (lang={lang}, model_tier={model_tier or 'auto'}, "
            f"selection={selection}, device={device}, "
            f"enable_mkldnn={enable_mkldnn}, cpu_threads={cpu_threads})..."
        )

        # Common switches for every construction path.
        common: Dict[str, Any] = {
            "use_doc_orientation_classify": False,
            "use_doc_unwarping": False,
            "use_textline_orientation": False,
            "lang": lang,
            "device": device,
            "enable_mkldnn": enable_mkldnn,
        }
        if cpu_threads > 0:
            common["cpu_threads"] = cpu_threads
        if det_max_side_px > 0:
            # det 工作分辨率封顶(默认 2560/'max'):检测输入有界,4K 全幅
            # 推理的 ~2.2GB 瞬时开销回落到 1080p 量级;识别裁剪仍取自原图。
            # 输入长边 ≤ 封顶值时不缩放,1080p 及条带裁剪行为不变。
            common["text_det_limit_side_len"] = det_max_side_px
            common["text_det_limit_type"] = "max"

        def _try_construct(extra: Dict[str, Any]) -> Any:
            """Construct PaddleOCR from common switches + extra model selection.

            On TypeError (older paddleocr versions may not accept the
            enable_mkldnn/cpu_threads/text_det_* kwargs), retry once without
            them.
            """
            attempt = dict(common)
            attempt.update(extra)
            try:
                return PaddleOCR(**attempt)
            except TypeError:
                reduced = {
                    k: v for k, v in attempt.items()
                    if k not in ("enable_mkldnn", "cpu_threads",
                                 "text_det_limit_side_len",
                                 "text_det_limit_type")
                }
                if len(reduced) == len(attempt):
                    raise
                logger.warning(
                    "PaddleOCR rejected enable_mkldnn/cpu_threads/text_det_* "
                    "kwargs; retrying without them."
                )
                return PaddleOCR(**reduced)

        # Degradation chain (each level logs a warning):
        #   1. Exact model selection (PP-OCRv6 tier models / v5 multilingual rec).
        #   2. Version-only defaults (ocr_version="PP-OCRv6"/"PP-OCRv5").
        #   3. Minimal (lang + device + doc-preprocessing switches only).
        try:
            self._ocr = _try_construct(selection)
        except Exception as e:
            logger.warning(
                f"PaddleOCR init with exact model selection {selection} failed: {e}. "
                f"Falling back to version-only init (ocr_version={fallback_version})."
            )
            try:
                self._ocr = _try_construct({"ocr_version": fallback_version})
            except Exception as e2:
                logger.warning(
                    f"PaddleOCR version-only init failed: {e2}. "
                    "Falling back to minimal init (lang + device only)."
                )
                try:
                    self._ocr = _try_construct({})
                except Exception as e3:
                    raise RuntimeError(
                        f"Failed to initialize PaddleOCR (lang={lang}, device={device}): {e3}"
                    ) from e3

        self._initialized = True
        logger.info("PaddleOCR initialized successfully.")

    def is_initialized(self) -> bool:
        """True when this instance is ready for predict() calls."""
        return self._initialized and self._ocr is not None

    def predict(self, img_input):
        """Run PaddleOCR prediction (thread-safe)."""
        if self._ocr is None:
            raise RuntimeError("PaddleOCR engine not initialized. Call initialize() first.")
        with self._predict_lock:
            return self._ocr.predict(img_input)

    def predict_batch(self, images: List[Any]) -> List[Any]:
        """Batch prediction using PaddleOCR 3.x native list input (thread-safe).

        Passes the whole list to self._ocr.predict() in one lock acquisition.
        The returned list is aligned with the input images.
        """
        if self._ocr is None:
            raise RuntimeError("PaddleOCR engine not initialized. Call initialize() first.")
        with self._predict_lock:
            return list(self._ocr.predict(images))

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

        # Document-level result format (newer PaddleOCR). Accepts one or more
        # per-image dicts (multiple dicts are concatenated in order).
        if (
            isinstance(raw_result, list)
            and raw_result
            and all(isinstance(x, dict) for x in raw_result)
        ):
            for single_dict in raw_result:
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
