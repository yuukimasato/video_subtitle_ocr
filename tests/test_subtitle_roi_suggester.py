# tests/test_subtitle_roi_suggester.py
"""Unit + integration tests for core/subtitle_roi_suggester.py.

Offline tests synthesize a small video (marker rectangles drawn onto frames)
and monkeypatch ``core.ocr_engine_manager.get_engine`` to return a probe engine
that detects those markers and emits synthetic OCR lines. This exercises the
full pipeline: frame sampling, score/bottom-region filtering, clustering,
bbox union + padding, ROI dict structure, progress callbacks and per-frame
failure tolerance — without loading any real OCR model.

The real integration test (VIDEO_SUBTITLE_OCR_REAL_TEST=1) runs the actual
PP-OCR engine on benchmarks/test_video_subtitle.mp4 and cross-checks the
suggested ROIs against benchmarks/gt.json.
"""

from __future__ import annotations

import json
import math
import os
from pathlib import Path

import cv2
import numpy as np
import pytest

from core import ocr_engine_manager
from core.subtitle_roi_suggester import (
    POLY_TILT_THRESHOLD_DEG,
    _quad_of_line,
    _segment_to_roi_entry,
    cluster_poly_points_if_tilted,
    suggest_subtitle_rois,
)
from utils.time_utils import format_time

PROJECT_ROOT = Path(__file__).resolve().parents[1]
REAL_VIDEO = PROJECT_ROOT / "benchmarks" / "test_video_subtitle.mp4"
REAL_GT = PROJECT_ROOT / "benchmarks" / "gt.json"

# Synthetic video layout: 320x240 @ 10 fps, 120 frames (12 s).
WIDTH, HEIGHT, FPS, N_FRAMES = 320, 240, 10.0, 120
SUBTITLE_BAND = (60, 200, 260, 220)   # bottom "subtitle" marker (white)
WATERMARK_BAND = (100, 30, 220, 40)   # top "watermark" marker (white)
LOWSCORE_BAND = (80, 175, 240, 185)   # bottom low-score marker (gray)
WHITE = (255, 255, 255)
GRAY = (200, 200, 200)
RED = (0, 0, 255)  # BGR


# ---------------------------------------------------------------------------
# Video synthesis + probe engine
# ---------------------------------------------------------------------------

def _write_video(path: Path, draw_fn) -> Path:
    """Write a synthetic video (lossless FFV1 preferred, mp4v fallback)."""
    for fourcc, ext in ((cv2.VideoWriter_fourcc(*"FFV1"), ".avi"),
                        (cv2.VideoWriter_fourcc(*"mp4v"), ".mp4")):
        candidate = path.with_suffix(ext)
        writer = cv2.VideoWriter(str(candidate), fourcc, FPS, (WIDTH, HEIGHT))
        if not writer.isOpened():
            continue
        for i in range(N_FRAMES):
            img = np.zeros((HEIGHT, WIDTH, 3), dtype=np.uint8)
            draw_fn(img, i)
            writer.write(img)
        writer.release()
        return candidate
    pytest.fail("No usable video codec available for test video synthesis")


def _mask_runs(mask: np.ndarray, min_len: int = 8):
    runs, start = [], None
    for x, hit in enumerate(mask):
        if hit and start is None:
            start = x
        elif not hit and start is not None:
            if x - start >= min_len:
                runs.append((start, x))
            start = None
    if start is not None and len(mask) - start >= min_len:
        runs.append((start, len(mask)))
    return runs


def _white_runs(frame: np.ndarray, y: int):
    row = frame[y].astype(int)
    return _mask_runs(row.min(axis=1) > 170)


def _gray_runs(frame: np.ndarray, y: int):
    row = frame[y].astype(int)
    return _mask_runs((row.min(axis=1) > 150) & (row.max(axis=1) < 235))


def _line(x1: int, y1: int, x2: int, y2: int, text: str, score: float) -> dict:
    poly = [[float(x1), float(y1)], [float(x2), float(y1)],
            [float(x2), float(y2)], [float(x1), float(y2)]]
    return {
        "poly": poly,
        "text": text,
        "score": score,
        "box": [int(x1), int(y1), int(x2), int(y2)],
    }


