# tests/test_font_closure.py
"""T3.4 端到端闭环：识别侧表 → 合规决策 → 逐事件 \\fn 落名 + 报告 + 更新建议包。

覆盖（按任务书）：
1. :func:`font_intel.closure.merge_fn_tag` 纯函数——块内合并顺序（与
   ``\\pos``/旋转同块，参考 ``merge_restoration_tags`` 的块内合并思路）、
   无块防御包裹、字体名清洗。
2. 合规四动作的事件级 \\fn 落名：allow 写识别名 / replace_auto 写替代链
   首个候选 / prompt 非交互降级保留样式字体 + 记录 / report_only 仅记录。
3. 翻译联动：mock ``resolve_font_chain`` 三种返回（对位命中 / 开源替代 /
   unresolved）的落名与报告差异；unknown/无识别保留现状样式。
4. 关闭态逐字节一致（font_identifications=None、合规关闭、组合态）。
5. 更新建议包：schema、生成触发（unknown 字体 / 低置信映射 / 同类形兜底）、
   导出→再导入→应用 round-trip、llm_inferred 不参与 replace_auto 的
   decide() 层防御、对话框采纳 → db_loader 写覆盖层。
6. 接线缝：PipelineWorker 合规配置构造、CLI ``--font-identify`` 参数面。

识别侧表全部直接构造 ``FontIdentifyResult``（不依赖 torch）。
"""

from __future__ import annotations

import inspect
import json
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

# QApplication 必须先于 conftest 的 QCoreApplication 创建（QWidget 需要，
# 与 test_font_map_review_dialog.py 同法：模块级创建，后到者复用）。
from PySide6.QtWidgets import QApplication  # noqa: E402

_app = QApplication.instance() or QApplication(sys.argv[:1])

from core.subtitle_generator.generator import OCRToASSOptimizer  # noqa: E402
from font_intel.fonts_db import FontsDB  # noqa: E402
from font_intel.identify_stage import (  # noqa: E402
    ENTRY_OK,
    FontIdentifyResult,
    STATUS_OK,
)

DISCLAIMER = "本报告不构成法律意见，商用前请自行核实授权条款"
SUGGESTIONS_SUFFIX = "_font_update_suggestions.json"


# ── 构造辅助 ─────────────────────────────────────────────────────


def _make_optimizer(tmp_path, **kw) -> OCRToASSOptimizer:
    return OCRToASSOptimizer(
        video_path=str(tmp_path / "in.mp4"),
        output_path=str(tmp_path / "out.ass"),
        fps=25.0,
        width=1280,
        height=720,
        **kw,
    )


def _make_item(frame_num, text, box, roi="roi_0"):
    poly = [[box[0], box[1]], [box[2], box[1]], [box[2], box[3]], [box[0], box[3]]]
    data = {
        "dt_polys": [poly],
        "rec_polys": [poly],
        "rec_texts": [text],
        "rec_scores": [0.95],
        "rec_boxes": [list(box)],
    }
    return (data, frame_num, roi, frame_num / 25.0)


def _items(text="学成归来", roi="roi_0"):
    # 底部区域 → BOTTOM 组，事件 body 即 OCR 原文（可与识别侧表精确对上）。
    return [_make_item(f, text, (100, 600, 500, 650), roi=roi)
            for f in range(10, 21)]


def _font(name, score=0.92, **kw) -> dict:
    d = {
        "name": name,
        "score": score,
        "model_score": score,
        "glyph_score": None,
        "glyph_ranked": False,
        "source": "test",
        "font_path": None,
        "license_category": None,
        "low_confidence": False,
    }
    d.update(kw)
    return d


def _entry(text, fonts, roi="roi_0", status=ENTRY_OK, frame=10) -> dict:
    return {
        "text": text,
        "roi_id": roi,
        "frame_num": frame,
        "box": (100, 600, 500, 650),
        "frame_time_sec": frame / 25.0,
        "status": status,
        "identified_fonts": fonts,
    }


def _ident_result(entries: dict) -> FontIdentifyResult:
    result = FontIdentifyResult(
        enabled=True, status=STATUS_OK,
        groups_total=len(entries), groups_sampled=len(entries))
    result.identifications.update(entries)
    return result


def _seed_db_file(path, extra_fonts=()) -> str:
    """种子库：思源黑体 CN=open_source，Malgun Gothic=commercial_paid。"""
    db = FontsDB(str(path))
    payload = [
        {"table": "fonts", "canonical_name": "思源黑体 CN",
         "license_category": "open_source", "category": "黑体",
         "languages": "zh",
         "official_url": "https://github.com/adobe-fonts/source-han-sans",
         "source": "test-seed"},
        {"table": "fonts", "canonical_name": "Malgun Gothic",
         "license_category": "commercial_paid",
         "official_url": "https://www.fonts.go.kr/malgun",
         "source": "test-seed"},
    ]
    payload.extend(extra_fonts)
    db.import_seed(payload)
    db.close()
    return str(path)


def _replace_auto_config(db_path: str, confidence=0.92, alternatives=None):
    from font_intel.compliance import ComplianceRules
    from font_intel.integration import FontComplianceConfig

    rules = ComplianceRules()
    rules.set_override("commercial_paid", "personal", "replace_auto")
    return FontComplianceConfig(
        enabled=True,
        scene="personal",
        rules=rules,
        db_path=db_path,
        confidence=confidence,
        alternatives_provider=(
            (lambda name: list(alternatives)) if alternatives is not None
            else None),
    )


