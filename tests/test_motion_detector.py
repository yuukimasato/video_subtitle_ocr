# tests/test_motion_detector.py
"""移动文字自动检测(FR-1,core/motion_detector.py)离线测试。

- 合成视频:一块含两条横向移动黑条的卡片路径 + 一条静止黑条;注入阈值
  找黑条的 mock OCR(不加载真模型);
- 断言:移动条检出(位移 > 门限)、静止条不检出;同动多行合并为块级
  区域;region 裁剪生效;quad 覆盖移动路径;
- 项目 CLI --motion-auto:monkeypatch 检测器 → 检出区域走轨迹管线并入
  输出(复用 --motion-quad 的 mock 引擎与合成视频)。

采样 OCR 的行心位移口径见模块 docstring;与主流水线移动门限同源(24px)。
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

from core.motion_detector import MotionRegion, detect_moving_text  # noqa: E402

FRAME_W, FRAME_H = 320, 240
FPS = 8.0
N_FRAMES = 24
STEP = 3.0  # 移动条每帧水平位移(px)

# 移动条(两条,同动同速,间隔 4px → 应经外扩合并为块级区域)与静止条(画面坐标)
MOVING_BARS = [
    (20, 50, 60, 64),
    (20, 68, 60, 82),
]
STATIC_BAR = (250, 120, 290, 136)


def make_frame(i: int) -> np.ndarray:
    frame = np.full((FRAME_H, FRAME_W, 3), 200, np.uint8)
    dx = int(round(STEP * i))
    for x1, y1, x2, y2 in MOVING_BARS:
        cv2.rectangle(frame, (x1 + dx, y1), (x2 + dx, y2), (10, 10, 10), -1)
    x1, y1, x2, y2 = STATIC_BAR
    cv2.rectangle(frame, (x1, y1), (x2, y2), (10, 10, 10), -1)
    return frame


def write_video(path) -> str:
    writer = cv2.VideoWriter(
        str(path), cv2.VideoWriter_fourcc(*"FFV1"), float(FPS),
        (FRAME_W, FRAME_H))
    if not writer.isOpened():
        pytest.skip("no usable video codec (FFV1) for motion detector tests")
    for i in range(N_FRAMES):
        writer.write(make_frame(i))
    writer.release()
    return str(path)


def make_fullframe_ocr():
    """全画面阈值找黑条 → 统一 OCR dict(文本按 y 序编号,稳定命名)。"""

    def ocr_fn(img):
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        mask = (gray < 60).astype(np.uint8)
        contours, _ = cv2.findContours(
            mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        rects = sorted(
            (cv2.boundingRect(c) for c in contours
             if cv2.contourArea(c) >= 30),
            key=lambda r: (r[1], r[0]))
        ocr = {"dt_polys": [], "rec_polys": [], "rec_texts": [],
               "rec_scores": [], "rec_boxes": []}
        for k, (x, y, w, h) in enumerate(rects):
            poly = [[float(x), float(y)], [float(x + w), float(y)],
                    [float(x + w), float(y + h)], [float(x), float(y + h)]]
            ocr["dt_polys"].append([list(p) for p in poly])
            ocr["rec_polys"].append(poly)
            ocr["rec_texts"].append(f"BAR{k}")
            ocr["rec_scores"].append(0.95)
            ocr["rec_boxes"].append([int(x), int(y), int(x + w), int(y + h)])
        return ocr

    return ocr_fn


@pytest.fixture
def video(tmp_path):
    return write_video(tmp_path / "detect.avi")


class TestDetectMovingText:
    def test_moving_detected_and_static_ignored(self, video):
        regions = detect_moving_text(
            video, ocr_fn=make_fullframe_ocr(),
            sample_stride_sec=0.5, move_thresh_px=24.0)
        assert len(regions) == 1, \
            f"moving card should merge into one region, got {regions}"
        region = regions[0]
        assert set(region.texts) == {"BAR0", "BAR1"}
        # 采样步长 0.5s(=4 帧)→ 首末采样帧 0/20,位移 ≈ 3×20
        assert region.max_displacement_px == pytest.approx(STEP * 20, abs=STEP)
        # quad 覆盖移动路径(起点 - pad 到终点 + pad)
        x1, y1, x2, y2 = region.bbox()
        assert x1 <= 20 + 2 and x2 >= 60 + STEP * 20 - 2
        assert y1 <= 50 and y2 >= 82
        # 时间范围覆盖采样区间(外扩后不小于首末采样帧)
        assert region.start_frame < region.end_frame

    def test_region_crop_scopes_detection(self, video):
        # 只看静止条所在右带 → 无移动区域
        regions = detect_moving_text(
            video, ocr_fn=make_fullframe_ocr(),
            region=(240, 0, FRAME_W, FRAME_H),
            sample_stride_sec=0.5, move_thresh_px=24.0)
        assert regions == []
        # 只看左半 → 移动条检出、静止条不在结果里
        regions = detect_moving_text(
            video, ocr_fn=make_fullframe_ocr(),
            region=(0, 0, 160, FRAME_H),
            sample_stride_sec=0.5, move_thresh_px=24.0)
        assert len(regions) == 1
        assert set(regions[0].texts) <= {"BAR0", "BAR1"}

    def test_threshold_filters_stationary(self, video):
        # 门限抬到超过总位移 → 无检出
        assert detect_moving_text(
            video, ocr_fn=make_fullframe_ocr(),
            sample_stride_sec=0.5, move_thresh_px=10_000.0) == []

    def test_min_samples_filters_sparse(self, video):
        # min_samples 高于采样数 → 无检出
        assert detect_moving_text(
            video, ocr_fn=make_fullframe_ocr(),
            sample_stride_sec=0.5, min_samples=99) == []

    def test_start_end_frame_window(self, video):
        # 检测窗口限制在前 1 秒(帧 0..7,位移 ≈ 3×7=21 < 24)→ 无检出
        assert detect_moving_text(
            video, ocr_fn=make_fullframe_ocr(),
            start_frame=0, end_frame=7,
            sample_stride_sec=0.5, move_thresh_px=24.0) == []
        # 窗口放宽到前 2 秒(帧 0..15,位移 ≈ 45 > 24)→ 检出
        regions = detect_moving_text(
            video, ocr_fn=make_fullframe_ocr(),
            start_frame=0, end_frame=15,
            sample_stride_sec=0.5, move_thresh_px=24.0)
        assert len(regions) == 1

    def test_unopenable_video_raises(self):
        with pytest.raises(RuntimeError):
            detect_moving_text(
                "/nonexistent/no_such.avi", ocr_fn=make_fullframe_ocr())


# ---------------------------------------------------------------------------
# 项目 CLI --motion-auto(monkeypatch 检测器,复用 --motion-quad 的基建)
# ---------------------------------------------------------------------------

class TestCliMotionAuto:
    def test_auto_requires_static_evidence_before_takeover(
            self, tmp_path, monkeypatch):
        from test_motion_ass_cli import build_case, make_mock_ocr

        from cli import load_roi_file  # noqa: F401  (确保 cli 可导入)
        import cli as cli_mod

        video_path, quad0 = build_case(tmp_path)
        # 检测器替换:返回覆盖卡片路径的区域(quad 旋向未归一,由消费方纠正)
        region = MotionRegion(
            quad=[[90.0, 80.0], [249.0, 80.0], [249.0, 179.0], [90.0, 179.0]],
            start_frame=0, end_frame=15,
            max_displacement_px=60.0, sample_count=6, texts=["LINE0"])
        monkeypatch.setattr(
            "core.motion_detector.detect_moving_text",
            lambda *a, **k: [region])
        engine = type("E", (), {"cleanup": lambda self: None})()
        monkeypatch.setattr(
            "scripts.motion_ass._default_ocr_fn",
            lambda engine_id=None, engine_options=None: (make_mock_ocr([]), engine))
        out = str(tmp_path / "auto.ass")
        code = cli_mod.main(
            [video_path, "-o", out, "--workers", "1", "-q",
             "--motion-auto", "--motion-auto-stride", "0.5"])
        assert code == 0
        text = open(out, encoding="utf-8-sig").read()
        motion_lines = [ln for ln in text.splitlines()
                        if ln.startswith("Dialogue:") and ",Scene,motion," in ln]
        # 默认静态 ROI 是底部条带,该合成卡片不在其中;A3 不允许
        # 自动轨迹在没有对应静态证据时绕过保真门控写出候选。
        assert motion_lines == []

    def test_auto_without_detection_continues(self, tmp_path, monkeypatch):
        from test_motion_ass_cli import build_case, make_mock_ocr

        import cli as cli_mod

        video_path, _quad0 = build_case(tmp_path)
        monkeypatch.setattr(
            "core.motion_detector.detect_moving_text",
            lambda *a, **k: [])
        engine = type("E", (), {"cleanup": lambda self: None})()
        monkeypatch.setattr(
            "scripts.motion_ass._default_ocr_fn",
            lambda engine_id=None, engine_options=None: (make_mock_ocr([]), engine))
        out = str(tmp_path / "none.ass")
        code = cli_mod.main(
            [video_path, "-o", out, "--workers", "1", "-q", "--motion-auto"])
        assert code == 0
        text = open(out, encoding="utf-8-sig").read()
        assert ",Scene,motion," not in text
        assert text.startswith("[Script Info]")
