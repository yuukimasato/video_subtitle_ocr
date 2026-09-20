# tests/test_pose_verify.py
"""屏幕空间行轨迹实测校正(core/pose_verify.py)离线单元测试。

用合成 FFV1 无损视频验证三种场景(背景横移 + 固定文字面板是 11.mp4 的
真实形态;单应轨迹被背景运动污染后行位姿漂移,实测校正应把它锚回):

- 固定文字 + 漂移单应(逐帧 +2px 平移污染)→ 判定静止,位姿吸附为
  常量,合成器输出单条 ``\\pos`` 事件(无 ``\\move``/``\\t``);
- 移动文字 + 恒等单应(单应声称静止)→ 不判静止,校正后的中心跟随
  真实位移,合成输出 ``\\move`` 且端点落在真值上;
- 文字中途消失(滚出画面)→ 失配跨度内的位姿被删除,轨迹切链,事件
  不再覆盖消失后的时间段。

失配删除的证据密度分级(防「两个稀疏采样失配就删掉数秒可见位姿」):
- 短暂遮挡造成的孤立失配采样,跨度超过 long_span_frames 时先在跨度
  内部补采确认:内部帧见到文字 → 整段保留并留痕(spans),不删;
- 真正消失的超长跨度,内部确认帧仍全部失配 → 确认删除(幽灵文字
  仍被去掉,reason=long_span_confirmed);
- 单帧位移超出常规搜索半径(漂移累积)→ 扩半径重试找回,纳入实测
  序列,不卷入失配删除;
- 边界分([drop_score, min_score),模糊/退化帧)只标记 borderline、
  切断失配配对,不触发删除。

另有退化守卫:零纹理补丁(纯色块)不参与校正(伪匹配防护);分块
解码(block_max_frames)与整批解码结果逐项一致(纯内存优化)。

两遍扫描管线(4K 内存/耗时优化)的不变式:
- 有效块帧数按 ``(1080/video_height)²`` 归一(字节预算恒定):1080p/缺省
  逐字节不变,2160p → 16,1440p → 36,``block_max_frames ≤ 0`` 不归一;
- **顺序读取帧数总量**在多块场景下有界(≈ 一遍视频长度 + 确认遍),不是
  「块数 × 视频长度」:块按时间顺序解码共用一个单调前进的顺序游标,相邻块
  采样窗重叠时靠有界预取避免回退重读;
- 非单调请求(帧号 < 游标位置)确定性回退:重开捕获从头顺序读,取到的帧
  与独立解码逐字节一致,不丢帧、不抛异常;
- 阶段 B 只消费确认帧字典:补上采样帧不改变任何结果(采样帧的分数/偏移
  早已存进 ``_LineWork``)。
"""

from __future__ import annotations

import os
import sys

import cv2
import numpy as np
import pytest

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from core.motion_ass import (  # noqa: E402
    MotionAssConfig,
    LinePose,
    LineTrack,
    build_line_tracks,
    synthesize_events,
)
from core import pose_verify  # noqa: E402
from core.pose_verify import (  # noqa: E402
    VerifyConfig,
    _match_line_samples,
    _sample_frames,
    verify_line_tracks,
)
from core.scene_plane_tracker import TrackedQuad  # noqa: E402

FRAME_W, FRAME_H = 320, 240
FPS = 8
N_FRAMES = 24
BOX = (60.0, 80.0, 180.0, 100.0)  # 行框(静止时)
BOX_CENTER = (120.0, 90.0)


# ---------------------------------------------------------------------------
# 合成视频与轨迹构造
# ---------------------------------------------------------------------------

def make_text_bar() -> np.ndarray:
    """黑底白缝的"文字条"。条纹间距不规则(模拟文字字形):周期性纹理
    会让归一化互相关出现 ±周期的混叠峰,实测中心散布,静止判定失效。"""
    w = int(BOX[2] - BOX[0])
    h = int(BOX[3] - BOX[1])
    bar = np.zeros((h, w, 3), np.uint8)
    rng = np.random.default_rng(11)
    x = 4
    while x < w - 6:
        bar[2:h - 2, x:x + 3] = 255
        x += 3 + int(rng.integers(3, 11))
    return bar


def write_video(path, draw_frame, n_frames: int = N_FRAMES) -> str:
    writer = cv2.VideoWriter(path, cv2.VideoWriter_fourcc(*"FFV1"), FPS,
                             (FRAME_W, FRAME_H))
    assert writer.isOpened()
    for f in range(n_frames):
        writer.write(draw_frame(f))
    writer.release()
    return path


def paste(frame, bar, x, y):
    h, w = bar.shape[:2]
    frame[y:y + h, x:x + w] = bar
    return frame


def panning_background(f: int) -> np.ndarray:
    """横向平移的条纹背景(每帧 +3px),给跟踪"污染源"。"""
    frame = np.full((FRAME_H, FRAME_W, 3), 60, np.uint8)
    offset = (3 * f) % 24
    for x in range(-24 + offset, FRAME_W + 24, 24):
        cv2.rectangle(frame, (x, 0), (x + 10, FRAME_H - 1), (130, 130, 130), -1)
    return frame


def translation_h(dx: float, dy: float = 0.0) -> list:
    return [[1.0, 0.0, dx], [0.0, 1.0, dy], [0.0, 0.0, 1.0]]


def make_tracks(n: int = N_FRAMES, dx_per_frame: float = 0.0) -> list:
    """逐帧 ok 轨迹,单应 = 逐帧 dx 平移(模拟背景运动漏进跟踪)。"""
    out = []
    for f in range(n):
        h_mat = translation_h(dx_per_frame * f)
        h_inv = translation_h(-dx_per_frame * f)
        quad = np.array([[60, 80], [180, 80], [180, 100], [60, 100]],
                        np.float64) + np.array([dx_per_frame * f, 0.0])
        out.append(TrackedQuad(
            frame_num=f, time_sec=f / FPS, status="ok",
            quad=[[list(map(float, p))] for p in quad],
            homography=h_mat, homography_inv=h_inv))
    return out


VCFG = VerifyConfig()


# ---------------------------------------------------------------------------
# 用例
# ---------------------------------------------------------------------------

def test_static_text_with_drifting_homography_snaps_to_const(tmp_path):
    r"""固定文字 + 漂移单应:实测校正判静止 → 单条 \pos 事件。"""
    bar = make_text_bar()

    def draw(f):
        return paste(panning_background(f), bar,
                     int(BOX[0]), int(BOX[1]))

    video = write_video(str(tmp_path / "static.avi"), draw)
    tracks = make_tracks(dx_per_frame=2.0)  # 单应逐帧漂移 +2px
    line_tracks = build_line_tracks([BOX], ["固定行"], tracks, ref_frame=0)

    new_tracks, reports = verify_line_tracks(video, tracks, line_tracks, VCFG)

    assert reports[0].static, reports[0]
    poses = new_tracks[0].poses
    centers = {p.center for p in poses.values()}
    assert len(centers) == 1  # 常量位姿
    cx, cy = next(iter(centers))
    assert abs(cx - BOX_CENTER[0]) < 2.0 and abs(cy - BOX_CENTER[1]) < 2.0

    events = synthesize_events(new_tracks, tracks, MotionAssConfig(),
                               style="Scene")
    assert len(events) == 1
    tags = events[0]["tags"]
    assert "\\pos(" in tags
    assert "\\move(" not in tags
    assert "\\t(" not in tags


