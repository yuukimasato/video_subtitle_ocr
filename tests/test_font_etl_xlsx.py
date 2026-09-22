# tests/test_font_etl_xlsx.py
"""Unit tests for font_intel.etl.xlsx_extract (S0 xlsx 对照表提取器).

覆盖：表头行定位与分类列截断（「繁体（注：…）」→「繁体」）、每候选
一记录（"/" 切分、note=分类、置信度 0.9、厂商前缀推断）、注释性整格与
超长 CJK 单句转 unresolved(reason=unrecognized)、分类行（【…】）与空行
跳过、多候选格按段判断不被整格 CJK 总数误杀、mapping_seed_records 的
一候选一行落库载荷、CLI 对 xlsx 输入的分派。
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

pytest.importorskip("openpyxl")

import openpyxl  # noqa: E402

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from font_intel.etl.db_loader import mapping_seed_records  # noqa: E402
from font_intel.etl.xlsx_extract import (  # noqa: E402
    extract_records,
    split_candidates,
)

CLI_SCRIPT = Path(PROJECT_ROOT) / "scripts" / "import_jp_cn_font_map.py"


def _make_workbook(path: Path, rows: list[tuple]) -> Path:
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Sheet1"
    for row in rows:
        ws.append(list(row))
    wb.save(path)
    return path


HEADER = ("日文", "简繁通用（字符数15000+）", "简繁扩充", "简体",
          "繁体（注：台繁和港繁不一定是字面意义）")


def _xlsx(tmp_path: Path, rows: list[tuple]) -> Path:
    return _make_workbook(tmp_path / "table.xlsx", [HEADER] + rows)


# ── split_candidates 单元 ────────────────────────────────────────
class TestSplitCandidates:
    def test_multi_candidate_segments(self):
        segments, note = split_candidates("华康古籍银杏W3P / 方正秉楠圆宋简体")
        assert note is False
        assert segments == ["华康古籍银杏W3P", "方正秉楠圆宋简体"]

    def test_sentence_with_comma_is_note(self):
        segments, note = split_candidates("A1明朝经常被用在大字标题上，有着浓厚的时代感")
        assert note is True and segments == []

    def test_long_cjk_single_segment_is_note(self):
        # 无句读的整句注释：单段 CJK 超长判注释
        segments, note = split_candidates("娥眉明朝体是以华康自家的明朝体为基础做出来的")
        assert note is True and segments == []

    def test_multi_candidate_cell_not_killed_by_total_cjk(self):
        # 多候选格总 CJK 数超过单段阈值，但每段都是短名 → 不误杀
        cell = "華康細明體(P)（台繁） / 華康明體W3(P) / 方正新秀麗"
        segments, note = split_candidates(cell)
        assert note is False
        assert len(segments) == 3

    def test_annotation_markers(self):
        assert split_candidates("没有对应的日文版本")[1] is True
        assert split_candidates("注：仅限日文")[1] is True


# ── extract_records 端到端 ───────────────────────────────────────
class TestExtractRecords:
    def test_basic_mapping_rows(self, tmp_path):
        path = _xlsx(tmp_path, [
            ("【明朝体类（宋体类）】", None, None, None, None),
            ("DFPGabiMincho-W3", None, None,
             "华康古籍银杏W3P / 方正秉楠圆宋简体", "華康古籍銀杏W3P"),
            ("A-OTF A1 Mincho Std Bold", "思源宋体", None,
             "方正标雅宋简体", None),
        ])
        result = extract_records(path)
        types = [r["type"] for r in result.records]
        assert types.count("mapping") == 5  # 3（DFPGabiMincho）+ 2（A1 Mincho）
        # 分类行进 unresolved，reason=category_header（不进复核 pending）
        assert [u["reason"] for u in result.unresolved] == ["category_header"]

        by_jp = {}
        for rec in result.records:
            by_jp.setdefault(rec["names"][0], []).append(rec)

        rows = by_jp["DFPGabiMincho-W3"]
        assert {(r["target"], r["note"]) for r in rows} == {
            ("华康古籍银杏W3P", "简体"),
            ("方正秉楠圆宋简体", "简体"),
            ("華康古籍銀杏W3P", "繁体"),
        }
        for rec in rows:
            assert rec["method"] == "rule"
            assert rec["confidence"] == 0.9
            assert rec["vendor"] == "DynaFont"
            assert rec["source_file"] == str(path)
            assert rec["line_no"] >= 2

        assert by_jp["A-OTF A1 Mincho Std Bold"][0]["vendor"] == "Morisawa"
        assert by_jp["A-OTF A1 Mincho Std Bold"][0]["note"] == "简繁通用"

    def test_note_cells_go_to_review_pending(self, tmp_path):
        path = _xlsx(tmp_path, [
            ("DFHSMincho Std W3", "娥眉明朝体是以华康自家的明朝体为基础做出的", None,
             "华康宋体W3(P)", "平成明朝体（注：早期由日本规格协定会制定）"),
        ])
        result = extract_records(path)
        # 简繁通用列（整句注释）与繁体列（含"注："）→ unresolved/unrecognized
        assert len(result.records) == 1
        assert result.records[0]["target"] == "华康宋体W3(P)"
        pending = [u for u in result.unresolved if u["reason"] == "unrecognized"]
        assert len(pending) == 2
        # 展示行保留分类与原文，供复核对话框人工判断
        assert all("（简繁通用）" in u["line"] or "（繁体）" in u["line"]
                   for u in pending)

    def test_combined_jp_name_expansion(self, tmp_path):
        # 一格多名：原组合串保真 + 共享前缀展开，展开行带"多名展开"标注
        path = _xlsx(tmp_path, [
            ("DFGabiMincho Std/StdN", None, None, "华康古籍银杏W3P", None),
        ])
        result = extract_records(path)
        jps = [r["names"][0] for r in result.records]
        assert jps == ["DFGabiMincho Std/StdN", "DFGabiMincho Std",
                       "DFGabiMincho StdN"]
        by_jp = {r["names"][0]: r for r in result.records}
        assert "多名展开" in by_jp["DFGabiMincho Std"]["note"]
        assert "多名展开" not in by_jp["DFGabiMincho Std/StdN"]["note"]
        assert all(r["target"] == "华康古籍银杏W3P" for r in result.records)

    def test_candidate_qualifier_cleaned_into_note(self, tmp_path):
        # 笔记式注记从品名剥离（(P) 保留），注记进 note 字段
        path = _xlsx(tmp_path, [
            ("DFPHSMinCho-W7", None, None, None,
             "華康中明體(P)（台繁） / 华康宋体W7（开粗体）"),
        ])
        result = extract_records(path)
        rows = {r["target"]: r for r in result.records}
        assert set(rows) == {"華康中明體(P)", "华康宋体W7"}
        assert rows["華康中明體(P)"]["note"] == "繁体；台繁"
        assert rows["华康宋体W7"]["note"] == "繁体；开粗体"

    def test_empty_and_missing_cells_skipped(self, tmp_path):
        path = _xlsx(tmp_path, [
            ("DFPHSMinCho-W3", None, None, None, None),  # 有名无候选 → 无产出
            (None, "思源宋体", None, None, None),        # 无日文名 → 整行跳过
        ])
        result = extract_records(path)
        assert result.records == []
        assert result.unresolved == []

    def test_workbook_without_header_raises_no_records(self, tmp_path):
        path = _make_workbook(tmp_path / "nohdr.xlsx", [
            ("随便", "什么", None, None, None),
        ])
        result = extract_records(path)
        assert result.records == []


# ── 与 db_loader 的衔接 ──────────────────────────────────────────
class TestSeedRoundtrip:
    def test_one_candidate_one_row(self, tmp_path):
        path = _xlsx(tmp_path, [
            ("FOT-ChiaroStd M", None, None, "汉仪润圆 / 龙藏体", None),
        ])
        result = extract_records(path)
        payload, skipped = mapping_seed_records(result.records)
        assert skipped == {}
        assert len(payload) == 2
        assert {row["cn_name"] for row in payload} == {"汉仪润圆", "龙藏体"}
        for row in payload:
            assert row["kind"] == "mapping"
            assert row["table"] == "jp_cn_font_map"
            assert row["source"].startswith("Seekladoom/")


# ── CLI 分派 ─────────────────────────────────────────────────────
class TestCli:
    def test_cli_accepts_xlsx_input(self, tmp_path):
        xlsx = _xlsx(tmp_path, [
            ("DFPGabiMincho-W3", None, None, "华康古籍银杏W3P", None),
        ])
        out = tmp_path / "out.jsonl"
        proc = subprocess.run(
            [sys.executable, str(CLI_SCRIPT),
             "--input", str(xlsx), "--output", str(out), "--stats"],
            capture_output=True, text=True)
        assert proc.returncode == 0, proc.stderr
        lines = [ln for ln in out.read_text(encoding="utf-8").splitlines() if ln]
        assert len(lines) == 1
        assert "mapping=1" in proc.stdout  # --stats 输出可见

    def test_cli_seed_load_xlsx(self, tmp_path):
        xlsx = _xlsx(tmp_path, [
            ("FOT-SeuratStd B", None, None, "思源黑体", None),
        ])
        db = tmp_path / "fonts.db"
        proc = subprocess.run(
            [sys.executable, str(CLI_SCRIPT),
             "--input", str(xlsx), "--db", str(db), "--seed-load"],
            capture_output=True, text=True)
        assert proc.returncode == 0, proc.stderr
        assert "[seed] written=1" in proc.stdout

        from font_intel.fonts_db import FontsDB
        with FontsDB(str(db)) as dbh:
            rows = dbh.lookup_jp_cn("FOT-SeuratStd B")
        assert len(rows) == 1
        assert rows[0]["cn_name"] == "思源黑体"
        assert rows[0]["layer"] == "seed"
