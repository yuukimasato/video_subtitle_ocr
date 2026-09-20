# tests/test_font_etl_rules.py
"""Unit tests for font_intel.etl (T1.3 映射表 ETL：S0 归一 + S1 规则解析).

覆盖：编码探测链（GBK 解码、UTF-8 优先于 GBK、latin-1 兜底）、CRLF/全角/
破折号/BOM 归一且不动汉字、六类句式黄金用例（mapping/rename/
negative_mapping/weight_list/flag_sc_tc/todo）及 pairing/append/pitfall
变体、unresolved 保留原行与行号、多行混合段落黄金快照、parse_file 的
GBK 端到端、CLI（jsonl 行数 / --stats / 退出码 0 与 2）、厂商前缀推断。
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from font_intel.etl.normalize import decode_bytes, normalize_text  # noqa: E402
from font_intel.etl.rule_parser import (  # noqa: E402
    VENDOR_PREFIX_RULES,
    ParseResult,
    infer_vendor,
    parse_bytes,
    parse_file,
    parse_lines,
)

CLI_SCRIPT = Path(PROJECT_ROOT) / "scripts" / "import_jp_cn_font_map.py"

RECORD_FIELDS = {
    "type", "names", "target", "note",
    "source_file", "line_no", "method", "confidence", "vendor",
}


# ── S0：编码探测 ──────────────────────────────────────────────────
class TestS0Decode:
    def test_decode_gbk(self):
        text = "A1明朝 → 华文宋体\r\n華康儷金黑，没有本家对应的日文版本\r\n"
        assert decode_bytes(text.encode("gbk")) == text

    def test_utf8_priority_over_gbk(self):
        # 合法 UTF-8 必须按 UTF-8 解出原文，不得被 GBK 链误判成乱码
        text = "方正悠宋，后6个字重待追加"
        assert decode_bytes(text.encode("utf-8")) == text

    def test_latin1_fallback(self):
        # 0xFF 在 UTF-8/GBK/Big5 均为非法字节，落到 latin-1 兜底
        assert decode_bytes(b"abc\xff\xfe") == "abcÿþ"


# ── S0：文本归一 ──────────────────────────────────────────────────
class TestS0Normalize:
    def test_crlf_and_cr_to_lf(self):
        assert normalize_text("a\r\nb\rc") == "a\nb\nc"

    def test_fullwidth_to_halfwidth(self):
        assert normalize_text("ＡＢａｂ１２３　Ｘ") == "ABab123 X"

    def test_dash_and_wave_unified(self):
        # —(2014) –(2013) ―(2015) ～(FF5E) 〜(301C) －(FF0D)
        assert normalize_text("—–―～〜－") == "---~~-"

    def test_bom_and_zero_width_removed(self):
        assert normalize_text("\ufeff华康\u200b俪金黑\u2060") == "华康俪金黑"

    def test_hanzi_untouched(self):
        # 简繁体保持原样，归并属后续任务
        text = "華康儷金黑与华康俪金黑"
        assert normalize_text(text) == text


# ── S1：单行句式黄金用例 ─────────────────────────────────────────
class TestS1Golden:
    def test_mapping_arrow(self):
        res = parse_lines(["A1明朝 → 华文宋体"], source_file="map.txt")
        assert not res.unresolved
        rec = res.records[0]
        assert set(rec) == RECORD_FIELDS
        assert rec["type"] == "mapping"
        assert rec["names"] == ["A1明朝"]
        assert rec["target"] == "华文宋体"
        assert rec["note"] == ""
        assert rec["source_file"] == "map.txt"
        assert rec["line_no"] == 1
        assert rec["method"] == "rule"
        assert rec["confidence"] == 0.95

    def test_mapping_ascii_arrow_variants(self):
        res = parse_lines(["MS-Gothic -> 黑体", "HG明朝体->宋体"], source_file="m.txt")
        assert [r["type"] for r in res.records] == ["mapping", "mapping"]
        assert res.records[0]["names"] == ["MS-Gothic"]
        assert res.records[0]["target"] == "黑体"
        assert res.records[1]["names"] == ["HG明朝体"]
        assert res.records[1]["target"] == "宋体"

    def test_rename_fullwidth_parens(self):
        res = parse_lines(["DFPMaruMojiRD-W12（旧：DFPBrushRD-W12）"])
        rec = res.records[0]
        assert rec["type"] == "rename"
        assert rec["names"] == ["DFPMaruMojiRD-W12", "DFPBrushRD-W12"]
        assert rec["note"] == "rename"
        assert rec["target"] is None
        assert rec["confidence"] == 0.95

    def test_rename_halfwidth_parens(self):
        res = parse_lines(["DFPMaruMojiRD-W12 (旧: DFPBrushRD-W12)"])
        assert res.records[0]["type"] == "rename"
        assert res.records[0]["names"] == ["DFPMaruMojiRD-W12", "DFPBrushRD-W12"]

    def test_negative_mapping_alias_split(self):
        line = "华康俪金黑 & 華康儷金黑，没有本家对应的日文版本"
        rec = parse_lines([line]).records[0]
        assert rec["type"] == "negative_mapping"
        # & 两侧的简繁写法都进 names（别名列表）
        assert rec["names"] == ["华康俪金黑", "華康儷金黑"]
        assert rec["target"] is None
        assert rec["note"] == "没有本家对应的日文版本"
        assert rec["confidence"] == 0.9

    def test_negative_mapping_fullwidth_amp(self):
        line = "华康俪金黑 ＆ 華康儷金黑，没有对应的日文版本"
        rec = parse_lines([line]).records[0]
        assert rec["type"] == "negative_mapping"
        assert rec["names"] == ["华康俪金黑", "華康儷金黑"]

    def test_pairing_recorded_as_mapping(self):
        rec = parse_lines(["FOT-Chiaro 追加搭配：汉仪润圆"]).records[0]
        assert rec["type"] == "mapping"
        assert rec["names"] == ["FOT-Chiaro"]
        assert rec["target"] == "汉仪润圆"
        assert rec["note"] == "pairing"
        assert rec["confidence"] == 0.9

    def test_weight_list_multiple_names(self):
        # 顿号/逗号混用切分
        rec = parse_lines(["E字重：方正粗黑宋、华康俪金黑，MSungGold"]).records[0]
        assert rec["type"] == "weight_list"
        assert rec["names"] == ["方正粗黑宋", "华康俪金黑", "MSungGold"]
        assert rec["note"] == "E"
        assert rec["target"] is None

    def test_weight_list_single_name(self):
        rec = parse_lines(["B字重：正中黑"]).records[0]
        assert rec["type"] == "weight_list"
        assert rec["names"] == ["正中黑"]
        assert rec["note"] == "B"

    def test_flag_sc_tc(self):
        rec = parse_lines(["森泽UD新黑 简繁通用"]).records[0]
        assert rec["type"] == "flag_sc_tc"
        assert rec["names"] == ["森泽UD新黑"]
        assert rec["confidence"] == 0.9

    def test_flag_sc_tc_append(self):
        rec = parse_lines(["简繁通用像素字体追加：UniFont"]).records[0]
        assert rec["type"] == "flag_sc_tc"
        assert rec["names"] == ["简繁通用像素字体", "UniFont"]

    def test_todo(self):
        rec = parse_lines(["方正悠宋后6个字重待追加"]).records[0]
        assert rec["type"] == "todo"
        assert rec["names"] == ["方正悠宋"]
        assert rec["confidence"] == 0.9

    def test_pitfall_slash_separated(self):
        rec = parse_lines(["华康新综艺体 / 华康雅宋体"]).records[0]
        assert rec["type"] == "pitfall"
        assert rec["names"] == ["华康新综艺体", "华康雅宋体"]
        assert rec["confidence"] == 0.7

    def test_pitfall_mixed_separators(self):
        # 顿号与空格混用切分
        rec = parse_lines(["华康新综艺体、华康雅宋体 华康俪金黑"]).records[0]
        assert rec["type"] == "pitfall"
        assert rec["names"] == ["华康新综艺体", "华康雅宋体", "华康俪金黑"]

    def test_generic_append_as_mapping(self):
        rec = parse_lines(["XFont 追加：YFont"]).records[0]
        assert rec["type"] == "mapping"
        assert rec["names"] == ["XFont"]
        assert rec["target"] == "YFont"
        assert rec["note"] == "append"


# ── S1：unresolved ───────────────────────────────────────────────
class TestS1Unresolved:
    def test_unrecognized_lines_keep_raw_and_line_no(self):
        res = parse_lines(
            ["", "   ", "# 注释", "这是一段说明文字", "华康俪金黑"],
            source_file="raw.txt",
        )
        assert not res.records
        assert [(u["line_no"], u["reason"]) for u in res.unresolved] == [
            (1, "empty"),
            (2, "empty"),
            (3, "comment"),
            (4, "unrecognized"),
            (5, "unrecognized"),
        ]
        # 原行文本被保留（仅去行尾换行符）
        assert [u["line"] for u in res.unresolved] == [
            "", "   ", "# 注释", "这是一段说明文字", "华康俪金黑",
        ]
        assert all(u["source_file"] == "raw.txt" for u in res.unresolved)


# ── S1：多行混合段落黄金快照 ─────────────────────────────────────
MIXED_LINES = [
    "# 中日字体对照（合成样例）",                                   # 1 comment
    "A1明朝 → 华文宋体",                                           # 2 mapping
    "DFPMaruMojiRD-W12（旧：DFPBrushRD-W12）",                     # 3 rename
    "华康俪金黑 & 華康儷金黑，没有本家对应的日文版本",             # 4 negative_mapping
    "FOT-Chiaro 追加搭配：汉仪润圆",                               # 5 mapping(pairing)
    "E字重：方正粗黑宋、华康俪金黑，MSungGold",                    # 6 weight_list
    "森泽UD新黑 简繁通用",                                         # 7 flag_sc_tc
    "方正悠宋后6个字重待追加",                                     # 8 todo
    "华康新综艺体 / 华康雅宋体",                                   # 9 pitfall
    "简繁日名称翻车：",                                            # 10 段落头 → unresolved
]


class TestS1MixedParagraph:
    def test_mixed_paragraph_snapshot(self):
        res = parse_lines(MIXED_LINES, source_file="mixed.txt")
        assert isinstance(res, ParseResult)
        # 黄金快照：每条记录的行号与类型
        assert [(r["line_no"], r["type"]) for r in res.records] == [
            (2, "mapping"),
            (3, "rename"),
            (4, "negative_mapping"),
            (5, "mapping"),
            (6, "weight_list"),
            (7, "flag_sc_tc"),
            (8, "todo"),
            (9, "pitfall"),
        ]
        assert res.stats() == {
            "mapping": 2,
            "rename": 1,
            "negative_mapping": 1,
            "weight_list": 1,
            "flag_sc_tc": 1,
            "todo": 1,
            "pitfall": 1,
            "unresolved": 2,
        }
        assert [(u["line_no"], u["reason"]) for u in res.unresolved] == [
            (1, "comment"),
            (10, "unrecognized"),
        ]

    def test_pairing_note_in_mixed(self):
        res = parse_lines(MIXED_LINES)
        pairing = next(r for r in res.records if r["line_no"] == 5)
        assert pairing["note"] == "pairing"
        assert pairing["target"] == "汉仪润圆"


# ── 文件级入口 ───────────────────────────────────────────────────
class TestFileEntry:
    def test_parse_bytes_pipeline(self):
        # S0 全角/换行归一后进入 S1
        res = parse_bytes("Ａ１明朝 → 华文宋体\r\n".encode("utf-8"), source_file="x.txt")
        assert res.records[0]["names"] == ["A1明朝"]
        assert res.stats() == {"mapping": 1, "unresolved": 0}

    def test_parse_bytes_trailing_newline_no_ghost_line(self):
        res = parse_bytes("A1明朝 → 华文宋体\n".encode("utf-8"))
        assert res.stats() == {"mapping": 1, "unresolved": 0}

    def test_parse_file_gbk_end_to_end(self, tmp_path):
        text = (
            "# 勘误\r\n"
            "A1明朝 → 华文宋体\r\n"
            "E字重：方正粗黑宋、华康俪金黑\r\n"
            "华康俪金黑 & 華康儷金黑，没有本家对应的日文版本\r\n"
        )
        p = tmp_path / "jp_cn_map.txt"
        p.write_bytes(text.encode("gbk"))
        res = parse_file(p)
        assert res.stats() == {
            "mapping": 1,
            "weight_list": 1,
            "negative_mapping": 1,
            "unresolved": 1,
        }
        assert res.records[0]["source_file"] == str(p)
        # CRLF 已归一，名字不含 \r
        assert all("\r" not in n for r in res.records for n in r["names"])


# ── 厂商前缀 ─────────────────────────────────────────────────────
class TestVendorPrefix:
    def test_vendor_prefix_rules_core_entries(self):
        table = dict(VENDOR_PREFIX_RULES)
        assert table["DF"] == "DynaFont"
        assert table["FOT-"] == "Fontworks"
        assert table["A-OTF"] == "Morisawa"
        assert table["华康"] == "DynaFont"
        assert table["方正"] == "Founder"
        assert table["汉仪"] == "HanYi"
        assert table["蒙纳"] == "Monotype"

    def test_infer_vendor_direct(self):
        assert infer_vendor("FOT-Chiaro") == "Fontworks"
        assert infer_vendor("A-OTF-UD黎ミン") == "Morisawa"
        assert infer_vendor("华康俪金黑") == "DynaFont"
        assert infer_vendor("DFPMaruMojiRD-W12") == "DynaFont"
        # 未知前缀留空，不猜
        assert infer_vendor("A1明朝") == ""

    def test_vendor_on_records(self):
        res = parse_lines(
            ["FOT-Chiaro 追加搭配：汉仪润圆", "A1明朝 → 华文宋体"],
        )
        assert res.records[0]["vendor"] == "Fontworks"
        assert res.records[1]["vendor"] == ""


# ── CLI ──────────────────────────────────────────────────────────
class TestCLI:
    def test_cli_jsonl_and_stats(self, tmp_path):
        f1 = tmp_path / "a.txt"
        f1.write_text("A1明朝 → 华文宋体\n\n看不懂的一行\n", encoding="utf-8")
        f2 = tmp_path / "b.txt"
        f2.write_bytes("FOT-Chiaro 追加搭配：汉仪润圆\n".encode("gbk"))
        out = tmp_path / "out.jsonl"
        proc = subprocess.run(
            [sys.executable, str(CLI_SCRIPT),
             "--input", str(f1), str(f2),
             "--output", str(out), "--stats"],
            capture_output=True, text=True,
        )
        assert proc.returncode == 0, proc.stderr
        lines = out.read_text(encoding="utf-8").splitlines()
        assert len(lines) == 2
        recs = [json.loads(line) for line in lines]
        assert recs[0]["type"] == "mapping"
        assert recs[0]["source_file"] == str(f1)
        assert recs[1]["note"] == "pairing"
        assert recs[1]["source_file"] == str(f2)
        # --stats：a.txt（空行 + 无法识别）unresolved=2；b.txt unresolved=0
        assert "unresolved=2" in proc.stdout
        assert "unresolved=0" in proc.stdout
        assert "TOTAL" in proc.stdout

    def test_cli_missing_input_exit_2(self, tmp_path):
        proc = subprocess.run(
            [sys.executable, str(CLI_SCRIPT),
             "--input", str(tmp_path / "nope.txt"),
             "--output", str(tmp_path / "o.jsonl")],
            capture_output=True, text=True,
        )
        assert proc.returncode == 2
        assert "nope.txt" in proc.stderr
