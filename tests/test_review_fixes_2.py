# tests/test_review_fixes_2.py
"""2026-09 代码审查修复的回归测试。

覆盖：
- color_presence_gate：色相环形均值与跨界 inRange 两段区间
- subtitle_generator/styling：_detect_language 不再被 defaultdict 键物化污染
- subtitle_generator/timeline：毫秒时间戳的浮点截断噪声
- ocr_optimizer：平票按"无多数"送审、批量 OCR 错位防护、失败图像不进缓存
- scene_plane_tracker：容器帧数为 0 时读到 EOF、退化单应显式报错
"""

from __future__ import annotations

import numpy as np
import pytest

import core.color_presence_gate as gate
import core.ocr_optimizer as oo
import core.scene_plane_tracker as spt
from core.subtitle_generator.styling import _StylingMixin


# ── color_presence_gate ──────────────────────────────────────


def _bgr_of_hue(hue_deg_cv: float, sat: int = 220, val: int = 220) -> np.ndarray:
    """构造指定 OpenCV 色相的纯色 BGR 图（2x2x4 像素 ≥16 样本）。"""
    hsv = np.zeros((4, 4, 3), dtype=np.uint8)
    hsv[..., 0] = int(hue_deg_cv)
    hsv[..., 1] = sat
    hsv[..., 2] = val
    import cv2

    return cv2.cvtColor(hsv, cv2.COLOR_HSV2BGR)


def test_calibrate_wrap_around_hue_produces_second_pair():
    # 红色系横跨 0/179：样本一半 hue=178，一半 hue=2。
    bgr = np.vstack([_bgr_of_hue(178), _bgr_of_hue(2)])
    bounds = gate.calibrate_hsv_from_crop(bgr, margin=15)
    assert "orange_lower2" in bounds and "orange_upper2" in bounds
    # 两段区间分别是 [0, hi] 与 [lo, 179]。
    assert int(bounds["orange_lower"][0]) == 0
    assert int(bounds["orange_upper2"][0]) == 179
    # 校准色必须落在范围内（旧实现对均值直接截断会得到 [163,179]∪… 或
    # 完全跑飞的单一区间，实际色相反而一个都不匹配）。
    r1 = gate.presence_ratio_masked(_bgr_of_hue(178), None, bounds)
    r2 = gate.presence_ratio_masked(_bgr_of_hue(2), None, bounds)
    assert r1 > 0.5 and r2 > 0.5


def test_calibrate_non_wrapped_hue_single_pair():
    bgr = _bgr_of_hue(60)  # 绿色，远离边界
    bounds = gate.calibrate_hsv_from_crop(bgr, margin=15)
    assert "orange_lower2" not in bounds
    lo, hi = int(bounds["orange_lower"][0]), int(bounds["orange_upper"][0])
    assert lo <= 60 <= hi


def test_presence_ratio_or_second_pair():
    bounds = {
        "orange_lower": np.array([0, 0, 0], dtype=np.uint8),
        "orange_upper": np.array([0, 0, 0], dtype=np.uint8),
        "white_lower": np.array([0, 0, 255], dtype=np.uint8),
        "white_upper": np.array([180, 55, 255], dtype=np.uint8),
        "orange_lower2": np.array([100, 100, 100], dtype=np.uint8),
        "orange_upper2": np.array([110, 255, 255], dtype=np.uint8),
    }
    bgr = _bgr_of_hue(105)
    # 第二段命中（hue 105 in [100,110]），主段 [0,0] 不命中。
    assert gate.presence_ratio_masked(bgr, None, bounds) > 0.5


def test_circular_hue_mean():
    # 环绕样本 [178,179,1,2] 的环形均值应接近 180/0，而不是 ~90。
    mh = gate._circular_hue_mean_deg(np.array([178.0, 179.0, 1.0, 2.0]))
    assert mh >= 175.0 or mh <= 5.0


# ── styling._detect_language ────────────────────────────────


def test_detect_language_unknown_script_returns_en_not_jp():
    mixin = _StylingMixin.__new__(_StylingMixin)
    # 希腊/阿拉伯/泰文字符都在已知的 5 个脚本范围之外。
    assert mixin._detect_language("ναι ευχαριστώ") == "EN"
    assert mixin._detect_language("مرحبا") == "EN"
    assert mixin._detect_language("สวัสดี") == "EN"


def test_detect_language_known_scripts():
    mixin = _StylingMixin.__new__(_StylingMixin)
    assert mixin._detect_language("こんにちは") == "JP"
    assert mixin._detect_language("你好世界") == "CH"
    assert mixin._detect_language("안녕하세요") == "KO"
    assert mixin._detect_language("привет") == "RU"
    assert mixin._detect_language("hello") == "EN"


# ── timeline._format_time_seconds ───────────────────────────


def test_format_time_millisecond_stamp_not_truncated_early():
    from core.subtitle_generator.timeline import _TimelineMixin

    mixin = _TimelineMixin.__new__(_TimelineMixin)
    mixin.fps = 30.0
    # 1160ms 的浮点表示 * 100 == 115.99999...，旧实现截断成 .15。
    assert mixin._format_time_seconds(1.16) == "0:00:01.16"
    assert mixin._format_time_seconds(2.28) == "0:00:02.28"
    # 非整 cs 的真分数仍然截断（不越过帧边界）。
    assert mixin._format_time_seconds(1.1599) == "0:00:01.15"




# ── ocr_optimizer 平票 / 批量对齐 / 图像缓存 ─────────────────


