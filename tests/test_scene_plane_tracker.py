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

import logging
import os
import sys

import cv2
import numpy as np
import pytest
from dataclasses import asdict
from typing import Tuple

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from core.scene_plane_tracker import (  # noqa: E402
    DEFAULT_MIN_MATCHES,
    DEFAULT_MIN_INLIER_RATIO,
    load_trajectory,
    save_trajectory,
    scan_content_windows,
    track_plane,
    track_plane_frames,
    unwarp_plane,
    _bbox_expand,
    _count_features_in_roi,
    _INIT_ROI_MIN_FEATURES,
    _INIT_STRONG_FEATURES,
    _validate_init_quad,
    ROI_PAD_PX,
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


def test_blank_lead_in_reanchors_on_first_textured_frame():
    # 首帧恰好无纹理(聊天界面文字逐条浮现前的空白屏、淡入首帧):不再直接
    # 报"特征不足",而是向后锚定在第一个特征足够的帧上;之前的帧记 lost
    # (该段无文字可回贴,不产出事件),后续轨迹与常规跟踪等价。
    card = make_card()
    frames = [np.full((FRAME_H, FRAME_W, 3), 200, np.uint8) for _ in range(3)]
    led_frames, gts = [], []
    for i in range(10):
        frame, gt = compose_scene(card, motion_mat(tx=3.0 * i))
        led_frames.append(frame)
        gts.append(gt)
    frames.extend(led_frames)
    tracks = track_plane_frames(frames, gts[0].tolist(), start_frame_num=0)

    assert len(tracks) == len(frames)
    assert [t.status for t in tracks] == ["lost"] * 3 + ["ok"] * 10
    assert tracks[0].quad is None
    assert tracks[3].frame_num == 3
    for track, gt in zip(tracks[3:], gts):
        assert quad_error(track.quad, gt) < 4.0


def _make_card_rows(nrows, x_limit, seed=7):
    """与 make_card 同款的「文字」色块卡,但只画前 ``nrows`` 行、行宽到 ``x_limit``。

    打字机式浮现的首帧:纹理少(特征数落在 min_matches..strong_need 之间),
    其后内容长全的帧才是「特征丰富」候选。
    """
    card = np.full((CARD_H, CARD_W, 3), 245, np.uint8)
    rng = np.random.default_rng(seed)
    for row in range(nrows):
        y = 16 + row * 26
        x = 14
        while x < x_limit:
            seg = int(rng.integers(24, 70))
            color = tuple(int(c) for c in rng.integers(20, 90, 3))
            cv2.rectangle(card, (x, y), (min(x + seg, CARD_W - 14), y + 12), color, -1)
            x += seg + int(rng.integers(6, 18))
    return card


def test_lead_in_plane_motion_falls_back_to_first_weak_frame(caplog):
    # 重锚定软一致性校验:引导段内平面自身在动(帧 0 只浮现了一行字、
    # 特征刚过下限;其后内容长全且平移超出 ~25% quad 对角线)时,最强
    # 候选帧按 quad0 原坐标锚定会整体错位 → 校验判「动」,退回更靠近
    # frame0 的 first_weak 候选——这里即 quad0 所在的帧 0 本身
    # (自匹配 = 单位阵,可信)。
    full_card = make_card()
    partial = _make_card_rows(1, x_limit=140)
    frame0, gt0 = compose_scene(partial, motion_mat(tx=0.0))
    frames = [frame0]
    for _ in range(5):
        frames.append(compose_scene(full_card, motion_mat(tx=85.0))[0])

    with caplog.at_level(logging.INFO, logger="core.scene_plane_tracker"):
        tracks = track_plane_frames(frames, gt0.tolist(), start_frame_num=0)

    assert any("lead-in consistency check" in r.message for r in caplog.records)
    assert len(tracks) == len(frames)
    # 锚定落在帧 0(quad0 原位),而不是平面已移动的最强帧。
    assert tracks[0].status == "ok"
    assert quad_error(tracks[0].quad, gt0) < 4.0


def _probe_cache_scene(n_weak):
    """两段式引导场景:1 行部分卡片(弱候选,实测 39 特征)重复 ``n_weak`` 帧,
    之后才是特征丰富(541 特征)的完整卡片帧——探测循环会先扫过全部弱帧,
    在首个强帧处锚定。返回值:(帧, 首帧 quad)。"""
    full = make_card()
    partial = _make_card_rows(1, x_limit=140)
    frame0, gt0 = compose_scene(partial, motion_mat(tx=0.0))
    frames = [frame0]
    for _ in range(n_weak):
        frames.append(compose_scene(partial, motion_mat(tx=0.0))[0])
    for _ in range(4):
        frames.append(compose_scene(full, motion_mat(tx=0.0))[0])
    return frames, gt0


def _run_with_probe_cache_spy(monkeypatch, frames, quad):
    """跑一遍跟踪,并统计探测期特征数组的存活峰值。

    ``_detect_features`` 每次调用前先数一遍「此前返回的特征数组还有几个
    活着」——探测阶段缓存下来的候选帧特征都还没被回收,故该峰值即缓存
    条数的上界。返回 (tracks, 存活峰值, 调用次数)。
    """
    import weakref

    import core.scene_plane_tracker as spt

    real = spt._detect_features
    refs: list = []
    peak = [0]

    def spy(gray, feature_count, mask=None):
        peak[0] = max(peak[0], sum(1 for r in refs if r() is not None))
        pts, desc = real(gray, feature_count, mask)
        refs.append(weakref.ref(pts))
        return pts, desc

    monkeypatch.setattr(spt, "_detect_features", spy)
    tracks = spt.track_plane_frames(frames, quad, start_frame_num=0)
    return tracks, peak[0], len(refs)


def test_init_probe_caches_only_used_candidate_features(monkeypatch):
    """A3:探测帧特征缓存不随探测帧数线性增长。

    初始化探测上限 900 帧,此前对**每个** ``min_matches ≤ count < strong_need``
    的探测帧都缓存特征(注释却称「通常 ≤2 个」),实际只读锚定帧与
    first_weak 两帧。这里用弱引用实测特征数组存活峰值:64 帧弱候选下
    修复前线性涨到 67,修复后恒为 3(首帧 + 锚定帧 + 上一跟踪帧)。
    同场景的探测/跟踪结果与修复前逐帧一致(状态、quad、匹配数、单应)。
    """
    short_frames, short_quad = _probe_cache_scene(8)
    long_frames, long_quad = _probe_cache_scene(64)
    short_tracks, short_peak, short_calls = _run_with_probe_cache_spy(
        monkeypatch, short_frames, short_quad.tolist())
    long_tracks, long_peak, long_calls = _run_with_probe_cache_spy(
        monkeypatch, long_frames, long_quad.tolist())

    assert len(long_frames) == len(short_frames) + 56  # 长场景多 56 帧弱候选
    assert long_calls > short_calls  # 特征检测次数确实随探测帧数增长
    # 缓存条数与探测帧数无关:64 帧弱候选的存活峰值不超过常数个小数组
    # (实测 3;修复前 ≈ 弱候选帧数 + 3,64 帧时为 67)。
    assert 2 <= long_peak <= 4, long_peak
    assert long_peak == short_peak, (short_peak, long_peak)
    # 功能等价:两段式引导仍从 frame0 起逐帧出轨迹,四边形贴住真实卡片
    for frames, quad, tracks in ((short_frames, short_quad, short_tracks),
                                 (long_frames, long_quad, long_tracks)):
        assert len(tracks) == len(frames)
        assert all(t.status == "ok" for t in tracks)
        assert quad_error(tracks[0].quad, quad) < 4.0


@pytest.mark.parametrize("h22", [0.0, float("nan")],
                         ids=["h22-zero", "h22-nan"])
def test_lead_in_degenerate_homography_keeps_candidate(monkeypatch, caplog,
                                                       h22):
    """A4:一致性校验遇退化单应(``h[2,2]`` = 0/非有限)按「证据不足」处理。

    RANSAC 内点 ≥4 不保证非退化:按 ``h[2,2]`` 归一化会除零——非零平移给
    inf(方向保守),0/0 与 NaN 矩阵进 ``np.linalg.svd`` 则直接抛 LinAlgError
    打断整条跟踪(修复前实测两种输入都抛)。现判退化 → 不判动、不拒绝该
    候选(warning 留痕),锚定落在首个强候选帧(帧 1)而非回退 first_weak。
    """
    real_h = cv2.findHomography
    state = {"n": 0}

    def fake_find_homography(src, dst, *args, **kwargs):
        state["n"] += 1
        if state["n"] == 1:  # 只替换引导段一致性校验那一次调用
            h = np.eye(3, dtype=np.float64)
            h[2, 2] = h22
            return h, np.ones((len(src), 1), np.uint8)
        return real_h(src, dst, *args, **kwargs)

    card = make_card()
    partial = _make_card_rows(1, x_limit=140)
    frame0, gt0 = compose_scene(partial, motion_mat(tx=0.0))
    frames = [frame0]
    for _ in range(5):  # 强候选帧上平面已平移 85px(远超 25% 对角线)
        frames.append(compose_scene(card, motion_mat(tx=85.0))[0])

    monkeypatch.setattr(cv2, "findHomography", fake_find_homography)
    with caplog.at_level(logging.WARNING, logger="core.scene_plane_tracker"):
        tracks = track_plane_frames(frames, gt0.tolist(), start_frame_num=0)

    assert state["n"] > 1  # 只挡住了校验那次,主循环照常用真实单应
    assert any("degenerate homography" in r.message
               and "candidate frame 1" in r.message for r in caplog.records)
    # 证据不足不拒绝候选:锚定留在帧 1(不回退 first_weak 的帧 0)
    assert [t.status for t in tracks[:2]] == ["lost", "ok"]


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
    make_card()
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


# ---------------------------------------------------------------------------
# 视频路径流式化:峰值内存与帧范围无关（不整段预载 frames）

def _write_video(path, frames, fps: float = 10.0) -> None:
    """把帧序列写成无损 FFV1 AVI（无可用编码器时跳过，与既有用例一致）。"""
    h, w = frames[0].shape[:2]
    writer = cv2.VideoWriter(
        str(path), cv2.VideoWriter_fourcc(*"FFV1"), fps, (w, h))
    if not writer.isOpened():
        pytest.skip("no usable video codec for streaming tests")
    for frame in frames:
        writer.write(frame)
    writer.release()


def _decode_video(path) -> Tuple[list, list]:
    """顺序解码出 (帧, 时间戳)，时间戳取数与 ``track_plane`` 一致。"""
    cap = cv2.VideoCapture(str(path))
    fps = float(cap.get(cv2.CAP_PROP_FPS) or 0.0) or 30.0
    frames, times = [], []
    while True:
        ok, frame = cap.read()
        if not ok or frame is None:
            break
        idx = len(frames)
        frames.append(frame)
        ms = float(cap.get(cv2.CAP_PROP_POS_MSEC))
        times.append(ms / 1000.0 if ms > 0 else idx / fps)
    cap.release()
    return frames, times


def _as_dicts(tracks):
    return [asdict(t) for t in tracks]


def _spy_read_positions(monkeypatch, positions):
    """记录每次 ``cap.read()`` 之前的解码位置、``set``/``grab`` 调用。"""
    real_read = cv2.VideoCapture.read
    real_set = cv2.VideoCapture.set
    real_grab = cv2.VideoCapture.grab

    def spy_read(cap):
        positions.append(int(cap.get(cv2.CAP_PROP_POS_FRAMES)))
        return real_read(cap)

    def spy_set(cap, prop, *args, **kwargs):
        calls["set"].append(prop)
        return real_set(cap, prop, *args, **kwargs)

    def spy_grab(cap, *args, **kwargs):
        calls["grab"].append(1)
        return real_grab(cap, *args, **kwargs)

    calls = {"set": [], "grab": []}
    monkeypatch.setattr(cv2.VideoCapture, "read", spy_read)
    monkeypatch.setattr(cv2.VideoCapture, "set", spy_set)
    monkeypatch.setattr(cv2.VideoCapture, "grab", spy_grab)
    return calls


def test_video_stream_never_retains_whole_frame_range(tmp_path, monkeypatch):
    """①:视频路径按需顺序解码——任意时刻存活的 BGR 帧数 ≤ 常数。

    旧实现把整段帧预载进 ``List[np.ndarray]``（4K 801 帧 ≈ 20GB），这里用
    弱引用实测解码帧的存活峰值：24 帧整段驻留会 ≥24，流式下只驻留当前帧
    （参考帧状态只留灰度与特征）。同时全段轨迹照常产出，证明不是「少读了
    帧」换来的低内存。
    """
    import weakref

    card = make_card()
    frames, gts = run_translation(card, n=24)
    video = tmp_path / "stream.avi"
    _write_video(video, frames)

    real_read = cv2.VideoCapture.read
    real_cvt = cv2.cvtColor
    refs: list = []
    alive: list = []

    def note() -> None:
        alive.append(sum(1 for r in refs if r() is not None))

    def spy_read(cap):
        note()  # 读下一帧之前：上一帧应已释放
        ok, frame = real_read(cap)
        if ok and frame is not None:
            refs.append(weakref.ref(frame))
        return ok, frame

    def spy_cvt(src, code, *args, **kwargs):
        note()  # 帧作为参数在手：此刻确有一帧存活
        return real_cvt(src, code, *args, **kwargs)

    monkeypatch.setattr(cv2.VideoCapture, "read", spy_read)
    monkeypatch.setattr(cv2, "cvtColor", spy_cvt)
    tracks = track_plane(str(video), gts[0].tolist(), start_frame=0,
                         end_frame=len(frames) - 1)

    assert len(refs) == len(frames)  # 24 帧全部解码
    assert len(tracks) == len(frames)
    assert all(t.status == "ok" for t in tracks)
    # 存活的解码帧数恒为常数（整段驻留时 ≥24）；峰值 ≥1 说明采样非空。
    assert 1 <= max(alive) <= 4, max(alive)


def test_video_stream_start_frame_offset(tmp_path):
    """``start_frame > 0``:来源从该帧起顺序解码（起始定位一次，不 seek）。

    ROI 驱动的轨迹管线会带非零起始帧：解码会话必须定位到 ``start_frame``，
    且帧号/时间戳/轨迹与「内存列表从同一索引起」逐字段一致。
    """
    card = make_card()
    frames, gts = run_translation(card, n=12)
    video = tmp_path / "offset.avi"
    _write_video(video, frames)
    list_frames, times = _decode_video(video)

    streamed = track_plane(str(video), gts[5].tolist(), start_frame=5,
                           end_frame=11)
    listed = track_plane_frames(list_frames[5:], gts[5].tolist(),
                                start_frame_num=5, times=times[5:])

    assert [t.frame_num for t in streamed] == list(range(5, 12))
    assert all(t.status == "ok" for t in streamed)
    for track, gt in zip(streamed, gts[5:]):
        assert quad_error(track.quad, gt) < 4.0
    assert _as_dicts(streamed) == _as_dicts(listed)


def test_video_stream_matches_in_memory_trajectory(tmp_path):
    """②:同一视频流式入口与内存列表入口给出**相同轨迹**（含空白引导段）。"""
    card = make_card()
    frames = [np.full((FRAME_H, FRAME_W, 3), 200, np.uint8) for _ in range(3)]
    gts = []
    for i in range(12):
        frame, gt = compose_scene(card, motion_mat(tx=3.0 * i))
        frames.append(frame)
        gts.append(gt)
    video = tmp_path / "lead_in.avi"
    _write_video(video, frames)

    streamed = track_plane(str(video), gts[0].tolist(), start_frame=0,
                           end_frame=len(frames) - 1)
    list_frames, times = _decode_video(video)
    listed = track_plane_frames(list_frames, gts[0].tolist(),
                                start_frame_num=0, times=times)

    assert [t.status for t in streamed] == ["lost"] * 3 + ["ok"] * 12
    assert _as_dicts(streamed) == _as_dicts(listed)


def test_video_stream_only_moves_forward_without_seek(tmp_path, monkeypatch):
    """③:视频来源只顺序 ``read``：不 ``set``、不 ``grab``，读取位置严格 +1。"""
    card = make_card()
    frames, gts = run_translation(card, n=12)
    video = tmp_path / "forward.avi"
    _write_video(video, frames)

    positions: list = []
    calls = _spy_read_positions(monkeypatch, positions)
    tracks = track_plane(str(video), gts[0].tolist(), start_frame=0,
                         end_frame=len(frames) - 1)

    assert len(tracks) == len(frames)
    # start_frame == 0:连起始定位都不需要（start > 0 时也只在打开后 set 一次）。
    assert calls["set"] == []
    assert calls["grab"] == []
    # 每次读到的是下一帧，没有跳帧/回头（随机 seek 会打乱或跳过位置）。
    assert positions == list(range(len(frames))), positions


def test_stream_probe_cap_and_first_weak_fallback(tmp_path, monkeypatch, caplog):
    """④:探测上限与 ``first_weak`` 回退在流式下仍生效（含回退重放）。

    弱特征场景（帧 0 空白、其后每帧特征刚过 ``min_matches``）探测扫满上限也
    找不到「特征丰富」帧 → 锚定回退到首个弱候选帧 1。此时锚定帧落在探测终点
    之前，流式来源必须回到帧 2 继续解码：这里断言它靠「重开 + 顺序重放」实现
    （位置序列 = 两段各从 0 起 +1 的连续读取，全程无 seek），并与内存列表路径
    轨迹逐字段一致。
    """
    import core.scene_plane_tracker as spt

    partial = _make_card_rows(1, x_limit=140)  # 弱特征：特征数落在 [12, 48)
    frame0 = np.full((FRAME_H, FRAME_W, 3), 200, np.uint8)
    frames = [frame0]
    gts = []
    for _ in range(9):
        frame, gt = compose_scene(partial, motion_mat(tx=0.0))
        frames.append(frame)
        gts.append(gt)
    video = tmp_path / "weak.avi"
    _write_video(video, frames)
    list_frames, times = _decode_video(video)

    # 探测上限压到 6 帧（机制与 900 同一条路径）：帧 0 空白、其后全弱，扫满
    # 上限也没有特征丰富帧 → 锚定回退到首个弱候选帧 1。
    monkeypatch.setattr(spt, "_MAX_INIT_PROBE_FRAMES", 6)
    positions: list = []
    calls = _spy_read_positions(monkeypatch, positions)
    with caplog.at_level(logging.INFO, logger="core.scene_plane_tracker"):
        streamed = track_plane(str(video), gts[0].tolist(), start_frame=0,
                               end_frame=len(frames) - 1)
    listed = track_plane_frames(list_frames, gts[0].tolist(), start_frame_num=0,
                                times=times)

    assert any("no feature-rich frame found" in r.message for r in caplog.records)
    assert [t.status for t in streamed] == ["lost"] + ["ok"] * 9
    # 探测 6 帧（位置 0..5）后回退到帧 1 → 重开重放 0..1，再从 2 读到 9。
    assert calls["set"] == [] and calls["grab"] == []
    assert positions == list(range(6)) + list(range(len(frames))), positions
    assert _as_dicts(streamed) == _as_dicts(listed)


def test_stream_handles_truncated_decode_with_fallback_replay(tmp_path):
    """来源提前耗尽(容器谎报帧数/中途解码失败)+ 回退重放仍接得上。

    探测扫到第 7 帧读不出来 → 探测截断(等价于旧的「frames 在此截断」);
    ``first_weak`` 锚定帧 0 → 来源重开重放 → 主循环接着帧 1..5 走完。
    与内存列表路径逐字段一致:不因「本次会话已读到末帧」而丢掉锚定帧之后的
    整段轨迹。
    """
    partial = _make_card_rows(1, x_limit=140)  # 弱特征,供 first_weak 回退
    frames = [compose_scene(partial, motion_mat(tx=0.0))[0] for _ in range(6)]
    quad = compose_scene(partial, motion_mat(tx=0.0))[1].tolist()
    video = tmp_path / "trunc.avi"
    _write_video(video, frames)
    list_frames, times = _decode_video(video)

    orig_get = cv2.VideoCapture.get

    def fake_get(cap, prop, *args, **kwargs):
        if prop == cv2.CAP_PROP_FRAME_COUNT:
            return 500  # 容器谎报 500 帧,实际只有 6 帧可解
        return orig_get(cap, prop, *args, **kwargs)

    cv2.VideoCapture.get = fake_get
    try:
        streamed = track_plane(str(video), quad, start_frame=0)
    finally:
        cv2.VideoCapture.get = orig_get

    listed = track_plane_frames(list_frames, quad, start_frame_num=0,
                                times=times)
    assert len(streamed) == len(frames)  # 截断处收尾,不吃掉锚定帧之后的轨迹
    assert all(t.status == "ok" for t in streamed)
    assert _as_dicts(streamed) == _as_dicts(listed)


def test_stream_probe_stops_at_real_cap(tmp_path, monkeypatch):
    """④(续):探测上限 900 帧在流式（视频）路径下仍生效，不越界多扫。

    902 帧视频（0.6 倍缩放省算力，特征数不受影响）：帧 0 弱特征、其后空屏，
    探测扫满 900 帧也找不到强帧，回退锚定帧 0（首帧即弱候选）。带 mask 的
    ``_detect_features`` 调用数即探测帧数（帧 0 + 探测 1..899），恰好 900；
    上限失效会一路扫到第 902 帧。
    """
    import core.scene_plane_tracker as spt

    scale = 0.6
    frame0, gt0 = compose_scene(_make_card_rows(1, x_limit=140),
                                motion_mat(tx=0.0))
    frame0 = cv2.resize(frame0, (int(FRAME_W * scale), int(FRAME_H * scale)),
                        interpolation=cv2.INTER_AREA)
    quad = (gt0 * scale).tolist()
    # 场景自检：锚定帧特征数必须落在 [min_matches, strong_need)——够锚定（否则
    # 直接 ValueError），又不至于被判成「特征丰富」而让探测提前收工。
    anchor_matches = track_plane_frames([frame0], quad)[0].matches
    assert DEFAULT_MIN_MATCHES <= anchor_matches < _INIT_STRONG_FEATURES, \
        anchor_matches

    blank = np.full(frame0.shape, 200, np.uint8)
    n_frames = spt._MAX_INIT_PROBE_FRAMES + 2
    video = tmp_path / "cap.avi"
    _write_video(video, [frame0] + [blank] * (n_frames - 1))

    real = spt._detect_features
    probe_calls = [0]

    def spy(gray, feature_count, mask=None):
        if mask is not None:
            probe_calls[0] += 1
        return real(gray, feature_count, mask)

    monkeypatch.setattr(spt, "_detect_features", spy)
    tracks = track_plane(str(video), quad, start_frame=0,
                         end_frame=n_frames - 1)

    assert spt._MAX_INIT_PROBE_FRAMES == 900
    assert probe_calls[0] == spt._MAX_INIT_PROBE_FRAMES, probe_calls[0]
    assert len(tracks) == n_frames
    # 无强帧 → 回退锚定帧 0；其后空屏无特征，逐帧 lost 不外推。
    assert tracks[0].status == "ok"
    assert all(t.status == "lost" and t.quad is None for t in tracks[1:])


# ---------------------------------------------------------------------------
# 锚定判据:region0 之外再看 quad 内部（_INIT_ROI_MIN_FEATURES）

def _gray(frame: np.ndarray) -> np.ndarray:
    return cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)


