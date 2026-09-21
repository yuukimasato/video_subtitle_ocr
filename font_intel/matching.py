# font_intel/matching.py
"""中日字体映射三级链查询（T2.4a）：对位 → 开源替代 → 同风格类别兜底。

纯查询模块：只读 ``FontsDB``（SQLite），零网络调用——所有候选都来自库内
种子/覆盖双层记录，绝不编造字体名。

对外 API（方案 §5.2/§5.3）
--------------------------
- ``lookup_jp_font(db, name)``：日文字体名 → 中文对位候选列表。返回值三态
  可区分：``[]`` = 表中无此字体；含 ``kind="negative_mapping"`` 行 = 表中
  明确标记无对位；含 ``kind="mapping"`` 行 = 对位候选。查询名先精确命中，
  未命中时经 ``fonts``/``aliases`` 表解析同一字体的简/繁/日别名写法重试。
- ``open_source_alternates(db, font)``：字体 → 开源替代候选。查库字段优先
  （``fonts.alternates`` 开源替代链，schema v3），Seekladoom 映射表的中文
  对位作为替代链补充环节；只保留 ``license_category="open_source"`` 的候选
  （未收录/许可不符的替代名一律丢弃，绝不猜）。本机已安装索引（seed 行
  ``source="local_index"``）仅作 ``installed`` 标注，不联网。
- ``resolve_font_chain(db, font_name, target_lang="zh")``：三级链组合。
  第一级：目标语言对位（``zh`` 正查映射表；``ja`` 反向查同一张表；其他
  目标语言无表可查，跳过并记入 notes）。第二级：对位不可用（无对位/
  负映射/许可不放行）时走开源替代（先沿被否对位的替代链续链，再查原
  字体）。第三级：仍无结果 → 同风格类别匹配（``STYLE_CATEGORY_GROUPS``
  常量表），仅在目标语言字体库（``fonts.languages``/``category`` 字段）
  内检索，开源优先排序。全链无果返回 ``unresolved``，调用方据此"保持原
  字体名 + 报告标注未映射"。

分层与排序语义
--------------
- 覆盖层（overlay）优先于种子层（seed）：同键遮蔽由 ``FontsDB`` 保证；
  matching 再补两条交叉语义——覆盖层负映射否决种子正映射（用户否决），
  覆盖层正映射压过种子负映射（用户补充）。
- 许可放行口径：``ALLOWED_LICENSE_CATEGORIES``（open_source/free_commercial）；
  ``unknown`` 从严不放行。第一/二级候选按（层、许可、置信度降序、名）排序，
  第三级按（许可、层、置信度降序、名）排序（"开源优先"）。
- 每个候选带：字体名、依据（basis：mapping/open_source/category_match）、
  所处链级、来源层、许可类别、置信度与人类可读理由，供合规闸门与报告消费。
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import asdict, dataclass, field
from typing import Optional

from font_intel.fonts_db import FontsDB, LAYER_OVERLAY, LAYER_SEED

logger = logging.getLogger(__name__)

# ── 常量 ─────────────────────────────────────────────────────────

# 链级依据（候选 basis 字段取值）。
BASIS_MAPPING = "mapping"
BASIS_OPEN_SOURCE = "open_source"
BASIS_CATEGORY_MATCH = "category_match"

# 链状态：resolved = 存在许可放行的候选；unresolved = 全链无果，
# 调用方保持原字体名并在报告标注未映射。
STATUS_RESOLVED = "resolved"
STATUS_UNRESOLVED = "unresolved"

# 许可放行口径：仅这两类可作为替换候选；unknown 默认从严（不放行）。
ALLOWED_LICENSE_CATEGORIES: tuple[str, ...] = ("open_source", "free_commercial")

_LICENSE_RANK = {"open_source": 0, "free_commercial": 1, "unknown": 2,
                 "commercial_paid": 3}
_LAYER_RANK = {LAYER_OVERLAY: 0, LAYER_SEED: 1}

# 各链级缺省置信度（库内记录未标注 confidence 时使用；层级越靠后越低）。
DEFAULT_MAPPING_CONFIDENCE = 0.6
OPEN_SOURCE_ALTERNATE_CONFIDENCE = 0.5
CATEGORY_MATCH_CONFIDENCE = 0.4

# 同风格类别映射规则（方案 §5.3：黑体↔Sans、明朝/宋↔Serif、
# 楷/手写↔Script/Casual、圆体↔Round）。模块级常量表，便于测试与后续扩展；
# 组内 token 是同一风格在不同厂商/词表下的等价写法（匹配大小写不敏感），
# 各组 token 必须互不相交（tests 有断言守护）。
STYLE_CATEGORY_GROUPS: dict[str, tuple[str, ...]] = {
    "sans": ("sans", "黑体", "黑", "gothic", "ゴシック", "heiti"),
    "serif": ("serif", "宋体", "宋", "明朝", "明朝体", "mincho", "song"),
    "script": ("script", "楷体", "楷", "手写", "handwriting", "casual"),
    "round": ("round", "圆体", "圆", "丸ゴシック", "maru"),
}


def category_style_group(category: Optional[str]) -> Optional[tuple[str, ...]]:
    """类别名 → 所属同风格组（token 元组）；不在表内返回 None（不猜）。"""
    token = (category or "").strip().lower()
    if not token:
        return None
    for group in STYLE_CATEGORY_GROUPS.values():
        if token in {t.lower() for t in group}:
            return group
    return None


# ── 链结果对象 ───────────────────────────────────────────────────

@dataclass
class ChainCandidate:
    """三级链单条候选：仅含可 JSON 序列化字段，供合规闸门与报告消费。"""

    name: str
    basis: str            # mapping / open_source / category_match
    level: int            # 1=对位 2=开源替代 3=同风格类别
    layer: str            # 证据行来源层：seed / overlay
    license_category: str
    confidence: float
    reason: str           # 依据的人类可读描述（报告用）
    source: Optional[str] = None      # 证据行溯源（map source/source_file 等）
    installed: Optional[bool] = None  # 是否命中本机已安装索引（补充校验标注）

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class ChainResult:
    """三级链解析结果。

    ``status=unresolved`` 时 ``candidates`` 恒为空；``notes`` 按序记录每一
    级的判定依据（含"对位存在但许可不放行""表中明确无对位"等），调用方
    据此保持原字体名并在报告标注未映射。
    """

    status: str
    font_name: str
    target_lang: str
    candidates: list[ChainCandidate] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    @property
    def resolved(self) -> bool:
        return self.status == STATUS_RESOLVED

    @property
    def best(self) -> Optional[ChainCandidate]:
        return self.candidates[0] if self.candidates else None

    def to_dict(self) -> dict:
        return {
            "status": self.status,
            "font_name": self.font_name,
            "target_lang": self.target_lang,
            "candidates": [c.to_dict() for c in self.candidates],
            "notes": list(self.notes),
        }


# ── 内部工具 ─────────────────────────────────────────────────────

def _font_spellings(db: FontsDB, name: str) -> list[str]:
    """经 fonts/aliases 表解析同一字体的全部写法（规范名在前，别名随后）；
    字体未收录时只返回原名。"""
    spellings = [name]
    seen = {name.lower()}
    try:
        info = db.lookup_font(name)
    except Exception as exc:  # 库异常不中断查询链（优雅降级为只查原名）
        logger.warning("fonts 别名解析失败，仅按原名查询: name=%s, error=%s", name, exc)
        info = None
    if info:
        for cand in [info.get("canonical_name")] + list(info.get("aliases") or []):
            text = (cand or "").strip()
            if text and text.lower() not in seen:
                seen.add(text.lower())
                spellings.append(text)
    return spellings


def _filter_map_rows(rows: list[dict]) -> list[dict]:
    """覆盖层交叉语义：负映射否决种子正映射（用户否决）；覆盖层正映射
    压过种子负映射（用户补充）。同键遮蔽已由 FontsDB 保证。"""
    has_overlay_neg = any(
        r.get("layer") == LAYER_OVERLAY and r.get("kind") == "negative_mapping"
        for r in rows)
    has_overlay_pos = any(
        r.get("layer") == LAYER_OVERLAY and r.get("kind") == "mapping"
        for r in rows)
    out: list[dict] = []
    for row in rows:
        if row.get("layer") == LAYER_OVERLAY:
            out.append(row)
            continue
        if has_overlay_neg and row.get("kind") == "mapping":
            continue
        if has_overlay_pos and row.get("kind") == "negative_mapping":
            continue
        out.append(row)
    return out


def _decorate_map_row(row: dict, matched_name: str) -> dict:
    """映射行加查询侧字段：basis/matched_name + 缺省置信度。"""
    item = dict(row)
    item["basis"] = BASIS_MAPPING
    item["matched_name"] = matched_name
    if item.get("confidence") is None:
        item["confidence"] = DEFAULT_MAPPING_CONFIDENCE
    return item


def _parse_alternates(text) -> list[str]:
    """fonts.alternates 列（JSON 数组文本）→ 字体名列表；空/损坏返回 []。"""
    if not text:
        return []
    try:
        parsed = json.loads(text)
    except (ValueError, TypeError):
        logger.warning("alternates 字段损坏，按无替代链处理: %r", text)
        return []
    if not isinstance(parsed, list):
        return []
    return [str(item).strip() for item in parsed if str(item).strip()]


def _languages_cover(languages_text, lang: str) -> bool:
    """fonts.languages 文本（如 "zh,en" / "zh-Hans, ja"）是否覆盖目标语言；
    未标注语言的字体无法断言覆盖，一律不算（第三级严格限定目标语言库）。"""
    if not languages_text or not lang:
        return False
    for token in re.split(r"[,，;；/、\s]+", str(languages_text)):
        token = token.strip().lower().replace("_", "-")
        if not token:
            continue
        if token == lang or token.split("-")[0] == lang:
            return True
    return False


def _is_installed(info: Optional[dict]) -> bool:
    """本机已安装索引补充校验：seed 行 source 标注为 local_index。"""
    return bool(info) and "local_index" in str((info or {}).get("source") or "")


def _sort_candidates(cands: list[ChainCandidate], *,
                     license_first: bool) -> list[ChainCandidate]:
    """链内排序：第一/二级（层、许可、置信度降序、名）——覆盖层优先；
    第三级（许可、层、置信度降序、名）——"开源优先排序"。"""
    def key(c: ChainCandidate):
        layer = _LAYER_RANK.get(c.layer, 2)
        lic = _LICENSE_RANK.get(c.license_category, 9)
        return ((lic, layer, -c.confidence, c.name) if license_first
                else (layer, lic, -c.confidence, c.name))
    return sorted(cands, key=key)


# ── 查询 API（方案 §5.2） ────────────────────────────────────────

def lookup_jp_font(db: FontsDB, name: str) -> list[dict]:
    """日文字体名 → 中文对位候选列表（三态可区分，不混淆）。

    - ``[]``：表中无此字体（含空名）；
    - 仅含 ``kind="negative_mapping"`` 行（``cn_name`` 为 None）：表中明确
      标记无对位；
    - 含 ``kind="mapping"`` 行：对位候选（附 basis/matched_name/layer/
      confidence 等查询侧字段）。

    查询名先精确命中映射表；未命中时经 fonts 别名表解析简/繁/日写法重试
    （如 ``华康俪金黑`` → 规范名 ``華康儷金黑``）。
    """
    key = (name or "").strip()
    if not key:
        return []
    for spelling in _font_spellings(db, key):
        try:
            rows = db.lookup_jp_cn(spelling, include_negative=True)
        except Exception as exc:
            logger.warning("映射表查询失败，跳过该写法: name=%s, error=%s",
                           spelling, exc)
            continue
        rows = _filter_map_rows(rows)
        if rows:
            return [_decorate_map_row(row, spelling) for row in rows]
    return []


def open_source_alternates(db: FontsDB, font: str) -> list[dict]:
    """字体 → 开源替代候选（``license_category="open_source"`` 的替代链）。

    查库字段优先：先读 ``fonts.alternates``（schema v3 开源替代链）；再以
    Seekladoom 映射表的中文对位作为替代链补充环节（经别名写法正查）。
    每个替代名都经 fonts 表核验许可，非 open_source（含未收录）一律丢弃，
    绝不猜。候选按来源顺序返回（字段链在前，映射链在后，同名去重）；
    无网络调用，本机已安装索引仅作 ``installed`` 标注。
    """
    name = (font or "").strip()
    if not name:
        return []
    results: list[dict] = []
    seen: set[str] = {name.lower()}
    spellings = _font_spellings(db, name)
    try:
        info = db.lookup_font(name)
    except Exception as exc:
        logger.warning("字体记录查询失败，跳过替代链字段: font=%s, error=%s",
                       name, exc)
        info = None

    # 1) 查库字段：fonts.alternates 开源替代链（schema v3）。
    if info:
        from_font = (info.get("canonical_name") or name)
        for alt_name in _parse_alternates(info.get("alternates")):
            if alt_name.lower() in seen:
                continue
            try:
                alt_info = db.lookup_font(alt_name)
            except Exception as exc:
                logger.warning("替代名核验失败，丢弃: alt=%s, error=%s",
                               alt_name, exc)
                continue
            if not alt_info or alt_info.get("license_category") != "open_source":
                continue  # 未收录/许可不符：丢弃，绝不猜
            seen.add(alt_name.lower())
            results.append({
                "name": alt_name,
                "basis": BASIS_OPEN_SOURCE,
                "via": "fonts.alternates",
                "license_category": "open_source",
                "layer": info.get("layer") or LAYER_SEED,
                "confidence": OPEN_SOURCE_ALTERNATE_CONFIDENCE,
                "from_font": from_font,
                "matched_name": alt_name,
                "source": alt_info.get("source"),
                "installed": _is_installed(alt_info),
            })

    # 2) 补充环节：映射表的中文对位（开源者入选）。
    for spelling in spellings:
        try:
            rows = db.lookup_jp_cn(spelling)
        except Exception as exc:
            logger.warning("映射表查询失败，跳过该写法: name=%s, error=%s",
                           spelling, exc)
            continue
        for row in rows:
            cn = (row.get("cn_name") or "").strip()
            if not cn or cn.lower() in seen:
                continue
            try:
                cn_info = db.lookup_font(cn)
            except Exception as exc:
                logger.warning("对位名核验失败，丢弃: cn=%s, error=%s", cn, exc)
                continue
            if not cn_info or cn_info.get("license_category") != "open_source":
                continue
            seen.add(cn.lower())
            results.append({
                "name": cn,
                "basis": BASIS_OPEN_SOURCE,
                "via": "jp_cn_font_map",
                "license_category": "open_source",
                "layer": row.get("layer") or LAYER_SEED,
                "confidence": OPEN_SOURCE_ALTERNATE_CONFIDENCE,
                "from_font": (info.get("canonical_name") or name) if info else name,
                "matched_name": spelling,
                "source": row.get("source") or row.get("source_file"),
                "installed": _is_installed(cn_info),
            })
    return results


# ── 三级链（方案 §5.3） ──────────────────────────────────────────

def _reverse_lookup_jp(db: FontsDB, cn_name: str) -> list[dict]:
    """中→日反向查询（同一张表）：返回装饰后的正映射行（jp 候选）。

    反向命中还要过一遍正向视图：若该 jp 名已被覆盖层否决（正向只剩负
    映射），则不再作为候选——与正查的否决语义一致。
    """
    out: list[dict] = []
    seen_names: set[str] = set()
    for spelling in _font_spellings(db, cn_name):
        try:
            rows = db.lookup_jp_cn_reverse(spelling)
        except Exception as exc:
            logger.warning("反向映射查询失败，跳过该写法: name=%s, error=%s",
                           spelling, exc)
            continue
        for row in rows:
            jp = (row.get("jp_name") or "").strip()
            if not jp or jp.lower() in seen_names:
                continue
            forward = lookup_jp_font(db, jp)
            if not any(r.get("kind") == "mapping" for r in forward):
                continue  # 覆盖层已否决/该名在正向已无正映射
            seen_names.add(jp.lower())
            out.append(_decorate_map_row(row, spelling))
    return out


def resolve_font_chain(db: FontsDB, font_name: str,
                       target_lang: str = "zh") -> ChainResult:
    """三级链解析：对位 → 开源替代 → 同风格类别兜底（方案 §5.3）。

    第一级：``target_lang="zh"`` 正查映射表（日→中）；``"ja"`` 反向查同一
    张表（中→日）；其他目标语言无表可查，跳过（记入 notes）。对位候选须
    许可放行（``ALLOWED_LICENSE_CATEGORIES``）才算可用；不可用（无对位/
    负映射/许可不放行）走第二级：先沿被否对位的开源替代链续链（日→中→
    开源），再查原字体自身替代链。仍无结果走第三级：按
    ``STYLE_CATEGORY_GROUPS`` 同风格类别，仅在目标语言字体库内检索，开源
    优先。全链无果返回 ``unresolved``（candidates 为空，notes 说明各级判
    定），调用方保持原字体名并报告标注未映射——绝不强行编造结果。
    """
    name = (font_name or "").strip()
    lang = (target_lang or "").strip().lower()
    notes: list[str] = []
    candidates: list[ChainCandidate] = []
    if not name:
        notes.append("字体名为空，无法解析映射链。")
        return ChainResult(STATUS_UNRESOLVED, name, lang, candidates, notes)

    # ── 第一级：目标语言对位 ─────────────────────────────────────
    disallowed_counterparts: list[str] = []
    if lang in ("zh", "ja"):
        if lang == "zh":
            entries = lookup_jp_font(db, name)
        else:
            entries = _reverse_lookup_jp(db, name)
        positives = [e for e in entries if e.get("kind") == "mapping"]
        negatives = [e for e in entries if e.get("kind") == "negative_mapping"]
        if negatives:
            notes.append(f"映射表明确标记无对位（negative_mapping）：{name}。")
        elif not positives:
            notes.append(f"映射表中无 {name} 的记录。")
        cand_col = "cn_name" if lang == "zh" else "jp_name"
        seen_names = {name.lower()}
        for row in sorted(
                positives,
                key=lambda r: _LAYER_RANK.get(r.get("layer") or LAYER_SEED, 2)):
            cand_name = (row.get(cand_col) or "").strip()
            if not cand_name or cand_name.lower() in seen_names:
                continue
            seen_names.add(cand_name.lower())
            try:
                cand_info = db.lookup_font(cand_name)
            except Exception as exc:
                logger.warning("对位许可核验失败，按 unknown 从严: name=%s, error=%s",
                               cand_name, exc)
                cand_info = None
            license_category = (cand_info or {}).get("license_category") or "unknown"
            direction = "日→中" if lang == "zh" else "中→日"
            if license_category in ALLOWED_LICENSE_CATEGORIES:
                candidates.append(ChainCandidate(
                    name=cand_name,
                    basis=BASIS_MAPPING,
                    level=1,
                    layer=row.get("layer") or LAYER_SEED,
                    license_category=license_category,
                    confidence=float(row.get("confidence")
                                     or DEFAULT_MAPPING_CONFIDENCE),
                    reason=(f"第一级目标语言对位（{direction} 映射表命中，"
                            f"匹配写法 {row.get('matched_name')}）"),
                    source=row.get("source") or row.get("source_file"),
                ))
            else:
                lic_desc = (license_category if cand_info is not None
                            else "unknown（未收录）")
                disallowed_counterparts.append(cand_name)
                notes.append(
                    f"对位候选 {cand_name} 许可类别 {lic_desc} 不满足放行条件，"
                    f"转入开源替代层。")
        if candidates:
            return ChainResult(STATUS_RESOLVED, name, lang,
                               _sort_candidates(candidates, license_first=False),
                               notes)
    else:
        notes.append(f"目标语言 {lang} 无映射表查询路径（表仅覆盖 ja↔zh），"
                     f"跳过第一级对位。")

    # ── 第二级：开源替代（先续被否对位的链，再查原字体） ─────────
    seen_alts = {name.lower()}
    for from_font in disallowed_counterparts + [name]:
        for alt in open_source_alternates(db, from_font):
            alt_name = (alt.get("name") or "").strip()
            if not alt_name or alt_name.lower() in seen_alts:
                continue
            seen_alts.add(alt_name.lower())
            candidates.append(ChainCandidate(
                name=alt_name,
                basis=BASIS_OPEN_SOURCE,
                level=2,
                layer=alt.get("layer") or LAYER_SEED,
                license_category=alt.get("license_category") or "open_source",
                confidence=float(alt.get("confidence")
                                 or OPEN_SOURCE_ALTERNATE_CONFIDENCE),
                reason=(f"第二级开源替代（经 {from_font} 的替代链 "
                        f"{alt.get('via')}）"),
                source=alt.get("source"),
                installed=alt.get("installed"),
            ))
    if candidates:
        return ChainResult(STATUS_RESOLVED, name, lang,
                           _sort_candidates(candidates, license_first=False),
                           notes)

    # ── 第三级：同风格类别兜底（仅目标语言字体库，开源优先） ─────
    try:
        info = db.lookup_font(name)
    except Exception as exc:
        logger.warning("字体记录查询失败，同风格匹配不可用: name=%s, error=%s",
                       name, exc)
        info = None
    category = (info or {}).get("category")
    if not category:
        notes.append(f"字体 {name} 无类别信息（未收录或未标注 category），"
                     f"同风格匹配不可用。")
        return ChainResult(STATUS_UNRESOLVED, name, lang, candidates, notes)
    group = category_style_group(category)
    if group is None:
        notes.append(f"类别 {category} 不在同风格类别映射表内，"
                     f"同风格匹配不可用。")
        return ChainResult(STATUS_UNRESOLVED, name, lang, candidates, notes)
    group_tokens = {t.lower() for t in group}
    own_names = {name.lower(),
                 ((info or {}).get("canonical_name") or "").strip().lower()}
    matches: list[tuple[dict, str]] = []
    try:
        all_fonts = db.list_fonts()
    except Exception as exc:
        logger.warning("字体库批量检索失败，同风格匹配不可用: error=%s", exc)
        all_fonts = []
    for row in all_fonts:
        row_name = (row.get("canonical_name") or "").strip()
        if not row_name or row_name.lower() in own_names:
            continue
        if (row.get("category") or "").strip().lower() not in group_tokens:
            continue
        if not _languages_cover(row.get("languages"), lang):
            continue  # 严格限定目标语言字体库
        license_category = row.get("license_category") or "unknown"
        if license_category not in ALLOWED_LICENSE_CATEGORIES:
            continue
        matches.append((row, license_category))
    if not matches:
        notes.append(f"目标语言 {lang} 字体库中无同风格（{category}）的"
                     f"可用字体。")
        return ChainResult(STATUS_UNRESOLVED, name, lang, candidates, notes)
    for row, license_category in matches:
        candidates.append(ChainCandidate(
            name=row["canonical_name"],
            basis=BASIS_CATEGORY_MATCH,
            level=3,
            layer=row.get("layer") or LAYER_SEED,
            license_category=license_category,
            confidence=CATEGORY_MATCH_CONFIDENCE,
            reason=(f"第三级同风格类别匹配（{category} ↔ "
                    f"{'/'.join(group)}），目标语言 {lang} 字体库内检索"),
            source=row.get("source"),
            installed=_is_installed(row),
        ))
    return ChainResult(STATUS_RESOLVED, name, lang,
                       _sort_candidates(candidates, license_first=True), notes)
