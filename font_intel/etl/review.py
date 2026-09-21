# font_intel/etl/review.py
"""S5 人工复核包：低置信/待人工条目的导出、复核决策的落盘与读回。

定位
----
ETL 链路的"人工兜底"层：S1/S2/S3 产出中置信度不足或未结构化的条目
（``LLM 产出永不免审直接生效``）导出为 JSON 复核包，交
``components/font_map_review_dialog.py`` 逐条采纳/否决；决策结果
（结构化 dict）落盘为决策包 JSON，经 ``scripts/import_jp_cn_font_map.py
--import-review`` 由 ``db_loader.apply_review_decisions`` 合并入覆盖层。

本模块只做纯数据变换与本地 JSON 读写（标准库，无网络调用）；不做任何
数据库写入，也不依赖 PySide6——对话框与写库都发生在调用方。

复核条目字段（逐条）：``source_file / line_no / line（原文行或按解析字段
重构的展示行）/ record_type / names / target / confidence / method /
reason / note / suggestion``。``suggestion`` 仅在条目可写库时非空
（mapping/negative_mapping 的建议值）；其余类型与待人工行
``suggestion=None``，复核对话框中只展示不可采纳。
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from font_intel.etl.db_loader import MAP_SOURCE_TAG

logger = logging.getLogger(__name__)

__all__ = [
    "REVIEW_SCHEMA",
    "DECISIONS_SCHEMA",
    "DEFAULT_CONFIDENCE_THRESHOLD",
    "build_review_items",
    "build_review_package",
    "write_review_package",
    "read_review_package",
    "decisions_package",
    "write_decisions_package",
    "read_decisions_package",
]

REVIEW_SCHEMA = "vso_font_map_review/1"
DECISIONS_SCHEMA = "vso_font_map_review_decisions/1"

# 置信度低于该阈值的记录进复核包；rule 解析 0.85（append 类启发式映射）
# 与 LLM 默认 0.5 均落入复核，0.9/0.95 的高置信句式直接通过。
DEFAULT_CONFIDENCE_THRESHOLD = 0.9


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


# ── 复核条目构建 ─────────────────────────────────────────────────


def _display_line(rec: dict) -> str:
    """按解析字段重构展示行（ETL 记录本身不含原始行文本，仅供人工参考；
    权威溯源是 source_file + line_no）。"""
    rtype = str(rec.get("type") or "")
    names = [str(n) for n in (rec.get("names") or [])]
    note = str(rec.get("note") or "")
    if rtype == "mapping" and rec.get("target"):
        return f"{names[0] if names else ''} → {rec['target']}"
    if rtype == "rename" and len(names) >= 2:
        return f"{names[0]}（旧：{names[1]}）"
    if rtype == "negative_mapping":
        body = " & ".join(names)
        return f"{body}，{note}" if note else body
    if rtype == "weight_list":
        return f"{note}字重：{'、'.join(names)}"
    return "、".join(names)


def build_review_items(
    records: list[dict],
    pending: list[dict] = (),
    *,
    threshold: float = DEFAULT_CONFIDENCE_THRESHOLD,
) -> list[dict]:
    """低置信记录 + 待人工行 → 复核条目列表（按文件与行号稳定排序）。

    Args:
        records: ETL 记录（rule_parser / llm_structurer 产出同构，
            ``confidence < threshold`` 的进入复核包）。
        pending: 待人工行（llm_structurer 的 ``pending_human`` 同构，
            ``{"source_file", "line_no", "line", "reason"}``，可选带
            ``method``）；规则阶段的 unresolved 行由调用方补
            ``method="rule"`` 后传入。
        threshold: 置信度阈值，低于它的记录进入复核。
    """
    items: list[dict] = []
    for rec in records or []:
        conf = rec.get("confidence")
        conf = float(conf) if isinstance(conf, (int, float)) else 0.0
        if conf >= threshold:
            continue
        rtype = str(rec.get("type") or "")
        names = [str(n) for n in (rec.get("names") or [])]
        target = rec.get("target")
        suggestion: Optional[dict] = None
        if rtype == "mapping" and target and names:
            suggestion = {"kind": "mapping", "jp_name": names[0],
                          "cn_name": str(target)}
        elif rtype == "negative_mapping" and names:
            suggestion = {"kind": "negative_mapping", "jp_name": names[0],
                          "cn_name": None}
        items.append({
            "source_file": str(rec.get("source_file") or ""),
            "line_no": int(rec.get("line_no") or 0),
            "line": _display_line(rec),
            "record_type": rtype,
            "names": names,
            "target": target,
            "confidence": conf,
            "method": str(rec.get("method") or ""),
            "reason": "low_confidence",
            "note": str(rec.get("note") or ""),
            "suggestion": suggestion,
        })
    for entry in pending or []:
        line_no = entry.get("line_no")
        items.append({
            "source_file": str(entry.get("source_file") or ""),
            "line_no": int(line_no) if line_no is not None else 0,
            "line": str(entry.get("line") or entry.get("raw") or ""),
            "record_type": "pending",
            "names": [],
            "target": None,
            "confidence": None,
            "method": str(entry.get("method") or ""),
            "reason": str(entry.get("reason") or ""),
            "note": "",
            "suggestion": None,
        })
    items.sort(key=lambda item: (item["source_file"], item["line_no"]))
    return items


def build_review_package(
    items: list[dict],
    *,
    threshold: float = DEFAULT_CONFIDENCE_THRESHOLD,
    source_tag: str = MAP_SOURCE_TAG,
) -> dict:
    """复核条目 → 复核包 dict（带 schema 标记与统计）。"""
    items = list(items or [])
    return {
        "schema": REVIEW_SCHEMA,
        "generated_at": _now_iso(),
        "source_tag": source_tag,
        "confidence_threshold": float(threshold),
        "stats": {
            "items": len(items),
            "actionable": sum(1 for i in items if i.get("suggestion")),
        },
        "items": items,
    }


# ── JSON 落盘与读回 ──────────────────────────────────────────────


def _dump_json(payload: dict, path: str | Path) -> None:
    text = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    Path(path).write_text(text, encoding="utf-8")


def _load_json(path: str | Path, expected_schema: str) -> dict:
    with open(path, encoding="utf-8") as fh:
        payload = json.load(fh)
    if not isinstance(payload, dict) or payload.get("schema") != expected_schema:
        raise ValueError(
            f"unexpected schema in {path}: expected {expected_schema!r}, "
            f"got {payload.get('schema') if isinstance(payload, dict) else type(payload).__name__!r}"
        )
    return payload


def write_review_package(package: dict, path: str | Path) -> None:
    """复核包 → JSON 文件（UTF-8、ensure_ascii=False，人工可读）。"""
    _dump_json(package, path)


def read_review_package(path: str | Path) -> dict:
    """读回复核包；schema 不符抛 ValueError（防错导决策包/他类文件）。"""
    return _load_json(path, REVIEW_SCHEMA)


def decisions_package(
    adopted: list[dict],
    rejected: list[dict],
    *,
    source_tag: str = MAP_SOURCE_TAG,
) -> dict:
    """复核决策 → 决策包 dict（``FontMapReviewDialog.get_result()`` 结果
    由调用方经此落盘，供 CLI ``--import-review`` 读回）。"""
    return {
        "schema": DECISIONS_SCHEMA,
        "generated_at": _now_iso(),
        "source_tag": source_tag,
        "adopted": list(adopted or []),
        "rejected": list(rejected or []),
    }


def write_decisions_package(package: dict, path: str | Path) -> None:
    """决策包 → JSON 文件。"""
    _dump_json(package, path)


def read_decisions_package(path: str | Path) -> dict:
    """读回决策包；schema 不符抛 ValueError。"""
    return _load_json(path, DECISIONS_SCHEMA)