def _blank_roi_frame(quad, seed: int) -> np.ndarray:
    """quad 内部空白、外圈（region0 内）撒高对比噪声的帧。

    复现 12.mp4 帧 34 / 4K 帧 156 的实测几何:白屏 JPEG 噪声让 region0 计数过
    ``strong_need``（实测 61 / 50），而 quad 内部几乎没有角点（实测 0）。噪声
    超出 quad 外扩 ``ROI_PAD_PX`` 的范围,故不影响新的内部判据。
    """
    frame = np.full((FRAME_H, FRAME_W, 3), 200, np.uint8)
    rng = np.random.default_rng(seed)
    noise = rng.integers(0, 255, (FRAME_H, FRAME_W, 3), dtype=np.uint8)
    x1f, y1f, x2f, y2f = _bbox_expand(np.asarray(quad, dtype=np.float64),
                                      ROI_PAD_PX)
    x1 = max(0, int(np.floor(x1f)))
    y1 = max(0, int(np.floor(y1f)))
    x2 = min(FRAME_W, int(np.ceil(x2f)))
    y2 = min(FRAME_H, int(np.ceil(y2f)))
    outside = np.ones((FRAME_H, FRAME_W), bool)
    outside[y1:y2, x1:x2] = False
    frame[outside] = noise[outside]
    return frame


