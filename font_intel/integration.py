# font_intel/integration.py
"""字体合规集成层（T1.7/T1.8）：ASS 头部 Style 行合规闸门 + 合规报告生成。

把 T1.6 的 ``decide()`` 接到出片链路上，两条铁律：

- **绝不阻塞出片**：``interactive=False``（阶段 4 无交互 UI）时 ``prompt``
  动作降级为"保留原字体名 + 记录决策"；本模块任何异常都被吞掉并回落
  原始头部，绝不因合规中断任务链。无网络调用。
- **用户模板不重写**：``template_mode=True`` 只收集决策、不修改任何字节
  （替换仅作用于程序内嵌的默认头部）。

替换语义（按 ``decide()`` 动作）：
``allow``/``report_only``/``granted`` → 保留原名（记录）；``prompt`` →
保留原名 + 记录（reason 注明非交互降级）；``replace_auto`` 且替代链非空
→ 替换为 ``alternatives[0]``。``replace_auto`` 有置信度门槛，不达标已被
``decide()`` 降级为 ``prompt``。

字体名取自每条 ``Style:`` 行第 2 列，**去重后**逐字体出一条决策（同一
字体跨多条 Style 行只决策一次，多个 style 名记录在 ``styles`` 列表下）。

报告（``write_compliance_report``）与 ASS 同目录写
``<stem>_compliance_report.md`` 与 ``.json``；md 固定含免责声明原文
"本报告不构成法律意见，商用前请自行核实授权条款"；官方链接一律复用
``sanitize_url`` 消毒；无决策（合规关闭）时不生成任何文件。
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, List, Optional

from font_intel.compliance import (
    HARD_RED_LINE,
    USAGE_SCENES,
    ComplianceRules,
    decide,
    sanitize_url,
)
from font_intel.fonts_db import FontsDB

logger = logging.getLogger(__name__)

# 免责声明原文（报告 md/json 固定携带，不得改写）。
DISCLAIMER_TEXT = "本报告不构成法律意见，商用前请自行核实授权条款"

_PROMPT_NOTE_NON_INTERACTIVE = (
    "非交互模式（interactive=False）：prompt 降级为保留原字体名并记录决策，"
    "不阻断出片。")
_PROMPT_NOTE_INTERACTIVE = (
    "交互模式：prompt 待用户确认，先保留原字体名并记录决策。")

# Style 行解析：prefix("Style: ") + 样式名 + 逗号 + 字体名(第 2 列) + 其余列。
# 只匹配 Style: 行（Format: Name, Fontname, ... 不匹配），逐字段保留原文，
# 替换时仅重写字体名列，其余字节不动。
_STYLE_LINE_RE = re.compile(
    r"^(?P<prefix>\s*Style:\s*)(?P<style>[^,]*),(?P<font>[^,]*)(?P<rest>,.*)$")


# ── 配置 + 惰性闸门 ──────────────────────────────────────────────

@dataclass
class FontComplianceConfig:
    """styling 合规闸门配置（生成器 ``font_compliance`` 参数）。

    ``enabled=False`` 或不传时生成器全部路径与旧版本逐字节一致（不开库、
    不决策、零报告文件）。``db_path`` 传 ``None`` 时回落 FontsDB 默认库。
    """

    enabled: bool
    scene: str = "personal"
    rules: Optional[ComplianceRules] = None
    db_path: Optional[str] = None
    interactive: bool = False
    confidence: Optional[float] = None
    # 替代字体查询（replace_auto 生效条件之一）；缺省无替代链。
    alternatives_provider: Optional[Callable[[str], list[str]]] = None
    # resolve() 缓存：同一配置只建一个 _Gate（字体库连接随闸门复用）。
    _gate: Optional["_Gate"] = field(
        default=None, init=False, repr=False, compare=False)

    def resolve(self) -> "_Gate":
        """取（并缓存）本配置的决策闸门；字体库惰性打开。"""
        if self._gate is None:
            self._gate = _Gate(self)
        return self._gate


class _Gate:
    """单配置的决策入口：惰性 FontsDB + 规则解析 + 场景归一。

    打开失败（文件损坏/权限/非库文件等）→ ``db=None`` + warning，全部
    字体按 unknown 从严处理（优雅降级，不抛异常）。
    """

    def __init__(self, config: FontComplianceConfig) -> None:
        self._config = config
        self._db: Optional[FontsDB] = None
        self._db_tried = False
        scene = str(config.scene or "personal").strip()
        if scene not in USAGE_SCENES:
            logger.warning(
                "font_compliance.scene 非法，回落 personal: %r", config.scene)
            scene = "personal"
        self.scene = scene
        # 无 rules 用默认矩阵（load_or_default(None) 永不抛异常）。
        self.rules = (config.rules if config.rules is not None
                      else ComplianceRules.load_or_default(None))

    @property
    def db(self) -> Optional[FontsDB]:
        if not self._db_tried:
            self._db_tried = True
            try:
                self._db = FontsDB(self._config.db_path)
            except Exception as exc:
                logger.warning(
                    "fonts.db 打开失败，全部按 unknown 从严处理: path=%r, error=%s",
                    self._config.db_path, exc)
                self._db = None
        return self._db

    def decide_for(self, font_name: str):
        return decide(
            font_name,
            scene=self.scene,
            db=self.db,
            confidence=self._config.confidence,
            rules=self.rules,
            alternatives_provider=self._config.alternatives_provider,
        )


# ── 头部合规闸门 ─────────────────────────────────────────────────

def apply_compliance_to_header(
    header_text: str,
    config: Optional[FontComplianceConfig],
    *,
    template_mode: bool = False,
) -> tuple[str, List[dict]]:
    """对 ASS 头部跑字体合规闸门，返回 (新头部, 决策列表)。

    - ``config`` 为 None 或 ``enabled=False`` → 原文原样返回、零决策；
    - 每条 ``Style:`` 行第 2 列字体名去重后逐个 ``decide()``；
    - 非模板模式按动作替换字体名（见模块 docstring）；``template_mode=
      True`` 只收集决策、不修改任何字节；
    - 决策 dict 字段：style/styles/font_name/final_font/replaced/action/
      license_category/scene/reason/official_url/alternatives/granted/
      confidence。本函数保证不抛异常：任何异常回落原头部 + 空决策。
    """
    header_text = header_text or ""
    if config is None or not getattr(config, "enabled", False):
        return header_text, []
    try:
        return _apply_locked(header_text, config, bool(template_mode))
    except Exception as exc:  # 合规永不中断出片
        logger.warning("字体合规闸门异常，保持原头部输出: %s", exc)
        return header_text, []


def _apply_locked(
    header_text: str,
    config: FontComplianceConfig,
    template_mode: bool,
) -> tuple[str, List[dict]]:
    gate = config.resolve()
    lines = header_text.split("\n")
    parsed: List[tuple] = []          # [(行号, match, 字体名)]
    styles_by_font: dict = {}
    order: List[str] = []             # 首次出现序（去重）
    for idx, line in enumerate(lines):
        m = _STYLE_LINE_RE.match(line)
        if m is None:
            continue
        font_name = m.group("font").strip()
        if not font_name:
            continue
        style_name = m.group("style").strip()
        styles_by_font.setdefault(font_name, [])
        if style_name:
            styles_by_font[font_name].append(style_name)
        parsed.append((idx, m, font_name))
        if font_name not in order:
            order.append(font_name)

    decisions: List[dict] = []
    final_by_font: dict = {}
    for font_name in order:
        decision = gate.decide_for(font_name)
        final_font, reason = _resolve_final_font(decision, config)
        final_by_font[font_name] = final_font
        styles = list(styles_by_font.get(font_name) or [])
        decisions.append({
            "style": styles[0] if styles else "",
            "styles": styles,
            "font_name": font_name,
            "final_font": final_font,
            "replaced": final_font != font_name,
            "action": decision.action,
            "license_category": decision.license_category,
            "scene": gate.scene,
            "reason": reason,
            "official_url": decision.official_url,
            "alternatives": list(decision.alternatives),
            "granted": bool(decision.granted),
            "confidence": config.confidence,
        })

    if template_mode:
        # 用户模板不重写：只收集决策，不修改任何字节。
        return header_text, decisions

    for idx, m, font_name in parsed:
        final_font = final_by_font[font_name]
        if final_font != font_name:
            lines[idx] = (m.group("prefix") + m.group("style") + ","
                          + final_font + m.group("rest"))
    return "\n".join(lines), decisions


def _resolve_final_font(decision, config: FontComplianceConfig) -> tuple:
    """按动作决定最终字体名；prompt 一律保留原名（非交互降级，见铁律）。"""
    reason = decision.reason
    if decision.action == "replace_auto" and decision.alternatives:
        return decision.alternatives[0], reason
    if decision.action == "prompt":
        note = (_PROMPT_NOTE_INTERACTIVE if config.interactive
                else _PROMPT_NOTE_NON_INTERACTIVE)
        return decision.font_name, reason + note
    # allow / report_only / 已获授权 / 无替代链兜底：保留原名。
    return decision.font_name, reason


# ── 合规报告 ─────────────────────────────────────────────────────

def _normalize_decision(d: dict) -> dict:
    """决策 dict → 报告记录（官方链接复用 sanitize_url 消毒）。"""
    styles = [str(s) for s in (d.get("styles") or [])]
    single = str(d.get("style") or "")
    if single and single not in styles:
        styles.insert(0, single)
    return {
        "style": single,
        "styles": styles,
        "font_name": str(d.get("font_name") or ""),
        "final_font": str(d.get("final_font") or d.get("font_name") or ""),
        "replaced": bool(d.get("replaced")),
        "action": str(d.get("action") or ""),
        "license_category": str(d.get("license_category") or "unknown"),
        "scene": str(d.get("scene") or ""),
        "reason": str(d.get("reason") or ""),
        "official_url": sanitize_url(d.get("official_url")),
        "alternatives": [str(a) for a in (d.get("alternatives") or [])],
        "granted": bool(d.get("granted")),
    }


def _render_markdown(payload: dict) -> str:
    lines: List[str] = [
        "# 字体合规报告",
        "",
        f"- ASS 文件: {payload['ass_path']}",
        f"- 生成时间: {payload['generated_at']} (UTC)",
        f"- 决策字体数: {len(payload['decisions'])}",
        "",
        f"> {DISCLAIMER_TEXT}",
        ">",
        f"> {HARD_RED_LINE}",
        "",
    ]
    for rec in payload["decisions"]:
        lines.extend([
            f"## {rec['font_name']}",
            "",
            f"- 涉及样式: {', '.join(rec['styles']) or '（无）'}",
            f"- 许可类别: {rec['license_category']}",
            f"- 决策: {rec['action']}"
            + (f"（最终字体 {rec['final_font']}）" if rec["replaced"] else ""),
            f"- 理由: {rec['reason']}",
            f"- 官方链接: {rec['official_url'] or '无（未收录或已按硬红线过滤）'}",
            "",
        ])
    meta = payload.get("meta") or {}
    if meta:
        lines.extend(["## 附加信息", ""])
        for key in sorted(meta):
            lines.append(f"- {key}: {meta[key]}")
        lines.append("")
    return "\n".join(lines)


def write_compliance_report(
    decisions: Optional[List[dict]],
    ass_path,
    *,
    meta: Optional[dict] = None,
) -> tuple:
    """与 ASS 同目录写 ``<stem>_compliance_report.md`` 与 ``.json``。

    返回 (md 路径, json 路径)；``decisions`` 为空（合规关闭/无决策）时
    **不生成任何文件**，返回 (None, None)。
    """
    if not decisions:
        return None, None
    ass_path = Path(ass_path)
    md_path = ass_path.with_name(f"{ass_path.stem}_compliance_report.md")
    json_path = ass_path.with_name(f"{ass_path.stem}_compliance_report.json")
    payload = {
        "disclaimer": DISCLAIMER_TEXT,
        "hard_red_line": HARD_RED_LINE,
        "ass_path": str(ass_path),
        "generated_at": datetime.now(timezone.utc).isoformat(
            timespec="seconds"),
        "meta": dict(meta or {}),
        "decisions": [_normalize_decision(d) for d in decisions],
    }
    md_path.write_text(_render_markdown(payload), encoding="utf-8")
    json_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8")
    return md_path, json_path
