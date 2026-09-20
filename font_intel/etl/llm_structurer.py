# font_intel/etl/llm_structurer.py
"""S2 LLM 结构化：把 S1 规则打不动的行交给 LLM 做分类与切分，再硬校验。

定位与红线
----------
LLM 在 ETL 链路里只是"结构化器"，不是"知识源"：system prompt 明确要求
只做行类型分类与字体名切分，禁止补充源行没有的字体对应关系；代码侧再用
两层硬校验兜住幻觉：

- 防幻觉子串校验：mapping/negative_mapping/rename 的每个字体名（names 与
  target）归一化后必须是源行原文的子串，否则整条降 ``pending_human``
  （reason=``hallucinated_name``）；
- S3 词表校验：提供 ``word_db`` 时，过了子串关的名字还要过
  ``FontsDB.lookup_font()``，两条都过才收录；词表未收录 →
  ``pending_human``（reason=``unknown_font_name``，留人工复核）。

降级（全局硬约束）
------------------
所有服务端调用走 ``core.llm_client.call_llm``（自带 429 有界退避）；退避
耗尽抛出的 ``LlmApiError``（及其余未预期异常）只让当前批整体降
``pending_human``（reason=``llm_failed``），不重试、不抛出、绝不中断，
单批失败不影响其他批。api_key 为空时全部直接进 ``pending_human``
（reason=``no_api_key``），不触网。
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from typing import Any, Optional

from core import llm_client
from core.llm_client import LlmApiError
from core.llm_prompts import get_prompt
from font_intel.etl.normalize import normalize_text
from font_intel.etl.rule_parser import infer_vendor
from font_intel.fonts_db import FontsDB

try:
    import json_repair
except ImportError:  # pragma: no cover - json_repair is a declared dependency
    json_repair = None

logger = logging.getLogger(__name__)

__all__ = [
    "PROMPT_ID",
    "EtlLlmConfig",
    "StructureResult",
    "structure_unresolved",
]

# prompt 模板 id（core/prompts/etl_font_map_struct.md）
PROMPT_ID = "etl_font_map_struct"

# 六类记录；LLM 返回六类之外一律整批拒绝。
VALID_TYPES = frozenset(
    {"mapping", "rename", "negative_mapping", "pitfall", "flag_sc_tc", "todo"}
)

# names/target 必须过防幻觉与词表校验的记录类型。
_NAME_BEARING_TYPES = frozenset({"mapping", "negative_mapping", "rename"})

_DEFAULT_CONFIDENCE = 0.5


@dataclass
class EtlLlmConfig:
    """S2 结构化的 LLM 调用配置。"""

    api_key: str = ""
    api_base_url: str = ""
    model: str = "gpt-4o-mini"
    batch_size: int = 20
    temperature: float = 0.1
    timeout_sec: float = 120.0


@dataclass
class StructureResult:
    """S2 产出：成功结构化的记录 + 需人工复核的行。"""

    records: list[dict] = field(default_factory=list)
    pending_human: list[dict] = field(default_factory=list)

    def stats(self) -> dict[str, int]:
        """按记录类型计数，外加 ``pending_human`` 总数（对齐 ParseResult 风格）。"""
        counts: dict[str, int] = {}
        for rec in self.records:
            counts[rec["type"]] = counts.get(rec["type"], 0) + 1
        counts["pending_human"] = len(self.pending_human)
        return counts


class _LlmOutputInvalid(Exception):
    """批次输出不符合约定 schema 的内部信号。"""


def _call_llm(messages: list[dict], cfg: EtlLlmConfig) -> Any:
    """薄封装：便于测试 monkeypatch；退避语义归 core.llm_client。

    除 LlmApiError 外的异常（含 tenacity.RetryError 等残漏形态与空响应
    ValueError）一并收口为 LlmApiError，保证上层只面对单一异常面。
    """
    try:
        return llm_client.call_llm(
            messages=messages,
            model=cfg.model,
            base_url=cfg.api_base_url or None,
            api_key=cfg.api_key or None,
            temperature=cfg.temperature,
            timeout=cfg.timeout_sec,
        )
    except LlmApiError:
        raise
    except Exception as exc:
        raise LlmApiError(
            f"LLM API call failed: {type(exc).__name__}: {exc}"
        ) from exc


# ── 输入行归一 ───────────────────────────────────────────────────


def _normalize_input_line(line: Any) -> dict:
    """兼容 rule_parser.unresolved（``line`` 键）与 ``{"raw", ...}`` 形态。"""
    if isinstance(line, str):
        return {"raw": line, "line_no": 0, "source_file": "", "reason": ""}
    raw = line.get("raw")
    if raw is None:
        raw = line.get("line", "")
    try:
        line_no = int(line.get("line_no") or 0)
    except (TypeError, ValueError):
        line_no = 0
    return {
        "raw": str(raw if raw is not None else ""),
        "line_no": line_no,
        "source_file": str(line.get("source_file") or ""),
        "reason": str(line.get("reason") or ""),
    }


def _pending(info: dict, reason: str) -> dict:
    return {
        "source_file": info["source_file"],
        "line_no": info["line_no"],
        "line": info["raw"],
        "reason": reason,
    }


# ── prompt 组装 ──────────────────────────────────────────────────


def _build_messages(batch: list[dict]) -> list[dict]:
    system = get_prompt(PROMPT_ID)
    payload = [{"id": i, "text": info["raw"]} for i, info in enumerate(batch)]
    user = "待结构化行共 {} 行（已带 id）：\n{}".format(
        len(batch), json.dumps(payload, ensure_ascii=False)
    )
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]


# ── 响应解析（整批级校验，任一不符整批作废） ─────────────────────


def _strip_code_fence(raw: str) -> str:
    s = str(raw).strip()
    if s.startswith("```"):
        s = re.sub(r"^```(?:json)?\s*", "", s, flags=re.IGNORECASE)
        s = re.sub(r"\s*```\s*$", "", s)
    return s.strip()


def _parse_llm_items(content: str, batch_size: int) -> Optional[list[dict]]:
    """解析 LLM 批次输出为归一化条目列表；任何 schema 不符返回 None。

    要求：JSON 数组（``json.loads`` 失败再用 ``json_repair``）、长度与批次
    一致、id 恰好覆盖 0..n-1、type 在六类内、names 为非空字符串列表。
    """
    s = _strip_code_fence(content)
    data: Any = None
    try:
        data = json.loads(s)
    except (ValueError, TypeError):
        if json_repair is None:
            return None
        try:
            data = json_repair.loads(s)
        except Exception:
            return None
    if not isinstance(data, list) or len(data) != batch_size:
        return None

    by_id: dict[str, dict] = {}
    for item in data:
        if not isinstance(item, dict):
            return None
        rtype = item.get("type")
        if rtype not in VALID_TYPES:
            return None
        names_raw = item.get("names")
        if (not isinstance(names_raw, list) or not names_raw
                or not all(isinstance(n, str) and n.strip() for n in names_raw)):
            return None
        target = item.get("target")
        if target is not None and not isinstance(target, str):
            return None
        conf = item.get("confidence", _DEFAULT_CONFIDENCE)
        if isinstance(conf, bool) or not isinstance(conf, (int, float)):
            conf = _DEFAULT_CONFIDENCE
        key = str(item.get("id"))
        if key in by_id:  # id 重复
            return None
        by_id[key] = {
            "type": rtype,
            "names": [n.strip() for n in names_raw],
            "target": (target.strip() or None) if isinstance(target, str) else None,
            "confidence": float(conf),
        }
    expected = {str(i) for i in range(batch_size)}
    if set(by_id) != expected:
        return None
    return [by_id[str(i)] for i in range(batch_size)]


# ── 硬校验（防幻觉 + 词表） ──────────────────────────────────────


def _name_bearing_values(item: dict) -> list[str]:
    """需要过校验的字体名：names 全部 + mapping 的 target。"""
    values = list(item["names"])
    if item["type"] == "mapping" and item["target"]:
        values.append(item["target"])
    return [v for v in values if v]


def _hallucinated(values: list[str], raw_line: str) -> bool:
    """归一化后名字必须是源行原文子串（防 LLM 补充源行没有的名字）。"""
    hay = normalize_text(raw_line)
    return any(normalize_text(v) not in hay for v in values)


def _unknown_names(
    values: list[str], word_db: FontsDB, cache: dict[str, bool]
) -> list[str]:
    """S3 词表校验：canonical_name 或别名都不命中即未知。"""
    unknown = []
    for value in values:
        hit = cache.get(value)
        if hit is None:
            hit = word_db.lookup_font(value) is not None
            cache[value] = hit
        if not hit:
            unknown.append(value)
    return unknown


def _build_record(item: dict, info: dict) -> dict:
    names = item["names"]
    return {
        "type": item["type"],
        "names": names,
        # 对齐 rule_parser：target 仅 mapping 类有值（其余为 None）
        "target": item["target"] if item["type"] == "mapping" else None,
        "note": "rename" if item["type"] == "rename" else "",
        "source_file": info["source_file"],
        "line_no": info["line_no"],
        "method": "llm",
        "confidence": item["confidence"],
        "vendor": infer_vendor(names[0]) if names else "",
    }


def _materialize(
    info: dict,
    item: dict,
    word_db: Optional[FontsDB],
    vocab_cache: dict[str, bool],
) -> tuple[str, dict]:
    """对单条 LLM 输出做硬校验并落型：("record", rec) 或 ("pending", entry)。"""
    if item["type"] in _NAME_BEARING_TYPES:
        values = _name_bearing_values(item)
        if values and _hallucinated(values, info["raw"]):
            return "pending", _pending(info, "hallucinated_name")
        if word_db is not None and values:
            if _unknown_names(values, word_db, vocab_cache):
                return "pending", _pending(info, "unknown_font_name")
    return "record", _build_record(item, info)


# ── 批处理 ───────────────────────────────────────────────────────


def _structure_batch(
    batch: list[dict],
    cfg: EtlLlmConfig,
    result: StructureResult,
    word_db: Optional[FontsDB],
    vocab_cache: dict[str, bool],
    log: logging.Logger,
) -> None:
    messages = _build_messages(batch)
    try:
        response = _call_llm(messages, cfg)
        content = response.choices[0].message.content
        items = _parse_llm_items(content, len(batch))
        if items is None:
            raise _LlmOutputInvalid
    except LlmApiError:
        # 429 有界退避耗尽等：本批整体降级，不重试、不中断。
        log.warning("ETL LLM 结构化批次失败（降级 llm_failed）：lines=%s",
                    [info["line_no"] for info in batch])
        result.pending_human.extend(_pending(info, "llm_failed") for info in batch)
        return
    except _LlmOutputInvalid:
        log.warning("ETL LLM 输出不符合 schema（降级 llm_output_invalid）：lines=%s",
                    [info["line_no"] for info in batch])
        result.pending_human.extend(
            _pending(info, "llm_output_invalid") for info in batch)
        return
    except Exception:  # 兜底：任何异常都不允许中断整条任务链
        log.exception("ETL LLM 结构化批次未预期异常（降级 llm_failed）")
        result.pending_human.extend(_pending(info, "llm_failed") for info in batch)
        return

    for info, item in zip(batch, items):
        kind, payload = _materialize(info, item, word_db, vocab_cache)
        if kind == "record":
            result.records.append(payload)
        else:
            result.pending_human.append(payload)


# ── 核心入口 ─────────────────────────────────────────────────────


def structure_unresolved(
    lines: list[dict],
    cfg: EtlLlmConfig,
    *,
    word_db: Optional[FontsDB] = None,
    log: Optional[logging.Logger] = None,
) -> StructureResult:
    """S2 入口：规则打不动的行 → LLM 结构化 → 硬校验 → 记录/人工复核。

    Args:
        lines: 形如 ``{"raw", "line_no", "reason"}`` 或 rule_parser.unresolved
            同构（``{"source_file", "line_no", "line", "reason"}``）的行列表。
        cfg: LLM 调用配置。
        word_db: 提供时启用 S3 词表校验；``None`` 只做子串校验。
        log: 可选 logger；缺省用模块 logger。

    Returns:
        ``StructureResult``：``.records``（method='llm'）+ ``.pending_human``。
    """
    log = log if log is not None else logger
    result = StructureResult()
    items = [_normalize_input_line(line) for line in (lines or [])]
    if not items:
        return result

    if not (cfg.api_key or "").strip():
        # 无凭据不触网：全部留人工，绝不抛错。
        result.pending_human.extend(_pending(info, "no_api_key") for info in items)
        return result

    step = max(1, int(cfg.batch_size))
    vocab_cache: dict[str, bool] = {}
    for start in range(0, len(items), step):
        batch = items[start:start + step]
        _structure_batch(batch, cfg, result, word_db, vocab_cache, log)
    return result