def _static_card_scene(n: int = 6):
    """静止卡片场景:帧 0 的 quad 内部就特征丰富 → 直接锚定帧 0,不进探测。

    返回 (帧列表, 首帧真实四边形)；丢帧判据的用例都从这份场景上逐条逼出来。
    """
    card = make_card()
    frame, gt = compose_scene(card, motion_mat(tx=0.0))
    return [frame.copy() for _ in range(n)], gt


def test_rich_roi_at_frame0_anchors_without_probe(monkeypatch):
    """①:帧 0 两条件都过（11.mp4/[DMG]-like 实测帧 0 的 quad 内部 2000 特征）。

    直接锚定帧 0、连探测都不进:以 ROI 计数函数的调用次数为探针（只有帧 0 那
    一次），且整条轨迹与「新判据关掉（阈值 0 = 旧规则）」逐字段一致——锚定帧
    存下来的特征集必须不变。
    """
    import core.scene_plane_tracker as spt

    frames, gt = _static_card_scene()
    quad = gt.tolist()
    assert _count_features_in_roi(_gray(frames[0]), np.asarray(quad), 2000) \
        >= _INIT_ROI_MIN_FEATURES  # 场景自检:帧 0 的 quad 内部确实丰富

    real = spt._count_features_in_roi
    calls = []

    def spy(gray, quad_arg, feature_count):
        calls.append(1)
        return real(gray, quad_arg, feature_count)

    monkeypatch.setattr(spt, "_count_features_in_roi", spy)
    tracks = track_plane_frames(frames, quad, start_frame_num=0)

    assert calls == [1]  # 只判了帧 0:没有进探测循环
    assert all(t.status == "ok" for t in tracks)
    assert tracks[0].frame_num == 0
    # 锚定帧 0 → 引导段循环体不执行:没有任何 pre_anchor 条目。
    assert all(t.lost_reason is None for t in tracks)

    monkeypatch.setattr(spt, "_INIT_ROI_MIN_FEATURES", 0)  # 旧规则
    legacy = track_plane_frames(frames, quad, start_frame_num=0)
    assert _as_dicts(tracks) == _as_dicts(legacy)