def _simple_ident(font_name="Malgun Gothic", score=0.92, text="学成归来",
                  roi="roi_0", **font_kw) -> FontIdentifyResult:
    return _ident_result({
        (roi, 10, 0): _entry(text, [_font(font_name, score, **font_kw)], roi=roi),
    })


def _no_report_files(tmp_path):
    return sorted(p.name for p in tmp_path.glob("*")
                  if "_compliance_report." in p.name)


def _suggestions_files(tmp_path):
    return sorted(p.name for p in tmp_path.glob(f"*{SUGGESTIONS_SUFFIX}"))


# ── 1. merge_fn_tag 纯函数 ───────────────────────────────────────


class TestMergeFnTag:
    def test_appends_inside_existing_block(self):
        from font_intel.closure import merge_fn_tag

        assert merge_fn_tag("{\\an8}", "FontA") == "{\\an8\\fnFontA}"

    def test_merges_with_pos_and_rotation_block(self):
        from font_intel.closure import merge_fn_tag

        # \fn 并入既有块内部（与 \pos/旋转标签同块、块尾追加），不产生
        # 第二个块、不落在块外（块外会被 libass 当字面文本渲染）。
        assert merge_fn_tag("{\\an5\\pos(10,20)\\frz8.0}", "FontB") == \
            "{\\an5\\pos(10,20)\\frz8.0\\fnFontB}"

    def test_wraps_standalone_block_when_no_tags(self):
        from font_intel.closure import merge_fn_tag

        assert merge_fn_tag("", "FontC") == "{\\fnFontC}"
        assert merge_fn_tag(None, "FontC") == "{\\fnFontC}"

    def test_sanitizes_braces_and_newlines_in_font_name(self):
        from font_intel.closure import merge_fn_tag

        assert merge_fn_tag("", "F}x{y") == "{\\fnFxy}"
        assert merge_fn_tag("", "F\nx") == "{\\fnFx}"

    def test_empty_name_is_noop(self):
        from font_intel.closure import merge_fn_tag

        assert merge_fn_tag("{\\an8}", "") == "{\\an8}"
        assert merge_fn_tag("{\\an8}", "  ") == "{\\an8}"

    def test_does_not_mutate_input(self):
        from font_intel.closure import merge_fn_tag

        tags = "{\\an8}"
        merge_fn_tag(tags, "F")
        assert tags == "{\\an8}"


# ── 2. 合规四动作的事件级 \fn 落名 ───────────────────────────────


