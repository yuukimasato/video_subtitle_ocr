# tests/test_roi_pose_tags.py
"""Tests for ROI pose metadata (\\pos \\frz \\frx \\fry) support.

Covers:
- SubtitleOCRGUI._compute_roi_pose: rect center pose; polygon tilt via the
  minimum-area rectangle's long edge (ASS \\frz is counterclockwise-positive).
- OCRToASSOptimizer: events from a ROI with pose tags enabled are written
  with {\an5\\pos(...)} and rotation tags; scene lines keep their own
  detected position and only gain the rotation.
"""

from __future__ import annotations

import math
import os
import sys

import numpy as np
import pytest

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from core.subtitle_generator import OCRToASSOptimizer


# ── Pose computation ────────────────────────────────────────────

def test_compute_rect_pose_center_no_rotation():
    from main_window import SubtitleOCRGUI

    pose = SubtitleOCRGUI._compute_roi_pose("rect", [100, 200, 400, 80])
    assert pose["pos"] == [300.0, 240.0]
    assert pose["frz"] == 0.0
    assert pose["frx"] == 0.0 and pose["fry"] == 0.0


def _rotated_poly_points(cx, cy, length, height, tilt_deg, falling_right):
    th = math.radians(tilt_deg)
    # Screen coords: Y grows downward. A text line "falling to the right"
    # visually has direction (cos, +sin); "rising to the right" (cos, -sin).
    dy = math.sin(th) if falling_right else -math.sin(th)
    d = np.array([math.cos(th), dy])
    p = np.array([-d[1], d[0]])
    c = np.array([cx, cy], dtype=float)
    L, H = length / 2.0, height / 2.0
    corners = [c + L * d + H * p, c - L * d + H * p, c - L * d - H * p, c + L * d - H * p]
    return [[float(x), float(y)] for x, y in corners]


def test_compute_poly_pose_falling_right_gives_negative_frz():
    from main_window import SubtitleOCRGUI

    pts = _rotated_poly_points(500, 400, 300, 60, 10.0, falling_right=True)
    pose = SubtitleOCRGUI._compute_roi_pose("poly", pts)
    assert abs(pose["pos"][0] - 500.0) < 1.0
    assert abs(pose["pos"][1] - 400.0) < 1.0
    assert abs(pose["frz"] - (-10.0)) < 0.5


def test_compute_poly_pose_rising_right_gives_positive_frz():
    from main_window import SubtitleOCRGUI

    pts = _rotated_poly_points(500, 400, 300, 60, 10.0, falling_right=False)
    pose = SubtitleOCRGUI._compute_roi_pose("poly", pts)
    assert abs(pose["frz"] - 10.0) < 0.5


def test_compute_poly_pose_invalid_points():
    from main_window import SubtitleOCRGUI

    assert SubtitleOCRGUI._compute_roi_pose("poly", [[1, 2]]) is None
    assert SubtitleOCRGUI._compute_roi_pose("unknown", [[1, 2], [3, 4], [5, 6]]) is None


# ── ASS tag writing ─────────────────────────────────────────────

def _make_ocr_item(frame_num, text, box):
    poly = [[box[0], box[1]], [box[2], box[1]], [box[2], box[3]], [box[0], box[3]]]
    data = {
        "dt_polys": [poly],
        "rec_polys": [poly],
        "rec_texts": [text],
        "rec_scores": [0.95],
        "rec_boxes": [list(box)],
    }
    return (data, frame_num, "roi_0", frame_num / 25.0)


def _build_converter(tmp_path, roi_pose_tags, width=1280, height=720):
    return OCRToASSOptimizer(
        video_path=str(tmp_path / "in.mp4"),
        output_path=str(tmp_path / "out.ass"),
        fps=25.0,
        width=width,
        height=height,
        roi_pose_tags=roi_pose_tags,
    )


def test_pose_tags_written_for_bottom_subtitle(tmp_path):
    conv = _build_converter(
        tmp_path,
        {"roi_0": {"pos": [640.0, 620.0], "frz": -12.5, "frx": 0.0, "fry": 0.0}},
    )
    # Box centered at y=625 > 0.75*720 → BOTTOM style (margin-based placement).
    items = [_make_ocr_item(f, "学成归来", (100, 600, 500, 650)) for f in range(10, 21)]
    conv.convert_from_memory(iter(items))

    text = (tmp_path / "out.ass").read_text(encoding="utf-8-sig")
    assert "\\an5" in text
    assert "\\pos(640.0,620.0)" in text
    assert "\\frz(-12.5)" in text
    assert "\\frx(0.0)" in text and "\\fry(0.0)" in text


def test_scene_lines_keep_own_pos_and_gain_rotation(tmp_path):
    conv = _build_converter(
        tmp_path,
        {"roi_0": {"pos": [640.0, 360.0], "frz": 8.0, "frx": 0.0, "fry": 0.0}},
    )
    # Box centered at y=325 → between 0.15H and 0.75H → SCENE style: the line
    # already carries {\an5\pos(x,y)} from its detected box.
    items = [_make_ocr_item(f, "店铺招牌", (200, 300, 400, 350)) for f in range(10, 21)]
    conv.convert_from_memory(iter(items))

    text = (tmp_path / "out.ass").read_text(encoding="utf-8-sig")
    assert "\\pos(300,325)" in text  # own detected position preserved
    assert "\\frz(8.0)" in text      # rotation appended


def test_no_pose_tags_when_disabled(tmp_path):
    conv = _build_converter(tmp_path, None)
    items = [_make_ocr_item(f, "学成归来", (100, 600, 500, 650)) for f in range(10, 21)]
    conv.convert_from_memory(iter(items))
    text = (tmp_path / "out.ass").read_text(encoding="utf-8-sig")
    assert "\\frz" not in text
    assert "学成归来" in text


# ── Merge keeps rotation tags ───────────────────────────────────

def test_parse_pos_from_tags_accepts_float_positions(tmp_path):
    conv = _build_converter(tmp_path, None)
    assert conv._parse_pos_from_tags("{\\an5\\pos(978.0,529.5)}") == (978.0, 529.5)
    assert conv._parse_pos_from_tags("{\\an5\\pos(978,529)}") == (978.0, 529.0)
    assert conv._parse_pos_from_tags("no tags") is None


def test_scene_merge_preserves_rotation_tags(tmp_path):
    """Regression: merging near-duplicate Scene events used to rebuild the
    tags as {\\an5\\pos(x,y)} only, silently dropping the rotation that
    _apply_roi_pose_tags had appended."""
    conv = _build_converter(tmp_path, None)
    base = {"roi": "roi_0", "style": "Scene", "body": "店铺招牌"}
    events = [
        dict(base, start_time="0:00:01.00", end_time="0:00:02.00",
             tags="{\\an5\\pos(300,325)\\frz(8.0)}"),
        dict(base, start_time="0:00:03.00", end_time="0:00:04.00",
             tags="{\\an5\\pos(302,326)\\frz(8.0)}"),
    ]
    merged = conv._merge_temporal_near_duplicate_events(events)
    assert len(merged) == 1
    assert "\\frz(8.0)" in merged[0]["tags"]
    assert "\\pos(30" in merged[0]["tags"]
