# tests/test_fullframe_scanner.py
"""Unit tests for core/fullframe_scanner.py (offline, probe OCR engine).

Synthesizes a small video with three kinds of text and verifies the census:
- bottom subtitle band whose text changes over time → one band ROI
  (source="auto", text_filter_policy="auto", fade refine default-on);
- constant top-right watermark text → watermark candidate, excluded from
  band clustering;
- mid-frame scene text → scene candidate with a keep-all ROI entry.
"""

from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np
import pytest

from core import ocr_engine_manager
from core.fullframe_scanner import perform_scan

WIDTH, HEIGHT, FPS, N_FRAMES = 320, 240, 10.0, 120
SUB_BAND = (60, 200, 260, 220)      # bottom subtitle band rows
WATERMARK_BAND = (200, 30, 300, 40)  # top-right watermark rows
SCENE_BAND = (70, 110, 200, 122)     # mid-frame scene text rows
WHITE = (255, 255, 255)
RED = (0, 0, 255)
GREEN = (0, 160, 0)


def _write_video(path: Path) -> Path:
    for fourcc, ext in ((cv2.VideoWriter_fourcc(*"FFV1"), ".avi"),
                        (cv2.VideoWriter_fourcc(*"mp4v"), ".mp4")):
        candidate = path.with_suffix(ext)
        writer = cv2.VideoWriter(str(candidate), fourcc, FPS, (WIDTH, HEIGHT))
        if not writer.isOpened():
            continue
        for i in range(N_FRAMES):
            img = np.zeros((HEIGHT, WIDTH, 3), dtype=np.uint8)
            # constant watermark on every frame
            x1, y1, x2, y2 = WATERMARK_BAND
            img[y1:y2, x1:x2] = WHITE
            # bottom subtitle: phase A frames 20-60, phase B frames 60-90
            x1, y1, x2, y2 = SUB_BAND
            if 20 <= i < 60:
                img[y1:y2, x1:x2] = WHITE
                img[4, 4] = GREEN  # marker: phase A text
            elif 60 <= i < 90:
                img[y1:y2, x1:x2] = WHITE
                img[4, 4] = RED    # marker: phase B text
            # mid-frame scene text in frames 50-70
            if 50 <= i < 70:
                x1, y1, x2, y2 = SCENE_BAND
                img[y1:y2, x1:x2] = WHITE
                img[8, 8] = GREEN  # marker: scene line present
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


def _line(x1, y1, x2, y2, text, score=0.95):
    poly = [[float(x1), float(y1)], [float(x2), float(y1)],
            [float(x2), float(y2)], [float(x1), float(y2)]]
    return {
        "poly": poly, "text": text, "score": score,
        "box": [int(x1), int(y1), int(x2), int(y2)],
    }


class ProbeEngine:
    """Emits watermark + subtitle + scene lines based on pixel markers."""

    def predict(self, frame):
        # OpenCV pixel order is BGR.
        blue, green, red = (int(v) for v in frame[4, 4])
        scene_blue, scene_green, scene_red = (int(v) for v in frame[8, 8])
        lines = []
        for x1, x2 in _white_runs(frame, 35):
            lines.append(_line(x1, WATERMARK_BAND[1], x2, WATERMARK_BAND[3], "示例水印"))
        if green > 120 and blue < 80:  # phase A marker
            for x1, x2 in _white_runs(frame, 210):
                lines.append(_line(x1, SUB_BAND[1], x2, SUB_BAND[3], "第一句台词"))
        if red > 150 and green < 80:  # phase B marker
            for x1, x2 in _white_runs(frame, 210):
                lines.append(_line(x1, SUB_BAND[1], x2, SUB_BAND[3], "第二句台词"))
        if scene_green > 120 and scene_blue < 80:  # scene marker
            for x1, x2 in _white_runs(frame, 115):
                lines.append(_line(x1, SCENE_BAND[1], x2, SCENE_BAND[3], "场景招牌"))
        return [{
            "dt_polys": [ln["poly"] for ln in lines],
            "rec_polys": [ln["poly"] for ln in lines],
            "rec_texts": [ln["text"] for ln in lines],
            "rec_scores": [ln["score"] for ln in lines],
            "rec_boxes": [ln["box"] for ln in lines],
        }]

    def normalize_result(self, raw):
        return {k: list(v) for k, v in raw[0].items()}


@pytest.fixture
def probe_env(monkeypatch):
    engine = ProbeEngine()
    monkeypatch.setattr(ocr_engine_manager, "set_engine", lambda *a, **k: None)
    monkeypatch.setattr(ocr_engine_manager, "get_engine", lambda: engine)
    return engine


