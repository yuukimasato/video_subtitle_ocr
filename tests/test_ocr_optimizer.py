# tests/test_ocr_optimizer.py
"""Unit tests for core/ocr_optimizer.py using a fake engine + synthetic frames.

The engine layer is neutralized by monkeypatching
``core.ocr_engine_manager.get_engine`` to return a controllable fake engine
(predict / predict_batch / normalize_result are all scriptable), so every
scenario below runs fully offline on numpy-generated frames.
"""

from __future__ import annotations

import numpy as np
import pytest

from core import ocr_engine_manager
from core.ocr_optimizer import OcrOptimizer

ROI_ENTRY = {"type": "rect", "points": [0, 560, 1280, 160], "start_time": 0.0, "end_time": 15.0}


def make_raw(text: str, score: float = 0.95) -> dict:
    """Build a raw single-line OCR result in unified dict format."""
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
    """Scriptable engine double.

    - ``single_texts`` / ``batch_texts``: queues consumed per OCR invocation
      (falls back to ``default_text`` when empty).
    - Counters let tests assert batching behaviour.
    """

    def __init__(self, single_texts=None, batch_texts=None, default_text="字幕默认"):
        self.single_texts = list(single_texts or [])
        self.batch_texts = list(batch_texts or [])
        self.default_text = default_text
        self.predict_calls = 0
        self.batch_calls = 0
        self.batch_sizes: list = []

    def _next_text(self, queue: list) -> str:
        if queue:
            return queue.pop(0)
        return self.default_text

    def predict(self, img_input):
        self.predict_calls += 1
        return make_raw(self._next_text(self.single_texts))

    def predict_batch(self, images):
        self.batch_calls += 1
        self.batch_sizes.append(len(images))
        return [make_raw(self._next_text(self.batch_texts)) for _ in images]

    def normalize_result(self, raw):
        if isinstance(raw, list):
            raw = raw[0] if raw else make_raw("")
        return {k: (list(v) if isinstance(v, list) else v) for k, v in raw.items()}


class ExplodingBatchEngine(FakeEngine):
    def predict_batch(self, images):
        self.batch_calls += 1
        raise RuntimeError("predict_batch boom")


class WrongLengthBatchEngine(FakeEngine):
    def predict_batch(self, images):
        self.batch_calls += 1
        self.batch_sizes.append(len(images))
        return [make_raw("x")]  # deliberately wrong count


@pytest.fixture
def install_fake_engine(monkeypatch):
    def _install(engine: FakeEngine) -> FakeEngine:
        monkeypatch.setattr(ocr_engine_manager, "get_engine", lambda: engine)
        return engine

    return _install


def make_optimizer(tmp_path, **overrides) -> OcrOptimizer:
    params = dict(
        work_dir=str(tmp_path),
        visualize=False,
        in_memory_mode=True,
        save_ocr_json=False,
        engine_options={"lang": "japan"},
    )
    params.update(overrides)
    return OcrOptimizer(**params)


def make_frames(
    n: int,
    width: int = 320,
    height: int = 80,
    base: int = 200,
    noise_rng: np.random.Generator | None = None,
    roi_id: str = "roi_0",
    start_frame: int = 0,
    fps: float = 30.0,
):
    """Synthetic frame tuples shaped like roi_extractor's output."""
    frames = []
    for i in range(n):
        if noise_rng is None:
            img = np.full((height, width, 3), base, dtype=np.uint8)
        else:
            # Per-pixel noise in [-3, 3], identical across channels so the
            # grayscale diff between any two frames stays below any sane
            # motion_pixel_delta, while full-resolution SSIM degrades clearly.
            noise = noise_rng.integers(-3, 4, (height, width, 1)).astype(np.int16)
            img = np.repeat(np.clip(base + noise, 0, 255).astype(np.uint8), 3, axis=2)
        frames.append((ROI_ENTRY, img, start_frame + i, roi_id, (start_frame + i) / fps))
    return frames


def not_cancelled() -> bool:
    return False


# ---------------------------------------------------------------------------
# a) Similar-sequence grouping + first/mid/last voting (batch path)
# ---------------------------------------------------------------------------

def test_grouping_reuses_cached_probe_results(tmp_path, install_fake_engine):
    engine = install_fake_engine(
        FakeEngine(default_text="字幕A", batch_texts=["字幕A", "字幕A", "字幕B"])
    )
    opt = make_optimizer(tmp_path, motion_sentinel_enabled=False)
    frames = make_frames(9)

    results = opt.process_roi_group(frames, is_cancelled_func=not_cancelled)

    # 9 static frames -> one sequence whose first/mid/last samples (0, 4, 8)
    # were ALREADY OCRed by the anchor + binary search. The result cache must
    # dedupe them: no extra batch call, no extra OCR call for those frames.
    assert engine.batch_calls == 0
    assert len(results) == 9
    for res in results:
        assert res[1]["rec_texts"] == ["字幕A"]
        assert res[3] == "roi_0"
    assert opt.frames_filled == 9
    # 1 initial + 4 binary-search singles; sampling added nothing.
    assert opt.ocr_calls == 5


