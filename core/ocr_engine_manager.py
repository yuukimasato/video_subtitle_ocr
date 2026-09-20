# core/ocr_engine_manager.py
"""
OCR Engine Manager.

Provides the unified entry point for OCR operations. Handles engine selection,
lazy initialization, and engine switching. Replaces the direct PaddleOCR calls
in ocr_processor.py with an engine-agnostic interface.
"""

from __future__ import annotations

import os
import json
import logging
import threading
from typing import Iterator, Tuple, Dict, Any, Optional

from core.ocr_engine_base import BaseOCREngine, OCREngineRegistry

logger = logging.getLogger(__name__)

# Process-level singleton state
_engine_instance: Optional[BaseOCREngine] = None
_engine_lock = threading.Lock()
_current_engine_id: Optional[str] = None
# Engine initialization options saved by set_engine() and applied (as kwargs)
# in get_engine() when the engine instance is lazily initialized.
_engine_options: Dict[str, Any] = {}
# Per-language engine instances for the per-ROI language override
# (roi["ocr_lang"]). Key: (engine_id, sorted options items with lang
# overridden). Bilingual subtitles need two recognition models loaded side by
# side; switching the singleton per frame would thrash model loads instead.
_lang_engines: Dict[Any, BaseOCREngine] = {}


def _cleanup_lang_engines() -> None:
    """Drop and clean up every cached per-language engine (lock held)."""
    global _lang_engines
    for engine in _lang_engines.values():
        try:
            engine.cleanup()
        except Exception as e:
            logger.warning(f"Error cleaning up per-language OCR engine: {e}")
    _lang_engines = {}


def roi_ocr_lang(roi_entry: Any) -> str:
    """Read the per-ROI language override from an ROI dict ('' = follow global)."""
    if not isinstance(roi_entry, dict):
        return ""
    try:
        return str(roi_entry.get("ocr_lang") or "").strip()
    except Exception:
        return ""


def _engine_is_ready(engine: BaseOCREngine) -> bool:
    """Best-effort readiness probe; engines without is_initialized() pass."""
    try:
        return bool(engine.is_initialized())
    except Exception:
        return True


def set_engine(engine_id: str, options: Optional[Dict[str, Any]] = None) -> None:
    """Switch the current OCR engine.

    The new engine will be lazily initialized on the next call to get_engine().
    Any existing engine instance is cleaned up immediately.

    Idempotent: requesting the engine that is already active (same id and
    options) keeps the initialized instance instead of tearing it down —
    OcrOptimizer calls this on its first OCR call, and reloading the model
    for every optimizer instance would dominate runtime.

    Args:
        engine_id: Engine identifier from the registry (e.g. "paddle", "rapid").
        options: Engine initialization options (e.g. lang, model_tier), passed
            as kwargs to the engine's initialize(). None clears saved options.
    """
    global _current_engine_id, _engine_instance, _engine_options
    new_options = dict(options) if options else {}
    with _engine_lock:
        if (
            _engine_instance is not None
            and _current_engine_id == engine_id
            and _engine_options == new_options
            and _engine_is_ready(_engine_instance)
        ):
            return
        if _engine_instance is not None:
            try:
                _engine_instance.cleanup()
            except Exception as e:
                logger.warning(f"Error cleaning up OCR engine: {e}")
            _engine_instance = None
        # Per-language engines were built for the previous engine/options;
        # they no longer match and would silently keep stale models alive.
        _cleanup_lang_engines()
        _current_engine_id = engine_id
        _engine_options = new_options
        logger.info(f"OCR engine switched to: {engine_id} (options={_engine_options})")