class TestEventClosureFourActions:
    def test_allow_writes_identified_font(self, tmp_path):
        db_path = _seed_db_file(tmp_path / "fonts.db")
        conv = _make_optimizer(
            tmp_path,
            font_compliance=_replace_auto_config(db_path),
            font_identifications=_simple_ident("思源黑体 CN"),
        )
        conv.convert_from_memory(iter(_items()))
        text = (tmp_path / "out.ass").read_text(encoding="utf-8-sig")
        # 开源放行：识别字体名即设即用（逐事件 \fn）。
        assert "\\fn思源黑体 CN" in text
        payload = json.loads(
            (tmp_path / "out_compliance_report.json").read_text(
                encoding="utf-8"))
        event_decisions = [d for d in payload["decisions"]
                           if d.get("scope") == "event"]
        assert event_decisions
        rec = event_decisions[0]
        assert rec["action"] == "allow"
        assert rec["font_name"] == "思源黑体 CN"
        assert rec["final_font"] == "思源黑体 CN"
        assert rec["events"] >= 1
        assert DISCLAIMER in (tmp_path / "out_compliance_report.md").read_text(
            encoding="utf-8")

    def test_replace_auto_writes_first_alternative(self, tmp_path):
        db_path = _seed_db_file(tmp_path / "fonts.db")
        conv = _make_optimizer(
            tmp_path,
            font_compliance=_replace_auto_config(
                db_path, confidence=0.92, alternatives=["思源黑体 CN"]),
            font_identifications=_simple_ident("Malgun Gothic"),
        )
        conv.convert_from_memory(iter(_items()))
        text = (tmp_path / "out.ass").read_text(encoding="utf-8-sig")
        assert "\\fn思源黑体 CN" in text
        payload = json.loads(
            (tmp_path / "out_compliance_report.json").read_text(
                encoding="utf-8"))
        rec = next(d for d in payload["decisions"]
                   if d.get("scope") == "event")
        assert rec["action"] == "replace_auto"
        assert rec["final_font"] == "思源黑体 CN"
        assert rec["replaced"] is True

    def test_replace_auto_low_confidence_degrades_to_style_font(
            self, tmp_path):
        db_path = _seed_db_file(tmp_path / "fonts.db")
        conv = _make_optimizer(
            tmp_path,
            font_compliance=_replace_auto_config(
                db_path, confidence=0.92, alternatives=["思源黑体 CN"]),
            # 识别阶段已标记 low_confidence → 不参与自动替换。
            font_identifications=_simple_ident(
                "Malgun Gothic", low_confidence=True),
        )
        conv.convert_from_memory(iter(_items()))
        text = (tmp_path / "out.ass").read_text(encoding="utf-8-sig")
        assert "\\fn" not in text
        rec = next(
            d for d in json.loads(
                (tmp_path / "out_compliance_report.json").read_text(
                    encoding="utf-8"))["decisions"]
            if d.get("scope") == "event")
        assert rec["action"] == "prompt"
        assert rec["final_font"] == "Malgun Gothic"

    def test_prompt_non_interactive_keeps_style_font_and_records(
            self, tmp_path):
        from font_intel.integration import FontComplianceConfig

        db_path = _seed_db_file(tmp_path / "fonts.db")
        conv = _make_optimizer(
            tmp_path,
            font_compliance=FontComplianceConfig(
                enabled=True, db_path=db_path),
            font_identifications=_simple_ident("Malgun Gothic"),
        )
        conv.convert_from_memory(iter(_items()))
        text = (tmp_path / "out.ass").read_text(encoding="utf-8-sig")
        # 非交互 prompt：保留样式字体（不写 \fn）+ 记录决策。
        assert "\\fn" not in text
        rec = next(
            d for d in json.loads(
                (tmp_path / "out_compliance_report.json").read_text(
                    encoding="utf-8"))["decisions"]
            if d.get("scope") == "event")
        assert rec["action"] == "prompt"
        assert "非交互" in rec["reason"]
        assert rec["official_url"] == "https://www.fonts.go.kr/malgun"

    def test_report_only_keeps_style_font(self, tmp_path):
        from font_intel.compliance import ComplianceRules
        from font_intel.integration import FontComplianceConfig

        db_path = _seed_db_file(tmp_path / "fonts.db")
        rules = ComplianceRules(strict_unknown=False)
        conv = _make_optimizer(
            tmp_path,
            font_compliance=FontComplianceConfig(
                enabled=True, db_path=db_path, rules=rules),
            # 识别字体不在库 → unknown → strict_unknown=False 放宽为
            # report_only：仅记录，不改输出。
            font_identifications=_simple_ident("Mystery Font"),
        )
        conv.convert_from_memory(iter(_items()))
        text = (tmp_path / "out.ass").read_text(encoding="utf-8-sig")
        assert "\\fn" not in text
        rec = next(
            d for d in json.loads(
                (tmp_path / "out_compliance_report.json").read_text(
                    encoding="utf-8"))["decisions"]
            if d.get("scope") == "event")
        assert rec["action"] == "report_only"

    def test_no_identification_keeps_style_font_and_no_decisions(
            self, tmp_path):
        db_path = _seed_db_file(tmp_path / "fonts.db")
        # 识别组存在但没有候选（status ok、候选列表空）→ 保留现状样式。
        conv = _make_optimizer(
            tmp_path,
            font_compliance=_replace_auto_config(db_path),
            font_identifications=_ident_result({
                ("roi_0", 10, 0): _entry("学成归来", [], status=ENTRY_OK),
            }),
        )
        conv.convert_from_memory(iter(_items()))
        text = (tmp_path / "out.ass").read_text(encoding="utf-8-sig")
        assert "\\fn" not in text
        payload = json.loads(
            (tmp_path / "out_compliance_report.json").read_text(
                encoding="utf-8"))
        assert not [d for d in payload["decisions"]
                    if d.get("scope") == "event"]

    def test_default_chain_backed_alternatives_provider(self, tmp_path):
        """未显式提供 alternatives_provider 时，replace_auto 沿三级链取
        首个候选（默认中文目标链）。"""
        db_path = _seed_db_file(tmp_path / "fonts.db")
        # 种子替代链：Malgun Gothic.alternates → 思源黑体 CN（开源）。
        db = FontsDB(db_path)
        db.upsert_overlay_font(
            "Malgun Gothic", license_category="commercial_paid",
            alternates=["思源黑体 CN"], source="test-overlay")
        db.close()
        rules = None
        from font_intel.compliance import ComplianceRules
        from font_intel.integration import FontComplianceConfig
        rules = ComplianceRules()
        rules.set_override("commercial_paid", "personal", "replace_auto")
        conv = _make_optimizer(
            tmp_path,
            font_compliance=FontComplianceConfig(
                enabled=True, db_path=db_path, rules=rules, confidence=0.92),
            font_identifications=_simple_ident("Malgun Gothic"),
        )
        conv.convert_from_memory(iter(_items()))
        text = (tmp_path / "out.ass").read_text(encoding="utf-8-sig")
        assert "\\fn思源黑体 CN" in text

    def test_scene_lines_get_fn_inside_existing_pos_block(self, tmp_path):
        """SCENE 行已有 {\\an5\\pos(...)} 块：\\fn 必须并入块内。"""
        from font_intel.integration import FontComplianceConfig

        db_path = _seed_db_file(tmp_path / "fonts.db")
        conv = _make_optimizer(
            tmp_path,
            font_compliance=FontComplianceConfig(
                enabled=True, db_path=db_path),
            font_identifications=_simple_ident(
                "思源黑体 CN", text="画面中央字"),
        )
        # SCENE 区域（y 居中）单行 → 事件带 \pos 块。
        items = [_make_item(f, "画面中央字", (100, 300, 500, 340))
                 for f in range(10, 21)]
        conv.convert_from_memory(iter(items))
        text = (tmp_path / "out.ass").read_text(encoding="utf-8-sig")
        line = next(l for l in text.splitlines()
                    if l.startswith("Dialogue:") and "画面中央字" in l)
        assert "\\fn思源黑体 CN" in line
        block = line[line.index("{"):line.index("}") + 1]
        assert block.endswith("\\fn思源黑体 CN}")


# ── 3. 翻译联动字体映射（mock resolve_font_chain 三态） ──────────


