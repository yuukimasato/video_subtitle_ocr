# tests/test_fonts_db.py
"""Unit tests for font_intel.fonts_db (T1.1 local font database).

Covers: schema v1 creation, versioned migration framework (no re-run on
latest, monotonic upgrade, downgrade refusal), idempotent seed import,
seed/overlay dual-layer query semantics (overlay wins, fallback to seed),
seed read-only guards, reset_overlay, alias lookup, jp->cn positive/negative
mapping lookup with provenance fields, and explicit-path / ":memory:" open.
"""

from __future__ import annotations

import os
import sys

import pytest

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

import font_intel.fonts_db as fonts_db_mod  # noqa: E402
from font_intel.fonts_db import FontsDB, MIGRATIONS, default_db_path  # noqa: E402

EXPECTED_TABLES = {
    "schema_version",
    "fonts",
    "aliases",
    "jp_cn_font_map",
    "license_rules",
    "user_overrides",
}


def _font_record(**kw) -> dict:
    rec = {"table": "fonts", "canonical_name": "MS Gothic", "vendor": "Monotype",
           "category": "sans", "languages": "ja,en", "license_category": "commercial_paid",
           "license_name": "MS License", "official_url": "https://example.com/msgothic",
           "source": "seed/ja_fonts.tsv"}
    rec.update(kw)
    return rec


def _alias_record(font: str, alias: str, **kw) -> dict:
    rec = {"table": "aliases", "font": font, "alias": alias, "source": "seed/ja_aliases.tsv"}
    rec.update(kw)
    return rec


def _map_record(**kw) -> dict:
    rec = {"table": "jp_cn_font_map", "jp_name": "ＭＳ ゴシック", "cn_name": "MS 哥特体",
           "kind": "mapping", "method": "manual", "confidence": 0.98,
           "source_file": "seed/jp_cn.tsv", "line_no": 12}
    rec.update(kw)
    return rec


def _rule_record(**kw) -> dict:
    rec = {"table": "license_rules", "license_category": "commercial_paid",
           "usage_scene": "commercial", "action": "prompt"}
    rec.update(kw)
    return rec


def _table_names(db: FontsDB) -> set:
    rows = db._conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
    ).fetchall()
    return {r[0] for r in rows}


def _count(db: FontsDB, table: str, where: str = "", params: tuple = ()) -> int:
    sql = f"SELECT COUNT(*) FROM {table}"
    if where:
        sql += f" WHERE {where}"
    return db._conn.execute(sql, params).fetchone()[0]


# ---------------------------------------------------------------------------
# instantiation / default path
# ---------------------------------------------------------------------------

