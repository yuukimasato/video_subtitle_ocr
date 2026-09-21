# tests/test_matching.py
"""Unit tests for font_intel.matching (T2.4 中日字体三级链查询).

Covers: lookup_jp_font 三态（正映射/负映射/表中无记录）与简繁日别名写法、
覆盖层遮蔽/否决语义、反向查询（lookup_jp_cn_reverse + target_lang="ja"）、
open_source_alternates（fonts.alternates 查库字段优先 + jp_cn 映射链补充）、
三级链 resolve_font_chain（对位 → 开源替代 → 同风格类别兜底 → unresolved）、
overlay 优先于 seed、置信度与依据字段，以及 fonts_db 为本任务新增的最小
只读辅助（schema v3 alternates 列、list_fonts 合并语义）。

全部查询基于临时 SQLite（:memory:），无任何网络调用。
"""

from __future__ import annotations

import json
import os
import sys

import pytest

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from font_intel.fonts_db import FontsDB, MIGRATIONS  # noqa: E402
from font_intel.matching import (  # noqa: E402
    ALLOWED_LICENSE_CATEGORIES,
    CATEGORY_MATCH_CONFIDENCE,
    DEFAULT_MAPPING_CONFIDENCE,
    OPEN_SOURCE_ALTERNATE_CONFIDENCE,
    STATUS_RESOLVED,
    STATUS_UNRESOLVED,
    STYLE_CATEGORY_GROUPS,
    ChainCandidate,
    ChainResult,
    category_style_group,
    lookup_jp_font,
    open_source_alternates,
    resolve_font_chain,
)


# ---------------------------------------------------------------------------
# fixtures
# ---------------------------------------------------------------------------

def _font(name: str, **kw) -> dict:
    rec = {"table": "fonts", "canonical_name": name,
           "license_category": "unknown", "source": "seed/test"}
    rec.update(kw)
    return rec


def _alias(font: str, alias: str) -> dict:
    return {"table": "aliases", "font": font, "alias": alias,
            "source": "seed/test"}


def _map(jp: str, cn: str | None = None, **kw) -> dict:
    rec = {"table": "jp_cn_font_map", "jp_name": jp, "cn_name": cn,
           "kind": "mapping", "confidence": 0.9}
    rec.update(kw)
    return rec


def _seed_db() -> FontsDB:
    """造一库种子 + 覆盖数据的内存库：覆盖映射/字体/类别/语言各种组合。"""
    db = FontsDB(path=":memory:")
    db.import_seed([
        # -- fonts（类别词表混用中英文写法，验证同风格组归并）--
        _font("ＭＳ ゴシック", category="gothic", languages="ja,en",
              license_category="commercial_paid"),
        _font("ＭＳ 明朝", category="mincho", languages="ja,en",
              license_category="commercial_paid"),
        _font("思源黑体", category="黑体", languages="zh,en",
              license_category="open_source"),
        _font("Noto Sans CJK", category="sans", languages="zh,en",
              license_category="open_source", source="local_index"),
        _font("文泉驿微米黑", category="黑体", languages="zh",
              license_category="open_source", source="local_index"),
        _font("汉仪雅酷黑", category="黑体", languages="zh",
              license_category="commercial_paid",
              alternates=["思源黑体", "Noto Sans CJK", "方正兰亭黑"]),
        _font("方正兰亭黑", category="黑体", languages="zh",
              license_category="commercial_paid"),
        _font("思源宋体", category="明朝", languages="zh",
              license_category="open_source"),
        _font("汉仪正圆", category="round", languages="ja",
              license_category="commercial_paid"),
        _font("资源圆体", category="圆体", languages="zh",
              license_category="open_source"),
        _font("圆体F", category="round", languages="zh",
              license_category="free_commercial"),
        _font("华康圆体W", category="圆体", languages="zh",
              license_category="commercial_paid"),
        _font("源ノ角ゴシック", category="gothic", languages="ja",
              license_category="open_source"),
        _font("DFG勘亭流", category="gothic", languages="ja",
              license_category="commercial_paid", alternates=["文泉驿微米黑"]),
        _font("実験字体X", category="experimental", languages="zh",
              license_category="open_source"),
        _font("華康儷金黑", category="黑体", languages="zh,ja",
              license_category="commercial_paid"),
        _alias("華康儷金黑", "华康俪金黑"),
        # -- jp→cn 映射（正/负混合）--
        _map("ＭＳ ゴシック", "思源黑体", confidence=0.98),
        _map("源ノ角ゴシック", "思源黑体", confidence=0.95),
        _map("ＭＳ 明朝", "汉仪雅酷黑", confidence=0.9),
        _map(jp="華康勘亭流", cn=None, kind="negative_mapping", confidence=0.9),
        _map(jp="DFG勘亭流", cn=None, kind="negative_mapping", confidence=0.9),
        _map(jp="无置信度映射", cn="文泉驿微米黑", confidence=None),
    ])
    return db


