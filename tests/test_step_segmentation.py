# tests/test_step_segmentation.py
"""步进/静止体制分段(core.step_segmentation)的单测。

覆盖:
- 关闭开关:逐位恒等回退(tracks/orig_index_of 不变,无 holds);
- 静止行(K=1):单 hold、恒位姿轨迹,合成出单条 \\pos 事件;
- 步进行(K≥2):换位阈值串段、边界取采样帧中点(reader=None)、hold 段
  划分整条 ok 时间轴(换位后到 B 侧首采样之间不空洞);
- 可见性断口:采样横着 ok 帧断口时断口首帧即边界且视同已确认;
- 相邻采样帧:换位发生在两帧之间,边界即 B 侧首采样帧、视同已确认;
- 连续运动回退:段内持续中间速度(慢漂移)超 hold_tol → 整行回退;
- 内部单样本 hold:reader=None(边界未确认)回退;定向加密两侧确认后
  放行,且加密定位的边界帧 = 合成换位帧;
- \\fad 渐隐:时间连续的相邻 hold 事件成对注入、隔断口的不注入、
  step_fade_ms=0 关闭;
- 合成接线:对齐投票按原始行集合(align_boxes/line_align_index),
  事件 line_idx 为拆分轨迹下标,由调用方重映射。

fixture 沿用 tests/test_motion_ass_collapse.py 的模式:合成逐帧 ok 的
TrackedQuad 列表 + 直接构造 LineTrack;实测采样用含 ``measured`` 字典的
stub 报告(与 LineVerifyReport.measured 同形:帧号 → (cx, cy, 分数))。
定向加密用注入的 ``frame_reader`` 合成帧源(黑底 + 固定噪声文字补丁,
按帧出现在 A/M/B 位置),不碰真实视频解码。
"""

from __future__ import annotations

import os
import sys

import numpy as np

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from core.motion_ass import (  # noqa: E402
    MotionAssConfig,
    LinePose,
    LineTrack,
    synthesize_events,
)
from core.scene_plane_tracker import TrackedQuad  # noqa: E402
from core.step_segmentation import (  # noqa: E402
    attach_step_fades,
    segment_line_holds,
)

FPS = 25.0
N_FRAMES = 220
BOX = (100.0, 100.0, 200.0, 150.0)     # w=100、h=50
A_CENTER = (60.0, 130.0)
M_CENTER = (110.0, 130.0)
B_CENTER = (170.0, 130.0)


# ---------------------------------------------------------------------------
# fixtures
# ---------------------------------------------------------------------------

def make_tracks(n: int = N_FRAMES, fps: float = FPS) -> list:
    """全 ok 的平移恒等 TrackedQuad 列表(时间轴 i/fps)。"""
    base = np.array([[100.0, 100.0], [200.0, 100.0],
                     [200.0, 150.0], [100.0, 150.0]])
    ident = np.eye(3)
    out = []
    for i in range(n):
        quad = (np.hstack([base, np.ones((4, 1))]) @ ident.T)[:, :2]
        out.append(TrackedQuad(
            frame_num=i, time_sec=i / fps, status="ok",
            quad=quad.tolist(),
            homography=[[float(v) for v in row] for row in ident],
            homography_inv=[[float(v) for v in row] for row in ident]))
    return out


def make_line(frames=None, center=(150.0, 125.0)) -> LineTrack:
    """恒位姿(仍带轻微跟踪噪声语义的直线位姿)的 LineTrack。"""
    if frames is None:
        frames = list(range(N_FRAMES))
    poses = {f: LinePose(center=center, angle_deg=0.0, scale=1.0)
             for f in frames}
    return LineTrack(text="テスト", ref_box=BOX, height=50.0, poses=poses)


def make_report(measured: dict):
    """LineVerifyReport 同形 stub(只暴露 measured)。"""
    from types import SimpleNamespace
    return SimpleNamespace(measured=measured)


def measured_at(pairs: dict, score: float = 0.9) -> dict:
    """{帧号: 中心} → LineVerifyReport.measured 同形字典。"""
    return {f: (float(c[0]), float(c[1]), score) for f, c in pairs.items()}


def noise_tile(w: int = 60, h: int = 20) -> np.ndarray:
    rng = np.random.default_rng(7)
    return rng.integers(0, 255, (h, w), dtype=np.uint8)