class PixelProbeEngine:
    """Fake OCR engine: emits synthetic lines for marker rects drawn in frames.

    - White band at SUBTITLE_BAND rows -> high-score bottom line.
    - White band at WATERMARK_BAND rows -> high-score top line.
    - Gray band at LOWSCORE_BAND rows -> low-score bottom line.
    - Red marker block at the top-left corner -> engine failure (raises).
    """

    def __init__(self):
        self.predict_calls = 0
        self.failure_calls = 0

    def predict(self, frame):
        self.predict_calls += 1
        blue, green, red = (int(v) for v in frame[4, 4])
        if red > 150 and green < 80 and blue < 80:
            self.failure_calls += 1
            raise RuntimeError("synthetic OCR failure")

        lines = []
        for x1, x2 in _white_runs(frame, 210):
            lines.append(_line(x1, SUBTITLE_BAND[1], x2, SUBTITLE_BAND[3], "测试字幕", 0.95))
        for x1, x2 in _white_runs(frame, 35):
            lines.append(_line(x1, WATERMARK_BAND[1], x2, WATERMARK_BAND[3], "水印文字", 0.95))
        for x1, x2 in _gray_runs(frame, 180):
            lines.append(_line(x1, LOWSCORE_BAND[1], x2, LOWSCORE_BAND[3], "低置信文本", 0.30))
        # Document-level unified format: one poly/box/text/score per line.
        return [{
            "dt_polys": [ln["poly"] for ln in lines],
            "rec_polys": [ln["poly"] for ln in lines],
            "rec_texts": [ln["text"] for ln in lines],
            "rec_scores": [ln["score"] for ln in lines],
            "rec_boxes": [ln["box"] for ln in lines],
        }]

    def normalize_result(self, raw):
        assert isinstance(raw, list) and len(raw) == 1
        return {k: list(v) for k, v in raw[0].items()}


@pytest.fixture
def probe_env(monkeypatch):
    """Install the probe engine; records the set_engine call. Returns (engine, calls)."""
    engine = PixelProbeEngine()
    calls = {}

    def fake_set_engine(engine_id, options=None):
        calls["engine_id"] = engine_id
        calls["options"] = options

    monkeypatch.setattr(ocr_engine_manager, "set_engine", fake_set_engine)
    monkeypatch.setattr(ocr_engine_manager, "get_engine", lambda: engine)
    return engine, calls


def make_drawer(sub_range=None, watermark=False, lowscore=False, poison_frames=()):
    def draw(img: np.ndarray, i: int) -> None:
        if i in set(poison_frames):
            img[0:8, 0:8] = RED
        if sub_range is not None and sub_range[0] <= i < sub_range[1]:
            x1, y1, x2, y2 = SUBTITLE_BAND
            img[y1:y2, x1:x2] = WHITE
        if watermark:
            x1, y1, x2, y2 = WATERMARK_BAND
            img[y1:y2, x1:x2] = WHITE
        if lowscore:
            x1, y1, x2, y2 = LOWSCORE_BAND
            img[y1:y2, x1:x2] = GRAY
    return draw


def expected_sample_indices(total=N_FRAMES, sample_count=12):
    last = total - 1
    return [int(round(i * last / (sample_count - 1))) for i in range(sample_count)]


# ---------------------------------------------------------------------------
# a) Basic clustering, time range, bbox union + padding, dict structure
# ---------------------------------------------------------------------------

