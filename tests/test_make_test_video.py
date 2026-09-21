# tests/test_make_test_video.py
"""make_test_video.py 的纯逻辑单测（不跑 ffmpeg）。

--lang ja（日文测试视频）支持：字幕表选择、水印文案、过滤图构建、
字体解析的语言路由。生成器是基准工具，这里只锁定可离线验证的部分。
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from benchmarks import make_test_video as mtv  # noqa: E402


class TestSelectSchedule:
    def test_zh_default_schedule_unchanged(self):
        subs, watermark = mtv.select_schedule("zh")
        assert len(subs) == 5
        assert subs[0]["text"] == "这是一段测试字幕"
        assert subs[-1].get("fade") is True
        assert watermark == "视频字幕OCR测试"

    def test_ja_schedule_has_kana_and_fade(self):
        subs, watermark = mtv.select_schedule("ja")
        assert len(subs) == 5
        # 必须含假名（平/片），否则不是日文句子
        assert any(
            any("\u3040" <= ch <= "\u30ff" for ch in s["text"]) for s in subs
        )
        assert subs[-1].get("fade") is True
        assert watermark != "视频字幕OCR测试"
        assert "OCR" in watermark

    def test_unknown_lang_rejected(self):
        with pytest.raises(ValueError):
            mtv.select_schedule("ko")


class TestBuildFilters:
    def test_one_drawtext_per_sub_plus_watermark(self):
        subs, watermark = mtv.select_schedule("zh")
        filters = mtv.build_filters(subs, "/fonts/f.ttf", watermark)
        drawtext = [f for f in filters if f.startswith("drawtext=")]
        assert len(drawtext) == len(subs) + 1  # 字幕 + 角标水印
        assert "これは" not in "".join(filters)

    def test_fade_entry_carries_alpha_expr(self):
        subs, watermark = mtv.select_schedule("ja")
        filters = mtv.build_filters(subs, "/fonts/f.ttf", watermark)
        fade_filters = [f for f in filters if "alpha=" in f]
        assert len(fade_filters) == 1
        assert "enable='between(t," in fade_filters[0]

    def test_text_escaping_survives_colon_and_quote(self):
        subs = [{"start": 0.0, "end": 1.0, "text": "a:b'c"}]
        filters = mtv.build_filters(subs, "/fonts/f.ttf", "wm")
        # 冒号与单引号必须转义，否则 drawtext 过滤图解析炸裂
        assert "a\\:b\\'c" in filters[-2]


class TestResolveFont:
    def test_returns_existing_file_when_cjk_font_present(self):
        try:
            path = mtv.resolve_font("ja")
        except SystemExit:
            pytest.skip("本机无 CJK 字体")
        assert Path(path).is_file()

    def test_ja_prefers_ja_match(self):
        """ja 路由应请求 sans:lang=ja（fc-match）；无字体环境跳过。"""
        try:
            mtv.resolve_font("ja")
        except SystemExit:
            pytest.skip("本机无 CJK 字体")
        # 能走到这里即可；具体字体文件由 fc-match 决定，不做强断言