def make_frame_reader(positions: dict, tile: np.ndarray,
                      size=(240, 180)) -> dict:
    """合成帧源:帧号 → 黑底图,文字补丁按 ``positions[帧号谓词]`` 摆放。

    ``positions`` = [(谓词帧号→bool, 中心), ...] 按序首 个命中者生效。
    """
    def render(f: int) -> np.ndarray:
        img = np.zeros((size[1], size[0], 3), dtype=np.uint8)
        for pred, center in positions:
            if pred(f):
                x0 = int(round(center[0] - tile.shape[1] / 2.0))
                y0 = int(round(center[1] - tile.shape[0] / 2.0))
                img[y0:y0 + tile.shape[0], x0:x0 + tile.shape[1], :] = \
                    tile[..., None]
                break
        return img

    cache: dict = {}

    def reader(wanted):
        out = {}
        for f in wanted:
            if f not in cache:
                cache[f] = render(int(f))
            out[int(f)] = cache[f]
        return out

    return reader


def base_cfg(**kw) -> MotionAssConfig:
    return MotionAssConfig(hold_pos_segmentation=True, **kw)


# ---------------------------------------------------------------------------
# 开关与回退
# ---------------------------------------------------------------------------

def test_disabled_flag_is_identity():
    line = make_line()
    rep = make_report(measured_at({10: A_CENTER, 50: B_CENTER}))
    tracks = [line]
    out = segment_line_holds(tracks, {0: rep},
                             MotionAssConfig(hold_pos_segmentation=False),
                             frame_reader=lambda w: {}, video_height=1080)
    assert out.tracks == tracks
    assert out.orig_index_of == [0]
    assert out.holds_by_line == {}


def test_no_measured_keeps_line():
    line = make_line()
    out = segment_line_holds([line], {}, base_cfg(), video_height=1080)
    assert out.tracks == [line]
    assert out.orig_index_of == [0]
    assert out.holds_by_line == {}


def test_continuous_drift_falls_back():
    # 每采样 +4px 的持续慢漂移:单 run 内偏差超 hold_tol → 整行回退。
    line = make_line()
    drift = {10 + 12 * i: (A_CENTER[0] + 4.0 * i, A_CENTER[1])
             for i in range(16)}
    rep = make_report(measured_at(drift))
    out = segment_line_holds([line], {0: rep}, base_cfg(), video_height=1080)
    assert out.holds_by_line == {}
    assert out.tracks == [line]
    assert out.orig_index_of == [0]


# ---------------------------------------------------------------------------
# 静止(K=1)与步进(K≥2)
# ---------------------------------------------------------------------------

def test_static_line_single_hold_and_event():
    tracks = make_tracks()
    line = make_line()
    static = {10 + 12 * i: A_CENTER for i in range(16)}
    rep = make_report(measured_at(static))
    out = segment_line_holds([line], {0: rep}, base_cfg(), video_height=1080)
    assert list(out.holds_by_line) == [0]
    holds = out.holds_by_line[0]
    assert len(holds) == 1
    assert holds[0].start_frame == 0 and holds[0].end_frame == N_FRAMES - 1
    assert holds[0].center == A_CENTER
    assert out.orig_index_of == [0, 0][:len(out.tracks)]
    assert len(out.tracks) == 1
    # 合成:单条 \pos 事件、无 \move
    events = synthesize_events(out.tracks, tracks, MotionAssConfig(),
                               video_height=1080)
    assert len(events) == 1
    assert "\\pos(" in events[0]["tags"]
    assert "\\move(" not in events[0]["tags"]


def test_stepped_two_holds_partition_timeline():
    line = make_line()
    # A 侧 3 个采样、B 侧 2 个;中点边界 = (110+160+1)//2 = 135 → 首个
    # ≥135 的 ok 帧 135。
    pairs = {10: A_CENTER, 60: A_CENTER, 110: A_CENTER,
             160: B_CENTER, 210: B_CENTER}
    rep = make_report(measured_at(pairs))
    out = segment_line_holds([line], {0: rep}, base_cfg(), video_height=1080)
    holds = out.holds_by_line[0]
    assert len(holds) == 2
    # hold 段划分整条 ok 时间轴:首段从行首帧、末段到行尾帧,无空洞。
    assert (holds[0].start_frame, holds[0].end_frame) == (0, 134)
    assert (holds[1].start_frame, holds[1].end_frame) == (135, N_FRAMES - 1)
    assert holds[0].center == A_CENTER and holds[1].center == B_CENTER
    assert holds[1].n_samples == 2
    assert len(out.tracks) == 2
    assert out.orig_index_of == [0, 0]
    # 拆分轨迹恒位姿且中心各归各段
    for track, center in zip(out.tracks, (A_CENTER, B_CENTER)):
        centers = {p.center for p in track.poses.values()}
        assert centers == {center}


