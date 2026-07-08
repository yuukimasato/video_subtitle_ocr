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


def set_engine(engine_id: str) -> None:
    """Switch the current OCR engine.

    The new engine will be lazily initialized on the next call to get_engine().
    Any existing engine instance is cleaned up immediately.
    """
    global _current_engine_id, _engine_instance
    with _engine_lock:
        if _engine_instance is not None:
            try:
                _engine_instance.cleanup()
            except Exception as e:
                logger.warning(f"Error cleaning up OCR engine: {e}")
            _engine_instance = None
        _current_engine_id = engine_id
        logger.info(f"OCR engine switched to: {engine_id}")


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

        _engine_instance = engine_cls()
        _engine_instance.initialize()
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
        frame_time_sec = float(frame_info[4]) if len(frame_info) >= 5 and frame_info[4] is not None else 0.0

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

        # Optional visualization
        if visualize:
            _save_visualization(engine, img_input, ocr_output_dir or work_dir,
                                frame_num, roi_identifier)

        yield (roi_entry_orig, ocr_data_dict, frame_num, roi_identifier, frame_time_sec)


def _save_visualization(engine, img_input, output_dir, frame_num, roi_identifier):
    """Save OCR visualization if the engine supports it."""
    try:
        import tempfile

        if isinstance(img_input, str):
            img_path = img_input
        else:
            import cv2
            temp_dir = tempfile.mkdtemp()
            img_path = os.path.join(temp_dir, f"temp_viz_{frame_num}_{roi_identifier}.jpg")
            cv2.imwrite(img_path, img_input)

        viz_dir = os.path.join(output_dir, "..", "ocr_visualization")
        os.makedirs(viz_dir, exist_ok=True)

        result = engine.predict(img_path)
        if hasattr(result, '__iter__'):
            for res in result:
                if hasattr(res, 'save_to_img'):
                    res.save_to_img(viz_dir)
    except Exception as e:
        logger.debug(f"Visualization skipped: {e}")