def test_blank_roi_lead_in_anchors_on_first_rich_frame(monkeypatch):
    """②:region0 够特征而 quad 内部空白的引导段 → 锚点后移到内容丰富帧。

    4 帧「白屏噪声」（旧规则会在帧 0 锚定）+ 5 帧真实卡片:锚点必须落在帧 4,
    帧 0-3 记 lost 且不产出四边形;每帧轨迹贴住真实卡片。对照:把新判据关掉
    （阈值 0）就是今天的错误行为——锚在空白帧上,真正的文字再出现时反而丢失。
    """
    import core.scene_plane_tracker as spt

    card = make_card()
    _, gt = compose_scene(card, motion_mat(tx=0.0))
    quad = gt.tolist()
    blanks = [_blank_roi_frame(quad, seed=s) for s in range(4)]
    rich = [compose_scene(card, motion_mat(tx=0.0))[0] for _ in range(5)]
    frames = blanks + rich

    tracks = track_plane_frames(frames, quad, start_frame_num=0)

    assert [t.status for t in tracks] == ["lost"] * 4 + ["ok"] * 5
    assert tracks[4].frame_num == 4
    assert all(t.quad is None for t in tracks[:4])
    # 引导段（锚定帧之前）记 "pre_anchor":跟踪尚未开始,不归因到质量门限。
    assert [t.lost_reason for t in tracks[:4]] == ["pre_anchor"] * 4
    assert tracks[4].lost_reason is None
    for track in tracks[4:]:
        assert quad_error(track.quad, gt) < 4.0

    monkeypatch.setattr(spt, "_INIT_ROI_MIN_FEATURES", 0)  # 旧规则
    legacy = track_plane_frames(frames, quad, start_frame_num=0)
    # 旧规则锚在空白帧 0 上(ok),此后噪声帧内容与它不匹配 → 全程丢失:
    # 真正的文字从未被跟踪。
    assert legacy[0].status == "ok"
    assert all(t.status == "lost" for t in legacy[1:])