def test_visibility_break_boundary_confirmed():
    # ok 帧 0..30 与 60..219 两段;采样横跨断口 → 断口首帧即边界。
    frames = list(range(0, 31)) + list(range(60, N_FRAMES))
    line = make_line(frames=frames)
    pairs = {10: A_CENTER, 20: A_CENTER, 70: B_CENTER, 90: B_CENTER}
    rep = make_report(measured_at(pairs))
    out = segment_line_holds([line], {0: rep}, base_cfg(), video_height=1080)
    holds = out.holds_by_line[0]
    assert len(holds) == 2
    assert (holds[0].start_frame, holds[0].end_frame) == (0, 30)
    assert (holds[1].start_frame, holds[1].end_frame) == (60, N_FRAMES - 1)
    assert holds[0].bound_confirmed == (True, True)
    # 断口内侧没有把 31..59 的假想帧划给任何 hold
    all_hold_frames = set()
    for track in out.tracks:
        all_hold_frames |= set(track.poses)
    assert all_hold_frames == set(frames)


def test_adjacent_samples_boundary_confirmed():
    # B 侧首采样帧 = A 侧末采样帧 + 1:换位发生在两帧之间,边界即 f_b。
    line = make_line()
    pairs = {10: A_CENTER, 60: A_CENTER, 61: B_CENTER, 110: B_CENTER}
    rep = make_report(measured_at(pairs))
    out = segment_line_holds([line], {0: rep}, base_cfg(), video_height=1080)
    holds = out.holds_by_line[0]
    assert len(holds) == 2
    assert (holds[0].start_frame, holds[0].end_frame) == (0, 60)
    assert (holds[1].start_frame, holds[1].end_frame) == (61, N_FRAMES - 1)
    assert holds[0].bound_confirmed == (True, True)


# ---------------------------------------------------------------------------
# 内部单样本 hold 与定向加密
# ---------------------------------------------------------------------------

def test_interior_single_sample_unconfirmed_falls_back():
    # reader=None:内部单样本 hold 两侧边界未确认 → 整行回退。
    line = make_line()
    pairs = {10: A_CENTER, 30: A_CENTER, 45: M_CENTER,
             60: B_CENTER, 80: B_CENTER}
    rep = make_report(measured_at(pairs))
    out = segment_line_holds([line], {0: rep}, base_cfg(), video_height=1080)
    assert out.holds_by_line == {}
    assert out.tracks == [line]


def test_densify_confirms_interior_single_sample():
    # 帧源换位:A < 40 → M ∈ [40,50) → B ≥ 50。中间单样本(45)两侧边界
    # 经定向加密确认 → 3 个 hold;加密定位的边界 = 真实换位帧 40 / 50。
    tile = noise_tile()
    reader = make_frame_reader([
        (lambda f: f < 40, A_CENTER),
        (lambda f: f < 50, M_CENTER),
        (lambda f: True, B_CENTER),
    ], tile)
    line = make_line()
    pairs = {10: A_CENTER, 30: A_CENTER, 45: M_CENTER,
             60: B_CENTER, 80: B_CENTER}
    rep = make_report(measured_at(pairs))
    cfg = base_cfg(verify_min_score=0.7)
    out = segment_line_holds([line], {0: rep}, cfg, video_height=1080,
                             frame_reader=reader)
    holds = out.holds_by_line[0]
    assert len(holds) == 3
    assert [(h.start_frame, h.end_frame) for h in holds] == \
        [(0, 39), (40, 49), (50, N_FRAMES - 1)]
    # 短段经邻域补采后中心是「补采中位」,与名义中心允许亚像素差。
    for hold, center in zip(holds, (A_CENTER, M_CENTER, B_CENTER)):
        assert abs(hold.center[0] - center[0]) <= 1.0
        assert abs(hold.center[1] - center[1]) <= 1.0
    assert holds[1].n_samples >= 2
    assert holds[1].bound_confirmed == (True, True)


