# tests/test_fontlib_index.py
"""Unit tests for font_intel.fontlib_index (T1.2 local font library index).

Covers: optional-dependency graceful degradation (no fontTools/Pillow ->
empty scan + warning, never raises), default font directory discovery,
recursive scan with extension filtering, corrupt-file tolerance, fontTools
metadata extraction against programmatically built synthetic fonts
(FontBuilder: family / aliases / vendor / license sniffing / usWeightClass),
strict license-category policy (unrecognized -> unknown, never
free_commercial), sanitize_url integration, TTC first-face handling,
reference glyph rendering + LRU glyph cache against a real system font,
and build_index stats / idempotent seed import.
"""

from __future__ import annotations

import glob
import logging
import os
import sys

import pytest

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

import font_intel.fontlib_index as fontlib_index  # noqa: E402
from font_intel.fonts_db import FontsDB  # noqa: E402

OFL_TEXT = "This font is licensed under the SIL Open Font License 1.1"


# ---------------------------------------------------------------------------
# helpers


def _build_test_font(
    path,
    family="TestFont CI",
    subfamily="Regular",
    manufacturer="TestLab",
    license_text=OFL_TEXT,
    license_url="https://example.com/ofl",
    weight=400,
) -> str:
    """用 FontBuilder 程序化构造最小 TTF（不依赖系统字体即可测元数据提取）。"""
    from fontTools.fontBuilder import FontBuilder
    from fontTools.pens.ttGlyphPen import TTGlyphPen

    fb = FontBuilder(1000, isTTF=True)
    fb.setupGlyphOrder([".notdef", "A"])
    fb.setupCharacterMap({0x41: "A"})
    pen = TTGlyphPen(None)
    pen.moveTo((50, 0))
    pen.lineTo((50, 700))
    pen.lineTo((450, 700))
    pen.closePath()
    fb.setupGlyf({".notdef": TTGlyphPen(None).glyph(), "A": pen.glyph()})
    fb.setupHorizontalMetrics({".notdef": (600, 0), "A": (600, 0)})
    fb.setupHorizontalHeader(ascent=800, descent=-200)
    fb.setupNameTable({"familyName": family, "styleName": subfamily})
    nt = fb.font["name"]
    nt.setName(f"{family} {subfamily}", 4, 3, 1, 0x409)
    nt.setName(f"{family.replace(' ', '')}-{subfamily}", 6, 3, 1, 0x409)
    if manufacturer:
        nt.setName(manufacturer, 8, 3, 1, 0x409)
    if license_text:
        nt.setName(license_text, 13, 3, 1, 0x409)
    if license_url:
        nt.setName(license_url, 14, 3, 1, 0x409)
    fb.setupOS2(usWeightClass=weight)
    fb.setupPost()
    fb.save(str(path))
    return str(path)


def _build_test_ttc(path, families=("TtcFaceA", "TtcFaceB")) -> str:
    from fontTools.ttLib import TTCollection

    coll = TTCollection()
    for fam in families:
        coll.fonts.append(_load_builder_font(fam))
    coll.save(str(path))
    return str(path)


def _load_builder_font(family):
    from fontTools.fontBuilder import FontBuilder
    from fontTools.pens.ttGlyphPen import TTGlyphPen

    fb = FontBuilder(1000, isTTF=True)
    fb.setupGlyphOrder([".notdef"])
    fb.setupCharacterMap({})
    fb.setupGlyf({".notdef": TTGlyphPen(None).glyph()})
    fb.setupHorizontalMetrics({".notdef": (600, 0)})
    fb.setupHorizontalHeader(ascent=800, descent=-200)
    fb.setupNameTable({"familyName": family, "styleName": "Regular"})
    fb.setupOS2(usWeightClass=400)
    fb.setupPost()
    return fb.font


def _isolate_dirs(monkeypatch) -> None:
    """屏蔽默认字体目录，让 scan_fonts 只看测试传入的 extra_dirs。"""
    monkeypatch.setattr(fontlib_index, "default_font_dirs", lambda: [])


def _find_system_ttf():
    for pattern in (
        "/usr/share/fonts/**/*.ttf",
        "/usr/local/share/fonts/**/*.ttf",
    ):
        hits = sorted(glob.glob(pattern, recursive=True))
        for hit in hits:
            if os.access(hit, os.R_OK):
                return hit
    return None


@pytest.fixture
def system_ttf():
    path = _find_system_ttf()
    if path is None:
        pytest.skip("本机无可用系统 TTF 字体")
    return path


# ---------------------------------------------------------------------------
# optional dependency mode


def test_is_available_true_with_fonttools_env():
    assert fontlib_index.is_available() is True


