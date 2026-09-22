# tests/test_font_etl_clean.py
"""Unit tests for font_intel.etl.clean (S0.5 标准化清洗).

覆盖：注记括号剥离（台繁/港繁/开粗体/注：…）、(P)/(Pro) 品名括号保留
（dynacw 官方约定：P=Proportional 调合字）、含空格英数交叉引用剥离
（华康宋体W7(华康宋体 Std W7)）、纯注记段剥空、一格多名展开（共享前缀
补全 + W7 等字重 token 白名单分发 + CJK 不展开 + 原组合串保真）、
note 列（schema v4）入库与查询往返。
"""

from __future__ import annotations

import os
import sys

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from font_intel.etl.clean import (  # noqa: E402
    clean_candidate_name,
    expand_combined_jp_name,
    is_probably_name,
)
from font_intel.fonts_db import FontsDB  # noqa: E402


# ── clean_candidate_name ─────────────────────────────────────────
class TestCleanCandidateName:
    def test_region_qualifier_stripped(self):
        name, quals = clean_candidate_name("華康中明體(P)（台繁）")
        assert name == "華康中明體(P)"  # (P) 是品名组成部分，保留
        assert quals == ["台繁"]

    def test_style_instruction_stripped(self):
        name, quals = clean_candidate_name("华康古籍银杏W3P（开粗体）")
        assert name == "华康古籍银杏W3P"
        assert quals == ["开粗体"]

    def test_xref_with_space_and_latin_stripped(self):
        name, quals = clean_candidate_name("华康宋体W7(华康宋体 Std W7)")
        assert name == "华康宋体W7"
        assert quals == ["华康宋体 Std W7"]

    def test_proportional_paren_kept(self):
        # dynacw 官方 FAQ：(P)=Proportional 调合字，正式品名
        name, quals = clean_candidate_name("華康細明體(P)")
        assert name == "華康細明體(P)"
        assert quals == []

    def test_plain_name_unchanged(self):
        assert clean_candidate_name("思源黑体") == ("思源黑体", [])
        assert clean_candidate_name("FOT-ChiaroStd M") == ("FOT-ChiaroStd M", [])

    def test_pure_qualifier_segment_emptied(self):
        name, quals = clean_candidate_name("（台繁）")
        assert name == ""
        assert quals == ["台繁"]
        assert is_probably_name(name) is False

    def test_whitespace_and_separators_normalized(self):
        name, _ = clean_candidate_name("  方正新秀麗  ")
        assert name == "方正新秀麗"

    def test_sc_tc_qualifiers(self):
        name, quals = clean_candidate_name("華康明體W3（港繁）")
        assert name == "華康明體W3"
        assert quals == ["港繁"]


# ── expand_combined_jp_name ──────────────────────────────────────
class TestExpandCombinedJpName:
    def test_shared_prefix_expansion(self):
        out = expand_combined_jp_name("DFGabiMincho Std/StdN")
        assert out == ["DFGabiMincho Std/StdN",  # 原组合串保真
                       "DFGabiMincho Std", "DFGabiMincho StdN"]

    def test_shared_weight_suffix_distribution(self):
        out = expand_combined_jp_name("DFHSMinchoR Pro-5/Pro-6/Pro-6N W7")
        assert out == ["DFHSMinchoR Pro-5/Pro-6/Pro-6N W7",
                       "DFHSMinchoR Pro-5 W7",
                       "DFHSMinchoR Pro-6 W7",
                       "DFHSMinchoR Pro-6N W7"]

    def test_non_weight_tail_not_distributed(self):
        # 末段 token 不在字重/样式白名单 → 不当共享后缀分发
        out = expand_combined_jp_name("FOT-MaruberiGeo Std/Pr6N G")
        assert out[0] == "FOT-MaruberiGeo Std/Pr6N G"
        # G 不在白名单 → 末段保持原样（不生成 ... Std G）
        assert "FOT-MaruberiGeo Std G" not in out

    def test_cjk_cell_not_expanded(self):
        assert expand_combined_jp_name("华康俪金黑 & 華康儷金黑") == [
            "华康俪金黑 & 華康儷金黑"]

    def test_single_segment_unchanged(self):
        assert expand_combined_jp_name("A-OTF A1 Mincho Std Bold") == [
            "A-OTF A1 Mincho Std Bold"]


# ── schema v4 note 列往返 ────────────────────────────────────────
class TestNoteColumnRoundtrip:
    def test_import_seed_note_persisted(self, tmp_path):
        db_path = str(tmp_path / "fonts.db")
        with FontsDB(db_path) as db:
            assert db.get_schema_version() >= 4
            db.import_seed([
                {"table": "jp_cn_font_map", "jp_name": "DFPGabiMincho-W3",
                 "cn_name": "华康古籍银杏W3P", "kind": "mapping",
                 "method": "rule", "confidence": 0.9,
                 "source_file": "table.xlsx", "line_no": 3,
                 "source": "Seekladoom/Japanese-Chinese-Fonts-adaptation (MIT)",
                 "note": "简体；开粗体"},
                {"table": "jp_cn_font_map", "jp_name": "华康俪金黑",
                 "cn_name": None, "kind": "negative_mapping",
                 "method": "rule", "confidence": 0.9,
                 "source_file": "errata.txt", "line_no": 4,
                 "source": "Seekladoom/Japanese-Chinese-Fonts-adaptation (MIT)",
                 "note": "没有本家对应的日文版本"},
            ])
            pos = db.lookup_jp_cn("DFPGabiMincho-W3")
            assert pos[0]["note"] == "简体；开粗体"
            neg = db.lookup_jp_cn("华康俪金黑", include_negative=True)
            assert neg[0]["note"] == "没有本家对应的日文版本"

    def test_import_seed_without_note_ok(self, tmp_path):
        db_path = str(tmp_path / "fonts.db")
        with FontsDB(db_path) as db:
            db.import_seed([
                {"table": "jp_cn_font_map", "jp_name": "A1明朝",
                 "cn_name": "华文宋体", "method": "rule",
                 "confidence": 0.95, "source_file": "t.txt", "line_no": 1,
                 "source": "s"},
            ])
            assert db.lookup_jp_cn("A1明朝")[0]["note"] is None
