# tests/test_motion_ass_collapse.py
"""静止链塌缩(synthesize_events 阶梯 0)与 settle 阈值分辨率归一的单测。

覆盖(core/motion_ass.py ``_seg_static`` 闭包 + 连续静止段分组合并):
- 1080p 基线:微动链(总位移 < 12px)塌缩为单条 ``\\pos`` 事件(旧行为);
- video_height 归一:有效位移阈值 = collapse_settle_tol_px ×
  (video_height/1080)。同一几何轨迹在 2160p 下阈值 ×2、540p 下 ×0.5,
  塌缩判定与 1080p 的同物理幅度一致(12px 是绝对像素,对分辨率敏感);
- 旋转/缩放变化速率门控(collapse_max_rot_rate / collapse_max_scale_rate)
  是相对量,不随分辨率缩放:速率超限的段在任何分辨率下都不塌缩;
- 实际运动段(超阈值位移)保持 ``\\move`` 输出;
- 硬约束回归:video_height=1080 与缺省(None)的事件列表逐字节一致。

fixture 构造沿用 tests/test_pose_verify.py 的模式:合成逐帧 ok 的
TrackedQuad 列表(单应 = 平移 / 绕行中心旋转 / 绕行中心缩放)经
build_line_tracks 得到 LineTrack,再驱动 synthesize_events。行文本不带
行尾标点,锚点即行框中心,排除标点补偿对坐标的影响。
"""

from __future__ import annotations

import math
import os
import re
import sys

import numpy as np
import pytest

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from core.motion_ass import (  # noqa: E402
    MotionAssConfig,
    build_line_tracks,
    simplify_and_segment,
    synthesize_events,
)
from core.scene_plane_tracker import TrackedQuad  # noqa: E402

FPS = 25.0
BOX = (100.0, 100.0, 200.0, 150.0)   # 行框 w=100、h=50,中心 (150,125)
BOX_CENTER = (150.0, 125.0)
SETTLE_1080 = MotionAssConfig().collapse_settle_tol_px  # 12.0(1080p 基准)


# ---------------------------------------------------------------------------
# 合成轨迹构造(沿用 test_pose_verify.py / test_motion_ass.py 的模式)
# ---------------------------------------------------------------------------

def translation_h(dx: float = 0.0, dy: float = 0.0) -> np.ndarray:
    return np.array([[1.0, 0.0, dx],
                     [0.0, 1.0, dy],
                     [0.0, 0.0, 1.0]], dtype=np.float64)


def rotation_about(cx: float, cy: float, deg: float) -> np.ndarray:
    t = math.radians(deg)
    r = np.array([[math.cos(t), -math.sin(t), 0.0],
                  [math.sin(t), math.cos(t), 0.0],
                  [0.0, 0.0, 1.0]])
    return translation_h(cx, cy) @ r @ translation_h(-cx, -cy)


def zoom_about(cx: float, cy: float, s: float) -> np.ndarray:
    return translation_h(cx, cy) @ np.diag([s, s, 1.0]) @ translation_h(-cx, -cy)


def make_tracks(homogs, fps: float = FPS, times=None) -> list:
    """由逐帧 H(init→frame) 构造全 ok 的 TrackedQuad 列表。

    ``times`` 显式给出逐帧 time_sec(缺省 i/fps);组级速率用例需要
    「同刻多帧」(跨度时长为 0)这类时间轴,故两处可独立指定。
    """
    base = np.array([[100.0, 100.0], [200.0, 100.0],
                     [200.0, 150.0], [100.0, 150.0]])
    out = []
    for i, h_mat in enumerate(homogs):
        h_arr = np.asarray(h_mat, dtype=np.float64)
        hom = np.hstack([base, np.ones((4, 1))])
        proj = hom @ h_arr.T
        quad = (proj[:, :2] / proj[:, 2:3]).tolist()
        t_sec = i / fps if times is None else float(times[i])
        out.append(TrackedQuad(
            frame_num=i, time_sec=t_sec, status="ok",
            quad=quad,
            homography=[[float(v) for v in row] for row in h_arr],
            homography_inv=[[float(v) for v in row]
                            for row in np.linalg.inv(h_arr)]))
    return out


