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
    assert "\\frz-12.5" in text
    assert "\\frx0.0" in text and "\\fry0.0" in text


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
    # rotation goes INSIDE the override block (outside `}` it would render
    # as literal on-screen text); numeric tags carry no parentheses
    # (\frz(8.0) is invalid ASS and silently ignored by libass).
    assert "{\\an5\\pos(300,325)\\frz8.0\\frx0.0\\fry0.0}" in text


def test_zero_tilt_rect_pose_still_writes_rotation_tags(tmp_path):
    """Pose enabled on an upright rect ROI: scene lines keep their detected
    \\pos but the full rotation block (zeros included) must be written, so
    the checkbox has a visible effect in the output (regression: a zero
    tilt used to append an empty string and the output was unchanged)."""
    conv = _build_converter(
        tmp_path,
        {"roi_0": {"pos": [960.5, 680.0], "frz": 0.0, "frx": 0.0, "fry": 0.0}},
    )
    items = [_make_ocr_item(f, "店铺招牌", (200, 300, 400, 350)) for f in range(10, 21)]
    conv.convert_from_memory(iter(items))

    text = (tmp_path / "out.ass").read_text(encoding="utf-8-sig")
    assert "{\\an5\\pos(300,325)\\frz0.0\\frx0.0\\fry0.0}" in text
    # ... and nothing leaks outside the override block as literal text
    assert "}\\frz" not in text and "}\\frx" not in text


def test_no_pose_tags_when_disabled(tmp_path):
    conv = _build_converter(tmp_path, None)
    items = [_make_ocr_item(f, "学成归来", (100, 600, 500, 650)) for f in range(10, 21)]
    conv.convert_from_memory(iter(items))
    text = (tmp_path / "out.ass").read_text(encoding="utf-8-sig")
    assert "\\frz" not in text
    assert "学成归来" in text


# ── Checkbox applies to the selected ROI immediately ────────────

class _StubListItem:
    pass


class _StubListView:
    def __init__(self, count: int, current_index: int):
        self._count = count
        self._current_index = current_index

    def currentItem(self):
        if 0 <= self._current_index < self._count:
            return _StubListItem()
        return None

    def row(self, _item):
        return self._current_index


class _StubLogger:
    def __init__(self):
        self.messages = []

    def info(self, message):
        self.messages.append(str(message))

    def warning(self, message):
        self.messages.append(str(message))


class _StubCheckable:
    def __init__(self, checked=False):
        self._checked = bool(checked)

    def isChecked(self):
        return self._checked


class _StubCombo:
    def __init__(self, data="overlap"):
        self._data = data

    def currentData(self):
        return self._data


class _StubRoiDefPanel:
    """详情面板最小桩:_sync_selected_roi_panel_flags 只读这四个控件。"""

    def __init__(self, pose=False, brightness=False, occlusion=False,
                 policy="overlap"):
        self.pose_tags_checkbox = _StubCheckable(pose)
        self.motion_brightness_checkbox = _StubCheckable(brightness)
        self.motion_occlusion_checkbox = _StubCheckable(occlusion)
        self.scene_text_policy_combo = _StubCombo(policy)


class _StubSelectableView:
    """带选中态的列表桩:_select_first_roi_without_seek 用。"""

    def __init__(self, count: int, current_index: int):
        self._count = count
        self.current_index = current_index
        self.set_rows = []

    def currentItem(self):
        if 0 <= self.current_index < self._count:
            return _StubListItem()
        return None

    def count(self):
        return self._count

    def setCurrentRow(self, row):
        self.set_rows.append(row)
        self.current_index = row


def _make_mixin_host(rois, current_index, roi_def=None, list_view=None):
    """Bare RoiEditingMixin instance with just the attributes the toggle
    handler touches (no QWidget instantiation — headless safe)."""
    from main_window.roi_editing import RoiEditingMixin

    class _StubListPanel:
        roi_list_widget = list_view or _StubListView(len(rois), current_index)

    host = RoiEditingMixin.__new__(RoiEditingMixin)
    host.roi_data = rois
    host.roi_list_widget = _StubListPanel()
    host.logger = _StubLogger()
    if roi_def is not None:
        host.roi_def_widget = roi_def
    return host


def test_pose_toggle_writes_selected_roi_immediately():
    roi = {"type": "rect", "points": [100, 200, 400, 80]}
    host = _make_mixin_host([roi], 0)

    host.on_pose_tags_toggled(True)
    assert roi["write_pose_tags"] is True
    assert roi["pose"]["pos"] == [300.0, 240.0]

    host.on_pose_tags_toggled(False)
    assert roi["write_pose_tags"] is False


def test_pose_toggle_poly_recomputes_tilt():
    pts = _rotated_poly_points(500, 400, 300, 60, 10.0, falling_right=True)
    roi = {"type": "poly", "points": pts}
    host = _make_mixin_host([roi], 0)

    host.on_pose_tags_toggled(True)
    assert abs(roi["pose"]["frz"] - (-10.0)) < 0.5


def test_pose_toggle_without_selection_is_noop():
    roi = {"type": "rect", "points": [100, 200, 400, 80]}
    host = _make_mixin_host([roi], -1)  # nothing selected in the list

    host.on_pose_tags_toggled(True)  # must not raise nor mutate
    assert "write_pose_tags" not in roi


# ── Motion companion options apply to the selected ROI immediately ──

