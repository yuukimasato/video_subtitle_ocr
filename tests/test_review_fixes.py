# tests/test_review_fixes.py
"""Regression tests for the 2026-09 code-review fix round.

Covers:
- core/subtitle_generator/generator: ASS body sanitization (braces/CR/LF).
- core/subtitle_generator/timeline: _format_time rounding + fps<=0 guard.
- core/coordinate_restorer: crop-origin clamp parity with roi_extractor.
- core/text_source_classifier: LLM assist dedup cache.
- core/subtitle_generator/styling: multi-line dialogue uses hard breaks.
"""

from __future__ import annotations

import os
import sys

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from core.subtitle_generator import OCRToASSOptimizer
from core.subtitle_generator.generator import _sanitize_ass_body


def _make_optimizer(tmp_path, fps=25.0):
    return OCRToASSOptimizer(
        video_path=str(tmp_path / "in.mp4"),
        output_path=str(tmp_path / "out.ass"),
        fps=fps,
        width=1280,
        height=720,
    )


# ── ASS body sanitization ───────────────────────────────────────

def test_sanitize_ass_body_escapes_braces():
    # ASCII braces would be parsed as override blocks by renderers.
    assert _sanitize_ass_body("{注释}") == "｛注释｝"
    assert _sanitize_ass_body("正常文本") == "正常文本"


def test_sanitize_ass_body_strips_real_line_breaks():
    assert _sanitize_ass_body("第一行\n第二行") == "第一行 第二行"
    assert _sanitize_ass_body("第一行\r\n第二行") == "第一行  第二行"
    assert _sanitize_ass_body("第一行\r第二行") == "第一行 第二行"


def test_sanitize_ass_body_keeps_ass_line_breaks():
    # Intentional ASS hard breaks must survive sanitization.
    assert _sanitize_ass_body("上\\N下") == "上\\N下"


# ── Timeline formatting ─────────────────────────────────────────

def test_format_time_truncates_to_stay_inside_frame_boundary(tmp_path):
    opt = _make_optimizer(tmp_path, fps=30.0)
    # 事件边界来自帧时间戳（或帧时间+帧距）。截断保证写出的厘秒不会越过它
    # 所描述的帧边界：四舍五入会把 59.9667 进位成 59.97+，让事件在下一帧上
    # 仍被画出（切换帧上旧字幕压住新硬字幕一帧）。进位/借位仍由 divmod 完成
    # （…99.99 → 下一秒）。
    assert opt._format_time(1799) == "0:00:59.96"
    assert opt._format_time_seconds(59.999) == "0:00:59.99"
    assert opt._format_time_seconds(60.0) == "0:01:00.00"
    assert opt._format_time(1800) == "0:01:00.00"


def test_format_time_fps_zero_does_not_crash(tmp_path):
    opt = _make_optimizer(tmp_path, fps=0.0)
    assert opt._format_time(100) == "0:00:00.00"


# ── Coordinate restoration clamp ────────────────────────────────

def test_get_roi_offset_clamps_negative_rect_origin():
    from core.coordinate_restorer import _get_roi_offset

    assert _get_roi_offset({"type": "rect", "points": [-10, -5, 100, 50]}) == (0, 0)
    assert _get_roi_offset({"type": "rect", "points": [10, 5, 100, 50]}) == (10, 5)


def test_get_roi_offset_poly_clamps_to_frame():
    from core.coordinate_restorer import _get_roi_offset

    pts = [[-20, -10], [80, -10], [80, 60], [-20, 60]]
    assert _get_roi_offset({"type": "poly", "points": pts}) == (0, 0)


# ── LLM assist dedup cache ──────────────────────────────────────

def test_llm_assist_dedups_same_text():
    from core.classification_features import TextRegionFeatures
    from core.text_source_classifier import TextSource, TextSourceClassifier

    calls = []
    clf = TextSourceClassifier(llm_config={"api_key": "k"})

    def _fake_assist(features, context=None):
        calls.append(features.raw_text)
        return (TextSource.SCENE, 0.9)

    clf._llm_assist = _fake_assist

    result = None
    for _ in range(5):
        feats = TextRegionFeatures(raw_text="疑似水印文本", relative_y=0.05)
        result = clf.classify(feats)

    assert len(calls) == 1
    assert result is not None and result.source == TextSource.SCENE


# ── Multi-line dialogue uses hard breaks ────────────────────────

def test_multiline_bottom_join_uses_hard_break(tmp_path):
    opt = _make_optimizer(tmp_path)
    from core.subtitle_generator.models import SubtitleGroup, TextLine

    g = SubtitleGroup(
        start_frame=10, end_frame=20,
        lines=[
            TextLine(text="第一行", score=0.95, box=(100, 600, 500, 620),
                     polygon=[(100, 600), (500, 600), (500, 620), (100, 620)]),
            TextLine(text="第二行", score=0.95, box=(100, 625, 500, 645),
                     polygon=[(100, 625), (500, 625), (500, 645), (100, 645)]),
        ],
    )
    lines = opt._determine_style_and_position(g)
    assert len(lines) == 1
    assert "\\N" in lines[0]["text"]
    assert "\\n" not in lines[0]["text"]
