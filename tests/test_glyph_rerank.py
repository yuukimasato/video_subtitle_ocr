# tests/test_glyph_rerank.py
"""Unit tests for font_intel.recognizer.glyph_rerank (T3.2 glyph rerank).

Covers: whole-string reference rendering against real system fonts (shape /
ink content / failure tolerance for missing font files, empty text, missing
glyphs, corrupt files and degenerate canvases), the three-way similarity
fusion (SSIM / pHash / HoG+cosine) basic properties (identical images score
~1.0, clearly different glyph images score lower on every metric), rerank
ordering (correct font first from a wrong-first candidate list), candidate
format coercion, safe degradation on empty/invalid inputs, render-cache hit
behaviour (counting wrapper proves underlying renders are not repeated),
LRU capacity eviction, fusion-weight override, and optional-dependency
graceful degradation when cv2/skimage are absent.

Tests use real system TTFs found under /usr/share/fonts (DejaVu /
Liberation families). When none of the required fonts exist the font-bound
cases skip with a reason; pure-logic cases (cache API, invalid inputs,
degradation) never depend on system fonts.
"""

from __future__ import annotations

import logging
import os
import sys

import numpy as np
import pytest

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

import font_intel.recognizer.glyph_rerank as glyph_rerank  # noqa: E402
from font_intel.recognizer.glyph_rerank import (  # noqa: E402
    RankedCandidate,
    ReferenceRenderCache,
    compare_images,
    render_reference,
    rerank,
)

# ---------------------------------------------------------------------------
# real system fonts (skip fixture when unavailable)


FONT_CANDIDATES = {
    "dejavu_sans": "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "dejavu_serif": "/usr/share/fonts/truetype/dejavu/DejaVuSerif.ttf",
    "liberation_sans": (
        "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf"
    ),
    "liberation_serif": (
        "/usr/share/fonts/truetype/liberation/LiberationSerif-Regular.ttf"
    ),
    "liberation_mono": (
        "/usr/share/fonts/truetype/liberation/LiberationMono-Regular.ttf"
    ),
}


@pytest.fixture(scope="module")
def real_fonts() -> dict[str, str]:
    """本机可用的真实系统字体集合；不足两个不同族字体则跳过字形用例。"""
    available = {k: p for k, p in FONT_CANDIDATES.items() if os.path.isfile(p)}
    if len(available) < 2:
        pytest.skip("本机缺少 DejaVu/Liberation 系统字体，字形重排用例跳过")
    return available


@pytest.fixture
def fresh_cache() -> ReferenceRenderCache:
    return ReferenceRenderCache(capacity=64)