def test_moving_text_with_identity_homography_gets_corrected(tmp_path):
    r"""移动文字 + 恒等单应:校正后中心跟随真实位移,输出 \move。"""
    bar = make_text_bar()

    def draw(f):
        return paste(panning_background(f), bar,
                     int(BOX[0]) + 2 * f, int(BOX[1]))

    video = write_video(str(tmp_path / "moving.avi"), draw)
    tracks = make_tracks(dx_per_frame=0.0)  # 单应声称静止
    line_tracks = build_line_tracks([BOX], ["移动行"], tracks, ref_frame=0)

    new_tracks, reports = verify_line_tracks(video, tracks, line_tracks, VCFG)

    assert not reports[0].static
    assert reports[0].corrected
    # 末帧实测中心 ≈ 初值 + 2*(n-1)
    last = max(new_tracks[0].poses)
    cx, cy = new_tracks[0].poses[last].center
    assert cx == pytest.approx(BOX_CENTER[0] + 2 * (N_FRAMES - 1), abs=3.0)

    events = synthesize_events(new_tracks, tracks, MotionAssConfig(),
                               style="Scene")
    assert events, "moving line must still produce event(s)"
    assert "\\move(" in events[0]["tags"]


def test_vanishing_text_drops_tail_poses(tmp_path):
    """文字中途消失:失配跨度删除位姿 → 事件止于消失附近。"""
    bar = make_text_bar()
    gone_from = 12

    def draw(f):
        frame = panning_background(f)
        if f < gone_from:
            paste(frame, bar, int(BOX[0]), int(BOX[1]))
        return frame

    video = write_video(str(tmp_path / "vanish.avi"), draw)
    tracks = make_tracks(dx_per_frame=0.0)
    line_tracks = build_line_tracks([BOX], ["消失行"], tracks, ref_frame=0)

    new_tracks, reports = verify_line_tracks(video, tracks, line_tracks, VCFG)

    kept = sorted(new_tracks[0].poses)
    assert reports[0].n_good >= 2
    assert max(kept) < N_FRAMES - 1  # 尾部落选
    assert max(kept) <= gone_from + 4  # 删除跨度不超过采样间隔的合理范围

    events = synthesize_events(new_tracks, tracks, MotionAssConfig(),
                               style="Scene")
    assert events
    last_end = max(e["end_time"] for e in events)
    from core.motion_ass import format_ass_time
    # 事件终点不超过消失后 1 秒(采样间隔量级)
    assert last_end <= format_ass_time((gone_from + FPS) / FPS)


def test_textureless_patch_skips_verification(tmp_path):
    """纯色补丁(零方差)不参与校正:宁可不校,不做伪匹配。

    色块足够大,覆盖行框全部漂移位置:任意采样位置的候选补丁都无纹理,
    自适应补丁选择找不到可用参考 → 整行跳过,轨迹原样保留。"""
    def draw(f):
        # 漂移总行程 2*(n-1)=46px:行框右缘最多到 226,块 50..250 全覆盖
        return paste(np.full((FRAME_H, FRAME_W, 3), 60, np.uint8),
                     np.full((20, 200, 3), 10, np.uint8),
                     50, int(BOX[1]))

    video = write_video(str(tmp_path / "flat.avi"), draw)
    tracks = make_tracks(dx_per_frame=2.0)
    line_tracks = build_line_tracks([BOX], ["纯色"], tracks, ref_frame=0)

    new_tracks, reports = verify_line_tracks(video, tracks, line_tracks, VCFG)

    assert not reports[0].static and not reports[0].corrected
    # 轨迹原样返回(位姿仍为漂移单应产出)
    assert new_tracks[0].poses[5].center[0] == pytest.approx(
        BOX_CENTER[0] + 2.0 * 5)


# ---------------------------------------------------------------------------
# 失配删除的证据密度分级(任务:防可见文字被误删出时间空洞)
# ---------------------------------------------------------------------------

def test_long_span_kept_when_interior_frame_visible(tmp_path):
    """超长失配跨度的跨内确认:两个采样失配但内部帧可见 → 保留不删。

    旧逻辑里相邻两个稀疏采样失配就删掉整段位姿;短时遮挡/运动模糊造成
    的假「不可见」会让可见文字出现数秒时间空洞。现在跨度帧长超过
    long_span_frames 时先在跨度内部均匀补采确认,见到文字(≥drop_score,
    含边界分)即整段保留,并把决策留痕在 report.spans。"""
    bar = make_text_bar()
    occluded = (9, 11)  # 两个相邻采样帧(帧距 2)短暂遮挡,中间帧 10 可见

    def draw(f):
        if f in occluded:
            # 整帧均匀空白:零方差窗口 → 归一化互相关无定义处记 0 分,
            # 常规/扩半径两次匹配都必然硬失配
            return np.full((FRAME_H, FRAME_W, 3), 60, np.uint8)
        return paste(panning_background(f), bar, int(BOX[0]), int(BOX[1]))

    video = write_video(str(tmp_path / "suspect.avi"), draw)
    tracks = make_tracks(dx_per_frame=0.0)
    line_tracks = build_line_tracks([BOX], ["闪烁行"], tracks, ref_frame=0)
    # long_span_frames 压到 1:9→11 的失配跨度(2 帧)按超长处理,
    # 触发跨内确认(确认帧 = 10,文字可见)
    cfg = VerifyConfig(long_span_frames=1, span_confirm_max=3)

    new_tracks, reports = verify_line_tracks(video, tracks, line_tracks, cfg)

    rep = reports[0]
    assert len(rep.spans) == 1, rep.spans
    span = rep.spans[0]
    assert (span.start, span.end) == occluded
    assert not span.dropped and span.reason == "long_span_kept_visible"
    assert span.scores[occluded[0]] < cfg.drop_score   # 边界采样确实硬失配
    assert span.scores[10] >= cfg.drop_score           # 确认帧见到文字
    assert all(f in new_tracks[0].poses for f in range(N_FRAMES))  # 一帧未删


