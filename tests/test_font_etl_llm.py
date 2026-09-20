# tests/test_font_etl_llm.py
"""Unit tests for font_intel.etl.llm_structurer (T1.4 ETL S2 LLM 结构化 + S3 词表校验).

覆盖：空输入 / api_key 为空短路（不触 LLM）、合法批次（含 code fence 变体）
字段与方法透传、batch_size 切分、防幻觉子串校验（hallucinated_name）、
S3 词表校验（unknown_font_name / word_db=None 跳过词表层）、畸形输出
（非 JSON / 数组长度不齐 / type 非法 → llm_output_invalid）、LlmApiError
与 tenacity.RetryError 的优雅降级（llm_failed，不重试不中断）、stats 计数。

测试全程 mock：monkeypatch llm_structurer._call_llm（薄封装），绝不真实
调用网络；退避语义归 core.llm_client。
"""

from __future__ import annotations

import json
import os
import sys
import types

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

import pytest  # noqa: E402

import core.llm_client as llm_client  # noqa: E402
import font_intel.etl.llm_structurer as llm_structurer  # noqa: E402
from core.llm_client import LlmApiError  # noqa: E402
from core.llm_prompts import get_prompt  # noqa: E402
from font_intel.etl.llm_structurer import (  # noqa: E402
    EtlLlmConfig,
    StructureResult,
    structure_unresolved,
)
from font_intel.fonts_db import FontsDB  # noqa: E402

RECORD_FIELDS = {
    "type", "names", "target", "note",
    "source_file", "line_no", "method", "confidence", "vendor",
}


def _cfg(**kw) -> EtlLlmConfig:
    kw.setdefault("api_key", "sk-test")
    kw.setdefault("api_base_url", "http://localhost/v1")
    kw.setdefault("model", "test-model")
    return EtlLlmConfig(**kw)


def _line(line_no: int, text: str, source: str = "map.txt") -> dict:
    """rule_parser.unresolved 同构输入行。"""
    return {
        "source_file": source,
        "line_no": line_no,
        "line": text,
        "reason": "unrecognized",
    }


def _item(idx: int, rtype: str, names: list, target=None, conf: float = 0.8) -> dict:
    return {"id": idx, "type": rtype, "names": names, "target": target,
            "confidence": conf}


def _resp(content: str):
    return types.SimpleNamespace(
        choices=[types.SimpleNamespace(message=types.SimpleNamespace(content=content))]
    )


def _install_llm(monkeypatch, outputs: list):
    """按调用次序回放 outputs（str 或异常实例）；记录每次调用。"""
    calls = {"n": 0, "messages": []}

    def fake_call_llm(messages, cfg):
        calls["n"] += 1
        calls["messages"].append(messages)
        out = outputs[calls["n"] - 1]
        if isinstance(out, Exception):
            raise out
        return _resp(out)

    monkeypatch.setattr(llm_structurer, "_call_llm", fake_call_llm)
    return calls


def _seed_db(names: list) -> FontsDB:
    db = FontsDB(":memory:")
    db.import_seed(
        [{"table": "fonts", "canonical_name": n} for n in names]
    )
    return db


# ── 短路：空输入 / 无 api_key ────────────────────────────────────
class TestShortCircuit:
    def test_empty_input_no_llm_call(self, monkeypatch):
        calls = _install_llm(monkeypatch, [])
        res = structure_unresolved([], _cfg())
        assert isinstance(res, StructureResult)
        assert res.records == []
        assert res.pending_human == []
        assert calls["n"] == 0
        assert res.stats() == {"pending_human": 0}

    def test_no_api_key_all_pending_no_llm(self, monkeypatch):
        calls = _install_llm(monkeypatch, [])
        lines = [_line(3, "看不懂的一行"), _line(9, "另一行", source="b.txt")]
        res = structure_unresolved(lines, _cfg(api_key="  "))
        assert calls["n"] == 0  # 绝不触网
        assert [p["reason"] for p in res.pending_human] == ["no_api_key"] * 2
        assert res.records == []
        assert res.pending_human[0]["line_no"] == 3
        assert res.pending_human[1]["source_file"] == "b.txt"


