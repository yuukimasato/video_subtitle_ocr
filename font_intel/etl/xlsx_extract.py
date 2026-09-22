# font_intel/etl/xlsx_extract.py
"""S0 xlsx 提取器：三大厂商 xlsx 对照表 → ETL 记录（与 rule_parser 同构）。

Seekladoom rar 内的结构化对照表是 xlsx（DynaFont/Fontworks/Morisawa 各一
份，行 = 日文字体名，列 = 简繁通用 / 简繁扩充 / 简体 / 繁体）。本模块把
每个非空单元格拆成"一候选一记录"（``lookup_jp_cn`` 的多候选行模型），
并经 S0.5 标准化清洗（:mod:`font_intel.etl.clean`）：日文名一格多名
展开、候选名注记剥离（(P) 品名括号保留）、注记进 note 字段。仅做结构
转换、不做字体知识推断；单元格内混写的注释、分类行（【…】）等打不动
的内容进 ``unresolved``（数据性内容 reason="unrecognized" 进复核
包，结构性行 reason="category_header"/"empty" 不进）。

约束：
- openpyxl 为惰性导入（仅 xlsx 路径需要；运行时不依赖）；
- 纯转换，无网络；映射关系只接受源表明示的内容（方案 §5.2）；
- 产出的记录置信度 0.9（结构化表格、源内容明示），与 rule_parser 的
  高置信句式同级，直接通过复核阈值入种子层。
"""

from __future__ import annotations

import logging
from pathlib import Path

from font_intel.etl.clean import (
    clean_candidate_name,
    expand_combined_jp_name,
    is_probably_name,
)
from font_intel.etl.normalize import normalize_text
from font_intel.etl.rule_parser import ParseResult, infer_vendor

logger = logging.getLogger(__name__)

__all__ = ["extract_records", "extract_file"]

# 表头首列标记（缺该列视为非对照表，fail loud）。
_HEADER_FIRST_CELL = "日文"
# 分类行前缀（如「【明朝体类（宋体类）】」）。
_CATEGORY_PREFIX = "【"
# 注释性内容标记：出现即整格转待人工（不拆候选，防把句子当字体名）。
_NOTE_MARKERS = ("注：", "注:", "没有", "参见", "同上")
# 句读：CJK 正文特征，字体名不会含这些。
_SENTENCE_PUNCT = "，。；！？"
# 单段 CJK 字符数超过该值视为句子（最长常见中文字体名 ≤ 12 字左右）。
_CJK_PROSE_LEN = 14

_CJK_RE_MAGIC = (0x3040, 0x30FF, 0x3400, 0x9FFF, 0xF900, 0xFAFF)


def _require_openpyxl():
    try:
        import openpyxl
    except ImportError as exc:  # pragma: no cover - 环境缺依赖时给出明确指引
        raise RuntimeError(
            "xlsx 对照表解析需要 openpyxl（开发依赖）：pip install openpyxl"
        ) from exc
    return openpyxl


def _cjk_count(text: str) -> int:
    return sum(
        1
        for ch in text
        if any(lo <= ord(ch) <= hi for lo, hi in
               ((_CJK_RE_MAGIC[0], _CJK_RE_MAGIC[1]),
                (_CJK_RE_MAGIC[2], _CJK_RE_MAGIC[3]),
                (_CJK_RE_MAGIC[4], _CJK_RE_MAGIC[5])))
    )


def _cell_text(value) -> str:
    """单元格 → 归一文本：None→空串，S0 字符归一（全角英数/破折号/零宽，
    与 txt 路径同款 normalize_text，不动汉字），换行折为空格。"""
    if value is None:
        return ""
    return " ".join(normalize_text(str(value)).split())


def _is_note_like(text: str) -> bool:
    """注释性内容：含句读 / 注释标记，或（作为整体、无候选切分时）
    CJK 超长。候选列表请改用 :func:`split_candidates` 的按段判断。"""
    if any(m in text for m in _NOTE_MARKERS):
        return True
    if any(p in text for p in _SENTENCE_PUNCT):
        return True
    return _cjk_count(text) > _CJK_PROSE_LEN