# ---------------------------------------------------------------------------
# lookup_jp_font：正映射 / 负映射 / 无记录 三态 + 别名写法
# ---------------------------------------------------------------------------

def test_lookup_jp_font_positive_mapping_decorated():
    db = _seed_db()
    try:
        hits = lookup_jp_font(db, "ＭＳ ゴシック")
        assert len(hits) == 1
        row = hits[0]
        assert row["kind"] == "mapping"
        assert row["cn_name"] == "思源黑体"
        # 依据与溯源字段：basis、matched_name、layer、置信度
        assert row["basis"] == "mapping"
        assert row["matched_name"] == "ＭＳ ゴシック"
        assert row["layer"] == "seed"
        assert row["confidence"] == pytest.approx(0.98)
        assert row["source_file"] is None or isinstance(row["source_file"], str)
    finally:
        db.close()


def test_lookup_jp_font_negative_mapping_is_explicit_not_empty():
    db = _seed_db()
    try:
        hits = lookup_jp_font(db, "華康勘亭流")
        # 表中明确标记无对位：返回负映射行（cn_name=None），而非空列表
        assert len(hits) == 1
        assert hits[0]["kind"] == "negative_mapping"
        assert hits[0]["cn_name"] is None
        assert hits[0]["basis"] == "mapping"
    finally:
        db.close()


def test_lookup_jp_font_no_record_returns_empty():
    db = _seed_db()
    try:
        assert lookup_jp_font(db, " completely unknown font ") == []
        assert lookup_jp_font(db, "") == []
    finally:
        db.close()


def test_lookup_jp_font_tries_alias_spellings():
    db = _seed_db()
    try:
        # 映射键是繁体规范名，查询用简体别名写法 → 经 fonts 别名表命中
        db.import_seed([_map("華康儷金黑", "方正兰亭黑", confidence=0.8)])
        hits = lookup_jp_font(db, "华康俪金黑")
        assert len(hits) == 1
        assert hits[0]["cn_name"] == "方正兰亭黑"
        assert hits[0]["matched_name"] == "華康儷金黑"
    finally:
        db.close()


def test_lookup_jp_font_overlay_same_key_shadows_seed():
    db = _seed_db()
    try:
        db.add_overlay_mapping("ＭＳ ゴシック", "思源黑体",
                               confidence=0.55, method="user_confirm")
        hits = lookup_jp_font(db, "ＭＳ ゴシック")
        assert len(hits) == 1
        assert hits[0]["layer"] == "overlay"
        assert hits[0]["confidence"] == pytest.approx(0.55)
        assert hits[0]["method"] == "user_confirm"
    finally:
        db.close()


def test_lookup_jp_font_overlay_negative_vetoes_seed_positive():
    db = _seed_db()
    try:
        db.add_overlay_mapping("ＭＳ ゴシック", None, kind="negative_mapping")
        hits = lookup_jp_font(db, "ＭＳ ゴシック")
        # 用户否决：覆盖层负映射压制种子正映射，只返回"明确无对位"
        assert len(hits) == 1
        assert hits[0]["kind"] == "negative_mapping"
        assert hits[0]["layer"] == "overlay"
    finally:
        db.close()