# ── 正常批：字段 / 透传 / 切分 ───────────────────────────────────
class TestHappyPath:
    def test_valid_batch_fields_and_passthrough(self, monkeypatch):
        payload = [
            _item(0, "mapping", ["A1明朝"], target="华文宋体", conf=0.8),
            _item(1, "rename", ["新名字体", "旧名字体"]),
        ]
        fenced = "```json\n{}\n```".format(json.dumps(payload, ensure_ascii=False))
        calls = _install_llm(monkeypatch, [fenced])
        lines = [_line(17, "A1明朝 追加搭配：华文宋体"),
                 _line(18, "新名字体（旧：旧名字体）", source="other.txt")]
        res = structure_unresolved(lines, _cfg())

        assert calls["n"] == 1
        assert len(res.records) == 2
        assert res.pending_human == []
        mapping, rename = res.records
        assert set(mapping) == RECORD_FIELDS
        assert mapping["type"] == "mapping"
        assert mapping["names"] == ["A1明朝"]
        assert mapping["target"] == "华文宋体"
        assert mapping["source_file"] == "map.txt"
        assert mapping["line_no"] == 17
        assert mapping["method"] == "llm"
        assert mapping["confidence"] == 0.8  # confidence 透传
        assert rename["type"] == "rename"
        assert rename["names"] == ["新名字体", "旧名字体"]
        assert rename["target"] is None
        assert rename["source_file"] == "other.txt"
        assert rename["line_no"] == 18
        assert rename["method"] == "llm"

    def test_raw_key_input_without_source_file(self, monkeypatch):
        """直接传 {"raw", "line_no"} 形态也兼容，source_file 缺省空串。"""
        payload = [_item(0, "flag_sc_tc", ["森泽UD新黑"])]
        _install_llm(monkeypatch, [json.dumps(payload, ensure_ascii=False)])
        res = structure_unresolved(
            [{"raw": "森泽UD新黑 简繁通用", "line_no": 5, "reason": "unrecognized"}],
            _cfg(),
        )
        assert len(res.records) == 1
        rec = res.records[0]
        assert rec["type"] == "flag_sc_tc"
        assert rec["source_file"] == ""
        assert rec["line_no"] == 5
        assert rec["target"] is None

    def test_batch_splitting_three_lines_batch_size_two(self, monkeypatch):
        payload1 = [
            _item(0, "mapping", ["A1明朝"], target="华文宋体"),
            _item(1, "mapping", ["FOT-Chiaro"], target="汉仪润圆"),
        ]
        payload2 = [_item(0, "pitfall", ["华康新综艺体", "华康雅宋体"])]
        calls = _install_llm(monkeypatch, [
            json.dumps(payload1, ensure_ascii=False),
            json.dumps(payload2, ensure_ascii=False),
        ])
        lines = [
            _line(1, "A1明朝 → 华文宋体"),
            _line(2, "FOT-Chiaro 追加搭配：汉仪润圆"),
            _line(3, "华康新综艺体 / 华康雅宋体"),
        ]
        res = structure_unresolved(lines, _cfg(batch_size=2))
        assert calls["n"] == 2  # 3 行 / batch_size=2 → 2 次调用
        assert len(res.records) == 3
        assert [r["line_no"] for r in res.records] == [1, 2, 3]
        assert [r["type"] for r in res.records] == ["mapping", "mapping", "pitfall"]

    def test_messages_use_registered_prompt_and_user_lines(self, monkeypatch):
        payload = [_item(0, "mapping", ["A1明朝"], target="华文宋体")]
        calls = _install_llm(monkeypatch, [json.dumps(payload, ensure_ascii=False)])
        structure_unresolved([_line(1, "A1明朝 → 华文宋体")], _cfg())
        messages = calls["messages"][0]
        assert messages[0]["role"] == "system"
        assert get_prompt("etl_font_map_struct") in messages[0]["content"]
        user_text = messages[1]["content"]
        assert "A1明朝 → 华文宋体" in user_text


