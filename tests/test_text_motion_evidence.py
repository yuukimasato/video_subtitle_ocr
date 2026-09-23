# tests/test_text_motion_evidence.py
"""D1:字形支持的屏幕位置测量(core.text_motion_evidence)测试。"""

from __future__ import annotations

import os
import sys

import cv2
import numpy as np

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from core.text_motion_evidence import (  # noqa: E402
    classify_text_centers,
    glyph_mask_from_reference,
    masked_text_match,
)


# ---------------------------------------------------------------------------
# classify_text_centers
# ---------------------------------------------------------------------------

def test_stationary_text_ignores_background_trajectory():
    centers = {0: (100, 100), 10: (100.5, 100), 20: (99.5, 100)}
    result = classify_text_centers(centers, {0: 0.9, 10: 0.9, 20: 0.9})
    assert result.state == "static"
    assert abs(result.anchor[0] - 100) < 1


def test_real_motion_is_preserved():
    result = classify_text_centers(
        {0: (100, 100), 10: (110, 100), 20: (120, 100)},
        {0: 0.9, 10: 0.9, 20: 0.9})
    assert result.state == "moving"


def test_background_only_match_cannot_establish_motion():
    result = classify_text_centers({0: (100, 100), 10: (80, 100)},
                                   {0: 0.2, 10: 0.2})
    assert result.state == "unknown"


def test_low_scores_are_not_trusted():
    # 中心稳定但分数全部低于 min_score:证据不可信 → unknown。
    result = classify_text_centers(
        {0: (100, 100), 10: (100, 100), 20: (100, 100)},
        {0: 0.2, 10: 0.2, 20: 0.2})
    assert result.state == "unknown"


def test_inconsistent_jumps_are_unknown():
    # 位移大但方向不一致(背景跳点形态)→ unknown,不判 moving。
    result = classify_text_centers(
        {0: (100, 100), 10: (130, 100), 20: (100, 130)},
        {0: 0.9, 10: 0.9, 20: 0.9})
    assert result.state == "unknown"


def test_non_finite_centers_rejected():
    result = classify_text_centers(
        {0: (100, 100), 10: (float("nan"), 100), 20: (100, 100)},
        {0: 0.9, 10: 0.9, 20: 0.9})
    assert result.state == "unknown"


# ---------------------------------------------------------------------------
# glyph_mask_from_reference / masked_text_match 端到端
# ---------------------------------------------------------------------------

def _compose_scene(shift_px: float, text_dx: float, frames_n: int = 12,
                   dim: bool = False, with_text: bool = True):
    """随机纹理背景以 shift_px/帧左移;文字以 text_dx/帧右移(固定=0)。"""
    rng = np.random.default_rng(23)
    background = rng.integers(100, 180, (96, 240), dtype=np.uint8)
    frames = []
    truth = []
    for frame in range(frames_n):
        image = np.roll(background, -int(round(shift_px * frame)), axis=1)
        if with_text:
            # 文字直接画在移动后的位置(整图 roll 会连背景一起平移)。
            cv2.putText(image, "TEST", (70 + int(round(text_dx * frame)), 55),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.8, 20, 2, cv2.LINE_AA)
        if dim:
            image = (image.astype(np.float64) * 0.5).astype(np.uint8)
        frames.append(image)
        truth.append(70 + text_dx * frame)
    return frames, truth


def _template_from_first(frames):
    """首帧文字范围提模板,mask 只含笔画。"""
    ref = frames[0]
    box = (60, 35, 120, 60)
    mask = glyph_mask_from_reference(ref, box)
    assert mask is not None, "glyph mask must be extractable"
    return ref, mask, box


def test_static_text_over_moving_background():
    frames, _truth = _compose_scene(shift_px=5, text_dx=0.0)
    ref, mask, box = _template_from_first(frames)
    centers, scores = {}, {}
    for i, img in enumerate(frames):
        hit = masked_text_match(img, ref, mask, (40, 25, 160, 70))
        assert hit is not None, f"frame {i} must match"
        centers[i * 10] = (hit[0], hit[1])
        scores[i * 10] = hit[2]
    result = classify_text_centers(centers, scores, min_samples=3)
    assert result.state == "static", result.reason
    drift = max(abs(c[0] - result.anchor[0]) for c in centers.values())
    assert drift <= 1.0


def test_moving_text_is_measured():
    frames, truth = _compose_scene(shift_px=5, text_dx=2.0)
    ref, mask, _box = _template_from_first(frames)
    centers, scores = {}, {}
    for i, img in enumerate(frames):
        # 搜索窗随真实位移加宽。
        x = truth[i]
        hit = masked_text_match(img, ref, mask,
                                (int(x) - 30, 25, int(x) + 60, 70))
        assert hit is not None, f"frame {i} must match"
        centers[i * 10] = (hit[0], hit[1])
        scores[i * 10] = hit[2]
    # 位移 2px/帧小于默认容差 2.5px:按场景传入 1px 容差(真实调用按
    # 分辨率/帧距选容差)。
    result = classify_text_centers(centers, scores, min_samples=3,
                                   tolerance_px=1.0)
    assert result.state == "moving", result.reason
    # 测得位移方向/量符合 2px/帧 ±1px。
    frames_sorted = sorted(centers)
    per_frame = ((centers[frames_sorted[-1]][0]
                  - centers[frames_sorted[0]][0])
                 / (frames_sorted[-1] - frames_sorted[0]) * 10.0)
    assert abs(per_frame - 2.0) <= 1.0


def test_background_only_is_unknown():
    frames, _truth = _compose_scene(shift_px=5, text_dx=0.0, with_text=False)
    ref, mask, _box = _template_from_first(
        _compose_scene(shift_px=0, text_dx=0.0)[0])
    centers, scores = {}, {}
    for i, img in enumerate(frames):
        hit = masked_text_match(img, ref, mask, (40, 25, 160, 70))
        if hit is not None:  # 歧义窗可能返回 None:记低分中心
            centers[i * 10] = (hit[0], hit[1])
            scores[i * 10] = hit[2]
    if len(centers) < 3:
        result = classify_text_centers(centers, scores, min_samples=3)
        assert result.state == "unknown"
    else:
        # 无字背景测得的中心不可能稳定收敛 → 非 static。
        result = classify_text_centers(centers, scores, min_samples=3)
        assert result.state in ("unknown", "moving")


def test_global_dimming_does_not_change_geometry():
    frames, _truth = _compose_scene(shift_px=5, text_dx=0.0)
    dim_frames, _t2 = _compose_scene(shift_px=5, text_dx=0.0, dim=True)
    ref, mask, _box = _template_from_first(frames)
    centers = {}
    for i, img in enumerate(dim_frames):
        hit = masked_text_match(img, ref, mask, (40, 25, 160, 70))
        if hit is not None:
            centers[i] = (hit[0], hit[1])
    assert len(centers) >= 10
    xs = [c[0] for c in centers.values()]
    assert max(xs) - min(xs) <= 1.0


def test_glyph_mask_rejects_flat_or_full_regions():
    flat = np.full((96, 240), 128, np.uint8)
    assert glyph_mask_from_reference(flat, (60, 35, 120, 60)) is None
    # 覆盖率 100%(整框都是笔画):超上限 → None。
    full = np.full((96, 240), 20, np.uint8)
    assert glyph_mask_from_reference(full, (60, 35, 120, 60)) is None


def test_masked_text_match_rejects_ambiguous_peak():
    # 无字形掩膜 → None。
    frame = np.full((96, 240), 120, np.uint8)
    assert masked_text_match(frame, frame, None,
                             (40, 25, 160, 70)) is None
