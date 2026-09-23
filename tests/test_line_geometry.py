# tests/test_line_geometry.py
"""C2:行框几何字号约束(core.line_geometry)单元测试。"""

from __future__ import annotations

import os
import sys

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

import pytest  # noqa: E402

from core.line_geometry import fit_line_size, measure_local_text  # noqa: E402


def test_short_metadata_keeps_smaller_size():
    assert fit_line_size((0, 0, 60, 12), (500, 100)) == 12.0
    assert fit_line_size((0, 0, 300, 30), (500, 100)) == 30.0
    assert fit_line_size((0, 0, 0, 30), (500, 100)) is None


def test_width_also_limits_size():
    assert fit_line_size((0, 0, 80, 30), (500, 100)) == 16.0


def test_invalid_boxes_return_none():
    assert fit_line_size((0, 0, 0, 30), None) is None
    assert fit_line_size((0, 0, -10, 30), None) is None
    assert fit_line_size((0, float("nan"), 10, 30), None) is None
    assert fit_line_size((0, 0, float("inf"), 30), None) is None
    assert fit_line_size(None, None) is None
    assert fit_line_size((1, 2, 3), None) is None


def test_no_measurement_falls_back_to_line_height():
    assert fit_line_size((0, 0, 100, 30), None) == 30.0


def test_bad_measurement_falls_back_to_line_height():
    assert fit_line_size((0, 0, 100, 30), (0, 0)) == 30.0
    assert fit_line_size((0, 0, 100, 30), (float("nan"), 10)) == 30.0
    assert fit_line_size((0, 0, 100, 30), (500,), None or 100) == 30.0


def test_scale_independence():
    # 字号与框成比例,不随全片分辨率错误膨胀:同一相对框在不同分辨率下
    # 等比放大 → fs 等比放大。
    small = fit_line_size((0, 0, 60, 12), (500, 100))
    large = fit_line_size((0, 0, 120, 24), (500, 100))
    assert large == pytest.approx(small * 2)


def test_measure_local_text_degrades_to_none():
    assert measure_local_text("任意文本", None) is None
    assert measure_local_text("", "/nonexistent/font.ttf") is None
    assert measure_local_text("任意文本", "/nonexistent/font.ttf") is None


# ---------------------------------------------------------------------------
# 生成器级回归
# ---------------------------------------------------------------------------

def _make_conv(tmp_path, **kw):
    from core.subtitle_generator import OCRToASSOptimizer

    return OCRToASSOptimizer(
        video_path="unused", output_path=str(tmp_path / "out.ass"),
        fps=25.0, width=1280, height=720, **kw)


def _items(box_text_pairs, frames=range(10, 21), roi="roi_0", fps=25.0):
    items = []
    for f in frames:
        polys, texts, boxes = [], [], []
        for text, box in box_text_pairs:
            x1, y1, x2, y2 = box
            polys.append([[x1, y1], [x2, y1], [x2, y2], [x1, y2]])
            texts.append(text)
            boxes.append(list(box))
        data = {"dt_polys": polys, "rec_polys": polys, "rec_texts": texts,
                "rec_scores": [0.95] * len(texts), "rec_boxes": boxes}
        items.append((data, f, roi, f / fps))
    return items


def test_small_metadata_not_inflated(tmp_path):
    """同屏 12px 小字与 30px 正文的 fs 不同(行高约束)。"""
    conv = _make_conv(tmp_path)
    conv.convert_from_memory(iter(_items([
        ("大字正文", (100, 300, 500, 330)),
        ("小字标签", (100, 340, 200, 352)),
    ])))
    text = (tmp_path / "out.ass").read_text(encoding="utf-8-sig")
    import re

    fs_by_body = {}
    for ln in text.splitlines():
        if ln.startswith("Dialogue:") and "\\fs" in ln:
            m = re.search(r"\\fs(\d+)", ln)
            body = re.sub(r"^\{[^}]*\}", "",
                          ln.split(",", 9)[9].lstrip(","))
            fs_by_body[body] = int(m.group(1))
    assert fs_by_body.get("大字正文") == 30
    assert fs_by_body.get("小字标签") == 12


def test_template_path_suppresses_auto_fs(tmp_path):
    """传模板时不写自动 fs(用户模板优先)。"""
    template = tmp_path / "tpl.ass"
    template.write_text("", encoding="utf-8")
    conv = _make_conv(tmp_path, template_path=str(template))
    conv.convert_from_memory(iter(_items([
        ("大字正文", (100, 300, 500, 330)),
    ])))
    text = (tmp_path / "out.ass").read_text(encoding="utf-8-sig")
    assert "\\fs" not in text


def test_rotation_off_adds_no_geometry(tmp_path):
    """无 pose 的 ROI 不新增 frz/颜色标签(fs 属于基础几何)。"""
    conv = _make_conv(tmp_path)
    conv.convert_from_memory(iter(_items([
        ("大字正文", (100, 300, 500, 330)),
    ])))
    text = (tmp_path / "out.ass").read_text(encoding="utf-8-sig")
    assert "\\frz" not in text
    assert "\\1c" not in text
