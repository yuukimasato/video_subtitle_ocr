# tests/test_compliance.py
"""Unit tests for font_intel.compliance (T1.6 compliance decision engine).

Covers: default rule matrix invariants (category x scene -> action),
single-cell overrides with action validation, unknown strict/lenient paths,
JSON round-trip and corrupt-file fallback to defaults, decide() against
FontsDB (hit / miss / exception degradation), grant & revoke license flow
persisted in user_overrides (approved=1), replace_auto confidence gate with
alternatives provider, hard red lines (HARD_RED_LINE constant, blocked URL
markers sanitization, official_url only from db), and JSON-serializable
ComplianceDecision.
"""

from __future__ import annotations

import dataclasses
import json
import logging
import os
import sys

import pytest

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from font_intel.fonts_db import FontsDB  # noqa: E402


@pytest.fixture()
def db() -> FontsDB:
    return FontsDB(":memory:")


def _seed_font(db: FontsDB, name: str, *, license_category: str,
               official_url: str | None = None) -> None:
    rec = {"table": "fonts", "canonical_name": name,
           "license_category": license_category,
           "source": "test-seed"}
    if official_url is not None:
        rec["official_url"] = official_url
    db.import_seed([rec])


# ---------------------------------------------------------------------------
# 默认规则矩阵不变量
# ---------------------------------------------------------------------------

class TestDefaultRulesMatrix:
    EXPECTED_BY_CATEGORY = {
        "open_source": "allow",
        "free_commercial": "allow",
        "commercial_paid": "prompt",
        "unknown": "prompt",  # 默认从严，等同 commercial_paid
    }

    def test_matrix_invariants_all_cells(self):
        from font_intel.compliance import DEFAULT_RULES, LICENSE_CATEGORIES, USAGE_SCENES
        for cat in LICENSE_CATEGORIES:
            for scene in USAGE_SCENES:
                assert DEFAULT_RULES[(cat, scene)] == self.EXPECTED_BY_CATEGORY[cat]

    def test_matrix_is_total(self):
        from font_intel.compliance import DEFAULT_RULES, LICENSE_CATEGORIES, USAGE_SCENES
        expected_keys = {(c, s) for c in LICENSE_CATEGORIES for s in USAGE_SCENES}
        assert set(DEFAULT_RULES) == expected_keys

    def test_unknown_strict_on(self):
        from font_intel.compliance import ComplianceRules, decide
        decision = decide("MysteryFont", scene="commercial",
                          db=None, rules=ComplianceRules(strict_unknown=True))
        assert decision.action == "prompt"

    def test_unknown_lenient_off(self):
        from font_intel.compliance import ComplianceRules, decide
        decision = decide("MysteryFont", scene="commercial",
                          db=None, rules=ComplianceRules(strict_unknown=False))
        assert decision.action == "report_only"

    def test_single_cell_override(self):
        from font_intel.compliance import ComplianceRules
        rules = ComplianceRules()
        rules.set_override("commercial_paid", "commercial", "replace_auto")
        assert rules.action_for("commercial_paid", "commercial") == "replace_auto"
        # 其他单元格不受影响
        assert rules.action_for("commercial_paid", "personal") == "prompt"
        assert rules.action_for("open_source", "commercial") == "allow"

    def test_override_rejected_on_invalid_action(self):
        from font_intel.compliance import ComplianceRules
        rules = ComplianceRules()
        with pytest.raises(ValueError):
            rules.set_override("open_source", "personal", "banana")

    def test_override_rejected_on_invalid_cell(self):
        from font_intel.compliance import ComplianceRules
        rules = ComplianceRules()
        with pytest.raises(ValueError):
            rules.set_override("shareware", "personal", "allow")
        with pytest.raises(ValueError):
            rules.set_override("open_source", "school", "allow")


# ---------------------------------------------------------------------------
# 规则持久化（JSON 层）
# ---------------------------------------------------------------------------

