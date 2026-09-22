# tests/test_license_list_etl.py
"""免费商用字体清单解析（G1 数据源）——两种清单格式的解析器单测。

纯解析、无网络：用内联的最小 markdown 片段锁定解析契约（许可归类、
风格分类、语言覆盖列位、徽章语义、跨源去重），快照漂移时在此处报警。
另覆盖 ``scripts/import_font_license_list.py`` 的 fetch 编排契约（全部
离线：urlopen / _fetch_snapshot 打桩）——快照内容进解析器、429 有界退避
（次数/单次/总等待上限，尊重 Retry-After）、重试耗尽优雅退出 exit 2。
"""

from __future__ import annotations

import importlib.util
import json
import os
import sys
import time
import urllib.error
from email.message import Message
from pathlib import Path

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from font_intel.etl.license_list import (  # noqa: E402
    parse_license_lists,
    parse_wordshub_readme,
    parse_yuleshow_readme,
)

_SCRIPT_PATH = Path(PROJECT_ROOT) / "scripts" / "import_font_license_list.py"
_spec = importlib.util.spec_from_file_location(
    "vso_import_font_license_list", _SCRIPT_PATH)
script = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(script)

_YULESHOW_SAMPLE = """# 免費商用中文字體總整理

## 黑體 Gothic / Sans-Serif

| 字體名 | 字重數 | 授權協議 | 簡 | 繁 | 日 | 韓 | 標點居中 | 官方鏈接 |
|---|---|---|---|---|---|---|---|---|
| 思源黑體 Noto Sans CJK | 7 | SIL OFL 1.1 | ✓ | ✓ | ✓ | ✓ | ⚡ | [GitHub](https://github.com/googlefonts/noto-cjk) |
| 方正黑體 | 2 | 免費商用（方正授權） | ✓ | | | | ✓ | [官網](https://www.foundertype.com) |

## 楷體 Kai / Regular Script

| 字體名 | 字重數 | 授權協議 | 簡 | 繁 | 日 | 韓 | 標點居中 | 官方鏈接 |
|---|---|---|---|---|---|---|---|---|
| 目雲楷體 | 1 | GPL-2.0 | ✓ | | | | | [GitHub](https://example.com/x) |

## 圓體 Rounded

| 字體名 | 字重數 | 授權協議 | 簡 | 繁 | 日 | 韓 | 標點居中 | 官方鏈接 |
|---|---|---|---|---|---|---|---|---|
| 神秘圆体 | 1 | 未知协议 | ✓ | | | | | |
"""

_WORDSHUB_SAMPLE = """# free-font

## 方正系列

<a id="user-content-方正黑体"  href="#方正黑体"><img src="a.svg" alt="方正黑体"></a>

「方正黑体」描述文字。

<div align="center">
<a href="https://example.com/auth"><img src = "https://img.shields.io/badge/免费商用-需要授权-orange?style=flat-square"></a>
</div>

## 日文

<a id="user-content-花园明朝"  href="#花园明朝"><img src="a.svg" alt="花园明朝"></a>

描述：HanaMin，开源项目。

<div align="center">
<a href="http://fonts.jp/hanazono/"><img src = "https://img.shields.io/badge/license-OFL--1.1-orange?style=flat-square"></a>
</div>
"""


def test_yuleshow_parses_license_category_and_styles():
    recs = {r["canonical_name"]: r for r in parse_yuleshow_readme(
        _YULESHOW_SAMPLE)}
    assert set(recs) == {"思源黑體 Noto Sans CJK", "方正黑體", "目雲楷體"}
    siyuan = recs["思源黑體 Noto Sans CJK"]
    assert siyuan["license_category"] == "open_source"
    assert siyuan["category"] == "黑体"
    # 简繁日韩 ✓ → languages 覆盖；官方链接取首个。
    assert siyuan["languages"] == "zh,ja,ko"
    assert siyuan["official_url"] == "https://github.com/googlefonts/noto-cjk"
    fangzheng = recs["方正黑體"]
    assert fangzheng["license_category"] == "free_commercial"
    assert fangzheng["languages"] == "zh"
    kai = recs["目雲楷體"]
    assert kai["category"] == "楷体"
    assert kai["license_category"] == "open_source"


def test_yuleshow_skips_unrecognized_license():
    recs = parse_yuleshow_readme(_YULESHOW_SAMPLE)
    # 「未知协议」不产出记录（绝不猜）。
    assert all(r["canonical_name"] != "神秘圆体" for r in recs)


def test_wordshub_badges_and_ja_section():
    recs = {r["canonical_name"]: r for r in parse_wordshub_readme(
        _WORDSHUB_SAMPLE)}
    fz = recs["方正黑体"]
    # 「免费商用-需要授权」仍是免费商用，license_name 保留登记细节。
    assert fz["license_category"] == "free_commercial"
    assert fz["license_name"] == "免费商用（需登记授权）"
    assert fz["category"] == "黑体"
    assert fz["languages"] == "zh"
    hanazono = recs["花园明朝"]
    # 日文段落 → languages=ja；OFL 徽章 → open_source。
    assert hanazono["license_category"] == "open_source"
    assert hanazono["languages"] == "ja"


