# tests/test_scene_plane_tracker.py
"""场景平面跟踪器（core/scene_plane_tracker.py）离线单元测试。

用合成"文字卡片"场景验证：
- 匀速平移的平面四边形逐帧跟随（轨迹误差 < 4px）；
- 整体被遮挡的帧记为 lost，重新可见后自动重捕获接回轨迹；
- 无特征噪声帧不产出垃圾轨迹；
- 轨迹 JSON 往返、透视展开、退化四边形拒绝。

全部基于 numpy 帧数组，不需要视频编码器（视频包装层单独一个用例）。
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

from core.scene_plane_tracker import (
    TrackedQuad,
    load_trajectory,
    save_trajectory,
    track_plane,
    track_plane_frames,
    unwarp_plane,
    _validate_init_quad,
)

FRAME_W, FRAME_H = 640, 480
CARD_W, CARD_H = 280, 180
FRAME_CENTER = (FRAME_W / 2, FRAME_H / 2)


def make_card() -> np.ndarray:
    """浅色卡片 + 多行"文字"色块（提供充足的 ORB 角点）。"""
    card = np.full((CARD_H, CARD_W, 3), 245, np.uint8)
    rng = np.random.default_rng(7)
    for row in range(6):
        y = 16 + row * 26
        x = 14
        while x < CARD_W - 34:
            seg = int(rng.integers(24, 70))
            color = tuple(int(c) for c in rng.integers(20, 90, 3))
            cv2.rectangle(card, (x, y), (min(x + seg, CARD_W - 14), y + 12), color, -1)
            x += seg + int(rng.integers(6, 18))
    return card


def motion_mat(tx: float, ty: float = 0.0, angle_deg: float = 0.0, scale: float = 1.0) -> np.ndarray:
    """卡片 → 画面的 3x3 变换：平移到画面中心后再施加运动。"""
    t = np.deg2rad(angle_deg)
    rot = np.array([
        [np.cos(t), -np.sin(t), 0.0],
        [np.sin(t), np.cos(t), 0.0],
        [0.0, 0.0, 1.0],
    ])
    scale_m = np.diag([scale, scale, 1.0])
    to_center = np.array([
        [1.0, 0.0, FRAME_CENTER[0] - CARD_W / 2 + tx],
        [0.0, 1.0, FRAME_CENTER[1] - CARD_H / 2 + ty],
        [0.0, 0.0, 1.0],
    ])
    to_origin = np.array([[1.0, 0.0, -CARD_W / 2.0],
                          [0.0, 1.0, -CARD_H / 2.0],
                          [0.0, 0.0, 1.0]])
    return to_center @ rot @ scale_m @ to_origin


def compose_scene(card: np.ndarray, m: np.ndarray) -> Tuple[np.ndarray, np.ndarray]:
    """把卡片按 m 贴进纯色背景，返回 (场景帧, 卡片四角的真实四边形)。"""
    frame = np.full((FRAME_H, FRAME_W, 3), 200, np.uint8)
    cv2.warpPerspective(
        card, m, (FRAME_W, FRAME_H), dst=frame,
        flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_TRANSPARENT,
    )
    corners = np.array([
        [0, 0], [CARD_W - 1, 0], [CARD_W - 1, CARD_H - 1], [0, CARD_H - 1]
    ], dtype=np.float32)
    gt = cv2.perspectiveTransform(corners.reshape(-1, 1, 2), m.astype(np.float32)).reshape(-1, 2)
    return frame, gt


def quad_error(track_quad, gt: np.ndarray) -> float:
    return float(np.mean(np.hypot(
        np.array(track_quad)[:, 0] - gt[:, 0],
        np.array(track_quad)[:, 1] - gt[:, 1],
    )))


def run_translation(card, n=30, step=3.0):
    frames, gts = [], []
    for i in range(n):
        frame, gt = compose_scene(card, motion_mat(tx=step * i))
        frames.append(frame)
        gts.append(gt)
    return frames, gts


# ---------------------------------------------------------------------------

def test_tracks_translation_trajectory():
    card = make_card()
    frames, gts = run_translation(card)
    tracks = track_plane_frames(frames, gts[0].tolist(), start_frame_num=100)

    assert len(tracks) == len(frames)
    assert all(t.status == "ok" for t in tracks)
    assert [t.frame_num for t in tracks] == list(range(100, 100 + len(frames)))
    for track, gt in zip(tracks, gts):
        assert quad_error(track.quad, gt) < 4.0
        # 累计单应必须与四边形自洽：用单应变换关键帧四角 ≈ 当前 quad。
        h_mat = np.array(track.homography)
        warped = cv2.perspectiveTransform(gts[0].reshape(-1, 1, 2).astype(np.float32), h_mat).reshape(-1, 2)
        assert float(np.mean(np.hypot(warped[:, 0] - gt[:, 0], warped[:, 1] - gt[:, 1]))) < 4.0


def test_handles_scale_and_rotation():
    card = make_card()
    frames, gts = [], []
    for i in range(20):
        frame, gt = compose_scene(card, motion_mat(tx=2.0 * i, angle_deg=0.4 * i, scale=1.0 + 0.006 * i))
        frames.append(frame)
        gts.append(gt)
    tracks = track_plane_frames(frames, gts[0].tolist())

    assert all(t.status == "ok" for t in tracks)
    for track, gt in zip(tracks, gts):
        assert quad_error(track.quad, gt) < 5.0


def test_full_occlusion_marks_lost_then_reacquires():
    card = make_card()
    frames, gts = [], []
    for i in range(30):
        m = motion_mat(tx=3.0 * i)
        if 10 <= i < 15:
            frames.append(np.full((FRAME_H, FRAME_W, 3), 200, np.uint8))
            gts.append(None)
        else:
            frame, gt = compose_scene(card, m)
            frames.append(frame)
            gts.append(gt)

    tracks = track_plane_frames(frames, gts[0].tolist())

    # 遮挡段：lost 且不产出四边形（不外推）。
    for i in range(10, 15):
        assert tracks[i].status == "lost"
        assert tracks[i].quad is None
    # 遮挡前正常。
    assert all(tracks[i].status == "ok" for i in range(10))
    # 文字重新可见后自动接回，且轨迹贴合真实位置。
    recovered = [i for i in range(15, 30) if tracks[i].status == "ok"]
    assert len(recovered) >= 10
    for i in recovered:
        assert quad_error(tracks[i].quad, gts[i]) < 5.0
    # 接回后的累计单应仍相对关键帧（逆变换可用于透视展开回统一坐标）。
    last_ok = tracks[recovered[-1]]
    assert last_ok.homography_inv is not None


def test_noise_frames_never_produce_track():
    card = make_card()
    frames, gts = run_translation(card, n=8)
    rng = np.random.default_rng(3)
    for _ in range(5):
        frames.append(rng.integers(0, 255, (FRAME_H, FRAME_W, 3), dtype=np.uint8))
        gts.append(None)

    tracks = track_plane_frames(frames, gts[0].tolist())

    assert all(tracks[i].status == "ok" for i in range(8))
    for i in range(8, len(frames)):
        assert tracks[i].status == "lost"
        assert tracks[i].quad is None


def test_trajectory_json_roundtrip(tmp_path):
    card = make_card()
    frames, gts = run_translation(card, n=6)
    tracks = track_plane_frames(frames, gts[0].tolist())

    path = str(tmp_path / "trajectory.json")
    save_trajectory(path, tracks, video_path="demo.mp4", init_quad=gts[0].tolist())
    loaded = load_trajectory(path)

    assert loaded["video"] == "demo.mp4"
    assert len(loaded["frames"]) == 6
    first = loaded["frames"][0]
    assert first["status"] == "ok"
    assert np.allclose(first["quad"], gts[0], atol=1e-6)
    assert len(first["homography"]) == 3 and len(first["homography"][0]) == 3
    assert len(first["homography_inv"]) == 3
    assert np.allclose(np.matmul(first["homography"], first["homography_inv"]),
                       np.eye(3), atol=1e-6)


def test_unwarp_plane_restorts_card_view():
    card = make_card()
    m = motion_mat(tx=40.0, ty=25.0)
    frame, gt = compose_scene(card, m)

    unwarped = unwarp_plane(frame, gt, (CARD_W, CARD_H))

    # 平移无旋转：展开结果应几乎逐像素还原卡片（边缘插值误差放宽）。
    diff = cv2.absdiff(unwarped, card).mean()
    assert diff < 3.0


def test_degenerate_and_flat_quads_rejected():
    with pytest.raises(ValueError):
        _validate_init_quad([[0, 0], [10, 0], [20, 0], [0, 0]])  # 共线
    card = make_card()
    blank = np.full((FRAME_H, FRAME_W, 3), 200, np.uint8)
    blank[380:430, 40:90] = 205  # 特征贫乏区域
    with pytest.raises(ValueError):
        track_plane_frames([blank, blank], [[40, 380], [90, 380], [90, 430], [40, 430]])


def test_video_wrapper_tracks_same_trajectory(tmp_path):
    writer = cv2.VideoWriter(
        str(tmp_path / "move.avi"), cv2.VideoWriter_fourcc(*"FFV1"), 10.0,
        (FRAME_W, FRAME_H),
    )
    if not writer.isOpened():
        pytest.skip("no usable video codec for wrapper test")
    card = make_card()
    frames, gts = run_translation(card, n=10)
    for frame in frames:
        writer.write(frame)
    writer.release()

    tracks = track_plane(str(tmp_path / "move.avi"), gts[0].tolist(), fps=10.0)

    assert len(tracks) == 10
    assert all(t.status == "ok" for t in tracks)
    for track, gt in zip(tracks, gts):
        assert quad_error(track.quad, gt) < 4.0
    assert tracks[0].frame_num == 0