def test_long_mismatch_span_confirmed_then_dropped(tmp_path):
    """超长失配跨度 + 跨内确认帧仍失配 → 确认删除(幽灵文字仍被去掉)。

    分级确认只应放过「假不可见」:文字真消失时内部补采同样失配,删除
    照常执行,reason 升级为 long_span_confirmed。"""
    bar = make_text_bar()
    blank_from, blank_to = 9, 14  # 该段文字真消失(整帧均匀空白)

    def draw(f):
        if blank_from <= f <= blank_to:
            return np.full((FRAME_H, FRAME_W, 3), 60, np.uint8)
        return paste(panning_background(f), bar, int(BOX[0]), int(BOX[1]))

    video = write_video(str(tmp_path / "gap.avi"), draw)
    tracks = make_tracks(dx_per_frame=0.0)
    line_tracks = build_line_tracks([BOX], ["间断行"], tracks, ref_frame=0)
    cfg = VerifyConfig(long_span_frames=1, span_confirm_max=3)

    new_tracks, reports = verify_line_tracks(video, tracks, line_tracks, cfg)

    rep = reports[0]
    assert rep.spans, "至少应有一个失配跨度"
    assert all(s.dropped for s in rep.spans)
    confirmed = [s for s in rep.spans if s.reason == "long_span_confirmed"]
    assert confirmed
    assert any(s.start <= blank_from and s.end >= blank_to
               for s in confirmed)  # 失配采样 9,11,12,14 构成的跨度
    kept = sorted(new_tracks[0].poses)
    assert not any(blank_from <= f <= blank_to for f in kept)


def test_retry_recovers_sample_displaced_beyond_search_radius(tmp_path):
    """扩半径重试:采样帧文字位移超出常规搜索半径 → 重试找回。

    文字仅在第 12 帧(采样帧)垂直跳变 +60px:常规半径(40)的窗口
    够不到(中心 90±50 不含 y=140..160),旧逻辑记为失配;重试半径
    (80)的窗口(90±90)完整覆盖 → 高分找回并纳入实测序列。该帧
    分数 ≥ min_score,不会卷进任何失配删除跨度。"""
    bar = make_text_bar()
    jump_frame, jump_dy = 12, 60

    def draw(f):
        # 纯黑背景:均匀区域零方差,不产生伪峰,重试行为完全确定
        frame = np.full((FRAME_H, FRAME_W, 3), 0, np.uint8)
        dy = jump_dy if f == jump_frame else 0
        return paste(frame, bar, int(BOX[0]), int(BOX[1]) + dy)

    video = write_video(str(tmp_path / "jump.avi"), draw)
    tracks = make_tracks(dx_per_frame=0.0)
    line_tracks = build_line_tracks([BOX], ["跳变行"], tracks, ref_frame=0)
    cfg = VerifyConfig()

    new_tracks, reports = verify_line_tracks(video, tracks, line_tracks, cfg)

    rep = reports[0]
    assert jump_frame in rep.retry_scores                 # 重试已触发
    assert rep.retry_scores[jump_frame] >= cfg.min_score  # 更大窗口找回
    assert rep.scores[jump_frame] >= cfg.min_score        # 重试分被采纳
    assert rep.n_good == rep.n_samples                    # 纳入实测序列
    assert rep.spans == []                                # 未卷入删除
    assert not rep.static                                 # 偏离中位,不判静止
    # 该帧位姿按实测锚到跳变后的真实位置(预测 + 实测偏移)
    cx, cy = new_tracks[0].poses[jump_frame].center
    assert abs(cx - BOX_CENTER[0]) < 3.0
    assert abs(cy - (BOX_CENTER[1] + jump_dy)) < 3.0


def test_borderline_score_blocks_drop_and_gets_flagged(tmp_path):
    """边界分分级:失配对中一方分数落在 [drop_score, min_score) → 不删。

    运动模糊帧(matchTemplate 分数退化但文字仍在)按分级语义保留并
    标记 borderline,同时切断失配配对——另一采样哪怕完全失配也不构成
    删除跨度(min_score 抬高到 0.85 使模糊帧落在边界带内)。"""
    bar = make_text_bar()
    blur_frame, gone_frame = 9, 11
    cfg = VerifyConfig(min_score=0.85)

    def draw(f):
        if f == gone_frame:
            return np.full((FRAME_H, FRAME_W, 3), 60, np.uint8)
        img = paste(panning_background(f), bar, int(BOX[0]), int(BOX[1]))
        if f == blur_frame:
            img = cv2.GaussianBlur(img, (11, 11), 0)  # 实测分数 ≈0.75
        return img

    video = write_video(str(tmp_path / "blur.avi"), draw)
    tracks = make_tracks(dx_per_frame=0.0)
    line_tracks = build_line_tracks([BOX], ["模糊行"], tracks, ref_frame=0)

    new_tracks, reports = verify_line_tracks(video, tracks, line_tracks, cfg)

    rep = reports[0]
    assert blur_frame in rep.borderline
    assert blur_frame not in rep.retry_scores  # 边界分 ≥ drop_score,不触发重试
    assert rep.spans == []                     # 边界分切断配对,无删除跨度
    poses = new_tracks[0].poses
    assert all(f in poses for f in range(N_FRAMES))


# ---------------------------------------------------------------------------
# 确认帧:未测不得按「已确认」删除 / 确认帧数随跨度增长
# ---------------------------------------------------------------------------

def test_long_span_unconfirmed_keeps_span(tmp_path, monkeypatch):
    """超长跨度但**确认帧一个都没测到**(补解码整批读不出来)→ 保留不删。

    旧行为把「确认帧解码不到」(``frames_img.get(f) is None → continue``)
    与「确认帧测到且硬失配」混为一谈,按 ``long_span_confirmed`` 删掉整段
    ——语义应是「未测」:证据不足既不能删,也不能声称确认过。三种判定
    必须可区分(long_span_confirmed / long_span_kept_visible /
    long_span_unconfirmed)。"""
    bar = make_text_bar()
    occluded = (9, 11)  # 两个相邻采样帧短暂遮挡,中间帧 10 可见

    def draw(f):
        if f in occluded:
            return np.full((FRAME_H, FRAME_W, 3), 60, np.uint8)
        return paste(panning_background(f), bar, int(BOX[0]), int(BOX[1]))

    video = write_video(str(tmp_path / "unconfirmed.avi"), draw)
    tracks = make_tracks(dx_per_frame=0.0)
    line_tracks = build_line_tracks([BOX], ["闪烁行"], tracks, ref_frame=0)
    cfg = VerifyConfig(long_span_frames=1, span_confirm_max=3)

    # 第二遍(补解码超长跨度确认帧)整批读不出来 → 确认帧全缺席
    real_decode = pose_verify._decode_frames
    calls = {"n": 0}

    def flaky_decode(path, wanted):
        calls["n"] += 1
        if calls["n"] > 1:
            return {}
        return real_decode(path, wanted)

    monkeypatch.setattr(pose_verify, "_decode_frames", flaky_decode)

    logs: list = []
    new_tracks, reports = verify_line_tracks(
        video, tracks, line_tracks, cfg, log=logs.append)

    rep = reports[0]
    assert calls["n"] >= 2                     # 确认帧确实补解码过
    assert len(rep.spans) == 1, rep.spans
    span = rep.spans[0]
    assert (span.start, span.end) == occluded
    assert not span.dropped and span.reason == "long_span_unconfirmed"
    assert all(f in new_tracks[0].poses for f in range(N_FRAMES))  # 一帧未删
    assert any("unconfirmed" in m for m in logs)