def get_engine() -> BaseOCREngine:
    """Get the current OCR engine instance (lazy initialization).

    Returns the process-level singleton engine instance, initializing it
    if necessary. Thread-safe with double-check locking.
    """
    global _engine_instance, _current_engine_id

    if _engine_instance is not None and _engine_is_ready(_engine_instance):
        return _engine_instance
    if _engine_instance is not None:
        logger.warning(
            "Cached OCR engine was cleaned up externally; re-initializing it."
        )

    with _engine_lock:
        if _engine_instance is not None:
            if _engine_is_ready(_engine_instance):
                return _engine_instance
            # Cached instance is dead (e.g. cleanup() called on it through a
            # reference the manager doesn't control): drop it so the rebuild
            # below creates a freshly initialized engine instead of handing
            # out an object whose predict() would raise.
            logger.warning("Rebuilding cleaned-up OCR engine '%s'.", _current_engine_id)
            _engine_instance = None

        engine_id = _current_engine_id or OCREngineRegistry.get_default()
        if not engine_id:
            raise RuntimeError(
                "No OCR engine available. Please install at least one OCR engine "
                "(e.g., PaddleOCR: pip install paddleocr)."
            )

        engine_cls = OCREngineRegistry.get(engine_id)
        if engine_cls is None:
            raise RuntimeError(
                f"OCR engine '{engine_id}' is not registered or not available. "
                f"Available engines: {[e.engine_id for e in OCREngineRegistry.list_available()]}"
            )

        engine = engine_cls()
        try:
            engine.initialize(**_engine_options)
        except Exception:
            # Never leave a half-initialized singleton behind: the next
            # get_engine() must retry initialization instead of returning a
            # broken instance forever.
            try:
                engine.cleanup()
            except Exception:
                pass
            raise
        _engine_instance = engine
        return _engine_instance


def get_engine_for_lang(lang: Optional[str]) -> BaseOCREngine:
    """Get an engine instance for ``lang`` (per-ROI language override).

    Empty/None ``lang`` (or the language already selected via set_engine)
    returns the normal process singleton — the default "auto (follow global)"
    path adds no extra model loads. Any other language builds (once) and
    caches a dedicated engine whose initialize() receives the global options
    with ``lang`` overridden, so bilingual runs keep both recognition models
    resident instead of reloading per frame.

    Thread-safe with the same lock as get_engine(); cached instances are
    dropped when set_engine() switches engine or options.
    """
    lang = str(lang or "").strip()
    # Whether the request can share the singleton; decided under the lock but
    # get_engine() itself must be called OUTSIDE it (it takes the same
    # non-reentrant lock when the singleton needs lazy initialization).
    share_singleton = True
    with _engine_lock:
        engine_id = _current_engine_id or OCREngineRegistry.get_default()
        if lang and lang != str(_engine_options.get("lang") or "ch"):
            engine_cls = OCREngineRegistry.get(engine_id) if engine_id else None
            if engine_cls is None:
                raise RuntimeError(
                    f"OCR engine '{engine_id}' is not registered or not available. "
                    f"Available engines: {[e.engine_id for e in OCREngineRegistry.list_available()]}"
                )
            if getattr(engine_cls, "supports_lang_override", False):
                if not engine_id:
                    raise RuntimeError(
                        "No OCR engine available. Please install at least one OCR engine "
                        "(e.g., PaddleOCR: pip install paddleocr)."
                    )
                share_singleton = False
                options = dict(_engine_options)
                options["lang"] = lang
                key = (engine_id, tuple(sorted(options.items())))
                cached = _lang_engines.get(key)
                if cached is not None and _engine_is_ready(cached):
                    return cached
                engine = engine_cls()
                try:
                    engine.initialize(**options)
                except Exception:
                    try:
                        engine.cleanup()
                    except Exception:
                        pass
                    raise
                _lang_engines[key] = engine
                logger.info(f"Initialized per-language OCR engine for lang={lang} ({engine_id}).")
                return engine
    if share_singleton:
        # Empty lang, or the engine ignores initialize(lang=...) (single
        # multilingual model — a dedicated instance would load identical
        # models for nothing), or the language is the singleton's own.
        return get_engine()


def get_current_engine_id() -> str:
    """Get the currently selected engine ID (may not be initialized yet)."""
    return _current_engine_id or OCREngineRegistry.get_default()


def is_engine_initialized() -> bool:
    """True when an engine instance is already live in this process."""
    return _engine_instance is not None


def build_standalone_engine(engine_id: Optional[str], options: Optional[Dict[str, Any]] = None) -> "BaseOCREngine":
    """Build a fresh, INDEPENDENT engine instance (not the process singleton).

    Used by the parallel boundary-refinement executor, where each worker
    thread needs its own engine so OCR calls don't serialize on the
    singleton's ``_predict_lock``. Caller owns the instance and must call
    ``cleanup()`` when done.
    """
    resolved = (
        engine_id if engine_id and engine_id != "auto"
        else OCREngineRegistry.get_default()
    )
    if not resolved:
        raise RuntimeError(
            "No OCR engine available. Please install at least one OCR engine "
            "(e.g., PaddleOCR: pip install paddleocr)."
        )
    engine_cls = OCREngineRegistry.get(resolved)
    if engine_cls is None:
        raise RuntimeError(
            f"OCR engine '{resolved}' is not registered or not available. "
            f"Available engines: {[e.engine_id for e in OCREngineRegistry.list_available()]}"
        )
    engine = engine_cls()
    engine.initialize(**(options or {}))
    return engine


