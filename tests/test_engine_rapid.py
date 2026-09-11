# tests/test_engine_rapid.py
"""Unit tests for core/ocr_engine_rapid.py without the real rapidocr package.

The engine's _ocr attribute is monkeypatched with duck-typed fakes:
- v3-style RapidOCROutput-like objects (attributes boxes/txts/scores, where
  boxes is a numpy array), and
- legacy list output ([box, text, score] triples).
"""

from __future__ import annotations

import numpy as np
import pytest

from core.ocr_engine_rapid import RapidOCREngine


BOX = np.array([[[10, 20], [110, 20], [110, 50], [10, 50]]], dtype=np.float64)


class FakeV3Output:
    """Duck-typed stand-in for rapidocr v3 RapidOCROutput."""

    def __init__(self, boxes=None, txts=None, scores=None):
        self.boxes = boxes
        self.txts = txts
        self.scores = scores


class FakeCallableOCR:
    """Stand-in for the RapidOCR callable instance."""

    def __init__(self, output):
        self.output = output
        self.calls: list = []

    def __call__(self, img_input):
        self.calls.append(img_input)
        return self.output


@pytest.fixture
def engine():
    eng = RapidOCREngine()
    eng.text_score = 0.5  # same default as initialize(); keep tests hermetic
    return eng


# ---------------------------------------------------------------------------
# predict → _to_triples normalization
# ---------------------------------------------------------------------------

def test_predict_normalizes_v3_output(engine, monkeypatch):
    fake = FakeCallableOCR(FakeV3Output(boxes=BOX.copy(), txts=["你好"], scores=[0.9]))
    monkeypatch.setattr(engine, "_ocr", fake)

    triples = engine.predict(np.zeros((8, 8, 3), dtype=np.uint8))

    assert fake.calls, "predict must call the underlying _ocr callable"
    assert triples == [[
        [[10.0, 20.0], [110.0, 20.0], [110.0, 50.0], [10.0, 50.0]],
        "你好",
        0.9,
    ]]


def test_predict_txts_none_means_no_result(engine, monkeypatch):
    monkeypatch.setattr(engine, "_ocr", FakeCallableOCR(FakeV3Output(boxes=BOX.copy(), txts=None, scores=[0.9])))
    assert engine.predict("img.jpg") == []

    monkeypatch.setattr(engine, "_ocr", FakeCallableOCR(None))
    assert engine.predict("img.jpg") == []


def test_predict_scores_shorter_than_txts_defaults_score_1(engine, monkeypatch):
    out = FakeV3Output(boxes=BOX.copy(), txts=["a", "b"], scores=[0.9])
    monkeypatch.setattr(engine, "_ocr", FakeCallableOCR(out))
    triples = engine.predict("x")
    assert triples[1][2] == 1.0
    assert triples[1][1] == "b"


def test_predict_legacy_list_passthrough(engine, monkeypatch):
    legacy = [[[0, 0], [1, 1]], "text", 0.8]
    monkeypatch.setattr(engine, "_ocr", FakeCallableOCR([legacy]))
    assert engine.predict("x") == [legacy]


def test_to_triples_unrecognized_object_returns_empty():
    class Weird:
        pass

    assert RapidOCREngine._to_triples(Weird()) == []
    assert RapidOCREngine._to_triples(None) == []
    assert RapidOCREngine._to_triples([]) == []


def test_to_triples_missing_scores_returns_empty():
    out = FakeV3Output(boxes=BOX.copy(), txts=["hi"], scores=None)
    assert RapidOCREngine._to_triples(out) == []


# ---------------------------------------------------------------------------
# normalize_result filtering / unified structure
# ---------------------------------------------------------------------------

def test_normalize_filters_low_score_lines(engine):
    box = [[10, 20], [110, 20], [110, 50], [10, 50]]
    raw = [
        [box, "keep-me", 0.9],
        [box, "drop-me", 0.4],  # below text_score=0.5
    ]
    data = engine.normalize_result(raw)

    assert data["rec_texts"] == ["keep-me"]
    assert data["rec_scores"] == [0.9]
    assert len(data["rec_polys"]) == 1
    assert data["rec_boxes"] == [[10, 20, 110, 50]]


def test_normalize_score_threshold_boundary_kept(engine):
    box = [[0, 0], [5, 0], [5, 5], [0, 5]]
    data = engine.normalize_result([[box, "boundary", 0.5]])
    assert data["rec_texts"] == ["boundary"]


def test_normalize_two_item_tuple_defaults_score_1(engine):
    box = [[1, 2], [3, 4], [5, 6], [7, 8]]
    data = engine.normalize_result([[box, "no-score"]])
    assert data["rec_scores"] == [1.0]
    assert data["rec_texts"] == ["no-score"]
    assert data["rec_polys"] == [[[1.0, 2.0], [3.0, 4.0], [5.0, 6.0], [7.0, 8.0]]]


def test_normalize_converts_numpy_box(engine):
    data = engine.normalize_result([[BOX[0].copy(), "np-box", 0.8]])
    assert isinstance(data["rec_polys"][0], list)
    assert data["rec_polys"][0] == [[10.0, 20.0], [110.0, 20.0], [110.0, 50.0], [10.0, 50.0]]


def test_normalize_malformed_items_skipped(engine):
    box = [[0, 0], [10, 0], [10, 10], [0, 10]]
    raw = [
        [box, "good", 0.9],
        [box, "missing-score"],  # len 2 → score defaults 1.0, kept
        "junk",                  # len 4 (string) → neither 2 nor 3 → skipped
    ]
    data = engine.normalize_result(raw)
    # "junk" (len 4) is definitely dropped.
    assert "junk" not in data["rec_texts"]
    assert data["rec_texts"][0] == "good"
    assert data["rec_scores"] == [0.9, 1.0]


def test_normalize_two_element_row_with_non_box_is_graceful(engine):
    # Regression: a 2-element row whose box is not a point sequence
    # (e.g. ["only-text", 0.9]) must be skipped with a warning, not crash.
    data = engine.normalize_result([["only-text", 0.9]])
    assert isinstance(data, dict)
    assert "only-text" not in data["rec_texts"]


def test_normalize_empty(engine):
    data = engine.normalize_result([])
    assert data["rec_texts"] == []
    assert data["dt_polys"] == []


def test_predict_before_initialize_raises(engine):
    with pytest.raises(RuntimeError):
        engine.predict("img.png")