# ── 防幻觉：名字必须是源行原文子串 ───────────────────────────────
class TestAntiHallucination:
    def test_hallucinated_target_pending_valid_line_unaffected(self, monkeypatch):
        # 行1：target "幻构黑体" 不在源行 → 整条降 pending_human；
        # 行2：全部合法 → 正常收录（其余不受影响）。
        payload = [
            _item(0, "mapping", ["未知字形甲"], target="幻构黑体"),
            _item(1, "mapping", ["A1明朝"], target="华文宋体"),
        ]
        _install_llm(monkeypatch, [json.dumps(payload, ensure_ascii=False)])
        lines = [
            _line(7, "未知字形甲 长得像另一款"),
            _line(8, "A1明朝 → 华文宋体"),
        ]
        res = structure_unresolved(lines, _cfg())
        assert len(res.records) == 1
        assert res.records[0]["line_no"] == 8
        assert len(res.pending_human) == 1
        p = res.pending_human[0]
        assert p["reason"] == "hallucinated_name"
        assert p["line_no"] == 7
        assert p["line"] == "未知字形甲 长得像另一款"
        assert p["source_file"] == "map.txt"

    def test_hallucinated_name_among_names_pending(self, monkeypatch):
        payload = [_item(0, "rename", ["方正悠宋", "编造旧名"])]
        _install_llm(monkeypatch, [json.dumps(payload, ensure_ascii=False)])
        res = structure_unresolved(
            [_line(4, "方正悠宋（旧：悠宋旧版）")], _cfg())
        assert res.records == []
        assert [p["reason"] for p in res.pending_human] == ["hallucinated_name"]

    def test_fullwidth_normalization_still_matches(self, monkeypatch):
        # 归一化后比对：全角字母数字与源行一致即不算幻觉
        payload = [_item(0, "mapping", ["Ａ１明朝"], target="华文宋体")]
        _install_llm(monkeypatch, [json.dumps(payload, ensure_ascii=False)])
        res = structure_unresolved(
            [_line(2, "Ａ１明朝 → 华文宋体")], _cfg())
        assert [p["reason"] for p in res.pending_human] == []
        assert res.records[0]["names"] == ["Ａ１明朝"]


# ── S3 词表校验 ──────────────────────────────────────────────────
class TestVocabularyCheck:
    def test_vocab_hit_accepted(self, monkeypatch):
        payload = [_item(0, "mapping", ["A1明朝"], target="华文宋体")]
        _install_llm(monkeypatch, [json.dumps(payload, ensure_ascii=False)])
        db = _seed_db(["A1明朝", "华文宋体"])
        res = structure_unresolved([_line(1, "A1明朝 → 华文宋体")], _cfg(), word_db=db)
        db.close()
        assert res.pending_human == []
        assert len(res.records) == 1
        assert res.records[0]["method"] == "llm"

    def test_vocab_miss_pending_unknown_font_name(self, monkeypatch):
        # 名字是源行子串（过防幻觉），但词表未收录 → 人工复核
        payload = [_item(0, "mapping", ["汉仪润圆"], target="华文黑体")]
        _install_llm(monkeypatch, [json.dumps(payload, ensure_ascii=False)])
        db = _seed_db(["华文宋体"])  # 词表里没有这两个名字
        res = structure_unresolved(
            [_line(6, "汉仪润圆 搭配 华文黑体")], _cfg(), word_db=db)
        db.close()
        assert res.records == []
        assert [p["reason"] for p in res.pending_human] == ["unknown_font_name"]

    def test_word_db_none_skips_vocab_layer(self, monkeypatch):
        # 同样的输出，无词表时只做子串校验 → 收录
        payload = [_item(0, "mapping", ["汉仪润圆"], target="华文黑体", conf=0.7)]
        _install_llm(monkeypatch, [json.dumps(payload, ensure_ascii=False)])
        res = structure_unresolved(
            [_line(6, "汉仪润圆 搭配 华文黑体")], _cfg(), word_db=None)
        assert res.pending_human == []
        assert res.records[0]["confidence"] == 0.7
        assert res.records[0]["method"] == "llm"


# ── 畸形输出：整批降级，其余批正常 ───────────────────────────────
class TestMalformedOutput:
    def test_non_json_batch_invalid_other_batch_ok(self, monkeypatch):
        good = json.dumps([_item(0, "mapping", ["A1明朝"], target="华文宋体")],
                          ensure_ascii=False)
        calls = _install_llm(monkeypatch, ["这不是JSON输出", good])
        lines = [_line(1, "第一行乱内容"), _line(2, "A1明朝 → 华文宋体")]
        res = structure_unresolved(lines, _cfg(batch_size=1))
        assert calls["n"] == 2
        assert len(res.records) == 1
        assert res.records[0]["line_no"] == 2
        assert len(res.pending_human) == 1
        assert res.pending_human[0]["reason"] == "llm_output_invalid"
        assert res.pending_human[0]["line_no"] == 1

    def test_array_length_mismatch_whole_batch_invalid(self, monkeypatch):
        payload = [_item(0, "mapping", ["A1明朝"], target="华文宋体")]  # 少一条
        _install_llm(monkeypatch, [json.dumps(payload, ensure_ascii=False)])
        res = structure_unresolved(
            [_line(1, "A1明朝 → 华文宋体"), _line(2, "FOT-Chiaro → 汉仪润圆")],
            _cfg(batch_size=2),
        )
        assert res.records == []
        assert [p["reason"] for p in res.pending_human] == ["llm_output_invalid"] * 2
        assert [p["line_no"] for p in res.pending_human] == [1, 2]

    def test_invalid_type_whole_batch_invalid(self, monkeypatch):
        payload = [
            _item(0, "mapping", ["A1明朝"], target="华文宋体"),
            _item(1, "rumor", ["FOT-Chiaro"]),  # 六类之外
        ]
        _install_llm(monkeypatch, [json.dumps(payload, ensure_ascii=False)])
        res = structure_unresolved(
            [_line(1, "A1明朝 → 华文宋体"), _line(2, "FOT-Chiaro 相关行")],
            _cfg(batch_size=2),
        )
        assert res.records == []
        assert {p["reason"] for p in res.pending_human} == {"llm_output_invalid"}


