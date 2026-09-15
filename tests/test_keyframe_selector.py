# tests/test_keyframe_selector.py
"""清晰关键帧选取（core/keyframe_selector.py）离线单元测试。

用合成"文字卡片"视频验证（与 test_scene_plane_tracker 同一构造模式）：

- 清晰文字帧得分高于高斯模糊帧，top-K 只选清晰帧；
- ``status="lost"`` 的帧（整帧涂黑遮挡 / 质量门限剔除）永不被选；
- k 大于 ok 帧数时全部返回（按分数降序）；
- ``min_gap_sec`` 贪心分散：两两时间差 ≥ 阈值；
- 分数并列取更早帧；无 ok 帧返回空列表；视频打不开抛 RuntimeError。

轨迹为手工构造的纯平移 TrackedQuad（单应精确已知），FFV1 无损编码保证
解码帧与写入帧逐位一致，分数完全确定。
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

from core.keyframe_selector import select_keyframes, _plane_size_from_quad
from core.scene_plane_tracker import TrackedQuad

FRAME_W, FRAME_H = 320, 240
CARD_W, CARD_H = 160, 100
CARD_X0, CARD_Y0 = 40, 70        # 卡片在关键帧平面上的位置（quad0 左上角）
FPS = 8                          # 整数帧率：time_sec = frame_num / 8 均为精确二进制小数


def make_card() -> np.ndarray:
    """浅色卡片 + 多行"文字"色块（清晰帧纹理充足，模糊后方差骤降）。"""
    card = np.full((CARD_H, CARD_W, 3), 245, np.uint8)
    rng = np.random.default_rng(7)
    for row in range(4):
        y = 8 + row * 24
        x = 10
        while x < CARD_W - 30:
            seg = int(rng.integers(20, 60))
            color = tuple(int(c) for c in rng.integers(20, 90, 3))
            cv2.rectangle(card, (x, y), (min(x + seg, CARD_W - 10), y + 12), color, -1)
            x += seg + int(rng.integers(6, 16))
    return card


def compose_frame(
    card: np.ndarray,
    tx: float = 0.0,
    *,
    blur_sigma: float = 0.0,
    black: bool = False,
) -> np.ndarray:
    """卡片平移贴进纯色背景；blur_sigma>0 施加高斯模糊；black 涂黑整帧（遮挡）。"""
    if black:
        return np.zeros((FRAME_H, FRAME_W, 3), np.uint8)
    frame = np.full((FRAME_H, FRAME_W, 3), 200, np.uint8)
    ox, oy = CARD_X0 + int(tx), CARD_Y0
    frame[oy:oy + CARD_H, ox:ox + CARD_W] = card
    if blur_sigma > 0:
        frame = cv2.GaussianBlur(frame, (0, 0), blur_sigma)
    return frame


def make_track(frame_num: int, *, ok: bool = True, tx: float = 0.0) -> TrackedQuad:
    """手工构造纯平移轨迹：单应精确已知，展开图与卡片逐像素一致。

    单应约定与 scene_plane_tracker 相同：homography = 关键帧平面 → 当前帧，
    homography_inv 为其逆。关键帧平面坐标即卡片局部坐标。
    """
    time_sec = frame_num / FPS
    if not ok:
        return TrackedQuad(
            frame_num=frame_num, time_sec=time_sec, status="lost", reproj_error=-1.0,
        )
    x0, y0 = CARD_X0 + tx, CARD_Y0
    quad = [
        [x0, y0], [x0 + CARD_W, y0],
        [x0 + CARD_W, y0 + CARD_H], [x0, y0 + CARD_H],
    ]
    h_fwd = [[1.0, 0.0, float(x0)], [0.0, 1.0, float(y0)], [0.0, 0.0, 1.0]]
    h_inv = [[1.0, 0.0, -float(x0)], [0.0, 1.0, -float(y0)], [0.0, 0.0, 1.0]]
    return TrackedQuad(
        frame_num=frame_num, time_sec=time_sec, status="ok",
        quad=quad, homography=h_fwd, homography_inv=h_inv,
        inlier_ratio=1.0, reproj_error=0.0, matches=100,
    )


def write_video(path, frames) -> str:
    """FFV1 无损写出合成序列；编解码器不可用则整组跳过。"""
    writer = cv2.VideoWriter(
        str(path), cv2.VideoWriter_fourcc(*"FFV1"), float(FPS), (FRAME_W, FRAME_H),
    )
    if not writer.isOpened():
        pytest.skip("no usable video codec (FFV1) for keyframe selector tests")
    for frame in frames:
        writer.write(frame)
    writer.release()
    return str(path)


# ---------------------------------------------------------------------------


def test_prefers_sharp_frames_over_blurred(tmp_path):
    card = make_card()
    frames, tracks = [], []
    for i in range(10):
        blurred = (i % 2 == 1)
        frames.append(compose_frame(card, tx=6 * i, blur_sigma=9.0 if blurred else 0.0))
        tracks.append(make_track(i, tx=6 * i))
    video = write_video(tmp_path / "sharp.avi", frames)

    picked = select_keyframes(video, tracks, k=3)

    # 偶数帧清晰、奇数帧模糊；清晰帧内容一致分数并列 → 取更早的三个清晰帧。
    assert picked == [0, 2, 4]
    assert all(fn % 2 == 0 for fn in picked)


def test_lost_frames_never_selected(tmp_path):
    card = make_card()
    frames = [
        compose_frame(card, tx=0),                   # f0 清晰 ok
        compose_frame(card, tx=6),                   # f1 清晰但 lost（门限剔除）
        compose_frame(card, tx=12, blur_sigma=9.0),  # f2 模糊 ok
        compose_frame(card, tx=18),                  # f3 清晰 ok
        compose_frame(card, black=True),             # f4 整帧涂黑（遮挡）lost
        compose_frame(card, tx=30, blur_sigma=9.0),  # f5 模糊 ok
    ]
    tracks = [
        make_track(0, tx=0),
        make_track(1, tx=6, ok=False),
        make_track(2, tx=12),
        make_track(3, tx=18),
        make_track(4, ok=False),
        make_track(5, tx=30),
    ]
    video = write_video(tmp_path / "lost.avi", frames)

    picked = select_keyframes(video, tracks, k=3)

    assert len(picked) == 3
    assert 1 not in picked and 4 not in picked
    assert set(picked) == {0, 2, 3}


def test_k_exceeding_ok_count_returns_all_by_score_desc(tmp_path):
    card = make_card()
    frames = [
        compose_frame(card, tx=0),                   # f0 清晰 ok
        compose_frame(card, tx=6, blur_sigma=9.0),   # f1 最模糊 ok
        compose_frame(card, tx=12, blur_sigma=3.0),  # f2 次清晰 ok
        compose_frame(card, tx=18, black=True),      # f3 lost
        compose_frame(card, tx=24, blur_sigma=6.0),  # f4 ok
        compose_frame(card, tx=30, black=True),      # f5 lost
    ]
    tracks = [
        make_track(0, tx=0), make_track(1, tx=6), make_track(2, tx=12),
        make_track(3, ok=False), make_track(4, tx=24), make_track(5, ok=False),
    ]
    video = write_video(tmp_path / "all.avi", frames)

    picked = select_keyframes(video, tracks, k=10)

    # 4 个 ok 帧全部返回，模糊度严格递增 → 分数严格降序可断言完整顺序。
    assert picked == [0, 2, 4, 1]


def test_min_gap_sec_respected(tmp_path):
    card = make_card()
    sigmas = [0.0, 1.5, 3.0, 6.0, 12.0]  # 清晰度逐帧递减
    frames = [compose_frame(card, tx=6 * i, blur_sigma=s) for i, s in enumerate(sigmas)]
    tracks = [make_track(i, tx=6 * i) for i in range(5)]
    video = write_video(tmp_path / "gap.avi", frames)

    # 无间隔约束：分数降序取前三。
    assert select_keyframes(video, tracks, k=3) == [0, 1, 2]

    # fps=8 → 相邻帧间隔 0.125s；min_gap 0.25s 贪心分散：
    # 选 f0(0.0s)；f1(0.125s) 过近跳过；f2(0.25s) 入选；f3 过近跳过；f4(0.5s) 入选。
    picked = select_keyframes(video, tracks, k=3, min_gap_sec=0.25)
    assert picked == [0, 2, 4]

    time_of = {t.frame_num: t.time_sec for t in tracks}
    times = [time_of[fn] for fn in picked]
    for a in range(len(times)):
        for b in range(a + 1, len(times)):
            assert abs(times[a] - times[b]) >= 0.25


def test_score_tie_prefers_earlier_frame(tmp_path):
    card = make_card()
    # 静止画面 + 相同单应：展开图逐位一致 → 分数完全并列。
    frames = [compose_frame(card) for _ in range(5)]
    tracks = [make_track(i) for i in range(5)]
    video = write_video(tmp_path / "tie.avi", frames)

    assert select_keyframes(video, tracks, k=2) == [0, 1]

    # 并列 + min_gap：按帧序贪心分散，取 0 / 2 / 4。
    assert select_keyframes(video, tracks, k=3, min_gap_sec=0.25) == [0, 2, 4]


def test_no_ok_frames_returns_empty(tmp_path):
    card = make_card()
    frames = [compose_frame(card, black=True) for _ in range(3)]
    tracks = [make_track(i, ok=False) for i in range(3)]
    video = write_video(tmp_path / "empty.avi", frames)

    assert select_keyframes(video, tracks, k=3) == []


def test_unopenable_video_raises_runtimeerror():
    with pytest.raises(RuntimeError):
        select_keyframes("/nonexistent/no_such_video.avi", [make_track(0)])


def test_plane_size_derivation_from_quad():
    # 轴对齐矩形：w/h 即边长。
    axis_quad = [[40.0, 70.0], [200.0, 70.0], [200.0, 170.0], [40.0, 170.0]]
    assert _plane_size_from_quad(axis_quad) == (160, 100)

    # 平行四边形：w=上下两边均值、h=左右两边均值。
    para_quad = [[0.0, 0.0], [100.0, 10.0], [110.0, 60.0], [10.0, 50.0]]
    assert _plane_size_from_quad(para_quad) == (100, 51)

    # 梯形：上下边、左右边分别取均值（w=(100+90)/2=95，h=(50+√2600)/2≈50）。
    trap_quad = [[0.0, 0.0], [100.0, 0.0], [90.0, 50.0], [0.0, 50.0]]
    assert _plane_size_from_quad(trap_quad) == (95, 50)

    # 退化小平面钳到最小 8px。
    tiny_quad = [[0.0, 0.0], [3.0, 0.0], [3.0, 2.0], [0.0, 2.0]]
    assert _plane_size_from_quad(tiny_quad) == (8, 8)