def run_batch_ocr(
    frames_iter: Iterator[Tuple],
    work_dir: str,
    visualize: bool = False,
    save_json: bool = True,
) -> Iterator[Tuple[Dict, Dict[str, Any], int, str, float]]:
    """Unified batch OCR entry point.

    Replaces ocr_processor.run_batch_ocr(). Fully compatible with existing
    callers — same input/output types. Internally delegates to the current
    engine via the engine manager.

    Args:
        frames_iter: Iterator of (roi_entry, img_input, frame_num, roi_identifier[, frame_time_sec])
        work_dir: Working directory for JSON output
        visualize: Whether to save visualization images
        save_json: Whether to save OCR results as JSON files

    Yields:
        (roi_entry, ocr_data_dict, frame_num, roi_identifier, frame_time_sec)
    """
    engine = None

    ocr_output_dir = None
    if save_json:
        ocr_output_dir = os.path.join(work_dir, "2_ocr_results")
        os.makedirs(ocr_output_dir, exist_ok=True)

    for frame_info in frames_iter:
        # Compatible tuple unpacking:
        # (roi_entry, img_input, frame_num, roi_identifier[, frame_time_sec])
        roi_entry_orig = frame_info[0]
        img_input = frame_info[1]
        frame_num = frame_info[2]
        roi_identifier = frame_info[3]
        try:
            frame_time_sec = float(frame_info[4]) if len(frame_info) >= 5 and frame_info[4] is not None else 0.0
        except (TypeError, ValueError):
            frame_time_sec = 0.0

        # Per-ROI language override: a roi dict carrying "ocr_lang" routes to
        # that language's engine (cached; "" follows the global selection).
        # The singleton itself is resolved lazily so a run whose ROIs all
        # carry explicit languages doesn't load an unused global model.
        roi_lang = roi_ocr_lang(roi_entry_orig)
        if roi_lang:
            frame_engine = get_engine_for_lang(roi_lang)
        else:
            if engine is None:
                engine = get_engine()
            frame_engine = engine

        # Run OCR through the current engine
        raw_result = frame_engine.predict(img_input)
        ocr_data_dict = frame_engine.normalize_result(raw_result)

        # Optionally save JSON
        if ocr_output_dir:
            is_path = isinstance(img_input, str)
            if is_path:
                base_name = f"{os.path.splitext(os.path.basename(img_input))[0]}_{roi_identifier}"
            else:
                base_name = f"frame_{frame_num:06d}_{roi_identifier}"
            json_path = os.path.join(ocr_output_dir, f"{base_name}.json")
            try:
                with open(json_path, "w", encoding="utf-8") as f:
                    json.dump(ocr_data_dict, f, ensure_ascii=False, indent=2)
            except Exception as e:
                logger.warning(f"Failed to save OCR JSON to {json_path}: {e}")

        # Optional visualization (reuses the raw predict result — no second
        # OCR pass, no lossy JPEG round-trip)
        if visualize:
            _save_visualization(raw_result, work_dir, frame_num, roi_identifier)

        yield (roi_entry_orig, ocr_data_dict, frame_num, roi_identifier, frame_time_sec)


def _save_visualization(raw_result, work_dir: str, frame_num: int, roi_identifier: str):
    """Save OCR visualization from an existing predict() result, if the engine
    supports it (PaddleOCR-style results expose save_to_img)."""
    try:
        if not hasattr(raw_result, "__iter__"):
            return
        viz_dir = os.path.join(work_dir, "ocr_visualization")
        os.makedirs(viz_dir, exist_ok=True)
        saved = False
        for res in raw_result:
            if hasattr(res, "save_to_img"):
                res.save_to_img(viz_dir)
                saved = True
        if not saved:
            logger.debug(f"No visualization produced for frame {frame_num} ({roi_identifier})")
    except Exception as e:
        logger.debug(f"Visualization skipped: {e}")
