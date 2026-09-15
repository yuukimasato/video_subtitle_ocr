# tests/test_screen_luma.py
"""屏幕亮度曲线测量(core/screen_luma.py)离线单元测试。

用合成灰度视频验证(FFV1 无损编码,解码帧与写入帧逐位一致,真值确定):

- 逐 ok 帧取 quad 外接矩形(裁到画面内)灰度中位值 → ratio 曲线,按帧号升序;
- lost 帧跳过;无 ok 帧返回空列表;视频打不开抛 RuntimeError;
- baseline = 各帧中位值的 baseline_percentile 分位(默认 90);
- ratio = luma / baseline 截到 (0, 1];全程全黑(基线 ≤ 0)→ 全 1.0;
- 中位值对 quad 内局部高光不敏感(均值会被拉高,中位不会);
- quad 超出画面时裁剪到画面内再测量;轨迹帧号可以不从 0 开始。

亮度序列仿真实手机屏幕调暗场景(基线 247 → 174 → 30 → 回亮)。
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

from core.screen_luma import (  # noqa: E402
    measure_luma_curve,
    measure_luma_curve_with_baseline,
)
from core.scene_plane_tracker import TrackedQuad  # noqa: E402

FRAME_W, FRAME_H = 320, 240
QUAD = [[40.0, 70.0], [200.0, 70.0], [200.0, 170.0], [40.0, 170.0]]
FPS = 8  # 整数帧率:time_sec = frame_num / 8 均为精确二进制小数

# 仿真实数据(基线 247 = 90 分位):8 帧恒亮 → 174 → 3 帧暗 30 → 过渡 100
# → 174 → 回亮 247;16 帧的 90 分位恰为 247。
LEVELS = [247] * 8 + [174] + [30] * 3 + [100] + [174] + [247] * 2


# ---------------------------------------------------------------------------
# 合成视频与轨迹构造
# ---------------------------------------------------------------------------

def solid_frame(level: int, *, overlay=None) -> np.ndarray:
    """整帧常量灰度;overlay=(x0, y0, x1, y1, level) 时叠加局部色块。"""
    frame = np.full((FRAME_H, FRAME_W, 3), int(level), np.uint8)
    if overlay is not None:
        x0, y0, x1, y1, lv = overlay
        frame[y0:y1, x0:x1] = int(lv)
    return frame


def two_tone_frame(left_level: int, right_level: int, *, split_x: int = 200) -> np.ndarray:
    """左右两块灰度的画面(验证 quad 裁剪:测量区只落在其中一块)。"""
    frame = np.full((FRAME_H, FRAME_W, 3), int(right_level), np.uint8)
    frame[:, :split_x] = int(left_level)
    return frame


def make_track(frame_num: int, *, ok: bool = True, quad=None) -> TrackedQuad:
    """手工构造轨迹项:quad 固定(纯静止平面),单应精确已知;lost 帧 quad 缺省。"""
    time_sec = frame_num / FPS
    if not ok:
        return TrackedQuad(
            frame_num=frame_num, time_sec=time_sec, status="lost", reproj_error=-1.0,
        )
    q = quad if quad is not None else QUAD
    h_fwd = [[1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0]]
    return TrackedQuad(
        frame_num=frame_num, time_sec=time_sec, status="ok",
        quad=[list(p) for p in q], homography=h_fwd,
        homography_inv=h_fwd, inlier_ratio=1.0, reproj_error=0.0, matches=100,
    )


def write_video(path, frames) -> str:
    """FFV1 无损写出合成序列;编解码器不可用则整组跳过。"""
    writer = cv2.VideoWriter(
        str(path), cv2.VideoWriter_fourcc(*"FFV1"), float(FPS), (FRAME_W, FRAME_H),
    )
    if not writer.isOpened():
        pytest.skip("no usable video codec (FFV1) for screen luma tests")
    for frame in frames:
        writer.write(frame)
    writer.release()
    return str(path)


def write_levels(path, levels) -> str:
    return write_video(path, [solid_frame(lv) for lv in levels])


# ---------------------------------------------------------------------------
# 曲线测量
# ---------------------------------------------------------------------------

def test_curve_levels_ordered_by_frame_num(tmp_path):
    video = write_levels(tmp_path / "dim.avi", LEVELS)
    tracks = [make_track(i) for i in range(len(LEVELS))]

    curve, baseline = measure_luma_curve_with_baseline(video, tracks)

    assert baseline == pytest.approx(247.0)
    assert [t for t, _ in curve] == [i / FPS for i in range(len(LEVELS))]
    for (_t, ratio), lvl in zip(curve, LEVELS):
        assert ratio == pytest.approx(lvl / 247.0)
        assert 0.0 < ratio <= 1.0

    # 便捷封装只返回曲线本体
    assert measure_luma_curve(video, tracks) == curve


def test_lost_frames_skipped(tmp_path):
    lost = {3, 10}
    video = write_levels(tmp_path / "lost.avi", LEVELS)
    tracks = [make_track(i, ok=(i not in lost)) for i in range(len(LEVELS))]

    curve = measure_luma_curve(video, tracks)

    assert [t for t, _ in curve] == [
        i / FPS for i in range(len(LEVELS)) if i not in lost]
    assert [_r for _t, _r in curve] == [
        LEVELS[i] / 247.0 for i in range(len(LEVELS)) if i not in lost]


def test_no_ok_frames_returns_empty(tmp_path):
    video = write_levels(tmp_path / "none.avi", [30] * 4)
    tracks = [make_track(i, ok=False) for i in range(4)]

    assert measure_luma_curve_with_baseline(video, tracks) == ([], 0.0)
    assert measure_luma_curve(video, tracks) == []


def test_unopenable_video_raises_runtimeerror():
    with pytest.raises(RuntimeError):
        measure_luma_curve("/nonexistent/no_such_video.avi", [make_track(0)])


def test_all_black_baseline_degenerates_to_ones(tmp_path):
    video = write_levels(tmp_path / "black.avi", [0, 0, 0, 0])
    tracks = [make_track(i) for i in range(4)]

    curve, baseline = measure_luma_curve_with_baseline(video, tracks)

    assert baseline == pytest.approx(0.0)
    assert curve == [(i / FPS, 1.0) for i in range(4)]


def test_ratio_clipped_to_one(tmp_path):
    # 18 帧 200 + 2 帧 255:90 分位 = 200 + 0.1*(255-200) = 205.5,
    # 255 帧的比值 1.24 被截到 1.0。
    levels = [200] * 18 + [255] * 2
    video = write_levels(tmp_path / "clip.avi", levels)
    tracks = [make_track(i) for i in range(len(levels))]

    curve = measure_luma_curve(video, tracks)

    assert all(r <= 1.0 for _t, r in curve)
    assert curve[-1][1] == pytest.approx(1.0)
    assert curve[-2][1] == pytest.approx(1.0)
    for _t, r in curve[:-2]:
        assert r == pytest.approx(200.0 / 205.5)


def test_median_robust_to_local_highlights(tmp_path):
    # quad(160×100=16000 px)内叠 60×40=2400 px 高光块(15% < 50%):
    # 中位值仍为暗底 30;若用均值会被拉高到 ≈62.6。
    frames = [solid_frame(247) for _ in range(9)]
    frames.append(solid_frame(30, overlay=(100, 90, 160, 130, 255)))
    video = write_video(tmp_path / "hl.avi", frames)
    tracks = [make_track(i) for i in range(10)]

    curve = measure_luma_curve(video, tracks)

    for _t, r in curve[:9]:
        assert r == pytest.approx(1.0)
    assert curve[9][1] == pytest.approx(30.0 / 247.0)


def test_quad_outside_frame_clipped(tmp_path):
    # f9:quad 右侧越界 → 裁剪后只测到画面内亮区(247);
    # f10:quad 左侧越界 → 只测到画面内暗区(30)。基线仍为 247。
    frames = [solid_frame(247) for _ in range(9)]
    frames.append(two_tone_frame(left_level=30, right_level=247))
    frames.append(two_tone_frame(left_level=30, right_level=247))
    video = write_video(tmp_path / "oob.avi", frames)
    quad_right = [[250.0, 70.0], [400.0, 70.0], [400.0, 170.0], [250.0, 170.0]]
    quad_left = [[-60.0, 70.0], [80.0, 70.0], [80.0, 170.0], [-60.0, 170.0]]
    tracks = [make_track(i) for i in range(9)]
    tracks.append(make_track(9, quad=quad_right))
    tracks.append(make_track(10, quad=quad_left))

    curve = measure_luma_curve(video, tracks)

    assert [t for t, _ in curve] == [i / FPS for i in range(11)]
    assert curve[9][1] == pytest.approx(1.0)      # 裁剪后 = 247/247
    assert curve[10][1] == pytest.approx(30.0 / 247.0)  # 裁剪后 = 30/247


def test_frame_nums_not_starting_at_zero(tmp_path):
    # 轨迹帧号 5~8(与解码序号一一对应),之前的帧不测。
    levels = [30, 30, 30, 30, 30, 100, 140, 247, 247]
    video = write_levels(tmp_path / "offset.avi", levels)
    tracks = [make_track(i) for i in range(5, 9)]

    curve, baseline = measure_luma_curve_with_baseline(video, tracks)

    assert baseline == pytest.approx(247.0)
    assert curve == [
        (5 / FPS, pytest.approx(100.0 / 247.0)),
        (6 / FPS, pytest.approx(140.0 / 247.0)),
        (7 / FPS, 1.0),
        (8 / FPS, 1.0),
    ]