@pytest.mark.parametrize("lsf, cap, expected", [
    (48, 3, 3),     # 旧行为(常数 cap)
    (48, 8, 7),     # 默认 cap=8:跨度 372 帧 → 7 个确认点(随跨度增长)
    (10, 8, 8),     # cap 封顶
    (200, 8, 1),    # 下限 1
])
def test_confirm_frame_count_scales_with_span(lsf, cap, expected):
    """确认帧数 = min(cap, max(1, 跨度 // long_span_frames)),不再恒为 cap:
    2000 帧的长跨度不再只有 3 个确认点(每次删除的额外解码代价 ≤ cap 帧)。"""
    n = 400
    lt = LineTrack(
        text="长行", ref_box=BOX, height=BOX[3] - BOX[1],
        poses={f: LinePose(center=BOX_CENTER, angle_deg=0.0, scale=1.0)
               for f in range(n)})
    samples = _sample_frames(sorted(lt.poses), 16)
    frame = np.full((FRAME_H, FRAME_W, 3), 60, np.uint8)
    paste(frame, make_text_bar(), int(BOX[0]), int(BOX[1]))

    work = _match_line_samples(
        0, lt, samples, 0, {0: frame},
        VerifyConfig(long_span_frames=lsf, span_confirm_max=cap))

    picks = list(work.confirm_picks.values())
    assert len(picks) == 1, work.confirm_picks
    a, b = next(iter(work.confirm_picks))
    assert (b - a) // lsf >= 1
    assert len(picks[0]) == expected
    assert len(picks[0]) <= cap


# ---------------------------------------------------------------------------
# 像素阈值按 video_height 归一(与 collapse 的 settle 同一基准)
# ---------------------------------------------------------------------------

def _capture_cfgs(monkeypatch) -> list:
    """钩住阶段 A,记录实际参与匹配的 VerifyConfig。"""
    seen: list = []
    real = pose_verify._match_line_samples

    def spy(li, lt, samples, ref_ok, frames_img, cfg, restrict=None):
        seen.append(cfg)
        return real(li, lt, samples, ref_ok, frames_img, cfg)

    monkeypatch.setattr(pose_verify, "_match_line_samples", spy)
    return seen


def test_pixel_thresholds_scale_by_video_height(tmp_path, monkeypatch):
    """video_height 给出时按 height/1080 等比缩放 static_tol / search_radius
    / retry_radius;分数门限与帧数门限(相对量)不缩放。"""
    bar = make_text_bar()

    def draw(f):
        return paste(panning_background(f), bar, int(BOX[0]), int(BOX[1]))

    video = write_video(str(tmp_path / "scaled.avi"), draw)
    tracks = make_tracks(dx_per_frame=2.0)
    line_tracks = build_line_tracks([BOX], ["固定行"], tracks, ref_frame=0)
    base = VerifyConfig()
    seen = _capture_cfgs(monkeypatch)

    verify_line_tracks(video, tracks, line_tracks, base, video_height=2160)
    assert len(seen) == 1
    got = seen[-1]
    assert got.static_tol_px == pytest.approx(base.static_tol_px * 2.0)
    assert got.search_radius_px == pytest.approx(base.search_radius_px * 2.0)
    assert got.retry_radius_px == pytest.approx(base.retry_radius_px * 2.0)
    assert got.min_score == base.min_score
    assert got.drop_score == base.drop_score
    assert got.long_span_frames == base.long_span_frames
    assert got.span_confirm_max == base.span_confirm_max
    assert got.min_static_samples == base.min_static_samples

    seen.clear()
    verify_line_tracks(video, tracks, line_tracks, base, video_height=540)
    assert seen[-1].static_tol_px == pytest.approx(base.static_tol_px * 0.5)


def test_video_height_absent_or_1080_keeps_config_identical(tmp_path, monkeypatch):
    """缺省 None 与恰好 1080 都不替换配置对象(逐字节一致);非正高度
    按缺省处理(防御分支)。"""
    bar = make_text_bar()

    def draw(f):
        return paste(panning_background(f), bar, int(BOX[0]), int(BOX[1]))

    video = write_video(str(tmp_path / "identity.avi"), draw)
    tracks = make_tracks(dx_per_frame=2.0)
    line_tracks = build_line_tracks([BOX], ["固定行"], tracks, ref_frame=0)
    base = VerifyConfig()
    seen = _capture_cfgs(monkeypatch)

    for h in (None, 1080, 1080.0, 0, -3):
        verify_line_tracks(video, tracks, line_tracks, base, video_height=h)
    assert len(seen) == 5
    assert all(c is base for c in seen)

    # 同一输入下 None / 1080 / 非正的判定结果完全一致
    ref_tracks, ref_reports = verify_line_tracks(video, tracks, line_tracks, base)
    for h in (1080, 0, -3):
        got_tracks, got_reports = verify_line_tracks(
            video, tracks, line_tracks, base, video_height=h)
        assert got_reports[0].static == ref_reports[0].static
        assert got_reports[0].scores == ref_reports[0].scores
        assert {f: p.center for f, p in got_tracks[0].poses.items()} == {
            f: p.center for f, p in ref_tracks[0].poses.items()}


# ---------------------------------------------------------------------------
# max_dev_px 哨兵:区分「零偏差」与「未评估」
# ---------------------------------------------------------------------------

def test_max_dev_px_sentinel_for_unevaluated_lines(tmp_path):
    """max_dev_px 未评估时为 -1.0(哨兵),评估时 ≥ 0 且等于实测最大偏离。"""
    bar = make_text_bar()

    def draw(f):
        return paste(panning_background(f), bar, int(BOX[0]), int(BOX[1]))

    video = write_video(str(tmp_path / "dev.avi"), draw)
    tracks = make_tracks(dx_per_frame=2.0)
    line_tracks = build_line_tracks([BOX], ["固定行"], tracks, ref_frame=0)

    _tracks, reports = verify_line_tracks(video, tracks, line_tracks, VCFG)
    rep = reports[0]
    assert rep.static                              # 静止判定分支确实执行
    assert 0.0 <= rep.max_dev_px <= VCFG.static_tol_px

    # 有删除跨度 → 静止判定不执行(未评估)→ 哨兵
    gone_from = 12

    def draw_gone(f):
        frame = panning_background(f)
        if f < gone_from:
            paste(frame, bar, int(BOX[0]), int(BOX[1]))
        return frame

    video2 = write_video(str(tmp_path / "dev_gone.avi"), draw_gone)
    _tracks2, reports2 = verify_line_tracks(video2, tracks, line_tracks, VCFG)
    rep2 = reports2[0]
    assert any(s.dropped for s in rep2.spans)      # 删除跨度存在
    assert not rep2.static
    assert rep2.max_dev_px == -1.0                 # 未评估 = 哨兵,而非 0.0

    # 帧数不足未建 plan 的行(无报告)不涉及;构造一份裸报告核对默认值
    from core.pose_verify import LineVerifyReport
    assert LineVerifyReport().max_dev_px == -1.0


# ---------------------------------------------------------------------------
# 分块解码等价性(纯内存优化,不改判定)
# ---------------------------------------------------------------------------

