# tests/test_motion_ass.py
"""motion_ass 合成器(core/motion_ass.py)单元测试。

Task 4(轨迹重建与平滑):
- homography_between 链乘与往返恒等;lost/缺失帧抛 KeyError;
- build_line_tracks:平移轨迹 center 误差 <0.5px、scale≈1、angle≈0;
  旋转轨迹 angle 递增;lost 帧无 pose;lost 参考帧重锚定;
- smooth_line_track:不改变线性趋势(斜率偏差 <1%)、衰减高频抖动;
- format_ass_time 与主流水线一致的 "H:MM:SS.CC" 格式往返。

Task 5(分段与标签阶梯)见文件后半部分。
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

from core.motion_ass import (  # noqa: E402
    LinePose,
    LineTrack,
    MotionAssConfig,
    brightness_tag_chain,
    build_line_tracks,
    format_ass_time,
    homography_between,
    simplify_and_segment,
    simplify_luma_curve,
    smooth_line_track,
    synthesize_events,
)
from core.scene_plane_tracker import TrackedQuad  # noqa: E402

FPS = 25.0
BOX = (100.0, 100.0, 200.0, 150.0)  # 平面坐标行框 w=100, h=50
BOX_CENTER = (150.0, 125.0)
BOX2 = (100.0, 200.0, 300.0, 240.0)  # 第二行,验证行顺序


# ---------------------------------------------------------------------------
# 合成轨迹构造工具
# ---------------------------------------------------------------------------

def mat_list(h_mat: np.ndarray) -> list:
    return [[float(v) for v in row] for row in h_mat]


def translation(dx: float = 0.0, dy: float = 0.0) -> np.ndarray:
    return np.array([
        [1.0, 0.0, dx],
        [0.0, 1.0, dy],
        [0.0, 0.0, 1.0],
    ], dtype=np.float64)


def rotation_about(cx: float, cy: float, deg: float) -> np.ndarray:
    t = math.radians(deg)
    r = np.array([
        [math.cos(t), -math.sin(t), 0.0],
        [math.sin(t), math.cos(t), 0.0],
        [0.0, 0.0, 1.0],
    ])
    to = translation(cx, cy)
    back = translation(-cx, -cy)
    return to @ r @ back


def box_corners(box=BOX) -> np.ndarray:
    x1, y1, x2, y2 = box
    return np.array([[x1, y1], [x2, y1], [x2, y2], [x1, y2]], dtype=np.float64)


def make_tracks(homogs, fps: float = FPS, lost=()) -> list:
    """由逐帧 H(init→frame) 构造 TrackedQuad 列表(lost 帧 quad/H 为 None)。"""
    tracks = []
    base = box_corners()
    for i, h_mat in enumerate(homogs):
        t_sec = i / fps
        if i in lost:
            tracks.append(TrackedQuad(frame_num=i, time_sec=t_sec, status="lost"))
            continue
        h_arr = np.asarray(h_mat, dtype=np.float64)
        hom = np.hstack([base, np.ones((4, 1))])
        proj = hom @ h_arr.T
        quad = (proj[:, :2] / proj[:, 2:3]).tolist()
        tracks.append(TrackedQuad(
            frame_num=i, time_sec=t_sec, status="ok",
            quad=quad, homography=mat_list(h_arr),
            homography_inv=mat_list(np.linalg.inv(h_arr)),
        ))
    return tracks


def translation_tracks(n: int, dx: float = 2.0, dy: float = 0.0, lost=()) -> list:
    return make_tracks([translation(dx * i, dy * i) for i in range(n)], lost=lost)


def parse_ass_time(s: str) -> float:
    h, m, rest = s.split(":")
    return int(h) * 3600 + int(m) * 60 + float(rest)


# ---------------------------------------------------------------------------
# Task 4: homography_between / format_ass_time
# ---------------------------------------------------------------------------

class TestHomographyBetween:
    def test_roundtrip_identity(self):
        homogs = [
            np.eye(3),
            translation(10.0, 5.0),
            rotation_about(500.0, 300.0, 7.0),
            translation(-30.0, 12.0),
            translation(40.0, 0.0) @ rotation_about(200.0, 150.0, -4.0),
        ]
        tracks = make_tracks(homogs)
        eye = np.eye(3)
        for a in range(5):
            assert np.allclose(homography_between(tracks, a, a), eye, atol=1e-9)
            for b in range(5):
                h_ab = homography_between(tracks, a, b)
                h_ba = homography_between(tracks, b, a)
                assert np.allclose(h_ab @ h_ba, eye, atol=1e-9), (a, b)

    def test_chain_semantics(self):
        """H(a→b) 把「a 平面坐标」映到 b 帧画面:H_b·inv(H_a)·(H_a·p) = H_b·p。"""
        homogs = [np.eye(3), translation(30.0, 0.0), translation(60.0, 10.0)]
        tracks = make_tracks(homogs)
        p = np.array([[BOX_CENTER[0]], [BOX_CENTER[1]], [1.0]])
        h_0_2 = homography_between(tracks, 0, 2)
        h_0_1 = homography_between(tracks, 0, 1)
        h_1_2 = homography_between(tracks, 1, 2)
        via = h_1_2 @ (h_0_1 @ p)
        direct = h_0_2 @ p
        assert np.allclose(via, direct, atol=1e-9)

    def test_lost_or_missing_raises_keyerror(self):
        tracks = translation_tracks(6, lost={3})
        with pytest.raises(KeyError):
            homography_between(tracks, 3, 1)
        with pytest.raises(KeyError):
            homography_between(tracks, 1, 3)
        with pytest.raises(KeyError):
            homography_between(tracks, 99, 1)
        with pytest.raises(KeyError):
            homography_between(tracks, 1, 99)


class TestFormatAssTime:
    @pytest.mark.parametrize("sec,expected", [
        (0.0, "0:00:00.00"),
        (0.04, "0:00:00.04"),
        (59.99, "0:00:59.99"),
        (3599.99, "0:59:59.99"),
        (3661.5, "1:01:01.50"),
    ])
    def test_format(self, sec, expected):
        assert format_ass_time(sec) == expected

    def test_negative_clamped(self):
        assert format_ass_time(-1.0) == "0:00:00.00"


# ---------------------------------------------------------------------------
# Task 4: build_line_tracks
# ---------------------------------------------------------------------------

class TestBuildLineTracks:
    def test_translation_center_scale_angle(self):
        n = 20
        tracks = translation_tracks(n, dx=2.0)
        line_tracks = build_line_tracks([BOX, BOX2], ["行一", "行二"], tracks, ref_frame=0)
        assert len(line_tracks) == 2
        assert [lt.text for lt in line_tracks] == ["行一", "行二"]
        lt = line_tracks[0]
        assert lt.ref_box == BOX
        assert lt.height == pytest.approx(50.0)
        assert sorted(lt.poses) == list(range(n))
        for i in range(n):
            pose = lt.poses[i]
            assert pose.center[0] == pytest.approx(BOX_CENTER[0] + 2.0 * i, abs=0.5)
            assert pose.center[1] == pytest.approx(BOX_CENTER[1], abs=0.5)
            assert pose.scale == pytest.approx(1.0, abs=1e-6)
            assert pose.angle_deg == pytest.approx(0.0, abs=1e-6)
        # 第二行独立轨迹,顺序与输入一致
        lt2 = line_tracks[1]
        assert lt2.height == pytest.approx(40.0)
        for i in range(0, n, 5):
            assert lt2.poses[i].center[0] == pytest.approx(200.0 + 2.0 * i, abs=0.5)
            assert lt2.poses[i].center[1] == pytest.approx(220.0, abs=0.5)

    def test_rotation_angle_increasing(self):
        n = 30
        homogs = [rotation_about(BOX_CENTER[0], BOX_CENTER[1], 0.5 * i) for i in range(n)]
        tracks = make_tracks(homogs)
        (lt,) = build_line_tracks([BOX], ["rot"], tracks, ref_frame=0)
        angles = [lt.poses[i].angle_deg for i in range(n)]
        for a, b in zip(angles, angles[1:]):
            assert b > a
        assert angles[0] == pytest.approx(0.0, abs=1e-9)
        assert angles[-1] == pytest.approx(14.5, abs=1e-6)
        # 旋转不改变中心与长边
        for i in range(n):
            assert lt.poses[i].center[0] == pytest.approx(BOX_CENTER[0], abs=0.5)
            assert lt.poses[i].center[1] == pytest.approx(BOX_CENTER[1], abs=0.5)
            assert lt.poses[i].scale == pytest.approx(1.0, abs=1e-6)

    def test_lost_frames_have_no_pose(self):
        tracks = translation_tracks(10, lost={3, 4, 7})
        (lt,) = build_line_tracks([BOX], ["x"], tracks, ref_frame=0)
        assert set(lt.poses) == {0, 1, 2, 5, 6, 8, 9}

    def test_lost_ref_frame_reanchored_to_nearest_ok(self):
        # ref_frame=2 lost → 重锚定到最近 ok 帧(并列取更早:帧 1)
        tracks = translation_tracks(10, dx=2.0, lost={2})
        (lt,) = build_line_tracks([BOX], ["x"], tracks, ref_frame=2)
        assert set(lt.poses) == {0, 1, 3, 4, 5, 6, 7, 8, 9}
        for f in lt.poses:
            expected_x = BOX_CENTER[0] + 2.0 * (f - 1)  # H(1→f) = 平移 2*(f-1)
            assert lt.poses[f].center[0] == pytest.approx(expected_x, abs=0.5)

    def test_line_track_dataclass_shape(self):
        tracks = translation_tracks(3)
        (lt,) = build_line_tracks([BOX], ["t"], tracks, ref_frame=0)
        assert isinstance(lt, LineTrack)
        assert isinstance(lt.poses[0], LinePose)
        assert isinstance(lt.poses[0].center, tuple) and len(lt.poses[0].center) == 2


# ---------------------------------------------------------------------------
# Task 4: smooth_line_track
# ---------------------------------------------------------------------------

class TestSmoothLineTrack:
    def test_linear_trend_preserved(self):
        n = 40
        tracks = make_tracks([translation(10.0 * i, 2.0 * i) for i in range(n)])
        (lt,) = build_line_tracks([BOX], ["x"], tracks, ref_frame=0)
        raw = {f: lt.poses[f].center for f in lt.poses}
        smooth_line_track(lt, window=5)
        frames = sorted(lt.poses)
        r = 2  # window//2,仅取窗口完整覆盖的内部帧
        interior = frames[r:n - r]
        xs = np.array([lt.poses[f].center[0] for f in interior])
        ys = np.array([lt.poses[f].center[1] for f in interior])
        idx = np.array(interior, dtype=float)
        slope_x = float(np.polyfit(idx, xs, 1)[0])
        slope_y = float(np.polyfit(idx, ys, 1)[0])
        assert slope_x == pytest.approx(10.0, rel=0.01)  # 斜率偏差 <1%
        assert slope_y == pytest.approx(2.0, rel=0.01)
        # 线性序列的对称窗口平均在内部帧精确还原原值
        for f in interior:
            assert lt.poses[f].center == pytest.approx(raw[f], abs=1e-6)
        # scale/angle 常量序列平滑后不变
        assert all(lt.poses[f].scale == pytest.approx(1.0, abs=1e-9) for f in frames)
        assert all(lt.poses[f].angle_deg == pytest.approx(0.0, abs=1e-9) for f in frames)
        # 帧集合不变(原地替换,不增不减)
        assert sorted(lt.poses) == list(range(n))

    def test_jitter_attenuated(self):
        n = 40
        homogs = []
        for i in range(n):
            jitter = 10.0 if i % 2 == 0 else -10.0
            homogs.append(translation(10.0 * i, 2.0 * i + jitter))
        tracks = make_tracks(homogs)
        (lt,) = build_line_tracks([BOX], ["x"], tracks, ref_frame=0)
        # 注意 H(0→t) 以帧 0 为基准,帧 0 自身的抖动计入基准偏移;
        # 用对原始 y 的线性拟合吸收常量偏移后,残余即纯高频抖动。
        raw = {f: lt.poses[f].center[1] for f in lt.poses}
        frames = sorted(raw)
        k_lin, b_lin = np.polyfit(np.array(frames, dtype=float),
                                  np.array([raw[f] for f in frames]), 1)
        raw_dev = max(abs(raw[f] - (k_lin * f + b_lin)) for f in frames)
        smooth_line_track(lt, window=5)
        interior = list(range(2, n - 2))
        sm_dev = max(
            abs(lt.poses[f].center[1] - (k_lin * f + b_lin)) for f in interior)
        assert raw_dev >= 8.0   # 原始高频抖动显著
        assert sm_dev <= 4.0    # 平滑后残余 ≤ 原始抖动的一半以下
        slope = float(np.polyfit(
            np.array(interior, dtype=float),
            [lt.poses[f].center[0] for f in interior], 1)[0])
        assert slope == pytest.approx(10.0, rel=0.01)

    def test_window_too_small_noop(self):
        tracks = translation_tracks(5, dx=3.0)
        (lt,) = build_line_tracks([BOX], ["x"], tracks, ref_frame=0)
        before = {f: lt.poses[f].center for f in lt.poses}
        smooth_line_track(lt, window=1)
        assert {f: lt.poses[f].center for f in lt.poses} == before

    def test_lost_gap_skipped(self):
        # 窗口(帧 5 → 半径 2)只数有 pose 的帧:lost 缺失帧跳过,
        # 间隔两侧的好帧仍落入同一窗口(帧 5 的窗口 = 帧 {3,5,6,7})
        tracks = translation_tracks(8, dx=1.0, lost={4})
        (lt,) = build_line_tracks([BOX], ["x"], tracks, ref_frame=0)
        smooth_line_track(lt, window=5)
        assert sorted(lt.poses) == [0, 1, 2, 3, 5, 6, 7]
        # 帧 5 平滑值 = mean(x@{3,5,6,7}) = mean(153,155,156,157) = 155.25
        assert lt.poses[5].center[0] == pytest.approx(BOX_CENTER[0] + 5.25, abs=1e-6)


# ---------------------------------------------------------------------------
# Task 5: simplify_and_segment(先写失败测试,实现见 Task 5 轮次)
# ---------------------------------------------------------------------------

class TestSimplifyAndSegment:
    def test_l_shape_two_segments(self):
        n = 60
        centers = []
        for i in range(n):
            if i < 30:
                centers.append((100.0 + 4.0 * i, 200.0))
            else:
                centers.append((100.0 + 4.0 * 29 + 2.0 * (i - 29), 200.0 + 4.0 * (i - 29)))
        poses = {i: LinePose(center=c, angle_deg=0.0, scale=1.0) for i, c in enumerate(centers)}
        track = LineTrack(text="l", ref_box=BOX, height=50.0, poses=poses)
        cfg = MotionAssConfig()
        segs = simplify_and_segment(track, cfg)
        assert len(segs) >= 2
        # 区间连续无缝:下一段起点 == 上一段终点
        for (a0, b0), (a1, b1) in zip(segs, segs[1:]):
            assert b0 == a1
        assert segs[0][0] == 0 and segs[-1][1] == n - 1
        for a, b in segs:
            assert b - a + 1 >= cfg.min_seg_frames

    def test_min_seg_enforced_on_spike(self):
        # 直线中第 20 帧垂直突跳 40px → DP 产生短段,须并入邻段
        n = 40
        poses = {}
        for i in range(n):
            y = 100.0 + (40.0 if i == 20 else 0.0)
            poses[i] = LinePose(center=(50.0 + 3.0 * i, y), angle_deg=0.0, scale=1.0)
        track = LineTrack(text="s", ref_box=BOX, height=50.0, poses=poses)
        segs = simplify_and_segment(track, MotionAssConfig())
        assert segs[0][0] == 0 and segs[-1][1] == n - 1
        for (a0, b0), (a1, b1) in zip(segs, segs[1:]):
            assert b0 == a1
        if len(segs) > 1:
            for a, b in segs:
                assert b - a + 1 >= MotionAssConfig().min_seg_frames

    def test_empty_track(self):
        track = LineTrack(text="e", ref_box=BOX, height=50.0, poses={})
        assert simplify_and_segment(track, MotionAssConfig()) == []


# ---------------------------------------------------------------------------
# Task 5: synthesize_events 标签阶梯
# ---------------------------------------------------------------------------

def move_points(tags: str):
    import re
    m = re.search(
        r"\\move\((-?[\d.]+),(-?[\d.]+),(-?[\d.]+),(-?[\d.]+)", tags)
    assert m, tags
    return tuple(float(g) for g in m.groups())


class TestSynthesizeSingleStraight:
    def test_single_move_event(self):
        n = 25
        tracks = translation_tracks(n, dx=2.0)
        line_tracks = build_line_tracks([BOX], ["直线"], tracks, ref_frame=0)
        events = synthesize_events(line_tracks, tracks, MotionAssConfig(), style="Scene")
        assert len(events) == 1
        ev = events[0]
        assert ev["name"] == "motion"
        assert ev["style"] == "Scene"
        assert ev["body"] == "直线"
        assert ev["start_time"] == format_ass_time(0.0)
        assert ev["end_time"] == format_ass_time((n - 1) / FPS)
        tags = ev["tags"]
        assert tags.startswith("{\\an5\\fs50\\move(")
        assert "\\pos(" not in tags
        x1, y1, x2, y2 = move_points(tags)
        assert (x1, y1) == pytest.approx((150.0, 125.0), abs=0.05)
        assert (x2, y2) == pytest.approx((150.0 + 2.0 * (n - 1), 125.0), abs=0.05)
        t_ms = int(round((n - 1) / FPS * 1000))
        assert f",0,{t_ms})" in tags


class TestSynthesizeArcMultiSegment:
    def test_arc_two_or_more_segments(self):
        # 半径 1500、跨 20° 的圆弧,60 帧:DP 切成若干段但不爆炸
        n = 60
        radius = 1500.0
        cx, cy = 960.0, 3000.0
        homogs = []
        for i in range(n):
            phi = math.radians(80.0 + 20.0 * i / (n - 1))
            px, py = cx + radius * math.cos(phi), cy - radius * math.sin(phi)
            theta = math.degrees(phi) - 90.0  # 沿切线方向的行角度
            homogs.append(
                translation(px - BOX_CENTER[0], py - BOX_CENTER[1])
                @ rotation_about(BOX_CENTER[0], BOX_CENTER[1], theta))
        tracks = make_tracks(homogs)
        line_tracks = build_line_tracks([BOX], ["arc"], tracks, ref_frame=0)
        events = synthesize_events(line_tracks, tracks, MotionAssConfig())
        assert 2 <= len(events) <= 20
        # 相邻边界时间字符串相等;前段 \move 终点 = 后段起点(±0.5px)
        for ev0, ev1 in zip(events, events[1:]):
            assert ev0["end_time"] == ev1["start_time"]
            x1, y1, x2, y2 = move_points(ev0["tags"])
            nx1, ny1, _, _ = move_points(ev1["tags"])
            assert (x2, y2) == pytest.approx((nx1, ny1), abs=0.5)
        for ev in events:
            assert "\\move(" in ev["tags"]
            assert "\\pos(" not in ev["tags"]


class TestSynthesizeRotationTTag:
    def test_linear_rotation_adds_frz_and_t(self):
        n = 40
        homogs = [rotation_about(BOX_CENTER[0], BOX_CENTER[1], 15.0 * i / (n - 1))
                  for i in range(n)]
        tracks = make_tracks(homogs)
        line_tracks = build_line_tracks([BOX], ["rot"], tracks, ref_frame=0)
        events = synthesize_events(line_tracks, tracks, MotionAssConfig())
        assert len(events) == 1  # 中心不动 → 单段;旋转用 \t 叠加
        tags = events[0]["tags"]
        assert "\\frz" in tags
        assert "\\t(" in tags
        assert "\\move(" in tags
        # 基值 + 目标值形式:\frz(0) … \t(0,T,\frz(15))
        assert "\\frz0.00" in tags
        t_ms = int(round((n - 1) / FPS * 1000))
        assert f"\\t(0,{t_ms},\\frz15.00)" in tags


class TestSynthesizeScaleFscxTag:
    def test_linear_zoom_adds_fscx_and_t(self):
        n = 40

        def zoom_about(cx: float, cy: float, s: float) -> np.ndarray:
            diag = np.diag([s, s, 1.0])
            return translation(cx, cy) @ diag @ translation(-cx, -cy)

        homogs = [zoom_about(BOX_CENTER[0], BOX_CENTER[1], 1.0 + 0.5 * i / (n - 1))
                  for i in range(n)]
        tracks = make_tracks(homogs)
        line_tracks = build_line_tracks([BOX], ["zoom"], tracks, ref_frame=0)
        # 中心不动 → 单段;缩放 1.0→1.5(逐帧增量 0.013 < 阈值,不切分)
        events = synthesize_events(line_tracks, tracks, MotionAssConfig())
        assert len(events) == 1
        tags = events[0]["tags"]
        assert "\\frz" not in tags
        # 基值 \fscx/\fscy 百分比 + \t 到目标百分比
        assert "\\fscx100.0\\fscy100.0" in tags
        t_ms = int(round((n - 1) / FPS * 1000))
        assert f"\\t(0,{t_ms},\\fscx150.0\\fscy150.0)" in tags
        # 平滑前 pose 的 scale 逐帧正确
        assert line_tracks[0].poses[0].scale == pytest.approx(1.0, abs=1e-9)
        assert line_tracks[0].poses[n - 1].scale == pytest.approx(1.5, abs=1e-9)


class TestSynthesizeDenseFallback:
    def test_jitter_triggers_pos_fallback(self):
        n = 24
        homogs = []
        for i in range(n):
            jitter = 25.0 if i % 2 == 0 else -25.0
            # x 匀速前进 + y 高频抖动 → 之字形轨迹(非共线,DP 必然切碎)
            homogs.append(translation(320.0 - BOX_CENTER[0] + 5.0 * i,
                                      240.0 + jitter - BOX_CENTER[1]))
        tracks = make_tracks(homogs)
        line_tracks = build_line_tracks([BOX], ["jit"], tracks, ref_frame=0)
        cfg = MotionAssConfig(dense_stride=2)
        events = synthesize_events(line_tracks, tracks, cfg)
        assert len(events) == n // cfg.dense_stride
        for ev in events:
            assert "\\pos(" in ev["tags"]
            assert "\\move(" not in ev["tags"]
            assert ev["tags"].startswith("{\\an5\\fs50\\pos(")
        # 事件时间互相衔接:每条覆盖 [t_k, t_{k+stride}]
        for ev0, ev1 in zip(events, events[1:]):
            assert ev0["end_time"] == ev1["start_time"]
        assert events[0]["start_time"] == format_ass_time(0.0)
        assert events[0]["end_time"] == format_ass_time(2 / FPS)
        # 坐标取好帧中心(1 位小数):x 相对帧 0 前进 5px/帧;
        # stride=2 只采样偶数帧 → y 恒为 125
        assert "\\pos(150.0,125.0)" in events[0]["tags"]
        assert "\\pos(160.0,125.0)" in events[1]["tags"]


class TestSynthesizeLostSplit:
    def test_mid_lost_splits_into_two_chains(self):
        n = 26
        lost = set(range(10, 16))
        tracks = translation_tracks(n, dx=3.0, lost=lost)
        line_tracks = build_line_tracks([BOX], ["gap"], tracks, ref_frame=0)
        events = synthesize_events(line_tracks, tracks, MotionAssConfig())
        assert len(events) == 2
        end1 = parse_ass_time(events[0]["end_time"])
        start2 = parse_ass_time(events[1]["start_time"])
        assert end1 < start2  # 不重叠、不外推
        assert events[0]["end_time"] == format_ass_time(9 / FPS)
        assert events[1]["start_time"] == format_ass_time(16 / FPS)
        for ev in events:
            assert "\\move(" in ev["tags"]

    def test_short_lost_held_when_configured(self):
        n = 26
        lost = set(range(10, 16))
        tracks = translation_tracks(n, dx=3.0, lost=lost)
        line_tracks = build_line_tracks([BOX], ["gap"], tracks, ref_frame=0)
        cfg = MotionAssConfig(lost_hold_sec=0.5)  # 间隔 0.28s ≤ 0.5 → 保持
        events = synthesize_events(line_tracks, tracks, cfg)
        assert len(events) == 1
        assert events[0]["start_time"] == format_ass_time(0.0)
        assert events[0]["end_time"] == format_ass_time((n - 1) / FPS)


class TestSynthesizeMisc:
    def test_no_poses_no_events(self):
        track = LineTrack(text="x", ref_box=BOX, height=50.0, poses={})
        tracks = translation_tracks(4)
        assert synthesize_events([track], tracks, MotionAssConfig()) == []

    def test_all_lost_ref_raises(self):
        tracks = translation_tracks(4, lost={0, 1, 2, 3})
        with pytest.raises(KeyError):
            build_line_tracks([BOX], ["x"], tracks, ref_frame=0)

    def test_events_ordered_within_line(self):
        n = 60
        radius = 1500.0
        cx, cy = 960.0, 3000.0
        homogs = []
        for i in range(n):
            phi = math.radians(80.0 + 20.0 * i / (n - 1))
            px, py = cx + radius * math.cos(phi), cy - radius * math.sin(phi)
            theta = math.degrees(phi) - 90.0
            homogs.append(
                translation(px - BOX_CENTER[0], py - BOX_CENTER[1])
                @ rotation_about(BOX_CENTER[0], BOX_CENTER[1], theta))
        tracks = make_tracks(homogs)
        line_tracks = build_line_tracks([BOX, BOX2], ["a", "b"], tracks, ref_frame=0)
        events = synthesize_events(line_tracks, tracks, MotionAssConfig())
        bodies = [ev["body"] for ev in events]
        assert bodies == sorted(bodies, key=lambda b: 0 if b == "a" else 1)
        # 行内按时间递增
        for body in ("a", "b"):
            times = [parse_ass_time(ev["start_time"]) for ev in events if ev["body"] == body]
            assert times == sorted(times)


# ---------------------------------------------------------------------------
# 增量特性:屏幕亮度自适应(simplify_luma_curve / brightness_tag_chain)
# ---------------------------------------------------------------------------

# 仿真实场景(基线 247 = 90 分位)简化后的亮度关键帧折线:
# 恒亮 247 → 7.34s 174 → 7.67~8.34s 暗 30 → 9.01s 174 → 9.67s 回亮 247,
# 比值 = 中位亮度 / 247。
REAL_CURVE = [
    (6.0, 1.0),
    (7.34, 174.0 / 247.0),
    (7.67, 30.0 / 247.0),
    (8.34, 30.0 / 247.0),
    (9.01, 174.0 / 247.0),
    (9.67, 1.0),
]


class TestSimplifyLumaCurve:
    def test_collinear_ramp_collapses_to_endpoints(self):
        # 线性渐变(共线)→ 只留首末点
        curve = [(i * 0.5, 1.0 - (1.0 - 30.0 / 247.0) * i / 10.0) for i in range(11)]
        out = simplify_luma_curve(curve, tol_luma=8.0, baseline_luma=247.0)
        assert out == [curve[0], curve[-1]]

    def test_corners_kept_and_flat_runs_collapsed(self):
        # 两个拐点(2.0s 变暗起点、2.5s 谷底)必须保留;平坦段与容差内的
        # 过渡抖动(2.25s,偏差 ≈0.011 < 8/247≈0.032)被舍弃。
        curve = [
            (0.0, 1.0), (1.0, 1.0), (2.0, 1.0),
            (2.25, 0.55),
            (2.5, 30.0 / 247.0),
            (3.0, 30.0 / 247.0), (4.0, 30.0 / 247.0),
        ]
        out = simplify_luma_curve(curve, tol_luma=8.0, baseline_luma=247.0)
        assert out == [curve[0], curve[2], curve[4], curve[6]]

    def test_tolerance_in_luma_levels(self):
        # 容差按亮度级换算:tol_ratio = tol_luma / baseline_luma。
        baseline = 250.0
        keep = [(0.0, 1.0), (1.0, 1.0 - 10.0 / baseline), (2.0, 1.0)]  # 偏差 10 级 > 8
        drop = [(0.0, 1.0), (1.0, 1.0 - 5.0 / baseline), (2.0, 1.0)]   # 偏差 5 级 < 8
        assert simplify_luma_curve(
            keep, tol_luma=8.0, baseline_luma=baseline) == keep
        assert simplify_luma_curve(
            drop, tol_luma=8.0, baseline_luma=baseline) == [drop[0], drop[2]]

    def test_short_curves_passthrough(self):
        assert simplify_luma_curve([], 8.0) == []
        assert simplify_luma_curve([(1.0, 0.5)], 8.0) == [(1.0, 0.5)]
        two = [(0.0, 1.0), (1.0, 0.2)]
        assert simplify_luma_curve(two, 8.0, 247.0) == two


class TestBrightnessTagChain:
    def test_faithful_following_dark(self):
        # 忠实跟随自检:r=0.12 → g=round(30.6)=31=0x1F、a=round(224.4)=224=0xE0
        assert brightness_tag_chain([(0.0, 0.12)], 0.0, 10.0) == \
            "\\1c&H1F1F1F&\\alpha&HE0&"

    def test_faithful_following_bright_base(self):
        # r=1.0 基值 → \1c&HFFFFFF& + \alpha&H00&,随后线性变暗到 r=0.5
        # (g=round(127.5)=128=0x80、a=round(127.5)=128=0x80)
        assert brightness_tag_chain([(0.0, 1.0), (2.0, 0.5)], 0.0, 4.0) == (
            "\\1c&HFFFFFF&\\alpha&H00&"
            "\\t(0,2000,\\1c&H808080&\\alpha&H80&)")

    def test_flat_or_empty_curve_returns_empty(self):
        # 曲线恒为 1.0(或无曲线)→ 不加任何标签
        assert brightness_tag_chain(
            [(0.0, 1.0), (5.0, 1.0), (9.0, 1.0)], 0.0, 9.0) == ""
        assert brightness_tag_chain([], 0.0, 5.0) == ""

    def test_variation_outside_span_returns_empty(self):
        # 变化段完全在事件跨度之外、起点插值仍为 1.0 → 不加标签
        curve = [(0.0, 1.0), (1.0, 0.3), (2.0, 1.0), (9.0, 1.0)]
        assert brightness_tag_chain(curve, 4.0, 8.0) == ""

    def test_real_curve_chain_and_touching_endpoints(self):
        chain = brightness_tag_chain(REAL_CURVE, 6.54, 10.21)
        assert chain == (
            "\\1c&HE1E1E1&\\alpha&H1E&"
            "\\t(0,800,\\1c&HB4B4B4&\\alpha&H4B&)"
            "\\t(800,1130,\\1c&H1F1F1F&\\alpha&HE0&)"
            "\\t(1130,1800,\\1c&H1F1F1F&\\alpha&HE0&)"
            "\\t(1800,2470,\\1c&HB4B4B4&\\alpha&H4B&)"
            "\\t(2470,3130,\\1c&HFFFFFF&\\alpha&H00&)")
        # 相邻 \t 的毫秒区间端点精确相接
        import re
        spans = [(int(a), int(b))
                 for a, b in re.findall(r"\\t\((\d+),(\d+),", chain)]
        assert spans == [(0, 800), (800, 1130), (1130, 1800),
                         (1800, 2470), (2470, 3130)]

    def test_event_span_clipped_to_middle(self):
        # 事件只覆盖曲线中段 [3,5]:首段 [2,4] 裁到 [3,4](ms 0~1000),
        # 末段 [4,6] 裁到 [4,5](ms 1000~2000),段目标值取段终点插值
        # (t=5 → r=0.56 → g=143=0x8F、a=112=0x70)。
        curve = [(0.0, 1.0), (2.0, 0.12), (4.0, 0.12), (6.0, 1.0)]
        chain = brightness_tag_chain(curve, 3.0, 5.0)
        assert chain == (
            "\\1c&H1F1F1F&\\alpha&HE0&"
            "\\t(0,1000,\\1c&H1F1F1F&\\alpha&HE0&)"
            "\\t(1000,2000,\\1c&H8F8F8F&\\alpha&H70&)")

    def test_sub_millisecond_intersection_dropped(self):
        # 事件起点距拐点 0.2ms:区间 [0,1] 与跨度的交集 <1ms → 丢弃
        curve = [(0.0, 1.0), (1.0, 0.5), (2.0, 1.0)]
        chain = brightness_tag_chain(curve, 0.9998, 1.5)
        assert chain == (
            "\\1c&H808080&\\alpha&H7F&"
            "\\t(0,500,\\1c&HBFBFBF&\\alpha&H40&)")

    def test_use_color_use_alpha_switches(self):
        curve = [(0.0, 1.0), (1.0, 0.12)]
        assert brightness_tag_chain(curve, 0.0, 1.0, use_color=False) == (
            "\\alpha&H00&\\t(0,1000,\\alpha&HE0&)")
        assert brightness_tag_chain(curve, 0.0, 1.0, use_alpha=False) == (
            "\\1c&HFFFFFF&\\t(0,1000,\\1c&H1F1F1F&)")
        assert brightness_tag_chain(
            curve, 0.0, 1.0, use_color=False, use_alpha=False) == ""
