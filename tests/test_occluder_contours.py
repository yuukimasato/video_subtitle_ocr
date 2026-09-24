# tests/test_occluder_contours.py
"""B4:前景轮廓分割(core.occluder_contours)单元测试。

合成场景刻意避开肤色先验:前景为蓝/绿指状块,白/暗背景各一张;验证
GrabCut 精化保留指缝、全局调暗不产生前景、缺种子 unknown、iclip 序列
坐标合法。
"""

from __future__ import annotations

import os
import sys

import cv2  # noqa: E402
import numpy as np
import pytest

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from core.occluder_contours import (  # noqa: E402
    ContourResult,
    ContourRing,
    apply_screen_occlusion,
    build_seeds,
    contours_to_iclip,
    refine_occluder_contours,
)

W, H = 320, 200
FPS = 25.0


def _two_finger_scene(background_gray: int) -> "np.ndarray":
    """背景 + 两根指状前景块(蓝/绿),指缝保持背景色。"""
    frame = np.full((H, W, 3), background_gray, np.uint8)
    cv2_rand = np.random.default_rng(7)
    # 背景:弱纹理噪声(幅度小,亮度归一差分不会误判前景)
    noise = cv2_rand.integers(-6, 7, (H, W, 1), dtype=np.int16)
    frame = np.clip(frame.astype(np.int16) + noise, 0, 255).astype(np.uint8)
    # 指 1(蓝):竖条 x 140..170, y 30..170
    frame[30:170, 140:170] = (200, 60, 30)
    # 指缝 x 170..186(保持背景)
    # 指 2(绿):竖条 x 186..216, y 20..160
    frame[20:160, 186:216] = (60, 200, 80)
    return frame


def _domain_mask():
    domain = np.zeros((H, W), np.uint8)
    domain[10:180, 120:236] = 1  # 待保护文字所在物体及外扩区
    return domain


def _reference(frame_bgr):
    return cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2GRAY)


def test_two_finger_foreground_segmentation():
    """白底与暗底各一张:主体分割 IoU≥0.95,指缝中心不属前景。"""

    for bg_gray in (230, 30):
        frame = _two_finger_scene(bg_gray)
        ref = np.full((H, W, 3), bg_gray, np.uint8)
        fg, bg, status, _reason = build_seeds(
            _reference(frame), _reference(ref), _domain_mask())
        assert status == "valid"
        result = refine_occluder_contours(
            frame, fg, bg, _domain_mask(), resolution_height=1080.0)
        assert result.valid, result.reason
        # 光栅化轮廓,对照真实前景(两根指)。
        raster = np.zeros((H, W), np.uint8)
        outer = [r.points.astype(np.int32) for r in result.rings
                 if not r.is_hole]
        inner = [r.points.astype(np.int32) for r in result.rings if r.is_hole]
        if outer:
            cv2.fillPoly(raster, outer, 1)
        if inner:
            cv2.fillPoly(raster, inner, 0)
        truth = np.zeros((H, W), np.uint8)
        truth[30:170, 140:170] = 1
        truth[20:160, 186:216] = 1
        inter = int(((raster > 0) & (truth > 0)).sum())
        union = int(((raster > 0) | (truth > 0)).sum())
        assert union and inter / union >= 0.95, (
            inter / union, bg_gray)
        # 指缝中心保持背景(不被凸包/填充吞掉)。
        assert raster[100, 178] == 0


def test_global_dimming_is_not_foreground():

    frame = _two_finger_scene(200)
    dim = cv2.convertScaleAbs(frame, alpha=0.4)
    fg, bg, status, reason = build_seeds(
        _reference(dim), _reference(frame), _domain_mask())
    assert status == "clear", reason
    assert not fg.any() and not bg.any()


def test_texture_motion_alone_is_not_foreground():
    """强纹理整体移动(非遮挡)在 domain 内不构成连通强前景:差分覆盖率
    超门限 → clear;此处验证 build_seeds 不会把纯差分当手。"""
    rng = np.random.default_rng(23)
    frame = rng.integers(0, 256, (H, W, 3), dtype=np.uint8)
    ref = np.roll(frame, 12, axis=1).copy()
    _fg, _bg, status, _reason = build_seeds(
        _reference(frame), _reference(ref), _domain_mask())
    assert status == "clear"