def test_chunked_decoding_equivalent_to_single_batch(tmp_path):
    """分块解码不改判定:整批(block_max_frames=64,两行同块)与小块
    (block_max_frames=4,每行 16 采样强制单行一块)结果逐项一致。"""
    bar = make_text_bar()

    def draw(f):
        frame = panning_background(f)
        paste(frame, bar, int(BOX[0]), int(BOX[1]))
        paste(frame, bar, int(BOX[0]), 140)
        return frame

    video = write_video(str(tmp_path / "chunk.avi"), draw)
    boxes = [BOX, (60.0, 140.0, 180.0, 160.0)]
    tracks = make_tracks(dx_per_frame=2.0)
    line_tracks = build_line_tracks(boxes, ["行甲", "行乙"], tracks, ref_frame=0)

    full_tracks, full_reports = verify_line_tracks(
        video, tracks, line_tracks, VerifyConfig())
    chunk_tracks, chunk_reports = verify_line_tracks(
        video, tracks, line_tracks, VerifyConfig(block_max_frames=4))

    assert set(full_reports) == set(chunk_reports) == {0, 1}
    for li in (0, 1):
        a, b = full_reports[li], chunk_reports[li]
        assert a.static == b.static and a.corrected == b.corrected
        assert a.scores == b.scores            # 同样的采样与匹配
        assert a.n_good == b.n_good
        assert a.borderline == b.borderline
        assert a.spans == b.spans              # 同样的判定
        pa, pb = full_tracks[li].poses, chunk_tracks[li].poses
        assert set(pa) == set(pb)
        assert all(pa[f].center == pb[f].center for f in pa)
    assert full_reports[0].static and full_reports[1].static


# ---------------------------------------------------------------------------
# 两遍扫描:顺序游标 / 有界预取 / 有效块帧数归一 / 阶段 B 只吃确认帧
# ---------------------------------------------------------------------------

class _CountingCapture:
    """cv2.VideoCapture 代理:统计顺序 ``read()`` 的帧数总量。

    「块数 × 视频长度」的退化正好体现为读取帧数暴涨,所以这是解码代价的
    直接度量(独立于返回了多少帧)。"""

    def __init__(self, real, counter):
        self._real = real
        self._counter = counter

    def read(self):
        ok, frame = self._real.read()
        self._counter["reads"] += 1
        return ok, frame

    def isOpened(self):  # noqa: N802 (cv2 接口名)
        return self._real.isOpened()

    def release(self):
        return self._real.release()

    def __getattr__(self, name):
        return getattr(self._real, name)


def _count_video_reads(monkeypatch) -> dict:
    """把 cv2.VideoCapture 换成计数代理,返回 {"reads": 0} 计数器。"""
    counter = {"reads": 0}
    real_capture = cv2.VideoCapture

    def fake_capture(*args, **kwargs):
        return _CountingCapture(real_capture(*args, **kwargs), counter)

    monkeypatch.setattr(cv2, "VideoCapture", fake_capture)
    return counter


def make_staggered_rows(n_rows=6, span=60, step=6):
    """时间上错开的行(模拟聊天面板逐条浮现),返回 [(box, bar), ...]。

    第 i 行只在帧 ``[i*step, i*step + span]`` 可见,窗口互相重叠(后一行
    的最早采样远早于前一行的最晚采样)——正是「块按时间分块后时间窗仍然
    重叠」的真实形态。图案逐行错开,避免跨行伪匹配。
    """
    base = make_text_bar()
    out = []
    for i in range(n_rows):
        y0 = 12.0 + 36.0 * i
        bar = np.roll(base, 7 * i, axis=1)
        h, w = bar.shape[:2]
        out.append(((60.0, y0, 60.0 + float(w), y0 + float(h)), bar))
    return out


def make_staggered_tracks(rows, span=60, step=6):
    """与 make_staggered_rows 对应的行轨迹(每行只在可见窗口内有位姿)。"""
    lines = []
    for i, (box, _bar) in enumerate(rows):
        lo, hi = i * step, i * step + span
        center = ((box[0] + box[2]) / 2.0, (box[1] + box[3]) / 2.0)
        lines.append(LineTrack(
            text=f"行{i}", ref_box=box, height=box[3] - box[1],
            poses={f: LinePose(center=center, angle_deg=0.0, scale=1.0)
                   for f in range(lo, hi + 1)}))
    return lines


def test_effective_block_frames_scaled_by_video_height():
    """有效块帧数按 (1080/height)² 归一:1080p/缺省逐字节不变、高分辨率
    等比例变小、低分辨率不超过配置值、≤0 的「不分块」语义不归一。"""
    cases = [
        (None, 64, 64), (1080, 64, 64), (1080.0, 64, 64),   # 系数 1.0 → 原值
        (2160, 64, 16), (1440, 64, 36), (2160, 16, 8),       # 4K/1440p 变小
        (2160, 8, 8), (2160, 4, 4),                          # 不超配置值(下限退让)
        (540, 64, 64), (720, 64, 64),                        # 低分辨率不超过配置
        (2160, 0, 0), (None, 0, 0), (2160, -5, -5),          # ≤0 不分块,不归一
    ]
    for height, limit, expected in cases:
        assert pose_verify._effective_block_frames(limit, height) == expected, (
            height, limit)


def test_effective_block_frames_reach_chunking_and_cursor(tmp_path, monkeypatch):
    """归一是 verify_line_tracks 内部的有效块大小:分块上限与游标预取窗口
    都按归一后的帧数取,<=0 时保持「不分块 + 无预取」。"""
    bar = make_text_bar()

    def draw(f):
        return paste(panning_background(f), bar, int(BOX[0]), int(BOX[1]))

    video = write_video(str(tmp_path / "effblock.avi"), draw)
    tracks = make_tracks(dx_per_frame=0.0)
    line_tracks = build_line_tracks([BOX], ["固定行"], tracks, ref_frame=0)

    limits: list = []
    real_chunk = pose_verify._chunk_plans

    def spy_chunk(plans, limit):
        limits.append(limit)
        return real_chunk(plans, limit)

    prefetch: list = []
    real_cursor = pose_verify._FrameCursor

    class SpyCursor(real_cursor):
        def __init__(self, video_path, prefetch_limit=0):
            prefetch.append(prefetch_limit)
            super().__init__(video_path, prefetch_limit)

    monkeypatch.setattr(pose_verify, "_chunk_plans", spy_chunk)
    monkeypatch.setattr(pose_verify, "_FrameCursor", SpyCursor)

    for height, expected in ((None, 64), (1080, 64), (2160, 16), (1440, 36)):
        limits.clear()
        prefetch.clear()
        verify_line_tracks(video, tracks, line_tracks, VerifyConfig(),
                           video_height=height)
        assert limits == [expected], (height, limits)
        # 游标预取窗口 = max(2 × 有效块帧数, 下限 32 帧)(第 2 遍的确认帧
        # 游标按升序小块解码,不需要预取)
        want_prefetch = max(32, 2 * expected)
        assert prefetch[0] == want_prefetch, (height, prefetch)

    limits.clear()
    prefetch.clear()
    verify_line_tracks(video, tracks, line_tracks,
                       VerifyConfig(block_max_frames=0), video_height=2160)
    assert limits == [0]           # 不分块语义不归一
    assert prefetch == [0]         # 不分块 → 无需预取