def _raw(lines):
    """lines: [(text, score, (x1, y1, x2, y2)), ...] → unified OCR dict。"""
    polys = [
        [[float(x1), float(y1)], [float(x2), float(y1)],
         [float(x2), float(y2)], [float(x1), float(y2)]]
        for _, _, (x1, y1, x2, y2) in lines
    ]
    return {
        "dt_polys": polys,
        "rec_polys": polys,
        "rec_texts": [t for t, _, _ in lines],
        "rec_scores": [float(s) for _, s, _ in lines],
        "rec_boxes": [[int(x1), int(y1), int(x2), int(y2)] for _, _, (x1, y1, x2, y2) in lines],
    }


def test_fuse_tie_votes_flagged_as_hard_line():
    box = (40, 40, 280, 70)
    sample_results = [
        (None, _raw([("甲", 0.95, box)])),
        (None, _raw([("乙", 0.95, box)])),
    ]
    anchor_aabbs = oo._line_aabbs(sample_results[0][1])
    best, hard_lines, _c = oo.fuse_samples_by_position(
        sample_results, anchor_aabbs, vlm_refine_min_confidence=0.0)
    # 1-1 平票=无多数：必须送审，而不是静默取置信度平局裁决。
    assert hard_lines == [0]
    assert best["rec_texts"][0] in ("甲", "乙")


def test_fuse_unanimous_two_observations_not_hard():
    box = (40, 40, 280, 70)
    sample_results = [
        (None, _raw([("甲", 0.95, box)])),
        (None, _raw([("甲", 0.95, box)])),
    ]
    anchor_aabbs = oo._line_aabbs(sample_results[0][1])
    _best, hard_lines, _c = oo.fuse_samples_by_position(
        sample_results, anchor_aabbs, vlm_refine_min_confidence=0.0)
    assert hard_lines == []


def test_run_batch_returns_none_when_image_missing(tmp_path, monkeypatch):
    """一帧读不出图像时批量路径必须回退逐帧，而不是返回错位列表。"""
    opt = oo.OcrOptimizer(
        work_dir=str(tmp_path), visualize=False, in_memory_mode=True,
        save_ocr_json=False,
    )
    # 3 帧，其中第 2 帧 img_input 指向不存在的文件（_get_image → None）。
    frames = []
    for i in range(3):
        img = np.full((20, 60, 3), 255, dtype=np.uint8)
        if i == 1:
            img = str(tmp_path / "missing.jpg")  # 不存在 → None
        frames.append(({"type": "rect", "points": [0, 0, 10, 10]}, img, i, "roi_0", float(i)))

    monkeypatch.setattr(opt, "_ensure_engine_selected", lambda: None)

    class _FakeEngine:
        def predict_batch(self, imgs):
            return [object() for _ in imgs]

        def normalize_batch_result(self, raws):
            return [opt._empty_ocr_data() for _ in raws]

        def normalize_result(self, raw):
            return opt._empty_ocr_data()

    import core.ocr_engine_manager as mgr
    monkeypatch.setattr(mgr, "get_engine", lambda: _FakeEngine())

    out = opt._run_batch_ocr_on_samples(frames)
    # 错位防护：返回 None（调用方回退逐帧），而不是 2 条错位结果。
    assert out is None


def test_failed_imread_not_cached(tmp_path):
    opt = oo.OcrOptimizer(
        work_dir=str(tmp_path), visualize=False, in_memory_mode=False,
        save_ocr_json=False,
    )
    missing = str(tmp_path / "missing.jpg")
    fd = ({"type": "rect", "points": [0, 0, 10, 10]}, missing, 1, "roi_0", 0.0)
    (tmp_path / "missing.jpg").write_bytes(b"\xff\xd8garbage")  # imread → None
    assert opt._get_image(fd) is None
    assert missing not in opt._image_cache


# ── scene_plane_tracker ─────────────────────────────────────


def test_unwarp_canonical_rejects_degenerate_homography():
    frame = np.zeros((4, 4, 3), dtype=np.uint8)
    bad = np.array([[1, 0, 0], [0, 1, 0], [0, 0, 0]], dtype=np.float64)
    with pytest.raises(ValueError):
        spt.unwarp_canonical(frame, bad, (4, 4))


def test_track_plane_total_zero_with_explicit_range(tmp_path):
    """容器帧数不可靠（报 0）且给了显式区间时不再抛 empty frame range。"""
    import cv2

    video = tmp_path / "v.mp4"
    rng = np.random.default_rng(7)
    wr = cv2.VideoWriter(str(video), cv2.VideoWriter_fourcc(*"mp4v"), 10.0, (256, 256))
    for i in range(6):
        frame = np.repeat(rng.integers(0, 255, size=(256, 256), dtype=np.uint8)[:, :, None], 3, axis=2)
        cv2.circle(frame, (128, 128), 20 + 2 * i, (255, 255, 255), 3)
        wr.write(frame.astype(np.uint8))
    wr.release()

    quad = [[64.0, 64.0], [192.0, 64.0], [192.0, 192.0], [64.0, 192.0]]

    orig_get = cv2.VideoCapture.get

    def fake_get(self, prop, *a, **k):
        if prop == cv2.CAP_PROP_FRAME_COUNT:
            return 0  # 模拟 MKV/WebM 报 0
        return orig_get(self, prop, *a, **k)

    import core.scene_plane_tracker as mod

    monkey = pytest.MonkeyPatch()
    monkey.setattr(cv2.VideoCapture, "get", fake_get)
    try:
        tracks = mod.track_plane(str(video), quad, start_frame=0, end_frame=3)
    finally:
        monkey.undo()
    assert len(tracks) == 4
