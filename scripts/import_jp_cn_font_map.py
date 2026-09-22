#!/usr/bin/env python3
# scripts/import_jp_cn_font_map.py
"""Seekladoom 中日字体对照表 ETL 编排脚本（S0-S1 解析 + S5 复核 + S6 入库）。

用途
----
读取（多个）自由格式的中日字体对照/勘误文本（GBK/CRLF 等，编码与换行
内部自动归一）以及 rar 内解包出的厂商 xlsx 对照表（按扩展名 ``.xlsx``
自动分派给 :mod:`font_intel.etl.xlsx_extract`），按规则解析为结构化
记录，支持三种互可组合的去向：

- ``--output``：JSONL 记录文件（T1.3 既有行为，不变）；
- ``--seed-load``（配 ``--db``）：S6 溯源入库——映射/负映射写入 fonts_db
  种子层，逐条带 ``source_file / line_no / method / confidence`` 与
  ``source="Seekladoom/Japanese-Chinese-Fonts-adaptation (MIT)"`` 标注；
- ``--export-review``：S5 复核导出——低置信记录与规则打不动的行导出为
  JSON 复核包，交 ``components/font_map_review_dialog.py`` 逐条采纳/否决；
- ``--import-review``（配 ``--db``）：S5 复核导入——采纳条目写入覆盖层
  （method=human），否决条目不写库、留痕日志后丢弃。

与 S2 的衔接
------------
解析模式只做规则解析（``method=rule``）；规则打不动的行不强行归类，
随 ``--export-review`` 以 ``record_type=pending`` 进入复核包，也可经
``font_intel.etl.llm_structurer.structure_unresolved``（S2）二次解析。

用法
----
    python scripts/import_jp_cn_font_map.py --input a.txt b.xlsx --output out.jsonl --stats
    python scripts/import_jp_cn_font_map.py --input a.txt --db fonts.db --seed-load
    python scripts/import_jp_cn_font_map.py --input a.txt --export-review review.json
    python scripts/import_jp_cn_font_map.py --import-review decisions.json --db fonts.db

退出码：0 正常；2 输入文件不存在 / 参数组合非法 / 决策包 schema 不符
（无网络调用，无重试）。
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

from font_intel.etl import review  # noqa: E402
from font_intel.etl.db_loader import apply_review_decisions, load_seed_records  # noqa: E402
from font_intel.etl.review import (  # noqa: E402
    build_review_items,
    build_review_package,
    read_decisions_package,
    write_review_package,
)
from font_intel.etl.rule_parser import ParseResult, parse_file  # noqa: E402
from font_intel.etl.xlsx_extract import extract_file as extract_xlsx  # noqa: E402
from font_intel.fonts_db import FontsDB  # noqa: E402

logger = logging.getLogger(__name__)

_XLSX_SUFFIXES = frozenset({".xlsx", ".xlsm"})


def _parse_one(path: str) -> ParseResult:
    """单文件解析：xlsx 走 S0 表格提取器，其余按文本规则解析。"""
    if Path(path).suffix.lower() in _XLSX_SUFFIXES:
        return extract_xlsx(path)
    return parse_file(path)


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="中日字体对照文本 ETL（S1 规则解析 + S5 复核导出/导入 + S6 种子入库）")
    parser.add_argument(
        "--input", nargs="+", action="append", metavar="FILE",
        help="输入文本文件，可一次给多个或多次给出（--import-review 模式外必填）")
    parser.add_argument(
        "--output", metavar="FILE",
        help="输出 JSONL 路径（每行一条解析记录，unresolved 不写入）")
    parser.add_argument(
        "--stats", action="store_true",
        help="向 stdout 打印各文件分类统计与 unresolved 行数")
    parser.add_argument(
        "--db", metavar="PATH",
        help="fonts.db 路径（--seed-load / --import-review 必填）")
    parser.add_argument(
        "--seed-load", action="store_true",
        help="S6：把解析出的映射/负映射写入 --db 种子层（带溯源标注）")
    parser.add_argument(
        "--export-review", metavar="FILE",
        help="S5：把低置信记录与规则打不动的行导出为复核包（JSON）")
    parser.add_argument(
        "--import-review", metavar="FILE",
        help="S5：导入复核决策包（JSON）：采纳→覆盖层，否决→留痕日志丢弃")
    return parser


def _print_stats(label: str, stats: dict[str, int]) -> None:
    parts = " ".join(f"{key}={value}" for key, value in sorted(stats.items()))
    print(f"[stats] {label} {parts}")


def _parse_inputs(
    inputs: list[str],
) -> tuple[list[dict], list[dict], list[tuple[str, dict[str, int]]], dict[str, int]]:
    """解析全部输入文件 →（记录, 规则未解析行, 逐文件统计, 总统计）。"""
    all_records: list[dict] = []
    all_unresolved: list[dict] = []
    per_file: list[tuple[str, dict[str, int]]] = []
    totals: dict[str, int] = {}
    for path in inputs:
        result: ParseResult = _parse_one(path)
        all_records.extend(result.records)
        all_unresolved.extend(result.unresolved)
        stats = result.stats()
        per_file.append((path, stats))
        for key, value in stats.items():
            totals[key] = totals.get(key, 0) + value
    return all_records, all_unresolved, per_file, totals


def _run_import_review(args: argparse.Namespace) -> int:
    """S5 导入：采纳→覆盖层（method=human），否决→留痕日志丢弃。"""
    if not args.db:
        print("--import-review 需要 --db 指定 fonts.db 路径", file=sys.stderr)
        return 2
    try:
        package = read_decisions_package(args.import_review)
    except (OSError, ValueError) as exc:
        print(f"cannot read review decisions: {exc}", file=sys.stderr)
        return 2
    with FontsDB(args.db) as db:
        stats = apply_review_decisions(db, package)
    print("[review] adopted_items={0} written_rows={1} rejected={2}".format(
        stats["adopted_items"], stats["written"], len(stats["rejected"])))
    return 0


def main(argv: list[str] | None = None) -> int:
    args = build_arg_parser().parse_args(argv)
    # 复核否决的留痕日志走根 logger（db_loader 内逐条 INFO）；脚本入口
    # 配置一次性 basicConfig，让留痕在终端可见。
    logging.basicConfig(level=logging.INFO,
                        format="%(levelname)s %(name)s: %(message)s")

    if args.import_review:
        return _run_import_review(args)

    inputs = [path for group in (args.input or []) for path in group]
    if not inputs:
        print("no input: --input 必填（或使用 --import-review 导入复核决策）",
              file=sys.stderr)
        return 2
    if not (args.output or args.export_review or args.seed_load):
        print("nothing to do: 需要指定 --output / --export-review / --seed-load 之一",
              file=sys.stderr)
        return 2
    if args.seed_load and not args.db:
        print("--seed-load 需要 --db 指定 fonts.db 路径", file=sys.stderr)
        return 2

    # 输入校验前置：任一文件缺失整体退出 2，不做部分解析
    missing = [path for path in inputs if not Path(path).is_file()]
    for path in missing:
        print(f"input not found: {path}", file=sys.stderr)
    if missing:
        return 2

    try:
        all_records, all_unresolved, per_file, totals = _parse_inputs(inputs)
    except OSError as exc:
        print(f"cannot read input: {exc}", file=sys.stderr)
        return 2

    if args.output:
        payload = "".join(
            json.dumps(record, ensure_ascii=False) + "\n"
            for record in all_records)
        Path(args.output).write_text(payload, encoding="utf-8")

    if args.export_review:
        # 规则打不动的行（排除空行/注释）以 pending 条目进复核包；
        # 低置信记录由 build_review_items 按阈值筛出。
        pending = [dict(entry, method="rule") for entry in all_unresolved
                   if entry.get("reason") == "unrecognized"]
        items = build_review_items(
            all_records, pending, threshold=review.DEFAULT_CONFIDENCE_THRESHOLD)
        package = build_review_package(
            items, threshold=review.DEFAULT_CONFIDENCE_THRESHOLD)
        write_review_package(package, args.export_review)
        print("[review] items={0} actionable={1} -> {2}".format(
            len(items), package["stats"]["actionable"], args.export_review))

    if args.seed_load:
        with FontsDB(args.db) as db:
            seeded = load_seed_records(db, all_records)
        print("[seed] written={0} skipped={1}".format(
            seeded["written"], sum(seeded["skipped"].values())))

    if args.stats:
        for label, stats in per_file:
            _print_stats(label, stats)
        _print_stats("TOTAL", totals)
    return 0


if __name__ == "__main__":
    sys.exit(main())