def test_basic_clustering_and_roi_structure(tmp_path, probe_env):
    engine, calls = probe_env
    video = _write_video(tmp_path / "basic", make_drawer(sub_range=(20, 90)))

    rois = suggest_subtitle_rois(str(video), sample_count=12, ocr_engine_id="paddle")

    assert calls == {"engine_id": "paddle", "options": {"lang": "ch", "model_tier": "auto"}}
    assert engine.predict_calls == 12

    hits = [i for i in expected_sample_indices() if 20 <= i < 90]
    assert len(hits) >= 2 and hits == sorted(hits)
    assert len(rois) == 1  # consecutive sample positions -> one segment

    roi = rois[0]
    # 自动建议的 ROI 默认开启帧级边界精修。
    assert roi["fade_in_refine_enabled"] is True
    assert set(roi.keys()) == {"start_time", "end_time", "start_frame", "end_frame", "type", "points", "fade_in_refine_enabled"}
    assert roi["type"] == "rect"
    assert roi["start_frame"] == min(hits)
    assert roi["end_frame"] == max(hits)
    assert roi["start_time"] == format_time(min(hits) / FPS)
    assert roi["end_time"] == format_time(max(hits) / FPS)
    # Union of all hit boxes (60,200)-(260,220), x-pad 16, y-pad 20*0.6=12.
    assert roi["points"] == [44, 188, 232, 44]
    x, y, w, h = roi["points"]
    assert all(isinstance(v, int) for v in (x, y, w, h))


def test_sample_indices_cover_first_and_last_frame(tmp_path, probe_env):
    engine, _ = probe_env
    video = _write_video(tmp_path / "cover", make_drawer(sub_range=(20, 90)))

    suggest_subtitle_rois(str(video), sample_count=12)

    indices = expected_sample_indices()
    assert indices[0] == 0 and indices[-1] == N_FRAMES - 1
    assert engine.predict_calls == len(indices) == 12


def test_sample_count_above_frame_count_clamps(tmp_path, probe_env):
    engine, _ = probe_env
    video = _write_video(tmp_path / "clamp", make_drawer(sub_range=(20, 90)))

    rois = suggest_subtitle_rois(str(video), sample_count=500)

    assert engine.predict_calls == N_FRAMES  # one sample per frame
    assert len(rois) == 1
    assert rois[0]["start_frame"] == 20
    assert rois[0]["end_frame"] == 89


# ---------------------------------------------------------------------------
# b) min_text_score / bottom_ratio filtering
# ---------------------------------------------------------------------------

def test_bottom_ratio_filters_top_lines(tmp_path, probe_env):
    video = _write_video(tmp_path / "top", make_drawer(watermark=True))

    rois = suggest_subtitle_rois(str(video), sample_count=12)

    assert rois == []  # every hit sits in the top region -> filtered


def test_min_text_score_filters_low_confidence_lines(tmp_path, probe_env):
    video = _write_video(tmp_path / "low", make_drawer(lowscore=True))

    rois = suggest_subtitle_rois(str(video), sample_count=12)

    assert rois == []  # score 0.30 < 0.6 -> filtered


def test_high_min_text_score_rejects_normal_subtitle(tmp_path, probe_env):
    video = _write_video(tmp_path / "strict", make_drawer(sub_range=(20, 90)))

    rois = suggest_subtitle_rois(str(video), sample_count=12, min_text_score=0.99)

    assert rois == []  # only lines scoring >= 0.99 survive; ours score 0.95


# ---------------------------------------------------------------------------
# c) Gap splitting (no interpolation) and per-frame failure tolerance
# ---------------------------------------------------------------------------

def test_gap_in_subtitles_splits_segments(tmp_path, probe_env):
    # Two subtitle windows straddle sample positions with a hole in between
    # (the sample near frame 54 has no subtitle) -> 2 segments, no interpolation.
    drawer = make_drawer(sub_range=(20, 45))

    def draw_two(img, i):
        drawer(img, i)
        if 60 <= i < 95:
            x1, y1, x2, y2 = SUBTITLE_BAND
            img[y1:y2, x1:x2] = WHITE

    video = _write_video(tmp_path / "gap", draw_two)
    rois = suggest_subtitle_rois(str(video), sample_count=12)

    assert len(rois) == 2
    first_hits = [i for i in expected_sample_indices() if 20 <= i < 45]
    second_hits = [i for i in expected_sample_indices() if 60 <= i < 95]
    assert rois[0]["start_frame"] == min(first_hits)
    assert rois[0]["end_frame"] == max(first_hits)
    assert rois[1]["start_frame"] == min(second_hits)
    assert rois[1]["end_frame"] == max(second_hits)
    # Sorted by time.
    assert rois[0]["start_frame"] < rois[1]["start_frame"]


