# font_intel/etl/db_loader.py
"""S6 溯源入库 + S5 复核决策合并：ETL 记录 ↔ fonts_db 的唯一写入通道。

职责与红线
----------
- 种子层：``load_seed_records`` 把 ETL 记录（method=rule/llm）转换为
  ``FontsDB.import_seed`` 载荷写入种子层——种子层数据只经 ``import_seed``
  入库且运行时只读，本模块不提供任何绕过该约束的写入口；
- 覆盖层：``apply_review_decisions`` 把人工复核"采纳"的决策写覆盖层
  （``add_overlay_mapping``，``method='human'``、``confidence=1.0``，保留
  ``source_file``/``line_no`` 溯源）；"否决"不写库，留痕日志后丢弃；
- 溯源：每条入库映射必带 ``source_file / line_no / method / confidence``
  四个溯源字段（缺一即整批拒绝），并统一标注
  ``source=MAP_SOURCE_TAG``（数据来源与许可）；
- 只写元数据：本模块只读写名称/映射/许可等事实元数据，绝不分发任何
  字体文件本体。

记录类型取舍：六类 ETL 记录中只有 ``mapping`` / ``negative_mapping``
对应 ``jp_cn_font_map`` 表结构（正/负映射行）；``rename`` / ``pitfall`` /
``flag_sc_tc`` / ``weight_list`` / ``todo`` 在当前 schema 下没有对应表，
计入 skipped 统计留待后续任务扩展，不强行塞入 fonts 表制造许可噪声。
"""

from __future__ import annotations

import logging
from typing import Optional

from font_intel.fonts_db import FontsDB

logger = logging.getLogger(__name__)

__all__ = [
    "MAP_SOURCE_TAG",
    "FONT_UPDATE_SOURCE_TAG",
    "VALID_METHODS",
    "mapping_seed_records",
    "load_seed_records",
    "apply_review_decisions",
    "apply_font_update_decisions",
]

# 方案 §5.1 S6：逐条映射统一标注的数据来源与许可（MIT）。
MAP_SOURCE_TAG = "Seekladoom/Japanese-Chinese-Fonts-adaptation (MIT)"

# T3.4 更新建议包采纳入库的来源标注（缺口回写，§5.4）。
FONT_UPDATE_SOURCE_TAG = "video_subtitle_ocr font update suggestions"

# 溯源 method 枚举（方案 §5.1：rule/llm/vlm/human）。
VALID_METHODS = frozenset({"rule", "llm", "vlm", "human"})

# 能落库的记录类型；其余类型 skipped（见模块 docstring 取舍）。
_DB_RECORD_TYPES = frozenset({"mapping", "negative_mapping"})


def _provenance(rec: dict) -> tuple[str, float, str, Optional[int]]:
    """校验并取出四项溯源字段；缺一即 ValueError（整批拒绝由调用方原子性兜底）。"""
    method = str(rec.get("method") or "").strip()
    if method not in VALID_METHODS:
        raise ValueError(f"record requires method in {sorted(VALID_METHODS)}, "
                         f"got {method!r}")
    conf = rec.get("confidence")
    if isinstance(conf, bool) or not isinstance(conf, (int, float)):
        raise ValueError(f"record requires numeric confidence, got {conf!r}")
    source_file = str(rec.get("source_file") or "")
    line_no = rec.get("line_no")
    line_no = int(line_no) if line_no is not None else None
    return method, float(conf), source_file, line_no


def mapping_seed_records(
    records: list[dict], *, source: str = MAP_SOURCE_TAG
) -> tuple[list[dict], dict[str, int]]:
    """ETL 记录 → ``import_seed`` 载荷。

    - ``mapping``：names[0] → target 一行正映射；
    - ``negative_mapping``：逐个别名一行负映射（cn_name 为 NULL），
      查询任一别名都能命中"没有日文本家对应"事实；
    - 其余类型跳过并计数，不写库。

    Returns:
        ``(payload, skipped)``：payload 为 ``import_seed`` 记录列表，
        skipped 为 ``{记录类型: 条数}``。
    """
    payload: list[dict] = []
    skipped: dict[str, int] = {}
    for rec in records or []:
        rtype = str(rec.get("type") or "")
        if rtype not in _DB_RECORD_TYPES:
            key = rtype or "<none>"
            skipped[key] = skipped.get(key, 0) + 1
            continue
        names = [str(n).strip() for n in (rec.get("names") or []) if str(n).strip()]
        if not names:
            raise ValueError(f"record without names: {rec!r}")
        method, conf, source_file, line_no = _provenance(rec)
        base = {
            "table": "jp_cn_font_map",
            "method": method,
            "confidence": conf,
            "source_file": source_file,
            "line_no": line_no,
            "source": source,
        }
        if rtype == "mapping":
            target = str(rec.get("target") or "").strip()
            if not target:
                raise ValueError("mapping record requires target")
            payload.append(dict(base, jp_name=names[0], cn_name=target,
                                kind="mapping"))
        else:  # negative_mapping
            for name in names:
                payload.append(dict(base, jp_name=name, cn_name=None,
                                    kind="negative_mapping"))
    return payload, skipped


def load_seed_records(
    db: FontsDB, records: list[dict], *, source: str = MAP_SOURCE_TAG
) -> dict:
    """S6：把 ETL 记录批量写入种子层（经 ``import_seed``，单批原子）。

    Returns:
        ``{"written": 落库行数, "skipped": {类型: 条数}}``。
    """
    payload, skipped = mapping_seed_records(records, source=source)
    if payload:
        db.import_seed(payload)
    logger.info("S6 种子入库: written=%s skipped=%s", len(payload), skipped)
    return {"written": len(payload), "skipped": skipped}


