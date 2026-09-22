# font_intel/closure.py
"""端到端闭环（T3.4）：识别侧表 → 合规决策 → 逐事件 ``\\fn`` 落名 → 报告与缺口更新建议包。

方案落点（docs/feature_plan_font_translation_compliance.md §2 数据流图、
§5.3 运行时流程、§5.4 数据库反馈更新、§6.2 决策动作）
--------------------------------------------------------------------
数据流中被本模块接通的环节：

    [font_intel 识别] 候选 Top-N
      → [matching 查询] 映射链（感知翻译目标语言）
      → [compliance 决策] allow / prompt / replace_auto / report_only
      → 逐事件 ``\\fn`` 覆盖通道落字体名（替换前后写入合规报告）
      → 缺口回写：unknown 字体 / 低置信映射 / 同类形兜底 → 更新建议包

两条铁律（与 ``font_intel/integration.py`` 一致）：

- **绝不阻塞出片**：本模块任何异常都被 ``apply_event_font_closure``
  吞掉并返回空决策（生成器据此继续走原路径）；``prompt`` 非交互降级
  为"保留样式字体 + 记录决策"。无网络调用。
- **保留现状样式**：识别字体 unknown / 无识别结果 / ``report_only`` /
  非交互 ``prompt`` 一律不写 ``\\fn``，输出与关闭态逐字节一致。

翻译联动（§5.3 路径二）
------------------------
``translation_config`` 存在时，被翻译行的 ``\\fn`` 决策**不走原名直判**，
改走 :func:`font_intel.matching.resolve_font_chain`（目标语言 = 翻译目标
语言归一码）：链上有可用候选（对位 / 开源替代 / 同类形）→ 以候选名做
``\\fn`` 并在报告记录依据（``mapping`` / ``open_source`` /
``category_match``）；链 ``unresolved`` → 保留原字体名引用 + 报告标注
"未映射"，不强行替换（§9：宁可保留引用，绝不编造替换）。

更新建议包（§5.4）
------------------
识别中出现 unknown 字体 / 低置信映射 / 链上更优建议（同类形兜底）时
逐条生成建议（类型：新增字体记录 / 映射修正 / 新增映射，附候选值、
理由、依据来源、``method=llm_inferred`` 或 ``glyph_rerank``、置信度），
随合规报告同目录输出 ``<stem>_font_update_suggestions.json``。
**worker 不弹 GUI**：包落盘即可；逐条采纳/否决由
``components/font_update_review_dialog.py`` 在用户主动触发时进行，
采纳项经 ``db_loader.apply_font_update_decisions`` 写覆盖层
（``method=human`` 确认语义）。
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from font_intel.compliance import decide, sanitize_url
from font_intel.matching import (
    ALLOWED_LICENSE_CATEGORIES,
    BASIS_CATEGORY_MATCH,
    BASIS_MAPPING,
    STATUS_RESOLVED,
    resolve_font_chain,
)

logger = logging.getLogger(__name__)

# 更新建议包 schema（复用 T1.5 review.py 的版本标记模式；导出可再导入）。
SUGGESTIONS_SCHEMA = "vso_font_update_suggestions/1"

# 译文行映射链 unresolved 时的回退字体偏好（确定性、逐库核验）：按目标
# 语言给出常见开源 CJK 字体名，取第一个「库内收录且许可放行」的；库里
# 没有任何命中 → 不写 \fn，译文行回落样式字体。映射缺口仍照常进报告与
# 更新建议包——回退只解决渲染，不掩盖数据缺口。
FALLBACK_FONTS_BY_LANG: Dict[str, tuple] = {
    "zh": (
        "思源黑体 CN", "思源黑体", "思源黑體 Noto Sans CJK",
        "Source Han Sans CN", "Noto Sans CJK SC", "Noto Sans SC",
        "思源宋体 CN", "思源宋体", "Source Han Serif CN", "Noto Serif CJK SC",
        "文泉驿正黑", "WenQuanYi Zen Hei", "方正黑体", "方正书宋",
    ),
    "ja": (
        "源ノ角ゴシック", "Noto Sans CJK JP", "源ノ明朝", "Noto Serif CJK JP",
        "IPAexゴシック", "IPAexGothic",
    ),
}

# 建议类型（§5.4：新增字体记录 / 映射修正 / 新增映射）。
TYPE_NEW_FONT_RECORD = "new_font_record"
TYPE_MAPPING_CORRECTION = "mapping_correction"
TYPE_NEW_MAPPING = "new_mapping"
SUGGESTION_TYPES = (TYPE_NEW_FONT_RECORD, TYPE_MAPPING_CORRECTION,
                    TYPE_NEW_MAPPING)

# 建议方法标注：llm_inferred（模型/链路推断，未经人工确认绝不参与
# replace_auto——防御在 compliance.decide() 内）或 glyph_rerank（字形
# 重排证据背书）。
METHOD_LLM_INFERRED = "llm_inferred"
METHOD_GLYPH_RERANK = "glyph_rerank"

# 映射置信度低于该值视为"低置信映射"（建议包触发条件之二）。
LOW_MAPPING_CONFIDENCE = 0.7

# 事件级识别快照挂在 subtitle_event 上的私有键（写出路径只读既有键，
# 该键绝不进入 ASS）。
_IDENT_KEY = "_font_ident"


# ── \fn 覆盖通道（块内合并） ─────────────────────────────────────


def merge_fn_tag(tags: str, font_name: str) -> str:
    """把 ``\\fn`` 字体覆盖并入单条事件的标签串（纯函数，不修改输入）。

    与 :func:`core.line_restoration.merge_restoration_tags` 同一"块内
    合并"思路：``\\fn`` 写在 override 块外会被当作字幕字面文本渲染。
    已有块 → 并入块内、追加在块尾（``\\fn`` 只改后续字形选择，与既有
    ``\\pos``/旋转标签正交，ASS 标签按出现顺序生效）；无块 → 防御性
    包成独立块。字体名剔除 ``{``/``}`` 与换行，防止溢出或分裂 override
    块；清洗后为空则原样返回。
    """
    safe = (str(font_name or "")
            .replace("{", "").replace("}", "")
            .replace("\r", "").replace("\n", "")
            .strip())
    if not safe:
        return tags
    tags = str(tags or "")
    if tags.endswith("}"):
        return tags[:-1] + "\\fn" + safe + "}"
    return tags + "{\\fn" + safe + "}"


# ── 翻译目标语言归一 ─────────────────────────────────────────────

# 人类可读目标语言名（TranslationConfig.target_language，与 GUI/CLI 取值
# 对齐）→ 映射链语言码（resolve_font_chain 只对 zh/ja 有对位表路径）。
_TARGET_LANG_CODES = {
    "简体中文": "zh", "繁體中文": "zh", "繁体中文": "zh", "中文": "zh",
    "日本語": "ja", "日语": "ja", "日文": "ja",
    "English": "en", "英语": "en", "英文": "en",
    "한국어": "ko", "韩语": "ko", "韓國語": "ko",
    "Русский": "ru", "俄语": "ru",
    "Français": "fr", "法语": "fr",
    "Deutsch": "de", "德语": "de",
    "Italiano": "it", "意大利语": "it",
    "Español": "es", "西班牙语": "es",
    "Português": "pt", "葡萄牙语": "pt",
    "العربية": "ar", "阿拉伯语": "ar",
}


def normalize_target_lang(name: Any) -> str:
    """翻译目标语言名 → 映射链语言码；未知名称原样小写返回（链侧按
    "无对位表路径"处理，走开源替代 + 同类形兜底）。"""
    key = str(name or "").strip()
    if not key:
        return ""
    code = _TARGET_LANG_CODES.get(key)
    if code:
        return code
    return key.lower()


# ── 识别侧表 → 事件匹配（翻译前按原文挂载） ─────────────────────


def _norm_text(text: Any) -> str:
    """匹配键归一：去全部空白（OCR 对空格的不同读法不应拆开匹配）。"""
    return "".join(str(text or "").split())


def build_identification_index(font_identifications: Any) -> Dict[Tuple[str, str], dict]:
    """识别侧表 → ``(roi_id, 归一文本) → 条目`` 索引（首现优先，确定序）。"""
    idents = getattr(font_identifications, "identifications", None)
    if not idents:
        return {}
    index: Dict[Tuple[str, str], dict] = {}
    for entry in idents.values():
        if not isinstance(entry, dict):
            continue
        roi = str(entry.get("roi_id") or "")
        text = _norm_text(entry.get("text"))
        if not roi or not text:
            continue
        index.setdefault((roi, text), entry)
    return index


def _ident_snapshot(entry: dict) -> Optional[dict]:
    """识别条目 → 事件级快照（Top-1 候选 + 置信度 + 标记）；无候选返回
    ``{"name": ""}``（闭包侧按"无识别"处理，保留现状样式）。"""
    fonts = entry.get("identified_fonts") or []
    top = fonts[0] if fonts else None
    if not isinstance(top, dict) or not str(top.get("name") or "").strip():
        return {"name": ""}
    return {
        "name": str(top.get("name")).strip(),
        "score": top.get("score"),
        "low_confidence": bool(top.get("low_confidence")),
        "glyph_ranked": bool(top.get("glyph_ranked")),
        "license_category": top.get("license_category"),
    }


def capture_event_identifications(events: List[dict],
                                  font_identifications: Any) -> int:
    """按原文把识别侧表挂到字幕事件（私有键 ``_font_ident``），返回条数。

    - **必须在润色/翻译改写 body 之前调用**（匹配依据是 OCR 原文）；
    - 事件 body 先整串匹配，再按 ``\\N`` 逐段匹配（BOTTOM 组多行合并
      事件逐一回退到行级识别条目）；
    - 策略事件（遮罩/NoteBox）与空 body 不参与；
    - 侧表为空/None → 零操作（关闭态零变化）。
    """
    index = build_identification_index(font_identifications)
    if not index:
        return 0
    captured = 0
    for ev in events or []:
        if not isinstance(ev, dict) or ev.get("policy"):
            continue
        body = str(ev.get("body") or "")
        if not body.strip():
            continue
        roi = str(ev.get("roi") or "")
        entry = None
        for segment in [body] + body.split("\\N"):
            entry = index.get((roi, _norm_text(segment)))
            if entry is not None:
                break
        if entry is None:
            continue
        ev[_IDENT_KEY] = _ident_snapshot(entry)
        captured += 1
    return captured


# ── 事件级合规决策 + \fn 落名 ────────────────────────────────────

_PROMPT_NOTE_NON_INTERACTIVE = (
    "非交互模式（interactive=False）：prompt 降级为保留样式字体并记录决策，"
    "不阻断出片。")
_PROMPT_NOTE_INTERACTIVE = (
    "交互模式：prompt 待用户确认，先保留样式字体并记录决策。")


def _default_alternatives_provider(db, target_lang: str = "zh"):
    """缺省替代链查询：未显式配置 ``alternatives_provider`` 的
    ``replace_auto`` 沿三级链（对位 → 开源替代 → 同类形）取候选。"""
    if db is None:
        return None

    def _provider(font_name: str) -> List[str]:
        result = resolve_font_chain(db, font_name, target_lang)
        return [c.name for c in result.candidates]

    return _provider


def _lookup_public_info(db, name: str) -> tuple:
    """字体许可类别与官方链接（报告用）；库不可用/查询失败按 unknown。"""
    if db is not None:
        try:
            info = db.lookup_font(name)
        except Exception:
            info = None
        if info:
            return (info.get("license_category") or "unknown",
                    sanitize_url(info.get("official_url")))
    return "unknown", ""


def apply_event_font_closure(
    events: List[dict],
    compliance_config,
    *,
    translation_config: Any = None,
) -> Tuple[List[dict], List[dict]]:
    """对已捕获识别快照的字幕事件执行合规决策并写入逐事件 ``\\fn``。

    返回 ``(事件级决策列表, 更新建议条目列表)``；本函数保证不抛异常：
    任何异常降级为 ``([], [])``（生成器继续走原路径，绝不中断出片）。
    决策/``\\fn`` 合并语义见模块 docstring；``compliance_config`` 关闭
    或无捕获事件时为零操作。
    """
    if compliance_config is None or not getattr(
            compliance_config, "enabled", False):
        return [], []
    try:
        return _apply_locked(events, compliance_config,
                             translation_config=translation_config)
    except Exception as exc:  # 合规永不中断出片
        logger.warning("字体识别闭环异常，保持原事件输出: %s", exc)
        return [], []


def _apply_locked(events, compliance_config, *, translation_config) \
        -> Tuple[List[dict], List[dict]]:
    gate = compliance_config.resolve()
    db = gate.db
    interactive = bool(getattr(compliance_config, "interactive", False))
    translation_active = translation_config is not None
    target_lang = (normalize_target_lang(
        getattr(translation_config, "target_language", ""))
        if translation_active else "")
    provider = getattr(compliance_config, "alternatives_provider", None)
    if provider is None:
        provider = _default_alternatives_provider(db, target_lang or "zh")

    decisions: List[dict] = []
    suggestions: List[dict] = []
    seen_suggestions: set = set()

    for ev in events or []:
        snapshot = ev.get(_IDENT_KEY) if isinstance(ev, dict) else None
        if not isinstance(snapshot, dict):
            continue
        identified = str(snapshot.get("name") or "").strip()
        if not identified:
            continue  # 无识别（无候选）→ 保留现状样式

        if translation_active:
            _decide_translated(
                ev, identified, snapshot, gate, db, target_lang, decisions,
                suggestions, seen_suggestions)
        else:
            _decide_direct(
                ev, identified, snapshot, gate, db, provider, interactive,
                decisions)
        suggestions.extend(
            _font_record_suggestion(identified, snapshot, db,
                                    seen_suggestions))
    return decisions, suggestions


def _record_decision(decisions: List[dict], *, ev, identified: str,
                     final_font: str, action: str, license_category: str,
                     gate, reason: str, official_url: str, alternatives,
                     granted: bool, snapshot: dict, fn_written: bool,
                     mapping_basis: str = "", target_lang: str = "",
                     unmapped: bool = False) -> None:
    decisions.append({
        "scope": "event",
        "style": str(ev.get("style") or ""),
        "font_name": identified,
        "final_font": final_font,
        "replaced": final_font != identified,
        "action": action,
        "license_category": license_category,
        "scene": gate.scene,
        "reason": reason,
        "official_url": official_url,
        "alternatives": list(alternatives or []),
        "granted": bool(granted),
        "events": 1,
        "fn_written": bool(fn_written),
        "identified_score": snapshot.get("score"),
        "mapping_basis": mapping_basis,
        "target_lang": target_lang,
        "unmapped": bool(unmapped),
    })


def _decide_direct(ev, identified, snapshot, gate, db, provider, interactive,
                   decisions) -> None:
    """原名直判（§5.3 路径一）：decide() 四动作 → \\fn 落名。

    - ``allow``（含已获授权放行）：识别字体名即设即用（``\\fn`` 原名）；
    - ``replace_auto``：替代链首个候选（置信度门槛与 llm_inferred 防御
      均已在 :func:`font_intel.compliance.decide` 内生效）；
    - ``prompt`` / ``report_only``：保留样式字体（不写 ``\\fn``）+ 记录。
    """
    # 识别阶段已标记 low_confidence 的候选不参与自动替换（confidence=None
    # → decide() 内部把 replace_auto 降级 prompt）。
    confidence = (None if snapshot.get("low_confidence")
                  else snapshot.get("score"))
    decision = decide(
        identified,
        scene=gate.scene,
        db=db,
        confidence=confidence,
        rules=gate.rules,
        alternatives_provider=provider,
    )
    reason = decision.reason
    fn_font = ""
    if decision.action == "allow":
        fn_font = identified
    elif decision.action == "replace_auto" and decision.alternatives:
        fn_font = decision.alternatives[0]
    elif decision.action == "prompt":
        note = (_PROMPT_NOTE_INTERACTIVE if interactive
                else _PROMPT_NOTE_NON_INTERACTIVE)
        reason += note
    if fn_font:
        ev["tags"] = merge_fn_tag(ev.get("tags"), fn_font)
    _record_decision(
        decisions, ev=ev, identified=identified,
        final_font=fn_font or identified,
        action=decision.action, license_category=decision.license_category,
        gate=gate, reason=reason, official_url=decision.official_url,
        alternatives=decision.alternatives, granted=decision.granted,
        snapshot=snapshot, fn_written=bool(fn_font))


def _fallback_font_for_lang(db, lang: str) -> tuple:
    """库内回退字体：偏好列表 ∩ 收录 ∩ 许可放行，取首个命中。

    返回 ``(字体名, fonts 表记录)``；无命中 ``(None, None)``。
    """
    if db is None:
        return None, None
    for name in FALLBACK_FONTS_BY_LANG.get(lang, ()):
        try:
            info = db.lookup_font(name)
        except Exception:
            info = None
        if info and (info.get("license_category") or "unknown") \
                in ALLOWED_LICENSE_CATEGORIES:
            return name, info
    return None, None


def _decide_translated(ev, identified, snapshot, gate, db, target_lang,
                       decisions, suggestions, seen_suggestions) -> None:
    """翻译联动（§5.3 路径二）：不走原名直判，改走三级映射链。"""
    lang = target_lang or "zh"
    if db is None:
        chain = None
        notes = ["字体库不可用，映射链查询跳过。"]
    else:
        chain = resolve_font_chain(db, identified, lang)
        notes = list(chain.notes)

    if chain is not None and chain.status == STATUS_RESOLVED and chain.candidates:
        best = chain.candidates[0]
        license_category, official_url = _lookup_public_info(db, best.name)
        ev["tags"] = merge_fn_tag(ev.get("tags"), best.name)
        _record_decision(
            decisions, ev=ev, identified=identified, final_font=best.name,
            action="replace_auto", license_category=license_category,
            gate=gate,
            reason=(f"翻译目标语言 {lang}：{best.reason}。"),
            official_url=official_url, alternatives=[c.name for c in chain.candidates],
            granted=False, snapshot=snapshot, fn_written=True,
            mapping_basis=best.basis, target_lang=lang)
        _mapping_suggestions(identified, chain, seen_suggestions, suggestions)
        return

    # unresolved：译文行不再钉死原（日文）字体名——原字体名对目标语言
    # 文本没有字形意义，写 \fn 反而把译文锁进错误字形体系。回退顺序：
    # 1) 库内已知开源/免费目标语言字体（确定性偏好列表 ∩ 许可放行）；
    # 2) 库内无可用回退 → 不写 \fn，译文行回落样式字体。两种情况都保持
    #    unmapped=True 留痕（回退只解决渲染，不掩盖映射数据缺口）。
    license_category, official_url = _lookup_public_info(db, identified)
    note_text = "；".join(notes) if notes else "全链无可用候选。"
    fallback_name, fallback_info = _fallback_font_for_lang(db, lang)
    if fallback_name is not None:
        fallback_license = (fallback_info or {}).get(
            "license_category") or "unknown"
        _, fallback_url = _lookup_public_info(db, fallback_name)
        ev["tags"] = merge_fn_tag(ev.get("tags"), fallback_name)
        _record_decision(
            decisions, ev=ev, identified=identified,
            final_font=fallback_name, action="replace_auto",
            license_category=fallback_license, gate=gate,
            reason=(f"翻译目标语言 {lang} 的映射链 unresolved（{note_text}），"
                    f"译文行回退库内开源字体 {fallback_name} 保障渲染；"
                    f"原字体 {identified} 保留于原文 Comment 行。"),
            official_url=fallback_url, alternatives=[fallback_name],
            granted=False, snapshot=snapshot, fn_written=True,
            mapping_basis="open_fallback", target_lang=lang, unmapped=True)
        return

    # 库内无可用回退字体：译文行不落 \fn（回落样式字体），决策留痕。
    _record_decision(
        decisions, ev=ev, identified=identified, final_font=identified,
        action="allow", license_category=license_category, gate=gate,
        reason=(f"翻译目标语言 {lang} 的映射链 unresolved（{note_text}），"
                f"库内无可用回退字体，译文行不写 \\fn（回落样式字体）；"
                f"报告标注未映射。"),
        official_url=official_url, alternatives=[], granted=False,
        snapshot=snapshot, fn_written=False, mapping_basis="unresolved",
        target_lang=lang, unmapped=True)


# ── 缺口更新建议（§5.4） ─────────────────────────────────────────


def _font_record_suggestion(identified: str, snapshot: dict, db,
                            seen: set) -> List[dict]:
    """unknown 字体（不在本地库）→ 新增字体记录建议。"""
    if db is None:
        return []
    try:
        known = db.lookup_font(identified) is not None
    except Exception:
        return []
    if known:
        return []
    key = (TYPE_NEW_FONT_RECORD, identified, None)
    if key in seen:
        return []
    seen.add(key)
    method = (METHOD_GLYPH_RERANK if snapshot.get("glyph_ranked")
              else METHOD_LLM_INFERRED)
    score = snapshot.get("score")
    return [{
        "type": TYPE_NEW_FONT_RECORD,
        "font_name": identified,
        "candidate": None,
        "reason": ("识别字体未收录本地字体库（许可类别 unknown，默认从严），"
                   "建议补充字体记录。"),
        "evidence": (f"font_identify Top-1 score={score} "
                     f"glyph_ranked={bool(snapshot.get('glyph_ranked'))}"),
        "method": method,
        "confidence": (float(score) if isinstance(score, (int, float))
                       else 0.0),
        "suggestion": {"kind": "font", "canonical_name": identified,
                       "license_category": "unknown"},
    }]


def _mapping_suggestions(identified: str, chain, seen: set,
                         suggestions: List[dict]) -> None:
    """链命中后的缺口建议：低置信映射 → 修正；同类形兜底 → 新增映射。"""
    best = chain.candidates[0]
    key = (None, identified, best.name)
    if best.basis == BASIS_MAPPING:
        if best.confidence >= LOW_MAPPING_CONFIDENCE:
            return
        entry_type = TYPE_MAPPING_CORRECTION
        reason = (f"映射链命中的对位候选置信度 {best.confidence:.2f} 低于"
                  f" {LOW_MAPPING_CONFIDENCE}，建议人工复核/修正该映射。")
        evidence = f"chain level {best.level} basis={best.basis}"
    elif best.basis == BASIS_CATEGORY_MATCH:
        entry_type = TYPE_NEW_MAPPING
        reason = ("映射链无直接对位/开源替代，按同风格类别兜底命中；建议"
                  "把该候选固化为显式映射，后续运行直达。")
        evidence = f"chain level {best.level} basis={best.basis}"
    else:
        return
    key = (entry_type, identified, best.name)
    if key in seen:
        return
    seen.add(key)
    suggestions.append({
        "type": entry_type,
        "font_name": identified,
        "candidate": best.name,
        "reason": reason,
        "evidence": evidence,
        "method": METHOD_LLM_INFERRED,
        "confidence": float(best.confidence),
        "suggestion": {"kind": "mapping", "jp_name": identified,
                       "cn_name": best.name},
    })


# ── 建议包 JSON（schema 版本标记，复用 review.py 模式） ──────────


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


def build_update_suggestions_package(items: List[dict], *,
                                     source_tag: str =
                                     "video_subtitle_ocr font_identify") -> dict:
    """建议条目 → 建议包 dict（带 schema 标记与统计）。"""
    items = [dict(i) for i in (items or [])]
    return {
        "schema": SUGGESTIONS_SCHEMA,
        "generated_at": _now_iso(),
        "source_tag": source_tag,
        "stats": {
            "items": len(items),
            "actionable": sum(1 for i in items if i.get("suggestion")),
        },
        "items": items,
    }


def suggestions_path_for(ass_path) -> Path:
    """与 ASS 同目录的更新建议包路径：``<stem>_font_update_suggestions.json``。"""
    ass_path = Path(ass_path)
    return ass_path.with_name(f"{ass_path.stem}_font_update_suggestions.json")


def write_update_suggestions_package(package: dict, path) -> None:
    """建议包 → JSON 文件（UTF-8、ensure_ascii=False，人工可读）。"""
    text = json.dumps(package, ensure_ascii=False, indent=2) + "\n"
    Path(path).write_text(text, encoding="utf-8")


def read_update_suggestions_package(path) -> dict:
    """读回建议包；schema 不符抛 ValueError（防错导他类文件）。"""
    with open(path, encoding="utf-8") as fh:
        payload = json.load(fh)
    if not isinstance(payload, dict) or payload.get("schema") != SUGGESTIONS_SCHEMA:
        raise ValueError(
            f"unexpected schema in {path}: expected {SUGGESTIONS_SCHEMA!r}, "
            f"got {payload.get('schema') if isinstance(payload, dict) else type(payload).__name__!r}"
        )
    return payload