def test_lookup_jp_font_overlay_positive_overrides_seed_negative():
    db = _seed_db()
    try:
        db.add_overlay_mapping("華康勘亭流", "文泉驿微米黑", confidence=0.99)
        hits = lookup_jp_font(db, "華康勘亭流")
        # 用户人工补充映射：覆盖层正映射压过种子负映射
        assert len(hits) == 1
        assert hits[0]["kind"] == "mapping"
        assert hits[0]["cn_name"] == "文泉驿微米黑"
        assert hits[0]["layer"] == "overlay"
    finally:
        db.close()


def test_lookup_jp_font_default_confidence_when_null():
    db = _seed_db()
    try:
        hits = lookup_jp_font(db, "无置信度映射")
        assert len(hits) == 1
        assert hits[0]["confidence"] == pytest.approx(DEFAULT_MAPPING_CONFIDENCE)
    finally:
        db.close()


# ---------------------------------------------------------------------------
# 反向查询（中→日，同一张表）
# ---------------------------------------------------------------------------

def test_lookup_jp_cn_reverse_helper():
    db = _seed_db()
    try:
        rows = db.lookup_jp_cn_reverse("思源黑体")
        jp_names = {r["jp_name"] for r in rows}
        assert {"ＭＳ ゴシック", "源ノ角ゴシック"} <= jp_names
        assert db.lookup_jp_cn_reverse("不存在的名字") == []
        assert db.lookup_jp_cn_reverse("") == []
    finally:
        db.close()


def test_reverse_lookup_overlay_same_key_shadow():
    db = _seed_db()
    try:
        db.add_overlay_mapping("ＭＳ ゴシック", "思源黑体", confidence=0.5)
        rows = db.lookup_jp_cn_reverse("思源黑体")
        ms_rows = [r for r in rows if r["jp_name"] == "ＭＳ ゴシック"]
        assert len(ms_rows) == 1
        assert ms_rows[0]["layer"] == "overlay"
    finally:
        db.close()


def test_resolve_chain_reverse_direction_target_ja():
    db = _seed_db()
    try:
        result = resolve_font_chain(db, "思源黑体", target_lang="ja")
        assert result.status == STATUS_RESOLVED
        # ＭＳ ゴシック（commercial_paid）不放行，开源的源ノ角ゴシック入选
        assert [c.name for c in result.candidates] == ["源ノ角ゴシック"]
        top = result.candidates[0]
        assert top.basis == "mapping"
        assert top.level == 1
        assert top.license_category == "open_source"
        assert any("ＭＳ ゴシック" in note for note in result.notes)
    finally:
        db.close()


# ---------------------------------------------------------------------------
# open_source_alternates：查库字段优先 + 映射链补充
# ---------------------------------------------------------------------------

def test_open_source_alternates_from_fonts_field_filters_non_open():
    db = _seed_db()
    try:
        alts = open_source_alternates(db, "汉仪雅酷黑")
        names = [a["name"] for a in alts]
        # 思源黑体/Noto（开源）入选；方正兰亭黑（commercial_paid）被剔除
        assert "思源黑体" in names and "Noto Sans CJK" in names
        assert "方正兰亭黑" not in names
        for alt in alts:
            assert alt["basis"] == "open_source"
            assert alt["license_category"] == "open_source"
            assert alt["via"] == "fonts.alternates"
            assert alt["confidence"] == pytest.approx(OPEN_SOURCE_ALTERNATE_CONFIDENCE)
        noto = next(a for a in alts if a["name"] == "Noto Sans CJK")
        # 本机已安装索引（source=local_index）作补充校验标注
        assert noto["installed"] is True
    finally:
        db.close()


