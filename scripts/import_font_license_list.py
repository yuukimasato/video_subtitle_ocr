#!/usr/bin/env python3
# scripts/import_font_license_list.py
"""免费商用字体清单 ETL 编排（G1：清单快照 → fonts 表种子入库）。

用途
----
读取清单快照（markdown），解析为结构化字体记录（名称/许可类别/风格
类别/语言覆盖/官方链接/来源溯源），经 ``FontsDB.import_seed`` 幂等写入
种子层，补齐三级映射链的许可闸门与同风格兜底数据面：

- **yuleshow**：``--source yuleshow``——表格含 授權協議/簡繁日韓覆盖/
  官方鏈接（字段最全，优先）；
- **wordshub**：``--source wordshub``——段落 + 徽章标注（补充覆盖）。

与 ``scripts/import_jp_cn_font_map.py`` 同风格：快照存档于仓库外
``sources/`` 目录，本脚本只做纯解析 + 入库。快照可用 ``--fetch`` 拉取
（调 GitHub API，联网动作显式发生）：429 限流走有界退避（限制最大重试
次数、单次等待时间和总等待时间，尊重 Retry-After），重试耗尽不抛栈、
以退出码 2 优雅退出（可稍后重跑或改用 ``--input`` 本地快照），绝不因
限流把半截数据静默入库。

用法
----
    python scripts/import_font_license_list.py --input cf_readme.md \
        --source yuleshow --db ~/.local/share/video_subtitle_ocr/fonts.db \
        --seed-load --stats
    python scripts/import_font_license_list.py --fetch --db fonts.db --seed-load

退出码：0 正常；2 输入文件不存在 / 参数组合非法 / 快照拉取失败（可恢复，
重试耗尽后优雅退出）。
"""

from __future__ import annotations

import argparse
import json
import os
import sys

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from font_intel.etl.license_list import (  # noqa: E402
    PARSERS,
    SOURCE_ORDER,
    parse_license_lists,
)

_FETCH_URLS = {
    "yuleshow": "https://api.github.com/repos/yuleshow/chinese-fonts/readme",
    "wordshub":
        "https://api.github.com/repos/wordshub/free-font/readme",
}
_GITHUB_RAW_ACCEPT = "application/vnd.github.raw"

# 429 有界退避：最多重试 _FETCH_MAX_RETRIES 次；单次等待 = min(Retry-After,
# 指数退避, 单次上限)且受总等待预算约束；重试耗尽转 FetchError 优雅退出。
_FETCH_MAX_RETRIES = 3
_FETCH_WAIT_CAP_SEC = 30.0
_FETCH_TOTAL_WAIT_CAP_SEC = 60.0


class FetchError(RuntimeError):
    """快照拉取失败（可恢复：稍后重试，或改用 --input 提供本地快照）。"""


def _fetch_snapshot(source: str, dest_dir: str) -> str:
    """GitHub API 拉取清单快照（Accept: raw），存档后返回本地路径。

    429 Too Many Requests 按有界退避重试：最多 ``_FETCH_MAX_RETRIES`` 次，
    单次等待取服务器 Retry-After 与指数退避的较大者、再被单次/总等待上限
    封顶（等待预算耗尽即停，不无限等待）；重试耗尽或遇其他 HTTP/网络错误
    统一抛 :class:`FetchError`（可恢复错误），由 main 转退出码 2 优雅退出。
    """
    import time
    import urllib.error
    import urllib.request

    url = _FETCH_URLS[source]
    os.makedirs(dest_dir, exist_ok=True)
    dest = os.path.join(dest_dir, f"license_list_{source}.md")
    waited = 0.0
    for attempt in range(_FETCH_MAX_RETRIES + 1):
        req = urllib.request.Request(
            url, headers={"Accept": _GITHUB_RAW_ACCEPT,
                          "User-Agent": "video-subtitle-ocr-etl"})
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                data = resp.read()
        except urllib.error.HTTPError as exc:
            if exc.code != 429:
                raise FetchError(
                    f"{source}: 快照拉取失败（HTTP {exc.code}：{exc.reason}）；"
                    f"可稍后重试或用 --input 提供本地快照。") from exc
            if attempt == _FETCH_MAX_RETRIES:
                raise FetchError(
                    f"{source}: GitHub API 持续 429 限流，已重试 {attempt} 次"
                    f"（累计等待 {waited:.0f}s）仍被限流；请稍后重试或用 "
                    f"--input 提供本地快照。") from exc
            try:
                hint = float(exc.headers.get("Retry-After", "") or 0)
            except ValueError:
                hint = 0.0
            delay = min(max(hint, 2.0 ** attempt), _FETCH_WAIT_CAP_SEC,
                        _FETCH_TOTAL_WAIT_CAP_SEC - waited)
            if delay <= 0:
                raise FetchError(
                    f"{source}: 429 退避等待预算耗尽（已等待 {waited:.0f}s，"
                    f"总上限 {_FETCH_TOTAL_WAIT_CAP_SEC:.0f}s）；请稍后重试"
                    f"或用 --input 提供本地快照。") from exc
            print(f"[fetch] {source}: 429 限流，{delay:.0f}s 后重试"
                  f"（第 {attempt + 1}/{_FETCH_MAX_RETRIES} 次）",
                  file=sys.stderr)
            time.sleep(delay)
            waited += delay
        except urllib.error.URLError as exc:
            raise FetchError(
                f"{source}: 快照拉取失败（网络错误：{exc.reason}）；"
                f"可稍后重试或用 --input 提供本地快照。") from exc
        else:
            with open(dest, "wb") as fh:
                fh.write(data)
            print(f"[fetch] {source}: {len(data)} bytes -> {dest}")
            return dest
    raise FetchError(f"{source}: 快照拉取失败（未知原因）")  # 循环内必返回/抛出