def test_missing_or_conflicting_seeds_unknown():

    frame = _two_finger_scene(200)
    empty = np.zeros((H, W), np.uint8)
    result = refine_occluder_contours(
        frame, empty, empty, _domain_mask())
    assert result.status == "unknown"
    assert "foreground seed" in result.reason
    # 冲突种子。
    fg = np.zeros((H, W), np.uint8)
    fg[50:60, 150:160] = 1
    bg = fg.copy()
    result2 = refine_occluder_contours(frame, fg, bg, _domain_mask())
    assert result2.status == "unknown"
    assert "conflict" in result2.reason


def test_contours_to_iclip_shape_and_validation():
    outer = ContourRing(np.array([[10.0, 10.0], [60.0, 10.0],
                                  [60.0, 60.0], [10.0, 60.0]]))
    inner = ContourRing(np.array([[25.0, 25.0], [45.0, 25.0],
                                  [45.0, 45.0], [25.0, 45.0]]),
                        is_hole=True, parent=0)
    tag = contours_to_iclip([outer, inner])
    assert tag.startswith("\\iclip(") and tag.endswith(")")
    assert tag.count("m ") == 2 and tag.count("l ") == 2
    with pytest.raises(ValueError):
        bad = ContourRing(np.array([[1.0, float("nan")], [2, 2], [3, 3]]))
        contours_to_iclip([bad])
    assert contours_to_iclip([]) == ""


def test_apply_screen_occlusion_time_local_clipping():
    """valid 帧写 clip、clear 帧不裁、unknown 只复用一帧。"""
    events = [{
        "start_time": "0:00:00.00", "end_time": "0:00:01.00",
        "style": "Scene", "name": "motion", "tags": "{\\pos(100,100)}",
        "body": "x", "layer": 1, "roi": "ev0",
    }]

    class _Geom:
        def frames(self):
            # 帧 5(0.2s)命中、帧 10(0.4s)清晰、帧 12(0.48s)再命中、
            # 帧 15(0.6s)缺失=unknown(可复用一帧)。
            return [(5, (0.0, 0.0, 100.0, 100.0)),
                    (10, (0.0, 0.0, 100.0, 100.0)),
                    (12, (0.0, 0.0, 100.0, 100.0)),
                    (15, (0.0, 0.0, 100.0, 100.0))]

    rings = [ContourRing(np.array([[0.0, 0.0], [50.0, 0.0],
                                   [50.0, 50.0], [0.0, 50.0]]))]
    screen = {
        5: ContourResult([ContourRing(r.points.copy(), r.is_hole, r.parent)
                          for r in rings], "valid", ""),
        10: ContourResult([], "clear", "no fg"),
        12: ContourResult([ContourRing(np.array([[60.0, 60.0], [90.0, 60.0],
                                                 [90.0, 90.0], [60.0, 90.0]]))],
                          "valid", ""),
        # 帧 15 缺失 → unknown
    }
    out = apply_screen_occlusion(events, screen, fps=FPS,
                                 event_geometry={"ev0": _Geom()})
    # 全时间覆盖:未知前导(0..0.2) + 命中(0.2) + 清晰(0.4) + 命中
    # (0.48,末帧+1) + 未知尾段(0.52..1.0,不复用过期轮廓)。
    assert len(out) == 5
    clipped = [e for e in out if "\\iclip(" in e["tags"]]
    assert len(clipped) == 2
    clear_seg = [e for e in out if "\\iclip(" not in e["tags"]]
    assert len(clear_seg) == 3
    assert clear_seg[0]["end_time"] == "0:00:00.20"   # 前导未知不裁
    assert clear_seg[1]["start_time"] == "0:00:00.40"  # clear 证据段
    # 命中段 0.48 只延伸到末帧+1 帧(0.52),其后 unknown 不复用。
    assert clipped[1]["start_time"] == "0:00:00.48"
    assert clipped[1]["end_time"] == "0:00:00.52"
    assert "60.0" in clipped[1]["tags"]


def test_apply_screen_occlusion_excludes_event_end_frame():
    """采样恰好落在 end 的帧不能把半开事件延长到 end 之后。"""
    event = {
        "start_time": "0:00:00.00", "end_time": "0:00:01.00",
        "style": "Scene", "tags": "{\\pos(10,10)}", "body": "x",
    }

    class _Geom:
        def frames(self):
            return [(10, (0.0, 0.0, 100.0, 100.0))]

    ring = ContourRing(np.array([[1.0, 1.0], [20.0, 1.0],
                                 [20.0, 20.0], [1.0, 20.0]]))
    out = apply_screen_occlusion(
        [event], {10: ContourResult([ring], "valid", "")},
        fps=10.0, event_geometry={str(id(event)): _Geom()})
    assert out == [event]