class TestRulesPersistence:
    def test_json_roundtrip(self, tmp_path):
        from font_intel.compliance import ComplianceRules
        rules = ComplianceRules(strict_unknown=False)
        rules.set_override("commercial_paid", "commercial", "replace_auto")
        rules.set_override("unknown", "publish", "allow")
        path = str(tmp_path / "rules.json")
        rules.save_json(path)
        loaded = ComplianceRules.load_json(path)
        assert loaded == rules
        assert loaded.strict_unknown is False
        assert loaded.action_for("commercial_paid", "commercial") == "replace_auto"
        assert loaded.action_for("unknown", "publish") == "allow"

    def test_corrupt_json_falls_back_to_default_with_warning(self, tmp_path, caplog):
        from font_intel.compliance import ComplianceRules
        path = str(tmp_path / "broken.json")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write("{not valid json!!!")
        with caplog.at_level(logging.WARNING):
            loaded = ComplianceRules.load_json(path)
        assert loaded == ComplianceRules()
        assert loaded.strict_unknown is True
        assert loaded.overrides == {}
        assert any("compliance" in r.message.lower() or "规则" in r.message
                   for r in caplog.records if r.levelno >= logging.WARNING)

    def test_missing_file_falls_back_to_default_with_warning(self, tmp_path, caplog):
        from font_intel.compliance import ComplianceRules
        path = str(tmp_path / "absent.json")
        with caplog.at_level(logging.WARNING):
            loaded = ComplianceRules.load_json(path)
        assert loaded == ComplianceRules()
        assert any(r.levelno >= logging.WARNING for r in caplog.records)

    def test_load_or_default_none_path_returns_default(self):
        from font_intel.compliance import ComplianceRules
        assert ComplianceRules.load_or_default(None) == ComplianceRules()

    def test_default_rules_path_xdg(self, monkeypatch, tmp_path):
        from font_intel.compliance import default_rules_path
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
        assert default_rules_path() == os.path.join(
            str(tmp_path), "video_subtitle_ocr", "compliance_rules.json")

    def test_default_rules_path_home_fallback(self, monkeypatch):
        from font_intel.compliance import default_rules_path
        monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
        assert default_rules_path() == os.path.join(
            os.path.expanduser("~"), ".config", "video_subtitle_ocr",
            "compliance_rules.json")


# ---------------------------------------------------------------------------
# decide() 决策
# ---------------------------------------------------------------------------

class TestDecide:
    def test_db_hit_open_source_allow_with_official_url(self, db):
        from font_intel.compliance import decide
        _seed_font(db, "Noto Sans CJK SC", license_category="open_source",
                   official_url="https://fonts.google.com/noto")
        decision = decide("Noto Sans CJK SC", scene="commercial", db=db)
        assert decision.action == "allow"
        assert decision.license_category == "open_source"
        assert decision.official_url == "https://fonts.google.com/noto"
        assert decision.granted is False
        assert decision.scene == "commercial"

    def test_db_miss_unknown_prompt(self, db):
        from font_intel.compliance import decide
        _seed_font(db, "Some Other Font", license_category="open_source")
        decision = decide("UnknownFont", scene="publish", db=db)
        assert decision.action == "prompt"
        assert decision.license_category == "unknown"

    def test_db_exception_degrades_to_unknown_without_raise(self, db):
        from font_intel.compliance import decide

        class BrokenDB:
            def lookup_font(self, name):
                raise RuntimeError("db corrupted")

        decision = decide("AnyFont", scene="personal", db=BrokenDB())
        assert decision.action == "prompt"
        assert decision.license_category == "unknown"


# ---------------------------------------------------------------------------
# 已获授权放行（user_overrides）
# ---------------------------------------------------------------------------

class TestGrantLicense:
    def test_grant_allow_then_revoke_restore_prompt(self, db):
        from font_intel.compliance import decide, grant_license, revoke_license
        _seed_font(db, "PaidFont", license_category="commercial_paid")
        assert decide("PaidFont", scene="commercial", db=db).action == "prompt"

        grant_license("PaidFont", db)
        granted = decide("PaidFont", scene="commercial", db=db)
        assert granted.action == "allow"
        assert granted.granted is True
        assert "用户已确认获得授权" in granted.reason

        revoke_license("PaidFont", db)
        assert decide("PaidFont", scene="commercial", db=db).action == "prompt"

    def test_grant_persisted_in_user_overrides_approved_1(self, db):
        from font_intel.compliance import grant_license
        grant_license("PaidFont", db)
        rows = db._conn.execute(
            "SELECT target_table, target_key, patch_json, approved "
            "FROM user_overrides WHERE target_key='PaidFont'").fetchall()
        assert len(rows) == 1
        assert rows[0]["target_table"] == "fonts"
        assert rows[0]["approved"] == 1
        patch = json.loads(rows[0]["patch_json"])
        assert patch.get("license_granted") is True
        assert patch.get("granted_at")


# ---------------------------------------------------------------------------
# replace_auto 置信度门槛
# ---------------------------------------------------------------------------

