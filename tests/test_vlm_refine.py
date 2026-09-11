# tests/test_vlm_refine.py
"""Unit tests for core/vlm_refine.py (VLM 难帧兜底) and the OcrOptimizer hook.

Offline coverage:
- is_configured() env-var gate (VLM_REFINE_BASE_URL / VLM_REFINE_API_KEY).
- refine_subtitle_text(): request assembly (JPEG data URLs, model, temperature 0),
  response parsing (json / fence / json_repair), and every failure path -> None.
- OcrOptimizer hook: hard-frame detection (votes below half / low confidence),
  text replacement, exception isolation, and the auto-enable gate.

The LLM transport is neutralized by monkeypatching ``core.llm_client.call_llm``
(no network access); optimizer tests monkeypatch
``core.vlm_refine.refine_subtitle_text`` and use a fake OCR engine.
"""

from __future__ import annotations

import numpy as np
import pytest
from types import SimpleNamespace

import core.llm_client as llm_client
import core.vlm_refine as vlm_refine
from core import ocr_engine_manager
from core.ocr_optimizer import OcrOptimizer
from core.vlm_refine import is_configured, refine_subtitle_text


def _fake_response(content: str):
    choice = SimpleNamespace(message=SimpleNamespace(content=content))
    return SimpleNamespace(choices=[choice])


def make_image(seed: int = 0) -> np.ndarray:
    return np.full((240, 320, 3), 100 + seed, dtype=np.uint8)


# ---------------------------------------------------------------------------
# a) is_configured env-var gate
# ---------------------------------------------------------------------------