def test_motion_flag_toggles_write_selected_roi_immediately():
    """亮度自适应/遮挡蒙版/场景策略:勾选或切换即写回——勾选后直接开始
    识别不再静默丢失(GUI 轨迹输出与 --auto-brightness/--occlusion-clip
    等价的前提)。"""
    roi = {"type": "poly", "points": [[10, 10], [50, 10], [50, 40], [10, 40]]}
    host = _make_mixin_host([roi], 0)

    host.on_motion_brightness_toggled(True)
    assert roi["motion_auto_brightness"] is True
    host.on_motion_occlusion_toggled(True)
    assert roi["motion_occlusion_clip"] is True
    host.on_scene_policy_changed("mask")
    assert roi["scene_text_policy"] == "mask"

    host.on_motion_brightness_toggled(False)
    assert roi["motion_auto_brightness"] is False
    host.on_scene_policy_changed("")  # 空值回退 overlap
    assert roi["scene_text_policy"] == "overlap"


def test_motion_flag_toggles_without_selection_are_noop():
    roi = {"type": "rect", "points": [100, 200, 400, 80]}
    host = _make_mixin_host([roi], -1)

    host.on_motion_brightness_toggled(True)
    host.on_motion_occlusion_toggled(True)
    host.on_scene_policy_changed("mask")
    assert "motion_auto_brightness" not in roi
    assert "motion_occlusion_clip" not in roi
    assert "scene_text_policy" not in roi


# ── 无选中行时的可见告警与启动前兜底同步（GUI 勾选丢失回归）────────

def test_toggle_without_selection_warns_only_when_rois_exist():
    """无选中行 + 已有 ROI 条目：写回丢弃必须留下可见告警（GUI 亮度自适应
    勾选丢失的根因就是这种状态下静默 return）；roi_data 为空的「先配置待
    新建 ROI」正常流程不打扰。"""
    roi = {"type": "rect", "points": [100, 200, 400, 80]}
    host = _make_mixin_host([roi], -1)

    host.on_motion_brightness_toggled(True)
    assert "motion_auto_brightness" not in roi
    assert any("未选中任何 ROI" in m for m in host.logger.messages)

    host_empty = _make_mixin_host([], -1)
    host_empty.on_motion_brightness_toggled(True)
    assert host_empty.logger.messages == []


def test_select_first_roi_without_seek_backfills_panel_state():
    """载入 ROI 后列表无选中 → 选中第一行并按住跳帧；已有选中或空列表
    时不动。"""
    view = _StubSelectableView(1, -1)
    host = _make_mixin_host(
        [{"type": "rect", "points": [0, 0, 10, 10], "start_frame": 30}], -1,
        list_view=view)

    host._select_first_roi_without_seek()
    assert view.set_rows == [0]
    assert view.current_index == 0
    assert host._suppress_selection_seek is False  # 用后复位

    # 已有选中：不再改选
    view2 = _StubSelectableView(2, 1)
    host2 = _make_mixin_host([], 1, list_view=view2)
    host2._select_first_roi_without_seek()
    assert view2.set_rows == []

    # 空列表：无行可选
    view3 = _StubSelectableView(0, -1)
    host3 = _make_mixin_host([], -1, list_view=view3)
    host3._select_first_roi_without_seek()
    assert view3.set_rows == []


def test_sync_selected_roi_panel_flags_writes_selection():
    """启动识别前以面板为准写回选中条目的四个逐 ROI 开关（含 pose 重算），
    无选中时不动任何条目——「面板显示已勾选、识别按旧值跑」的最终兜底。"""
    roi = {"type": "rect", "points": [100, 200, 400, 80],
           "motion_auto_brightness": False}
    panel = _StubRoiDefPanel(pose=True, brightness=True, occlusion=False,
                             policy="mask")
    host = _make_mixin_host([roi], 0, roi_def=panel)

    host._sync_selected_roi_panel_flags()
    assert roi["write_pose_tags"] is True
    assert roi["pose"]["pos"] == [300.0, 240.0]
    assert roi["motion_auto_brightness"] is True
    assert roi["motion_occlusion_clip"] is False
    assert roi["scene_text_policy"] == "mask"

    # pose 关闭时与 on_pose_tags_toggled(False) 同契约：仅置 False，
    # 旧 pose 键保留（管线以 write_pose_tags 为准，不会输出位置标签）
    panel2 = _StubRoiDefPanel(pose=False, policy="overlap")
    roi2 = {"type": "rect", "points": [100, 200, 400, 80],
            "write_pose_tags": True, "pose": {"pos": [1.0, 2.0]}}
    host2 = _make_mixin_host([roi2], 0, roi_def=panel2)
    host2._sync_selected_roi_panel_flags()
    assert roi2["write_pose_tags"] is False
    assert roi2["pose"] == {"pos": [1.0, 2.0]}  # 原值未动
    assert roi2["scene_text_policy"] == "overlap"


def test_sync_selected_roi_panel_flags_without_selection_is_noop():
    roi = {"type": "rect", "points": [100, 200, 400, 80]}
    host = _make_mixin_host([roi], -1, roi_def=_StubRoiDefPanel(pose=True))

    host._sync_selected_roi_panel_flags()
    assert "write_pose_tags" not in roi


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
             tags="{\\an5\\pos(300,325)\\frz8.0}"),
        dict(base, start_time="0:00:03.00", end_time="0:00:04.00",
             tags="{\\an5\\pos(302,326)\\frz8.0}"),
    ]
    merged = conv._merge_temporal_near_duplicate_events(events)
    assert len(merged) == 1
    assert "\\frz8.0" in merged[0]["tags"]
    assert "\\pos(30" in merged[0]["tags"]
