# font_intel/etl/__init__.py
"""映射表 ETL 子包：S0 归一（``normalize``）+ S1 规则解析（``rule_parser``）。

S2（对 unresolved 行做 LLM 结构化）由后续任务在此包内扩展。
"""

from font_intel.etl.normalize import decode_bytes, normalize_text
from font_intel.etl.rule_parser import (
    VENDOR_PREFIX_RULES,
    ParseResult,
    infer_vendor,
    parse_bytes,
    parse_file,
    parse_lines,
)

__all__ = [
    "decode_bytes",
    "normalize_text",
    "VENDOR_PREFIX_RULES",
    "ParseResult",
    "infer_vendor",
    "parse_lines",
    "parse_bytes",
    "parse_file",
]
