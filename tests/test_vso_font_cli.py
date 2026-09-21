# tests/test_vso_font_cli.py
"""``vso-font`` 独立 CLI（T3.5）端到端测试：参数解析、图片识别（合成小图 +
真实字体渲染，字形重排路径）、无 torch 时的可恢复报错/降级、视频 + ROI
采样识别、临时字体目录建库、复核/建议包导入往返。

现有 ``cli.py``（单命令 + 位置参数 video）保持不动——本模块只测独立入口
``font_cli.py``，经 ``font_cli.main(argv)`` 进程内调用（与既有 GUI/CLI 测试
同风格，monkeypatch 生效）。
"""

from __future__ import annotations

import importlib.util
import json
import os
import shutil
import sys

import pytest

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

import font_cli  # noqa: E402
import font_intel.fontlib_index as fontlib_index  # noqa: E402
from font_intel.closure import (  # noqa: E402
    build_update_suggestions_package,
    write_update_suggestions_package,
)
from font_intel.etl.review import (  # noqa: E402
    build_review_package,
    write_review_package,
)
from font_intel.fonts_db import FontsDB  # noqa: E402

OFL_TEXT = "This font is licensed under the SIL Open Font License 1.1"

_DEJAVU_SANS = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
_LIB_SANS = (
    "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf"
)


def _has_system_ttf() -> bool:
    return any(os.path.isfile(p) for p in (_DEJAVU_SANS, _LIB_SANS))


def _first_system_ttf() -> str:
    for p in (_DEJAVU_SANS, _LIB_SANS):
        if os.path.isfile(p):
            return p
    raise AssertionError("unreachable")


def _has_torch() -> bool:
    return importlib.util.find_spec("torch") is not None


def _build_test_font(
    path,
    family="TestFont CI",
    subfamily="Regular",
    license_text=OFL_TEXT,
    license_url="https://example.com/ofl",
) -> str:
    """FontBuilder 最小 TTF：字形 "A" 为实心三角（与样张渲染同形）。"""
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
    nt.setName("TestLab", 8, 3, 1, 0x409)
    if license_text:
        nt.setName(license_text, 13, 3, 1, 0x409)
    if license_url:
        nt.setName(license_url, 14, 3, 1, 0x409)
    fb.setupOS2(usWeightClass=400)
    fb.setupPost()
    fb.save(str(path))
    return str(path)