def _chain_candidate(name, basis, level=1, conf=0.8):
    from font_intel.matching import ChainCandidate

    return ChainCandidate(
        name=name, basis=basis, level=level, layer="seed",
        license_category="open_source", confidence=conf,
        reason=f"测试候选（{basis}）")


def _patch_chain(monkeypatch, result):
    import font_intel.closure as closure

    calls: list = []

    def fake_resolve(db, font_name, target_lang="zh"):
        calls.append((font_name, target_lang))
        return result

    monkeypatch.setattr(closure, "resolve_font_chain", fake_resolve)
    return calls


class TestTranslationLinkedMapping:
    def _run(self, tmp_path, ident, monkeypatch, chain_result):
        from font_intel.integration import FontComplianceConfig
        from font_intel.translation import TranslationConfig

        calls = _patch_chain(monkeypatch, chain_result)
        db_path = _seed_db_file(tmp_path / "fonts.db")
        conv = _make_optimizer(
            tmp_path,
            font_compliance=FontComplianceConfig(
                enabled=True, db_path=db_path),
            font_identifications=ident,
            translation_config=TranslationConfig(
                target_language="简体中文",
                cloud_api_key="", cloud_base_url="", sakura_base_url=""),
            roi_ocr_langs={"roi_0": "japan"},
        )
        conv.convert_from_memory(iter(_items()))
        text = (tmp_path / "out.ass").read_text(encoding="utf-8-sig")
        report = json.loads(
            (tmp_path / "out_compliance_report.json").read_text(
                encoding="utf-8"))
        recs = [d for d in report["decisions"] if d.get("scope") == "event"]
        return text, recs, calls

    def test_mapping_hit_writes_candidate_and_records_basis(
            self, tmp_path, monkeypatch):
        from font_intel.matching import STATUS_RESOLVED, ChainResult

        ident = _simple_ident("源ノ角ゴシック JP")
        result = ChainResult(
            STATUS_RESOLVED, "源ノ角ゴシック JP", "zh",
            [_chain_candidate("思源黑体 CN", "mapping")], [])
        text, recs, calls = self._run(tmp_path, ident, monkeypatch, result)
        assert calls == [("源ノ角ゴシック JP", "zh")]
        assert "\\fn思源黑体 CN" in text
        assert recs
        assert recs[0]["action"] == "replace_auto"
        assert recs[0]["mapping_basis"] == "mapping"
        assert recs[0]["target_lang"] == "zh"
        assert recs[0]["final_font"] == "思源黑体 CN"

    def test_open_source_alternate_basis_recorded(self, tmp_path, monkeypatch):
        from font_intel.matching import STATUS_RESOLVED, ChainResult

        ident = _simple_ident("商業フォント")
        result = ChainResult(
            STATUS_RESOLVED, "商業フォント", "zh",
            [_chain_candidate("思源黑体 CN", "open_source", level=2)],
            [])
        text, recs, _calls = self._run(tmp_path, ident, monkeypatch, result)
        assert "\\fn思源黑体 CN" in text
        assert recs[0]["mapping_basis"] == "open_source"

    def test_unresolved_keeps_original_name_and_marks_unmapped(
            self, tmp_path, monkeypatch):
        from font_intel.matching import STATUS_UNRESOLVED, ChainResult

        ident = _simple_ident("謎フォント")
        result = ChainResult(
            STATUS_UNRESOLVED, "謎フォント", "zh", [],
            ["映射表中无 謎フォント 的记录。"])
        text, recs, _calls = self._run(tmp_path, ident, monkeypatch, result)
        # 保留原字体名引用 + 报告标注未映射，不强行替换。
        assert "\\fn謎フォント" in text
        assert recs[0]["final_font"] == "謎フォント"
        assert recs[0]["mapping_basis"] == "unresolved"
        assert recs[0]["unmapped"] is True
        assert "未映射" in (tmp_path / "out_compliance_report.md").read_text(
            encoding="utf-8")

    def test_unknown_identification_keeps_style_font(self, tmp_path,
                                                     monkeypatch):
        from font_intel.integration import FontComplianceConfig
        from font_intel.translation import TranslationConfig

        _patch_chain(monkeypatch, None)  # 不应被调用
        db_path = _seed_db_file(tmp_path / "fonts.db")
        conv = _make_optimizer(
            tmp_path,
            font_compliance=FontComplianceConfig(
                enabled=True, db_path=db_path),
            # 识别字体为 unknown（无候选）→ 保留现状样式、不查链。
            font_identifications=_ident_result({
                ("roi_0", 10, 0): _entry("学成归来", []),
            }),
            translation_config=TranslationConfig(
                target_language="简体中文",
                cloud_api_key="", cloud_base_url="", sakura_base_url=""),
        )
        conv.convert_from_memory(iter(_items()))
        text = (tmp_path / "out.ass").read_text(encoding="utf-8-sig")
        assert "\\fn" not in text


# ── 4. 关闭态逐字节一致 ─────────────────────────────────────────