def translation_homogs(n: int, dx: float) -> list:
    """n 帧直线平移链:总位移 = dx × (n-1)。"""
    return [translation_h(dx * i, 0.0) for i in range(n)]


def rotation_homogs(n: int, total_deg: float) -> list:
    """n 帧绕行框中心旋转链:中心不动、总角度 total_deg。"""
    return [rotation_about(*BOX_CENTER, total_deg * i / (n - 1))
            for i in range(n)]


def zoom_homogs(n: int, total_scale: float) -> list:
    """n 帧绕行框中心缩放链:中心不动、scale 1.0 → 1.0 + total_scale。"""
    return [zoom_about(*BOX_CENTER, 1.0 + total_scale * i / (n - 1))
            for i in range(n)]


def line_track_from_homogs(homogs, text: str = "塌缩行", times=None):
    tracks = make_tracks(homogs, times=times)
    (lt,) = build_line_tracks([BOX], [text], tracks, ref_frame=0)
    return lt, tracks


def has_pos(tags: str) -> bool:
    return "\\pos(" in tags


def has_move(tags: str) -> bool:
    return "\\move(" in tags


def move_points(tags: str):
    m = re.search(
        r"\\move\((-?[\d.]+),(-?[\d.]+),(-?[\d.]+),(-?[\d.]+)", tags)
    assert m, tags
    return tuple(float(g) for g in m.groups())


def collapsed_pos(tags: str) -> bool:
    """是否「塌缩成单条 \\pos」形式(有 \\pos、无 \\move)。"""
    return has_pos(tags) and not has_move(tags)


def pos_point(tags: str):
    m = re.search(r"\\pos\((-?[\d.]+),(-?[\d.]+)\)", tags)
    assert m, tags
    return (float(m.group(1)), float(m.group(2)))


def ass_seconds(stamp: str) -> float:
    """ASS ``H:MM:SS.CC`` → 秒(与 core.motion_ass.format_ass_time 逆变换)。"""
    h, m, rest = stamp.split(":")
    return int(h) * 3600 + int(m) * 60 + float(rest)


def covered_frames(events, tracks, lt, event):
    """事件时段 [start, end) 内的链帧(排他边界:与下一组共享的边界帧归下组)。"""
    tmap = {t.frame_num: t for t in tracks}
    t0 = ass_seconds(event["start_time"])
    t1 = ass_seconds(event["end_time"])
    return [f for f in sorted(lt.poses)
            if t0 - 1e-9 <= tmap[f].time_sec < t1 - 1e-9]


def assert_pos_anchor_bounded(events, tracks, lt, settle: float = SETTLE_1080):
    """塌缩不变式:每条静止 \\pos 事件的锚点与该事件时段内**每一帧**的
    跟踪锚点距离 ≤ settle(行居中判定的锚点即行框中心,见 fixture 说明)。

    这正是组级复核要保证的不变式——旧实现按段级判据合并,同向多段串成
    一组后组内累计位移无上界,此断言即失败。"""
    pos_events = [e for e in events if collapsed_pos(e["tags"])]
    assert pos_events, "fixture must produce collapsed \\pos event(s)"
    for ev in pos_events:
        px, py = pos_point(ev["tags"])
        frames = covered_frames(events, tracks, lt, ev)
        assert frames, f"no tracked frame inside event span: {ev}"
        dev = max(math.hypot(lt.poses[f].center[0] - px,
                             lt.poses[f].center[1] - py) for f in frames)
        assert dev <= settle + 1e-6, (
            f"\\pos anchor {px, py} deviates {dev:.2f}px from a tracked frame "
            f"inside its own span (settle={settle}): {ev}")


# ---------------------------------------------------------------------------
# 1080p 基线(等价旧行为)
# ---------------------------------------------------------------------------

