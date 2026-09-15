# tests/test_watermark_filter.py
"""Unit tests for core/watermark_filter.py."""

from __future__ import annotations


from core.watermark_filter import WatermarkFilter, build_watermark_filter


ENTRIES = [
    {"text": "示例水印", "bbox": (200.0, 30.0, 300.0, 42.0)},
    {"text": "TV Station", "bbox": (20.0, 20.0, 90.0, 34.0)},
]


def test_disabled_or_empty_config_returns_none():
    assert build_watermark_filter(None) is None
    assert build_watermark_filter({}) is None
    assert build_watermark_filter({"enabled": False, "entries": ENTRIES}) is None
    assert build_watermark_filter({"enabled": True, "entries": []}) is None


def test_exact_text_match_drops_line():
    watermark_filter = build_watermark_filter({"enabled": True, "entries": ENTRIES})
    hit, reason = watermark_filter.should_drop("示例水印", None)
    assert hit and reason == "text"


def test_normalization_tolerates_spaces_and_case():
    watermark_filter = build_watermark_filter({"enabled": True, "entries": ENTRIES})
    hit, reason = watermark_filter.should_drop("T V  station", None)
    assert hit and reason == "text"


def test_substring_match_requires_min_length():
    watermark_filter = build_watermark_filter({"enabled": True, "entries": ENTRIES})
    # "水印" is only 2 chars → no substring matching → normal subtitle survives
    hit, _ = watermark_filter.should_drop("今天的水印格外的明显", None)
    assert not hit
    # ≥3 chars substring of the watermark text matches
    hit, _ = watermark_filter.should_drop("看示例水印的说明", None)
    assert hit


def test_normal_subtitle_text_survives():
    watermark_filter = build_watermark_filter({"enabled": True, "entries": ENTRIES})
    hit, _ = watermark_filter.should_drop("今天天气真不错", None)
    assert not hit


def test_box_center_containment_drops_line():
    watermark_filter = build_watermark_filter({"enabled": True, "entries": ENTRIES})
    # Line whose center falls inside the watermark box (with tolerance).
    hit, reason = watermark_filter.should_drop("完全不同的文本", (210.0, 32.0, 280.0, 44.0))
    assert hit and reason == "box"
    # Far away box survives.
    hit, _ = watermark_filter.should_drop("完全不同的文本", (600.0, 500.0, 900.0, 530.0))
    assert not hit


def test_invalid_entries_are_ignored():
    watermark_filter = WatermarkFilter([None, {}, {"text": ""}, {"bbox": "bad"}])
    assert not watermark_filter.enabled
