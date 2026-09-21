# tests/test_font_etl_db_loader.py
"""Unit tests for font_intel.etl.db_loader + CLI S5/S6 编排 (T1.5).

覆盖：jp_cn_font_map.source 溯源列（schema v2 迁移）、S6 溯源入库
（mapping_seed_records 转换 / 非映射类型跳过 / 溯源字段校验 / 批量原子 /
幂等重载）、S5 复核决策合并（采纳→覆盖层 method=human、confidence=1.0，
负映射逐名展开，否决→留痕日志不写库，畸形决策拒绝），以及 CLI 的
--seed-load / --export-review / --import-review 编排（缺参退出码 2、坏
schema 拒绝、既有 --output 模式兼容）。全程 :memory:/临时目录，无网络。
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

import pytest  # noqa: E402

from font_intel.etl import db_loader  # noqa: E402
from font_intel.etl.db_loader import (  # noqa: E402
    MAP_SOURCE_TAG,
    VALID_METHODS,
    apply_review_decisions,
    load_seed_records,
    mapping_seed_records,
)
from font_intel.etl.review import (  # noqa: E402
    DECISIONS_SCHEMA,
    DEFAULT_CONFIDENCE_THRESHOLD,
    REVIEW_SCHEMA,
    build_review_items,
    read_decisions_package,
    read_review_package,
    write_decisions_package,
)
from font_intel.fonts_db import FontsDB, MIGRATIONS  # noqa: E402

CLI_SCRIPT = Path(PROJECT_ROOT) / "scripts" / "import_jp_cn_font_map.py"


def _count(db: FontsDB, table: str, where: str = "", params: tuple = ()) -> int:
    sql = f"SELECT COUNT(*) FROM {table}"
    if where:
        sql += f" WHERE {where}"
    return db._conn.execute(sql, params).fetchone()[0]


def _rule_mapping(**kw) -> dict:
    """rule_parser / llm_structurer 产出的 mapping 记录同构。"""
    rec = {
        "type": "mapping",
        "names": ["FOT-Chiaro"],
        "target": "汉仪润圆",
        "note": "pairing",
        "source_file": "map.txt",
        "line_no": 5,
        "method": "rule",
        "confidence": 0.9,
        "vendor": "Fontworks",
    }
    rec.update(kw)
    return rec


def _rule_negative(**kw) -> dict:
    rec = {
        "type": "negative_mapping",
        "names": ["华康俪金黑", "華康儷金黑"],
        "target": None,
        "note": "没有本家对应的日文版本",
        "source_file": "map.txt",
        "line_no": 4,
        "method": "rule",
        "confidence": 0.9,
        "vendor": "DynaFont",
    }
    rec.update(kw)
    return rec


def _adopted_mapping_item(**kw) -> dict:
    """review.build_review_items 产出的可采纳条目同构。"""
    item = {
        "source_file": "map.txt",
        "line_no": 7,
        "line": "XFont 追加：YFont",
        "record_type": "mapping",
        "names": ["XFont"],
        "target": "YFont",
        "confidence": 0.85,
        "method": "rule",
        "reason": "low_confidence",
        "note": "append",
        "suggestion": {"kind": "mapping", "jp_name": "XFont", "cn_name": "YFont"},
    }
    item.update(kw)
    return item


# ── schema v2：jp_cn_font_map.source 溯源列 ──────────────────────
class TestSchemaV2SourceColumn:
    def test_latest_migration_adds_source_column(self, tmp_path):
        db = FontsDB(str(tmp_path / "fonts.db"))
        try:
            assert db.get_schema_version() == MIGRATIONS[-1][0]
            cols = {r[1] for r in db._conn.execute(
                "PRAGMA table_info(jp_cn_font_map)")}
            assert "source" in cols
        finally:
            db.close()

    def test_seed_import_persists_source(self):
        db = FontsDB(":memory:")
        try:
            db.import_seed([{
                "table": "jp_cn_font_map", "jp_name": "ＭＳ ゴシック",
                "cn_name": "MS 哥特体", "method": "rule", "confidence": 0.95,
                "source_file": "seed/jp_cn.tsv", "line_no": 12,
                "source": MAP_SOURCE_TAG,
            }])
            row = db._conn.execute(
                "SELECT source FROM jp_cn_font_map").fetchone()
            assert row[0] == MAP_SOURCE_TAG
        finally:
            db.close()

    def test_overlay_mapping_accepts_source(self):
        db = FontsDB(":memory:")
        try:
            db.add_overlay_mapping("XFont", "YFont", source=MAP_SOURCE_TAG)
            row = db._conn.execute(
                "SELECT source, layer FROM jp_cn_font_map").fetchone()
            assert row[0] == MAP_SOURCE_TAG
            assert row[1] == "overlay"
        finally:
            db.close()


# ── S6：ETL 记录 → import_seed 载荷 ──────────────────────────────
class TestMappingSeedRecords:
    def test_mapping_record_converted_with_provenance(self):
        payload, skipped = mapping_seed_records([_rule_mapping()])
        assert skipped == {}
        assert len(payload) == 1
        rec = payload[0]
        assert rec["table"] == "jp_cn_font_map"
        assert rec["jp_name"] == "FOT-Chiaro"
        assert rec["cn_name"] == "汉仪润圆"
        assert rec["kind"] == "mapping"
        assert rec["method"] == "rule"
        assert rec["confidence"] == 0.9
        assert rec["source_file"] == "map.txt"
        assert rec["line_no"] == 5
        assert rec["source"] == MAP_SOURCE_TAG  # 逐条来源标注

    def test_negative_mapping_expands_per_name(self):
        payload, skipped = mapping_seed_records([_rule_negative()])
        assert skipped == {}
        assert [(r["jp_name"], r["cn_name"], r["kind"]) for r in payload] == [
            ("华康俪金黑", None, "negative_mapping"),
            ("華康儷金黑", None, "negative_mapping"),
        ]
        assert all(r["source"] == MAP_SOURCE_TAG for r in payload)

    def test_non_mapping_types_skipped_with_counts(self):
        records = [
            _rule_mapping(),
            {"type": "rename", "names": ["新名", "旧名"], "target": None,
             "note": "rename", "source_file": "m.txt", "line_no": 3,
             "method": "rule", "confidence": 0.95, "vendor": ""},
            {"type": "pitfall", "names": ["甲", "乙"], "target": None,
             "note": "", "source_file": "m.txt", "line_no": 9,
             "method": "llm", "confidence": 0.5, "vendor": ""},
        ]
        payload, skipped = mapping_seed_records(records)
        assert len(payload) == 1
        assert skipped == {"rename": 1, "pitfall": 1}

    def test_invalid_method_rejected(self):
        with pytest.raises(ValueError, match="method"):
            mapping_seed_records([_rule_mapping(method="guess")])

    def test_missing_confidence_rejected(self):
        with pytest.raises(ValueError, match="confidence"):
            mapping_seed_records([_rule_mapping(confidence=None)])

    def test_mapping_without_target_rejected(self):
        with pytest.raises(ValueError, match="target"):
            mapping_seed_records([_rule_mapping(target=None)])

    def test_valid_methods_documented(self):
        # 方案 §5.1 S6 的 method 枚举：rule/llm/vlm/human
        assert VALID_METHODS == frozenset({"rule", "llm", "vlm", "human"})


# ── S6：批量入库种子层 ───────────────────────────────────────────
class TestLoadSeedRecords:
    def test_end_to_end_seed_rows_with_provenance(self):
        db = FontsDB(":memory:")
        try:
            stats = load_seed_records(
                db, [_rule_mapping(), _rule_negative(),
                     _rule_mapping(type="rename", names=["新名", "旧名"])])
            assert stats["written"] == 3  # 1 正映射 + 2 负映射行
            assert stats["skipped"] == {"rename": 1}
            assert _count(db, "jp_cn_font_map", "layer='seed'") == 3

            hits = db.lookup_jp_cn("FOT-Chiaro")
            assert len(hits) == 1
            hit = hits[0]
            assert hit["layer"] == "seed"
            assert hit["cn_name"] == "汉仪润圆"
            assert hit["method"] == "rule"
            assert hit["confidence"] == 0.9
            assert hit["source_file"] == "map.txt"
            assert hit["line_no"] == 5
            assert hit["source"] == MAP_SOURCE_TAG
        finally:
            db.close()

    def test_reload_is_idempotent(self):
        db = FontsDB(":memory:")
        try:
            records = [_rule_mapping(), _rule_negative()]
            load_seed_records(db, records)
            load_seed_records(db, records)  # 重复导入不产生重复行
            assert _count(db, "jp_cn_font_map") == 3
        finally:
            db.close()

    def test_atomic_batch_rejected_on_bad_record(self):
        db = FontsDB(":memory:")
        try:
            with pytest.raises(ValueError):
                load_seed_records(
                    db, [_rule_mapping(), _rule_mapping(method="bogus")])
            # 单批原子：任一记录非法则整批不落库
            assert _count(db, "jp_cn_font_map") == 0
        finally:
            db.close()

    def test_empty_records_noop(self):
        db = FontsDB(":memory:")
        try:
            stats = load_seed_records(db, [])
            assert stats == {"written": 0, "skipped": {}}
        finally:
            db.close()


# ── S5：复核决策 → 覆盖层 ────────────────────────────────────────
class TestApplyReviewDecisions:
    def test_adopted_mapping_writes_overlay_human_provenance(self):
        db = FontsDB(":memory:")
        try:
            # 种子层已有同键映射（XFont→YFont），人工复核确认后写覆盖层
            load_seed_records(db, [_rule_mapping(names=["XFont"],
                                                 target="YFont", note="")])
            stats = apply_review_decisions(
                db, {"adopted": [_adopted_mapping_item()], "rejected": []})
            assert stats == {"adopted_items": 1, "written": 1, "rejected": []}
            assert _count(db, "jp_cn_font_map", "layer='overlay'") == 1

            hits = db.lookup_jp_cn("XFont")
            # 同键覆盖层行遮蔽种子行（种子行保留在库中、不可变）
            assert len(hits) == 1
            assert hits[0]["layer"] == "overlay"
            assert hits[0]["cn_name"] == "YFont"
            assert hits[0]["method"] == "human"
            assert hits[0]["confidence"] == 1.0
            assert hits[0]["source_file"] == "map.txt"
            assert hits[0]["line_no"] == 7
            assert hits[0]["source"] == MAP_SOURCE_TAG
            assert _count(db, "jp_cn_font_map", "layer='seed'") == 1
        finally:
            db.close()

    def test_adopted_negative_mapping_expands_aliases(self):
        item = {
            "source_file": "map.txt", "line_no": 4, "line": "华康俪金黑 & 華康儷金黑，没有…对应",
            "record_type": "negative_mapping",
            "names": ["华康俪金黑", "華康儷金黑"],
            "target": None, "confidence": 0.6, "method": "llm",
            "reason": "low_confidence", "note": "",
            "suggestion": {"kind": "negative_mapping",
                           "jp_name": "华康俪金黑", "cn_name": None},
        }
        db = FontsDB(":memory:")
        try:
            stats = apply_review_decisions(db, {"adopted": [item], "rejected": []})
            assert stats["written"] == 2  # 每个别名一行负映射
            for name in ("华康俪金黑", "華康儷金黑"):
                neg = db.lookup_jp_cn(name, include_negative=True)
                assert len(neg) == 1
                assert neg[0]["kind"] == "negative_mapping"
                assert neg[0]["method"] == "human"
                assert neg[0]["source"] == MAP_SOURCE_TAG
        finally:
            db.close()

    def test_rejected_not_written_but_logged(self, caplog):
        rejected_item = _adopted_mapping_item()
        db = FontsDB(":memory:")
        try:
            with caplog.at_level(logging.INFO, logger="font_intel.etl.db_loader"):
                stats = apply_review_decisions(
                    db, {"adopted": [], "rejected": [rejected_item]})
            assert stats == {
                "adopted_items": 0, "written": 0, "rejected": [rejected_item]}
            assert _count(db, "jp_cn_font_map") == 0  # 否决不写库
            # 留痕日志：可按文件与行号追溯被丢弃的条目
            assert any("map.txt" in r.message and "7" in r.message
                       for r in caplog.records)
        finally:
            db.close()

    def test_malformed_adopted_decision_rejected(self):
        db = FontsDB(":memory:")
        try:
            with pytest.raises(ValueError, match="suggestion kind"):
                apply_review_decisions(db, {"adopted": [
                    {"suggestion": {"kind": "mystery"}}], "rejected": []})
            with pytest.raises(ValueError):
                apply_review_decisions(db, {"adopted": ["not-a-dict"], "rejected": []})
            assert _count(db, "jp_cn_font_map") == 0
        finally:
            db.close()


# ── 复核条目构建（S5 导出侧的最小依赖） ──────────────────────────
class TestBuildReviewItems:
    def test_low_conf_mapping_actionable_high_conf_excluded(self):
        records = [
            _rule_mapping(names=["XFont"], target="YFont", note="append",
                          line_no=7, confidence=0.85),
            _rule_mapping(line_no=2, confidence=0.95),
        ]
        items = build_review_items(records, [], threshold=0.9)
        assert len(items) == 1
        item = items[0]
        assert item["line_no"] == 7
        assert item["reason"] == "low_confidence"
        assert item["suggestion"] == {
            "kind": "mapping", "jp_name": "XFont", "cn_name": "YFont"}
        assert item["method"] == "rule"

    def test_pending_entries_kept_without_suggestion(self):
        pending = [{"source_file": "m.txt", "line_no": 11,
                    "line": "看不懂的一行", "reason": "unrecognized",
                    "method": "rule"}]
        items = build_review_items([], pending, threshold=0.9)
        assert len(items) == 1
        item = items[0]
        assert item["record_type"] == "pending"
        assert item["line"] == "看不懂的一行"
        assert item["confidence"] is None
        assert item["suggestion"] is None
        assert item["reason"] == "unrecognized"

    def test_sorted_by_source_then_line(self):
        records = [_rule_mapping(line_no=9, confidence=0.5)]
        pending = [
            {"source_file": "a.txt", "line_no": 2, "line": "甲",
             "reason": "unrecognized"},
            {"source_file": "a.txt", "line_no": 1, "line": "乙",
             "reason": "unrecognized"},
        ]
        items = build_review_items(records, pending, threshold=0.9)
        assert [(i["source_file"], i["line_no"]) for i in items] == [
            ("a.txt", 1), ("a.txt", 2), ("map.txt", 9)]


# ── CLI 编排 ─────────────────────────────────────────────────────
class TestCLI:
    def _run(self, *argv):
        return subprocess.run(
            [sys.executable, str(CLI_SCRIPT), *argv],
            capture_output=True, text=True,
        )

    def test_seed_load_writes_db_with_provenance(self, tmp_path):
        src = tmp_path / "map.txt"
        src.write_text("A1明朝 → 华文宋体\n", encoding="utf-8")
        db_path = tmp_path / "fonts.db"
        proc = self._run("--input", str(src), "--db", str(db_path),
                         "--seed-load", "--stats")
        assert proc.returncode == 0, proc.stderr
        db = FontsDB(str(db_path))
        try:
            hits = db.lookup_jp_cn("A1明朝")
            assert len(hits) == 1
            hit = hits[0]
            assert hit["layer"] == "seed"
            assert hit["cn_name"] == "华文宋体"
            assert hit["method"] == "rule"
            assert hit["source_file"] == str(src)
            assert hit["line_no"] == 1
            assert hit["source"] == MAP_SOURCE_TAG
        finally:
            db.close()
        assert "[seed]" in proc.stdout

    def test_seed_load_requires_db(self, tmp_path):
        src = tmp_path / "map.txt"
        src.write_text("A1明朝 → 华文宋体\n", encoding="utf-8")
        proc = self._run("--input", str(src), "--seed-load")
        assert proc.returncode == 2
        assert "--db" in proc.stderr

    def test_export_review_writes_package(self, tmp_path):
        src = tmp_path / "map.txt"
        src.write_text(
            "# 注释\n"                     # comment：不导出
            "A1明朝 → 华文宋体\n"          # 高置信映射：不导出
            "XFont 追加：YFont\n"          # append 0.85 < 0.9：导出（可采纳）
            "华康新综艺体 / 华康雅宋体\n"  # pitfall 0.7：导出（不可写库）
            "这是一段说明文字\n",           # unrecognized：导出（待人工）
            encoding="utf-8",
        )
        out = tmp_path / "review.json"
        proc = self._run("--input", str(src), "--export-review", str(out))
        assert proc.returncode == 0, proc.stderr
        pkg = read_review_package(out)
        assert pkg["schema"] == REVIEW_SCHEMA
        assert pkg["confidence_threshold"] == DEFAULT_CONFIDENCE_THRESHOLD
        by = {(i["source_file"], i["line_no"]): i for i in pkg["items"]}
        assert len(pkg["items"]) == 3
        append_item = by[(str(src), 3)]
        assert append_item["suggestion"] == {
            "kind": "mapping", "jp_name": "XFont", "cn_name": "YFont"}
        pitfall_item = by[(str(src), 4)]
        assert pitfall_item["record_type"] == "pitfall"
        assert pitfall_item["suggestion"] is None
        pending_item = by[(str(src), 5)]
        assert pending_item["record_type"] == "pending"
        assert pending_item["line"] == "这是一段说明文字"

    def test_import_review_applies_overlay_and_counts_rejected(self, tmp_path):
        db_path = tmp_path / "fonts.db"
        item = _adopted_mapping_item()
        rejected = [_adopted_mapping_item(line_no=9, names=["ZFont"],
                                          suggestion={"kind": "mapping",
                                                      "jp_name": "ZFont",
                                                      "cn_name": "WFont"})]
        pkg = {
            "schema": DECISIONS_SCHEMA,
            "generated_at": "2026-09-21T00:00:00+00:00",
            "source_tag": MAP_SOURCE_TAG,
            "adopted": [item],
            "rejected": rejected,
        }
        decisions_path = tmp_path / "decisions.json"
        write_decisions_package(pkg, decisions_path)

        proc = self._run("--import-review", str(decisions_path),
                         "--db", str(db_path))
        assert proc.returncode == 0, proc.stderr
        assert "adopted_items=1" in proc.stdout
        assert "rejected=1" in proc.stdout

        db = FontsDB(str(db_path))
        try:
            hits = db.lookup_jp_cn("XFont")
            assert hits[0]["layer"] == "overlay"
            assert hits[0]["method"] == "human"
            assert hits[0]["cn_name"] == "YFont"
        finally:
            db.close()

    def test_import_review_requires_db(self, tmp_path):
        path = tmp_path / "d.json"
        path.write_text("{}", encoding="utf-8")
        proc = self._run("--import-review", str(path))
        assert proc.returncode == 2
        assert "--db" in proc.stderr

    def test_import_review_rejects_bad_schema(self, tmp_path):
        db_path = tmp_path / "fonts.db"
        path = tmp_path / "d.json"
        path.write_text(json.dumps({"schema": "bogus/9", "adopted": [],
                                    "rejected": []}), encoding="utf-8")
        proc = self._run("--import-review", str(path), "--db", str(db_path))
        assert proc.returncode == 2

    def test_legacy_output_mode_still_works(self, tmp_path):
        src = tmp_path / "map.txt"
        src.write_text("A1明朝 → 华文宋体\n", encoding="utf-8")
        out = tmp_path / "out.jsonl"
        proc = self._run("--input", str(src), "--output", str(out), "--stats")
        assert proc.returncode == 0, proc.stderr
        assert out.read_text(encoding="utf-8").count("\n") == 1

    def test_input_without_action_exits_2(self, tmp_path):
        src = tmp_path / "map.txt"
        src.write_text("A1明朝 → 华文宋体\n", encoding="utf-8")
        proc = self._run("--input", str(src))
        assert proc.returncode == 2

    def test_missing_input_still_exits_2(self, tmp_path):
        proc = self._run("--input", str(tmp_path / "nope.txt"),
                         "--output", str(tmp_path / "o.jsonl"))
        assert proc.returncode == 2
        assert "nope.txt" in proc.stderr
