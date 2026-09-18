# tests/test_review_fixes_3.py
"""2026-09 第二轮代码审查修复的回归测试。

覆盖：
- motion_ass：单帧链不再被零长守卫整条丢弃(尾帧延伸先行)
- scene_text_policy：遮罩事件同款单帧链修复；wrap_cjk 禁则下挪不产空行
- subtitle_generator/llm_merge：策略事件(policy=True)不进 LLM 碎片合并
- subtitle_generator/styling：模板路径同样补齐 NoteBox 样式
- motion_detector：region 越过画面左/上边缘时 OCR 框不再整体平移
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import cv2
import numpy as np
import pytest

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from core.motion_ass import (  # noqa: E402
    MotionAssConfig,
    build_line_tracks,
    format_ass_time,
    synthesize_events,
)
from core.scene_plane_tracker import TrackedQuad  # noqa: E402
from core.scene_text_policy import (  # noqa: E402
    _mask_events_for_block,
    wrap_cjk,
)
from core.subtitle_generator.styling import _StylingMixin  # noqa: E402
from core.subtitle_generator.llm_merge import _LlmMergeMixin  # noqa: E402
from core.subtitle_llm_polish import SubtitlePolisherConfig  # noqa: E402

FPS = 25.0
BOX = (100.0, 100.0, 200.0, 150.0)


# ── 合成轨迹:仅一帧 ok(闪烁跟踪/遮挡恢复的典型形态) ────────────────


def _single_ok_tracks(n: int = 6, ok_frame: int = 2) -> list:
    eye = np.eye(3, dtype=np.float64)
    tracks = []
    for i in range(n):
        t_sec = i / FPS
        if i != ok_frame:
            tracks.append(TrackedQuad(frame_num=i, time_sec=t_sec, status="lost"))
            continue
        tracks.append(TrackedQuad(
            frame_num=i, time_sec=t_sec, status="ok",
            quad=[[100.0, 100.0], [200.0, 100.0], [200.0, 150.0], [100.0, 150.0]],
            homography=[[float(v) for v in row] for row in eye],
            homography_inv=[[float(v) for v in row] for row in eye],
        ))
    return tracks


def test_motion_single_pose_chain_yields_event():
    # 回归:单帧链 segs=[(0,0)] 时 f1 == f0,旧代码在链尾延伸前判零长,
    # 整条链丢成 0 事件——只在一帧上跟踪成功的字幕完全消失。
    tracks = _single_ok_tracks()
    line_tracks = build_line_tracks([BOX], ["一瞬"], tracks, ref_frame=2)
    events = synthesize_events(line_tracks, tracks, MotionAssConfig(),
                               style="Scene")
    assert len(events) == 1
    ev = events[0]
    assert ev["start_time"] == format_ass_time(2 / FPS)
    # 链尾延伸到轨迹里下一帧(lost 帧也有时间戳)
    assert ev["end_time"] == format_ass_time(3 / FPS)
    assert ev["body"] == "一瞬"


def test_mask_single_pose_chain_yields_event():
    # 回归:遮罩事件与文本事件同源的单帧链丢弃——该帧原字既无遮罩也无
    # 替换文本,直接透出。
    tracks = _single_ok_tracks()
    events = _mask_events_for_block(
        BOX, (16, 16, 16), tracks, MotionAssConfig(),
        ref_frame=2, style="Mask")
    assert len(events) == 1
    ev = events[0]
    assert ev["start_time"] == format_ass_time(2 / FPS)
    assert ev["end_time"] == format_ass_time(3 / FPS)
    assert ev["layer"] == 0


# ── wrap_cjk:行首禁则下挪不产生空行 ─────────────────────────────────


def test_wrap_cjk_kinsoku_carry_no_empty_line():
    # max_chars=1(极窄带宽)时旧行为把 line[:-1] 的空串写进行表;修复后
    # 禁则字符连同上一字符下挪,宁超限不空行。
    assert wrap_cjk("あ。", 1) == ["あ。"]
    assert wrap_cjk("」。", 1) == ["」。"]
    assert all(line for line in wrap_cjk("あ。x。", 1))


# ── llm_merge:策略事件不参与碎片合并 ─────────────────────────────────


class _MergeHost(_LlmMergeMixin):
    def _parse_ass_time_to_seconds(self, t: str) -> float:
        h, m, s = str(t).split(":")
        return int(h) * 3600 + int(m) * 60 + float(s)

    def _scene_tags_close(self, a: str, b: str) -> bool:
        return True

    def _dialogue_time_gap_seconds(self, end: str, start: str) -> float:
        return self._parse_ass_time_to_seconds(start) - self._parse_ass_time_to_seconds(end)

    def _is_noise_body(self, body: str) -> bool:
        return False

    def _median_scene_tags(self, grp) -> str:
        return grp[0].get("tags", "")


def _policy_scene(start: str, end: str, layer: int) -> dict:
    return {
        "roi": "roi_0",
        "start_time": start,
        "end_time": end,
        "style": "Scene",
        "tags": "{\\an5\\pos(100,900)}",
        "body": "",
        "policy": True,
        "layer": layer,
    }


def test_llm_merge_keeps_policy_events_intact():
    # 两条 Scene 策略事件(时间/位置都满足合并条件):修复前会被 LLM 碎片
    # 合并重建为普通 dict,丢掉 policy/layer 标记。指向不可达端口的 cfg
    # 保证:若仍进入合并,请求必然失败而非静默通过。
    host = _MergeHost()
    ev0 = _policy_scene("0:00:01.00", "0:00:01.10", layer=0)
    ev1 = _policy_scene("0:00:01.10", "0:00:01.30", layer=1)
    cfg = SubtitlePolisherConfig(
        api_key="test-key",
        api_base_url="http://127.0.0.1:9/v1",
        model="fake-model",
        fragment_merge_enabled=True,
    )
    out = host._llm_merge_fragmented_events([ev0, ev1], cfg)
    assert len(out) == 2
    for ev in out:
        assert ev.get("policy") is True
        assert ev.get("layer") in (0, 1)


# ── styling:模板路径补齐 NoteBox 样式 ────────────────────────────────


_TEMPLATE = """[Script Info]
Title: t
PlayResX: 1
PlayResY: 1

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,Font,36,&H00FFFFFF,&H000000FF,&H00000000,&H00000000,0,0,0,0,100,100,0,0,1,2,1,2,10,10,10,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""