class TestCollapse1080Baseline:
    def test_static_micro_motion_collapses_to_single_pos(self):
        """总位移 6px(<12)的直线微动链 → 单条 \\pos(无 \\move/\\t)。"""
        n = 13  # 0.5px/帧 × 12 = 6px
        lt, tracks = line_track_from_homogs(translation_homogs(n, 0.5))
        events = synthesize_events([lt], tracks, MotionAssConfig(),
                                   video_height=1080)
        assert len(events) == 1
        tags = events[0]["tags"]
        assert has_pos(tags) and not has_move(tags)
        assert "\\t(" not in tags
        assert "\\pos(150.0,125.0)" in tags  # 锚点 = 帧 0 行框中心

    def test_move_tol_floor_inert_with_defaults(self):
        """默认配置下 move_tol_px 地板项(2px)不参与:阈值即 12px——
        位移 11px 塌缩、13px 不塌缩。"""
        lt, tracks = line_track_from_homogs(translation_homogs(13, 1.0))
        assert has_pos(synthesize_events(
            [lt], tracks, MotionAssConfig(), video_height=1080)[0]["tags"])
        lt2, tracks2 = line_track_from_homogs(translation_homogs(13, 13.0 / 12))
        assert has_move(synthesize_events(
            [lt2], tracks2, MotionAssConfig(), video_height=1080)[0]["tags"])


# ---------------------------------------------------------------------------
# settle 阈值按 video_height 归一(1080p 基准等比缩放)
# ---------------------------------------------------------------------------

class TestCollapseResolutionScaling:
    def test_2160p_threshold_doubles(self):
        """总位移 18px(介于 12 与 24)的同几何轨迹:1080p 判运动(\\move),
        2160p 阈值 ×2=24px 判静止(\\pos)——与 1080p 下 9px 微动同判定。"""
        n = 13  # 1.5px/帧 × 12 = 18px
        lt, tracks = line_track_from_homogs(translation_homogs(n, 1.5))
        ev1080 = synthesize_events([lt], tracks, MotionAssConfig(),
                                   video_height=1080)
        ev2160 = synthesize_events([lt], tracks, MotionAssConfig(),
                                   video_height=2160)
        assert len(ev1080) == 1
        assert has_move(ev1080[0]["tags"]) and not has_pos(ev1080[0]["tags"])
        assert len(ev2160) == 1
        assert has_pos(ev2160[0]["tags"]) and not has_move(ev2160[0]["tags"])

    def test_540p_threshold_halves(self):
        """总位移 8px:1080p(阈值 12)塌缩为 \\pos;540p 阈值 ×0.5=6px,
        8px 超阈 → 保持 \\move(低分辨率下小位移不再被吸死)。"""
        n = 5  # 2px/帧 × 4 = 8px
        lt, tracks = line_track_from_homogs(translation_homogs(n, 2.0))
        ev1080 = synthesize_events([lt], tracks, MotionAssConfig(),
                                   video_height=1080)
        ev540 = synthesize_events([lt], tracks, MotionAssConfig(),
                                  video_height=540)
        assert len(ev1080) == 1
        assert has_pos(ev1080[0]["tags"]) and not has_move(ev1080[0]["tags"])
        assert len(ev540) == 1
        assert has_move(ev540[0]["tags"]) and not has_pos(ev540[0]["tags"])
        # 540p 运动端点仍落在真值上(150 → 158)
        assert move_points(ev540[0]["tags"])[:2] == pytest.approx((150.0, 125.0))
        assert move_points(ev540[0]["tags"])[2:] == pytest.approx((158.0, 125.0))

    def test_threshold_scales_continuously(self):
        """非整数倍分辨率同样按比例:720p 阈值 = 12 × (720/1080) = 8px,
        位移 7px 塌缩、9px 不塌缩。"""
        lt, tracks = line_track_from_homogs(translation_homogs(8, 1.0))  # 7px
        assert has_pos(synthesize_events(
            [lt], tracks, MotionAssConfig(), video_height=720)[0]["tags"])
        lt2, tracks2 = line_track_from_homogs(translation_homogs(10, 1.0))  # 9px
        assert has_move(synthesize_events(
            [lt2], tracks2, MotionAssConfig(), video_height=720)[0]["tags"])


