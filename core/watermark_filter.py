# core/watermark_filter.py
"""水印文本剔除：按深度扫描产出的水印清单过滤 OCR 文本行。

水印 = 恒定文本 + 恒定坐标（见 core/fullframe_scanner.py 的判定逻辑）。
过滤发生在场景文字分类**之前**且独立于其开关：剔除水印不影响场景字的保留
策略（手动 ROI 的 keep_all 策略同样会被剔除水印行——除非用户在复核对话框
中不勾选该水印）。

匹配规则：
- 文本：归一化后相等；或较短线串（≥3 字符）是较长线串的子串
  （容错 OCR 漏识/多识少量字符的情况）。
- 位置：文本行中心落入水印框内（外扩容差像素）。
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Sequence, Tuple

from core.fullframe_scanner import normalize_text

logger = logging.getLogger(__name__)

# 子串匹配允许的最短归一化长度（字符）。过短的串（如 "tv"）做子串匹配会
# 大面积误伤正常字幕。
MIN_SUBSTR_LEN = 3

# 行中心判定落入水印框时向外扩的容差（像素）。
BOX_CENTER_TOLERANCE_PX = 6.0


class WatermarkFilter:
    """由水印条目构建的行级过滤器。"""

    def __init__(self, entries: Sequence[Dict[str, Any]]):
        self._texts = set()
        self._boxes: List[Tuple[float, float, float, float]] = []
        for entry in entries or []:
            if not isinstance(entry, dict):
                continue
            text = normalize_text(entry.get("text", ""))
            if text:
                self._texts.add(text)
            bbox = entry.get("bbox")
            try:
                box = tuple(float(v) for v in bbox)
                if len(box) == 4 and box[2] > box[0] and box[3] > box[1]:
                    self._boxes.append(box)  # type: ignore[arg-type]
            except (TypeError, ValueError):
                continue

    @property
    def enabled(self) -> bool:
        return bool(self._texts or self._boxes)

    def should_drop(self, text: str, box: Optional[Sequence[float]]) -> Tuple[bool, str]:
        """返回 (是否剔除, 命中原因 "text"/"box"/"")。"""
        normalized = normalize_text(text)
        if normalized:
            for watermark_text in self._texts:
                if watermark_text == normalized:
                    return True, "text"
                shorter, longer = sorted((watermark_text, normalized), key=len)
                if len(shorter) >= MIN_SUBSTR_LEN and shorter in longer:
                    return True, "text"
        if box is not None and self._boxes:
            try:
                x1, y1, x2, y2 = (float(v) for v in box)
            except (TypeError, ValueError):
                return False, ""
            cx, cy = (x1 + x2) / 2.0, (y1 + y2) / 2.0
            for wx1, wy1, wx2, wy2 in self._boxes:
                if (wx1 - BOX_CENTER_TOLERANCE_PX) <= cx <= (wx2 + BOX_CENTER_TOLERANCE_PX) and \
                        (wy1 - BOX_CENTER_TOLERANCE_PX) <= cy <= (wy2 + BOX_CENTER_TOLERANCE_PX):
                    return True, "box"
        return False, ""


def build_watermark_filter(config: Optional[Dict[str, Any]]) -> Optional[WatermarkFilter]:
    """从 watermark_filter_config 构建过滤器；未启用时返回 None。"""
    if not config or not config.get("enabled"):
        return None
    watermark_filter = WatermarkFilter(config.get("entries") or [])
    return watermark_filter if watermark_filter.enabled else None