def test_no_rich_roi_falls_back_to_legacy_anchor(monkeypatch):
    """③:探测上限内没有帧满足 quad 内部判据 → 回退旧规则的锚定帧。

    3 帧空屏（region0 也不够）+ 4 帧白屏噪声（旧规则在此锚定、quad 内部空）:
    新判据永远找不到候选 (b) → 锚定回退到候选 (a) = 帧 3,与「阈值 0 的旧规则」
    逐字段一致（不削弱断言:回退结果必须完全相同）。
    """
    import core.scene_plane_tracker as spt

    card = make_card()
    _, gt = compose_scene(card, motion_mat(tx=0.0))
    quad = gt.tolist()
    blank = np.full((FRAME_H, FRAME_W, 3), 200, np.uint8)
    frames = [blank.copy() for _ in range(3)] \
        + [_blank_roi_frame(quad, seed=s) for s in range(4)]

    tracks = track_plane_frames(frames, quad, start_frame_num=0)
    assert [t.status for t in tracks] == ["lost"] * 3 + ["ok"] + ["lost"] * 3
    assert tracks[3].frame_num == 3

    monkeypatch.setattr(spt, "_INIT_ROI_MIN_FEATURES", 0)  # 旧规则
    legacy = track_plane_frames(frames, quad, start_frame_num=0)
    assert _as_dicts(tracks) == _as_dicts(legacy)


def test_roi_count_only_computed_for_legacy_passing_frames(monkeypatch):
    """④:ROI 判据只对「已过旧 region0 规则」的帧计算（成本与探测帧数无关）。

    场景 = 2 帧空屏（region0 不够 → 不该算）+ 1 帧白屏噪声（region0 够 → 算
    一次,quad 内部不够）+ 1 帧真卡片（region0 够 → 算一次,命中）。ROI 计数
    恰好 2 次，而 region0 检测（带掩码的 _detect_features）覆盖全部探测帧。
    """
    import core.scene_plane_tracker as spt

    card = make_card()
    _, gt = compose_scene(card, motion_mat(tx=0.0))
    quad = gt.tolist()
    blank = np.full((FRAME_H, FRAME_W, 3), 200, np.uint8)
    frames = [blank.copy() for _ in range(2)] + [
        _blank_roi_frame(quad, seed=0),
        compose_scene(card, motion_mat(tx=0.0))[0],
    ]

    roi_calls = []
    masked_calls = []
    real_roi = spt._count_features_in_roi
    real_detect = spt._detect_features

    def roi_spy(gray, quad_arg, feature_count):
        roi_calls.append(1)
        return real_roi(gray, quad_arg, feature_count)

    def detect_spy(gray, feature_count, mask=None):
        if mask is not None:
            masked_calls.append(1)
        return real_detect(gray, feature_count, mask)

    monkeypatch.setattr(spt, "_count_features_in_roi", roi_spy)
    monkeypatch.setattr(spt, "_detect_features", detect_spy)
    tracks = track_plane_frames(frames, quad, start_frame_num=0)

    assert len(masked_calls) >= 4  # 帧 0 + 探测帧 1..3 都做了 region0 检测
    assert len(roi_calls) == 2, roi_calls  # 只有 region0 够的帧 2、3
    assert [t.status for t in tracks] == ["lost"] * 3 + ["ok"]


# ---------------------------------------------------------------------------
# 丢帧原因（TrackedQuad.lost_reason）