class TestReplaceAutoGate:
    def _rules_with_replace(self):
        from font_intel.compliance import ComplianceRules
        rules = ComplianceRules()
        rules.set_override("commercial_paid", "commercial", "replace_auto")
        return rules

    def test_high_confidence_with_alternatives(self, db):
        from font_intel.compliance import decide
        _seed_font(db, "PaidFont", license_category="commercial_paid")
        decision = decide("PaidFont", scene="commercial", db=db,
                          confidence=0.9, rules=self._rules_with_replace(),
                          alternatives_provider=lambda name: ["Source Han Sans"])
        assert decision.action == "replace_auto"
        assert decision.alternatives == ["Source Han Sans"]

    def test_low_confidence_degrades_to_prompt(self, db):
        from font_intel.compliance import decide
        _seed_font(db, "PaidFont", license_category="commercial_paid")
        decision = decide("PaidFont", scene="commercial", db=db,
                          confidence=0.6, rules=self._rules_with_replace(),
                          alternatives_provider=lambda name: ["Source Han Sans"])
        assert decision.action == "prompt"
        assert decision.alternatives == []
        assert "置信度" in decision.reason

    def test_missing_confidence_degrades_to_prompt(self, db):
        from font_intel.compliance import decide
        _seed_font(db, "PaidFont", license_category="commercial_paid")
        decision = decide("PaidFont", scene="commercial", db=db,
                          rules=self._rules_with_replace(),
                          alternatives_provider=lambda name: ["Source Han Sans"])
        assert decision.action == "prompt"
        assert "置信度" in decision.reason

    def test_empty_alternatives_degrades_to_prompt(self, db):
        from font_intel.compliance import decide
        _seed_font(db, "PaidFont", license_category="commercial_paid")
        decision = decide("PaidFont", scene="commercial", db=db,
                          confidence=0.95, rules=self._rules_with_replace(),
                          alternatives_provider=lambda name: [])
        assert decision.action == "prompt"
        assert "替代" in decision.reason

    def test_no_provider_degrades_to_prompt(self, db):
        from font_intel.compliance import decide
        _seed_font(db, "PaidFont", license_category="commercial_paid")
        decision = decide("PaidFont", scene="commercial", db=db,
                          confidence=0.95, rules=self._rules_with_replace(),
                          alternatives_provider=None)
        assert decision.action == "prompt"


# ---------------------------------------------------------------------------
# 硬红线
# ---------------------------------------------------------------------------

class TestHardRedLine:
    def test_hard_red_line_constant_declared(self):
        from font_intel.compliance import HARD_RED_LINE
        assert isinstance(HARD_RED_LINE, str) and HARD_RED_LINE

    def test_sanitize_url_blocks_every_marker(self, caplog):
        from font_intel.compliance import BLOCKED_URL_MARKERS, sanitize_url
        for marker in BLOCKED_URL_MARKERS:
            url = f"https://dl.example.com/{marker}/paid-font.7z"
            with caplog.at_level(logging.WARNING):
                assert sanitize_url(url) == "", marker
        assert any(r.levelno >= logging.WARNING for r in caplog.records)

    def test_sanitize_url_case_insensitive(self):
        from font_intel.compliance import sanitize_url
        assert sanitize_url("https://example.com/CRACKED/PaidFont.ttf") == ""
        assert sanitize_url("https://example.com/Torrent/PaidFont.ttf") == ""

    def test_sanitize_url_keeps_official(self):
        from font_intel.compliance import sanitize_url
        url = "https://fonts.google.com/specimen/Noto+Sans+SC"
        assert sanitize_url(url) == url
        assert sanitize_url("") == ""
        assert sanitize_url(None) == ""

    def test_decision_official_url_only_from_db(self, db):
        from font_intel.compliance import decide
        _seed_font(db, "PaidFont", license_category="commercial_paid",
                   official_url="https://vendor.example.com/buy")
        decision = decide("PaidFont", scene="personal", db=db)
        assert decision.official_url == "https://vendor.example.com/buy"

    def test_decision_blocked_db_url_sanitized(self, db):
        from font_intel.compliance import decide
        _seed_font(db, "CrackedFont", license_category="commercial_paid",
                   official_url="https://pirate.example.com/crack/PaidFont")
        decision = decide("CrackedFont", scene="personal", db=db)
        assert decision.official_url == ""


# ---------------------------------------------------------------------------
# 决策对象可序列化
# ---------------------------------------------------------------------------

class TestDecisionSerializable:
    def test_decision_json_dumps(self, db):
        from font_intel.compliance import ComplianceRules, decide
        _seed_font(db, "PaidFont", license_category="commercial_paid")
        rules = ComplianceRules()
        rules.set_override("commercial_paid", "commercial", "replace_auto")
        decision = decide("PaidFont", scene="commercial", db=db,
                          confidence=0.9, rules=rules,
                          alternatives_provider=lambda name: ["Noto Sans SC"])
        payload = json.dumps(dataclasses.asdict(decision), ensure_ascii=False)
        assert "PaidFont" in payload
        assert decision.to_dict() == dataclasses.asdict(decision)
