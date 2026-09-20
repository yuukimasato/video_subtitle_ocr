# font_intel/etl/normalize.py
"""S0 文本归一：编码探测与字符级清洗（映射表 ETL 的前置层）。

约束：
- 编码探测链 UTF-8 → GBK → Big5 → latin-1，成功即停，不做语言推断；
- 只做无损形式归一（换行 / 全角数字字母空格 / 破折号波浪线 / BOM /
  零宽字符），不得改动汉字本身——简繁异体保持原样，归并属后续任务。
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

__all__ = ["decode_bytes", "normalize_text"]

# 探测链按序尝试；latin-1 对任意字节序列都不抛错，作为最终兜底。
_ENCODING_CHAIN = ("utf-8", "gbk", "big5", "latin-1")


def _build_table() -> dict[int, int | None]:
    table: dict[int, int | None] = {}
    # 全角数字 ０-９ / 大写 Ａ-Ｚ / 小写 ａ-ｚ → 半角（偏移 0xFEE0）
    for lo, hi in ((0xFF10, 0xFF19), (0xFF21, 0xFF3A), (0xFF41, 0xFF5A)):
        for cp in range(lo, hi + 1):
            table[cp] = cp - 0xFEE0
    table[0x3000] = 0x20  # 表意空格 → 普通空格
    # 破折号/减号类 → ASCII '-'
    for cp in (0x2010, 0x2011, 0x2012, 0x2013, 0x2014, 0x2015, 0x2212, 0xFF0D):
        table[cp] = 0x2D
    # 波浪线类 → ASCII '~'
    for cp in (0x301C, 0x3030, 0xFF5E):
        table[cp] = 0x7E
    # BOM 与零宽/双向控制字符 → 删除
    for cp in (0xFEFF, 0x200B, 0x200C, 0x200D, 0x2060, 0x00AD, 0x200E, 0x200F):
        table[cp] = None
    return table


_NORMALIZE_TABLE: dict[int, int | None] = _build_table()


def decode_bytes(raw: bytes) -> str:
    """按探测链解码字节序列：UTF-8 → GBK → Big5 → latin-1，成功即停。"""
    for encoding in _ENCODING_CHAIN:
        try:
            return raw.decode(encoding)
        except (UnicodeDecodeError, ValueError):
            continue
    # 理论不可达：latin-1 不抛 UnicodeDecodeError
    return raw.decode("latin-1", errors="replace")


def normalize_text(text: str) -> str:
    """行尾/全角/破折号/不可见字符归一；不改任何汉字。"""
    if not text:
        return text
    # CRLF / 孤立 CR → LF
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    return text.translate(_NORMALIZE_TABLE)
