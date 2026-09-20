#!/usr/bin/env python3
# scripts/import_jp_cn_font_map.py
"""Seekladoom 中日字体对照表 ETL 导入脚本（T1.3：S0 归一 + S1 规则解析）。

用途
----
读取（多个）自由格式的中日字体对照/勘误文本（GBK/CRLF 等，编码与换行
内部自动归一），按规则解析为结构化 JSONL 记录，供后续任务导入
``font_intel`` 种子库。

与 S2 的衔接
------------
本脚本只做规则解析（``method=rule``）；规则打不动的行不强行归类，仅在
``--stats`` 里汇报 unresolved 行数，原文可经
``font_intel.etl.rule_parser.parse_file`` 取回（``ParseResult.unresolved``），
留给 S2 LLM 结构化任务二次解析。

用法
----
    python scripts/import_jp_cn_font_map.py --input a.txt b.txt --output out.jsonl --stats

退出码：0 正常；2 输入文件不存在（无网络调用，无重试）。
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from font_intel.etl.rule_parser import ParseResult, parse_file  # noqa: E402

logger = logging.getLogger(__name__)


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="中日字体对照文本 → 结构化 JSONL（S0 归一 + S1 规则解析）")
    parser.add_argument(
        "--input", nargs="+", action="append", required=True, metavar="FILE",
        help="输入文本文件，可一次给多个或多次给出")
    parser.add_argument(
        "--output", required=True, metavar="FILE",
        help="输出 JSONL 路径（每行一条解析记录，unresolved 不写入）")
    parser.add_argument(
        "--stats", action="store_true",
        help="向 stdout 打印各文件分类统计与 unresolved 行数")
    return parser


def _print_stats(label: str, stats: dict[str, int]) -> None:
    parts = " ".join(f"{key}={value}" for key, value in sorted(stats.items()))
    print(f"[stats] {label} {parts}")


def main(argv: list[str] | None = None) -> int:
    args = build_arg_parser().parse_args(argv)
    inputs = [path for group in args.input for path in group]

    # 输入校验前置：任一文件缺失整体退出 2，不做部分解析
    missing = [path for path in inputs if not Path(path).is_file()]
    for path in missing:
        print(f"input not found: {path}", file=sys.stderr)
    if missing:
        return 2

    all_records: list[dict] = []
    totals: dict[str, int] = {}
    for path in inputs:
        try:
            result: ParseResult = parse_file(path)
        except OSError as exc:
            print(f"cannot read {path}: {exc}", file=sys.stderr)
            return 2
        all_records.extend(result.records)
        stats = result.stats()
        for key, value in stats.items():
            totals[key] = totals.get(key, 0) + value
        if args.stats:
            _print_stats(path, stats)

    payload = "".join(
        json.dumps(record, ensure_ascii=False) + "\n" for record in all_records)
    Path(args.output).write_text(payload, encoding="utf-8")

    if args.stats:
        _print_stats("TOTAL", totals)
    return 0


if __name__ == "__main__":
    sys.exit(main())