def test_open_source_alternates_via_map_chain_without_fonts_row():
    db = _seed_db()
    try:
        # ＭＳ 明朝自身无开源替代字段，但其开源对位经映射表构成替代链一环
        db.import_seed([_map("ＭＳ 明朝", "文泉驿微米黑", confidence=0.7)])
        alts = open_source_alternates(db, "ＭＳ 明朝")
        assert [a["name"] for a in alts] == ["文泉驿微米黑"]
        assert alts[0]["via"] == "jp_cn_font_map"
        assert alts[0]["matched_name"] == "ＭＳ 明朝"
    finally:
        db.close()


def test_open_source_alternates_empty_for_unknown_font():
    db = _seed_db()
    try:
        assert open_source_alternates(db, "不存在的字体") == []
        assert open_source_alternates(db, "") == []
    finally:
        db.close()


def test_open_source_alternates_fonts_field_takes_priority():
    db = _seed_db()
    try:
        # 字段与映射链同时命中同一替代时，去重且查库字段（fonts.alternates）在前
        db.import_seed([_map("汉仪雅酷黑", "Noto Sans CJK", confidence=0.9)])
        alts = open_source_alternates(db, "汉仪雅酷黑")
        noto = [a for a in alts if a["name"] == "Noto Sans CJK"]
        assert len(noto) == 1
        assert noto[0]["via"] == "fonts.alternates"
    finally:
        db.close()


# ---------------------------------------------------------------------------
# resolve_font_chain：三级链
# ---------------------------------------------------------------------------

def test_chain_level1_mapping_resolved():
    db = _seed_db()
    try:
        result = resolve_font_chain(db, "ＭＳ ゴシック", target_lang="zh")
        assert result.status == STATUS_RESOLVED
        assert result.font_name == "ＭＳ ゴシック"
        assert result.target_lang == "zh"
        top = result.candidates[0]
        assert top.name == "思源黑体"
        assert top.basis == "mapping"
        assert top.level == 1
        assert top.layer == "seed"
        assert top.license_category == "open_source"
        assert top.confidence == pytest.approx(0.98)
        assert top.reason
    finally:
        db.close()


def test_chain_level2_open_source_when_counterpart_disallowed():
    db = _seed_db()
    try:
        result = resolve_font_chain(db, "ＭＳ 明朝", target_lang="zh")
        # 对位 汉仪雅酷黑（commercial_paid）不放行 → 沿其替代链走到开源替代
        assert result.status == STATUS_RESOLVED
        names = [c.name for c in result.candidates]
        assert "思源黑体" in names
        assert "方正兰亭黑" not in names
        for cand in result.candidates:
            assert cand.level == 2
            assert cand.basis == "open_source"
            assert cand.license_category == "open_source"
            assert cand.confidence == pytest.approx(OPEN_SOURCE_ALTERNATE_CONFIDENCE)
        assert any("汉仪雅酷黑" in note for note in result.notes)
    finally:
        db.close()


def test_chain_level2_alternates_of_original_after_negative_mapping():
    db = _seed_db()
    try:
        result = resolve_font_chain(db, "DFG勘亭流", target_lang="zh")
        # 第一级明确无对位（负映射）→ 第二级用原字体的开源替代链兜底
        assert result.status == STATUS_RESOLVED
        assert [c.name for c in result.candidates] == ["文泉驿微米黑"]
        top = result.candidates[0]
        assert top.level == 2
        assert top.basis == "open_source"
        assert any("负映射" in note or "无对位" in note for note in result.notes)
    finally:
        db.close()


def test_chain_level3_category_fallback_within_target_language():
    db = _seed_db()
    try:
        result = resolve_font_chain(db, "汉仪正圆", target_lang="zh")
        # 无映射、无替代 → 同风格类别兜底：仅目标语言字体库内检索，开源优先
        assert result.status == STATUS_RESOLVED
        names = [c.name for c in result.candidates]
        assert names == ["资源圆体", "圆体F"]  # 开源在前；commercial_paid 的华康圆体W不入候选
        assert all(c.level == 3 and c.basis == "category_match" for c in result.candidates)
        assert result.candidates[0].license_category == "open_source"
        assert result.candidates[1].license_category == "free_commercial"
        assert result.candidates[0].confidence == pytest.approx(CATEGORY_MATCH_CONFIDENCE)
    finally:
        db.close()


