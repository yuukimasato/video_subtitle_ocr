# tests/test_occlusion_mask.py
"""手部遮挡检测与 \\iclip 蒙版(core/occlusion_mask.py)离线单元测试。

- detect_occlusion_polygons:恒等单应下,展开图(= 原图)与锚定帧灰度差
  圈出遮挡矩形;无遮挡返回空;尺寸不符防御返回空;
- collect_occlusions:合成视频(FFV1)后半段叠加遮挡块 → 只在遮挡帧检出;
- attach_occlusion_clips:遮挡跨度内的事件追加 \\iclip(动画/静态/并集),
  跨度外事件与无遮挡输入逐字节原样;行框相交占比门限生效;
  \iclip 坐标经 H(init→frame) 映射(平移轨迹下随帧右移)。

平面用合成卡片(亮底 + 暗色「文字」条),遮挡物为中灰矩形(与锚定图差
> diff_tol),不读真模型、不依赖 ffmpeg。
"""

from __future__ import annotations

import os
import re
import sys

import cv2
import numpy as np
import pytest

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from core.occlusion_mask import (  # noqa: E402
    OcclusionConfig,
    attach_occlusion_clips,
    build_clip_tag,
    collect_occlusions,
    detect_occlusion_polygons,
)
from core.scene_plane_tracker import TrackedQuad  # noqa: E402

FPS = 25.0
PLANE_W, PLANE_H = 240, 160
BOX = (100.0, 100.0, 200.0, 150.0)  # 行框(平面坐标)
OCCLUDER = (120, 110, 180, 140)     # 遮挡矩形(平面坐标,x0,y0,x1,y1)

# 合成平面:亮底 245 + 两行暗色「文字」条(与 scripts 测试的卡片同模式)
_BARS = [(30, 30, 200, 44), (40, 70, 160, 84)]


def make_plane(*, occluder: bool = False) -> np.ndarray:
    plane = np.full((PLANE_H, PLANE_W, 3), 245, np.uint8)
    for x1, y1, x2, y2 in _BARS:
        cv2.rectangle(plane, (x1, y1), (x2, y2), (10, 10, 10), -1)
    if occluder:
        cv2.rectangle(plane, OCCLUDER[:2], OCCLUDER[2:], (150, 140, 130), -1)
    return plane


def make_tracks(n: int, dx: float = 2.0) -> list:
    """恒等单应 + 平移 (dx·t, 0) 的 ok 轨迹(quad 值仅占位)。"""
    tracks = []
    for i in range(n):
        h = np.array([
            [1.0, 0.0, dx * i],
            [0.0, 1.0, 0.0],
            [0.0, 0.0, 1.0],
        ], dtype=np.float64)
        tracks.append(TrackedQuad(
            frame_num=i, time_sec=i / FPS, status="ok",
            quad=[[0.0, 0.0], [1.0, 0.0], [1.0, 1.0], [0.0, 1.0]],
            homography=[[float(v) for v in row] for row in h],
            homography_inv=[[float(v) for v in row]
                            for row in np.linalg.inv(h)],
        ))
    return tracks


def make_line_track(text: str = "あいう") -> "LineTrack":  # noqa: F821
    from core.motion_ass import LinePose, LineTrack

    poses = {i: LinePose(center=(150.0 + 2.0 * i, 125.0),
                         angle_deg=0.0, scale=1.0) for i in range(20)}
    return LineTrack(text=text, ref_box=BOX, height=50.0, poses=poses)


def make_event(line_idx: int = 0) -> dict:
    from core.motion_ass import format_ass_time

    return {
        "start_time": format_ass_time(0.0),
        "end_time": format_ass_time(19 / FPS),
        "style": "Scene",
        "name": "motion",
        "tags": "{\\an5\\fs50\\move(150.0,125.0,188.0,125.0,0,760)}",
        "body": "あいう",
        "line_idx": line_idx,
    }


IDENTITY = np.eye(3)
CFG = OcclusionConfig()


# ---------------------------------------------------------------------------
# detect_occlusion_polygons
# ---------------------------------------------------------------------------