def split_candidates(cell: str) -> tuple[list[str], bool]:
    """单元格 → (候选段列表, 是否注释性整格)。

    "/" 切分候选；注释判定（句读、注释标记）作用于整格（候选名不会
    含句读），CJK 超长判定作用于**单段**（多候选格总 CJK 数自然超限，
    但每段仍是短字体名；无切分的整句注释单段即超长）。
    """
    if any(m in cell for m in _NOTE_MARKERS):
        return [], True
    if any(p in cell for p in _SENTENCE_PUNCT):
        return [], True
    segments = [s.strip() for s in cell.split("/") if s.strip()]
    if any(_cjk_count(s) > _CJK_PROSE_LEN for s in segments):
        return [], True
    return segments, False


def _header_category(label: str) -> str:
    """表头单元格 → 分类名（截掉括号说明，如「繁体（注：…）」→「繁体」）。"""
    for sep in ("（", "("):
        idx = label.find(sep)
        if idx > 0:
            label = label[:idx]
    return label.strip()


def extract_records(path: str | Path) -> ParseResult:
    """xlsx 对照表 → ParseResult（records + unresolved，与 rule_parser 同构）。"""
    openpyxl = _require_openpyxl()
    p = Path(path)
    result = ParseResult()
    wb = openpyxl.load_workbook(p, data_only=True, read_only=True)
    try:
        for ws in wb.worksheets:
            _extract_sheet(ws, str(p), result)
    finally:
        wb.close()
    return result


def extract_file(path: str | Path) -> ParseResult:
    """别名（与 rule_parser.parse_file 对齐的调用形态）。"""
    return extract_records(path)


def _unresolved(line_no: int, line: str, reason: str, source_file: str) -> dict:
    return {"source_file": source_file, "line_no": line_no,
            "line": line, "reason": reason}


def _extract_sheet(ws, source_file: str, result: ParseResult) -> None:
    rows = list(ws.iter_rows(values_only=True))
    if not rows:
        return

    # 定位表头行：首列 =「日文」。找不到视为非对照表 sheet，跳过。
    header_idx = next(
        (i for i, row in enumerate(rows)
         if row and _cell_text(row[0]) == _HEADER_FIRST_CELL),
        None,
    )
    if header_idx is None:
        if any(_cell_text(c) for row in rows for c in row):
            logger.debug("xlsx sheet %r 无表头行，跳过", ws.title)
        return

    header = [_cell_text(c) for c in rows[header_idx]]
    # 候选列：表头有分类名（截掉括号说明）的数据列；表头为空的列跳过。
    categories = {
        idx: _header_category(label)
        for idx, label in enumerate(header)
        if idx > 0 and label
    }

    for offset, row in enumerate(rows[header_idx + 1:], start=header_idx + 2):
        line_no = offset  # 行号从 1 起，含表头行计数，与 Excel 行号一致
        cells = [_cell_text(c) for c in row]
        jp_raw = cells[0].strip() if cells else ""
        if not jp_raw:
            continue
        if jp_raw.startswith(_CATEGORY_PREFIX):
            result.unresolved.append(
                _unresolved(line_no, jp_raw, "category_header", source_file))
            continue
        if _is_note_like(jp_raw):
            result.unresolved.append(
                _unresolved(line_no, jp_raw, "unrecognized", source_file))
            continue

        # S0.5：一格多名展开（DFGabiMincho Std/StdN 等，共享字重后缀
        # 记法），返回值首项为原组合串（源内容保真）。
        jp_names = expand_combined_jp_name(jp_raw)

        for idx, category in sorted(categories.items()):
            cell = cells[idx].strip() if idx < len(cells) else ""
            if not cell:
                continue
            display = f"{jp_raw} → {cell}（{category}）"
            segments, note_like = split_candidates(cell)
            if note_like:
                result.unresolved.append(
                    _unresolved(line_no, display, "unrecognized", source_file))
                continue
            for segment in segments:
                # S0.5：注记从品名剥离（(P) 品名括号保留），注记进 note。
                target, qualifiers = clean_candidate_name(segment)
                if not is_probably_name(target):
                    continue
                for pos, jp_name in enumerate(jp_names):
                    note_parts = [category]
                    if pos > 0:
                        # 展开名是清洗派生（源文件只有组合串），标注留痕；
                        # pos==0 为原组合串，源内容保真不打标。
                        note_parts.append("多名展开")
                    note_parts.extend(qualifiers)
                    result.records.append({
                        "type": "mapping",
                        "names": [jp_name],
                        "target": target,
                        "note": "；".join(p for p in note_parts if p),
                        "source_file": source_file,
                        "line_no": line_no,
                        "method": "rule",
                        "confidence": 0.9,
                        "vendor": infer_vendor(jp_name),
                    })
