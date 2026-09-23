# tests/test_scene_policy_slices.py
"""A2 静态路径的真实入口回归:策略事件按活动行集合切片(生成器入口)。

旧行为(V2 缺陷):external/whitespace 的整块事件时间取全 ROI min/max 并
集——同一文字的两段可见时间之间,NoteBox 仍以联合正文常驻;静态 mask
以块内行时间 min/max 生成。新契约:每片只布局当时有效的行,时间夹在片
内,行离场的间隔不产出事件。
"""

from __future__ import annotations

import os
import sys

import cv2
import numpy as np

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

W, H, FPS = 320, 240, 25.0


def _write_video(tmp_path):
    video = tmp_path / "in.avi"
    frame = np.full((H, W, 3), 250, np.uint8)
    frame[100:106, 40:120] = 20  # 招牌文字笔画
    writer = cv2.VideoWriter(str(video), cv2.VideoWriter_fourcc(*"FFV1"),
                             FPS, (W, H))
    assert writer.isOpened()
    for _ in range(60):
        writer.write(frame)
    writer.release()
    return str(video)


def _ocr_items(sign_box):
    """同一招牌文字的两个分离可见窗口:帧 5-14 与帧 30-39。"""
    poly = [[sign_box[0], sign_box[1]], [sign_box[2], sign_box[1]],
            [sign_box[2], sign_box[3]], [sign_box[0], sign_box[3]]]
    items = []
    for lo, hi in ((5, 14), (30, 39)):
        for f in range(lo, hi + 1):
            data = {
                "dt_polys": [poly], "rec_polys": [poly],
                "rec_texts": ["店铺招牌"], "rec_scores": [0.95],
                "rec_boxes": [list(sign_box)],
            }
            items.append((data, f, "roi_0", f / FPS))
    return items


def _note_lines(text):
    return [ln for ln in text.splitlines() if ln.startswith("Dialogue:")
            and "NoteBox," in ln]


def _times(line):
    fields = line.split(":", 1)[1].split(",", 9)
    return fields[1], fields[2]


def test_external_note_events_follow_active_slices(tmp_path):
    """文字离场的间隔不再产出 NoteBox;两段各自夹在自己的时间片内。"""
    from core.subtitle_generator import OCRToASSOptimizer

    video = _write_video(tmp_path)
    out = tmp_path / "out.ass"
    conv = OCRToASSOptimizer(
        video_path=video, output_path=str(out), fps=FPS, width=W, height=H,
        roi_scene_text_policies={"roi_0": "external"},
        roi_analysis_rects={"roi_0": (20, 80, 140, 130)})
    conv.convert_from_memory(iter(_ocr_items((30, 85, 130, 115))))
    notes = _note_lines(out.read_text(encoding="utf-8-sig"))
    assert len(notes) == 2, notes
    spans = sorted(_times(ln) for ln in notes)
    # 每段紧跟自己的可见窗口(5..14 帧 ≈ [0.20, 0.60);30..39 ≈ [1.20, 1.60));
    # 中间 0.60–1.20 的空档没有任何事件。
    assert spans[0][1] <= "0:00:00.68" and spans[1][0] >= "0:00:01.12", spans


def test_mask_events_follow_active_slices(tmp_path):
    """mask 的遮罩同样按切片时间生成,不再跨空档常驻。"""
    from core.subtitle_generator import OCRToASSOptimizer

    video = _write_video(tmp_path)
    out = tmp_path / "out.ass"
    conv = OCRToASSOptimizer(
        video_path=video, output_path=str(out), fps=FPS, width=W, height=H,
        roi_scene_text_policies={"roi_0": "mask"},
        roi_analysis_rects={"roi_0": (20, 80, 140, 130)})
    conv.convert_from_memory(iter(_ocr_items((30, 85, 130, 115))))
    text = out.read_text(encoding="utf-8-sig")
    masks = [ln for ln in text.splitlines()
             if ln.startswith("Dialogue: 0,") and "\\p1" in ln]
    assert len(masks) == 2, masks
    spans = sorted(_times(ln) for ln in masks)
    assert spans[0][1] <= "0:00:00.68" and spans[1][0] >= "0:00:01.12", spans
    # 文本行(layer 1)同样只在两个窗口内出现
    texts = [ln for ln in text.splitlines()
             if ln.startswith("Dialogue: 1,") and "店铺招牌" in ln]
    assert len(texts) == 2, texts


def test_identity_merge_keeps_same_row_continuous(tmp_path):
    """同一行连续可见(帧 5-24 不断):单一切片、单条事件,不碎裂。"""
    from core.subtitle_generator import OCRToASSOptimizer

    video = _write_video(tmp_path)
    out = tmp_path / "out.ass"
    poly = [[30, 85], [130, 85], [130, 115], [30, 115]]
    items = []
    for f in range(5, 25):
        data = {
            "dt_polys": [poly], "rec_polys": [poly],
            "rec_texts": ["店铺招牌"], "rec_scores": [0.95],
            "rec_boxes": [[30, 85, 130, 115]],
        }
        items.append((data, f, "roi_0", f / FPS))
    conv = OCRToASSOptimizer(
        video_path=video, output_path=str(out), fps=FPS, width=W, height=H,
        roi_scene_text_policies={"roi_0": "external"},
        roi_analysis_rects={"roi_0": (20, 80, 140, 130)})
    conv.convert_from_memory(iter(items))
    notes = _note_lines(out.read_text(encoding="utf-8-sig"))
    assert len(notes) == 1, notes