# ---------------------------------------------------------------------------
# 旋转/缩放速率门控:相对量,不随分辨率缩放
# ---------------------------------------------------------------------------

class TestCollapseRateGate:
    def test_fast_rotation_not_collapsed_at_any_height(self):
        """0 位移、15°/0.96s(≈15.6°/s > 3°/s)的旋转:1080p/2160p 都不
        塌缩(速率门控不受分辨率影响),输出 \\move + \\t(\\frz)。"""
        n = 25
        lt, tracks = line_track_from_homogs(rotation_homogs(n, 15.0))
        for h in (1080, 2160):
            events = synthesize_events([lt], tracks, MotionAssConfig(),
                                       video_height=h)
            assert len(events) == 1
            tags = events[0]["tags"]
            assert has_move(tags) and not has_pos(tags)
            assert "\\t(" in tags and "\\frz" in tags

    def test_slow_rotation_collapses_even_at_540p(self):
        """0 位移、2°/0.96s(≈2.08°/s < 3°/s)的慢旋转按静止处理;
        540p 位移阈值减半不影响(位移为 0),速率门控不变 → 仍塌缩。"""
        n = 25
        lt, tracks = line_track_from_homogs(rotation_homogs(n, 2.0))
        for h in (1080, 540):
            events = synthesize_events([lt], tracks, MotionAssConfig(),
                                       video_height=h)
            tags = events[0]["tags"]
            assert has_pos(tags) and not has_move(tags)
            assert "\\t(" not in tags  # 塌缩后假 \t 一并消失

    def test_fast_zoom_not_collapsed_at_any_height(self):
        """0 位移、缩放 1.0→1.15 / 0.96s(≈0.156/s > 0.1/s):1080p/2160p
        都不塌缩,输出 \\move + \\t(\\fscx)。"""
        n = 25
        lt, tracks = line_track_from_homogs(zoom_homogs(n, 0.15))
        for h in (1080, 2160):
            events = synthesize_events([lt], tracks, MotionAssConfig(),
                                       video_height=h)
            tags = events[0]["tags"]
            assert has_move(tags) and not has_pos(tags)
            assert "\\t(" in tags and "\\fscx" in tags

    def test_slow_zoom_collapses_even_at_540p(self):
        """0 位移、缩放 1.0→1.08 / 0.96s(≈0.083/s < 0.1/s)按静止处理;
        540p 下位移阈值减半(位移为 0 不受影响)仍塌缩为 \\pos。"""
        n = 25
        lt, tracks = line_track_from_homogs(zoom_homogs(n, 0.08))
        for h in (1080, 540):
            events = synthesize_events([lt], tracks, MotionAssConfig(),
                                       video_height=h)
            tags = events[0]["tags"]
            assert has_pos(tags) and not has_move(tags)


# ---------------------------------------------------------------------------
# 实际运动段保持 \move
# ---------------------------------------------------------------------------

class TestRealMotionKeepsMove:
    def test_large_motion_stays_move_at_all_heights(self):
        """总位移 48px 的真实运动段:1080p 与 2160p(阈值 24)都超阈,
        保持单条 \\move;非静止段的输出不依赖 video_height(逐字节一致)。"""
        n = 25  # 2px/帧 × 24 = 48px
        lt, tracks = line_track_from_homogs(translation_homogs(n, 2.0))
        ev1080 = synthesize_events([lt], tracks, MotionAssConfig(),
                                   video_height=1080)
        ev2160 = synthesize_events([lt], tracks, MotionAssConfig(),
                                   video_height=2160)
        for events in (ev1080, ev2160):
            assert len(events) == 1
            assert has_move(events[0]["tags"]) and not has_pos(events[0]["tags"])
        assert ev1080 == ev2160
        # 端点落在真值上:(150,125) → (198,125)
        assert move_points(ev1080[0]["tags"]) == pytest.approx(
            (150.0, 125.0, 198.0, 125.0))


