# core/line_geometry.py
"""按 OCR 行框几何约束 Scene 字号(C2,V5)。

真实样本暴露的两类几何问题:4K 歌词条使用全局字号导致下方文字明显过
大;phone12 的小字标签被统一 Scene 字号放大。本模块提供纯几何约束:

- :func:`fit_line_size` —— 行框(视频屏幕坐标)决定字号上界:有字体
  墨迹测量时 ``base_size × min(框宽/墨宽, 框高/墨高)``,无测量时退回
  行高保守估计;非法/非有限/非正框返回 None(调用方保留既有样式并记
  诊断),绝不把错误框强行变成一像素文字。
- :func:`measure_local_text` —— 可选 Pillow 的墨迹测量(已解析且覆盖
  正文的本地字体路径);无路径/缺字/无 Pillow 返回 None,不强制引入
  font_intel 可选依赖。

注意:Pillow 墨迹高度与 ASS 的 em 字号并不等价,此函数只提供初值/约束;
最终字号准确度由 libass 固定字体渲染测试校准(test_line_geometry_render)。
"""

from __future__ import annotations

import math
from functools import lru_cache
from typing import Optional, Sequence, Tuple

__all__ = ["fit_line_size", "measure_local_text"]

_FONT_CACHE_MAX = 256


def fit_line_size(
    box: Sequence[float],
    measured: Optional[Tuple[float, float]],
    base_size: float = 100.0,
) -> Optional[float]:
    """行框几何 → 字号上界(厘字号)。

    ``box`` = (x1, y1, x2, y2) 屏幕坐标;``measured`` = 目标字体在
    ``base_size`` 字号时的墨迹 (宽, 高)。无可靠测量时按行高保守估计
    (round(h, 2));非法/NaN/inf/非正框返回 None。
    """
    if box is None or len(box) != 4:
        return None
    try:
        values = [float(v) for v in box]
    except (TypeError, ValueError):
        return None
    if not all(math.isfinite(v) for v in values):
        return None
    w = values[2] - values[0]
    h = values[3] - values[1]
    if w <= 0 or h <= 0:
        return None
    if measured is None:
        return round(h, 2)
    try:
        mw, mh = (float(measured[0]), float(measured[1]))
    except (TypeError, ValueError, IndexError):
        return round(h, 2)
    if not all(math.isfinite(v) and v > 0 for v in (mw, mh, base_size)):
        return round(h, 2)
    return round(float(base_size) * min(w / mw, h / mh), 2)


@lru_cache(maxsize=_FONT_CACHE_MAX)
def _load_font(path: str, size: int):
    from PIL import ImageFont

    return ImageFont.truetype(path, size)


def measure_local_text(
    text: str,
    font_path: Optional[str],
) -> Optional[Tuple[float, float]]:
    """本地字体墨迹 (宽, 高);无路径/缺字/异常降级 None。

    只捕获 OSError/ImportError/ValueError(环境缺失与字体问题);其他
    异常照常抛出,不吞逻辑错误。LRU 缓存上限 256 项。
    """
    if not font_path or not text:
        return None
    try:
        font = _load_font(str(font_path), 100)
        left, top, right, bottom = font.getbbox(str(text))
        width = float(right - left)
        height = float(bottom - top)
    except (OSError, ImportError, ValueError):
        return None
    if width <= 0 or height <= 0:
        return None
    return (width, height)