def test_default_db_path_respects_xdg(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    assert default_db_path() == os.path.join(str(tmp_path), "video_subtitle_ocr", "fonts.db")
    monkeypatch.delenv("XDG_DATA_HOME", raising=False)
    monkeypatch.setattr(os.path, "expanduser", lambda p: "/home/fakeuser")  # isolate $HOME
    expected = os.path.join("/home/fakeuser", ".local", "share", "video_subtitle_ocr", "fonts.db")
    assert default_db_path() == expected


def test_explicit_path_creates_parent_dirs(tmp_path):
    db_path = tmp_path / "nested" / "dir" / "fonts.db"
    db = FontsDB(path=str(db_path))
    try:
        assert db_path.exists()
        assert db.get_schema_version() == MIGRATIONS[-1][0]
    finally:
        db.close()


def test_memory_db_roundtrip():
    db = FontsDB(path=":memory:")
    try:
        db.import_seed([_font_record()])
        assert db.lookup_font("MS Gothic")["layer"] == "seed"
    finally:
        db.close()


def test_file_db_persists_across_reopen(tmp_path):
    db_path = str(tmp_path / "fonts.db")
    db = FontsDB(path=db_path)
    db.import_seed([_font_record()])
    db.close()
    db2 = FontsDB(path=db_path)
    try:
        assert db2.lookup_font("MS Gothic") is not None
    finally:
        db2.close()


# ---------------------------------------------------------------------------
# schema migrations (latest version tracked via MIGRATIONS, currently v2:
# jp_cn_font_map.source provenance column)
# ---------------------------------------------------------------------------

def test_empty_db_creates_schema_at_latest_version(tmp_path):
    db = FontsDB(path=str(tmp_path / "fonts.db"))
    try:
        assert db.get_schema_version() == MIGRATIONS[-1][0]
        assert _table_names(db) == EXPECTED_TABLES
    finally:
        db.close()


def test_reopen_at_latest_version_does_not_rerun_migrations(tmp_path):
    db_path = str(tmp_path / "fonts.db")
    db = FontsDB(path=db_path)
    db.import_seed([_font_record()])
    applied = db._conn.execute(
        "SELECT version, applied_at FROM schema_version ORDER BY version"
    ).fetchall()
    db.close()

    # If any migration were re-executed, DDL would raise and the applied_at
    # rows would change -- success here proves migrations did not re-run.
    db2 = FontsDB(path=db_path)
    try:
        assert db2.get_schema_version() == MIGRATIONS[-1][0]
        rows = db2._conn.execute(
            "SELECT version, applied_at FROM schema_version ORDER BY version").fetchall()
        assert [tuple(r) for r in rows] == [tuple(r) for r in applied]
        assert db2.lookup_font("MS Gothic") is not None
    finally:
        db2.close()


def test_upgrade_from_older_version_applies_pending_migration(tmp_path):
    db_path = str(tmp_path / "fonts.db")
    db = FontsDB(path=db_path)
    db.close()

    real = fonts_db_mod.MIGRATIONS
    pending_version = real[-1][0] + 1
    fonts_db_mod.MIGRATIONS = real + [
        (pending_version,
         "CREATE TABLE t11_marker (id INTEGER PRIMARY KEY, note TEXT)"),
    ]
    try:
        db2 = FontsDB(path=db_path)
        try:
            assert db2.get_schema_version() == pending_version
            assert "t11_marker" in _table_names(db2)
            # v1 objects must survive the upgrade untouched.
            assert "fonts" in _table_names(db2)
        finally:
            db2.close()
    finally:
        fonts_db_mod.MIGRATIONS = real


def test_downgrade_open_refused(tmp_path):
    db_path = str(tmp_path / "fonts.db")
    real = fonts_db_mod.MIGRATIONS
    db = FontsDB(path=db_path)  # DB created at the latest real version
    db.close()

    # Pretend the code only knows one version less (drop the last migration):
    # DB is at the newest version, code claims older -> refuse to open rather
    # than silently degrade.
    fonts_db_mod.MIGRATIONS = real[:-1]
    try:
        with pytest.raises(RuntimeError, match="schema"):
            FontsDB(path=db_path)
    finally:
        fonts_db_mod.MIGRATIONS = real


def test_migrations_versions_are_monotonic_from_1():
    versions = [v for v, _sql in MIGRATIONS]
    assert versions == list(range(1, len(versions) + 1))


# ---------------------------------------------------------------------------
# seed import (idempotent)
# ---------------------------------------------------------------------------

def test_import_seed_idempotent(tmp_path):
    db = FontsDB(path=str(tmp_path / "fonts.db"))
    try:
        records = [
            _font_record(),
            _alias_record("MS Gothic", "ＭＳ ゴシック"),
            _map_record(),
            _rule_record(),
        ]
        db.import_seed(records)
        db.import_seed(records)  # second pass must not duplicate anything
        assert _count(db, "fonts") == 1
        assert _count(db, "aliases") == 1
        assert _count(db, "jp_cn_font_map") == 1
        assert _count(db, "license_rules") == 1

        # Re-import with a changed field replaces the old seed value.
        db.import_seed([_font_record(vendor="New Vendor")])
        assert _count(db, "fonts") == 1
        assert db.lookup_font("MS Gothic")["vendor"] == "New Vendor"
    finally:
        db.close()


def test_import_seed_rejects_overlay_layer_and_unknown_table(tmp_path):
    db = FontsDB(path=str(tmp_path / "fonts.db"))
    try:
        with pytest.raises(ValueError):
            db.import_seed([_font_record(layer="overlay")])
        with pytest.raises(ValueError):
            db.import_seed([{"table": "user_overrides", "target_table": "fonts"}])
        with pytest.raises(ValueError):
            db.import_seed([{"table": "nope", "x": 1}])
        assert _count(db, "fonts") == 0
    finally:
        db.close()


def test_import_seed_alias_requires_known_font(tmp_path):
    db = FontsDB(path=":memory:")
    try:
        with pytest.raises(ValueError):
            db.import_seed([_alias_record("Unknown Font", "别名")])
    finally:
        db.close()


# ---------------------------------------------------------------------------
# dual-layer semantics
# ---------------------------------------------------------------------------

def test_overlay_priority_then_fallback_to_seed(tmp_path):
    db = FontsDB(path=str(tmp_path / "fonts.db"))
    try:
        db.import_seed([_font_record(category="sans")])
        db.upsert_overlay_font("MS Gothic", category="rounded", vendor="Overlay Vendor")
        hit = db.lookup_font("MS Gothic")
        assert hit["layer"] == "overlay"
        assert hit["category"] == "rounded"
        assert hit["vendor"] == "Overlay Vendor"
        # Seed row still present underneath.
        assert _count(db, "fonts", "layer='seed'") == 1

        db.reset_overlay()
        hit = db.lookup_font("MS Gothic")
        assert hit["layer"] == "seed"
        assert hit["category"] == "sans"
    finally:
        db.close()


def test_seed_is_readonly_no_public_seed_writer(tmp_path):
    db = FontsDB(path=str(tmp_path / "fonts.db"))
    try:
        db.import_seed([_font_record()])
        # Overlay writers reject any attempt to target the seed layer.
        with pytest.raises(ValueError):
            db.upsert_overlay_font("MS Gothic", layer="seed")
        with pytest.raises(ValueError):
            db.add_overlay_mapping("ＭＳ ゴシック", "MS 哥特体", layer="seed")
        # The only seed writer is import_seed; overlay upsert must not touch it.
        db.upsert_overlay_font("MS Gothic", category="rounded")
        assert _count(db, "fonts", "layer='seed'") == 1
        assert _count(db, "fonts", "layer='overlay'") == 1
        seed_row = db._conn.execute(
            "SELECT category FROM fonts WHERE layer='seed'").fetchone()
        assert seed_row[0] == "sans"
    finally:
        db.close()


def test_overlay_alias_hits_lookup(tmp_path):
    db = FontsDB(path=":memory:")
    try:
        db.import_seed([_font_record()])
        db.upsert_overlay_font("MS Gothic", aliases=["ゴシック9"])
        hit = db.lookup_font("ゴシック9")
        assert hit is not None
        assert hit["layer"] == "overlay"
        assert hit["canonical_name"] == "MS Gothic"
    finally:
        db.close()


def test_reset_overlay_clears_overlay_rows_only(tmp_path):
    db = FontsDB(path=":memory:")
    try:
        db.import_seed([
            _font_record(),
            _alias_record("MS Gothic", "ＭＳ ゴシック"),
            _map_record(),
            _rule_record(),
        ])
        db.upsert_overlay_font("MS Gothic", aliases=["overlay-alias"])
        db.add_overlay_mapping("ＭＳ ゴシック", "MS 哥特体", confidence=0.5)
        db.reset_overlay()
        assert _count(db, "fonts", "layer='overlay'") == 0
        assert _count(db, "aliases", "layer='overlay'") == 0
        assert _count(db, "jp_cn_font_map", "layer='overlay'") == 0
        assert _count(db, "license_rules", "layer='overlay'") == 0
        # Seed rows intact.
        assert _count(db, "fonts", "layer='seed'") == 1
        assert _count(db, "aliases", "layer='seed'") == 1
        assert _count(db, "jp_cn_font_map", "layer='seed'") == 1
        assert _count(db, "license_rules", "layer='seed'") == 1
    finally:
        db.close()


# ---------------------------------------------------------------------------
# lookups
# ---------------------------------------------------------------------------

def test_lookup_font_by_alias_and_miss(tmp_path):
    db = FontsDB(path=":memory:")
    try:
        db.import_seed([_font_record(), _alias_record("MS Gothic", "ＭＳ ゴシック")])
        hit = db.lookup_font("ＭＳ ゴシック")
        assert hit is not None
        assert hit["canonical_name"] == "MS Gothic"
        assert hit["license_category"] == "commercial_paid"
        assert "ＭＳ ゴシック" in hit["aliases"]
        assert db.lookup_font("Nonexistent Font") is None
    finally:
        db.close()


def test_lookup_jp_cn_positive_and_negative(tmp_path):
    db = FontsDB(path=":memory:")
    try:
        db.import_seed([
            _map_record(),  # ＭＳ ゴシック -> MS 哥特体 (confidence 0.98)
            _map_record(jp_name="ms ゴシック", cn_name=None, kind="negative_mapping",
                        confidence=0.9, source_file="seed/neg.tsv", line_no=3),
        ])
        hits = db.lookup_jp_cn("ＭＳ ゴシック")
        assert len(hits) == 1
        assert hits[0]["kind"] == "mapping"
        assert hits[0]["cn_name"] == "MS 哥特体"
        assert hits[0]["confidence"] == pytest.approx(0.98)
        assert hits[0]["source_file"] == "seed/jp_cn.tsv"
        assert hits[0]["line_no"] == 12
        # Negative mappings are excluded by default, visible on demand.
        assert db.lookup_jp_cn("ms ゴシック") == []
        neg = db.lookup_jp_cn("ms ゴシック", include_negative=True)
        assert len(neg) == 1
        assert neg[0]["kind"] == "negative_mapping"
        assert neg[0]["confidence"] == pytest.approx(0.9)
    finally:
        db.close()


def test_overlay_mapping_shadows_seed_same_key(tmp_path):
    db = FontsDB(path=":memory:")
    try:
        db.import_seed([_map_record()])
        db.add_overlay_mapping("ＭＳ ゴシック", "MS 哥特体",
                               confidence=0.55, method="user_confirm",
                               source_file="user", line_no=1)
        hits = db.lookup_jp_cn("ＭＳ ゴシック")
        assert len(hits) == 1  # seed row shadowed by the overlay row (same key)
        assert hits[0]["layer"] == "overlay"
        assert hits[0]["confidence"] == pytest.approx(0.55)
        assert hits[0]["method"] == "user_confirm"
        # Seed row still physically present under the overlay.
        assert _count(db, "jp_cn_font_map", "layer='seed'") == 1
    finally:
        db.close()


def test_lookup_license_rule_overlay_first(tmp_path):
    db = FontsDB(path=":memory:")
    try:
        db.import_seed([_rule_record()])
        rule = db.lookup_license_rule("commercial_paid", "commercial")
        assert rule is not None
        assert rule["action"] == "prompt"
        assert rule["layer"] == "seed"
        assert db.lookup_license_rule("open_source", "commercial") is None
    finally:
        db.close()


def test_lookup_font_overlay_without_seed_counterpart(tmp_path):
    db = FontsDB(path=":memory:")
    try:
        db.upsert_overlay_font("My Local Font", category="handwriting")
        hit = db.lookup_font("My Local Font")
        assert hit is not None
        assert hit["layer"] == "overlay"
        assert hit["license_category"] == "unknown"
    finally:
        db.close()