class TestDetectOcclusionPolygons:
    def test_occluder_region_detected(self):
        anchor = cv2.cvtColor(make_plane(), cv2.COLOR_BGR2GRAY)
        frame = make_plane(occluder=True)
        polys = detect_occlusion_polygons(
            frame, IDENTITY, anchor, (PLANE_W, PLANE_H), (0, 0),
            diff_tol=CFG.diff_tol, min_area_px=CFG.min_area_px,
            epsilon_px=CFG.epsilon_px, morph_kernel=CFG.morph_kernel)
        assert polys, "occluder rectangle must yield at least one polygon"
        # 多边形并集的外接框应基本覆盖遮挡矩形(平面坐标)
        all_pts = np.vstack(polys)
        x0, y0 = all_pts.min(axis=0)
        x1, y1 = all_pts.max(axis=0)
        assert x0 <= OCCLUDER[0] + 8 and y0 <= OCCLUDER[1] + 8
        assert x1 >= OCCLUDER[2] - 8 and y1 >= OCCLUDER[3] - 8

    def test_no_occluder_returns_empty(self):
        anchor = cv2.cvtColor(make_plane(), cv2.COLOR_BGR2GRAY)
        frame = make_plane()
        assert detect_occlusion_polygons(
            frame, IDENTITY, anchor, (PLANE_W, PLANE_H), (0, 0)) == []

    def test_anchor_size_mismatch_defensive(self):
        anchor = cv2.cvtColor(make_plane(), cv2.COLOR_BGR2GRAY)[:80, :120]
        frame = make_plane(occluder=True)
        assert detect_occlusion_polygons(
            frame, IDENTITY, anchor, (PLANE_W, PLANE_H), (0, 0)) == []

    def test_global_dimming_is_not_occlusion(self):
        # 整平面调暗(背光调节/调暗剧情):亮度归一后差分≈0;覆盖率门限兜底
        anchor = cv2.cvtColor(make_plane(), cv2.COLOR_BGR2GRAY)
        dim = cv2.convertScaleAbs(make_plane(), alpha=0.15)  # 245→≈37
        assert detect_occlusion_polygons(
            dim, IDENTITY, anchor, (PLANE_W, PLANE_H), (0, 0)) == []

    def test_partial_dim_plus_occluder_still_detected(self):
        # 调暗背景上的真实局部遮挡(手)仍应检出:归一只抵消等比明暗
        anchor = cv2.cvtColor(make_plane(), cv2.COLOR_BGR2GRAY)
        dim_occluder = cv2.convertScaleAbs(make_plane(occluder=True),
                                           alpha=0.15)
        # 手在调暗平面上:亮度归一(anchor_med/frame_med)后手区域差分保留
        polys = detect_occlusion_polygons(
            dim_occluder, IDENTITY, anchor, (PLANE_W, PLANE_H), (0, 0),
            diff_tol=CFG.diff_tol, min_area_px=CFG.min_area_px)
        assert polys, "partial occluder on dimmed plane must be detected"

    def test_coverage_gate_filters_whole_plane_change(self):
        # 内容大幅变化且非等比(噪声整屏重铺)→ 覆盖率超门限 → 不判遮挡
        anchor = cv2.cvtColor(make_plane(), cv2.COLOR_BGR2GRAY)
        rng = np.random.default_rng(5)
        noise = rng.integers(0, 256, (PLANE_H, PLANE_W, 3), dtype=np.uint8)
        assert detect_occlusion_polygons(
            noise, IDENTITY, anchor, (PLANE_W, PLANE_H), (0, 0)) == []


# ---------------------------------------------------------------------------
# collect_occlusions
# ---------------------------------------------------------------------------

def write_video(path, frames) -> str:
    writer = cv2.VideoWriter(
        str(path), cv2.VideoWriter_fourcc(*"FFV1"), float(FPS),
        (PLANE_W, PLANE_H))
    if not writer.isOpened():
        pytest.skip("no usable video codec (FFV1) for occlusion tests")
    for frame in frames:
        writer.write(frame)
    writer.release()
    return str(path)


class TestCollectOcclusions:
    def test_only_occluded_frames_reported(self, tmp_path):
        n = 12
        frames = [make_plane() for _ in range(n)]
        for i in range(6, n):
            frames[i] = make_plane(occluder=True)
        video = write_video(tmp_path / "occ.avi", frames)
        anchor = cv2.cvtColor(make_plane(), cv2.COLOR_BGR2GRAY)

        # 平面静止 → 轨迹恒等:展开图逐位复原,只有真遮挡产生灰度差
        occl = collect_occlusions(
            video, make_tracks(n, dx=0.0), anchor_gray=anchor,
            plane_size=(PLANE_W, PLANE_H), origin=(0, 0), cfg=CFG)

        assert set(occl) <= set(range(6, n))
        assert occl, "occluded tail must be detected"

    def test_unopenable_video_raises(self):
        anchor = cv2.cvtColor(make_plane(), cv2.COLOR_BGR2GRAY)
        with pytest.raises(RuntimeError):
            collect_occlusions(
                "/nonexistent/no_such.avi", make_tracks(2),
                anchor_gray=anchor, plane_size=(PLANE_W, PLANE_H),
                origin=(0, 0), cfg=CFG)