# ---------------------------------------------------------------------------
# 硬约束回归:video_height=1080 与缺省(None)输出逐字节一致
# ---------------------------------------------------------------------------

_EQUIV_FIXTURES = {
    "static_micro_6px": translation_homogs(13, 0.5),
    "micro_18px": translation_homogs(13, 1.5),
    "large_motion_48px": translation_homogs(25, 2.0),
    "fast_rotation": rotation_homogs(25, 15.0),
    "slow_rotation": rotation_homogs(25, 2.0),
    "fast_zoom": zoom_homogs(25, 0.15),
    "slow_zoom": zoom_homogs(25, 0.08),
}


@pytest.mark.parametrize("name", sorted(_EQUIV_FIXTURES))
def test_video_height_1080_matches_default(name):
    """video_height=1080(含浮点)恰为基准、行为与缺省逐字节一致;
    非正高度按缺省处理(防御分支)。"""
    lt, tracks = line_track_from_homogs(_EQUIV_FIXTURES[name])
    base = synthesize_events([lt], tracks, MotionAssConfig())
    assert base, "fixture must produce events"
    for h in (1080, 1080.0, 0, None):
        assert synthesize_events([lt], tracks, MotionAssConfig(),
                                 video_height=h) == base


# ---------------------------------------------------------------------------
# 组级复核:段级判据只保证「单段首末位移 ≤ settle」,同向多段串成一组后
# 组内累计位移无上界 → 整组单条 \pos 会错位超过 settle(settle 本身)。
# 组级复核保证不变式:塌缩 \pos 锚点距组内任意帧的跟踪锚点 ≤ settle。
# ---------------------------------------------------------------------------

def l_shape_homogs(step: float = 10.0, n_leg: int = 41) -> list:
    """L 形轨迹:先右移 step px(n_leg 帧),再下移 step px(n_leg 帧)。

    DP 在拐角处分段 → 2 段,每段位移 step ≤ settle;段级判据各自合格,
    但组终点距组首锚点 √2·step > settle(同向累计无上界的最小复现)。
    """
    out = []
    for i in range(2 * n_leg - 1):
        t = min(i, n_leg - 1)
        u = max(0, i - (n_leg - 1))
        out.append(translation_h(step * t / (n_leg - 1),
                                 step * u / (n_leg - 1)))
    return out


def diamond_homogs(radius: float = 3.0, hold: int = 8, cycles: int = 2) -> list:
    """真·静止链:半径 radius 的 2D 菱形微抖(每角点保持 hold 帧)。

    位移沿两个轴向交替,DP 在每个角点分段 → 多段;但全程锚点都在参考
    锚点 radius·√2 内 → 组级复核应把全部段并成**单条** \\pos(不退化成
    多事件,也不退化成 \\move)。
    """
    corners = [(radius, 0.0), (0.0, -radius), (-radius, 0.0), (0.0, radius)]
    offs = []
    for _ in range(cycles):
        for cx, cy in corners:
            offs.extend([(cx, cy)] * hold)
    offs.append(corners[0])
    return [translation_h(dx, dy) for dx, dy in offs]


def zigzag4_homogs(step: float = 10.0, n_leg: int = 11) -> list:
    """4 段等步长折线:右、下、右、下,每段 step px(累计 4·step > settle)。"""
    hs = []
    x = y = 0.0
    for leg in range(4):
        for _ in range(n_leg if leg == 0 else n_leg - 1):
            hs.append(translation_h(x, y))
            if leg % 2 == 0:
                x += step / (n_leg - 1)
            else:
                y += step / (n_leg - 1)
    hs.append(translation_h(x, y))
    return hs