def test_shared_cursor_reads_each_frame_once_across_blocks(tmp_path, monkeypatch):
    """多块场景的顺序读取帧数总量有上界:块按时间顺序共用单调游标 + 有界
    预取后 ≈ 一遍视频长度(+ 确认遍),而不是「块数 × 视频长度」。

    同一场景关掉预取(等价于「无保留的单调游标」)后读取量必然退化,说明
    该上界正是共享游标 + 预取换来的。"""
    n_frames = 100
    span, step = 60, 6
    rows = make_staggered_rows(n_rows=6, span=span, step=step)
    line_tracks = make_staggered_tracks(rows, span=span, step=step)

    def draw(f):
        frame = panning_background(f)
        for i, (box, bar) in enumerate(rows):
            if i * step <= f <= i * step + span:
                paste(frame, bar, int(box[0]), int(box[1]))
        return frame

    video = write_video(str(tmp_path / "stagger.avi"), draw, n_frames=n_frames)
    tracks = make_tracks(n=n_frames)
    cfg = VerifyConfig(block_max_frames=16)

    counter = _count_video_reads(monkeypatch)
    block_sizes: list = []
    real_match = pose_verify._match_line_samples

    def spy_match(li, lt, samples, ref_ok, frames_img, cfg_, restrict=None):
        block_sizes.append(len(frames_img))
        return real_match(li, lt, samples, ref_ok, frames_img, cfg_)

    monkeypatch.setattr(pose_verify, "_match_line_samples", spy_match)

    new_tracks, reports = verify_line_tracks(video, tracks, line_tracks, cfg)

    reads = counter["reads"]
    assert len(block_sizes) == len(line_tracks) >= 4   # 确实多块(每行一块)
    assert max(block_sizes) <= 16                      # 单块驻留 ≤ 有效块帧数
    assert reads <= n_frames + 40, reads               # ≈ 一遍视频长度
    assert reads <= 2 * n_frames + 32                  # 用户要求的宽松上界
    assert set(reports) == set(range(len(line_tracks)))

    # 关掉预取窗口(系数与下限都归零 = 无保留的单调游标):相邻块时间窗
    # 重叠 → 每块回退重读前缀,读取量超出该上界
    monkeypatch.setattr(pose_verify, "_PREFETCH_BLOCK_FACTOR", 0)
    monkeypatch.setattr(pose_verify, "MIN_PREFETCH_FRAMES", 0)
    counter["reads"] = 0
    verify_line_tracks(video, tracks, line_tracks, cfg)
    assert counter["reads"] > 2 * n_frames, counter["reads"]

    # 预取只影响解码路径,不影响判定:两次结果逐项一致
    _tracks2, reports2 = verify_line_tracks(video, tracks, line_tracks, cfg)
    for li in reports:
        assert reports2[li].scores == reports[li].scores
        assert reports2[li].spans == reports[li].spans
        assert reports2[li].dropped_frames == reports[li].dropped_frames


def test_frame_cursor_monotonic_advance_and_restart_fallback(tmp_path, monkeypatch):
    """游标只前进(单调批次不重读前缀);非单调请求确定性回退:重开捕获
    从头顺序读——取到的帧与独立解码逐字节一致,不丢帧、不抛异常;
    读不到的帧照旧缺席。"""
    def draw(f):
        return np.full((FRAME_H, FRAME_W, 3), (10 * f) % 256, np.uint8)

    video = write_video(str(tmp_path / "cursor.avi"), draw)
    counter = _count_video_reads(monkeypatch)

    with pose_verify._FrameCursor(video) as cur:
        got = cur.read([4, 9])
        assert set(got) == {4, 9}
        assert counter["reads"] == 10              # 顺序读到批尾(0..9)

        got2 = cur.read([12, 15])
        assert set(got2) == {12, 15}
        assert counter["reads"] == 16              # 只前进:前缀不重读

        got3 = cur.read([3, 11])                   # 帧号 < 游标位置 → 非单调
        assert set(got3) == {3, 11}                # 不丢帧
        assert counter["reads"] == 16 + 12         # 重开从头顺序读到 11

        # 回退后游标继续前进:后续帧照常读到
        got4 = cur.read([20])
        assert set(got4) == {20}
        assert counter["reads"] == 16 + 12 + 9     # 12..20

        # 超出片尾的帧:缺席而非异常(既有缺帧降级语义)
        assert cur.read([N_FRAMES + 100]) == {}

    # 与独立解码逐字节一致(同一帧不论从预取区/重读里取,内容相同)
    ref = pose_verify._decode_frames(video, sorted(set(got) | set(got3)))
    for f, img in list(got.items()) + list(got3.items()):
        assert np.array_equal(img, ref[f]), f

    with pytest.raises(RuntimeError):
        pose_verify._decode_frames(str(tmp_path / "missing.avi"), [0])


def test_stage_b_consumes_only_confirm_frames(tmp_path, monkeypatch):
    """阶段 B 只消费确认帧字典:给它补上该行全部采样帧不改变任何结果
    (采样帧的分数/偏移早已存进 _LineWork),证明第二遍不需要采样帧图像。"""
    bar = make_text_bar()
    occluded = (9, 11)          # 两个相邻采样帧短暂遮挡,中间帧 10 可见

    def draw(f):
        if f in occluded:
            return np.full((FRAME_H, FRAME_W, 3), 60, np.uint8)
        return paste(panning_background(f), bar, int(BOX[0]), int(BOX[1]))

    video = write_video(str(tmp_path / "stageb.avi"), draw)
    tracks = make_tracks(dx_per_frame=0.0)
    line_tracks = build_line_tracks([BOX], ["闪烁行"], tracks, ref_frame=0)
    cfg = VerifyConfig(long_span_frames=1, span_confirm_max=3)

    seen: list = []
    real_finalize = pose_verify._finalize_line

    def spy_finalize(work, frames_img, cfg_, log, restrict=None):
        seen.append((work, sorted(frames_img)))
        return real_finalize(work, frames_img, cfg_, log)

    monkeypatch.setattr(pose_verify, "_finalize_line", spy_finalize)
    new_tracks, reports = verify_line_tracks(video, tracks, line_tracks, cfg)

    assert len(seen) == 1
    work, given = seen[0]
    picks = {f for p in work.confirm_picks.values() for f in p}
    assert picks, work.confirm_picks
    assert set(given) <= picks                 # 只有确认帧,没有采样帧
    assert not picks & set(work.samples)       # 该场景的确认帧确实不是采样帧
    assert reports[0].spans and not reports[0].spans[0].dropped
    assert reports[0].spans[0].reason == "long_span_kept_visible"

    # 把该行采样帧全部塞进阶段 B 的字典 → 结果逐项不变(阶段 B 不看它们)
    def enriched_finalize(work_, frames_img, cfg_, log, restrict=None):
        extra = pose_verify._decode_frames(
            video, sorted(set(work_.samples) | {work_.ref_ok}))
        merged = dict(extra)
        merged.update(frames_img)
        assert len(merged) > len(frames_img)
        return real_finalize(work_, merged, cfg_, log)

    monkeypatch.setattr(pose_verify, "_finalize_line", enriched_finalize)
    tracks2, reports2 = verify_line_tracks(video, tracks, line_tracks, cfg)

    assert reports2[0] == reports[0]
    pa = new_tracks[0].poses
    pb = tracks2[0].poses
    assert set(pa) == set(pb)
    assert all(pa[f].center == pb[f].center for f in pa)



