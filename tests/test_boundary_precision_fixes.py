# tests/test_boundary_precision_fixes.py
"""边界精度与文本抖动修复的回归测试。

覆盖：
- 帧分组：尾随省略号抖动不再碎裂；短组吸收进相邻同文本组。
- 载入阶段：同一行拆分框合并；底部带幻影噪声行剔除。
- 事件合并：子串相似的时间间隔护栏；同 ROI 相邻事件端点齐平。
- 生成器：代表行逐槽多数投票（双行字幕丢行/省略号完整性）。
"""

from __future__ import annotations

import os
import sys

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from core.subtitle_generator import OCRToASSOptimizer
from core.subtitle_generator.data_grouping import strip_flicker_punctuation
from core.subtitle_generator.models import FrameData, SubtitleGroup, TextLine


def _make_optimizer(tmp_path, fps=23.976, width=1920, height=1080):
    return OCRToASSOptimizer(
        video_path=str(tmp_path / "in.mp4"),
        output_path=str(tmp_path / "out.ass"),
        fps=fps,
        width=width,
        height=height,
    )


def _line(text, box, score=0.99):
    x0, y0, x1, y1 = box
    return TextLine(
        text=text,
        score=score,
        box=box,
        polygon=[(x0, y0), (x1, y0), (x1, y1), (x0, y1)],
    )


def _group(frames):
    frames = sorted(frames, key=lambda f: f.frame_num)
    return SubtitleGroup(
        start_frame=frames[0].frame_num,
        end_frame=frames[-1].frame_num,
        lines=list(frames[0].lines),
        frames=list(frames),
    )


def _frame(frame_num, time_sec, lines):
    return FrameData(frame_num=frame_num, time_sec=time_sec, lines=list(lines))


# ── strip_flicker_punctuation ───────────────────────────────────

def test_strip_flicker_punctuation_removes_trailing_ellipsis():
    assert strip_flicker_punctuation("好熱…") == "好熱"
    assert strip_flicker_punctuation("好熱·.") == "好熱"
    assert strip_flicker_punctuation("好熱") == "好熱"
    assert strip_flicker_punctuation("") == ""


# ── 帧分组：省略号抖动 + 短组吸收 ────────────────────────────────

def test_grouping_merges_ellipsis_flicker_frames(tmp_path):
    opt = _make_optimizer(tmp_path)
    t0 = 55.0
    frames = []
    texts = ["好熱", "好熱…", "好熱….", "好熱", "好熱…", "好熱…"]
    for i, txt in enumerate(texts):
        frames.append(_frame(10 + i, t0 + i / 24.0, [_line(txt, (800, 980, 1100, 1040))]))
    groups = opt._group_consecutive_frames(frames)
    assert len(groups) == 1
    assert groups[0].start_frame == 10
    assert groups[0].end_frame == 15


def test_short_fade_in_group_absorbed_into_adjacent_group(tmp_path):
    opt = _make_optimizer(tmp_path)
    frames = [
        # 淡入首帧读作 "好熱…"，其后 4 帧读作 "好熱"：孤帧不应被丢弃。
        _frame(100, 4.0, [_line("好熱…", (800, 980, 1100, 1040))]),
        _frame(102, 4.1, [_line("好熱", (800, 980, 1100, 1040))]),
        _frame(103, 4.15, [_line("好熱", (800, 980, 1100, 1040))]),
        _frame(104, 4.2, [_line("好熱", (800, 980, 1100, 1040))]),
        _frame(105, 4.25, [_line("好熱", (800, 980, 1100, 1040))]),
    ]
    groups = opt._group_consecutive_frames(frames)
    assert len(groups) == 1
    assert groups[0].start_frame == 100
    assert groups[0].end_frame == 105


def test_dissimilar_short_group_still_dropped(tmp_path):
    opt = _make_optimizer(tmp_path)
    frames = [
        _frame(10, 0.4, [_line("嘸", (800, 980, 1100, 1040))]),
        _frame(11, 0.45, [_line("嘸", (800, 980, 1100, 1040))]),
        _frame(20, 0.85, [_line("完全不同的字幕", (700, 980, 1200, 1040))]),
        _frame(21, 0.9, [_line("完全不同的字幕", (700, 980, 1200, 1040))]),
        _frame(22, 0.95, [_line("完全不同的字幕", (700, 980, 1200, 1040))]),
        _frame(23, 1.0, [_line("完全不同的字幕", (700, 980, 1200, 1040))]),
    ]
    groups = opt._group_consecutive_frames(frames)
    assert len(groups) == 1
    assert groups[0].start_frame == 20


# ── 载入阶段：同行框合并 + 噪声行剔除 ────────────────────────────

def test_same_row_split_boxes_merged_with_space(tmp_path):
    opt = _make_optimizer(tmp_path)
    left = _line("什麼意思", (700, 980, 900, 1040))
    right = _line("我是哪種人", (940, 982, 1180, 1038))
    merged = opt._merge_same_row_lines([left, right])
    assert len(merged) == 1
    # 间隙 40px 约为字高的 2/3 → 补空格。
    assert merged[0].text == "什麼意思 我是哪種人"


def test_same_row_split_boxes_merged_without_space(tmp_path):
    opt = _make_optimizer(tmp_path)
    left = _line("有什麼關係", (700, 980, 900, 1040))
    right = _line("很有趣啊", (904, 980, 1060, 1040))
    merged = opt._merge_same_row_lines([left, right])
    assert len(merged) == 1
    assert merged[0].text == "有什麼關係很有趣啊"