# ── 优雅降级：LLM 服务端失败不中断 ───────────────────────────────
class TestDegradation:
    def test_llm_api_error_batch_pending_others_continue(self, monkeypatch):
        good = json.dumps([_item(0, "mapping", ["A1明朝"], target="华文宋体")],
                          ensure_ascii=False)
        err = LlmApiError("LLM API call failed after bounded retries: 429")
        calls = _install_llm(monkeypatch, [err, good])
        lines = [_line(1, "第一行"), _line(2, "A1明朝 → 华文宋体")]
        res = structure_unresolved(lines, _cfg(batch_size=1))
        assert calls["n"] == 2  # 失败批不重试，后续批继续
        assert len(res.records) == 1
        assert res.records[0]["line_no"] == 2
        assert len(res.pending_human) == 1
        assert res.pending_human[0]["reason"] == "llm_failed"
        assert res.pending_human[0]["line_no"] == 1

    def test_all_batches_fail_no_raise(self, monkeypatch):
        err = LlmApiError("bounded retries exhausted")
        calls = _install_llm(monkeypatch, [err, err])
        res = structure_unresolved([_line(1, "甲"), _line(2, "乙")], _cfg(batch_size=1))
        assert calls["n"] == 2
        assert res.records == []
        assert [p["reason"] for p in res.pending_human] == ["llm_failed"] * 2

    def test_retry_error_collapses_into_llm_api_error(self, monkeypatch):
        """core.llm_client.call_llm 抛 tenacity.RetryError 的变体（真实薄封装
        在位，不 mock _call_llm）：异常面必须收口 → 批次降级 llm_failed，
        绝不外泄，且批间互不影响。"""
        tenacity = pytest.importorskip("tenacity")
        good = json.dumps([_item(0, "mapping", ["A1明朝"], target="华文宋体")],
                          ensure_ascii=False)
        responses = iter([
            tenacity.RetryError(last_attempt=None),  # 第一批：重试耗尽形态
            _resp(good),
        ])

        def flaky_call_llm(*args, **kwargs):
            out = next(responses)
            if isinstance(out, Exception):
                raise out
            return out

        monkeypatch.setattr(llm_client, "call_llm", flaky_call_llm)
        res = structure_unresolved(
            [_line(1, "第一行"), _line(2, "A1明朝 → 华文宋体")], _cfg(batch_size=1))
        assert len(res.records) == 1
        assert res.records[0]["line_no"] == 2
        assert [p["reason"] for p in res.pending_human] == ["llm_failed"]


# ── stats 计数 ───────────────────────────────────────────────────
class TestStats:
    def test_stats_counts_records_and_pending(self, monkeypatch):
        payload = [
            _item(0, "mapping", ["A1明朝"], target="华文宋体"),
            _item(1, "mapping", ["未知字形甲"], target="幻构黑体"),
            _item(2, "flag_sc_tc", ["森泽UD新黑"]),
        ]
        _install_llm(monkeypatch, [json.dumps(payload, ensure_ascii=False)])
        lines = [
            _line(1, "A1明朝 → 华文宋体"),
            _line(2, "未知字形甲 某某"),
            _line(3, "森泽UD新黑 简繁通用"),
        ]
        res = structure_unresolved(lines, _cfg())
        assert res.stats() == {"mapping": 1, "flag_sc_tc": 1, "pending_human": 1}


# ── prompt 注册 ──────────────────────────────────────────────────
class TestPromptRegistration:
    def test_prompt_registered_with_six_types_and_guardrails(self):
        text = get_prompt("etl_font_map_struct")
        assert text.strip()
        for rtype in ("mapping", "rename", "negative_mapping",
                      "pitfall", "flag_sc_tc", "todo"):
            assert rtype in text
        # 结构化器定位：禁止补充源行没有的对应关系
        assert "禁止" in text