def test_scan_classifies_band_watermark_scene(tmp_path, probe_env):
    video = _write_video(tmp_path / "scan")
    report = perform_scan(str(video), sample_count=12, with_snapshots=False)

    assert report.fps == FPS and report.total_frames == N_FRAMES
    assert len(report.sample_indices) == 12

    # Watermark: constant text + constant position on every sample.
    assert len(report.watermarks) == 1
    wm = report.watermarks[0]
    assert wm["text"] == "示例水印"
    assert wm["presence"] == pytest.approx(1.0)
    assert wm["bbox"][1] < HEIGHT * 0.25  # top region

    # Bottom band: one ROI, auto policy, fade refine on, in the bottom half.
    assert len(report.band_rois) == 1
    band = report.band_rois[0]
    assert band["source"] == "auto"
    assert band["text_filter_policy"] == "auto"
    assert band["fade_in_refine_enabled"] is True
    assert band["band_region"] == "bottom"
    x, y, w, h = band["points"]
    assert y > HEIGHT * 0.55
    hits = [i for i in report.sample_indices if 20 <= i < 90]
    assert band["start_frame"] <= min(hits)
    assert band["end_frame"] >= max(hits)

    # Scene text: mid-frame cluster with a keep-all ROI entry.
    scene_texts = [c["text"] for c in report.scene_candidates]
    assert any("场景招牌" in t for t in scene_texts)
    scene_entry = next(
        c["roi_entry"] for c in report.scene_candidates if "场景招牌" in c["text"]
    )
    assert scene_entry["text_filter_policy"] == "keep_all"
    assert scene_entry["fade_in_refine_enabled"] is False

    # Watermark hits must not leak into band clustering (top band is absent).
    assert all(r.get("band_region") != "top" for r in report.band_rois)


def test_scan_with_snapshots_embeds_jpeg(tmp_path, probe_env):
    video = _write_video(tmp_path / "snap")
    report = perform_scan(str(video), sample_count=6, with_snapshots=True)
    assert report.watermarks and report.watermarks[0]["snapshot_jpeg"]
    jpeg = report.watermarks[0]["snapshot_jpeg"]
    assert jpeg[:2] == b"\xff\xd8"  # JPEG SOI


def test_scan_missing_video_returns_empty_report(tmp_path, probe_env):
    report = perform_scan(str(tmp_path / "missing.mp4"))
    assert report.is_empty
    assert report.band_rois == [] and report.watermarks == []


# ---------------------------------------------------------------------------
# 倾斜字幕带 → poly ROI（band 聚类转换层）
# ---------------------------------------------------------------------------

def _band_hit(frame_idx, quad, score=0.95):
    xs = [p[0] for p in quad]
    ys = [p[1] for p in quad]
    return {
        "sample_pos": frame_idx, "frame_idx": frame_idx,
        "bbox": (min(xs), min(ys), max(xs), max(ys)),
        "quad": quad,
        "height": max(1.0, max(ys) - min(ys)),
        "center_y": (min(ys) + max(ys)) / 2.0,
        "text": "字幕",
        "region": "bottom",
    }


def _tilted_quad(cx, cy, length, height, tilt_deg):
    import math
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


def test_band_cluster_tilted_hits_yield_poly_roi():
    from core.fullframe_scanner import _band_cluster_to_roi_entry

    hits = [
        _band_hit(10, _tilted_quad(160, 210, 200, 20, 9.0)),
        _band_hit(30, _tilted_quad(160, 208, 200, 20, 9.0)),
    ]
    entry = _band_cluster_to_roi_entry(
        hits, "bottom", FPS, WIDTH, HEIGHT, N_FRAMES, range_pad_frames=5,
    )
    assert entry["type"] == "poly"
    assert len(entry["points"]) == 4
    for x, y in entry["points"]:
        assert 0 <= x < WIDTH and 0 <= y < HEIGHT
    # 富化字段保持不变
    assert entry["source"] == "auto"
    assert entry["text_filter_policy"] == "auto"
    assert entry["band_region"] == "bottom"


def test_band_cluster_axis_aligned_hits_keep_rect_roi():
    from core.fullframe_scanner import _band_cluster_to_roi_entry

    hits = [_band_hit(10, _tilted_quad(160, 210, 200, 20, 0.0))]
    entry = _band_cluster_to_roi_entry(
        hits, "bottom", FPS, WIDTH, HEIGHT, N_FRAMES, range_pad_frames=5,
    )
    assert entry["type"] == "rect"
    x, y, w, h = entry["points"]
    assert 0 <= x and 0 <= y and x + w <= WIDTH and y + h <= HEIGHT