class TestOffStateByteIdentical:
    def _baseline(self, tmp_path, **kw):
        baseline_dir = tmp_path / "baseline"
        baseline_dir.mkdir()
        base = OCRToASSOptimizer(
            video_path=str(baseline_dir / "in.mp4"),
            output_path=str(baseline_dir / "out.ass"),
            fps=25.0, width=1280, height=720, **kw)
        base.convert_from_memory(iter(_items()))
        return baseline_dir

    def test_identifications_without_compliance_is_byte_identical(
            self, tmp_path):
        baseline_dir = self._baseline(tmp_path)
        conv = _make_optimizer(
            tmp_path, font_identifications=_simple_ident("Malgun Gothic"))
        conv.convert_from_memory(iter(_items()))
        assert (tmp_path / "out.ass").read_bytes() == \
            (baseline_dir / "out.ass").read_bytes()
        assert _no_report_files(tmp_path) == []
        assert _suggestions_files(tmp_path) == []

    def test_none_idents_none_translation_is_byte_identical(self, tmp_path):
        baseline_dir = self._baseline(tmp_path)
        conv = _make_optimizer(
            tmp_path, font_identifications=None, translation_config=None)
        conv.convert_from_memory(iter(_items()))
        assert (tmp_path / "out.ass").read_bytes() == \
            (baseline_dir / "out.ass").read_bytes()
        assert _no_report_files(tmp_path) == []

    def test_identifications_with_translation_but_no_compliance_translates_only(
            self, tmp_path, monkeypatch):
        from font_intel.translation import TranslationConfig
        import font_intel.translation as tr

        baseline_dir = self._baseline(tmp_path)
        # 基线同样开翻译（对照组：只翻译、无识别无合规）。
        monkeypatch.setattr(
            tr, "translate_subtitle_texts",
            lambda bodies, cfg, **kw: type(
                "R", (), {"texts": [f"译[{b}]" for b in bodies],
                          "stats": None})())
        base = OCRToASSOptimizer(
            video_path=str(baseline_dir / "in.mp4"),
            output_path=str(baseline_dir / "out.ass"),
            fps=25.0, width=1280, height=720,
            translation_config=TranslationConfig(
                target_language="简体中文",
                cloud_api_key="", cloud_base_url="", sakura_base_url=""),
            roi_ocr_langs={"roi_0": "japan"})
        base.convert_from_memory(iter(_items()))

        conv = _make_optimizer(
            tmp_path,
            font_identifications=_simple_ident("Malgun Gothic"),
            translation_config=TranslationConfig(
                target_language="简体中文",
                cloud_api_key="", cloud_base_url="", sakura_base_url=""),
            roi_ocr_langs={"roi_0": "japan"})
        conv.convert_from_memory(iter(_items()))
        # 有识别侧表但合规关闭：输出与"仅翻译"运行逐字节一致（无 \fn）。
        assert (tmp_path / "out.ass").read_bytes() == \
            (baseline_dir / "out.ass").read_bytes()
        assert _no_report_files(tmp_path) == []

    def test_disabled_compliance_config_is_byte_identical(self, tmp_path):
        from font_intel.integration import FontComplianceConfig

        baseline_dir = self._baseline(tmp_path)
        conv = _make_optimizer(
            tmp_path,
            font_compliance=FontComplianceConfig(enabled=False),
            font_identifications=_simple_ident("Malgun Gothic"))
        conv.convert_from_memory(iter(_items()))
        assert (tmp_path / "out.ass").read_bytes() == \
            (baseline_dir / "out.ass").read_bytes()
        assert _suggestions_files(tmp_path) == []


# ── 5. 缺口更新建议包 ───────────────────────────────────────────