# ---------------------------------------------------------------------------
# attach_occlusion_clips
# ---------------------------------------------------------------------------

def _floats(tag: str) -> list:
    return [float(v) for v in re.findall(r"-?\d+\.?\d*", tag)]


class TestAttachOcclusionClips:
    def test_no_occlusions_returns_identical(self):
        events = [make_event()]
        out = attach_occlusion_clips(
            events, tracks=make_tracks(20), line_tracks=[make_line_track()],
            occlusions={}, cfg=CFG)
        assert out == events

    def test_single_hit_static_clip(self):
        # 遮挡只落在采样首帧(帧 0)→ 静态 \iclip,无 \t 动画
        poly = np.array([[120.0, 110.0], [180.0, 110.0],
                         [180.0, 140.0], [120.0, 140.0]])
        occl = {0: [poly]}
        events = [make_event()]
        out = attach_occlusion_clips(
            events, tracks=make_tracks(20), line_tracks=[make_line_track()],
            occlusions=occl, cfg=CFG)
        assert len(out) == 1
        assert "\\iclip(" in out[0]["tags"]
        assert "\\t(0," not in out[0]["tags"].split("\\iclip(", 1)[1]

    def test_multi_hit_animated_clip(self):
        # 首末命中帧多边形个数相同 → \iclip + \t(0,ms,\iclip(...)) 动画;
        # 平移轨迹下,末帧(19)坐标比首帧右移 2×19-0:tag 内最大 x 应 ≥
        # 遮挡右缘 180 + 2×19 - 容差。
        poly = np.array([[120.0, 110.0], [180.0, 110.0],
                         [180.0, 140.0], [120.0, 140.0]])
        occl = {0: [poly.copy()], 19: [poly.copy()]}
        events = [make_event()]
        out = attach_occlusion_clips(
            events, tracks=make_tracks(20), line_tracks=[make_line_track()],
            occlusions=occl, cfg=CFG)
        tag = out[0]["tags"]
        assert tag.count("\\iclip(") == 2
        assert "\\t(0,760,\\iclip(" in tag
        # 平面 → 画面坐标映射:帧 19 的多边形整体右移 38px
        clip_part = tag.split("{", 1)[1]
        assert max(_floats(clip_part)) >= 180.0 + 38.0 - 1.0

    def test_occlusion_outside_line_box_ignored(self):
        # 遮挡在行框之外(min_line_overlap 门限)→ 事件原样
        poly = np.array([[20.0, 10.0], [60.0, 10.0], [60.0, 40.0], [20.0, 40.0]])
        occl = {0: [poly], 19: [poly.copy()]}
        events = [make_event()]
        out = attach_occlusion_clips(
            events, tracks=make_tracks(20), line_tracks=[make_line_track()],
            occlusions=occl, cfg=CFG)
        assert out == events

    def test_count_mismatch_falls_back_to_static_union(self):
        poly = np.array([[120.0, 110.0], [180.0, 110.0],
                         [180.0, 140.0], [120.0, 140.0]])
        poly2 = np.array([[130.0, 112.0], [190.0, 112.0],
                          [190.0, 142.0], [130.0, 142.0]])
        hits = [(0, [poly.copy()]), (19, [poly.copy(), poly2.copy()])]
        tag = build_clip_tag(hits, {f: t for f, t in enumerate(make_tracks(20))},
                             CFG, 0.0, 19 / FPS)
        assert tag.startswith("\\iclip(")
        assert "\\t(" not in tag
        # 并集:首末两帧共 3 个多边形子路径
        assert tag.count("m ") == 3

    def test_events_without_line_idx_untouched(self):
        poly = np.array([[120.0, 110.0], [180.0, 110.0],
                         [180.0, 140.0], [120.0, 140.0]])
        occl = {0: [poly.copy()], 19: [poly.copy()]}
        ev = make_event()
        del ev["line_idx"]
        out = attach_occlusion_clips(
            [ev], tracks=make_tracks(20), line_tracks=[make_line_track()],
            occlusions=occl, cfg=CFG)
        assert out == [ev]
