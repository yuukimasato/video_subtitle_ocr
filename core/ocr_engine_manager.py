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
        ):
            return
        if _engine_instance is not None:
            try:
                _engine_instance.cleanup()
            except Exception as e:
                logger.warning(f"Error cleaning up OCR engine: {e}")
            _engine_instance = None
        _current_engine_id = engine_id
        _engine_options = new_options
        logger.info(f"OCR engine switched to: {engine_id} (options={_engine_options})")


def get_engine() -> BaseOCREngine:
    """Get the current OCR engine instance (lazy initialization).

    Returns the process-level singleton engine instance, initializing it
    if necessary. Thread-safe with double-check locking.
    """
    global _engine_instance, _current_engine_id

    if _engine_instance is not None:
        return _engine_instance

    with _engine_lock:
        if _engine_instance is not None:
            return _engine_instance

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


def get_current_engine_id() -> str:
    """Get the currently selected engine ID (may not be initialized yet)."""
    return _current_engine_id or OCREngineRegistry.get_default()


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
    engine = get_engine()

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

        # Run OCR through the current engine
        raw_result = engine.predict(img_input)
        ocr_data_dict = engine.normalize_result(raw_result)

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