def test_same_row_small_gap_joined_with_space(tmp_path):
    # 实测案例（163s）：一行内左右两框，净空 12px、字高约 63px —— 原文有空格。
    opt = _make_optimizer(tmp_path)
    left = _line("別想那麼多", (651, 966, 950, 1029))
    right = _line("快點交往啦", (962, 964, 1271, 1034))
    merged = opt._merge_same_row_lines([left, right])
    assert len(merged) == 1
    assert merged[0].text == "別想那麼多 快點交往啦"


def test_scene_region_same_row_boxes_not_merged(tmp_path):
    # 场景区的画面文字保留独立框与位置，不做同行合并。
    opt = _make_optimizer(tmp_path)
    a = _line("左側招牌", (300, 500, 600, 560))
    b = _line("右側招牌", (640, 502, 900, 558))
    merged = opt._merge_same_row_lines([a, b])
    assert len(merged) == 2


def test_band_junk_line_filtered_but_scene_kept(tmp_path):
    opt = _make_optimizer(tmp_path)
    junk_char = _line("C", (1500, 990, 1530, 1040))       # 底部带单字母 → 剔除
    junk_ascii = _line("YE", (300, 995, 360, 1035))       # 底部带纯 ASCII → 剔除
    kept_short = _line("嗯。", (800, 980, 900, 1040))      # 单字+标点 → 保留
    scene_char = _line("福", (900, 500, 960, 560))         # 场景区单字 → 保留
    assert opt._is_band_junk_line(junk_char)
    assert opt._is_band_junk_line(junk_ascii)
    assert not opt._is_band_junk_line(kept_short)
    assert not opt._is_band_junk_line(scene_char)


# ── 事件合并：子串间隔护栏 + 端点齐平 ────────────────────────────

def _ev(roi, start, end, body, style="CH", tags=""):
    return {
        "roi": roi, "start_time": start, "end_time": end,
        "style": style, "tags": tags, "body": body,
    }


def test_substring_merge_blocked_when_gap_too_large(tmp_path):
    opt = _make_optimizer(tmp_path)
    events = [
        _ev("roi_0", "0:02:52.01", "0:02:52.86", "真的假的？"),
        _ev("roi_0", "0:02:53.53", "0:02:54.53", "真的"),
    ]
    merged = opt._merge_temporal_near_duplicate_events(events)
    assert len(merged) == 2
    assert merged[0]["body"] == "真的假的？"
    assert merged[1]["body"] == "真的"


def test_substring_merge_still_works_for_contiguous_flicker(tmp_path):
    opt = _make_optimizer(tmp_path)
    events = [
        _ev("roi_0", "0:00:55.10", "0:00:55.39", "好熱"),
        _ev("roi_0", "0:00:55.39", "0:00:55.64", "好熱…"),
        _ev("roi_0", "0:00:55.64", "0:00:57.10", "好熱"),
    ]
    merged = opt._merge_temporal_near_duplicate_events(events)
    assert len(merged) == 1
    assert merged[0]["start_time"] == "0:00:55.10"
    assert merged[0]["end_time"] == "0:00:57.10"
    assert merged[0]["body"] == "好熱…"


def test_same_roi_event_endpoints_clamped(tmp_path):
    opt = _make_optimizer(tmp_path)
    events = [
        _ev("roi_0", "0:00:57.09", "0:00:57.10", "好熱"),
        _ev("roi_0", "0:00:57.09", "0:01:04.60", "祝你生日快樂"),
    ]
    merged = opt._merge_temporal_near_duplicate_events(events)
    assert merged[0]["end_time"] == "0:00:57.09"
    assert merged[1]["start_time"] == "0:00:57.09"


# ── 代表行逐槽多数投票 ──────────────────────────────────────────

def test_representative_lines_vote_restores_missing_second_line(tmp_path):
    opt = _make_optimizer(tmp_path)
    frames = []
    # 前 5 帧只有下行，后 5 帧两行齐全：行数众数 2，应恢复两行。
    for i in range(5):
        frames.append(_frame(i, 0.0 + i / 24.0, [
            _line("打死也不肯離開這裡的樣子", (600, 1020, 1300, 1070)),
        ]))
    for i in range(5, 10):
        frames.append(_frame(i, 0.0 + i / 24.0, [
            _line("你現在就一副", (700, 960, 1100, 1010)),
            _line("打死也不肯離開這裡的樣子", (600, 1020, 1300, 1070)),
        ]))
    group = _group(frames)
    lines = opt._select_representative_lines(group)
    assert [line.text for line in lines] == ["你現在就一副", "打死也不肯離開這裡的樣子"]


def test_representative_lines_vote_prefers_longer_reading(tmp_path):
    opt = _make_optimizer(tmp_path)
    frames = []
    texts = ["我不太喜歡菸味", "我不太喜歡菸味…", "我不太喜歡菸味", "我不太喜歡菸味…"]
    for i, txt in enumerate(texts):
        frames.append(_frame(i, i / 24.0, [_line(txt, (800, 980, 1100, 1040))]))
    lines = opt._select_representative_lines(_group(frames))
    assert len(lines) == 1
    assert lines[0].text == "我不太喜歡菸味…"