def test_is_configured_requires_both_env_vars(monkeypatch):
    for name in ("VLM_REFINE_BASE_URL", "VLM_REFINE_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    assert is_configured() is False

    monkeypatch.setenv("VLM_REFINE_BASE_URL", "http://localhost:8000/v1")
    assert is_configured() is False  # key missing

    monkeypatch.setenv("VLM_REFINE_API_KEY", "sk-test")
    assert is_configured() is True

    monkeypatch.setenv("VLM_REFINE_BASE_URL", "   ")  # blank counts as unset
    assert is_configured() is False

    monkeypatch.setenv("VLM_REFINE_BASE_URL", "http://localhost:8000/v1")
    monkeypatch.setenv("VLM_REFINE_API_KEY", "")
    assert is_configured() is False


# ---------------------------------------------------------------------------
# b) refine_subtitle_text happy paths
# ---------------------------------------------------------------------------

def test_refine_returns_parsed_list_and_builds_vision_request(monkeypatch):
    recorded = {}

    def fake_call_llm(messages, model, temperature, **kwargs):
        recorded["messages"] = messages
        recorded["model"] = model
        recorded["temperature"] = temperature
        return _fake_response('["字幕甲", "字幕乙"]')

    monkeypatch.setattr(llm_client, "call_llm", fake_call_llm)

    images = [make_image(0), make_image(1)]
    result = refine_subtitle_text(images, [["字幕甲", "字幕乙"], ["字幕丙"]])

    assert result == ["字幕甲", "字幕乙"]
    assert recorded["model"] == "HunyuanOCR"
    assert recorded["temperature"] == 0

    system, user = recorded["messages"]
    assert system["role"] == "system"
    assert user["role"] == "user"
    parts = user["content"]
    assert parts[0]["type"] == "text"
    assert "字幕甲" in parts[0]["text"] and "字幕丙" in parts[0]["text"]
    image_parts = [p for p in parts if p["type"] == "image_url"]
    assert len(image_parts) == 2
    for part, seed in zip(image_parts, (0, 1)):
        url = part["image_url"]["url"]
        assert url.startswith("data:image/jpeg;base64,")


def test_refine_uses_model_env_override(monkeypatch):
    recorded = {}

    def fake_call_llm(messages, model, temperature, **kwargs):
        recorded["model"] = model
        return _fake_response('["甲"]')

    monkeypatch.setattr(llm_client, "call_llm", fake_call_llm)
    monkeypatch.setenv("VLM_REFINE_MODEL", "my-vlm")

    assert refine_subtitle_text([make_image()], [["甲", "乙"]]) == ["甲"]
    assert recorded["model"] == "my-vlm"


def test_refine_parses_json_code_fence(monkeypatch):
    monkeypatch.setattr(
        llm_client, "call_llm", lambda *a, **k: _fake_response('```json\n["甲", "乙"]\n```')
    )
    assert refine_subtitle_text([make_image()], [["甲"], ["乙"]]) == ["甲", "乙"]


def test_refine_repairs_broken_json_via_json_repair(monkeypatch):
    # Trailing comma: json.loads fails, json_repair succeeds.
    monkeypatch.setattr(
        llm_client, "call_llm", lambda *a, **k: _fake_response('["字幕甲", "字幕乙",]')
    )
    assert refine_subtitle_text([make_image()], [["甲"], ["乙"]]) == ["字幕甲", "字幕乙"]


def test_refine_strips_whitespace_from_items(monkeypatch):
    monkeypatch.setattr(
        llm_client, "call_llm", lambda *a, **k: _fake_response('["  字幕甲 ", "字幕乙"]')
    )
    assert refine_subtitle_text([make_image()], [["甲"], ["乙"]]) == ["字幕甲", "字幕乙"]


# ---------------------------------------------------------------------------
# c) refine_subtitle_text failure paths -> None
# ---------------------------------------------------------------------------

def test_refine_empty_inputs_return_none_without_calling_llm(monkeypatch):
    def boom(*a, **k):
        raise AssertionError("call_llm must not be called for empty inputs")

    monkeypatch.setattr(llm_client, "call_llm", boom)
    assert refine_subtitle_text([], [["甲"]]) is None
    assert refine_subtitle_text([make_image()], []) is None


def test_refine_garbage_response_returns_none(monkeypatch):
    monkeypatch.setattr(
        llm_client, "call_llm", lambda *a, **k: _fake_response("抱歉，我无法处理该请求。")
    )
    assert refine_subtitle_text([make_image()], [["甲"], ["乙"]]) is None


def test_refine_length_mismatch_returns_none(monkeypatch):
    monkeypatch.setattr(llm_client, "call_llm", lambda *a, **k: _fake_response('["甲"]'))
    assert refine_subtitle_text([make_image()], [["甲"], ["乙"]]) is None


def test_refine_non_string_or_empty_items_return_none(monkeypatch):
    monkeypatch.setattr(llm_client, "call_llm", lambda *a, **k: _fake_response('["甲", 3]'))
    assert refine_subtitle_text([make_image()], [["甲"], ["乙"]]) is None

    monkeypatch.setattr(llm_client, "call_llm", lambda *a, **k: _fake_response('["甲", ""]'))
    assert refine_subtitle_text([make_image()], [["甲"], ["乙"]]) is None


def test_refine_call_llm_exception_returns_none(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("connection refused")

    monkeypatch.setattr(llm_client, "call_llm", boom)
    assert refine_subtitle_text([make_image()], [["甲"]]) is None


def test_refine_undecodable_image_returns_none(monkeypatch):
    def boom(*a, **k):
        raise AssertionError("call_llm must not be reached")

    monkeypatch.setattr(llm_client, "call_llm", boom)
    assert refine_subtitle_text([None], [["甲"]]) is None


# ---------------------------------------------------------------------------
# d) OcrOptimizer hook: hard-frame detection + replacement
# ---------------------------------------------------------------------------

def make_raw(text: str, score: float = 0.95) -> dict:
    if text == "":
        return {
            "dt_polys": [],
            "rec_polys": [],
            "rec_texts": [],
            "rec_scores": [],
            "rec_boxes": [],
        }
    poly = [[0.0, 0.0], [100.0, 0.0], [100.0, 30.0], [0.0, 30.0]]
    return {
        "dt_polys": [poly],
        "rec_polys": [poly],
        "rec_texts": [text],
        "rec_scores": [score],
        "rec_boxes": [0, 0, 100, 30],
    }


class FakeEngine:
    """Scriptable engine double (same contract as tests/test_ocr_optimizer.py)."""

    def __init__(self, single_texts=None, batch_texts=None, default_text="字幕默认", score=0.95):
        self.single_texts = list(single_texts or [])
        self.batch_texts = list(batch_texts or [])
        self.default_text = default_text
        self.score = score
        self.predict_calls = 0
        self.batch_calls = 0

    def _next_text(self, queue: list) -> str:
        if queue:
            return queue.pop(0)
        return self.default_text

    def predict(self, img_input):
        self.predict_calls += 1
        return make_raw(self._next_text(self.single_texts), self.score)

    def predict_batch(self, images):
        self.batch_calls += 1
        return [make_raw(self._next_text(self.batch_texts), self.score) for _ in images]

    def normalize_result(self, raw):
        if isinstance(raw, list):
            raw = raw[0] if raw else make_raw("")
        return {k: (list(v) if isinstance(v, list) else v) for k, v in raw.items()}


ROI_ENTRY = {"type": "rect", "points": [0, 560, 1280, 160], "start_time": 0.0, "end_time": 15.0}


def make_frames(n: int) -> list:
    return [
        (ROI_ENTRY, np.full((80, 320, 3), 200, dtype=np.uint8), i, "roi_0", i / 30.0)
        for i in range(n)
    ]


def not_cancelled() -> bool:
    return False


@pytest.fixture
def refine_recorder(monkeypatch):
    """Patch core.vlm_refine.refine_subtitle_text; returns (calls, setter)."""
    calls = []

    def _default_refine(images, candidates):
        calls.append({"images": images, "candidates": candidates})
        return None

    def _set(return_value=None, exc=None):
        def fake_refine(images, candidates):
            calls.append({"images": images, "candidates": candidates})
            if exc is not None:
                raise exc
            return return_value

        monkeypatch.setattr(vlm_refine, "refine_subtitle_text", fake_refine)

    monkeypatch.setattr(vlm_refine, "refine_subtitle_text", _default_refine)
    return calls, _set


def make_optimizer(tmp_path, **overrides) -> OcrOptimizer:
    params = dict(
        work_dir=str(tmp_path),
        visualize=False,
        in_memory_mode=True,
        save_ocr_json=False,
    )
    params.update(overrides)
    return OcrOptimizer(**params)


def test_hard_frame_low_votes_triggers_refine_and_replaces_text(tmp_path, monkeypatch, refine_recorder):
    calls, set_refine = refine_recorder
    set_refine(return_value=["修正文本"])

    # One dissenting sample out of three -> winning votes (1) < 3/2 -> hard.
    # Driven through _get_best_ocr_result_from_sequence directly: inside
    # process_roi_group the sampled frames are usually already in the OCR
    # result cache, which (by design) bypasses predict_batch.
    engine = FakeEngine(default_text="字幕A", batch_texts=["字幕A", "字幕B", "字幕C"])
    monkeypatch.setattr(ocr_engine_manager, "get_engine", lambda: engine)
    opt = make_optimizer(tmp_path, motion_sentinel_enabled=False, vlm_refine_enabled=True)
    result = opt._get_best_ocr_result_from_sequence(make_frames(9))

    assert len(calls) == 1
    assert len(calls[0]["images"]) == 3  # first/mid/last sample frames
    assert calls[0]["candidates"] == [["字幕A", "字幕B", "字幕C"]]
    # Text replaced, scores/boxes stay from the voting result.
    assert result[1]["rec_texts"] == ["修正文本"]
    assert result[1]["rec_scores"] == [0.95]


def test_hard_frame_low_confidence_triggers_refine(tmp_path, monkeypatch, refine_recorder):
    calls, set_refine = refine_recorder
    set_refine(return_value=["修正文本"])

    # Unanimous text (no vote problem) but avg confidence 0.5 < 0.85 -> hard.
    engine = FakeEngine(default_text="字幕A", score=0.5)
    monkeypatch.setattr(ocr_engine_manager, "get_engine", lambda: engine)
    opt = make_optimizer(tmp_path, motion_sentinel_enabled=False, vlm_refine_enabled=True)
    results = opt.process_roi_group(make_frames(9), is_cancelled_func=not_cancelled)

    assert len(calls) == 1
    assert all(res[1]["rec_texts"] == ["修正文本"] for res in results)
    assert all(res[1]["rec_scores"] == [0.5] for res in results)


def test_confident_majority_frame_does_not_refine(tmp_path, monkeypatch, refine_recorder):
    calls, set_refine = refine_recorder
    set_refine(return_value=["不应生效"])

    engine = FakeEngine(default_text="字幕A")
    monkeypatch.setattr(ocr_engine_manager, "get_engine", lambda: engine)
    opt = make_optimizer(tmp_path, motion_sentinel_enabled=False, vlm_refine_enabled=True)
    results = opt.process_roi_group(make_frames(9), is_cancelled_func=not_cancelled)

    assert calls == []  # 3/3 votes, 0.95 confidence -> not a hard frame
    assert all(res[1]["rec_texts"] == ["字幕A"] for res in results)


def test_refine_exception_does_not_break_main_flow(tmp_path, monkeypatch, refine_recorder):
    calls, set_refine = refine_recorder
    set_refine(exc=RuntimeError("VLM endpoint down"))

    engine = FakeEngine(default_text="字幕A", batch_texts=["字幕A", "字幕B", "字幕C"])
    monkeypatch.setattr(ocr_engine_manager, "get_engine", lambda: engine)
    opt = make_optimizer(tmp_path, motion_sentinel_enabled=False, vlm_refine_enabled=True)
    result = opt._get_best_ocr_result_from_sequence(make_frames(9))

    assert len(calls) == 1  # attempted exactly once
    # Original voting result preserved.
    assert result[1]["rec_texts"] == ["字幕A"]


def test_refine_none_result_keeps_voting_result(tmp_path, monkeypatch, refine_recorder):
    calls, set_refine = refine_recorder
    set_refine(return_value=None)  # refine failed gracefully

    engine = FakeEngine(default_text="字幕A", batch_texts=["字幕A", "字幕B", "字幕C"])
    monkeypatch.setattr(ocr_engine_manager, "get_engine", lambda: engine)
    opt = make_optimizer(tmp_path, motion_sentinel_enabled=False, vlm_refine_enabled=True)
    result = opt._get_best_ocr_result_from_sequence(make_frames(9))

    assert len(calls) == 1
    assert result[1]["rec_texts"] == ["字幕A"]


def test_refine_not_called_when_not_configured(tmp_path, monkeypatch, refine_recorder):
    calls, set_refine = refine_recorder
    set_refine(return_value=["不应被调用"])
    for name in ("VLM_REFINE_BASE_URL", "VLM_REFINE_API_KEY"):
        monkeypatch.delenv(name, raising=False)

    # Hard frame, but vlm_refine_enabled=None + unconfigured env -> skip refine.
    engine = FakeEngine(default_text="字幕A", batch_texts=["字幕A", "字幕B", "字幕C"])
    monkeypatch.setattr(ocr_engine_manager, "get_engine", lambda: engine)
    opt = make_optimizer(tmp_path, motion_sentinel_enabled=False)  # enabled=None
    results = opt.process_roi_group(make_frames(9), is_cancelled_func=not_cancelled)

    assert calls == []
    assert all(res[1]["rec_texts"] == ["字幕A"] for res in results)


def test_refine_not_called_when_explicitly_disabled(tmp_path, monkeypatch, refine_recorder):
    calls, set_refine = refine_recorder
    set_refine(return_value=["不应被调用"])

    engine = FakeEngine(default_text="字幕A", batch_texts=["字幕A", "字幕B", "字幕C"])
    monkeypatch.setattr(ocr_engine_manager, "get_engine", lambda: engine)
    opt = make_optimizer(
        tmp_path, motion_sentinel_enabled=False, vlm_refine_enabled=False
    )
    results = opt.process_roi_group(make_frames(9), is_cancelled_func=not_cancelled)

    assert calls == []
    assert all(res[1]["rec_texts"] == ["字幕A"] for res in results)
