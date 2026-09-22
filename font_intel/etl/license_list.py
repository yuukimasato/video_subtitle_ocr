# font_intel/etl/license_list.py
"""免费商用字体清单解析（G1 数据源：清单 → fonts 表种子记录）。

数据源（快照存档于仓库外 ``sources/`` 目录，此处只做纯解析，无网络调用）：

- **yuleshow/chinese-fonts**（主源）：markdown 表格，按风格分节
  （``## 黑體 Gothic / Sans-Serif`` 等），列含 字體名/授權協議/簡繁日韓
  覆盖/官方鏈接——可完整填充 ``category``/``languages``/``license_category``
  /``official_url``，第三级同风格兜底与许可闸门都依赖这些字段。
- **wordshub/free-font**（补充源）：``## 系列`` 段落 + ``user-content-*``
  锚点 + shields.io 徽章标注许可（免费商用-无需授权 / license-OFL-* /
  免费商用-需要授权）；类别从字体名推断，推断不出置空不猜。

输出为 :meth:`font_intel.fonts_db.FontsDB.import_seed` 可直接消费的
``{"table": "fonts", ...}`` 记录列表；调用方负责幂等入库（同唯一键替换）。
许可归类口径（从严）：开源协议（OFL/GPL/Apache/IPA/BSD/SIL）→
``open_source``；「免費商用」→ ``free_commercial``；无法识别 → 不产出记录
（绝不猜）。
"""
from __future__ import annotations

import re
from typing import Any, Dict, List

# yuleshow 阅读顺序在前（字段最全），wordshub 补充其未覆盖的字体；
# 同名字体先到先得，避免跨源字段互相覆盖。
SOURCE_ORDER = ("yuleshow", "wordshub")

_OPEN_SOURCE_TOKENS = ("ofl", "gpl", "apache", "ipa", "bsd", "sil", "mit")
# 风格分类使用 matching.STYLE_CATEGORY_GROUPS 认得的 token（黑体/宋体/
# 楷体/手写/圆体参与第三级同风格匹配；仿宋/隶书/像素/艺术暂无同风格组，
# 仅作展示字段保留）。
_YULESHOW_SECTION_CATEGORY = {
    "黑體": "黑体",
    "宋體": "宋体",
    "楷體": "楷体",
    "隸書": "隶书",
    "圓體": "圆体",
    "仿宋": "仿宋",
    "像素": "像素",
    "手寫": "手写",
    "藝術": "艺术",
}
_YULESHOW_LANG_FLAGS = (("簡", "zh"), ("繁", "zh"), ("日", "ja"), ("韓", "ko"))

_WORDSHUB_BADGE_FREE = "免费商用-无需授权"
_WORDSHUB_BADGE_FREE_AUTH = "免费商用-需要授权"
_WORDSHUB_OPEN_TOKENS = _OPEN_SOURCE_TOKENS
# 日文段落的字体 languages=ja；其余（中文免费商用清单）默认 zh。
_WORDSHUB_JA_SECTION = "日文"

_ANCHOR_RE = re.compile(r'user-content-([^"]+)"')
_BADGE_RE = re.compile(r"img\.shields\.io/badge/([^\"?)]+)")
_LANG_TOKEN_RE = re.compile(r"[,，;；/、\s]+")


def _license_from_protocol(protocol: str) -> str:
    lowered = (protocol or "").lower()
    if any(tok in lowered for tok in _OPEN_SOURCE_TOKENS):
        return "open_source"
    if "免費商用" in (protocol or "") or "免费商用" in (protocol or ""):
        return "free_commercial"
    return ""


def _languages_from_flags(flags: str) -> str:
    """简/繁/日/韩 ✓ 列 → fonts.languages 文本（如 "zh,ja"）。"""
    langs: List[str] = []
    for flag, lang in _YULESHOW_LANG_FLAGS:
        if flag in flags and lang not in langs:
            langs.append(lang)
    return ",".join(langs)


def parse_yuleshow_readme(text: str) -> List[Dict[str, Any]]:
    """yuleshow/chinese-fonts README → fonts 种子记录。

    语言覆盖按表头列位置解析（表头 ``| 字體名 | 字重數 | 授權協議 | 簡 |
    繁 | 日 | 韓 | ...``，单元格是 ✓ 标记），列序变化不致错位。
    """
    records: List[Dict[str, Any]] = []
    category = ""
    lang_columns: Dict[str, str] = {}  # 列索引 → 语言 token
    for raw_line in (text or "").splitlines():
        line = raw_line.strip()
        if line.startswith("## "):
            header = line[3:].strip()
            category = ""
            lang_columns = {}
            for key, token in _YULESHOW_SECTION_CATEGORY.items():
                if header.startswith(key):
                    category = token
                    break
            continue
        if not line.startswith("|"):
            continue
        cells = [c.strip() for c in line.strip("|").split("|")]
        if not cells or not cells[0]:
            continue
        if "字體名" in cells[0]:
            # 表头行：登记 簡/繁/日/韓 所在列索引（相对 strip 后的 cells）。
            lang_columns = {}
            for idx, cell in enumerate(cells):
                for flag, lang in _YULESHOW_LANG_FLAGS:
                    if cell.strip() == flag:
                        lang_columns[str(idx)] = lang
            continue
        if set(cells[0]) <= {"-", ":", " "}:
            continue  # 分隔行 |---|---|
        name = cells[0]
        if not name or name in ("字體",):
            continue
        protocol = next((c for c in cells[2:] if _license_from_protocol(c)), "")
        license_category = _license_from_protocol(protocol)
        if not license_category:
            continue  # 无法识别的许可 → 不产出记录（绝不猜）
        langs: List[str] = []
        for idx_str, lang in lang_columns.items():
            idx = int(idx_str)
            if idx < len(cells) and cells[idx] \
                    and lang not in langs:
                flag = cells[idx]
                if flag in ("✓", "✔", "✓†") or flag.startswith(("✓", "✔")):
                    langs.append(lang)
        official_url = ""
        for cell in cells:
            m = re.search(r"\((https?://[^)]+)\)", cell)
            if m:
                official_url = m.group(1)
                break
        records.append({
            "table": "fonts",
            "canonical_name": name,
            "license_category": license_category,
            "category": category or None,
            "languages": ",".join(langs) or None,
            "license_name": protocol or None,
            "official_url": official_url or None,
            "source": "yuleshow/chinese-fonts",
        })
    return records