class TestUpdateSuggestionsPackage:
    def test_unknown_font_generates_new_font_record(self, tmp_path):
        from font_intel.integration import FontComplianceConfig

        db_path = _seed_db_file(tmp_path / "fonts.db")
        conv = _make_optimizer(
            tmp_path,
            font_compliance=FontComplianceConfig(
                enabled=True, db_path=db_path),
            font_identifications=_simple_ident(
                "Mystery Font", glyph_ranked=True),
        )
        conv.convert_from_memory(iter(_items()))
        files = _suggestions_files(tmp_path)
        assert files == ["out" + SUGGESTIONS_SUFFIX]
        package = json.loads(
            (tmp_path / files[0]).read_text(encoding="utf-8"))
        assert package["schema"] == "vso_font_update_suggestions/1"
        assert package["stats"]["items"] >= 1
        item = next(i for i in package["items"]
                    if i["type"] == "new_font_record")
        assert item["font_name"] == "Mystery Font"
        assert item["method"] == "glyph_rerank"
        assert item["suggestion"]["kind"] == "font"
        assert item["suggestion"]["canonical_name"] == "Mystery Font"

    def test_unknown_font_without_glyph_rank_uses_llm_inferred(
            self, tmp_path):
        from font_intel.integration import FontComplianceConfig

        db_path = _seed_db_file(tmp_path / "fonts.db")
        conv = _make_optimizer(
            tmp_path,
            font_compliance=FontComplianceConfig(
                enabled=True, db_path=db_path),
            font_identifications=_simple_ident("Mystery Font"),
        )
        conv.convert_from_memory(iter(_items()))
        package = json.loads(
            (tmp_path / ("out" + SUGGESTIONS_SUFFIX)).read_text(
                encoding="utf-8"))
        item = next(i for i in package["items"]
                    if i["type"] == "new_font_record")
        assert item["method"] == "llm_inferred"

    def test_no_unknown_font_no_suggestion_file(self, tmp_path):
        from font_intel.integration import FontComplianceConfig

        db_path = _seed_db_file(tmp_path / "fonts.db")
        conv = _make_optimizer(
            tmp_path,
            font_compliance=FontComplianceConfig(
                enabled=True, db_path=db_path),
            font_identifications=_simple_ident("思源黑体 CN"),
        )
        conv.convert_from_memory(iter(_items()))
        assert _suggestions_files(tmp_path) == []

    def test_category_match_fallback_suggests_new_mapping(
            self, tmp_path, monkeypatch):
        from font_intel.matching import STATUS_RESOLVED, ChainResult

        ident = _simple_ident("商業フォント")
        result = ChainResult(
            STATUS_RESOLVED, "商業フォント", "zh",
            [_chain_candidate("思源黑体 CN", "category_match", level=3,
                              conf=0.4)], [])
        self._run_translation(tmp_path, ident, monkeypatch, result)
        package = json.loads(
            (tmp_path / ("out" + SUGGESTIONS_SUFFIX)).read_text(
                encoding="utf-8"))
        item = next(i for i in package["items"]
                    if i["type"] == "new_mapping")
        assert item["font_name"] == "商業フォント"
        assert item["candidate"] == "思源黑体 CN"
        assert item["suggestion"] == {
            "kind": "mapping", "jp_name": "商業フォント",
            "cn_name": "思源黑体 CN"}

    def test_low_confidence_mapping_suggests_correction(
            self, tmp_path, monkeypatch):
        from font_intel.matching import STATUS_RESOLVED, ChainResult

        ident = _simple_ident("商業フォント")
        result = ChainResult(
            STATUS_RESOLVED, "商業フォント", "zh",
            [_chain_candidate("思源黑体 CN", "mapping", level=1, conf=0.5)],
            [])
        self._run_translation(tmp_path, ident, monkeypatch, result)
        package = json.loads(
            (tmp_path / ("out" + SUGGESTIONS_SUFFIX)).read_text(
                encoding="utf-8"))
        item = next(i for i in package["items"]
                    if i["type"] == "mapping_correction")
        assert item["suggestion"]["cn_name"] == "思源黑体 CN"
        assert item["confidence"] == pytest.approx(0.5)

    def _run_translation(self, tmp_path, ident, monkeypatch, chain_result):
        from font_intel.integration import FontComplianceConfig
        from font_intel.translation import TranslationConfig

        _patch_chain(monkeypatch, chain_result)
        db_path = _seed_db_file(tmp_path / "fonts.db")
        conv = _make_optimizer(
            tmp_path,
            font_compliance=FontComplianceConfig(
                enabled=True, db_path=db_path),
            font_identifications=ident,
            translation_config=TranslationConfig(
                target_language="简体中文",
                cloud_api_key="", cloud_base_url="", sakura_base_url=""),
            roi_ocr_langs={"roi_0": "japan"},
        )
        conv.convert_from_memory(iter(_items()))


class TestSuggestionRoundTripAndDefense:
    def _package(self, tmp_path):
        from font_intel.closure import (
            build_update_suggestions_package,
            write_update_suggestions_package,
        )

        items = [
            {"type": "new_font_record", "font_name": "Mystery Font",
             "candidate": None, "reason": "未收录", "evidence": "identify",
             "method": "glyph_rerank", "confidence": 0.9,
             "suggestion": {"kind": "font",
                            "canonical_name": "Mystery Font",
                            "license_category": "unknown"}},
            {"type": "new_mapping", "font_name": "商業フォント",
             "candidate": "思源黑体 CN", "reason": "同类形兜底",
             "evidence": "chain level 3", "method": "llm_inferred",
             "confidence": 0.4,
             "suggestion": {"kind": "mapping", "jp_name": "商業フォント",
                            "cn_name": "思源黑体 CN"}},
        ]
        package = build_update_suggestions_package(items)
        path = tmp_path / "sug.json"
        write_update_suggestions_package(package, path)
        return package, path

    def test_write_read_round_trip_schema_checked(self, tmp_path):
        from font_intel.closure import read_update_suggestions_package

        package, path = self._package(tmp_path)
        loaded = read_update_suggestions_package(path)
        assert loaded["schema"] == package["schema"]
        assert loaded["items"] == package["items"]
        assert loaded["stats"]["actionable"] == 2

    def test_read_rejects_wrong_schema(self, tmp_path):
        from font_intel.closure import read_update_suggestions_package

        bad = tmp_path / "bad.json"
        bad.write_text(json.dumps({"schema": "other/1", "items": []}),
                       encoding="utf-8")
        with pytest.raises(ValueError):
            read_update_suggestions_package(bad)

    def test_apply_adopted_suggestions_writes_overlay_human(self, tmp_path):
        from font_intel.closure import read_update_suggestions_package
        from font_intel.etl.db_loader import apply_font_update_decisions

        _package, path = self._package(tmp_path)
        package = read_update_suggestions_package(path)
        db_path = str(tmp_path / "fonts.db")
        db = FontsDB(db_path)
        try:
            stats = apply_font_update_decisions(
                db, {"adopted": package["items"], "rejected": []})
            assert stats["written"] == 2
            # 字体记录落覆盖层。
            info = db.lookup_font("Mystery Font")
            assert info is not None
            assert info["license_category"] == "unknown"
            # 映射落覆盖层且 method=human（人工确认语义）、confidence=1.0。
            rows = db.lookup_jp_cn("商業フォント")
            assert any(r["cn_name"] == "思源黑体 CN" and r["method"] == "human"
                       and r["confidence"] == 1.0 for r in rows)
        finally:
            db.close()

    def test_llm_inferred_candidates_never_drive_replace_auto(
            self, tmp_path):
        """§5.4 防御：候选的证据仅来自 llm_inferred 覆盖层映射行时，
        decide() 不得对其 replace_auto。"""
        from font_intel.compliance import ComplianceRules, decide

        db_path = str(tmp_path / "fonts.db")
        db = FontsDB(db_path)
        try:
            db.import_seed([
                {"table": "fonts", "canonical_name": "TargetCN",
                 "license_category": "open_source", "source": "test-seed"},
            ])
            # 覆盖层 llm_inferred 推断行（未经人工确认）。
            db.add_overlay_mapping(
                "SomeJP", "TargetCN", kind="mapping",
                method="llm_inferred", confidence=0.9, source="test")
            rules = ComplianceRules()
            rules.set_override("unknown", "personal", "replace_auto")
            decision = decide(
                "SomeJP", scene="personal", db=db, confidence=0.95,
                rules=rules, alternatives_provider=lambda n: ["TargetCN"])
            assert decision.action == "prompt"
            assert decision.alternatives == []
        finally:
            db.close()

    def test_human_confirmed_candidates_still_replace(self, tmp_path):
        from font_intel.compliance import ComplianceRules, decide

        db_path = str(tmp_path / "fonts.db")
        db = FontsDB(db_path)
        try:
            db.import_seed([
                {"table": "fonts", "canonical_name": "TargetCN",
                 "license_category": "open_source", "source": "test-seed"},
            ])
            db.add_overlay_mapping(
                "SomeJP", "TargetCN", kind="mapping",
                method="human", confidence=1.0, source="test")
            rules = ComplianceRules()
            rules.set_override("unknown", "personal", "replace_auto")
            decision = decide(
                "SomeJP", scene="personal", db=db, confidence=0.95,
                rules=rules, alternatives_provider=lambda n: ["TargetCN"])
            assert decision.action == "replace_auto"
            assert decision.alternatives == ["TargetCN"]
        finally:
            db.close()

    def test_candidates_without_mapping_trace_are_not_blocked(self, tmp_path):
        """无任何映射行痕迹的候选（如 fonts.alternates 字段链/用户自带
        provider）不受 llm_inferred 防御拦截——防御只针对推断行。"""
        from font_intel.compliance import ComplianceRules, decide

        db_path = str(tmp_path / "fonts.db")
        db = FontsDB(db_path)
        try:
            db.import_seed([
                {"table": "fonts", "canonical_name": "TargetCN",
                 "license_category": "open_source", "source": "test-seed"},
            ])
            rules = ComplianceRules()
            rules.set_override("unknown", "personal", "replace_auto")
            decision = decide(
                "SomeJP", scene="personal", db=db, confidence=0.95,
                rules=rules, alternatives_provider=lambda n: ["TargetCN"])
            assert decision.action == "replace_auto"
            assert decision.alternatives == ["TargetCN"]
        finally:
            db.close()