# ---------------------------------------------------------------------------
# 合成接线与 \fad 渐隐
# ---------------------------------------------------------------------------

def _two_hold_split():
    tracks = make_tracks()
    line = make_line()
    pairs = {10: A_CENTER, 110: A_CENTER, 160: B_CENTER, 210: B_CENTER}
    rep = make_report(measured_at(pairs))
    out = segment_line_holds([line], {0: rep}, base_cfg(), video_height=1080)
    return tracks, out


def test_synthesize_two_holds_events_and_fades():
    tracks, out = _two_hold_split()
    events = synthesize_events(out.tracks, tracks, MotionAssConfig(),
                               video_height=1080)
    assert len(events) == 2
    assert all("\\pos(" in ev["tags"] and "\\move(" not in ev["tags"]
               for ev in events)
    # line_idx 为拆分轨迹下标(0,1),按 scripts 接线顺序先重映射回原始
    # 行号,再注入渐隐。
    assert [ev["line_idx"] for ev in events] == [0, 1]
    for ev in events:
        ev["line_idx"] = out.orig_index_of[ev["line_idx"]]
    n = attach_step_fades(events, out.holds_by_line,
                          MotionAssConfig(step_fade_ms=100))
    assert n == 1
    assert "\\fad(0,100)" in events[0]["tags"]
    assert "\\fad(100,0)" in events[1]["tags"]


def test_fades_skip_gap_and_zero_ms():
    tracks, out = _two_hold_split()
    events = synthesize_events(out.tracks, tracks, MotionAssConfig(),
                               video_height=1080)
    # 中间隔不可见跨度(时间不连续)的不注入。
    events[1]["start_time"] = "0:00:20.00"
    n = attach_step_fades(events, out.holds_by_line,
                          MotionAssConfig(step_fade_ms=100))
    assert n == 0
    assert "\\fad" not in events[0]["tags"]
    # step_fade_ms=0:纯硬切。
    events2 = synthesize_events(out.tracks, tracks, MotionAssConfig(),
                                video_height=1080)
    n2 = attach_step_fades(events2, out.holds_by_line,
                           MotionAssConfig(step_fade_ms=0))
    assert n2 == 0
    assert all("\\fad" not in ev["tags"] for ev in events2)


def test_align_vote_on_original_boxes():
    # 拆分轨迹的行框是复制品:对齐投票按原始行集合进行(本例单行,行为
    # 等价,只验证参数通路不抛错且判定照常产出)。
    tracks, out = _two_hold_split()
    diag = {}
    events = synthesize_events(out.tracks, tracks, MotionAssConfig(),
                               video_height=1080, diagnostics=diag,
                               align_boxes=[BOX], line_align_index=[0, 0])
    assert len(events) == 2
    assert diag.get("rows")


# ---------------------------------------------------------------------------
# 配置口径
# ---------------------------------------------------------------------------

def test_continuous_scroll_jumps_majority_falls_back():
    # 匀速滚动:几乎每对相邻采样都在「换位」(段内攒不出停帧) → 整行回退。
    line = make_line()
    drift = {10 + 12 * i: (A_CENTER[0] + 14.0 * i, A_CENTER[1])
             for i in range(16)}
    rep = make_report(measured_at(drift))
    out = segment_line_holds([line], {0: rep}, base_cfg(), video_height=1080)
    assert out.holds_by_line == {}
    assert out.tracks == [line]


def test_jumps_across_visibility_gaps_not_scroll_evidence():
    # 逐场景出现的文本:跳变都横跨 ok 帧断口(天然边界),段内全静止 →
    # 正常 hold 拆分,不判连续运动。ok 帧:0..49 / 100..149 / 200..219,
    # 每段中部各有两个同位采样、三段位置各异。
    frames = (list(range(0, 50)) + list(range(100, 150))
              + list(range(200, 220)))
    line = make_line(frames=frames)
    pairs = {10: A_CENTER, 40: A_CENTER,
             110: B_CENTER, 140: B_CENTER,
             210: (100.0, 130.0)}
    rep = make_report(measured_at(pairs))
    out = segment_line_holds([line], {0: rep}, base_cfg(), video_height=1080)
    assert len(out.holds_by_line.get(0, ())) == 3
    assert len(out.tracks) == 3
    # 断口即边界:三段 hold 与三段 ok 帧区间一致
    assert [(h.start_frame, h.end_frame) for h in out.holds_by_line[0]] == \
        [(0, 49), (100, 149), (200, 219)]


