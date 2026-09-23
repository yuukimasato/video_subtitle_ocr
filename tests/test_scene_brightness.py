# tests/test_scene_brightness.py
"""B3:有色遮罩与屏幕亮度绑定(core.scene_brightness)单元测试。"""

from __future__ import annotations

import os
import sys

import numpy as np
import pytest

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from core.scene_brightness import (  # noqa: E402
    apply_scene_brightness,
    sample_static_mask_luma_curve,
    sample_visible_background_luma,
)


def test_colored_mask_dims_without_revealing_original_text():
    mask = dict(start_time="0:00:00.00", end_time="0:00:01.00",
                tags=r"{\p1\1c&HC8C8C8&}", body="", base_color=(200, 200, 200))
    result = apply_scene_brightness([mask], [(0, 0.5), (1, 0.5)])
    assert r"\1c&H646464&" in result[0]["tags"]
    assert r"\alpha" not in result[0]["tags"]
    assert result[0]["base_color"] == (200, 200, 200)
    # 输入不被修改。
    assert mask["tags"] == r"{\p1\1c&HC8C8C8&}"


def test_text_event_keeps_alpha_behavior():
    ev = dict(start_time="0:00:00.00", end_time="0:00:01.00",
              tags=r"{\an5}", body="字幕")
    result = apply_scene_brightness([ev], [(0, 0.5), (1, 0.5)])
    assert r"\alpha" in result[0]["tags"]
    assert r"\1c&H" in result[0]["tags"]


def test_base_color_not_popped_by_processing():
    ev = dict(start_time="0:00:00.00", end_time="0:00:01.00",
              tags="", body="", base_color=(10, 20, 30))
    result = apply_scene_brightness([ev], [(0, 0.25)])
    assert result[0]["base_color"] == (10, 20, 30)


def test_constant_curve_produces_no_tags():
    ev = dict(start_time="0:00:00.00", end_time="0:00:01.00", tags="", body="")
    result = apply_scene_brightness([ev], [(0, 1.0), (1, 1.0)])
    assert result[0]["tags"] == ""


def test_sliced_events_use_absolute_times_without_reset():
    """切片后各段按自身起止求亮度:前段尾值 = 后段首值(曲线连续)。"""
    import re

    ev_a = dict(start_time="0:00:00.00", end_time="0:00:00.50", tags="", body="")
    ev_b = dict(start_time="0:00:00.50", end_time="0:00:01.00", tags="", body="")
    result = apply_scene_brightness([ev_a, ev_b],
                                    [(0, 1.0), (0.5, 0.5), (1, 0.5)])
    tail = re.findall(r"\\t\(\d+,(\d+),\\1c&H([0-9A-F]{6})&",
                      result[0]["tags"])
    head = re.search(r"\\1c&H([0-9A-F]{6})&", result[1]["tags"])
    # 前段末目标值(0.5→灰 0x80)与后段首基值一致:不重置曲线。
    assert tail and tail[-1][1] == head.group(1)


# ---------------------------------------------------------------------------
# sample_visible_background_luma
# ---------------------------------------------------------------------------

def test_background_luma_follows_background_not_hand():
    """屏幕由 200 降到 80、前景手始终 230:采样应跟随背景而非手。"""
    frame = np.full((40, 60, 3), 80, np.uint8)
    background = np.ones((40, 60), bool)
    occluder = np.zeros((40, 60), np.uint8)
    occluder[10:20, 20:40] = 1  # 手部前景(亮 230)
    frame[occluder > 0] = 230
    bright = sample_visible_background_luma(frame, background, occluder)
    assert bright == pytest.approx(80.0, abs=2.0)
    # 不排除手时也无法采到 230(手不是背景)——这里验证排除逻辑本身:
    dim = np.full((40, 60, 3), 200, np.uint8)
    dim[occluder > 0] = 230
    bright2 = sample_visible_background_luma(dim, background, occluder)
    assert bright2 == pytest.approx(200.0, abs=2.0)


def test_too_few_valid_pixels_returns_none():
    frame = np.full((40, 60, 3), 100, np.uint8)
    background = np.zeros((40, 60), np.uint8)
    background[:5, :5] = 1  # 25 像素 < 64
    assert sample_visible_background_luma(frame, background) is None


def test_none_inputs_return_none():
    frame = np.full((40, 60, 3), 100, np.uint8)
    mask = np.ones((40, 60), np.uint8)
    assert sample_visible_background_luma(None, mask) is None
    assert sample_visible_background_luma(frame, None) is None
    assert sample_visible_background_luma(frame, mask,
                                          np.zeros((3, 3), np.uint8)) is None


# ---------------------------------------------------------------------------
# sample_static_mask_luma_curve
# ---------------------------------------------------------------------------

def _reader(frames_gray):
    def read(f):
        if 0 <= f < len(frames_gray):
            g = frames_gray[f]
            return np.stack([g, g, g], axis=-1).astype(np.uint8)
        return None
    return read


def test_static_curve_follows_dimming():
    """背景 200 → 80 的强变化被曲线捕捉(ratio 显著下降)。"""
    fps = 25.0
    n = 50
    gray = np.full((40, 60), 200, np.uint8)
    frames = [gray.copy() for _ in range(n)]
    for i in range(25, n):
        frames[i][:] = 80
    curve = sample_static_mask_luma_curve(
        _reader(frames), (0, 0, 60, 40), 0.0, n / fps, fps=fps, stride=3)
    assert curve
    bright = [r for t, r in curve if t < 0.9]
    dim = [r for t, r in curve if t > 1.2]
    assert bright and dim
    assert max(bright) == pytest.approx(1.0, abs=0.05)
    assert max(dim) < 0.6  # 80/200 = 0.4


def test_static_curve_strokes_excluded():
    """暗色笔画(原字幕)不抬高背景亮度采样。"""
    fps = 25.0
    gray = np.full((60, 80), 120, np.uint8)
    gray[20:24, 10:70] = 20  # 暗笔画
    frames = [gray.copy() for _ in range(30)]
    curve = sample_static_mask_luma_curve(
        _reader(frames), (0, 0, 80, 60), 0.0, 1.2, fps=fps, stride=3)
    assert curve
    # 全部比值 ≈ 1(背景恒 120,笔画被剔除)。
    assert all(r == pytest.approx(1.0, abs=0.02) for _t, r in curve)


def test_static_curve_no_valid_samples_returns_empty():
    fps = 25.0
    curve = sample_static_mask_luma_curve(
        lambda f: None, (0, 0, 60, 40), 0.0, 1.0, fps=fps)
    assert curve == []
