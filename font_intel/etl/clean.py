# font_intel/etl/clean.py
"""S0.5 标准化清洗：对照表单元格 → 规范品名 + 注记留痕。

背景：Seekladoom 对照表是笔记式整理，单元格混写品名与注记。本模块把
两者拆开——品名进 ``cn_name``/``jp_name``，注记进 ``note`` 字段
（schema v4 的 ``jp_cn_font_map.note`` 列），只做文本层拆解，不做字体
知识推断（方案 §5.2：映射只接受源文件明示的内容）。

联网核实的命名约定（2026-09-22）：
- DynaFont 半角 ``(P)`` = Proportional（调合字/比例字宽），是正式品名
  的组成部分，**不得剥离**（来源：dynacw.com.hk 官方 FAQ）；
- ``FOT-``（Fontworks）、``A-OTF``（Morisawa）是厂商+OpenType 格式
  前缀，属品名，保留原样；
- 全角括号注记（台繁/港繁/开粗体/旧：…/注：…等）与"括号内含空格的
  英数交叉引用"（如 ``华康宋体W7(华康宋体 Std W7)``）是笔记式注记，
  从品名剥离进 note。
"""

from __future__ import annotations

import re

__all__ = [
    "clean_candidate_name",
    "expand_combined_jp_name",
    "is_probably_name",
]

# 括号段（不跨层）；全角/半角都覆盖，关键词判定区分注记与品名组成部分。
_PAREN_RE = re.compile(r"[（(]([^（）()]*)[）)]")

# 注记关键词：地区标记 / 字重操作 / 新旧 / 待办 / 说明文字。
_ANNOTATION_KEYWORDS = (
    "台繁", "港繁", "陸繁", "陆繁", "台灣", "台湾", "香港",
    "簡", "简", "繁", "開粗體", "开粗体", "開斜體", "开斜体",
    "舊", "旧", "待", "暫", "暂", "缺", "無", "无", "僅", "仅",
    "註", "注", "參見", "参见", "同上", "字重",
)

# 交叉引用括号：内容同时含空白与英文字母（"(华康宋体 Std W7)"、
# "(Source Han Serif)"）；"(P)"/"(Pro)" 无空格，是品名组成部分，保留。
_SHARED_TOKEN_RE = re.compile(
    r"^(?:W\d+|Pr\d+|StdN|Std|Pro|Bold|Black|Heavy|Medium|Light|Regular|[ULMDB])$"
)


def _has_cjk(text: str) -> bool:
    return any(
        0x3040 <= ord(ch) <= 0x30FF or 0x3400 <= ord(ch) <= 0x9FFF
        or 0xF900 <= ord(ch) <= 0xFAFF
        for ch in text
    )


def _is_annotation(content: str) -> bool:
    return any(k in content for k in _ANNOTATION_KEYWORDS)


def _is_xref(content: str) -> bool:
    content = content.strip()
    return " " in content and any(
        ch.isalpha() and ord(ch) < 128 for ch in content
    )


def clean_candidate_name(text: str) -> tuple[str, list[str]]:
    """候选单元格文本 → (规范品名, 注记列表)。

    剥离注记括号（关键词注记 + 含空格英数的交叉引用），保留 ``(P)``、
    ``(Pro)`` 等无空格品名括号；首尾分隔符与多余空白归一。品名剥空
    （整段都是注记）时返回空串，由调用方丢弃该段。
    """
    name = " ".join(text.split())
    qualifiers: list[str] = []
    for _ in range(4):  # 嵌套括号迭代剥离上限（防病态输入）
        changed = False

        def _repl(m: re.Match) -> str:
            nonlocal changed
            content = m.group(1)
            if _is_annotation(content) or _is_xref(content):
                qualifiers.append(content.strip())
                changed = True
                return " "
            return m.group(0)

        name = _PAREN_RE.sub(_repl, name)
        if not changed:
            break
    name = " ".join(name.split()).strip(" /、；;，,．.")
    return name, [q for q in qualifiers if q]


def is_probably_name(text: str) -> bool:
    """剥注记后是否还像字体名：非空且含字母数字。"""
    return bool(text) and any(ch.isalnum() for ch in text)


def expand_combined_jp_name(cell: str) -> list[str]:
    """日文名一格多名展开（共享前后缀记法）。

    表格里 ``A B/C`` 或 ``A B/C/E T`` 是"同系列多名"笔记记法：
    ``DFGabiMincho Std/StdN`` = DFGabiMincho Std + DFGabiMincho StdN；
    ``DFHSMinchoR Pro-5/Pro-6/Pro-6N W7`` = 三个名共享字重后缀 W7。
    展开规则：首个分段提供公共前缀，后续短段补前缀；末尾共享 token
    仅在命中字重/样式白名单（W7、Pr6、Std、B…）时分发到每个展开名，
    防止把只属于末段的名字误当共享。仅对纯英数（无 CJK）单元格生效；
    返回值含原组合串（源文件明示内容保真），展开名附后。
    """
    cell = " ".join(cell.split())
    if "/" not in cell or _has_cjk(cell):
        return [cell]
    core, shared = cell, ""
    head, sep, tail = cell.rpartition(" ")
    if sep and "/" in head and _SHARED_TOKEN_RE.match(tail):
        core, shared = head, tail
    segments = [s.strip() for s in core.split("/") if s.strip()]
    if len(segments) < 2:
        return [cell]
    first = segments[0]
    prefix = first.rsplit(" ", 1)[0] if " " in first else ""
    suffix = f" {shared}" if shared else ""
    expanded = [first + suffix]
    for seg in segments[1:]:
        base = seg if (prefix and seg.startswith(prefix + " ")) else (
            f"{prefix} {seg}" if prefix else seg)
        expanded.append(base + suffix)
    out = [cell]  # 原组合串保真在前，展开名去重附后
    for name in expanded:
        if name and name not in out:
            out.append(name)
    return out
