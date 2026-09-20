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


def test_route_chinese_cht_rides_v6_fast_path():
    """繁体中文是 paddleocr 3.7 _PPOCRV6_LANGS 的一等语言——与 ch/en/japan
    同一套 v6 模型，无需回落 v5、无需额外下载模型。"""
    assert resolve_model_selection("chinese_cht", None) == {"ocr_version": "PP-OCRv6"}
    assert resolve_model_selection("chinese_cht", "auto") == {"ocr_version": "PP-OCRv6"}
    assert resolve_model_selection("chinese_cht", "tiny") == {
        "text_detection_model_name": "PP-OCRv6_tiny_det",
        "text_recognition_model_name": "PP-OCRv6_tiny_rec",
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


def test_route_latin_langs_rides_v6_fast_path():
    """拉丁语系是 paddleocr 3.7 _PPOCRV6_LANGS 的成员——与 ch/en/japan
    同一套 v6 模型（已缓存），不必回落 v5（否则要额外下载
    PP-OCRv5_server_det + latin_PP-OCRv5_mobile_rec）。"""
    for lang in ("french", "german", "it", "es", "pt"):
        assert resolve_model_selection(lang, None) == {"ocr_version": "PP-OCRv6"}
        assert resolve_model_selection(lang, "small") == {
            "text_detection_model_name": "PP-OCRv6_small_det",
            "text_recognition_model_name": "PP-OCRv6_small_rec",
        }


def test_route_unknown_legacy_lang_names_not_used():
    """paddleocr 3.7 只认 it/es/pt——旧式名 italian/spanish/portuguese
    不在任何语言族里，初始化直接报「No models are available」
    （回归：下拉框曾用旧式名导致这三个语言必然失败）。"""
    for legacy in ("italian", "spanish", "portuguese"):
        # 未进 v6 快车道：回落 v5 后由 paddleocr 报未知语言，而非静默错模型
        assert resolve_model_selection(legacy, None) == {"ocr_version": "PP-OCRv5"}


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
    assert "chinese_cht" in info.supports_languages
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


# ---------------------------------------------------------------------------
# det 工作分辨率封顶(4K 推理内存优化)
#
# PaddleOCR 3.7 经 paddlex 的 OCR 管线实际生效的 det 缩放是
# limit_side_len=64/limit_type='min'——对 ≥64px 的输入不缩放,DBNet 检测
# 在全分辨率上跑:单张 4K 帧推理瞬时 ~2.2GB(实测 3476MB 峰值 vs 1080p
# 1672MB)。v6 检测模型的既定工作分辨率本就是长边 960('max'),把 det 输入
# 封顶(默认 2560)即可把内存拉回 1080p 量级;rec 裁剪仍取自原图,识别
# 分辨率不受影响。≤封顶值的输入(1080p 全幅、条带裁剪)不缩放,行为不变。
# ---------------------------------------------------------------------------

import sys
import types


def _install_fake_paddleocr(monkeypatch) -> list:
    """伪造 paddleocr 模块,捕获 PaddleOCREngine.initialize 的构造 kwargs。"""
    captured = []

    class FakePaddleOCR:
        def __init__(self, **kwargs):
            captured.append(kwargs)

    mod = types.ModuleType("paddleocr")
    mod.PaddleOCR = FakePaddleOCR
    monkeypatch.setitem(sys.modules, "paddleocr", mod)
    return captured


def test_initialize_det_side_cap_default_2560(monkeypatch):
    captured = _install_fake_paddleocr(monkeypatch)
    engine = PaddleOCREngine()
    engine.initialize(lang="japan")
    kwargs = captured[0]
    assert kwargs["text_det_limit_side_len"] == 2560
    assert kwargs["text_det_limit_type"] == "max"


def test_initialize_det_side_cap_configurable_and_disable(monkeypatch):
    captured = _install_fake_paddleocr(monkeypatch)
    engine = PaddleOCREngine()
    engine.initialize(lang="ch", det_max_side_px=1920)
    assert captured[0]["text_det_limit_side_len"] == 1920
    assert captured[0]["text_det_limit_type"] == "max"

    captured2 = _install_fake_paddleocr(monkeypatch)
    engine2 = PaddleOCREngine()
    engine2.initialize(lang="ch", det_max_side_px=0)
    assert "text_det_limit_side_len" not in captured2[0]
    assert "text_det_limit_type" not in captured2[0]


def test_initialize_det_cap_survives_typeerror_fallback(monkeypatch):
    """旧版 paddleocr 拒绝 det kwargs(TypeError)时,降级重试剥掉它们,
    而不是让整条初始化链失败。"""
    captured = []

    class PickyFakePaddleOCR:
        def __init__(self, **kwargs):
            if "text_det_limit_side_len" in kwargs:
                raise TypeError("unexpected keyword argument")
            captured.append(kwargs)

    mod = types.ModuleType("paddleocr")
    mod.PaddleOCR = PickyFakePaddleOCR
    monkeypatch.setitem(sys.modules, "paddleocr", mod)

    engine = PaddleOCREngine()
    engine.initialize(lang="ch")
    assert captured, "降级后仍应完成构造"
    assert "text_det_limit_side_len" not in captured[0]
