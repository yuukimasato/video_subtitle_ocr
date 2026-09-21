#!/usr/bin/env python3
# i18n/apply_translations.py
"""Fill unfinished <translation> entries in Qt .ts files from dict modules.

Usage:
    python i18n/apply_translations.py i18n/app_en.ts [i18n/app_zh_TW.ts ...]

The dict module is picked by filename: app_en.ts -> i18n/translations_en.py.
Entries are keyed by exact source text; unfinished (empty) entries are
filled, and unfinished entries whose existing text matches the dict are
confirmed (unfinished flag removed — lupdate borrows identical source
strings across contexts but re-flags them). Existing translations that
differ from the dict are left untouched. After filling, any
Chinese-source message that is still untranslated is reported (language
names kept in their native script are expected to remain untranslated).
"""
from __future__ import annotations

import importlib.util
import re
import sys
from pathlib import Path

_MESSAGE_RE = re.compile(r"<message>.*?</message>", re.S)
_UNFINISHED_RE = r"\s*<translation type=\"unfinished\"></translation>"
# 语言原生名称刻意不翻译（所有语言下显示原称）。
_INTENTIONALLY_UNTRANSLATED = {"简体中文", "繁體中文", "English", "日本語"}


def _xml_escape(s: str) -> str:
    return (
        s.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace("'", "&apos;")
        .replace('"', "&quot;")
    )


def _xml_unescape(s: str) -> str:
    return (
        s.replace("&lt;", "<")
        .replace("&gt;", ">")
        .replace("&quot;", '"')
        .replace("&apos;", "'")
        .replace("&amp;", "&")
    )


def _escape_text(s: str) -> str:
    """Escape a translation for insertion as XML text content."""
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _load_dict(ts_path: Path) -> dict:
    m = re.fullmatch(r"app_(.+)\.ts", ts_path.name)
    if not m:
        raise SystemExit(f"Not a recognized app_<lang>.ts name: {ts_path}")
    module_path = ts_path.parent / f"translations_{m.group(1)}.py"
    if not module_path.exists():
        raise SystemExit(f"Dict module not found: {module_path}")
    spec = importlib.util.spec_from_file_location(module_path.stem, module_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.DICT


def fill(ts_path: Path) -> int:
    table = _load_dict(ts_path)
    text = ts_path.read_text(encoding="utf-8")
    filled = 0
    confirmed = 0
    for src, translation in table.items():
        for candidate in dict.fromkeys((src, _xml_escape(src))):
            # 空白未译条目 → 直接回填；lupdate 从其他上下文"借用"译文的
            # 非空未译条目（译文与词表一致）→ 去掉 unfinished 标记视为
            # 已确认（T3.5：FontUpdateReviewDialog 复用 FontMapReviewDialog
            # 的「应用/取消」等源文案时，lupdate 会复制译文但标记 unfinished）。
            pattern = re.compile(
                r"(<source>" + re.escape(candidate) + r"</source>)"
                + r"\s*<translation type=\"unfinished\">(.*?)</translation>",
                re.S,
            )

            def _sub(m_: "re.Match[str]") -> str:
                nonlocal filled, confirmed
                body = m_.group(2)
                if not body.strip():
                    filled += 1
                    return (
                        m_.group(1)
                        + "\n        <translation>"
                        + _escape_text(translation)
                        + "</translation>"
                    )
                if _xml_unescape(body) == translation:
                    confirmed += 1
                    return (
                        m_.group(1)
                        + "\n        <translation>"
                        + body
                        + "</translation>"
                    )
                # 既有译文与词表不一致：保留原样（人工译文优先）。
                return m_.group(0)

            text = pattern.sub(_sub, text)
    ts_path.write_text(text, encoding="utf-8")
    print(
        f"{ts_path.name}: filled {filled} entries, confirmed {confirmed} "
        f"(dict has {len(table)})"
    )

    missing = set()
    for m in _MESSAGE_RE.finditer(text):
        block = m.group(0)
        if 'type="unfinished"></translation>' in block:
            s = re.search(r"<source>(.*?)</source>", block, re.S)
            if s:
                src = _xml_unescape(s.group(1))
                if re.search(r"[\u4e00-\u9fff]", src) and src not in _INTENTIONALLY_UNTRANSLATED:
                    missing.add(src)
    if missing:
        print(f"{ts_path.name}: {len(missing)} Chinese sources still untranslated:")
        for s in sorted(missing):
            print("  -", s.replace("\n", "\\n"))
    return filled


if __name__ == "__main__":
    if len(sys.argv) < 2:
        raise SystemExit(__doc__)
    for arg in sys.argv[1:]:
        fill(Path(arg))