def test_config_defaults_and_scaling():
    cfg = MotionAssConfig()
    assert cfg.hold_pos_segmentation is True
    assert cfg.hold_tol_px == 2.5
    assert cfg.step_jump_px == 0.0
    assert cfg.step_fade_ms == 100
    assert cfg.densify_halfwin_frames == 12
    # 4K 下 hold 容差按分辨率归一(段内允许偏离翻倍)。
    out = segment_line_holds(
        [make_line()], {}, MotionAssConfig(hold_tol_px=2.5),
        video_height=2160)
    assert out.tracks  # 无实测不进分段,仅验证参数路径不抛错


# ---------------------------------------------------------------------------
# B2:切片后按原 hold 绝对边界注入渐隐
# ---------------------------------------------------------------------------

def test_fades_anchor_at_hold_boundaries_after_slicing():
    """遮挡切片把 hold A 拆成两段后,渐隐只落在原 hold 边界,
    hold 内部的切片边界不重新从 0 渐显。"""
    from core.motion_ass import MotionAssConfig
    from core.step_segmentation import HoldSegment, attach_step_fades

    holds = [
        HoldSegment(start_frame=0, end_frame=24,
                    center=(100.0, 50.0), n_samples=3,
                    bound_confirmed=(True, True)),
        HoldSegment(start_frame=50, end_frame=74,
                    center=(160.0, 50.0), n_samples=3,
                    bound_confirmed=(True, True)),
    ]
    times_by_frame = {f: f / 25.0 for f in range(100)}
    # hold A [0,1.0s) 被遮挡切片拆成两段;hold B [2.0,3.0s) 一段。
    events = [
        {"line_idx": 0, "start_time": "0:00:00.00", "end_time": "0:00:00.40",
         "tags": "", "body": "x"},
        {"line_idx": 0, "start_time": "0:00:00.40", "end_time": "0:00:01.00",
         "tags": "", "body": "x"},
        {"line_idx": 0, "start_time": "0:00:02.00", "end_time": "0:00:03.00",
         "tags": "", "body": "x"},
    ]
    n = attach_step_fades(events, {0: holds}, MotionAssConfig(step_fade_ms=80),
                          times_by_frame=times_by_frame)
    # hold A 末段结束于 1.00,hold B 起于 2.00:边界时间 2.00 处只有起始
    # 事件匹配,两侧不衔接(中间是不可见间隔)→ 不注入;hold 内部的切片
    # 边界(0.40)同样无渐隐。
    assert n == 0
    assert all("\\fad" not in e["tags"] for e in events)
    # 连续边界场景:
    holds2 = [
        HoldSegment(start_frame=0, end_frame=24, center=(100.0, 50.0),
                    n_samples=3, bound_confirmed=(True, True)),
        HoldSegment(start_frame=10, end_frame=20, center=(160.0, 50.0),
                    n_samples=3, bound_confirmed=(True, True)),
    ]
    events2 = [
        {"line_idx": 0, "start_time": "0:00:00.00", "end_time": "0:00:00.40",
         "tags": "", "body": "x"},
        {"line_idx": 0, "start_time": "0:00:00.40", "end_time": "0:00:00.44",
         "tags": "", "body": "x"},
        {"line_idx": 0, "start_time": "0:00:00.44", "end_time": "0:00:00.88",
         "tags": "", "body": "x"},
    ]
    n2 = attach_step_fades(events2, {0: holds2},
                           MotionAssConfig(step_fade_ms=80),
                           times_by_frame=times_by_frame)
    # hold2 边界 = 帧 10 = 0.40s:渐隐落在衔接该边界的 (event0, event1) 上;
    # hold 内部的切片边界 0.44(event1 → event2)不受影响。
    assert n2 == 1
    assert events2[0]["tags"] == "{\\fad(0,80)}"
    assert events2[1]["tags"] == "{\\fad(80,0)}"
    assert "\\fad" not in events2[2]["tags"]