def test_sequence_voting_uses_single_predict_batch(tmp_path, install_fake_engine):
    """Voting itself: uncached first/mid/last samples go through exactly one
    predict_batch call and the majority text wins."""
    engine = install_fake_engine(
        FakeEngine(default_text="字幕A", batch_texts=["字幕A", "字幕A", "字幕B"])
    )
    opt = make_optimizer(tmp_path, motion_sentinel_enabled=False)
    frames = make_frames(9)

    result = opt._get_best_ocr_result_from_sequence(frames)

    assert engine.batch_calls == 1
    assert engine.batch_sizes == [3]
    # Majority vote wins over the single dissenting sample.
    assert result[1]["rec_texts"] == ["字幕A"]
    assert result[1]["rec_scores"] == [0.95]
    # One batch invocation counts as one OCR call.
    assert opt.ocr_calls == 1


# ---------------------------------------------------------------------------
# b) save_ocr_json=True forces the per-frame path (no predict_batch)
# ---------------------------------------------------------------------------

def test_save_ocr_json_uses_per_frame_path(tmp_path, install_fake_engine):
    engine = install_fake_engine(FakeEngine(default_text="静态字幕"))
    opt = make_optimizer(tmp_path, motion_sentinel_enabled=False, save_ocr_json=True)
    frames = make_frames(9)

    results = opt.process_roi_group(frames, is_cancelled_func=not_cancelled)

    assert engine.batch_calls == 0
    assert engine.predict_calls >= 1
    assert len(results) == 9
    assert all(res[1]["rec_texts"] == ["静态字幕"] for res in results)
    # Debug JSON dumps land in work_dir/2_ocr_results.
    json_dir = tmp_path / "2_ocr_results"
    assert json_dir.is_dir()
    assert len(list(json_dir.glob("*.json"))) >= 1


# ---------------------------------------------------------------------------
# c) Batch failure falls back to per-frame OCR
# ---------------------------------------------------------------------------

def test_batch_exception_falls_back_to_per_frame(tmp_path, install_fake_engine):
    engine = install_fake_engine(ExplodingBatchEngine(default_text="回退文本"))
    opt = make_optimizer(tmp_path, motion_sentinel_enabled=False)
    frames = make_frames(9)

    # Drive the sampling path directly: inside process_roi_group the samples
    # usually hit the result cache and never reach predict_batch.
    result = opt._get_best_ocr_result_from_sequence(frames)

    assert engine.batch_calls == 1  # attempted once, then abandoned
    assert engine.predict_calls == 3  # per-frame fallback for the 3 samples
    assert result[1]["rec_texts"] == ["回退文本"]
    assert opt.ocr_calls == 3


def test_batch_wrong_result_count_falls_back(tmp_path, install_fake_engine):
    engine = install_fake_engine(WrongLengthBatchEngine(default_text="对齐回退"))
    opt = make_optimizer(tmp_path, motion_sentinel_enabled=False)
    frames = make_frames(4)

    result = opt._get_best_ocr_result_from_sequence(frames)
    assert engine.batch_calls == 1
    assert result[1]["rec_texts"] == ["对齐回退"]


# ---------------------------------------------------------------------------
# d) Levenshtein text tolerance (threshold boundary 0.9)
# ---------------------------------------------------------------------------

def test_frames_similar_single_char_diff_at_boundary(tmp_path):
    opt = make_optimizer(tmp_path)  # text_similarity_threshold=0.9

    ten_chars = "这是一段测试字幕文本"
    one_sub = "这是一段测试字慕文本"  # 1 substitution in 10 chars -> sim 0.9
    assert opt._are_frames_similar(make_raw(ten_chars), make_raw(one_sub)) is True

    short = "今天天气真不错"        # 7 chars
    short_plus = "今天天气真不错啊"   # 8 chars, distance 1 -> sim 0.875 < 0.9
    assert opt._are_frames_similar(make_raw(short), make_raw(short_plus)) is False

    two_subs = "这是二段测试字慕文本"  # 2 substitutions -> sim 0.8 < 0.9
    assert opt._are_frames_similar(make_raw(ten_chars), make_raw(two_subs)) is False

    # Exact match fast path, and empty-text semantics.
    assert opt._are_frames_similar(make_raw(ten_chars), make_raw(ten_chars)) is True
    assert opt._are_frames_similar(make_raw(""), make_raw(ten_chars)) is False
    assert opt._are_frames_similar(make_raw(""), make_raw("")) is False


