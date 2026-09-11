# tests/test_regression_fixes.py
"""Regression tests for the code-review fixes.

Covers:
- utils/time_utils: fractional-second digit handling (".5" == 500ms) and
  rounding carry in format_time.
- core/roi_extractor.get_roi_frame_number: "MM:SS.mmm" strings must not
  crash the float() fallback.
- core/ocr_engine_manager: a failed initialize() must not leave a broken
  singleton behind.
- core/video_type_detector: multi-font score on BGR regions, cut frequency
  denominator, tail-preserving sampling.
- core/subtitle_llm_polish: unvalidated polish output must be discarded;
  _post_chat forwards timeout_sec to call_llm.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import numpy as np
import pytest

from utils.time_utils import format_time, parse_time


# ── utils/time_utils ────────────────────────────────────────────

@pytest.mark.parametrize(
    "value,expected",
    [
        ("00:00:01.5", 1.5),      # ".5" means 500ms, not 5ms
        ("00:00:01.05", 1.05),    # ".05" means 50ms, not 5ms
        ("00:00:01.500", 1.5),
        ("01:05.5", 65.5),        # MM:SS.mmm form
        ("1.5", 1.5),
        ("00:01:00", 60.0),
    ],
)
def test_parse_time_fraction_digits(value, expected):
    assert parse_time(value) == pytest.approx(expected)


def test_parse_time_invalid_raises():
    with pytest.raises(ValueError):
        parse_time("not a time")


def test_format_time_rounding_carry():
    assert format_time(59.9999) == "00:01:00.000"
    assert format_time(0.9995) == "00:00:01.000"
    assert format_time(-1) == "00:00:00.000"


def test_format_parse_roundtrip():
    for secs in (0.0, 1.5, 61.05, 3671.999, 7200.123):
        assert parse_time(format_time(secs)) == pytest.approx(secs, abs=1e-3)


# ── core/roi_extractor.get_roi_frame_number ─────────────────────

def test_get_roi_frame_number_mmss_string():
    from core.roi_extractor import get_roi_frame_number

    fps = 25.0
    # Old code fell through to float("00:12.5") -> ValueError.
    assert get_roi_frame_number({"start_time": "00:12.5"}, fps, "start_time", "start_frame") == int(12.5 * fps)


def test_get_roi_frame_number_fraction_digits():
    from core.roi_extractor import get_roi_frame_number

    fps = 25.0
    # ".5" = 500ms: 1.5s * 25fps = 37 frames (old bug gave 5ms -> 0 frames).
    assert get_roi_frame_number({"start_time": "00:00:01.5"}, fps, "start_time", "start_frame") == 37


def test_get_roi_frame_number_frame_key_wins():
    from core.roi_extractor import get_roi_frame_number

    entry = {"start_time": "00:00:09.9", "start_frame": 5}
    assert get_roi_frame_number(entry, 25.0, "start_time", "start_frame") == 5


# ── core/ocr_engine_manager: init failure recovery ──────────────

def test_get_engine_init_failure_does_not_poison_singleton(monkeypatch):
    from core import ocr_engine_manager as mgr
    from core.ocr_engine_base import BaseOCREngine, OCREngineInfo, OCREngineRegistry

    class OkayEngine(BaseOCREngine):
        @classmethod
        def get_engine_info(cls):
            return OCREngineInfo("ok", "OK", "0", "test", False)

        @classmethod
        def is_available(cls):
            return True

        def initialize(self, **kwargs):
            pass

        def predict(self, img_input):
            return []

        def normalize_result(self, raw_result):
            return {}

        def cleanup(self):
            pass

    class BrokenEngine(OkayEngine):
        broken_cleanups = 0

        @classmethod
        def get_engine_info(cls):
            return OCREngineInfo("broken", "Broken", "0", "test", False)

        def initialize(self, **kwargs):
            raise RuntimeError("model download failed")

        def cleanup(self):
            BrokenEngine.broken_cleanups += 1

    monkeypatch.setattr(OCREngineRegistry, "get", classmethod(lambda cls, eid: BrokenEngine))
    mgr.set_engine("broken")

    with pytest.raises(RuntimeError, match="model download failed"):
        mgr.get_engine()
    # No half-initialized instance may survive for later get_engine() calls.
    assert mgr._engine_instance is None
    assert BrokenEngine.broken_cleanups == 1

    # Recovery: switching to a working engine succeeds afterwards.
    monkeypatch.setattr(OCREngineRegistry, "get", classmethod(lambda cls, eid: OkayEngine))
    mgr.set_engine("ok")
    engine = mgr.get_engine()
    assert isinstance(engine, OkayEngine)


# ── core/video_type_detector ────────────────────────────────────

def _striped_region(h: int, w: int, fill: int) -> np.ndarray:
    region = np.full((h, w, 3), fill, dtype=np.uint8)
    region[:, ::4] = 0  # alternating strokes so run-lengths exist
    return region


def test_compute_multi_font_score_bgr_regions_nonzero():
    from core.video_type_detector import compute_multi_font_score

    regions = [
        _striped_region(20, 40, 200),
        _striped_region(40, 40, 120),
        _striped_region(60, 40, 60),
    ]
    # Old code did float(region.mean(axis=(0,1))) which raises TypeError on
    # BGR input and was silently swallowed -> score was always 0.
    assert compute_multi_font_score(regions) > 0.0


def test_extract_motion_features_uses_real_sample_span():
    from core.video_type_detector import extract_motion_features

    rng = np.random.RandomState(0)
    frames = [rng.randint(0, 255, (240, 320, 3), dtype=np.uint8) for _ in range(4)]
    _, _, cut_count, cut_freq, _ = extract_motion_features(frames, fps=25.0, sample_span_sec=40.0)
    # Frequency must be computed against the real span (0/N cuts over 40s),
    # not len(frames)/fps (which would inflate it by orders of magnitude).
    assert cut_freq == pytest.approx(cut_count / 40.0)


def test_build_sample_frame_list_keeps_tail():
    from core.video_type_detector import build_sample_frame_list

    total = 100_000
    samples = build_sample_frame_list(total, duration_sec=total / 25.0)
    assert len(samples) <= 30
    assert samples[-1] == total - 1  # credits frames must survive resampling
    assert samples == sorted(samples)


# ── core/subtitle_llm_polish ────────────────────────────────────

def _polish_cfg():
    from core.subtitle_llm_polish import SubtitlePolisherConfig

    return SubtitlePolisherConfig(api_key="test-key", batch_size=10)


def test_polish_discards_unvalidated_output(monkeypatch):
    from core import subtitle_llm_polish as sp

    originals = ["你好世界你好世界你好", "第二行字幕内容举例这里"]

    def bad_post_chat(cfg, messages, **kwargs):
        # Valid JSON but missing id "1" -> validation must fail every round.
        return json.dumps({"lines": [{"id": "0", "text": "被改写的第一行文本"}]})

    monkeypatch.setattr(sp, "_post_chat", bad_post_chat)
    out = sp.polish_subtitle_texts(originals, _polish_cfg())
    assert out == originals  # unvalidated output must not be applied


def test_polish_applies_validated_output(monkeypatch):
    from core import subtitle_llm_polish as sp

    originals = ["你好世界你好世界你好", "第二行字幕内容举例这里"]

    def good_post_chat(cfg, messages, **kwargs):
        return json.dumps(
            {"lines": [
                {"id": "0", "text": "你好世界你好世界你好"},
                {"id": "1", "text": "第二行字幕内容举例这里"},
            ]}
        )

    monkeypatch.setattr(sp, "_post_chat", good_post_chat)
    out = sp.polish_subtitle_texts(originals, _polish_cfg())
    assert out == originals  # identical text still counts as a valid result


def test_post_chat_forwards_timeout(monkeypatch):
    from core import subtitle_llm_polish as sp

    captured: dict = {}

    def fake_call_llm(*args, **kwargs):
        captured.update(kwargs)
        content = json.dumps(
            {"lines": [{"id": "0", "text": "你好世界你好世界你好"}]}
        )
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=content))])

    monkeypatch.setattr(sp, "call_llm", fake_call_llm)
    sp._post_chat(_polish_cfg(), [{"role": "user", "content": "x"}], timeout_sec=7.5)
    # timeout_sec must actually reach the LLM client (previously ignored).
    assert captured.get("timeout") == 7.5
