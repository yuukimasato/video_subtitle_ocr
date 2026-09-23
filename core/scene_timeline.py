# core/scene_timeline.py
"""场景文字策略的活动行集合时间切片(A2)。

真实样本(phone11/phone12,V2)暴露的策略层缺陷:external/whitespace/mask
把块内(甚至整个 ROI)的时间取 min/max、正文全部拼接,即使上游行时序
正确,策略层也会把不同时刻的文字抹平成一条常驻事件——开头就出现未来
文字、已消失的文字滞留到结尾。

本模块提供唯一的时间原语:把若干行各自的可见区间(半开,厘秒)做端点
扫描,输出每个「活跃行集合」恒定的连续区间(:class:`ActiveSlice`)。策略
层在每个切片内只布局该时刻有效的行;切片之外的时间不输出事件。

约定:
- 时间全部是**厘秒整数**(ASS 百分秒原生精度),由调用方从秒/ASS 时间串
  换算;本模块不做浮点舍入决策;
- 半开区间 ``[start_cs, end_cs)``;非正时长抛 :class:`ValueError`,不静默
  删除;
- ``row_id`` 是调用方分配的稳定行序(通常 = 屏幕阅读序位置),输出切片的
  ``row_ids`` 按其升序,保证行序稳定;同一 ``row_id`` 的重叠区间按多重
  计数处理(计数器),不会因删除一个区间误伤另一个;
- 空档(无活跃行)不产出切片;相邻切片仅在活跃集合完全一致时才可被
  调用方合并(本模块合并的是被同集合连续覆盖的相邻边界,属于同一切片)。
"""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass
from typing import Sequence


@dataclass(frozen=True)
class TimedRow:
    """一行文字的一个连续可见实例(半开区间,厘秒)。"""

    row_id: int
    start_cs: int
    end_cs: int


@dataclass(frozen=True)
class ActiveSlice:
    """一个活跃行集合恒定的时间区间(半开,厘秒);``row_ids`` 升序。"""

    start_cs: int
    end_cs: int
    row_ids: tuple


def active_slices(rows: Sequence[TimedRow]) -> list:
    """端点扫描:输出每个活跃行集合恒定的连续区间。

    同一 ``row_id`` 的多个实例(时间不重叠)各自计数;计数器归零该行才
    离场。空档跳过;仅当相邻边界两侧活跃集合完全一致时区间延续(等价于
    合并相邻同身份切片)。
    """
    boundaries: dict = defaultdict(Counter)
    for row in rows:
        if row.end_cs <= row.start_cs:
            raise ValueError(
                f"non-positive row duration: row_id={row.row_id} "
                f"[{row.start_cs}, {row.end_cs})")
        boundaries[row.start_cs][row.row_id] += 1
        boundaries[row.end_cs][row.row_id] -= 1
    times = sorted(boundaries)
    live: Counter = Counter()
    out: list = []
    for i, start in enumerate(times[:-1]):
        live.update(boundaries[start])
        ids = tuple(sorted(k for k, n in live.items() if n > 0))
        end = times[i + 1]
        if not ids:
            continue
        if out and out[-1].end_cs == start and out[-1].row_ids == ids:
            out[-1] = ActiveSlice(out[-1].start_cs, end, ids)
        else:
            out.append(ActiveSlice(start, end, ids))
    return out