def _patch_first_homography(monkeypatch, h_mat, n_inliers=None):
    """把主循环里**第一次** ``cv2.findHomography`` 换成固定返回 ``(h_mat, 掩码)``。

    锚定帧（帧 0，见 ``_static_card_scene``）不估单应，故第一次调用即帧 1:只挡
    这一帧，其余帧照常用真实单应——返回的 ``state`` 里 ``n`` 即调用次数。
    ``h_mat=None`` 表示直接返回 ``(None, None)``（退化分支）。
    """
    real = cv2.findHomography
    state = {"n": 0}

    def fake(src, dst, *args, **kwargs):
        state["n"] += 1
        if state["n"] == 1:
            if h_mat is None:
                return None, None
            n = len(src) if n_inliers is None else min(n_inliers, len(src))
            mask = np.zeros((len(src), 1), np.uint8)
            mask[:n] = 1
            return np.array(h_mat, np.float64), mask
        return real(src, dst, *args, **kwargs)

    monkeypatch.setattr(cv2, "findHomography", fake)
    return state


def test_lost_reason_few_matches():
    """④a:few_matches——匹配对不足 min_matches（空白帧与参考帧无配对）。"""
    frames, gt = _static_card_scene()
    frames[3] = np.full((FRAME_H, FRAME_W, 3), 200, np.uint8)

    tracks = track_plane_frames(frames, gt.tolist(), start_frame_num=0)

    assert [t.status for t in tracks] == ["ok"] * 3 + ["lost"] + ["ok"] * 2
    assert tracks[3].lost_reason == "few_matches"
    assert tracks[3].matches == 0
    assert tracks[3].inlier_ratio == 0.0
    assert tracks[3].reproj_error == -1.0  # 未估重投影（inf）→ 轨迹存 -1.0


def test_lost_reason_degenerate_homography(monkeypatch):
    """④b:degenerate_h——findHomography 判退化返回 None（内点不足/共线）。

    匹配数本身够（> min_matches，与 few_matches 区分开），卡在单应估计上。
    """
    frames, gt = _static_card_scene()
    state = _patch_first_homography(monkeypatch, None)
    tracks = track_plane_frames(frames, gt.tolist(), start_frame_num=0)

    assert state["n"] > 1  # 只挡了帧 1,后续帧仍走真实单应
    assert tracks[0].status == "ok" and tracks[0].lost_reason is None
    assert tracks[1].lost_reason == "degenerate_h"
    assert tracks[1].inlier_ratio == 0.0  # 未拿到掩码 → 内点率保持初值
    assert tracks[1].reproj_error == -1.0
    assert tracks[1].matches >= DEFAULT_MIN_MATCHES
    assert tracks[2].status == "ok"  # ref 未更新,帧 2 贴上帧 0 的卡片


@pytest.mark.parametrize(
    "case,n_inliers,expect_ratio,expect_reproj",
    [
        ("low_inlier", 1, None, -1.0),
        ("non_finite", None, 1.0, -1.0),
        ("non_convex", None, 1.0, None),
        ("high_reproj", None, 1.0, 5.0),
    ],
)
def test_lost_reason_homography_branches(monkeypatch, case, n_inliers,
                                         expect_ratio, expect_reproj):
    """④c-④f:单应相关四条判据的原因（用例 id 即期望的 lost_reason）:

    - ``low_inlier``:内点率 < min_inlier_ratio（掩码只留 1 个内点）——且与旧
      实现一样**不估重投影**（reproj 留 inf → 存 -1.0）;
    - ``non_finite``:单应含 NaN → 变换出的四边形非有限;
    - ``non_convex``:单应近乎奇异（1e-8·I）→ 四边形退化成一点（非凸）;
    - ``high_reproj``:单应整体偏 5px → 重投影 RMSE 超 max_reproj_error。
    """
    h_mat = np.eye(3, dtype=np.float64)
    if case == "non_finite":
        h_mat[0, 2] = np.nan
    elif case == "non_convex":
        h_mat = h_mat * 1e-8
    elif case == "high_reproj":
        h_mat[0, 2] = 5.0

    frames, gt = _static_card_scene()
    state = _patch_first_homography(monkeypatch, h_mat, n_inliers=n_inliers)
    with np.errstate(invalid="ignore"):  # NaN 单应:重投影/面积比较告警无意义
        tracks = track_plane_frames(frames, gt.tolist(), start_frame_num=0)

    assert state["n"] > 1  # 只挡了帧 1,后续帧仍用真实单应
    assert tracks[0].status == "ok" and tracks[0].lost_reason is None
    assert tracks[1].lost_reason == case
    if expect_ratio is None:  # low_inlier:1 个内点 / 全部匹配
        assert DEFAULT_MIN_INLIER_RATIO > tracks[1].inlier_ratio > 0.0
    else:
        assert tracks[1].inlier_ratio == expect_ratio
    if expect_reproj is None:  # non_convex:重投影被估过但四边形判非凸
        assert tracks[1].reproj_error > 0.0
    else:
        assert tracks[1].reproj_error == pytest.approx(expect_reproj, abs=1e-3)
    # 其余帧不受影响:帧 2 起重新贴上静止卡片。
    assert all(t.status == "ok" for t in tracks[2:])


def test_nan_reprojection_is_lost_not_ok(monkeypatch):
    """NaN 重投影判 lost（与旧的一次性 ``and`` 表达式一致）。

    判据写成 ``not (reproj <= 阈)`` 而非 ``reproj > 阈``:两者对有限值等价,
    但 NaN 时 ``NaN > 阈`` 为假,会让该帧漏过后四条判据、被记成 ok 且
    ``reproj_error`` 写进轨迹 JSON（裸 NaN）;旧的 ``and`` 表达式在 NaN 下恒判
    lost。真实 OpenCV 路径下未复现出 NaN 重投影（``perspectiveTransform`` 对
    退化分母返回 0 而非 NaN）,此处直接构造以保证该口径不回退。
    """
    frames, gt = _static_card_scene()
    import core.scene_plane_tracker as spt
    monkeypatch.setattr(spt, "_reproj_rmse", lambda *a, **k: float("nan"))

    tracks = track_plane_frames(frames, gt.tolist(), start_frame_num=0)

    assert tracks[0].status == "ok"
    assert tracks[1].status == "lost"
    assert tracks[1].lost_reason == "high_reproj"


