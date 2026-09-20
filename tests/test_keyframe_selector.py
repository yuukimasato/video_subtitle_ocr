# tests/test_keyframe_selector.py
"""清晰关键帧选取（core/keyframe_selector.py）离线单元测试。

用合成"文字卡片"视频验证（与 test_scene_plane_tracker 同一构造模式）：

- 清晰文字帧得分高于高斯模糊帧，top-K 只选清晰帧；
- ``status="lost"`` 的帧（整帧涂黑遮挡 / 质量门限剔除）永不被选；
- k 大于 ok 帧数时全部返回（按分数降序）；
- ``min_gap_sec`` 贪心分散：两两时间差 ≥ 阈值；
- 分数并列取更早帧；无 ok 帧返回空列表；视频打不开抛 RuntimeError；
- ``ensure_coverage``（默认关闭）：清晰度集中在一段时，池尾追加 ok 帧时间
  跨度分桶的代表帧（池前缀逐位不变）、池长上界生效、单桶/零跨度/帧数少于
  桶数等退化场景确定性收敛、``diagnostics`` 实据齐全。

轨迹为手工构造的纯平移 TrackedQuad（单应精确已知），FFV1 无损编码保证
解码帧与写入帧逐位一致，分数完全确定。
"""

from __future__ import annotations

import dataclasses
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


# ---------------------------------------------------------------------------
# 时间覆盖（ensure_coverage）：清晰度集中在一段时，池尾追加后段代表帧
# ---------------------------------------------------------------------------

COVERAGE_N = 48               # 48 帧 @8fps = 6s：前 16 帧清晰、后 32 帧模糊
COVERAGE_HEAD = 16            # 清晰段帧数（"空白引导段"）
TAIL_SHARP_FRAMES = (20, 40)  # 后段里略清晰的两帧：落在第 1/第 2 桶
TAIL_SHARP_SIGMA = 1.5
TAIL_BLUR_SIGMA = 9.0


def build_sharp_head_video(tmp_path, name: str = "coverage.avi"):
    """清晰度完全集中在前 16 帧、后 32 帧强模糊的序列（+ 手工平移轨迹）。

    后段（= "文字只出现在这里" 的段落）在清晰度上毫无优势：纯清晰度 top-K
    只会取到头几帧，后段一帧候选都没有——这正是 ``ensure_coverage`` 要补的
    覆盖缺陷。平移 2px/帧保证卡片始终在画面内（既有 6px/帧 的构造在 48 帧上
    会出画面，numpy 切片静默裁剪会让展开图不再等于卡片）。
    """
    card = make_card()
    frames, tracks = [], []
    for i in range(COVERAGE_N):
        if i < COVERAGE_HEAD:
            sigma = 0.0
        else:
            sigma = TAIL_SHARP_SIGMA if i in TAIL_SHARP_FRAMES else TAIL_BLUR_SIGMA
        frames.append(compose_frame(card, tx=2 * i, blur_sigma=sigma))
        tracks.append(make_track(i, tx=2 * i))
    return write_video(tmp_path / name, frames), tracks


def test_coverage_appends_frames_outside_sharp_cluster(tmp_path):
    """清晰度集中在头部时，开启覆盖后池包含后段的桶代表帧。

    3 个桶（缺省 = k）按 ok 帧时间跨度等分：桶 0 = 帧 0-15（清晰段，前缀
    已占）、桶 1 = 帧 16-31、桶 2 = 帧 32-47。后两桶各追加桶内最清晰帧。
    """
    video, tracks = build_sharp_head_video(tmp_path)

    base = select_keyframes(video, tracks, k=3)
    assert base == [0, 1, 2], "清晰度全部集中在头部（前 16 帧分数并列最高）"

    pool = select_keyframes(video, tracks, k=3, ensure_coverage=True)
    assert pool[:3] == base            # 硬约束：前缀逐位不变
    assert pool[3:] == [20, 40]        # 后两桶各取桶内最清晰帧（文字所在段）


def test_coverage_prefix_identical_to_selection_without_coverage(tmp_path):
    """硬约束：覆盖开启后前缀与关闭时逐位相同（min_gap 贪心结果不变）。

    分批 OCR 的批边界按前缀顺序切分，前缀不变 ⇒ 既有视频（首批即有文字）
    的接受批与产物不变；覆盖只可能在「原池一帧文字都没有」时才被走到。
    """
    video, tracks = build_sharp_head_video(tmp_path)

    for kwargs in ({"k": 3}, {"k": 3, "min_gap_sec": 0.25},
                   {"k": 12, "min_gap_sec": 0.125}):
        base = select_keyframes(video, tracks, **kwargs)
        pool = select_keyframes(video, tracks, ensure_coverage=True, **kwargs)
        assert pool[:len(base)] == base, kwargs
        assert len(pool) >= len(base)

    # 覆盖确实起了作用（否则前半段断言退化成恒真）
    covered = select_keyframes(video, tracks, k=3, min_gap_sec=0.25,
                               ensure_coverage=True)
    assert covered == [0, 2, 4, 20, 40]