def rate_break_homogs(kind: str = "rot"):
    """组级速率越界:第二段的跨度时长为 0(全部帧同刻),段级速率门控
    在 ``dur ≤ 1e-6`` 时直接判静止、不评估速率——组级复核是唯一防线。

    时间轴:12 帧 0.25s 间隔(第一段,角度/缩放不变)+ 6 帧同刻(第二段,
    角 60°/缩放 1.5 跳变发生在第二段内部)。第一段段级合格;并入第二段
    后整组角速率 60°/2.75s、缩放速率 0.5/2.75s 均超限 → 另起新组。
    锚点全程距首帧 ≤ 9px(< settle),故切分只能来自速率门控而非位移。
    """
    times = [0.25 * i for i in range(12)] + [2.75] * 6
    offs = [(0.0, 0.0)] * 12 + [(4.0, -6.0)] * 5 + [(9.0, 0.0)]
    if kind == "rot":
        hs = [translation_h(dx, dy) @ rotation_about(*BOX_CENTER, deg)
              for (dx, dy), deg in zip(offs, [0.0] * 14 + [60.0] * 4)]
    elif kind == "zoom":
        hs = [translation_h(dx, dy) @ zoom_about(*BOX_CENTER, s)
              for (dx, dy), s in zip(offs, [1.0] * 14 + [1.5] * 4)]
    else:  # pragma: no cover - 参数化列表限定取值
        raise ValueError(kind)
    return hs, times


class TestStaticGroupDisplacementBound:
    def test_l_shape_does_not_collapse_into_single_pos(self):
        r"""L 形(右移 10px + 下移 10px,DP 正确切 2 段、每段 ≤ settle):
        不再出现「单条 \pos 覆盖全程」——真值终点距组首锚点 14.14px >
        settle,旧行为把整组塌缩成一条 \pos(错位超过 settle 本身)。"""
        homogs = l_shape_homogs()
        lt, tracks = line_track_from_homogs(homogs)
        events = synthesize_events([lt], tracks, MotionAssConfig(),
                                   video_height=1080)
        span = (events[0]["start_time"], events[-1]["end_time"])
        assert len(events) >= 2
        assert not any((e["start_time"], e["end_time"]) == span
                       and collapsed_pos(e["tags"]) for e in events), \
            "整段 3.24s 被单条 \\pos 覆盖:组内累计位移无上界"
        # 全程端点错位 √2×10 = 14.14px > settle → 关掉塌缩才是「真值」
        first_anchor = pos_point(events[0]["tags"])
        last = sorted(lt.poses)[-1]
        end = lt.poses[last].center
        assert math.hypot(end[0] - first_anchor[0],
                          end[1] - first_anchor[1]) > SETTLE_1080
        assert_pos_anchor_bounded(events, tracks, lt)

    def test_true_static_chain_still_collapses_to_single_pos(self):
        """真·静止链(2D 菱形微抖,8 段)仍塌缩成**单条** \\pos:
        组级复核只拆「组内越界」的组,不发散成多事件、也不退化成 \\move
        (否则大量微小抖动的静态文字会退化成海量 \\move 事件)。"""
        homogs = diamond_homogs()
        lt, tracks = line_track_from_homogs(homogs)
        events = synthesize_events([lt], tracks, MotionAssConfig(),
                                   video_height=1080)
        assert len(events) == 1, events
        tags = events[0]["tags"]
        assert collapsed_pos(tags) and "\\t(" not in tags
        assert tags.endswith("\\pos(150.0,125.0)}")  # 组参考锚点 = 帧 0 中心
        # 反证链本身是多段的:关掉塌缩后逐段 \\move(旧路径不塌缩的真值)
        raw = synthesize_events([lt], tracks,
                                MotionAssConfig(collapse_static_chains=False),
                                video_height=1080)
        assert len(raw) == len(simplify_and_segment(lt, MotionAssConfig())) > 1
        assert all(has_move(e["tags"]) for e in raw)
        assert_pos_anchor_bounded(events, tracks, lt)

    def test_equal_step_segments_split_at_group_boundary(self):
        """4 段等步长折线(每段 10px ≤ settle、累计 40px > settle):
        逐组按组参考锚点切分,每组一条 \\pos 且组内偏差 ≤ settle。"""
        homogs = zigzag4_homogs()
        lt, tracks = line_track_from_homogs(homogs)
        events = synthesize_events([lt], tracks, MotionAssConfig(),
                                   video_height=1080)
        assert len(events) == 4, events
        assert all(collapsed_pos(e["tags"]) for e in events)
        # 组参考锚点 = 各段首帧锚点(逐级推进)
        assert [pos_point(e["tags"]) for e in events] == [
            (150.0, 125.0), (161.0, 125.0), (161.0, 135.0), (171.0, 135.0)]
        assert_pos_anchor_bounded(events, tracks, lt)

    def test_segment_level_criterion_would_have_merged_everything(self):
        """切分确实来自组级复核:三段(每段 ≤ settle)的锚点全程距首帧
        ≤ settle 时仍是一条 \\pos;L 形(同向累计超限)才拆成两条。"""
        # 微阶梯:每段 ≤ 4px、全程距首帧 ≤ 4px → 组级合格,单条 \pos
        micro = [translation_h(4.0 * min(i, 12) / 12.0, 0.0) for i in range(25)]
        micro += [translation_h(4.0, -4.0 * (i - 12) / 12.0)
                  for i in range(13, 25)]
        lt, tracks = line_track_from_homogs(micro)
        events = synthesize_events([lt], tracks, MotionAssConfig(),
                                   video_height=1080)
        assert len(events) == 1 and collapsed_pos(events[0]["tags"])