def test_lost_reason_area_jump_on_scale_change():
    """④g:area_jump——一帧内卡片缩到 0.6 倍（面积比 ~0.36，超 max_area_jump）。"""
    card = make_card()
    base, gts = run_translation(card, n=6)
    quad = gts[0].tolist()
    base[3] = compose_scene(card, motion_mat(tx=0.0, scale=0.6))[0]

    tracks = track_plane_frames(base, quad, start_frame_num=0)

    assert [t.status for t in tracks] == ["ok"] * 3 + ["lost"] + ["ok"] * 2
    assert tracks[3].lost_reason == "area_jump"
    # 单应本身估得住（重投影未超限）:面积比是唯一没过的判据。
    assert tracks[3].reproj_error <= 4.0


def test_lost_reason_vertex_jump_on_large_translation():
    """④h:vertex_jump——一帧内卡片平移 250px（超 0.8×边长上限），单应估得住。"""
    card = make_card()
    base, gts = run_translation(card, n=6)
    quad = gts[0].tolist()
    base[3] = compose_scene(card, motion_mat(tx=250.0))[0]

    tracks = track_plane_frames(base, quad, start_frame_num=0)

    assert [t.status for t in tracks] == ["ok"] * 3 + ["lost"] + ["ok"] * 2
    assert tracks[3].lost_reason == "vertex_jump"
    assert tracks[3].reproj_error <= 4.0


def test_ok_frames_have_no_lost_reason():
    """⑤:ok 帧恒 lost_reason=None;主循环里的 lost 帧必带原因且取自词汇表。

    遮挡场景（帧 8-11 全屏遮挡）同时含 ok 与 lost:锚定帧 0 之后没有引导段,
    故 lost 帧全部来自主循环,原因一律非 None。
    """
    reasons = {"few_matches", "degenerate_h", "low_inlier", "non_finite",
               "non_convex", "high_reproj", "area_jump", "vertex_jump",
               "pre_anchor"}  # pre_anchor 只出现在锚定帧之前（本场景锚定帧 0）
    card = make_card()
    frames, gts = [], []
    for i in range(24):
        if 8 <= i < 12:
            frames.append(np.full((FRAME_H, FRAME_W, 3), 200, np.uint8))
            gts.append(None)
        else:
            frame, gt = compose_scene(card, motion_mat(tx=3.0 * i))
            frames.append(frame)
            gts.append(gt)

    tracks = track_plane_frames(frames, gts[0].tolist())

    assert tracks[0].status == "ok"  # 无引导段:全部帧都在主循环内
    assert any(t.status == "lost" for t in tracks)
    assert any(t.status == "ok" for t in tracks)
    for track in tracks:
        assert (track.status == "ok") == (track.lost_reason is None), track
        if track.status == "lost":
            assert track.lost_reason in reasons


def test_trajectory_json_omits_none_lost_reason(tmp_path):
    """⑥:``save_trajectory`` 只在 lost_reason 非 None 时写该键。

    帧 0 锚定的轨迹（11.mp4/[DMG]-like:无引导段、全 ok）JSON 与加入该字段前
    **逐字节一致**（旧序列化 = ``frames=[asdict(t) ...]`` 去掉新键）——这同时
    钉住「锚定帧 0 时引导段循环体不执行」:多出一个 pre_anchor 条目就会让字节
    对比失败。含丢失的轨迹才带键,键位仍在最后一个字段（asdict 顺序）,
    ``load_trajectory`` 对缺键帧照常读到 None。
    """
    import json

    card = make_card()
    frames, gts = run_translation(card, n=6)
    tracks = track_plane_frames(frames, gts[0].tolist(), start_frame_num=0)

    assert tracks[0].frame_num == 0 and all(t.status == "ok" for t in tracks)
    assert all(t.lost_reason is None for t in tracks)  # 引导段循环体未执行

    # 全 ok 轨迹:不含该键,与旧写法（frames=[asdict(t) ...]）逐字节一致。
    ok_path = tmp_path / "ok.json"
    save_trajectory(str(ok_path), tracks, video_path="demo.mp4",
                    init_quad=gts[0].tolist())
    text = ok_path.read_text(encoding="utf-8")
    assert "lost_reason" not in text
    legacy_text = json.dumps(
        {"video": "demo.mp4", "init_quad": gts[0].tolist(), "meta": {},
         "frames": [{k: v for k, v in asdict(t).items() if k != "lost_reason"}
                    for t in tracks]},
        ensure_ascii=False, indent=2)
    assert text == legacy_text

    # 含丢失帧:带键、值为原因,且键在最后（字段顺序 = dataclass 顺序）。
    frames[3] = np.full((FRAME_H, FRAME_W, 3), 200, np.uint8)
    tracks = track_plane_frames(frames, gts[0].tolist(), start_frame_num=0)
    assert [t.status for t in tracks] == ["ok"] * 3 + ["lost"] + ["ok"] * 2
    lost_path = tmp_path / "lost.json"
    save_trajectory(str(lost_path), tracks, video_path="demo.mp4",
                    init_quad=gts[0].tolist())
    loaded = load_trajectory(str(lost_path))["frames"]
    assert "lost_reason" not in loaded[0]
    assert loaded[3]["lost_reason"] == "few_matches"
    assert loaded[3]["reproj_error"] == -1.0
    assert list(loaded[3].keys())[-1] == "lost_reason"


def test_pre_anchor_lead_in_frames_in_json(tmp_path):
    """⑥(续):锚定帧之前的引导段条目带 lost_reason="pre_anchor"、且写进 JSON。

    观测目标:引导段此前与 ok 帧一样在 JSON 里缺键,下游直方图只能把整段报成
    「原因未知」（12.mp4 帧 0-200 实测 123 帧 lost 却 reasons=none）;现在它
    作为普通词汇字符串出现,可被直方图直接统计,而 ok 帧仍不写该键。
    """
    card = make_card()
    _, gt = compose_scene(card, motion_mat(tx=0.0))
    quad = gt.tolist()
    frames = [_blank_roi_frame(quad, seed=s) for s in range(4)]
    frames += [compose_scene(card, motion_mat(tx=0.0))[0] for _ in range(3)]

    tracks = track_plane_frames(frames, quad, start_frame_num=0)
    assert [t.status for t in tracks] == ["lost"] * 4 + ["ok"] * 3
    assert [t.lost_reason for t in tracks[:4]] == ["pre_anchor"] * 4

    path = tmp_path / "lead_in.json"
    save_trajectory(str(path), tracks, video_path="12.mp4", init_quad=quad)
    loaded = load_trajectory(str(path))["frames"]

    assert loaded[0]["status"] == "lost"
    assert [fr.get("lost_reason") for fr in loaded[:4]] == ["pre_anchor"] * 4
    assert all("lost_reason" not in fr for fr in loaded[4:])  # ok 帧仍省略
    # 直方图口径:非 ok 帧的原因字符串逐个可统计,不再有不可归因的 None。
    reasons = {fr["lost_reason"] for fr in loaded if fr["status"] == "lost"}
    assert reasons == {"pre_anchor"}


