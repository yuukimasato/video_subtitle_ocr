# font_intel/etl/rule_parser.py
"""S1 规则解析器：把归一后的对照文本行解析为结构化记录。

约束：
- 纯函数，无 IO、无网络；``parse_lines`` 的输入假定已通过 S0 归一；
- 句式覆盖：映射（→ / ->）、改名（(旧：…)）、负映射（& 简繁别名 +
  "没有…对应/版本"）、追加搭配（note=pairing）、追加（note=append）、
  字重清单（weight_list）、简繁通用标记（flag_sc_tc）、待办（todo）、
  坑名裸清单（pitfall，模糊句式给低置信度）；
- 规则打不动的行进 ``unresolved``（保留原行文本与行号），留给后续
  S2 LLM 结构化任务，不强行归类；
- ``vendor`` 仅按 ``VENDOR_PREFIX_RULES`` 前缀推断，判不了留空串，不猜。

记录约定：``names`` 为涉及字体名列表；``target`` 仅 mapping 类有值
（其余为 None）；rename 的 ``names`` 顺序固定为新名在前、旧名在后。
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path

from font_intel.etl.normalize import decode_bytes, normalize_text

logger = logging.getLogger(__name__)

__all__ = [
    "VENDOR_PREFIX_RULES",
    "ParseResult",
    "infer_vendor",
    "parse_lines",
    "parse_bytes",
    "parse_file",
]

# 厂商前缀 → 厂商名；调用方按序匹配（长前缀在前，避免短前缀遮蔽）。
VENDOR_PREFIX_RULES: tuple[tuple[str, str], ...] = (
    ("A-OTF", "Morisawa"),
    ("FOT-", "Fontworks"),
    ("華康", "DynaFont"),
    ("华康", "DynaFont"),
    ("方正", "Founder"),
    ("汉仪", "HanYi"),
    ("蒙纳", "Monotype"),
    ("森澤", "Morisawa"),
    ("森泽", "Morisawa"),
    ("DF", "DynaFont"),
)

_ARROW_RE = re.compile(r"^(?P<src>.+?)\s*(?:→|->)\s*(?P<dst>.+)$")
_RENAME_RE = re.compile(r"^(?P<new>.+?)[（(]\s*旧\s*[:：]\s*(?P<old>.+?)[）)]\s*$")
_NEGATIVE_RE = re.compile(r"[，,]?\s*(?P<desc>没有.{0,40}?(?:对应|版本).*)$")
_PAIRING_RE = re.compile(r"^(?P<base>.+?)\s*追加搭配\s*[:：]\s*(?P<partner>.+)$")
_APPEND_RE = re.compile(r"^(?P<base>.+?)\s*追加\s*[:：]\s*(?P<added>.+)$")
_TODO_RE = re.compile(r"^(?P<name>.+?)(?:后\s*\d+\s*个字重)?待(?:追加|补充|补全|整理|确认)")
_WEIGHT_RE = re.compile(r"^(?P<grade>[A-Za-zＡ-Ｚａ-ｚ])\s*-?\s*字重\s*[:：]\s*(?P<rest>.+)$")
_AMP_SPLIT_RE = re.compile(r"&|＆")
_WEIGHT_SPLIT_RE = re.compile(r"[，,、;；/]")
_PITFALL_SPLIT_RE = re.compile(r"\s*/\s*|\s*、\s*|\s+")
# 字体名可含 CJK/假名/字母数字/连字符等；含括号冒号等标点的段落不是裸清单
_NAMEISH_RE = re.compile(r"^[\w\-·.]+$")
_HAS_CJK_RE = re.compile(r"[\u3040-\u30FF\u3400-\u9FFF\uF900-\uFAFF]")


@dataclass
class ParseResult:
    """单次解析产出：结构化记录 + 规则打不动的行。"""

    records: list[dict] = field(default_factory=list)
    unresolved: list[dict] = field(default_factory=list)

    def stats(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for rec in self.records:
            counts[rec["type"]] = counts.get(rec["type"], 0) + 1
        counts["unresolved"] = len(self.unresolved)
        return counts


def infer_vendor(name: str) -> str:
    """按前缀表推断厂商；无命中返回空串（不猜）。"""
    for prefix, vendor in VENDOR_PREFIX_RULES:
        if name.startswith(prefix):
            return vendor
    return ""


def _record(
    rtype: str,
    names: list[str],
    line_no: int,
    source_file: str,
    *,
    target: str | None = None,
    note: str = "",
    confidence: float,
) -> dict:
    return {
        "type": rtype,
        "names": names,
        "target": target,
        "note": note,
        "source_file": source_file,
        "line_no": line_no,
        "method": "rule",
        "confidence": confidence,
        "vendor": infer_vendor(names[0]) if names else "",
    }


def _unresolved(line_no: int, line: str, reason: str, source_file: str) -> dict:
    return {
        "source_file": source_file,
        "line_no": line_no,
        "line": line,
        "reason": reason,
    }


def _parse_line(line: str, line_no: int, source_file: str) -> dict | None:
    """按句式优先级解析单行；打不动返回 None（不强行归类）。"""
    m = _ARROW_RE.match(line)
    if m:
        return _record("mapping", [m.group("src").strip()], line_no, source_file,
                       target=m.group("dst").strip(), confidence=0.95)

    m = _RENAME_RE.match(line)
    if m:
        return _record("rename", [m.group("new").strip(), m.group("old").strip()],
                       line_no, source_file, note="rename", confidence=0.95)

    # 负映射：&/＆ 两侧是同一字体的简繁写法，拆为别名列表
    if "&" in line or "＆" in line:
        m = _NEGATIVE_RE.search(line)
        if m:
            name_part = line[: m.start()].strip().rstrip("，,")
            names = [p.strip() for p in _AMP_SPLIT_RE.split(name_part) if p.strip()]
            if names:
                return _record("negative_mapping", names, line_no, source_file,
                               note=m.group("desc").strip(), confidence=0.9)

    m = _PAIRING_RE.match(line)
    if m:
        return _record("mapping", [m.group("base").strip()], line_no, source_file,
                       target=m.group("partner").strip(), note="pairing",
                       confidence=0.9)

    m = _TODO_RE.match(line)
    if m:
        return _record("todo", [m.group("name").strip().rstrip("，, ")],
                       line_no, source_file, confidence=0.9)

    m = _WEIGHT_RE.match(line)
    if m:
        grade = m.group("grade")
        if 0xFF10 <= ord(grade) <= 0xFF5A:  # 全角档位 → 半角
            grade = chr(ord(grade) - 0xFEE0)
        names = [p.strip() for p in _WEIGHT_SPLIT_RE.split(m.group("rest").strip())
                 if p.strip()]
        if names:
            return _record("weight_list", names, line_no, source_file,
                           note=grade, confidence=0.95)

    if "简繁通用" in line:
        m = _APPEND_RE.match(line)
        if m:
            names = [m.group("base").strip(), m.group("added").strip()]
        else:
            names = [line.replace("简繁通用", "").strip()]
        names = [n for n in names if n]
        if names:
            return _record("flag_sc_tc", names, line_no, source_file,
                           note="sc_tc_common", confidence=0.9)

    m = _APPEND_RE.match(line)
    if m:
        return _record("mapping", [m.group("base").strip()], line_no, source_file,
                       target=m.group("added").strip(), note="append",
                       confidence=0.85)

    # 坑名裸清单：至少两段、每段形似字体名、至少一段含 CJK/假名
    segments = [s.strip() for s in _PITFALL_SPLIT_RE.split(line) if s.strip()]
    if (len(segments) >= 2
            and all(_NAMEISH_RE.match(s) for s in segments)
            and any(_HAS_CJK_RE.search(s) for s in segments)):
        return _record("pitfall", segments, line_no, source_file, confidence=0.7)

    return None


def parse_lines(lines: list[str], source_file: str = "") -> ParseResult:
    """逐行解析（行号 1 起）；输入假定已过 S0 归一。"""
    result = ParseResult()
    for line_no, raw_line in enumerate(lines, start=1):
        line = raw_line.strip()
        body = raw_line.rstrip("\r\n")  # unresolved 保留原行，仅去行尾换行符
        if not line:
            result.unresolved.append(_unresolved(line_no, body, "empty", source_file))
            continue
        if line.startswith("#") or line.startswith("//"):
            result.unresolved.append(_unresolved(line_no, body, "comment", source_file))
            continue
        record = _parse_line(line, line_no, source_file)
        if record is None:
            result.unresolved.append(
                _unresolved(line_no, body, "unrecognized", source_file))
        else:
            result.records.append(record)
    return result


def parse_bytes(raw: bytes, source_file: str = "") -> ParseResult:
    """bytes → S0 解码归一 → 按 LF 切行 → S1（文件末尾换行不产生幽灵空行）。"""
    text = normalize_text(decode_bytes(raw))
    lines = text.split("\n")
    if lines and lines[-1] == "":
        lines.pop()
    return parse_lines(lines, source_file=source_file)


def parse_file(path: str | Path) -> ParseResult:
    """读文件 bytes 并解析；文件不存在时抛 FileNotFoundError。"""
    p = Path(path)
    return parse_bytes(p.read_bytes(), source_file=str(p))