# ---------------------------------------------------------------------------
# e) SSIM downsampling switch (similarity_target_width)
# ---------------------------------------------------------------------------

def test_grayscale_downsample_enabled(tmp_path):
    opt = make_optimizer(tmp_path, similarity_target_width=320)
    frame = (ROI_ENTRY, np.full((720, 1280, 3), 128, dtype=np.uint8), 0, "roi_0", 0.0)

    gray = opt._get_grayscale_image(frame)
    assert gray is not None and gray.ndim == 2
    assert gray.shape[1] == 320  # downsampled to the target width
    assert gray.shape[1] <= 320


def test_grayscale_downsample_disabled(tmp_path):
    opt = make_optimizer(tmp_path, similarity_target_width=0)
    frame = (ROI_ENTRY, np.full((720, 1280, 3), 128, dtype=np.uint8), 1, "roi_0", 0.0)

    gray = opt._get_grayscale_image(frame)
    assert gray.shape == (720, 1280)  # original resolution


def test_grayscale_never_upsamples(tmp_path):
    opt = make_optimizer(tmp_path, similarity_target_width=320)
    frame = (ROI_ENTRY, np.full((80, 300, 3), 128, dtype=np.uint8), 2, "roi_0", 0.0)

    gray = opt._get_grayscale_image(frame)
    assert gray.shape == (80, 300)


# ---------------------------------------------------------------------------
# f) Feature-cache LRU eviction
# ---------------------------------------------------------------------------

def test_feature_cache_lru_eviction(tmp_path):
    opt = make_optimizer(tmp_path, feature_cache_max_entries=3)
    frames = [
        (ROI_ENTRY, np.full((64, 64, 3), 200, dtype=np.uint8), i, "roi_0", i / 30.0)
        for i in range(4)
    ]

    for frame in frames:
        opt._get_grayscale_image(frame)

    keys = [("roi_0", i) for i in range(4)]
    assert len(opt._feature_cache) == 3
    assert keys[0] not in opt._feature_cache  # oldest evicted
    assert keys[3] in opt._feature_cache

    # LRU refresh: touching key 1 keeps it alive over key 2.
    opt._get_grayscale_image(frames[1])
    extra = (ROI_ENTRY, np.full((64, 64, 3), 200, dtype=np.uint8), 4, "roi_0", 4 / 30.0)
    opt._get_grayscale_image(extra)
    assert ("roi_0", 1) in opt._feature_cache
    assert ("roi_0", 2) not in opt._feature_cache


# ---------------------------------------------------------------------------
# g) Motion sentinel skip (adjacent near-identical frames reuse last result)
# ---------------------------------------------------------------------------

def test_motion_sentinel_skips_identical_frames(tmp_path, install_fake_engine):
    # First frame OCRs to empty text, so no grouping is attempted and the
    # remaining identical frames must be skipped by the motion sentinel.
    engine = install_fake_engine(FakeEngine(default_text=""))
    opt = make_optimizer(tmp_path)  # motion_sentinel_enabled=True by default
    frames = make_frames(5)

    results = opt.process_roi_group(frames, is_cancelled_func=not_cancelled)

    assert opt.ocr_calls == 1
    # Frame 0 is a real OCR call that happened to recognize nothing (not a
    # filled frame); frames 1-4 are motion-sentinel fills.
    assert opt.frames_filled == 4
    assert len(results) == 5
    for res in results:
        assert res[1]["rec_texts"] == []


def test_motion_sentinel_reuses_last_text_on_tiny_noise(tmp_path, install_fake_engine):
    # Full-resolution SSIM of independently-noised frames is ~0.9 (< 0.98), so
    # no grouping happens; but the per-pixel diff stays below motion_pixel_delta,
    # so the sentinel skips OCR and reuses the previous result.
    rng = np.random.default_rng(42)
    engine = install_fake_engine(FakeEngine(default_text="字幕X"))
    opt = make_optimizer(tmp_path, similarity_target_width=0)  # keep noise at full res
    frames = make_frames(5, width=640, height=360, noise_rng=rng)

    results = opt.process_roi_group(frames, is_cancelled_func=not_cancelled)

    assert opt.frames_filled == 5
    # Frame 0 is OCRed exactly once (a single-frame sequence reuses the anchor
    # result instead of sampling it again), frames 1-4 are skipped entirely:
    # ocr_calls must not grow with the frame count.
    assert opt.ocr_calls == 1
    assert engine.predict_calls == 1
    assert all(res[1]["rec_texts"] == ["字幕X"] for res in results)


# ---------------------------------------------------------------------------
# h) Cancellation short-circuits to []
# ---------------------------------------------------------------------------