def test_chain_unresolved_when_nothing_found():
    db = _seed_db()
    try:
        result = resolve_font_chain(db, "実験字体X", target_lang="zh")
        # 全链无果：明确 unresolved，绝不编造候选
        assert result.status == STATUS_UNRESOLVED
        assert result.candidates == []
        assert result.notes
        # 未收录字体同样 unresolved
        result2 = resolve_font_chain(db, "不存在的字体", target_lang="zh")
        assert result2.status == STATUS_UNRESOLVED
        assert result2.candidates == []
        # 空字体名：不抛异常，unresolved
        result3 = resolve_font_chain(db, "  ", target_lang="zh")
        assert result3.status == STATUS_UNRESOLVED
    finally:
        db.close()


def test_chain_unknown_target_lang_skips_level1():
    db = _seed_db()
    try:
        result = resolve_font_chain(db, "ＭＳ ゴシック", target_lang="en")
        # 表只覆盖 ja↔zh：第一级跳过（notes 标注），第二级开源替代仍可用
        assert result.status == STATUS_RESOLVED
        assert all(c.level != 1 for c in result.candidates)
        assert result.candidates[0].level == 2
        assert any("跳过第一级" in note for note in result.notes)
    finally:
        db.close()


def test_chain_overlay_mapping_wins_over_seed():
    db = _seed_db()
    try:
        db.add_overlay_mapping("ＭＳ ゴシック", "文泉驿微米黑", confidence=0.88)
        result = resolve_font_chain(db, "ＭＳ ゴシック", target_lang="zh")
        assert result.status == STATUS_RESOLVED
        # 覆盖层候选排在前（同键遮蔽后仅剩覆盖层行 + 种子行按层排序）
        assert result.candidates[0].name == "文泉驿微米黑"
        assert result.candidates[0].layer == "overlay"
        assert result.candidates[1].name == "思源黑体"
        assert result.candidates[1].layer == "seed"
    finally:
        db.close()


def test_chain_result_shapes_and_serialization():
    db = _seed_db()
    try:
        result = resolve_font_chain(db, "ＭＳ ゴシック", target_lang="zh")
        assert isinstance(result, ChainResult)
        assert isinstance(result.candidates[0], ChainCandidate)
        payload = result.to_dict()
        cand = payload["candidates"][0]
        for key in ("name", "basis", "level", "layer", "license_category",
                    "confidence", "reason"):
            assert key in cand
        assert payload["status"] == STATUS_RESOLVED
        assert payload["font_name"] == "ＭＳ ゴシック"
        assert payload["target_lang"] == "zh"
        assert result.resolved is True
        assert result.best is result.candidates[0]
    finally:
        db.close()


def test_chain_default_target_lang_is_zh():
    db = _seed_db()
    try:
        result = resolve_font_chain(db, "ＭＳ ゴシック")
        assert result.target_lang == "zh"
        assert result.status == STATUS_RESOLVED
    finally:
        db.close()


# ---------------------------------------------------------------------------
# 同风格类别映射规则常量表
# ---------------------------------------------------------------------------