# ---------------------------------------------------------------------------
# ROI 包含门控(校正不得把行移出所属 ROI;4K 固定歌词条实测缺陷的修复)
#
# 4K NCOP 实测:verify 的模板匹配把固定歌词条校正到条带外 115–215px 并把
# 可见段误删(大半径重试锁到条带外相似纹理)。修复:调用方传入用户手绘
# ROI(屏幕坐标四边形),匹配的实测中心被限制在 ROI(按最小边 10%、至少
# 4px 外扩)内——全局最优峰在 ROI 外时改取 ROI 内次优峰,ROI 内无峰则按
# 失配证据处理(走既有的分级删除/保留判定)。
#
# 合成场景:参考帧在真值位置放文字条 A(模板来源),此后真值位置换成
# A 的轻微模糊版(互相关 ~0.9,仍 ≥ min_score),同时 ROI 之外放一份
# **完全相同**的 A(互相关恰 1.0,必然胜过真值)——不钳制时校正必然
# 追随 ROI 外的伪峰(复现 4K 形态),钳制后回到真值。
# ---------------------------------------------------------------------------

FRAME2_W, FRAME2_H = 400, 240
BOX2 = (60.0, 80.0, 180.0, 100.0)       # 真值行框,中心 (120, 90)
TRUE_CENTER2 = (120.0, 90.0)
DISTRACTOR_X = 180                       # 伪峰文字条粘贴 x(中心 240,ROI 外)
GHOST_CENTER2 = (240.0, 90.0)
# 手绘 ROI:紧贴行框(右缘 184);默认外扩 = max(4, 0.1×min(128,28)) = 4px
ROI_QUAD = [[56, 76], [184, 76], [184, 104], [56, 104]]


def _bg2(f: int) -> np.ndarray:
    frame = np.full((FRAME2_H, FRAME2_W, 3), 60, np.uint8)
    offset = (3 * f) % 24
    for x in range(-24 + offset, FRAME2_W + 24, 24):
        cv2.rectangle(frame, (x, 0), (x + 10, FRAME2_H - 1), (130, 130, 130), -1)
    return frame


def _write_video2(path, draw) -> str:
    writer = cv2.VideoWriter(path, cv2.VideoWriter_fourcc(*"FFV1"), FPS,
                             (FRAME2_W, FRAME2_H))
    assert writer.isOpened()
    for f in range(N_FRAMES):
        writer.write(draw(f))
    writer.release()
    return path


def _make_tracks2(n: int = N_FRAMES) -> list:
    """恒等单应的逐帧 ok 轨迹(预测位姿恒在真值中心)。"""
    out = []
    for f in range(n):
        h_mat = [[1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0]]
        quad = np.array([[56, 76], [184, 76], [184, 104], [56, 104]],
                        np.float64)
        out.append(TrackedQuad(
            frame_num=f, time_sec=f / FPS, status="ok",
            quad=[[list(map(float, p))] for p in quad],
            homography=h_mat, homography_inv=h_mat))
    return out


def _ghost_stage(bar_a, bar_b):
    """f=0 真值=A(模板来源),f≥1 真值=A 的模糊版,ROI 外恒有完全相同的 A。"""
    def draw(f):
        frame = _bg2(f)
        paste(frame, bar_a if f == 0 else bar_b,
              int(BOX2[0]), int(BOX2[1]))
        paste(frame, bar_a, DISTRACTOR_X, int(BOX2[1]))
        return frame
    return draw


# 常规搜索半径 130:伪峰(中心距预测 120px)在**首轮**匹配即可达
# ——真实 4K 形态里伪峰正是被常规/重试大半径窗口捞到的,不是只在重试里。
ROI_VCFG = VerifyConfig(search_radius_px=130.0, retry_radius_px=260.0)


def test_roi_clamp_rejects_out_of_roi_ghost_peak(tmp_path):
    """全局最优峰在 ROI 外:钳制后改取 ROI 内真值峰,不追随伪峰。"""
    bar_a = make_text_bar()
    bar_b = cv2.GaussianBlur(bar_a, (3, 3), 0)
    video = _write_video2(str(tmp_path / "clamp.avi"), _ghost_stage(bar_a, bar_b))
    tracks = _make_tracks2()
    line_tracks = build_line_tracks([BOX2], ["歌词条"], tracks, ref_frame=0)

    new_tracks, reports = verify_line_tracks(
        video, tracks, line_tracks, ROI_VCFG, roi_quad=ROI_QUAD)

    rep = reports[0]
    assert rep.static, rep
    assert rep.n_clamped >= 1          # 至少一次改取 ROI 内次优峰
    assert rep.n_roi_fallback == 0     # ROI 内有可采纳峰:不走回退分支
    last = max(new_tracks[0].poses)
    cx, cy = new_tracks[0].poses[last].center
    assert abs(cx - TRUE_CENTER2[0]) < 3.0 and abs(cy - TRUE_CENTER2[1]) < 3.0


def test_no_roi_quad_follows_ghost_peak(tmp_path):
    """不传 ROI(或关掉钳制)时复现 4K 形态:校正追随 ROI 外伪峰。"""
    bar_a = make_text_bar()
    bar_b = cv2.GaussianBlur(bar_a, (3, 3), 0)
    video = _write_video2(str(tmp_path / "ghost.avi"), _ghost_stage(bar_a, bar_b))
    tracks = _make_tracks2()

    # 不传 roi_quad:旧行为,校正中心落在 ROI 外伪峰上
    line_tracks = build_line_tracks([BOX2], ["歌词条"], tracks, ref_frame=0)
    new_tracks, reports = verify_line_tracks(
        video, tracks, line_tracks, ROI_VCFG)
    last = max(new_tracks[0].poses)
    cx, cy = new_tracks[0].poses[last].center
    assert abs(cx - GHOST_CENTER2[0]) < 3.0, (cx, cy)
    assert reports[0].n_clamped == 0

    # 传 roi_quad 但 cfg.roi_clamp=False:同旧行为
    line_tracks = build_line_tracks([BOX2], ["歌词条"], tracks, ref_frame=0)
    cfg_off = VerifyConfig(search_radius_px=130.0, retry_radius_px=260.0,
                           roi_clamp=False)
    new_tracks, reports = verify_line_tracks(
        video, tracks, line_tracks, cfg_off, roi_quad=ROI_QUAD)
    last = max(new_tracks[0].poses)
    cx, cy = new_tracks[0].poses[last].center
    assert abs(cx - GHOST_CENTER2[0]) < 3.0, (cx, cy)