# ── 6. 对话框（offscreen） ──────────────────────────────────────


class TestFontUpdateReviewDialog:
    def _items(self):
        return [
            {"type": "new_font_record", "font_name": "Mystery Font",
             "candidate": None, "reason": "未收录", "evidence": "identify",
             "method": "glyph_rerank", "confidence": 0.9,
             "suggestion": {"kind": "font",
                            "canonical_name": "Mystery Font",
                            "license_category": "unknown"}},
            {"type": "new_mapping", "font_name": "商業フォント",
             "candidate": "思源黑体 CN", "reason": "同类形兜底",
             "evidence": "chain", "method": "llm_inferred",
             "confidence": 0.4,
             "suggestion": {"kind": "mapping", "jp_name": "商業フォント",
                            "cn_name": "思源黑体 CN"}},
            {"type": "new_font_record", "font_name": "NoSuggest",
             "candidate": None, "reason": "x", "evidence": "y",
             "method": "llm_inferred", "confidence": 0.5,
             "suggestion": None},
        ]

    def test_rows_and_get_result(self):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from PySide6.QtWidgets import QApplication

        _app = QApplication.instance() or QApplication(sys.argv[:1])
        from components.font_update_review_dialog import FontUpdateReviewDialog

        dialog = FontUpdateReviewDialog(self._items())
        assert dialog._rows[0].checkbox.isEnabled()
        assert not dialog._rows[0].checkbox.isChecked()  # 默认全不选
        assert not dialog._rows[2].checkbox.isEnabled()  # 无建议不可采纳
        dialog._rows[0].checkbox.setChecked(True)
        result = dialog.get_result()
        assert [i["font_name"] for i in result["adopted"]] == ["Mystery Font"]
        assert [i["font_name"] for i in result["rejected"]] == \
            ["商業フォント"]

    def test_dialog_does_not_write_db(self):
        """分层红线：对话框模块不做任何 DB 写入（不依赖 font_intel）。"""
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        import components.font_update_review_dialog as mod

        assert "FontsDB" not in inspect.getsource(mod)

    def test_adopted_flow_via_db_loader(self, tmp_path):
        """对话框采纳 → db_loader 写覆盖层（映射行下次运行参与链查询）。"""
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from PySide6.QtWidgets import QApplication

        _app = QApplication.instance() or QApplication(sys.argv[:1])
        from components.font_update_review_dialog import FontUpdateReviewDialog
        from font_intel.etl.db_loader import apply_font_update_decisions

        dialog = FontUpdateReviewDialog(self._items()[:2])
        dialog._rows[1].checkbox.setChecked(True)  # 只采纳映射条目
        result = dialog.get_result()
        db = FontsDB(str(tmp_path / "fonts.db"))
        try:
            stats = apply_font_update_decisions(db, result)
            assert stats["adopted_items"] == 1
            assert stats["written"] == 1
            rows = db.lookup_jp_cn("商業フォント")
            assert rows and rows[0]["method"] == "human"
            assert db.lookup_font("Mystery Font") is None  # 未采纳不写库
        finally:
            db.close()