def apply_review_decisions(
    db: FontsDB, decisions: dict, *, source: str = MAP_SOURCE_TAG
) -> dict:
    """S5：把复核决策写入覆盖层（采纳）或留痕丢弃（否决）。

    ``decisions`` 形如 ``{"adopted": [条目...], "rejected": [条目...]}``
    （``font_intel.etl.review`` 的决策包 / ``FontMapReviewDialog.get_result()``
    同构）。采纳条目的 ``suggestion.kind`` 决定写法：

    - ``mapping``：一行覆盖层正映射；
    - ``negative_mapping``：逐个别名一行覆盖层负映射。

    采纳行统一 ``method='human'``、``confidence=1.0``（人工确认语义），
    保留原条目的 ``source_file``/``line_no`` 溯源与 ``source`` 来源标注；
    否决条目不写库，仅打留痕日志（含文件与行号）后丢弃。

    Returns:
        ``{"adopted_items": 采纳条目数, "written": 落库行数,
        "rejected": 否决条目列表}``。
    """
    decisions = decisions or {}
    adopted = list(decisions.get("adopted") or [])
    rejected = list(decisions.get("rejected") or [])
    written = 0
    for item in adopted:
        if not isinstance(item, dict):
            raise ValueError(f"review decision must be a dict: {item!r}")
        suggestion = item.get("suggestion") or {}
        kind = suggestion.get("kind")
        line_no = item.get("line_no")
        kwargs = dict(
            method="human",
            confidence=1.0,
            source_file=str(item.get("source_file") or ""),
            line_no=int(line_no) if line_no is not None else None,
            source=source,
        )
        if kind == "mapping":
            db.add_overlay_mapping(
                str(suggestion.get("jp_name") or ""),
                suggestion.get("cn_name"),
                kind="mapping",
                **kwargs,
            )
            written += 1
        elif kind == "negative_mapping":
            names = [str(n).strip() for n in (item.get("names") or [])
                     if str(n).strip()]
            if not names:
                names = [str(suggestion.get("jp_name") or "").strip()]
            for name in names:
                db.add_overlay_mapping(name, None, kind="negative_mapping",
                                       **kwargs)
                written += 1
        else:
            raise ValueError(f"unsupported suggestion kind: {kind!r}")
    for item in rejected:
        logger.info("复核否决，丢弃不写库: %s:%s %s",
                    item.get("source_file"), item.get("line_no"),
                    str(item.get("line") or "")[:60])
    return {"adopted_items": len(adopted), "written": written, "rejected": rejected}


def apply_font_update_decisions(
    db: FontsDB, decisions: dict, *, source: str = FONT_UPDATE_SOURCE_TAG
) -> dict:
    """T3.4 §5.4：把「更新建议包」的人工采纳决策写入覆盖层。

    ``decisions`` 形如 ``{"adopted": [建议条目...], "rejected": [...]}``
    （``FontUpdateReviewDialog.get_result()`` / 建议包导入后的选择同构）。
    采纳条目的 ``suggestion.kind`` 决定写法：

    - ``font``：``upsert_overlay_font`` 写覆盖层字体记录（fonts 表无
      method 列，人工确认语义经 ``source`` 标注传递）；
    - ``mapping``：一行覆盖层正映射，``method='human'``、
      ``confidence=1.0``（与 S5 复核采纳一致的人工确认语义）；
    - ``negative_mapping``：逐个别名一行覆盖层负映射。

    否决条目不写库，仅打留痕日志。**未经人工确认的 ``llm_inferred``
    建议绝不经本函数入库**（只有对话框/导入后显式采纳的条目进入
    ``adopted``；运行时 ``compliance.decide`` 另有防御拦截漏网记录）。

    Returns:
        ``{"adopted_items": 采纳条目数, "written": 落库行数,
        "rejected": 否决条目列表}``。
    """
    decisions = decisions or {}
    adopted = list(decisions.get("adopted") or [])
    rejected = list(decisions.get("rejected") or [])
    written = 0
    for item in adopted:
        if not isinstance(item, dict):
            raise ValueError(f"update suggestion must be a dict: {item!r}")
        suggestion = item.get("suggestion") or {}
        kind = suggestion.get("kind")
        if kind == "font":
            name = str(suggestion.get("canonical_name") or "").strip()
            if not name:
                raise ValueError("font suggestion requires canonical_name")
            db.upsert_overlay_font(
                name,
                vendor=suggestion.get("vendor"),
                category=suggestion.get("category"),
                languages=suggestion.get("languages"),
                license_category=str(
                    suggestion.get("license_category") or "unknown"),
                source=source,
            )
            written += 1
        elif kind == "mapping":
            jp = str(suggestion.get("jp_name")
                     or item.get("font_name") or "").strip()
            if not jp:
                raise ValueError("mapping suggestion requires jp_name")
            db.add_overlay_mapping(
                jp, suggestion.get("cn_name"), kind="mapping",
                method="human", confidence=1.0,
                source_file=str(item.get("source_file") or ""),
                source=source)
            written += 1
        elif kind == "negative_mapping":
            names = [str(n).strip() for n in (item.get("names") or [])
                     if str(n).strip()]
            if not names:
                names = [str(suggestion.get("jp_name")
                             or item.get("font_name") or "").strip()]
            for name in names:
                db.add_overlay_mapping(name, None, kind="negative_mapping",
                                       method="human", confidence=1.0,
                                       source_file=str(item.get("source_file") or ""),
                                       source=source)
                written += 1
        else:
            raise ValueError(f"unsupported suggestion kind: {kind!r}")
    for item in rejected:
        logger.info("更新建议否决，丢弃不写库: %s %s",
                    item.get("type"), item.get("font_name"))
    return {"adopted_items": len(adopted), "written": written,
            "rejected": rejected}
