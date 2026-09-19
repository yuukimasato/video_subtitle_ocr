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

另有退化守卫:零纹理补丁(纯色块)不参与校正(伪匹配防护)。
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
    build_line_tracks,
    synthesize_events,
)
from core.pose_verify import VerifyConfig, verify_line_tracks  # noqa: E402
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


def write_video(path, draw_frame) -> str:
    writer = cv2.VideoWriter(path, cv2.VideoWriter_fourcc(*"FFV1"), FPS,
                             (FRAME_W, FRAME_H))
    assert writer.isOpened()
    for f in range(N_FRAMES):
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