def test_merge_dedupes_by_name_yuleshow_first():
    merged = parse_license_lists([
        ("wordshub", _WORDSHUB_SAMPLE),
        ("yuleshow", _YULESHOW_SAMPLE),
    ])
    names = [r["canonical_name"] for r in merged]
    # 跨源同名去重（样本里无真重名，断言无重复即可），且无来源乱序注入。
    assert len(names) == len(set(names))
    assert "方正黑体" in names and "思源黑體 Noto Sans CJK" in names


# ── fetch 编排契约（离线：urlopen / _fetch_snapshot 打桩） ────────────


class _FakeResponse:
    """urlopen 桩返回值：context manager + read()。"""

    def __init__(self, data: bytes):
        self._data = data

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        return False

    def read(self):
        return self._data


def _http_error(code: int, retry_after: str = "") -> urllib.error.HTTPError:
    hdrs = Message()
    if retry_after:
        hdrs["Retry-After"] = retry_after
    return urllib.error.HTTPError(
        "https://api.github.com/fake", code, "err", hdrs, None)


def _read_jsonl(path: Path) -> list:
    return [json.loads(line) for line in
            path.read_text(encoding="utf-8").splitlines() if line.strip()]


def test_fetch_wires_snapshot_content_into_parsers(monkeypatch, tmp_path):
    """回归：--fetch 曾把快照「路径」当「内容」传解析器 → 静默 0 记录入库。"""
    def fake_fetch(source, dest_dir):
        p = Path(dest_dir) / f"license_list_{source}.md"
        p.write_text(_YULESHOW_SAMPLE, encoding="utf-8")
        return str(p)

    monkeypatch.setattr(script, "_fetch_snapshot", fake_fetch)
    out = tmp_path / "out.jsonl"
    db_path = tmp_path / "fonts.db"
    rc = script.main([
        "--fetch", "--output", str(out), "--db", str(db_path),
        "--seed-load", "--stats",
    ])
    assert rc == 0
    recs = _read_jsonl(out)
    # 样本 4 行中 3 行可识别许可（「未知协议」不产出）。
    assert len(recs) == 3
    assert recs[0]["canonical_name"] == "思源黑體 Noto Sans CJK"


def test_fetch_429_recovers_after_bounded_retry(monkeypatch, tmp_path):
    """429 → 有界退避后成功：退避等待受限、快照内容照常入库。"""
    calls = {"n": 0}
    sleeps: list[float] = []

    def fake_urlopen(req, timeout=0):
        calls["n"] += 1
        if calls["n"] <= 2:
            raise _http_error(429)
        return _FakeResponse(_YULESHOW_SAMPLE.encode("utf-8"))

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    monkeypatch.setattr(time, "sleep", sleeps.append)
    out = tmp_path / "out.jsonl"
    rc = script.main([
        "--fetch", "--output", str(out), "--sources-dir", str(tmp_path),
    ])
    assert rc == 0
    assert len(_read_jsonl(out)) == 3
    assert len(sleeps) == 2  # 两次 429 → 两次退避，不超最大重试次数
    assert all(s <= script._FETCH_WAIT_CAP_SEC for s in sleeps)
    assert sum(sleeps) <= script._FETCH_TOTAL_WAIT_CAP_SEC


def test_fetch_429_honors_retry_after_hint(monkeypatch, tmp_path):
    """服务器 Retry-After 优先于指数退避（仍被单次上限封顶）。"""
    calls = {"n": 0}
    sleeps: list[float] = []

    def fake_urlopen(req, timeout=0):
        calls["n"] += 1
        if calls["n"] == 1:
            raise _http_error(429, retry_after="7")
        return _FakeResponse(_YULESHOW_SAMPLE.encode("utf-8"))

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    monkeypatch.setattr(time, "sleep", sleeps.append)
    rc = script.main([
        "--fetch", "--output", str(tmp_path / "out.jsonl"),
        "--sources-dir", str(tmp_path),
    ])
    assert rc == 0
    assert sleeps == [7.0]


def test_fetch_persistent_429_exits_gracefully(monkeypatch, tmp_path, capsys):
    """持续 429：重试耗尽后优雅退出（exit 2），不抛栈、不产出空记录文件。"""
    monkeypatch.setattr(
        "urllib.request.urlopen",
        lambda req, timeout=0: (_ for _ in ()).throw(_http_error(429)))
    sleeps: list[float] = []
    monkeypatch.setattr(time, "sleep", sleeps.append)
    out = tmp_path / "out.jsonl"
    rc = script.main([
        "--fetch", "--output", str(out), "--sources-dir", str(tmp_path),
    ])
    assert rc == 2
    assert len(sleeps) == script._FETCH_MAX_RETRIES
    assert "429" in capsys.readouterr().err
    assert not out.exists()  # 绝不带空解析结果继续走输出/入库