class _Sty(_StylingMixin):
    def __init__(self, policies=None, motion_events=None, template_path=None):
        self.roi_scene_text_policies = policies or {}
        self.motion_events = motion_events or []
        self.template_path = template_path
        self.width = 640
        self.height = 360
        self.video_path = Path("v.mp4")


def test_template_header_gets_notebox_when_policy_active(tmp_path):
    tpl = tmp_path / "t.ass"
    tpl.write_text(_TEMPLATE, encoding="utf-8")
    sty = _Sty(policies={"roi_0": "mask"}, template_path=tpl)
    header = sty._get_ass_header()
    assert "Style: NoteBox" in header
    # 样式行必须在 [Events] 之前(样式段内)
    assert header.index("Style: NoteBox") < header.index("[Events]")


def test_template_header_unchanged_when_all_overlap(tmp_path):
    tpl = tmp_path / "t.ass"
    tpl.write_text(_TEMPLATE, encoding="utf-8")
    sty = _Sty(policies={"roi_0": "overlap"}, template_path=tpl)
    assert "NoteBox" not in sty._get_ass_header()


def test_template_with_own_notebox_not_duplicated(tmp_path):
    tpl = tmp_path / "t.ass"
    tpl.write_text(
        _TEMPLATE.replace("Style: Default,", "Style: NoteBox,Font,36,")
        .replace("[Events]", "[Events]", 1),
        encoding="utf-8")
    sty = _Sty(policies={"roi_0": "mask"}, template_path=tpl)
    assert sty._get_ass_header().count("Style: NoteBox") == 1


def test_default_header_still_adds_notebox_for_motion_events():
    sty = _Sty(motion_events=[{"style": "NoteBox", "tags": "m", "body": ""}])
    assert "Style: NoteBox" in sty._get_ass_header()


# ── motion_detector:region 越过画面左缘时框不偏移 ────────────────────

_FRAME_W, _FRAME_H, _N_FRAMES = 320, 240, 24


def _write_moving_bar_video(path) -> str:
    writer = cv2.VideoWriter(
        str(path), cv2.VideoWriter_fourcc(*"FFV1"), 8.0,
        (_FRAME_W, _FRAME_H))
    if not writer.isOpened():
        pytest.skip("no usable video codec (FFV1) for motion detector tests")
    for i in range(_N_FRAMES):
        frame = np.full((_FRAME_H, _FRAME_W, 3), 200, np.uint8)
        x1 = 40 + 4 * i
        cv2.rectangle(frame, (x1, 100), (x1 + 50, 114), (10, 10, 10), -1)
        writer.write(frame)
    writer.release()
    return str(path)


def _bar_ocr(img):
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    mask = (gray < 60).astype(np.uint8)
    contours, _ = cv2.findContours(
        mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    rects = sorted(
        (cv2.boundingRect(c) for c in contours if cv2.contourArea(c) >= 30),
        key=lambda r: (r[1], r[0]))
    ocr = {"dt_polys": [], "rec_polys": [], "rec_texts": [],
           "rec_scores": [], "rec_boxes": []}
    for k, (x, y, w, h) in enumerate(rects):
        poly = [[float(x), float(y)], [float(x + w), float(y)],
                [float(x + w), float(y + h)], [float(x), float(y + h)]]
        ocr["rec_polys"].append(poly)
        ocr["rec_texts"].append(f"bar{k}")
        ocr["rec_scores"].append(0.99)
    return ocr


def test_detect_region_negative_origin_not_shifted(tmp_path):
    # region x1=-20 越过画面左缘:裁剪起点钳到 0,OCR 框偏移量必须用钳后
    # 值;旧代码用未钳的 -20,检出区域整体左移 20px(40 → 20)。
    video = _write_moving_bar_video(tmp_path / "bar.avi")
    from core.motion_detector import detect_moving_text

    regions = detect_moving_text(
        video, ocr_fn=_bar_ocr, region=(-20, 0, 300, _FRAME_H),
        sample_stride_sec=0.5, move_thresh_px=24.0)
    assert regions, "moving bar not detected"
    x1, _y1, _x2, _y2 = regions[0].bbox()
    # 条起点 x=40,外扩 pad=0.5*14=7 → 期望 ≈33;偏移 bug 会给 ≈13。
    assert x1 == pytest.approx(33.0, abs=3.0)


@pytest.mark.parametrize("region", [(-20, 0, 300, _FRAME_H), (0, 0, 300, _FRAME_H)])
def test_detect_region_shift_invariant(tmp_path, region):
    # 裁剪窗左缘从 -20 收到 0,检出内容(条的真实画面位置)应完全一致。
    video = _write_moving_bar_video(tmp_path / "bar.avi")
    from core.motion_detector import detect_moving_text

    regions = detect_moving_text(
        video, ocr_fn=_bar_ocr, region=region,
        sample_stride_sec=0.5, move_thresh_px=24.0)
    assert regions, region
    bbox = regions[0].bbox()
    assert bbox == pytest.approx((32.5, 92.5, 178.5, 122.5), abs=2.0)
