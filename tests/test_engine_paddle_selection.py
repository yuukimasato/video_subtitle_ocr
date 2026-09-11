# tests/test_engine_paddle_selection.py
"""Pure-logic tests for core/ocr_engine_paddle.py — no paddleocr required.

Covers:
- resolve_model_selection routing (PP-OCRv6 fast path, tier models, PP-OCRv5
  multilingual fallback table).
- PaddleOCREngine.get_engine_info() metadata fields.
- PaddleOCREngine.normalize_result() for both raw formats (3.x document-level
  dict / classic line-level) plus empty and malformed inputs.
"""

from __future__ import annotations

import numpy as np

from core.ocr_engine_paddle import (
    PaddleOCREngine,
    resolve_model_selection,
)


# ---------------------------------------------------------------------------
# resolve_model_selection routing
# ---------------------------------------------------------------------------

def test_route_ch_auto_uses_v6_default():
    assert resolve_model_selection("ch", None) == {"ocr_version": "PP-OCRv6"}
    assert resolve_model_selection("ch", "auto") == {"ocr_version": "PP-OCRv6"}
    assert resolve_model_selection("ch", "") == {"ocr_version": "PP-OCRv6"}


def test_route_en_tiny_uses_v6_tier_models():
    assert resolve_model_selection("en", "tiny") == {
        "text_detection_model_name": "PP-OCRv6_tiny_det",
        "text_recognition_model_name": "PP-OCRv6_tiny_rec",
    }


def test_route_japan_medium_uses_v6_medium_models():
    assert resolve_model_selection("japan", "medium") == {
        "text_detection_model_name": "PP-OCRv6_medium_det",
        "text_recognition_model_name": "PP-OCRv6_medium_rec",
    }


def test_route_ch_small_uses_v6_small_models():
    assert resolve_model_selection("ch", "small") == {
        "text_detection_model_name": "PP-OCRv6_small_det",
        "text_recognition_model_name": "PP-OCRv6_small_rec",
    }


def test_route_korean_falls_back_to_v5_korean_rec():
    assert resolve_model_selection("korean", None) == {
        "ocr_version": "PP-OCRv5",
        "text_recognition_model_name": "korean_PP-OCRv5_mobile_rec",
    }


def test_route_russian_falls_back_to_cyrillic_rec():
    assert resolve_model_selection("russian", "tiny") == {
        "ocr_version": "PP-OCRv5",
        "text_recognition_model_name": "cyrillic_PP-OCRv5_mobile_rec",
    }


def test_route_arabic_falls_back_to_arabic_rec():
    assert resolve_model_selection("arabic", None) == {
        "ocr_version": "PP-OCRv5",
        "text_recognition_model_name": "arabic_PP-OCRv5_mobile_rec",
    }


def test_route_french_falls_back_to_v5_only():
    # Latin languages not in the fallback table rely on paddleocr's own mapping.
    assert resolve_model_selection("french", None) == {"ocr_version": "PP-OCRv5"}
    assert resolve_model_selection("french", "medium") == {"ocr_version": "PP-OCRv5"}


def test_route_none_lang_defaults_to_ch():
    assert resolve_model_selection(None, None) == {"ocr_version": "PP-OCRv6"}
    assert resolve_model_selection("", None) == {"ocr_version": "PP-OCRv6"}


def test_route_tier_is_case_insensitive():
    assert resolve_model_selection("en", "TINY") == {
        "text_detection_model_name": "PP-OCRv6_tiny_det",
        "text_recognition_model_name": "PP-OCRv6_tiny_rec",
    }
    assert resolve_model_selection("en", " Auto ") == {"ocr_version": "PP-OCRv6"}


# ---------------------------------------------------------------------------
# Engine metadata
# ---------------------------------------------------------------------------

def test_get_engine_info_fields():
    info = PaddleOCREngine.get_engine_info()
    assert info.engine_id == "paddle"
    assert info.version == "3.7.0"
    assert info.supports_gpu is True
    assert list(info.supported_model_tiers) == ["tiny", "small", "medium"]
    assert "ch" in info.supports_languages
    assert "japan" in info.supports_languages


# ---------------------------------------------------------------------------
# normalize_result — document-level dict format (PaddleOCR 3.x)
# ---------------------------------------------------------------------------

def _poly():
    return [[0.0, 0.0], [100.0, 0.0], [100.0, 30.0], [0.0, 30.0]]


def test_normalize_document_level_dict():
    raw = [
        {
            "rec_texts": ["你好世界", "第二行"],
            "rec_scores": [0.98, 0.87],
            "rec_polys": [np.array(_poly(), dtype=np.float32), _poly()],
            "rec_boxes": [np.array([0, 0, 100, 30]), [5, 5, 90, 25]],
        }
    ]
    data = PaddleOCREngine().normalize_result(raw)

    assert data["rec_texts"] == ["你好世界", "第二行"]
    assert data["rec_scores"] == [0.98, 0.87]
    assert len(data["rec_polys"]) == 2
    assert len(data["dt_polys"]) == 2
    assert len(data["rec_boxes"]) == 2
    # numpy arrays must be converted to plain lists.
    assert isinstance(data["rec_polys"][0], list)
    assert data["rec_polys"][0] == _poly()
    assert data["rec_boxes"][0] == [0, 0, 100, 30]


def test_normalize_document_level_truncates_to_min_length():
    # Mismatched lengths: normalize must truncate to the shortest list.
    raw = [
        {
            "rec_texts": ["A", "B", "C"],
            "rec_scores": [0.9, 0.8],
            "rec_polys": [_poly()],
            "rec_boxes": [[0, 0, 1, 1], [2, 2, 3, 3], [4, 4, 5, 5]],
        }
    ]
    data = PaddleOCREngine().normalize_result(raw)
    assert data["rec_texts"] == ["A"]
    assert data["rec_scores"] == [0.9]
    assert len(data["rec_polys"]) == 1
    assert len(data["rec_boxes"]) == 1


def test_normalize_empty_result():
    data = PaddleOCREngine().normalize_result([])
    assert data == {
        "dt_polys": [],
        "rec_polys": [],
        "rec_texts": [],
        "rec_scores": [],
        "rec_boxes": [],
    }
    assert PaddleOCREngine().normalize_result(None) == data


# ---------------------------------------------------------------------------
# normalize_result — line-level legacy format
# ---------------------------------------------------------------------------

def test_normalize_line_level_legacy():
    box = [[10, 20], [110, 20], [110, 50], [10, 50]]
    raw = [
        (box, ("你好", 0.95)),
        (box, ("世界", 0.7)),
    ]
    data = PaddleOCREngine().normalize_result(raw)

    assert data["rec_texts"] == ["你好", "世界"]
    assert data["rec_scores"] == [0.95, 0.7]
    assert data["dt_polys"] == [box, box]
    assert data["rec_polys"] == [box, box]
    # Axis-aligned bounding boxes are derived from the polygon.
    assert data["rec_boxes"] == [[10, 20, 110, 50], [10, 20, 110, 50]]


def test_normalize_malformed_lines_are_skipped():
    box = [[0, 0], [10, 0], [10, 10], [0, 10]]
    raw = [
        (box, ("good", 0.9)),
        "garbage",                       # not a tuple/list pair
        (box, "not-a-tuple"),            # second element not a tuple
        ((1, 2, 3), ("bad", 0.5)),       # first element not a list
    ]
    data = PaddleOCREngine().normalize_result(raw)
    assert data["rec_texts"] == ["good"]
    assert data["rec_scores"] == [0.9]
    assert len(data["rec_boxes"]) == 1