def _render_crop(text: str, font_path: str, px: int = 200) -> np.ndarray:
    """模拟视频字块：大字号、黑字白底（与参考图反色，验证极性对齐）。"""
    from PIL import Image, ImageDraw, ImageFont

    face = ImageFont.truetype(font_path, px)
    left, top, right, bottom = face.getbbox(text)
    w, h = right - left + px, bottom - top + px
    canvas = Image.new("L", (w, h), 255)
    ImageDraw.Draw(canvas).text(
        (px // 2 - left, px // 2 - top), text, font=face, fill=0
    )
    return np.asarray(canvas)


# ---------------------------------------------------------------------------
# rendering: shape, content, failures


class TestRenderReference:
    def test_basic_shape_and_ink(self, real_fonts, fresh_cache):
        img = render_reference(
            "Ag", real_fonts["dejavu_sans"], canvas=128, cache=fresh_cache
        )
        assert img is not None
        assert isinstance(img, np.ndarray)
        assert img.ndim == 2
        assert img.shape == (128, 128)
        assert img.dtype == np.uint8
        assert int(img.max()) > 200  # 白字
        assert int(img.min()) == 0  # 黑底
        ink = int((img > 128).sum())
        assert ink > 50  # 有实际笔画而非空白

    def test_canvas_tuple(self, real_fonts, fresh_cache):
        img = render_reference(
            "Ag", real_fonts["dejavu_sans"], canvas=(160, 96), cache=fresh_cache
        )
        assert img is not None
        assert img.shape == (96, 160)

    def test_whole_string_no_per_char_split(self, real_fonts, fresh_cache):
        """多字符整串渲染：字距由字体排版决定，无逐字分割痕迹。"""
        img = render_reference(
            "Vol", real_fonts["liberation_serif"], canvas=192, cache=fresh_cache
        )
        assert img is not None
        assert int((img > 128).sum()) > 100

    def test_missing_font_path(self, fresh_cache):
        assert render_reference("ABC", "/nonexistent/nope.ttf", cache=fresh_cache) is None

    def test_empty_and_blank_text(self, real_fonts, fresh_cache):
        path = real_fonts["dejavu_sans"]
        assert render_reference("", path, cache=fresh_cache) is None
        assert render_reference("   ", path, cache=fresh_cache) is None

    def test_missing_glyph_returns_none(self, real_fonts, fresh_cache):
        """DejaVu 无 CJK 覆盖：缺字形按失败处理，绝不渲染 .notdef 豆腐块。"""
        assert (
            render_reference("字幕", real_fonts["dejavu_sans"], cache=fresh_cache)
            is None
        )

    def test_corrupt_font_file(self, tmp_path, fresh_cache):
        bad = tmp_path / "broken.ttf"
        bad.write_bytes(b"this is not a font file" * 32)
        assert render_reference("ABC", str(bad), cache=fresh_cache) is None

    def test_degenerate_canvas(self, real_fonts, fresh_cache):
        assert (
            render_reference("A", real_fonts["dejavu_sans"], canvas=(0, 64), cache=fresh_cache)
            is None
        )
        assert (
            render_reference("A", real_fonts["dejavu_sans"], canvas=(-8, 64), cache=fresh_cache)
            is None
        )

    def test_never_raises_on_garbage_inputs(self, fresh_cache):
        assert render_reference(None, None, cache=fresh_cache) is None
        assert render_reference("A", 12345, cache=fresh_cache) is None


# ---------------------------------------------------------------------------
# comparison: three metrics + fusion


class TestCompareImages:
    def test_identical_images_score_near_one(self, real_fonts):
        ref = render_reference("R", real_fonts["dejavu_sans"], canvas=128)
        scores = compare_images(ref, ref)
        assert scores["ssim"] > 0.99
        assert scores["phash"] == pytest.approx(1.0)
        assert scores["hog"] > 0.99
        assert scores["score"] > 0.99
        assert set(scores) == {"ssim", "phash", "hog", "score"}

    def test_different_glyphs_score_lower(self, real_fonts):
        m = render_reference("M", real_fonts["dejavu_sans"], canvas=128)
        dot = render_reference(".", real_fonts["dejavu_sans"], canvas=128)
        scores = compare_images(m, dot)
        assert scores["ssim"] < 0.9
        assert scores["phash"] < 0.95
        assert scores["hog"] < 0.9

    def test_polarity_inverted_crop_still_scores_high(self, real_fonts):
        """黑字白底的视频字块经极性对齐后，同字体得分仍应显著高。"""
        text = "Voltage"
        ref = render_reference(text, real_fonts["dejavu_sans"], canvas=128)
        crop = _render_crop(text, real_fonts["dejavu_sans"], px=200)
        same = compare_images(ref, crop)
        assert same["score"] > 0.7

    def test_self_compare_beats_cross_font(self, real_fonts):
        text = "Voltage"
        ref_same = render_reference(text, real_fonts["dejavu_sans"], canvas=128)
        ref_other = render_reference(text, real_fonts["liberation_serif"], canvas=128)
        crop = _render_crop(text, real_fonts["dejavu_sans"], px=200)
        d_same = compare_images(ref_same, crop)
        d_other = compare_images(ref_other, crop)
        assert d_same["score"] > d_other["score"] + 0.05
        assert d_same["ssim"] > d_other["ssim"]

    def test_weights_override(self, real_fonts):
        ref = render_reference("R", real_fonts["dejavu_sans"], canvas=128)
        crop = _render_crop("R", real_fonts["dejavu_sans"], px=160)
        only_ssim = compare_images(ref, crop, weights={"ssim": 1.0, "phash": 0.0, "hog": 0.0})
        assert only_ssim["score"] == pytest.approx(only_ssim["ssim"])

    def test_single_and_multi_char(self, real_fonts):
        path = real_fonts["dejavu_sans"]
        ref1 = render_reference("R", path, canvas=128)
        ref2 = render_reference("ABC DEF", path, canvas=192)
        crop1 = _render_crop("R", path, px=120)
        crop2 = _render_crop("ABC DEF", path, px=120)
        assert compare_images(ref1, crop1)["score"] > 0.5
        assert compare_images(ref2, crop2)["score"] > 0.5

    def test_blank_image_degrades_to_zero(self, real_fonts):
        ref = render_reference("R", real_fonts["dejavu_sans"], canvas=128)
        scores = compare_images(ref, np.full((64, 64), 7, dtype=np.uint8))
        assert scores == {"ssim": 0.0, "phash": 0.0, "hog": 0.0, "score": 0.0}

    @pytest.mark.parametrize(
        "bad_ref",
        [None, np.zeros((0, 0)), np.zeros(4), "not-an-image"],
    )
    def test_invalid_inputs_raise_valueerror(self, real_fonts, bad_ref):
        good = render_reference("R", real_fonts["dejavu_sans"], canvas=64)
        with pytest.raises(ValueError):
            compare_images(bad_ref, good)
        with pytest.raises(ValueError):
            compare_images(good, bad_ref)


# ---------------------------------------------------------------------------
# rerank


class TestRerank:
    def test_correct_font_first_from_wrong_first_list(self, real_fonts, fresh_cache):
        text = "Voltage"
        target = real_fonts["dejavu_sans"]
        crop = _render_crop(text, target, px=200)
        candidates = [
            {"name": "Liberation Serif", "font_path": real_fonts["liberation_serif"]},
            {"name": "DejaVu Sans", "font_path": target},
            {"name": "Liberation Mono", "font_path": real_fonts["liberation_mono"]},
        ]
        ranked = rerank(text, crop, candidates, font_index=fresh_cache)
        assert len(ranked) == 3
        assert isinstance(ranked[0], RankedCandidate)
        assert ranked[0].name == "DejaVu Sans"
        assert ranked[0].font_path == target
        assert ranked[0].score > ranked[1].score
        assert set(ranked[0].per_metric) == {"ssim", "phash", "hog"}
        scores = [c.score for c in ranked]
        assert scores == sorted(scores, reverse=True)

    def test_tuple_and_str_candidate_formats(self, real_fonts, fresh_cache):
        text = "Voltage"
        target = real_fonts["dejavu_sans"]
        crop = _render_crop(text, target, px=200)
        candidates = [
            ("Liberation Serif", real_fonts["liberation_serif"]),
            target,  # 纯路径字符串：name 取文件名主干
            {"font_path": real_fonts["liberation_mono"]},  # 无 name：回退主干名
        ]
        ranked = rerank(text, crop, candidates, font_index=fresh_cache)
        assert [c.font_path for c in ranked] == [
            target,
            real_fonts["liberation_mono"],
            real_fonts["liberation_serif"],
        ]
        by_path = {c.font_path: c for c in ranked}
        assert by_path[target].name == "DejaVuSans"

    def test_unrenderable_candidate_sinks_without_raising(self, real_fonts, fresh_cache):
        text = "Voltage"
        target = real_fonts["dejavu_sans"]
        crop = _render_crop(text, target, px=200)
        candidates = [
            {"name": "Ghost", "font_path": "/nonexistent/ghost.ttf"},
            {"name": "DejaVu Sans", "font_path": target},
        ]
        ranked = rerank(text, crop, candidates, font_index=fresh_cache)
        assert len(ranked) == 2
        assert ranked[0].name == "DejaVu Sans"
        assert ranked[1].score == 0.0
        assert ranked[1].per_metric == {}

    def test_empty_candidates(self, fresh_cache):
        assert rerank("ABC", np.zeros((32, 32)), [], font_index=fresh_cache) == []

    def test_invalid_crop_degrades_preserving_order(self, real_fonts, fresh_cache, caplog):
        candidates = [
            ("A-font", real_fonts["liberation_serif"]),
            ("B-font", real_fonts["dejavu_sans"]),
        ]
        with caplog.at_level(logging.WARNING):
            for bad_crop in (None, np.zeros((0, 0)), "junk"):
                ranked = rerank("Voltage", bad_crop, candidates, font_index=fresh_cache)
                assert [c.name for c in ranked] == ["A-font", "B-font"]
                assert all(c.score == 0.0 for c in ranked)
        assert "warning" in caplog.text.lower() or "降级" in caplog.text

    def test_empty_text_degrades(self, real_fonts, fresh_cache, caplog):
        candidates = [("B-font", real_fonts["dejavu_sans"])]
        with caplog.at_level(logging.WARNING):
            ranked = rerank("", np.zeros((32, 32), np.uint8), candidates, font_index=fresh_cache)
        assert [c.name for c in ranked] == ["B-font"]
        assert ranked[0].score == 0.0

    def test_weights_override_propagates(self, real_fonts, fresh_cache):
        text = "Voltage"
        target = real_fonts["dejavu_sans"]
        crop = _render_crop(text, target, px=200)
        candidates = [
            ("Liberation Serif", real_fonts["liberation_serif"]),
            ("DejaVu Sans", target),
        ]
        ranked = rerank(
            text,
            crop,
            candidates,
            font_index=fresh_cache,
            weights={"ssim": 1.0, "phash": 0.0, "hog": 0.0},
        )
        assert ranked[0].name == "DejaVu Sans"
        assert ranked[0].score == pytest.approx(ranked[0].per_metric["ssim"])

    def test_module_level_fusion_weights_constant(self):
        assert glyph_rerank.SSIM_WEIGHT + glyph_rerank.PHASH_WEIGHT + glyph_rerank.HOG_WEIGHT == pytest.approx(1.0)


# ---------------------------------------------------------------------------
# render cache


class TestRenderCache:
    def test_cache_hit_avoids_repeated_render(self, real_fonts, monkeypatch):
        calls = {"n": 0}
        real_fn = glyph_rerank._render_reference_uncached

        def counting(text, font_path, canvas):
            calls["n"] += 1
            return real_fn(text, font_path, canvas)

        monkeypatch.setattr(glyph_rerank, "_render_reference_uncached", counting)
        cache = ReferenceRenderCache(capacity=8)
        first = render_reference("Ag", real_fonts["dejavu_sans"], canvas=128, cache=cache)
        second = render_reference("Ag", real_fonts["dejavu_sans"], canvas=128, cache=cache)
        assert calls["n"] == 1
        assert first is not None and second is not None
        assert np.array_equal(first, second)
        # 同 key、不同 canvas：缓存不命中，补渲染
        render_reference("Ag", real_fonts["dejavu_sans"], canvas=192, cache=cache)
        assert calls["n"] == 2
        # 失败结果同样入缓存：两次缺字体路径只触底一次
        render_reference("Ag", "/nonexistent/x.ttf", canvas=128, cache=cache)
        render_reference("Ag", "/nonexistent/x.ttf", canvas=128, cache=cache)
        assert calls["n"] == 3

    def test_rerank_shares_crop_normalization_and_cache(self, real_fonts, fresh_cache, monkeypatch):
        """同一批候选重复 rerank 不重复渲染参考字形。"""
        calls = {"n": 0}
        real_fn = glyph_rerank._render_reference_uncached

        def counting(text, font_path, canvas):
            calls["n"] += 1
            return real_fn(text, font_path, canvas)

        monkeypatch.setattr(glyph_rerank, "_render_reference_uncached", counting)
        text = "Voltage"
        target = real_fonts["dejavu_sans"]
        crop = _render_crop(text, target, px=200)
        candidates = [
            ("Liberation Serif", real_fonts["liberation_serif"]),
            ("DejaVu Sans", target),
        ]
        first = rerank(text, crop, candidates, font_index=fresh_cache)
        assert calls["n"] == 2  # 每个候选恰好渲染一次
        second = rerank(text, crop, candidates, font_index=fresh_cache)
        assert calls["n"] == 2  # 全部命中缓存
        assert first[0].name == second[0].name == "DejaVu Sans"

    def test_lru_capacity_eviction(self):
        cache = ReferenceRenderCache(capacity=1)
        img_a = np.full((8, 8), 1, np.uint8)
        img_b = np.full((8, 8), 2, np.uint8)
        cache.put("/a.ttf", "A", (64, 64), img_a)
        assert cache.get("/a.ttf", "A", (64, 64)) == (True, img_a)
        cache.put("/b.ttf", "B", (64, 64), img_b)
        hit_a, _ = cache.get("/a.ttf", "A", (64, 64))
        assert hit_a is False  # 容量 1：a 已被 b 淘汰
        assert cache.get("/b.ttf", "B", (64, 64)) == (True, img_b)

    def test_invalid_capacity_rejected(self):
        with pytest.raises(ValueError):
            ReferenceRenderCache(capacity=0)

    def test_none_cache_bypasses(self, real_fonts):
        """cache=None 直连渲染，不写模块缓存。"""
        before = len(glyph_rerank.DEFAULT_RENDER_CACHE._entries)
        img = render_reference("Ag", real_fonts["dejavu_sans"], canvas=72, cache=None)
        assert img is not None
        assert len(glyph_rerank.DEFAULT_RENDER_CACHE._entries) == before


# ---------------------------------------------------------------------------
# optional dependency degradation


class TestOptionalDependencyDegradation:
    def test_compare_images_without_cv2_skimage(self, real_fonts, monkeypatch, caplog):
        monkeypatch.setattr(glyph_rerank, "_CV2_AVAILABLE", False)
        monkeypatch.setattr(glyph_rerank, "_SKIMAGE_AVAILABLE", False)
        ref = render_reference("R", real_fonts["dejavu_sans"], canvas=128)
        with caplog.at_level(logging.WARNING):
            scores = compare_images(ref, ref)
        assert scores == {"ssim": 0.0, "phash": 0.0, "hog": 0.0, "score": 0.0}
        assert "warning" in caplog.text.lower() or "降级" in caplog.text

    def test_rerank_without_cv2_skimage_preserves_order(
        self, real_fonts, fresh_cache, monkeypatch, caplog
    ):
        monkeypatch.setattr(glyph_rerank, "_CV2_AVAILABLE", False)
        monkeypatch.setattr(glyph_rerank, "_SKIMAGE_AVAILABLE", False)
        crop = _render_crop("Voltage", real_fonts["dejavu_sans"], px=200)
        candidates = [
            ("Liberation Serif", real_fonts["liberation_serif"]),
            ("DejaVu Sans", real_fonts["dejavu_sans"]),
        ]
        with caplog.at_level(logging.WARNING):
            ranked = rerank("Voltage", crop, candidates, font_index=fresh_cache)
        assert [c.name for c in ranked] == ["Liberation Serif", "DejaVu Sans"]
        assert all(c.score == 0.0 for c in ranked)


# ---------------------------------------------------------------------------
# honest positioning


def test_module_docstring_states_rerank_not_recognizer():
    doc = (glyph_rerank.__doc__ or "").strip()
    assert "重排" in doc
    assert "不是" in doc or "而非" in doc
