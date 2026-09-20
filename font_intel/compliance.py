# font_intel/compliance.py
"""合规决策引擎（T1.6）：许可类别 × 使用场景 → 动作。

维度与枚举（与 fonts.db license_rules 的 CHECK 约束保持一致）：
- 许可类别 ``open_source``（OFL/Apache/MIT 等开源）｜``free_commercial``
  （免费商用非开源）｜``commercial_paid``（商用需授权）｜``unknown``；
- 使用场景 ``personal`` / ``publish`` / ``commercial``；
- 动作 ``allow`` / ``prompt`` / ``replace_auto`` / ``report_only``。

默认规则矩阵 ``DEFAULT_RULES`` 是不变量：开源与免费商用全场景放行；
商用需授权全场景 ``prompt``（弹窗确认）；``unknown`` 默认从严（等同
``commercial_paid``），``ComplianceRules.strict_unknown=False`` 可放宽为
``report_only``。规则支持按 (license_category, scene) 单元格覆盖。

硬红线（写死模块级，不可配置）：不提供、不链接、不缓存任何破解版或
非官方渠道字体文件；输出到决策/报告的 URL 一律经 ``sanitize_url`` 过滤，
且 ``decide()`` 的 ``official_url`` 只能来自 fonts.db 的 official_url 字段。

决策永不因数据问题抛异常：字体库查询失败按 ``unknown`` 从严处理并记
warning（优雅降级）。本期只做 JSON 配置层，GUI/CLI 接线为后续任务。
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Callable, Optional

from font_intel.fonts_db import FontsDB

logger = logging.getLogger(__name__)

LICENSE_CATEGORIES = ("open_source", "free_commercial", "commercial_paid", "unknown")
USAGE_SCENES = ("personal", "publish", "commercial")
RULE_ACTIONS = ("allow", "prompt", "replace_auto", "report_only")

# replace_auto 仅对高置信度识别结果生效；低于门槛一律降级 prompt。
REPLACE_AUTO_MIN_CONFIDENCE = 0.85

HARD_RED_LINE = (
    "硬红线：本系统不提供、不链接、不缓存任何破解版或非官方渠道的字体文件；"
    "对商用需授权字体仅输出厂商官方授权/购买页面链接。"
)

# 盗版/破解渠道 URL 标记（匹配大小写不敏感）；命中即视为非法渠道链接。
BLOCKED_URL_MARKERS = (
    "crack",
    "keygen",
    "torrent",
    "pirat",
    "warez",
    "nulled",
    "破解",
    "盗版",
    "注册机",
)

RuleKey = tuple  # (license_category, usage_scene)


# ── 默认规则矩阵（不变量） ────────────────────────────────────────

def _build_default_rules() -> dict[RuleKey, str]:
    matrix: dict[RuleKey, str] = {}
    for scene in USAGE_SCENES:
        for cat in ("open_source", "free_commercial"):
            matrix[(cat, scene)] = "allow"
        # unknown 默认从严，与 commercial_paid 同级（strict_unknown 可放宽）。
        for cat in ("commercial_paid", "unknown"):
            matrix[(cat, scene)] = "prompt"
    return matrix


DEFAULT_RULES: dict[RuleKey, str] = _build_default_rules()


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


def _validate_cell(license_category: str, usage_scene: str, action: str) -> None:
    """规则单元格合法性：类别/场景/动作都必须在枚举内，越界即拒。"""
    if license_category not in LICENSE_CATEGORIES:
        raise ValueError(f"invalid license_category: {license_category!r}")
    if usage_scene not in USAGE_SCENES:
        raise ValueError(f"invalid usage_scene: {usage_scene!r}")
    if action not in RULE_ACTIONS:
        raise ValueError(f"invalid rule action: {action!r}")


def default_rules_path() -> str:
    """规则配置默认路径：``$XDG_CONFIG_HOME/video_subtitle_ocr/compliance_rules.json``
    （未设置 XDG_CONFIG_HOME 时回落 ``~/.config/...``）。"""
    config_home = os.environ.get("XDG_CONFIG_HOME", "").strip() or os.path.join(
        os.path.expanduser("~"), ".config"
    )
    return os.path.join(config_home, "video_subtitle_ocr", "compliance_rules.json")


# ── 规则持久化 ───────────────────────────────────────────────────

@dataclass
class ComplianceRules:
    """用户可配置的合规决策规则（默认矩阵 + 单元格覆盖 + unknown 策略）。"""

    strict_unknown: bool = True
    overrides: dict[RuleKey, str] = field(default_factory=dict)

    def set_override(self, license_category: str, usage_scene: str, action: str) -> None:
        """按 (license_category, scene) 单键覆盖；动作必须属于四个枚举值。"""
        _validate_cell(license_category, usage_scene, action)
        self.overrides[(license_category, usage_scene)] = action

    def clear_override(self, license_category: str, usage_scene: str) -> None:
        self.overrides.pop((license_category, usage_scene), None)

    def action_for(self, license_category: str, usage_scene: str) -> str:
        """动作解析优先级：单元格覆盖 > unknown 宽松策略 > 默认矩阵。"""
        overridden = self.overrides.get((license_category, usage_scene))
        if overridden is not None:
            return overridden
        if license_category == "unknown" and not self.strict_unknown:
            return "report_only"
        return DEFAULT_RULES[(license_category, usage_scene)]

    def to_dict(self) -> dict:
        """序列化为 JSON 友好结构（元组键转为 嵌套 category→scene→action）。"""
        nested: dict[str, dict[str, str]] = {}
        for (cat, scene), action in sorted(self.overrides.items()):
            nested.setdefault(cat, {})[scene] = action
        return {"strict_unknown": self.strict_unknown, "overrides": nested}

    @classmethod
    def from_dict(cls, data: dict) -> "ComplianceRules":
        """反序列化；任何非法字段（未知键值/非法动作）直接 ValueError 拒绝。"""
        if not isinstance(data, dict):
            raise ValueError(f"ComplianceRules expects a dict, got {type(data)!r}")
        rules = cls(strict_unknown=bool(data.get("strict_unknown", True)))
        overrides = data.get("overrides") or {}
        if not isinstance(overrides, dict):
            raise ValueError(f"overrides expects a dict, got {type(overrides)!r}")
        for cat, scenes in overrides.items():
            if not isinstance(scenes, dict):
                raise ValueError(f"overrides[{cat!r}] expects a dict, got {type(scenes)!r}")
            for scene, action in scenes.items():
                rules.set_override(str(cat), str(scene), str(action))
        return rules

    def save_json(self, path: str) -> None:
        parent = os.path.dirname(os.path.abspath(path))
        os.makedirs(parent, exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(self.to_dict(), fh, ensure_ascii=False, indent=2)

    @classmethod
    def load_json(cls, path: str) -> "ComplianceRules":
        """从 JSON 载入；文件缺失/损坏/结构非法时返回默认规则并记 warning
        （配置层损坏不阻断主流程，由用户在设置中重新修正）。"""
        try:
            with open(path, "r", encoding="utf-8") as fh:
                data = json.load(fh)
            return cls.from_dict(data)
        except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
            logger.warning(
                "compliance rules 配置不可用，回落默认规则: path=%s, error=%s", path, exc
            )
            return cls()

    @classmethod
    def load_or_default(cls, path: Optional[str]) -> "ComplianceRules":
        """path 为空或文件不可用时返回默认规则（永不抛异常）。"""
        if not path:
            return cls()
        return cls.load_json(path)


# ── 已获授权放行（user_overrides 层） ─────────────────────────────

def _overrides_conn(db: FontsDB):
    # fonts_db 未暴露 user_overrides 写入口（运行时确认记录不属于种子/覆盖
    # 情报），合规引擎经其 sqlite 连接直接读写该表。
    conn = getattr(db, "_conn", None)
    if conn is None:
        raise ValueError("db must be a FontsDB instance with a sqlite connection")
    return conn


def grant_license(font_name: str, db: FontsDB) -> None:
    """记录"用户已确认获得授权"（user_overrides：approved=1，patch 带
    license_granted 与时间戳）；同名字体旧记录先删后插，保证单行有效。"""
    name = (font_name or "").strip()
    if not name:
        raise ValueError("grant_license requires a non-empty font_name")
    conn = _overrides_conn(db)
    with conn:
        conn.execute(
            "DELETE FROM user_overrides WHERE target_table='fonts' AND target_key=?",
            (name,),
        )
        conn.execute(
            "INSERT INTO user_overrides (target_table, target_key, patch_json,"
            " approved, created_at) VALUES ('fonts', ?, ?, 1, ?)",
            (name,
             json.dumps({"license_granted": True, "granted_at": _now_iso()},
                        ensure_ascii=False),
             _now_iso()),
        )
    logger.info("license grant recorded: font=%s", name)


def revoke_license(font_name: str, db: FontsDB) -> None:
    """撤销已获授权记录（approved 置 0，保留确认历史供审计）。"""
    name = (font_name or "").strip()
    if not name:
        raise ValueError("revoke_license requires a non-empty font_name")
    conn = _overrides_conn(db)
    with conn:
        conn.execute(
            "UPDATE user_overrides SET approved=0 "
            "WHERE target_table='fonts' AND target_key=?",
            (name,),
        )
    logger.info("license grant revoked: font=%s", name)


def has_granted_license(font_name: str, db: Optional[FontsDB]) -> bool:
    """是否已获授权放行；查询失败视为未授权（决策侧再兜底，不抛异常）。"""
    name = (font_name or "").strip()
    if db is None or not name:
        return False
    try:
        row = _overrides_conn(db).execute(
            "SELECT 1 FROM user_overrides WHERE target_table='fonts'"
            " AND target_key=? AND approved=1 LIMIT 1",
            (name,),
        ).fetchone()
        return row is not None
    except Exception as exc:
        logger.warning("授权记录查询失败，按未授权处理: font=%s, error=%s", name, exc)
        return False


# ── 硬红线：URL 消毒 ─────────────────────────────────────────────

def sanitize_url(url: Optional[str]) -> str:
    """过滤盗版/破解渠道链接：命中 ``BLOCKED_URL_MARKERS``（大小写不敏感）
    一律返回空串并 warning；其余原样返回。报告层必须复用本函数。"""
    if not url:
        return ""
    text = str(url)
    lowered = text.lower()
    for marker in BLOCKED_URL_MARKERS:
        if marker.lower() in lowered:
            logger.warning("命中非官方/破解渠道标记，已丢弃链接: marker=%s", marker)
            return ""
    return text


# ── 决策对象 ─────────────────────────────────────────────────────

@dataclass
class ComplianceDecision:
    """单字体的合规决策结果；仅含可 JSON 序列化字段。"""

    font_name: str
    license_category: str
    scene: str
    action: str
    reason: str
    official_url: str = ""
    alternatives: list[str] = field(default_factory=list)
    granted: bool = False

    def to_dict(self) -> dict:
        return asdict(self)


# ── 决策引擎 ─────────────────────────────────────────────────────

def decide(
    font_name: str,
    *,
    scene: str = "personal",
    db: Optional[FontsDB] = None,
    confidence: Optional[float] = None,
    rules: Optional[ComplianceRules] = None,
    alternatives_provider: Optional[Callable[[str], list[str]]] = None,
) -> ComplianceDecision:
    """按 类别×场景 规则表产出单字体合规决策（不因数据问题抛异常）。

    优先级：已获授权放行 > 单元格覆盖/默认矩阵 > replace_auto 置信度门槛
    （不达标降级 prompt）。``official_url`` 只来自 fonts.db 的 official_url
    字段且必经 ``sanitize_url`` 消毒；许可类别查询失败按 unknown 从严。
    """
    name = (font_name or "").strip()
    if scene not in USAGE_SCENES:
        raise ValueError(f"invalid usage_scene: {scene!r}")
    active_rules = rules if rules is not None else ComplianceRules()

    # 1) 许可类别：db 命中取 license_category，未命中/失败按 unknown 从严。
    category = "unknown"
    category_source = "not_in_db"
    official_url = ""
    if db is not None:
        try:
            info = db.lookup_font(name)
        except Exception as exc:
            logger.warning("字体库查询失败，按 unknown 从严处理: font=%s, error=%s",
                           name, exc)
            info = None
        if info:
            raw_category = info.get("license_category") or "unknown"
            category = raw_category if raw_category in LICENSE_CATEGORIES else "unknown"
            category_source = "db"
            # 硬红线：official_url 只能来自 db 字段，且必经消毒。
            official_url = sanitize_url(info.get("official_url"))

    # 2) 已获授权放行：命中直接 allow，优先于一切规则。
    if has_granted_license(name, db):
        return ComplianceDecision(
            font_name=name,
            license_category=category,
            scene=scene,
            action="allow",
            reason=f"用户已确认获得授权（user_overrides 记录），直接放行；"
                   f"许可类别 {category}（{'来自字体库' if category_source == 'db' else '未收录，从严标注 unknown'}）。",
            official_url=official_url,
            granted=True,
        )

    # 3) 规则解析：单元格覆盖 > unknown 宽松策略 > 默认矩阵。
    action = active_rules.action_for(category, scene)
    if active_rules.overrides.get((category, scene)) is not None:
        reason = (f"规则 {category}×{scene} 命中用户单元格覆盖 → {action}；"
                  f"许可类别 {'来自字体库' if category_source == 'db' else '未在字体库命中'}。")
    elif category == "unknown" and not active_rules.strict_unknown:
        reason = (f"字体未在本地字体库命中，strict_unknown=False：unknown×{scene}"
                  f" → {action}（仅记录不阻断）。")
    elif category == "unknown":
        reason = (f"字体未在本地字体库命中，按 unknown 从严策略（等同 commercial_paid）："
                  f"unknown×{scene} → {action}。")
    else:
        reason = f"许可类别 {category} 来自字体库，规则 {category}×{scene} → {action}。"

    # 4) replace_auto 门槛：仅高置信度且替代链非空时生效，否则降级 prompt。
    alternatives: list[str] = []
    if action == "replace_auto":
        if alternatives_provider is not None:
            try:
                alternatives = [str(a) for a in (alternatives_provider(name) or [])]
            except Exception as exc:
                logger.warning("替代字体查询失败，按空替代链处理: font=%s, error=%s",
                               name, exc)
                alternatives = []
        if confidence is None or float(confidence) < REPLACE_AUTO_MIN_CONFIDENCE:
            action = "prompt"
            reason += (f"replace_auto 仅对识别置信度 ≥ {REPLACE_AUTO_MIN_CONFIDENCE} 生效，"
                       f"当前置信度 {confidence}，降级 prompt。")
        elif not alternatives:
            action = "prompt"
            reason += "replace_auto 需要非空替代字体链，未取得可用替代，降级 prompt。"
        else:
            reason += f"识别置信度 {confidence} 达标，自动替换为替代字体。"

    return ComplianceDecision(
        font_name=name,
        license_category=category,
        scene=scene,
        action=action,
        reason=reason,
        official_url=official_url,
        alternatives=alternatives if action == "replace_auto" else [],
    )