def test_cancelled_returns_empty_list(tmp_path, install_fake_engine):
    engine = install_fake_engine(FakeEngine(default_text="不应被调用"))
    opt = make_optimizer(tmp_path)
    frames = make_frames(5)

    results = opt.process_roi_group(frames, is_cancelled_func=lambda: True)

    assert results == []
    assert opt.ocr_calls == 0
    assert engine.predict_calls == 0
    assert engine.batch_calls == 0


# ---------------------------------------------------------------------------
# Cleanup clears all caches
# ---------------------------------------------------------------------------

def test_cleanup_clears_caches(tmp_path):
    opt = make_optimizer(tmp_path)
    frame = (ROI_ENTRY, np.full((64, 64, 3), 200, dtype=np.uint8), 0, "roi_0", 0.0)
    opt._get_grayscale_image(frame)
    assert opt._feature_cache

    opt.cleanup()
    assert not opt._feature_cache
    assert not opt._image_cache
    assert not opt._last_gray_by_roi
    assert not opt._last_ocr_by_roi

# ---------------------------------------------------------------------------
# i) Regression: flat-triples engines (RapidOCR style) through the batch path
# ---------------------------------------------------------------------------

def test_batch_path_with_rapid_style_engine(tmp_path, monkeypatch):
    """A real RapidOCREngine (stubbed rapidocr callable) must produce non-empty
    text through the batch voting path.

    Regression for the bug where the optimizer wrapped RapidOCR's flat
    [box, text, score] triples in a single-element list before
    normalize_result(), silently emptying every batch result.
    """
    from core.ocr_engine_rapid import RapidOCREngine

    class _FakeRapidOutput:
        boxes = np.array([[[0, 0], [100, 0], [100, 30], [0, 30]]], dtype=np.float32)
        txts = ("字幕回归",)
        scores = (0.97,)

    engine = RapidOCREngine()
    engine._ocr = lambda img: _FakeRapidOutput()
    engine._initialized = True
    engine.text_score = 0.5
    monkeypatch.setattr(ocr_engine_manager, "get_engine", lambda: engine)

    opt = make_optimizer(tmp_path, engine_options={"lang": "ch"})
    frames = make_frames(9)
    results = opt.process_roi_group(frames, is_cancelled_func=not_cancelled)

    assert results, "expected filled results"
    non_empty = [r for r in results if r[1].get("rec_texts")]
    assert non_empty, "batch path emptied all RapidOCR-style results"
    assert all(r[1]["rec_texts"] == ["字幕回归"] for r in non_empty)


# ---------------------------------------------------------------------------
# g) Device-sized predict_batch chunking (Phase 3)
# ---------------------------------------------------------------------------

def test_batch_chunk_size_gpu_vs_cpu(tmp_path, install_fake_engine, monkeypatch):
    install_fake_engine(FakeEngine())
    opt = make_optimizer(tmp_path)
    monkeypatch.setattr("core.ocr_processor.get_device_mode", lambda: "gpu")
    assert opt._predict_batch_chunk_size() == 12
    opt2 = make_optimizer(tmp_path)
    monkeypatch.setattr("core.ocr_processor.get_device_mode", lambda: "cpu")
    assert opt2._predict_batch_chunk_size() == 6
    # Cached per instance: a second call does not re-probe the device.
    assert opt2._predict_batch_chunk_size() == 6


def test_batch_ocr_on_samples_chunks_pending(tmp_path, install_fake_engine, monkeypatch):
    engine = install_fake_engine(FakeEngine(default_text="字幕默认"))
    opt = make_optimizer(tmp_path)
    monkeypatch.setattr("core.ocr_processor.get_device_mode", lambda: "cpu")  # chunk=6
    frames = make_frames(14)
    out = opt._run_batch_ocr_on_samples(frames)
    assert out is not None and len(out) == 14
    # 14 pending frames at chunk size 6 -> 6+6+2.
    assert engine.batch_calls == 3
    assert engine.batch_sizes == [6, 6, 2]
    # Results stay aligned with the input order.
    assert [r[2] for r in out] == [f[2] for f in frames]
    assert opt.ocr_calls == 1  # whole batch invocation counts once


def test_batch_ocr_on_samples_single_chunk_when_under_size(tmp_path, install_fake_engine, monkeypatch):
    engine = install_fake_engine(FakeEngine(default_text="字幕默认"))
    opt = make_optimizer(tmp_path)
    monkeypatch.setattr("core.ocr_processor.get_device_mode", lambda: "cpu")
    frames = make_frames(3)
    out = opt._run_batch_ocr_on_samples(frames)
    assert engine.batch_calls == 1 and engine.batch_sizes == [3]
    assert [r[2] for r in out] == [f[2] for f in frames]