def test_style_category_groups_cover_required_pairs():
    # 黑体↔Sans、明朝/宋↔Serif、楷/手写↔Script/Casual、圆体↔Round
    assert category_style_group("黑体") == STYLE_CATEGORY_GROUPS["sans"]
    assert category_style_group("gothic") == STYLE_CATEGORY_GROUPS["sans"]
    assert category_style_group("Serif") == STYLE_CATEGORY_GROUPS["serif"]
    assert category_style_group("明朝") == STYLE_CATEGORY_GROUPS["serif"]
    assert category_style_group("宋体") == STYLE_CATEGORY_GROUPS["serif"]
    assert category_style_group("script") == STYLE_CATEGORY_GROUPS["script"]
    assert category_style_group("楷体") == STYLE_CATEGORY_GROUPS["script"]
    assert category_style_group("handwriting") == STYLE_CATEGORY_GROUPS["script"]
    assert category_style_group("casual") == STYLE_CATEGORY_GROUPS["script"]
    assert category_style_group("圆体") == STYLE_CATEGORY_GROUPS["round"]
    assert category_style_group("Round") == STYLE_CATEGORY_GROUPS["round"]


def test_style_category_groups_tokens_disjoint_and_normalized():
    seen: dict[str, str] = {}
    for group_name, tokens in STYLE_CATEGORY_GROUPS.items():
        assert isinstance(tokens, tuple) and tokens
        for token in tokens:
            low = token.lower()
            assert low not in seen, f"类别词 {token!r} 同时属于 {seen[low]} 与 {group_name}"
            seen[low] = group_name
    assert category_style_group(None) is None
    assert category_style_group("  ") is None
    assert category_style_group("experimental") is None


def test_allowed_license_categories_constant():
    # 匹配链放行口径：开源 + 免费商用；unknown 从严不放行
    assert set(ALLOWED_LICENSE_CATEGORIES) == {"open_source", "free_commercial"}


# ---------------------------------------------------------------------------
# fonts_db 最小扩展：schema v3 alternates 列 + list_fonts
# ---------------------------------------------------------------------------

def test_schema_v3_adds_alternates_column(tmp_path):
    db = FontsDB(path=str(tmp_path / "fonts.db"))
    try:
        assert db.get_schema_version() == MIGRATIONS[-1][0]
        columns = {r[1] for r in db._conn.execute("PRAGMA table_info(fonts)")}
        assert "alternates" in columns
    finally:
        db.close()


def test_alternates_seed_and_overlay_roundtrip(tmp_path):
    db = FontsDB(path=str(tmp_path / "fonts.db"))
    try:
        db.import_seed([_font("思源黑体", license_category="open_source",
                             alternates=["文泉驿微米黑", "文泉驿微米黑", " "])])
        stored = json.loads(db.lookup_font("思源黑体")["alternates"])
        assert stored == ["文泉驿微米黑"]  # 规范化为去重去空的 JSON 数组文本
        # 覆盖层替换 alternates；未覆盖（None）时回落种子值
        db.upsert_overlay_font("思源黑体", license_category="open_source",
                               alternates=["Noto Sans CJK"])
        assert json.loads(db.lookup_font("思源黑体")["alternates"]) == ["Noto Sans CJK"]
        db.upsert_overlay_font("思源黑体", license_category="open_source")
        assert json.loads(db.lookup_font("思源黑体")["alternates"]) == ["文泉驿微米黑"]
        # 非法类型拒绝
        with pytest.raises(ValueError):
            db.import_seed([_font("X", alternates=123)])
    finally:
        db.close()


def test_list_fonts_merges_overlay_over_seed():
    db = _seed_db()
    try:
        db.upsert_overlay_font("ＭＳ ゴシック", category="rounded",
                               aliases=["MS Gothic UI"])
        fonts = db.list_fonts()
        names = [f["canonical_name"] for f in fonts]
        assert names == sorted(names)  # 结果确定（按规范名排序）
        assert len(names) == len(set(names))  # 同名 seed/overlay 合并为一行
        ms = next(f for f in fonts if f["canonical_name"] == "ＭＳ ゴシック")
        assert ms["layer"] == "overlay"
        assert ms["category"] == "rounded"
        assert ms["languages"] == "ja,en"  # 覆盖层 NULL 字段回落种子
        assert "MS Gothic UI" in ms["aliases"]
        assert all("license_category" in f for f in fonts)
    finally:
        db.close()