# ---------------------------------------------------------------------------
# 内容扫描器:scan_content_windows(按链重锚定的输入)

def _content_video(tmp_path, plan, n_frames: int, *, name: str = "content.avi"):
    """按 ``plan`` 写出合成视频:``plan[i]`` 真值 = 第 i 帧 ROI 内是否有卡片。

    返回 ``(视频路径, quad)``。卡片静止贴在画面中心(与
    ``_static_card_scene`` 同一场景),「无内容」帧写纯背景——ROI 内部特征数
    在 2000(卡片)与 0(纯背景)之间切换,判据分离度拉满,不依赖编码噪声。
    """
    card = make_card()
    frame_card, gt = compose_scene(card, motion_mat(tx=0.0))
    blank = np.full((FRAME_H, FRAME_W, 3), 200, np.uint8)
    video = tmp_path / name
    _write_video(video, [frame_card if plan[i] else blank
                         for i in range(n_frames)])
    return video, gt.tolist()


def _window_scene(tmp_path, n_frames: int = 24, *, name: str = "windows.avi"):
    """帧 0-4 空白、5-9 有内容、10-12 空白、13-20 有内容、21-23 空白。"""
    plan = [False] * 5 + [True] * 5 + [False] * 3 + [True] * 8 + [False] * 3
    assert len(plan) == n_frames
    return _content_video(tmp_path, plan, n_frames, name=name)


def test_scan_content_windows_boundaries_inclusive(tmp_path):
    """窗口 = 达标的**连续**帧区间,绝对帧号、闭区间、升序。

    场景(24 帧):0-4 空白 / 5-9 内容 / 10-12 空白 / 13-20 内容 / 21-23 空白
    ⇒ ``[(5, 9), (13, 20)]``——两端帧号都算在窗口内(闭区间,首尾帧的
    「有内容」与中间帧同权),空白段把两段内容切开。
    """
    video, quad = _window_scene(tmp_path)
    assert _count_features_in_roi(
        _gray(_decode_video(video)[0][5]), np.asarray(quad), 2000) \
        >= _INIT_ROI_MIN_FEATURES  # 场景自检:内容帧的 quad 内部确实丰富

    windows = scan_content_windows(str(video), quad, 0, 23)
    assert windows == [(5, 9), (13, 20)]


def test_scan_content_windows_drops_short_runs(tmp_path):
    """短于 ``min_window_frames`` 的连续段丢弃(单帧/两帧噪声不成窗口)。"""
    plan = [False] * 4 + [True] * 2 + [False] * 2 + [True] * 4 + [False] * 2
    video, quad = _content_video(tmp_path, plan, len(plan), name="short.avi")

    assert scan_content_windows(str(video), quad, 0, len(plan) - 1) == [(8, 11)]
    # 阈值降到 2:两帧的段也在
    assert scan_content_windows(str(video), quad, 0, len(plan) - 1,
                                min_window_frames=2) == [(4, 5), (8, 11)]
    # 阈值抬到 5:只剩 4 帧的内容段也不够
    assert scan_content_windows(str(video), quad, 0, len(plan) - 1,
                                min_window_frames=5) == []


def test_scan_content_windows_all_blank_returns_empty(tmp_path):
    """全空白(首帧到末帧 ROI 内部都无内容)⇒ ``[]``,不抛错。"""
    video, quad = _content_video(tmp_path, [False] * 8, 8, name="blank.avi")

    assert scan_content_windows(str(video), quad, 0, 7) == []


def test_scan_content_windows_start_frame_offset(tmp_path, monkeypatch):
    """``start_frame > 0``:扫描从该帧起(帧号仍是绝对帧号),且只定位一次。

    内容在 10-19:从 15 起扫 ⇒ ``[(15, 19)]``(10-14 不在扫描范围内,不会
    被算成窗口);解码纪律与 ``track_plane`` 相同——``set(POS_FRAMES)`` 恰好
    一次、无 ``grab``、读取位置严格 +1。
    """
    plan = [False] * 10 + [True] * 10 + [False] * 5
    video, quad = _content_video(tmp_path, plan, len(plan), name="offset.avi")
    positions: list = []
    calls = _spy_read_positions(monkeypatch, positions)

    windows = scan_content_windows(str(video), quad, 15, 24)
    assert windows == [(15, 19)]
    # 起始定位一次(且只在 start > 0 时):此后只顺序 read,不 seek、不跳帧。
    assert calls["set"] == [cv2.CAP_PROP_POS_FRAMES]
    assert calls["grab"] == []
    assert positions == list(range(15, 25)), positions

    # end_frame 截断同样生效(闭区间)
    assert scan_content_windows(str(video), quad, 15, 17) == [(15, 17)]
    # 从内容起点之前扫:窗口起点是内容真正出现的帧
    assert scan_content_windows(str(video), quad, 0, 24) == [(10, 19)]


def test_scan_content_windows_min_features_override(tmp_path):
    """``min_features`` 覆盖缺省下限;缺省 = ``_INIT_ROI_MIN_FEATURES``。

    卡片内容帧的 quad 内部计数 ~2000:阈值抬到 2001 时没有任何帧达标
    (检测上限就是 ``feature_count=2000``)⇒ ``[]``;阈值降到 1 时背景上
    偶发的角点也可能达标,故只断言内容段**必定**在结果里。
    """
    video, quad = _window_scene(tmp_path, name="minfeat.avi")

    assert scan_content_windows(str(video), quad, 0, 23,
                                min_features=2001) == []
    relaxed = scan_content_windows(str(video), quad, 0, 23, min_features=1)
    assert (5, 9) in relaxed and (13, 20) in relaxed


def test_scan_content_windows_rejects_bad_quad_and_missing_video(tmp_path):
    """输入校验与跟踪同一入口:退化 quad 抛错,打不开的视频抛 RuntimeError。"""
    video, quad = _window_scene(tmp_path, name="validate.avi")

    with pytest.raises(ValueError):
        scan_content_windows(str(video), [[0, 0], [10, 0], [0, 10], [10, 10]])
    with pytest.raises(RuntimeError):
        scan_content_windows(str(tmp_path / "missing.avi"), quad, 0, 5)