def _default_db_path() -> str:
    sys.path.insert(0, PROJECT_ROOT)
    from font_intel.fonts_db import default_db_path

    return default_db_path()


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="免费商用字体清单 → fonts.db 种子层（幂等）")
    parser.add_argument("--input", nargs="*", default=[],
                        help="清单快照 markdown 路径（可多个，配合 --source）")
    parser.add_argument("--source", nargs="*", default=[],
                        choices=sorted(PARSERS),
                        help="每个 --input 对应的来源 key（数量须一致）")
    parser.add_argument("--fetch", action="store_true",
                        help="联网拉取全部来源快照到 sources/（显式网络动作）")
    parser.add_argument("--sources-dir",
                        default=os.path.join(
                            os.path.expanduser("~"),
                            ".local/share/video_subtitle_ocr/sources"),
                        help="快照存档目录（默认 XDG sources/）")
    parser.add_argument("--db", help="fonts.db 路径（缺省用默认 XDG 路径）")
    parser.add_argument("--seed-load", action="store_true",
                        help="解析结果写入 fonts 表种子层（幂等替换）")
    parser.add_argument("--output", help="另存解析记录为 JSONL（可选）")
    parser.add_argument("--stats", action="store_true", help="打印统计")
    args = parser.parse_args(argv)

    db_path = args.db or _default_db_path()

    texts = []
    if args.fetch:
        for source in SOURCE_ORDER:
            try:
                path = _fetch_snapshot(source, args.sources_dir)
            except FetchError as exc:
                # 可恢复错误：不打断其他任务链，留信息优雅退出（exit 2），
                # 绝不带着空/半截解析结果继续 seed 入库。
                print(f"[fetch] 失败: {exc}", file=sys.stderr)
                return 2
            with open(path, encoding="utf-8") as fh:
                texts.append((source, fh.read()))
    elif args.input:
        if len(args.input) != len(args.source):
            parser.error("--input 与 --source 数量必须一致")
        for path, source in zip(args.input, args.source):
            if not os.path.isfile(path):
                print(f"输入文件不存在: {path}", file=sys.stderr)
                return 2
            with open(path, encoding="utf-8") as fh:
                texts.append((source, fh.read()))

    records = parse_license_lists(texts)
    if args.output:
        with open(args.output, "w", encoding="utf-8") as fh:
            for rec in records:
                fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
        print(f"[output] {len(records)} records -> {args.output}")

    by_source: dict = {}
    for rec in records:
        by_source[rec["source"]] = by_source.get(rec["source"], 0) + 1
    by_license: dict = {}
    for rec in records:
        by_license[rec["license_category"]] = \
            by_license.get(rec["license_category"], 0) + 1

    if args.seed_load:
        from font_intel.fonts_db import FontsDB

        db = FontsDB(db_path)
        try:
            db.import_seed(records)
        finally:
            db.close()
        print(f"[seed] {len(records)} records -> {db_path}")

    if args.stats:
        print(f"[stats] total={len(records)}")
        for key, n in by_source.items():
            print(f"  source {key}: {n}")
        for key, n in sorted(by_license.items()):
            print(f"  license {key}: {n}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