def _wordshub_category(name: str) -> str:
    """字体名 → 风格类别 token；推断不出置空（不猜）。"""
    for key, token in (
        ("黑体", "黑体"), ("黑", "黑体"),
        ("宋体", "宋体"), ("明體", "宋体"), ("明朝", "宋体"),
        ("楷体", "楷体"), ("楷", "楷体"),
        ("圆体", "圆体"), ("圓體", "圆体"),
        ("仿宋", "仿宋"), ("隸", "隶书"), ("隶书", "隶书"),
        ("手写", "手写"), ("手寫", "手写"),
    ):
        if key in name:
            return token
    return ""


def parse_wordshub_readme(text: str) -> List[Dict[str, Any]]:
    """wordshub/free-font README → fonts 种子记录。

    每个字体 = 一个 ``user-content-*`` 锚点；许可取锚点之后、下一锚点之前
    的首个 shields 徽章。段落归属按**锚点出现时**的段落计（徽章晚于锚点、
    可能在下一段落边界之后才出现，不能按落袋时的段落算）。类别从名字推断
    （推断不出置空）。
    """
    entries: List[Dict[str, Any]] = []  # [(name, license_badge, section)]
    section = ""
    current_name: str = ""
    current_section: str = ""
    current_badge: str = ""
    for raw_line in (text or "").splitlines():
        line = raw_line.strip()
        if line.startswith("## "):
            section = line[3:].strip()
            continue
        anchor = _ANCHOR_RE.search(line)
        if anchor is not None:
            if current_name and current_badge:
                entries.append((current_name, current_badge, current_section))
            current_name = anchor.group(1).strip()
            current_section = section
            current_badge = ""
            continue
        if current_name and not current_badge:
            badge = _BADGE_RE.search(line)
            if badge is not None:
                current_badge = badge.group(1)
    if current_name and current_badge:
        entries.append((current_name, current_badge, current_section))

    records: List[Dict[str, Any]] = []
    for name, badge, sec in entries:
        if badge.startswith(_WORDSHUB_BADGE_FREE_AUTH):
            # 「免费商用（需登记授权）」仍是免费商用，license_name 保留细节。
            license_category = "free_commercial"
            license_name = "免费商用（需登记授权）"
        elif badge.startswith(_WORDSHUB_BADGE_FREE):
            license_category = "free_commercial"
            license_name = "免费商用（无需授权）"
        elif any(tok in badge.lower() for tok in _WORDSHUB_OPEN_TOKENS):
            license_category = "open_source"
            # shields badge：label-message-color → 去颜色段、"--"还原"-"。
            license_name = badge.rsplit("-", 1)[0].replace("--", "-")
        else:
            continue
        languages = "ja" if sec == _WORDSHUB_JA_SECTION else "zh"
        records.append({
            "table": "fonts",
            "canonical_name": name,
            "license_category": license_category,
            "category": _wordshub_category(name) or None,
            "languages": languages,
            "license_name": license_name,
            "official_url": None,
            "source": "wordshub/free-font",
        })
    return records


PARSERS = {
    "yuleshow": parse_yuleshow_readme,
    "wordshub": parse_wordshub_readme,
}


def parse_license_lists(texts: List[Any]) -> List[Dict[str, Any]]:
    """多源合并：同名字体先到先得（yuleshow 字段全，优先）；跨源去重。

    Args:
        texts: ``(source_key, text)`` 二元组列表；未知 source_key 的条目
            跳过（调用方可用 PARSERS 显式扩展）。
    """
    merged: List[Dict[str, Any]] = []
    seen: set = set()
    ordered: List[Any] = []
    for key in SOURCE_ORDER:
        for src, text in texts:
            if src == key:
                ordered.append((src, text))
    for src, text in ordered:
        parser = PARSERS.get(src)
        if parser is None:
            continue
        for rec in parser(text):
            key = rec["canonical_name"].lower()
            if key in seen:
                continue
            seen.add(key)
            merged.append(rec)
    return merged