def test_roi_clamp_margin_accepts_slightly_outside(tmp_path):
    """真值中心在 ROI 外但在外扩余量内:采样照常采纳,不被误判失配。"""
    bar = make_text_bar()
    # 行框右移使中心 x=183:恰在 ROI 右缘 180 外 3px(< 余量 4px)
    box = (123.0, 80.0, 243.0, 100.0)

    def draw(f):
        frame = _bg2(f)
        return paste(frame, bar, 123, int(BOX2[1]))

    video = _write_video2(str(tmp_path / "margin.avi"), draw)
    tracks = _make_tracks2()
    line_tracks = build_line_tracks([box], ["边缘行"], tracks, ref_frame=0)

    new_tracks, reports = verify_line_tracks(
        video, tracks, line_tracks, ROI_VCFG, roi_quad=ROI_QUAD)

    rep = reports[0]
    assert rep.n_good >= 3, rep
    assert rep.static, rep
    last = max(new_tracks[0].poses)
    cx, cy = new_tracks[0].poses[last].center
    assert abs(cx - 183.0) < 3.0 and abs(cy - 90.0) < 3.0


def test_roi_clamp_falls_back_when_no_inroi_peak(tmp_path):
    """真值在 ROI+余量之外且 ROI 内无替代峰:回退全局峰(旧行为)。

    DMG 邮件屏实测场景:文字滚出手绘 ROI 但**仍在画面上可见**——钳制
    不得把它判成失配(否则遮罩中途消失、原字裸露),必须照常跟随。
    """
    bar = make_text_bar()
    # 中心 x=196:超出 ROI 右缘 180 + 余量 4 = 184
    box = (136.0, 80.0, 256.0, 100.0)

    def draw(f):
        frame = _bg2(f)
        return paste(frame, bar, 136, int(BOX2[1]))

    video = _write_video2(str(tmp_path / "outside.avi"), draw)
    tracks = _make_tracks2()
    line_tracks = build_line_tracks([box], ["界外行"], tracks, ref_frame=0)

    new_tracks, reports = verify_line_tracks(
        video, tracks, line_tracks, ROI_VCFG, roi_quad=ROI_QUAD)

    rep = reports[0]
    # 全部采样照常采纳(ROI 内只有背景噪声 < min_score → 回退全局峰)
    assert rep.n_good == rep.n_samples, rep
    assert rep.n_clamped == 0, rep
    # 滚出 ROI 仍可见的形态:每个采样都走了「回退且全局峰在 ROI 外」,
    # n_roi_fallback 如实计数(该计数同时是「消失段假锁」残留形态的观测位)
    assert rep.n_roi_fallback == rep.n_samples, rep
    assert rep.static, rep
    last = max(new_tracks[0].poses)
    cx, cy = new_tracks[0].poses[last].center
    assert abs(cx - 196.0) < 3.0 and abs(cy - 90.0) < 3.0


def test_roi_clamp_noop_when_global_peak_inroi(tmp_path):
    """全局峰始终在 ROI 内:钳制是**逐位无操作**。

    选峰被掩膜改写时才存在钳制语义;全局峰可采纳且在 ROI 内时,钳制路径
    的选峰、亚像素细化都必须与不钳制完全一致(细化用真实分数面 res,不
    得让掩膜边界的 -1 邻居进抛物线把细化拉向 ROI 内侧)。
    """
    bar = make_text_bar()

    def draw(f):
        frame = _bg2(f)
        return paste(frame, bar, int(BOX2[0]), int(BOX2[1]))

    video = _write_video2(str(tmp_path / "noop.avi"), draw)
    tracks = _make_tracks2()

    line_tracks = build_line_tracks([BOX2], ["歌词条"], tracks, ref_frame=0)
    plain_tracks, plain_reports = verify_line_tracks(
        video, tracks, line_tracks, ROI_VCFG)

    line_tracks = build_line_tracks([BOX2], ["歌词条"], tracks, ref_frame=0)
    clamped_tracks, clamped_reports = verify_line_tracks(
        video, tracks, line_tracks, ROI_VCFG, roi_quad=ROI_QUAD)

    rep = clamped_reports[0]
    assert rep.n_clamped == 0 and rep.n_roi_fallback == 0, rep
    assert plain_reports[0].static == rep.static
    for f, pose in clamped_tracks[0].poses.items():
        assert plain_tracks[0].poses[f].center == pose.center, f


def test_quad_mask_helper_and_normalization():
    """半平面掩膜与四边形归一:乱序/顺时针输入归一后判定一致;退化报错。"""
    from core.pose_verify import _normalize_roi_quad, _quad_inside_mask

    quad = _normalize_roi_quad(
        [[184, 104], [56, 104], [56, 76], [184, 76]])  # 逆时针乱序
    # 网格语义:xs × ys 的所有组合 → (|ys|, |xs|) 布尔阵
    inside = _quad_inside_mask(
        np.array([120.0, 250.0]), np.array([90.0]), quad, 4.0)
    assert inside.tolist() == [[True, False]]          # (250,90) 在右缘外
    # 外扩 40px 后 (190,90) 应被接纳(距右缘 184 差 6px)
    inside2 = _quad_inside_mask(
        np.array([120.0, 190.0]), np.array([90.0]), quad, 40.0)
    assert inside2.tolist() == [[True, True]]

    with pytest.raises(ValueError):
        _normalize_roi_quad([[0, 0], [1, 1], [2, 2]])          # 点数不足
    with pytest.raises(ValueError):
        _normalize_roi_quad([[0, 0], [10, 0], [10, 10], [np.nan, 5]])


def test_verify_tolerates_malformed_roi_quad(tmp_path):
    """非法 ROI(含 NaN)不抛异常:钳制禁用,结果与不传 ROI 一致。"""
    bar_a = make_text_bar()
    bar_b = cv2.GaussianBlur(bar_a, (3, 3), 0)
    video = _write_video2(str(tmp_path / "badroi.avi"), _ghost_stage(bar_a, bar_b))
    tracks = _make_tracks2()
    line_tracks = build_line_tracks([BOX2], ["歌词条"], tracks, ref_frame=0)

    new_tracks, reports = verify_line_tracks(
        video, tracks, line_tracks, ROI_VCFG,
        roi_quad=[[56, 76], [184, 76], [184, np.nan], [56, 104]])

    assert reports, "校验照常执行(仅钳制禁用)"
    last = max(new_tracks[0].poses)
    cx, cy = new_tracks[0].poses[last].center
    assert abs(cx - GHOST_CENTER2[0]) < 3.0   # 无钳制 → 追随伪峰(旧行为)


def test_motion_ass_config_has_verify_roi_clamp():
    """MotionAssConfig 透传字段存在且默认开启。"""
    assert MotionAssConfig().verify_roi_clamp is True