class TestGroupRateGate:
    @pytest.mark.parametrize("kind", ["rot", "zoom"])
    def test_group_rate_overflow_starts_new_group(self, kind):
        """组级角/缩放变化速率按**整组跨度**重算:第二段跨度时长为 0 时
        段级速率门控不生效,并入后整组速率超限 → 另起一个新组(仍 \\pos,
        不退化成 \\move),而不是把整组塌缩成一条错位 \\pos。"""
        homogs, times = rate_break_homogs(kind)
        lt, tracks = line_track_from_homogs(homogs, times=times)
        events = synthesize_events([lt], tracks, MotionAssConfig(),
                                   video_height=1080)
        assert len(events) == 2, events
        assert all(collapsed_pos(e["tags"]) for e in events)
        assert [pos_point(e["tags"]) for e in events] == [
            (150.0, 125.0), (154.0, 119.0)]
        assert events[1]["end_time"] > events[1]["start_time"]  # 非零长
        # 切分只能来自速率门控:全程锚点距首帧 9px < settle(位移未越限)
        frames = sorted(lt.poses)
        c0 = lt.poses[frames[0]].center
        dev = max(math.hypot(lt.poses[f].center[0] - c0[0],
                             lt.poses[f].center[1] - c0[1]) for f in frames)
        assert dev <= SETTLE_1080
        assert_pos_anchor_bounded(events, tracks, lt)


class TestCollapseDisabledUnchanged:
    @pytest.mark.parametrize("name", ["l_shape", "diamond", "zigzag4"])
    def test_collapse_disabled_emits_one_move_per_segment(self, name):
        """``collapse_static_chains=False`` 行为不变:静止组不成立,逐段
        \\move(段数 = simplify_and_segment 的最终分段,端点 = 段首末锚点),
        与组级复核无关。"""
        homogs = {"l_shape": l_shape_homogs(),
                  "diamond": diamond_homogs(),
                  "zigzag4": zigzag4_homogs()}[name]
        lt, tracks = line_track_from_homogs(homogs)
        cfg = MotionAssConfig(collapse_static_chains=False)
        events = synthesize_events([lt], tracks, cfg, video_height=1080)
        segs = simplify_and_segment(lt, cfg)
        chain = sorted(lt.poses)
        assert len(events) == len(segs) > 1
        assert all(has_move(e["tags"]) and not collapsed_pos(e["tags"])
                   for e in events)
        for ev, (ai, bi) in zip(events, segs):
            a = lt.poses[chain[ai]].center
            b = lt.poses[chain[bi]].center
            assert move_points(ev["tags"]) == pytest.approx(
                (a[0], a[1], b[0], b[1]), abs=0.05)