def test_failed_ocr_frame_treated_as_no_subtitle(tmp_path, probe_env):
    engine, _ = probe_env
    # Sample point near frame 54 fails -> the continuous subtitle [20, 95)
    # splits into two segments instead of raising.
    poison = [i for i in expected_sample_indices() if 20 <= i < 95]
    poison_frame = poison[len(poison) // 2]

    video = _write_video(tmp_path / "poison", make_drawer(sub_range=(20, 95), poison_frames=[poison_frame]))
    rois = suggest_subtitle_rois(str(video), sample_count=12)

    assert engine.failure_calls == 1
    assert len(rois) == 2
    before = [i for i in expected_sample_indices() if 20 <= i < poison_frame]
    after = [i for i in expected_sample_indices() if poison_frame < i < 95]
    assert rois[0]["start_frame"] == min(before) and rois[0]["end_frame"] == max(before)
    assert rois[1]["start_frame"] == min(after) and rois[1]["end_frame"] == max(after)


# ---------------------------------------------------------------------------
# d) Progress callback + robustness
# ---------------------------------------------------------------------------

def test_progress_cb_called_per_sample_frame(tmp_path, probe_env):
    video = _write_video(tmp_path / "progress", make_drawer(sub_range=(20, 90)))

    calls = []
    suggest_subtitle_rois(str(video), sample_count=12, progress_cb=lambda done, total: calls.append((done, total)))

    assert calls == [(i, 12) for i in range(1, 13)]


def test_missing_video_returns_empty_list(tmp_path, probe_env):
    assert suggest_subtitle_rois(str(tmp_path / "does_not_exist.mp4")) == []


# ---------------------------------------------------------------------------
# e) Real integration test (opt-in, requires the cached PP-OCR models)
# ---------------------------------------------------------------------------

def _gt_union_overlap(start_sec: float, end_sec: float, gt, fps: float) -> float:
    """Fraction of the ROI time span covered by the union of gt intervals.

    A single-sample ROI has start == end; it still spans one frame, so the
    span is extended to at least one frame duration.
    """
    duration = max(end_sec - start_sec, 1.0 / fps)
    span_end = start_sec + duration
    overlap = sum(
        max(0.0, min(span_end, entry["end"]) - max(start_sec, entry["start"]))
        for entry in gt
    )
    return overlap / duration


@pytest.mark.skipif(os.environ.get("VIDEO_SUBTITLE_OCR_REAL_TEST") != "1", reason="real OCR test")
def test_real_video_suggests_bottom_rois_matching_gt():
    assert REAL_VIDEO.exists(), f"missing benchmark video: {REAL_VIDEO}"
    gt = json.loads(REAL_GT.read_text(encoding="utf-8"))

    cap = cv2.VideoCapture(str(REAL_VIDEO))
    assert cap.isOpened()
    fps = float(cap.get(cv2.CAP_PROP_FPS))
    width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    cap.release()
    assert fps > 0 and width == 1280 and height == 720

    rois = suggest_subtitle_rois(str(REAL_VIDEO), sample_count=8, lang="ch", model_tier="auto")

    assert len(rois) >= 1
    for roi in rois:
        assert set(roi.keys()) == {"start_time", "end_time", "start_frame", "end_frame", "type", "points"}
        assert roi["type"] == "rect"
        x, y, w, h = roi["points"]
        assert 0 <= x and 0 <= y and x + w <= width and y + h <= height
        # ROI must sit in the bottom 45% of the frame.
        assert (y + h / 2) >= height * (1 - 0.45), f"ROI not in bottom region: {roi}"
        # ROI time range must largely overlap the gt subtitle intervals.
        start_sec = roi["start_frame"] / fps
        end_sec = roi["end_frame"] / fps
        coverage = _gt_union_overlap(start_sec, end_sec, gt, fps)
        assert coverage >= 0.5, f"ROI {roi} covers only {coverage:.0%} of gt subtitle time"


# ---------------------------------------------------------------------------
# 倾斜字幕带 → 多边形 ROI（旋转矩形）
# ---------------------------------------------------------------------------

def _tilted_quad(cx: float, cy: float, length: float, height: float, tilt_deg: float):
    """以 (cx, cy) 为中心、长轴倾斜 tilt_deg 的文本行四边形顶点。"""
    t = math.radians(tilt_deg)
    ux, uy = math.cos(t), math.sin(t)
    vx, vy = -math.sin(t), math.cos(t)
    hl, hh = length / 2.0, height / 2.0
    return [
        [cx - hl * ux - hh * vx, cy - hl * uy - hh * vy],
        [cx + hl * ux - hh * vx, cy + hl * uy - hh * vy],
        [cx + hl * ux + hh * vx, cy + hl * uy + hh * vy],
        [cx - hl * ux + hh * vx, cy - hl * uy + hh * vy],
    ]


def _poly_long_edge_tilt_deg(points) -> float:
    pts = [np.array(p, dtype=float) for p in points]
    e1 = pts[1] - pts[0]
    e2 = pts[2] - pts[1]
    edge = e1 if (e1 @ e1) >= (e2 @ e2) else e2
    tilt = math.degrees(math.atan2(edge[1], edge[0]))
    if tilt > 90:
        tilt -= 180
    elif tilt <= -90:
        tilt += 180
    return tilt


def test_quad_of_line_prefers_poly_and_falls_back_to_bbox():
    quad = _quad_of_line([[1.0, 2.0], [3.0, 2.0], [3.0, 4.0], [1.0, 4.0]], (0, 0, 10, 10))
    assert quad[0] == [1.0, 2.0]
    fallback = _quad_of_line(None, (5.0, 6.0, 9.0, 12.0))
    assert fallback == [[5.0, 6.0], [9.0, 6.0], [9.0, 12.0], [5.0, 12.0]]
    assert _quad_of_line(None, None) == []


def test_axis_aligned_cluster_returns_none():
    quads = _tilted_quad(160, 210, 200, 20, 0.0) * 2
    assert cluster_poly_points_if_tilted(quads, WIDTH, HEIGHT, 20.0) is None


def test_small_tilt_within_threshold_returns_none():
    quads = _tilted_quad(160, 210, 200, 20, POLY_TILT_THRESHOLD_DEG - 1.0) * 2
    assert cluster_poly_points_if_tilted(quads, WIDTH, HEIGHT, 20.0) is None


def test_tilted_cluster_returns_padded_rotated_poly():
    tilt = 10.0
    quads = _tilted_quad(160, 210, 200, 20, tilt) + _tilted_quad(160, 208, 200, 20, tilt)
    poly = cluster_poly_points_if_tilted(quads, WIDTH, HEIGHT, 20.0)
    assert poly is not None
    assert len(poly) == 4
    # 长边倾角与输入字幕带的倾斜角一致
    assert abs(_poly_long_edge_tilt_deg(poly) - tilt) < 1.5
    # 顶点全部落在画面内
    for x, y in poly:
        assert 0 <= x < WIDTH and 0 <= y < HEIGHT
    # 长边外扩 2*X_PAD_PX=32、短边外扩 2*(0.6*20)=24
    a = np.array(poly, dtype=float)
    e1 = float(np.linalg.norm(a[1] - a[0]))
    e2 = float(np.linalg.norm(a[2] - a[1]))
    short, long = sorted((e1, e2))
    assert short > 20
    assert long > 200


def test_segment_to_roi_entry_rect_for_axis_aligned_hits():
    hits = [{
        "sample_pos": 0, "frame_idx": 5,
        "bbox": (50.0, 200.0, 250.0, 220.0),
        "quad": [[50.0, 200.0], [250.0, 200.0], [250.0, 220.0], [50.0, 220.0]],
        "height": 20.0, "center_y": 210.0, "text": "字幕",
    }]
    entry = _segment_to_roi_entry({"hits": hits}, fps=FPS, frame_width=WIDTH, frame_height=HEIGHT)
    assert entry["type"] == "rect"
    # 与历史行为一致：union bbox 外扩 x±16 / y±0.6*行高
    assert entry["points"] == [34, 188, 232, 44]


def test_segment_to_roi_entry_poly_for_tilted_hits():
    hits = []
    for i, cy in enumerate((210.0, 208.0)):
        quad = _tilted_quad(160, cy, 200, 20, 12.0)
        xs = [p[0] for p in quad]
        ys = [p[1] for p in quad]
        hits.append({
            "sample_pos": i, "frame_idx": i * 10,
            "bbox": (min(xs), min(ys), max(xs), max(ys)),
            "quad": quad,
            "height": 20.0, "center_y": cy, "text": "字幕",
        })
    entry = _segment_to_roi_entry({"hits": hits}, fps=FPS, frame_width=WIDTH, frame_height=HEIGHT)
    assert entry["type"] == "poly"
    assert len(entry["points"]) == 4
    assert abs(_poly_long_edge_tilt_deg(entry["points"]) - 12.0) < 1.5
    assert entry["start_frame"] == 0 and entry["end_frame"] == 10


# ---------------------------------------------------------------------------
# 倾斜判定回归：点云整体倾斜 ≠ 倾斜字幕带（真实 1080p 样本复现）
# ---------------------------------------------------------------------------

def _hquad(cx: float, cy: float, length: float, height: float):
    """水平文本行四边形顶点（与 _tilted_quad 的 0° 输出一致，显式写出）。"""
    hl, hh = length / 2.0, height / 2.0
    return [
        [cx - hl, cy - hh],
        [cx + hl, cy - hh],
        [cx + hl, cy + hh],
        [cx - hl, cy + hh],
    ]


def test_horizontal_lines_drift_positions_return_none():
    """水平字幕带（各行倾角 0°）即使点云整体呈对角分布也不得判为倾斜带。

    回归用例：1080p 对白字幕逐句位置/宽度漂移，全部点的外接矩形偏角
    ≈11°，旧实现据此输出对角多边形 ROI，OCR 截断宽字幕文本。
    """
    quads = (
        _hquad(200, 700, 300, 40)
        + _hquad(960, 950, 1100, 40)
        + _hquad(1700, 1000, 300, 40)
    )
    # 前置：该点云的最小外接矩形确实倾斜超过阈值（旧实现会误判）
    rect = cv2.minAreaRect(np.array(quads, dtype=np.float32))
    angle_dev = min(rect[2], 90.0 - rect[2])
    assert angle_dev > POLY_TILT_THRESHOLD_DEG
    assert cluster_poly_points_if_tilted(quads, 1920, 1080, 40.0) is None


def test_tilt_minority_among_horizontal_returns_none():
    """倾斜行占比不足（< POLY_TILT_MAJORITY_RATIO）时退回矩形路径。"""
    quads = (
        _hquad(400, 700, 900, 40)
        + _hquad(960, 800, 900, 40)
        + _tilted_quad(600, 900, 900, 40, 12.0)
    )
    assert cluster_poly_points_if_tilted(quads, 1920, 1080, 40.0) is None


def test_inconsistent_tilt_directions_return_none():
    """倾斜行方向不一致（+10° 与 -10°）时单一旋转矩形无法覆盖，退回矩形。"""
    quads = (
        _tilted_quad(400, 700, 900, 40, 10.0)
        + _tilted_quad(960, 800, 900, 40, -10.0)
    )
    assert cluster_poly_points_if_tilted(quads, 1920, 1080, 40.0) is None


def test_majority_tilted_ignores_horizontal_outlier():
    """多数行倾斜且方向一致时仍输出多边形，倾角取自倾斜行自身。"""
    quads = (
        _tilted_quad(400, 700, 900, 40, 12.0)
        + _tilted_quad(500, 760, 900, 40, 12.0)
        + _hquad(960, 1050, 900, 40)
    )
    poly = cluster_poly_points_if_tilted(quads, 1920, 1080, 40.0)
    assert poly is not None
    assert abs(_poly_long_edge_tilt_deg(poly) - 12.0) < 1.5
    # 多边形覆盖倾斜行，而不是被水平离群行拉成轴对齐
    assert abs(_poly_long_edge_tilt_deg(poly)) > POLY_TILT_THRESHOLD_DEG
