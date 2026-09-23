# core/trajectory_fidelity.py
"""轨迹接管的内容保真门控(A3)。

真实样本(phone11,V2)暴露:轨迹管线的 100% 帧覆盖率只说明「平面跟踪成
功」,不代表「文字内容完整」——参考帧融合词表不含后来出现的文字时,接管
会把整段 ROI 换成固定十行旁注,画面实际文字随时间变化全部丢失。

本模块把「时间覆盖率」(必要条件,由 :func:`core.pipeline_worker.
trajectory_takeover_ok` 判定)与「文本内容对照」分离:对静态 OCR 证据与
轨迹预策略证据划片比较,逐片要求两路的规范化行文本元组完全一致。任何
mismatch / 单侧覆盖 / 无静态证据都保守拒绝,拒绝后由调用方保留静态结果、
移除该 ROI 的轨迹候选。这是保守筛选,不能代替几何/语义真值。
"""

from __future__ import annotations

import unicodedata
from dataclasses import dataclass
from typing import Sequence, Tuple


@dataclass(frozen=True)
class TextSpan:
    """一行文字的一个可见区间(厘秒,半开)``row_order`` 为屏幕阅读序。"""

    start_cs: int
    end_cs: int
    text: str
    row_order: int = 0


@dataclass(frozen=True)
class FidelityResult:
    """门控结论:``ok`` 通过;``reason`` 记录通过/首个拒绝原因。"""

    ok: bool
    reason: str


def _normalize_text(text: str) -> str:
    """NFKC 归一并去空白/标点(保留字母数字与 CJK),消除 OCR 抖动噪声。"""
    normalized = unicodedata.normalize("NFKC", str(text))
    return "".join(ch for ch in normalized if ch.isalnum())


def _active_rows(spans: Sequence[TextSpan], start: int, end: int) -> Tuple:
    """切片内活跃行的规范化文本元组(按 row_order 稳定排序)。"""
    active = [sp for sp in spans if sp.start_cs <= start and sp.end_cs >= end]
    active.sort(key=lambda sp: sp.row_order)
    return tuple(_normalize_text(sp.text) for sp in active)


def check_text_fidelity(
    static: Sequence[TextSpan],
    motion: Sequence[TextSpan],
) -> FidelityResult:
    """按两路证据的端点划片,逐片比较规范化行文本元组。

    - 无静态证据 → ``ok=False, reason="unverified..."``(调用方保留静态、
      记录无法验证);
    - 非正时长 / 单侧覆盖 / 行文本或顺序不一致 → 拒绝并给出首个片段位置
      与两路内容;
    - 全部切片一致且至少一个可比切片 → 通过。

    有意不使用字符多重集:『甲乙』与『乙甲』必须拒绝(行序也是证据)。
    """
    static = list(static or [])
    motion = list(motion or [])
    if not static:
        return FidelityResult(
            False, "unverified: no static text evidence for this ROI")
    if not motion:
        return FidelityResult(
            False, "rejected: trajectory produced no text evidence")
    for span in static + motion:
        if span.end_cs <= span.start_cs:
            return FidelityResult(
                False, f"rejected: non-positive span [{span.start_cs}, "
                       f"{span.end_cs}) (text={span.text!r})")
    boundaries = sorted({b for span in static + motion
                         for b in (span.start_cs, span.end_cs)})
    compared = 0
    for start, end in zip(boundaries[:-1], boundaries[1:]):
        static_rows = _active_rows(static, start, end)
        motion_rows = _active_rows(motion, start, end)
        if not static_rows and not motion_rows:
            continue
        if not static_rows or not motion_rows:
            return FidelityResult(
                False,
                f"rejected: interval [{start}, {end}) cs is covered on one "
                f"side only (static_rows={len(static_rows)}, "
                f"motion_rows={len(motion_rows)})")
        compared += 1
        if static_rows != motion_rows:
            return FidelityResult(
                False,
                f"rejected: text mismatch in interval [{start}, {end}) cs: "
                f"static={static_rows!r} motion={motion_rows!r}")
    if not compared:
        return FidelityResult(
            False, "rejected: no comparable interval between the two paths")
    return FidelityResult(
        True, f"ok: {compared} interval(s) matched after normalization")