def test_coverage_degenerate_buckets_and_zero_span(tmp_path):
    """退化场景确定性收敛（不抛错、不死循环、不追加）。"""
    video, tracks = build_sharp_head_video(tmp_path)
    base = select_keyframes(video, tracks, k=3)

    # 单桶：唯一桶已被前缀占 → 不追加
    assert select_keyframes(video, tracks, k=3, ensure_coverage=True,
                            coverage_buckets=1) == base
    # 桶数 <= 0：视作不补齐（只返回前缀）
    for buckets in (0, -3):
        assert select_keyframes(video, tracks, k=3, ensure_coverage=True,
                                coverage_buckets=buckets) == base
    # 帧数 < 桶数：空桶直接跳过（每帧各自占桶，前缀已占满有候选的桶）
    sparse = select_keyframes(video, tracks, k=3, ensure_coverage=True,
                              coverage_buckets=1000)
    assert sparse[:3] == base
    assert len(sparse) <= COVERAGE_N

    # 时间跨度为零（ok 帧全在同一时刻）→ 单桶，前缀已占 → 不追加
    flat = [dataclasses.replace(t, time_sec=0.0) for t in tracks]
    assert select_keyframes(video, flat, k=3, ensure_coverage=True) == base

    # 单帧轨迹（跨度为零 + k 未达）也不抛错
    one = select_keyframes(video, tracks[:1], k=3, ensure_coverage=True)
    assert one == [0]


def test_coverage_pool_size_bound(tmp_path):
    """池长有界：追加帧不超过 max_pool_size - 前缀长度，前缀永不被截断。"""
    video, tracks = build_sharp_head_video(tmp_path)
    base = select_keyframes(video, tracks, k=2)
    assert base == [0, 1]

    # 显式上限 5 = 前缀 2 + 覆盖 3（12 桶里前 3 个未占桶各一帧）
    pool = select_keyframes(video, tracks, k=2, ensure_coverage=True,
                            coverage_buckets=12, max_pool_size=5)
    assert pool[:2] == base
    assert len(pool) == 5

    # 缺省上限 = 2 * k
    default_pool = select_keyframes(video, tracks, k=2, ensure_coverage=True,
                                    coverage_buckets=12)
    assert len(default_pool) == 4

    # 上限 <= 前缀长度：不追加；上限为负也不截断前缀
    for limit in (1, 0, -5):
        assert select_keyframes(video, tracks, k=2, ensure_coverage=True,
                                coverage_buckets=12,
                                max_pool_size=limit) == base


def test_coverage_k_nonpositive_and_no_ok_frames_unchanged(tmp_path):
    """既有早退行为不变：k<=0 / 无 ok 帧即使开启覆盖也返回空列表。"""
    video, tracks = build_sharp_head_video(tmp_path)
    assert select_keyframes(video, tracks, k=0, ensure_coverage=True) == []
    lost = [make_track(i, ok=False) for i in range(3)]
    assert select_keyframes(video, lost, k=3, ensure_coverage=True) == []


def test_diagnostics_report_scores_prefix_and_coverage(tmp_path):
    """``diagnostics`` 实据齐全：候选分数/时间、前缀、池、覆盖情况。"""
    video, tracks = build_sharp_head_video(tmp_path)
    diag: dict = {}

    pool = select_keyframes(video, tracks, k=3, ensure_coverage=True,
                            coverage_buckets=3, diagnostics=diag)

    assert diag["pool"] == pool == [0, 1, 2, 20, 40]
    assert diag["prefix"] == pool[:3]
    assert diag["prefix_len"] == 3
    # 候选帧 = 全部 ok 帧；头部（清晰）分数高于后段（模糊）
    assert set(diag["scores"]) == set(range(COVERAGE_N))
    assert diag["scores"][0] > diag["scores"][25]
    assert diag["times"][COVERAGE_N - 1] == pytest.approx((COVERAGE_N - 1) / FPS)
    cov = diag["coverage"]
    assert cov["enabled"] is True
    assert cov["buckets"] == 3
    assert cov["limit"] == 6                    # 2 * k
    assert cov["added"] == [20, 40]
    assert cov["covered_buckets"] == 3          # 覆盖后每桶都有代表帧
    assert cov["span_sec"] == pytest.approx([0.0, (COVERAGE_N - 1) / FPS])

    # 关闭覆盖时也写入实据（覆盖段标记 enabled=False、无追加）
    off: dict = {}
    base = select_keyframes(video, tracks, k=3, diagnostics=off)
    assert off["pool"] == base and off["coverage"]["enabled"] is False
    assert off["coverage"]["added"] == [] and off["coverage"]["buckets"] == 0