def test_scan_fonts_degrades_gracefully_without_deps(monkeypatch, caplog, tmp_path):
    _build_test_font(os.path.join(tmp_path, "f.ttf"))
    _isolate_dirs(monkeypatch)
    monkeypatch.setattr(fontlib_index, "_FONTTOOLS_AVAILABLE", False)
    with caplog.at_level(logging.WARNING):
        assert fontlib_index.is_available() is False
        assert fontlib_index.scan_fonts(extra_dirs=[str(tmp_path)]) == []
    assert "warning" in caplog.text.lower() or "未安装" in caplog.text


# ---------------------------------------------------------------------------
# default font dirs


def test_default_font_dirs_only_existing(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("XDG_DATA_HOME", "")
    dirs = fontlib_index.default_font_dirs()
    assert all(os.path.isdir(d) for d in dirs)
    # 伪造的 HOME 下用户字体目录不存在，不得返回
    assert os.path.join(str(tmp_path), ".fonts") not in dirs
    assert os.path.join(str(tmp_path), ".local", "share", "fonts") not in dirs
    if os.path.isdir("/usr/share/fonts"):
        assert "/usr/share/fonts" in dirs


# ---------------------------------------------------------------------------
# metadata extraction on synthetic fonts


def test_extract_record_from_synthetic_ttf(tmp_path):
    path = _build_test_font(os.path.join(tmp_path, "testfont.ttf"))
    rec = fontlib_index.extract_font_record(path)
    assert rec is not None
    assert rec["canonical_name"] == "TestFont CI"
    # 别名：family+subfamily 组合与 full/PostScript 名，去重且不含规范名本身
    assert "TestFont CI Regular" in rec["aliases"]
    assert "TestFontCI-Regular" in rec["aliases"]
    assert "TestFont CI" not in rec["aliases"]
    assert len(rec["aliases"]) == len(set(rec["aliases"]))
    assert rec["license_name"] == "OFL-1.1"
    assert rec["license_category"] == "open_source"
    assert rec["vendor"] == "TestLab"
    assert rec["us_weight_class"] == 400
    assert rec["file_path"] == path
    assert rec["source"] == "local_index"
    # 语言覆盖检测属后续任务：本任务固定空列表
    assert rec["languages"] == []
    assert rec["official_url"] == "https://example.com/ofl"


def test_extract_record_ttc_takes_first_face(tmp_path):
    path = _build_test_ttc(os.path.join(tmp_path, "c.ttc"))
    rec = fontlib_index.extract_font_record(path)
    assert rec is not None
    assert rec["canonical_name"] == "TtcFaceA"


def test_license_category_strict_unknown_without_license(tmp_path):
    path = _build_test_font(
        os.path.join(tmp_path, "p.ttf"),
        license_text="Built for internal use only.",
        license_url="",
    )
    rec = fontlib_index.extract_font_record(path)
    assert rec is not None
    # 从严：有 license 文本但认不出 → unknown；绝不猜 free_commercial
    assert rec["license_name"] == ""
    assert rec["license_category"] == "unknown"
    assert rec["license_category"] != "free_commercial"


def test_license_category_unknown_when_no_license_info_at_all(tmp_path):
    path = _build_test_font(
        os.path.join(tmp_path, "q.ttf"), license_text="", license_url=""
    )
    rec = fontlib_index.extract_font_record(path)
    assert rec is not None
    assert rec["license_name"] == ""
    assert rec["license_category"] == "unknown"


def test_sanitize_url_integration_blocks_piracy_marker(tmp_path):
    path = _build_test_font(
        os.path.join(tmp_path, "bad.ttf"),
        license_url="https://example.com/crack/TestFont",
    )
    rec = fontlib_index.extract_font_record(path)
    assert rec is not None
    assert rec["official_url"] == ""


# ---------------------------------------------------------------------------
# directory scanning


def test_scan_fonts_recursive_and_extension_filter(monkeypatch, tmp_path):
    _isolate_dirs(monkeypatch)
    sub = tmp_path / "nested" / "deep"
    sub.mkdir(parents=True)
    valid = _build_test_font(sub / "real.ttf")
    (tmp_path / "notes.txt").write_text("not a font", encoding="utf-8")

    flat = fontlib_index.scan_fonts(extra_dirs=[str(tmp_path)], recursive=False)
    assert flat == []  # 字体在子目录，非递归扫不到

    recs = fontlib_index.scan_fonts(extra_dirs=[str(tmp_path)], recursive=True)
    assert [r["file_path"] for r in recs] == [valid]
    # .txt 与目录本身都不会被当作字体收集
    assert all(r["file_path"].endswith(".ttf") for r in recs)


def test_scan_fonts_skips_corrupt_file(monkeypatch, caplog, tmp_path):
    _isolate_dirs(monkeypatch)
    valid = _build_test_font(tmp_path / "good.ttf")
    corrupt = tmp_path / "broken.ttf"
    corrupt.write_bytes(os.urandom(512))

    with caplog.at_level(logging.WARNING):
        recs = fontlib_index.scan_fonts(extra_dirs=[str(tmp_path)])
    assert [r["file_path"] for r in recs] == [valid]
    assert "broken.ttf" in caplog.text  # 单文件失败 warning 后继续


def test_scan_fonts_unreadable_file_skipped(monkeypatch, caplog, tmp_path):
    if os.geteuid() == 0:
        pytest.skip("root 不受文件权限限制")
    _isolate_dirs(monkeypatch)
    valid = _build_test_font(tmp_path / "good.ttf")
    locked = _build_test_font(tmp_path / "locked.ttf", family="Locked Face")
    os.chmod(locked, 0o000)

    with caplog.at_level(logging.WARNING):
        recs = fontlib_index.scan_fonts(extra_dirs=[str(tmp_path)])
    assert [r["file_path"] for r in recs] == [valid]


# ---------------------------------------------------------------------------
# reference glyph rendering + cache


def test_render_reference_glyphs_returns_pil_images(system_ttf):
    Image = pytest.importorskip("PIL.Image")
    out = fontlib_index.render_reference_glyphs(system_ttf, "永A1", size=64)
    assert isinstance(out, dict)
    # 任一常见拉丁字体都有 A/1；永 缺字形时允许跳过
    assert "A" in out and "1" in out
    for ch, img in out.items():
        assert isinstance(img, Image.Image)
        assert img.width > 0 and img.height > 0


def test_cmap_codepoints_public_wrapper(system_ttf):
    codes = fontlib_index.cmap_codepoints(system_ttf)
    assert codes is not None
    assert ord("A") in codes


def test_cmap_codepoints_corrupt_file_returns_none(tmp_path):
    bad = tmp_path / "broken.ttf"
    bad.write_bytes(b"not a font" * 16)
    assert fontlib_index.cmap_codepoints(str(bad)) is None
    assert fontlib_index.cmap_codepoints("/nonexistent/x.ttf") is None


def test_glyph_cache_hit_does_not_rerender(system_ttf):
    cache = fontlib_index.FontGlyphCache()
    first = cache.get(system_ttf, "A1")
    second = cache.get(system_ttf, "A1")
    assert first and second
    # identity 断言：命中直接返回同一对象，不重新渲染
    assert second["A"] is first["A"]
    assert second["1"] is first["1"]


def test_glyph_cache_lru_eviction(system_ttf, tmp_path):
    # 同一字体复制两份路径，构造两个缓存键
    other = os.path.join(str(tmp_path), "copy.ttf")
    with open(system_ttf, "rb") as fh:
        data = fh.read()
    with open(other, "wb") as fh:
        fh.write(data)

    cache = fontlib_index.FontGlyphCache(capacity=1)
    first_a = cache.get(system_ttf, "A1")["A"]
    cache.get(other, "A1")  # 容量 1：挤出 system_ttf 条目
    again_a = cache.get(system_ttf, "A1")["A"]
    assert again_a is not first_a  # 旧条目已被淘汰，重新渲染产生新对象


# ---------------------------------------------------------------------------
# build_index


def test_build_index_stats_and_lookup(monkeypatch, tmp_path):
    _isolate_dirs(monkeypatch)
    _build_test_font(tmp_path / "one.ttf")
    _build_test_font(tmp_path / "two.ttf", family="Second Face",
                     manufacturer="Lab2", license_text="", license_url="")

    db = FontsDB(":memory:")
    try:
        stats = fontlib_index.build_index(db, extra_dirs=[str(tmp_path)])
        assert stats["scanned"] == 2
        assert stats["imported"] == 2
        assert stats["failed"] == 0

        hit = db.lookup_font("TestFont CI")
        assert hit is not None
        assert hit["license_category"] == "open_source"
        assert hit["source"] == "local_index"
        assert "TestFont CI Regular" in hit["aliases"]
        # 无许可信息者从严 unknown
        second = db.lookup_font("Second Face")
        assert second is not None
        assert second["license_category"] == "unknown"

        # 幂等：二次 build 计数不翻倍
        stats2 = fontlib_index.build_index(db, extra_dirs=[str(tmp_path)])
        assert stats2["scanned"] == 2
        assert stats2["imported"] == 2
        fonts_count = db._conn.execute("SELECT COUNT(*) FROM fonts").fetchone()[0]
        aliases_count = db._conn.execute(
            "SELECT COUNT(*) FROM aliases").fetchone()[0]
        assert fonts_count == 2
        assert aliases_count == 4
    finally:
        db.close()


def test_build_index_counts_failures(monkeypatch, tmp_path):
    _isolate_dirs(monkeypatch)
    _build_test_font(tmp_path / "good.ttf")
    (tmp_path / "bad.ttf").write_bytes(b"\x00\x01not a font")

    db = FontsDB(":memory:")
    try:
        stats = fontlib_index.build_index(db, extra_dirs=[str(tmp_path)])
        assert stats["scanned"] == 2
        assert stats["imported"] == 1
        assert stats["failed"] == 1
    finally:
        db.close()