# ── 7. 接线缝：worker / CLI ─────────────────────────────────────


class TestWiring:
    def test_optimizer_accepts_font_identifications(self):
        assert "font_identifications" in inspect.signature(
            OCRToASSOptimizer.__init__).parameters

    def test_worker_builds_compliance_config_from_identify_config(
            self, tmp_path):
        from core.pipeline_worker import PipelineWorker
        from font_intel.identify_stage import FontIdentifyConfig
        from font_intel.integration import FontComplianceConfig

        worker = PipelineWorker(
            video_path=str(tmp_path / "in.avi"),
            roi_data=[], total_frames=100, fps=25.0,
            video_width=1280, video_height=720,
            output_ass_path=str(tmp_path / "out.ass"),
            debug_mode=False, template_path=None,
            font_identify_config=FontIdentifyConfig(
                enabled=True, db_path=str(tmp_path / "fonts.db")),
        )
        # 无识别结果 → None（生成器行为与旧版本一致）。
        assert worker._build_font_compliance_config() is None
        worker.font_identifications = _simple_ident("Malgun Gothic")
        cfg = worker._build_font_compliance_config()
        assert isinstance(cfg, FontComplianceConfig)
        assert cfg.enabled is True
        assert cfg.db_path == str(tmp_path / "fonts.db")
        assert cfg.interactive is False
        # 绝不进入阶段 1-3 参数包（红线）。
        ctx = worker._stage_ctx()
        assert "font" not in "".join(f for f in vars(ctx))

    def test_worker_disabled_identify_yields_none_config(self, tmp_path):
        from core.pipeline_worker import PipelineWorker

        worker = PipelineWorker(
            video_path=str(tmp_path / "in.avi"),
            roi_data=[], total_frames=100, fps=25.0,
            video_width=1280, video_height=720,
            output_ass_path=str(tmp_path / "out.ass"),
            debug_mode=False, template_path=None,
        )
        assert worker._build_font_compliance_config() is None

    def test_cli_font_identify_args(self):
        from cli import _font_identify_config_from_args, parse_args

        args = parse_args(["video.mp4"])
        assert args.font_identify is False
        assert args.font_db == ""
        assert _font_identify_config_from_args(args) is None

        args = parse_args(
            ["video.mp4", "--font-identify", "--font-db", "/tmp/fonts.db"])
        assert args.font_identify is True
        cfg = _font_identify_config_from_args(args)
        assert cfg is not None
        assert cfg.enabled is True
        assert cfg.db_path == "/tmp/fonts.db"

    def test_cli_default_db_path_falls_back_to_default(self):
        from cli import _font_identify_config_from_args, parse_args
        from font_intel import fonts_db

        args = parse_args(["video.mp4", "--font-identify"])
        cfg = _font_identify_config_from_args(args)
        assert cfg is not None
        assert cfg.db_path is None  # 运行时落 fonts_db.default_db_path()
        assert fonts_db.default_db_path()


# ── 8. 事件捕获（文本匹配）单元 ─────────────────────────────────


class TestCaptureEventIdentifications:
    def test_exact_and_segment_matching(self):
        from font_intel.closure import capture_event_identifications

        idents = _ident_result({
            ("roi_0", 10, 0): _entry("学成归来", [_font("F1")]),
            ("roi_0", 12, 1): _entry("第二行", [_font("F2")], frame=12),
        })
        events = [
            {"roi": "roi_0", "style": "CH", "tags": "", "body": "学成归来"},
            {"roi": "roi_0", "style": "CH", "tags": "{\\an8}",
             "body": "前缀\\N第二行"},
            {"roi": "roi_1", "style": "CH", "tags": "", "body": "学成归来"},
            {"roi": "roi_0", "style": "Scene", "tags": "", "body": "",
             "policy": True},
        ]
        captured = capture_event_identifications(events, idents)
        assert captured == 2
        assert events[0]["_font_ident"]["name"] == "F1"
        assert events[1]["_font_ident"]["name"] == "F2"
        assert "_font_ident" not in events[2]  # ROI 不匹配
        assert "_font_ident" not in events[3]  # 策略事件跳过

    def test_none_idents_is_noop(self):
        from font_intel.closure import capture_event_identifications

        events = [{"roi": "roi_0", "body": "x"}]
        assert capture_event_identifications(events, None) == 0
        assert "_font_ident" not in events[0]

    def test_whitespace_normalized_matching(self):
        from font_intel.closure import capture_event_identifications

        idents = _ident_result({
            ("roi_0", 10, 0): _entry("学 成 归 来", [_font("F1")]),
        })
        events = [{"roi": "roi_0", "style": "CH", "tags": "",
                   "body": "学成归来"}]
        assert capture_event_identifications(events, idents) == 1

    def test_normalize_target_lang(self):
        from font_intel.closure import normalize_target_lang

        assert normalize_target_lang("简体中文") == "zh"
        assert normalize_target_lang("繁體中文") == "zh"
        assert normalize_target_lang("日本語") == "ja"
        assert normalize_target_lang("English") == "en"
        assert normalize_target_lang("") == ""