def _render_text_image(font_path: str, text: str = "A", px: int = 96,
                       canvas: int = 160) -> str:
    """用指定字体把 text 画到白底图上（与 glyph_rerank 参考渲染同风格）。"""
    from PIL import Image, ImageDraw, ImageFont

    img = Image.new("L", (canvas, canvas), 255)
    draw = ImageDraw.Draw(img)
    face = ImageFont.truetype(font_path, px)
    left, top, right, bottom = draw.textbbox((0, 0), text, font=face)
    draw.text(((canvas - (right - left)) // 2 - left,
               (canvas - (bottom - top)) // 2 - top),
              text, font=face, fill=0)
    out = os.path.splitext(font_path)[0] + f"_sample_{abs(hash(text)) % 10**8}.png"
    img.save(out)
    return out


@pytest.fixture
def font_dir(tmp_path):
    """临时字体库：合成字体 + 一个真实系统字体（跨字体对照）。"""
    d = tmp_path / "fonts"
    d.mkdir()
    _build_test_font(d / "testfont.ttf", family="TestFont CI")
    if _has_system_ttf():
        shutil.copy(_first_system_ttf(), d / "system.ttf")
    return str(d)


@pytest.fixture(autouse=True)
def _isolate_system_dirs(monkeypatch):
    """屏蔽默认系统字体目录：识别/建库只看测试显式传入的目录（快且确定）。"""
    monkeypatch.setattr(fontlib_index, "default_font_dirs", lambda: [])


# ── 参数解析 ─────────────────────────────────────────────────────


def test_parser_identify_defaults():
    args = font_cli.build_parser().parse_args(["identify", "img.png"])
    assert args.command == "identify"
    assert args.input == "img.png"
    assert args.roi == ""
    assert args.text == ""
    assert args.top_n == 5
    assert args.samples == 5
    assert args.font_db == ""
    assert args.font_dir == ""
    assert args.json == ""
    assert args.no_download is False


def test_parser_identify_full_options():
    argv = [
        "identify", "clip.mp4", "--roi", "10,20,30,40", "--text", "ABC",
        "--top-n", "3", "--font-db", "/tmp/f.db", "--font-dir", "/tmp/fonts",
        "--json", "/tmp/out.json", "--samples", "2", "--no-download",
    ]
    args = font_cli.build_parser().parse_args(argv)
    assert args.roi == "10,20,30,40"
    assert args.text == "ABC"
    assert args.top_n == 3
    assert args.samples == 2
    assert args.no_download is True
    assert args.json == "/tmp/out.json"


def test_parser_roi_alias_auto():
    args = font_cli.build_parser().parse_args(["identify", "img.png", "--roi", "auto"])
    assert args.roi == "auto"


@pytest.mark.parametrize("bad", ["1,2,3", "a,b,c,d", "-1,0,10,10", "10,10,0,5"])
def test_parser_rejects_bad_roi_at_runtime(bad, capsys):
    args = font_cli.build_parser().parse_args(["identify", "img.png", f"--roi={bad}"])
    with pytest.raises(ValueError):
        font_cli._parse_roi(args.roi)


def test_parser_index_and_review():
    args = font_cli.build_parser().parse_args(
        ["index", "--dir", "/tmp/fonts", "--db", "/tmp/f.db"])
    assert args.command == "index" and args.dir == "/tmp/fonts"
    assert args.db == "/tmp/f.db"

    args = font_cli.build_parser().parse_args(
        ["review", "import", "pkg.json", "--db", "/tmp/f.db"])
    assert args.command == "review"
    assert args.review_command == "import"
    assert args.package == "pkg.json"


def test_version_flag(capsys):
    with pytest.raises(SystemExit) as exc:
        font_cli.build_parser().parse_args(["--version"])
    assert exc.value.code == 0
    assert "vso-font" in capsys.readouterr().out


# ── identify：图片（字形重排路径，无 torch 也可用） ───────────────


@pytest.mark.skipif(not fontlib_index.is_available(),
                    reason="缺少 fontTools/Pillow")
def test_identify_image_ranks_correct_font_first(font_dir, tmp_path, capsys):
    sample = _render_text_image(os.path.join(font_dir, "testfont.ttf"), "A")
    out_json = str(tmp_path / "result.json")
    rc = font_cli.main([
        "identify", sample, "--text", "A", "--font-dir", font_dir,
        "--top-n", "3", "--json", out_json,
    ])
    assert rc == 0

    payload = json.loads(open(out_json, encoding="utf-8").read())
    assert payload["schema"] == font_cli.IDENTIFY_SCHEMA
    assert payload["input_type"] == "image"
    assert payload["frames_sampled"] == 1
    names = [c["name"] for c in payload["candidates"]]
    assert "TestFont CI" in names
    # 合成字体样张 vs 同字体参考字形：自比对必须排首位。
    assert names[0] == "TestFont CI"
    top = payload["candidates"][0]
    assert top["source"] == "glyph_rerank"
    assert top["glyph_ranked"] is True
    assert 0.0 <= top["score"] <= 1.0
    # 无 --font-db 且默认库不存在 → 许可类别为 None（不凭空建库）。
    assert top["license_category"] is None
    # 硬红线：输出里只允许官方链接或库内字段，绝不出现破解渠道。
    for cand in payload["candidates"]:
        assert "torrent" not in json.dumps(cand).lower()
    # stdout 人读行同样列出候选。
    out = capsys.readouterr().out
    assert "TestFont CI" in out


@pytest.mark.skipif(not fontlib_index.is_available(),
                    reason="缺少 fontTools/Pillow")
def test_identify_image_with_font_db_license_lookup(font_dir, tmp_path):
    """--font-db 指向已索引库 → 候选携带许可类别与官方链接。"""
    from font_intel.fonts_db import FontsDB
    from font_intel import fontlib_index as fli

    db_path = str(tmp_path / "fonts.db")
    db = FontsDB(db_path)
    try:
        fli.build_index(db, extra_dirs=[font_dir])
    finally:
        db.close()

    sample = _render_text_image(os.path.join(font_dir, "testfont.ttf"), "A")
    out_json = str(tmp_path / "result.json")
    rc = font_cli.main([
        "identify", sample, "--text", "A", "--font-dir", font_dir,
        "--font-db", db_path, "--json", out_json,
    ])
    assert rc == 0
    payload = json.loads(open(out_json, encoding="utf-8").read())
    top = next(c for c in payload["candidates"] if c["name"] == "TestFont CI")
    # OFL 文本经 sniff_license 归 open_source（从严，绝不猜 free_commercial）。
    assert top["license_category"] == "open_source"
    assert top["official_url"] == "https://example.com/ofl"


def test_identify_image_without_torch_and_text_recoverable_error(
        font_dir, tmp_path, capsys):
    """无 torch（或权重）且无 --text → 明确报错、退出码非 0 且信息可恢复。"""
    if _has_torch():
        pytest.skip("本机已安装 torch，无法测无 torch 降级路径")
    sample = _render_text_image(os.path.join(font_dir, "testfont.ttf"), "A")
    rc = font_cli.main(["identify", sample, "--font-dir", font_dir])
    assert rc == font_cli.EXIT_MISSING_DEPS
    err = capsys.readouterr().err
    assert "--text" in err
    assert "torch" in err


@pytest.mark.skipif(not fontlib_index.is_available(),
                    reason="缺少 fontTools/Pillow")
def test_identify_missing_input_fails(font_dir, capsys):
    rc = font_cli.main(["identify", str(tmp_path_not_exists()), "--text", "A"])
    assert rc == font_cli.EXIT_ERROR


def tmp_path_not_exists():
    import tempfile
    return os.path.join(tempfile.gettempdir(), "vso_font_no_such_file.png")


# ── identify：视频 + ROI 采样 ────────────────────────────────────


@pytest.mark.skipif(not fontlib_index.is_available(),
                    reason="缺少 fontTools/Pillow")
def test_identify_video_with_roi_samples_frames(font_dir, tmp_path):
    import cv2

    sample_png = _render_text_image(os.path.join(font_dir, "testfont.ttf"), "A")
    sample_img = cv2.imread(sample_png, cv2.IMREAD_GRAYSCALE)
    h, w = sample_img.shape[:2]
    video_path = str(tmp_path / "clip.avi")
    writer = cv2.VideoWriter(
        video_path, cv2.VideoWriter_fourcc(*"MJPG"), 25.0, (w, h))
    assert writer.isOpened()
    for _ in range(8):
        writer.write(cv2.cvtColor(sample_img, cv2.COLOR_GRAY2BGR))
    writer.release()

    # ROI 覆盖字块区域（留边距，防裁到边缘）。
    margin = 16
    roi = f"{margin},{margin},{w - 2 * margin},{h - 2 * margin}"
    out_json = str(tmp_path / "video_result.json")
    rc = font_cli.main([
        "identify", video_path, "--roi", roi, "--text", "A",
        "--font-dir", font_dir, "--samples", "3", "--json", out_json,
    ])
    assert rc == 0
    payload = json.loads(open(out_json, encoding="utf-8").read())
    assert payload["input_type"] == "video"
    assert payload["frames_sampled"] == 3
    assert payload["roi"] == [int(v) for v in roi.split(",")]
    names = [c["name"] for c in payload["candidates"]]
    assert names and names[0] == "TestFont CI"


# ── index：临时字体目录建库 ──────────────────────────────────────


@pytest.mark.skipif(not fontlib_index.is_available(),
                    reason="缺少 fontTools/Pillow")
def test_index_builds_db_from_temp_dir(font_dir, tmp_path, capsys):
    db_path = str(tmp_path / "fonts.db")
    rc = font_cli.main(["index", "--dir", font_dir, "--db", db_path])
    assert rc == 0
    stats = json.loads(capsys.readouterr().out)
    assert stats["scanned"] >= 2
    assert stats["imported"] >= 2

    db = FontsDB(db_path)
    try:
        assert db.lookup_font("TestFont CI") is not None
    finally:
        db.close()

    # 二次构建幂等（import_seed 同键先删后插），退出码仍为 0。
    rc = font_cli.main(["index", "--dir", font_dir, "--db", db_path])
    assert rc == 0


# ── review import：T1.5 复核包 / T3.4 建议包往返 ─────────────────


def _update_suggestions_package(path):
    items = [
        {
            "type": "new_font_record", "font_name": "TestFont CI",
            "candidate": None, "reason": "识别字体未收录本地字体库",
            "evidence": "font_identify Top-1", "method": "glyph_rerank",
            "confidence": 0.9,
            "suggestion": {"kind": "font", "canonical_name": "TestFont CI",
                           "license_category": "open_source"},
        },
        {
            "type": "new_mapping", "font_name": "A1明朝", "candidate": "华文宋体",
            "reason": "同风格类别兜底命中", "evidence": "chain level 3",
            "method": "llm_inferred", "confidence": 0.6,
            "suggestion": {"kind": "mapping", "jp_name": "A1明朝",
                           "cn_name": "华文宋体"},
        },
    ]
    write_update_suggestions_package(
        build_update_suggestions_package(items), path)
    return items


def test_review_import_update_suggestions_package(tmp_path, capsys):
    pkg = str(tmp_path / "video_font_update_suggestions.json")
    _update_suggestions_package(pkg)
    db_path = str(tmp_path / "fonts.db")

    rc = font_cli.main(["review", "import", pkg, "--db", db_path])
    assert rc == 0
    summary = json.loads(capsys.readouterr().out)
    assert summary["adopted_items"] == 2
    assert summary["written"] == 2

    db = FontsDB(db_path)
    try:
        rec = db.lookup_font("TestFont CI")
        assert rec is not None and rec.get("license_category") == "open_source"
        rows = db.lookup_jp_cn("A1明朝")
        assert any(r["cn_name"] == "华文宋体" for r in rows)
    finally:
        db.close()


def test_review_import_map_review_package(tmp_path, capsys):
    """T1.5 复核包（vso_font_map_review/1）：采纳带建议值的条目入库。"""
    pkg = str(tmp_path / "review.json")
    items = [
        {"source_file": "tbl.txt", "line_no": 3, "line": "A1明朝 → 华文宋体",
         "record_type": "mapping", "names": ["A1明朝"], "target": "华文宋体",
         "confidence": 0.5, "method": "llm", "reason": "low_confidence",
         "note": "",
         "suggestion": {"kind": "mapping", "jp_name": "A1明朝",
                        "cn_name": "华文宋体"}},
        {"source_file": "tbl.txt", "line_no": 4, "line": "自由格式",
         "record_type": "pending", "names": [], "target": None,
         "confidence": None, "method": "llm", "reason": "词表未命中",
         "note": "", "suggestion": None},
    ]
    write_review_package(build_review_package(items), pkg)
    db_path = str(tmp_path / "fonts.db")

    rc = font_cli.main(["review", "import", pkg, "--db", db_path])
    assert rc == 0
    summary = json.loads(capsys.readouterr().out)
    assert summary["adopted_items"] == 1  # 仅带建议值的条目
    assert summary["written"] == 1

    db = FontsDB(db_path)
    try:
        rows = db.lookup_jp_cn("A1明朝")
        assert any(r["cn_name"] == "华文宋体" for r in rows)
    finally:
        db.close()


def test_review_import_rejects_unknown_schema(tmp_path, capsys):
    pkg = str(tmp_path / "not_a_package.json")
    with open(pkg, "w", encoding="utf-8") as fh:
        json.dump({"schema": "something/else", "items": []}, fh)
    db_path = str(tmp_path / "fonts.db")
    rc = font_cli.main(["review", "import", pkg, "--db", db_path])
    assert rc == font_cli.EXIT_ERROR
    assert "schema" in capsys.readouterr().err
