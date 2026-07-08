# core/subtitle_llm_polish.py
"""可选：调用 OpenAI 兼容接口（DeepSeek）批量校对 OCR 字幕文本。

v2.0: 基于 llm_subtitle_optimizer 的 LLM 基础设施重构。
  - openai 库客户端 + tenacity 指数退避重试
  - Agent Loop 自验证（键匹配 + 相似度阈值）
  - 提示词从 Markdown 文件加载，支持模板变量
"""
from __future__ import annotations

import difflib
import json
import logging
import re
import ssl
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

from core.llm_client import call_llm
from core.llm_prompts import get_prompt
from core.text_utils import count_words

logger = logging.getLogger(__name__)


def _short_text(s: str, max_len: int = 96) -> str:
    t = (s or "").replace("\n", "\\n").replace("\r", "")
    if len(t) <= max_len:
        return t
    return t[: max(0, max_len - 1)] + "…"


# OCR 误输出为「可见转义」的小写 n/r/t；不含 ASS 强制换行的「\N」（大写 N）。
_LIT_ESC_NRT_RE = re.compile(r"\\[nrt]")


def _normalize_space_symbols(s: str) -> str:
    """将字面量 \\n/\\r/\\t 及不应出现在单行里的换行、制表符等规范为单个半角空格。"""
    if not s:
        return s
    t = _LIT_ESC_NRT_RE.sub(" ", s)
    t = t.replace("\r\n", " ").replace("\r", " ").replace("\n", " ").replace("\t", " ")
    t = t.replace("\u00a0", " ").replace("\u3000", " ")
    t = re.sub(r" {2,}", " ", t)
    return t


DEFAULT_DEEPSEEK_BASE = "https://api.deepseek.com"
# DeepSeek 当前主推 deepseek-v4-flash / deepseek-v4-pro，旧版 id 逐步弃用。
DEFAULT_DEEPSEEK_MODEL = "deepseek-v4-flash"
DEEPSEEK_CHAT_MODEL_CANDIDATES_V4 = ("deepseek-v4-flash", "deepseek-v4-pro")
DEEPSEEK_CHAT_MODEL_LEGACY_FALLBACK = ("deepseek-chat", "deepseek-reasoner", "deepseek-coder")
DEFAULT_OPENAI_BASE = "https://api.openai.com"

CancelCheck = Callable[[], bool]
BatchCb = Callable[[int, int], None]
LogLine = Callable[[str], None]


@dataclass
class SubtitlePolisherConfig:
    api_key: str
    api_base_url: str = DEFAULT_DEEPSEEK_BASE
    model: str = DEFAULT_DEEPSEEK_MODEL
    batch_size: int = 40
    text_polish_enabled: bool = True
    strategy_review_enabled: bool = False
    strategy_max_iterations: int = 3
    fragment_merge_enabled: bool = False
    log_line: Optional[LogLine] = None


def _cfg_log(cfg: SubtitlePolisherConfig, msg: str) -> None:
    if not msg:
        return
    fn = cfg.log_line
    if fn is None:
        return
    try:
        fn(msg)
    except Exception:
        pass


def deepseek_merge_fragment_text(
    cfg: SubtitlePolisherConfig,
    candidates: Sequence[str],
    *,
    cancel_check: CancelCheck = lambda: False,
    timeout_sec: float = 90.0,
) -> Optional[str]:
    """
    Given multiple candidate subtitle strings that belong to the same moment,
    let the model pick/produce the most complete and accurate final text.
    Returns merged text, or None to indicate skipping/failure.
    """
    if not cfg.api_key or not cfg.api_key.strip():
        return None
    if cancel_check():
        return None
    cands = [str(x).replace("\r\n", "\n").replace("\r", "\n") for x in (candidates or []) if str(x).strip()]
    if len(cands) < 2:
        return cands[0] if cands else None

    user_json = json.dumps({"candidates": cands}, ensure_ascii=False)
    user_msg = (
        "以下是同一位置、时间上相邻的字幕碎片（可能因 OCR 抖动导致拆成多条）。\n"
        "请输出一条“最完整、最准确”的最终字幕文本。\n"
        "要求：\n"
        "1) 只纠正明显错别字/标点/空格；不要扩写、不要改写语气；不要新增原文没有的信息。\n"
        "2) 若候选之间存在前缀/后缀补全关系，优先选择信息更完整的一条。\n"
        "3) 若文本中出现 OCR 噪声式的「反斜杠 + 小写 n/r/t」（看似 \\n \\r \\t）或多余的真实换行/制表符，"
        "请合并为一句内可读文本：统一用单个标准半角空格分隔；不要保留字面量转义串；"
        "ASS 强制换行用的「反斜杠 + 大写 N」（\\N）可保留。\n"
        "4) 只输出纯 JSON：{\"text\":\"...\"}，不要 Markdown，不要其它文字。\n\n"
        + user_json
    )
    try:
        raw = _post_chat(
            cfg,
            [
                {
                    "role": "system",
                    "content": get_prompt("fragment_merge"),
                },
                {"role": "user", "content": user_msg},
            ],
            temperature=0.2,
            timeout_sec=timeout_sec,
        )
        data = json.loads(_unwrap_json_fence(raw))
        text = data.get("text")
        if text is None:
            return None
        merged = _normalize_space_symbols(str(text))
        head = min(4, len(cands))
        prev = " | ".join(_short_text(c, 72) for c in cands[:head])
        if len(cands) > head:
            prev += f" …(共{len(cands)}条候选)"
        _cfg_log(
            cfg,
            "[碎片合并] 候选 → 合并结果：\n"
            f"  候选：{prev}\n"
            f"  结果：{_short_text(merged, 220)}",
        )
        return merged
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, ValueError, KeyError, RuntimeError) as e:
        logger.warning("[subtitle_llm_polish] fragment merge failed: %s", e, exc_info=logger.isEnabledFor(logging.DEBUG))
        return None


def fetch_openai_compatible_model_ids(
    api_key: str,
    api_base_url: str,
    *,
    timeout_sec: float = 30.0,
) -> List[str]:
    """GET /v1/models（OpenAI 兼容），返回模型 id 列表。"""
    base = (api_base_url or "").strip().rstrip("/")
    if not base:
        raise ValueError("empty API base URL")
    key = (api_key or "").strip()
    if not key:
        raise ValueError("empty API key")
    url = f"{base}/v1/models"
    req = urllib.request.Request(
        url,
        method="GET",
        headers={"Authorization": f"Bearer {key}"},
    )
    ctx = ssl.create_default_context()
    try:
        with urllib.request.urlopen(req, timeout=timeout_sec, context=ctx) as resp:
            resp_bytes = resp.read()
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", errors="replace") if e.fp else ""
        raise RuntimeError(f"HTTP {e.code}: {detail or e.reason}") from e
    except urllib.error.URLError as e:
        reason = getattr(e, "reason", e)
        raise RuntimeError(str(reason)) from e
    parsed = json.loads(resp_bytes.decode("utf-8", errors="replace"))
    data = parsed.get("data")
    if not isinstance(data, list):
        raise RuntimeError(f"unexpected /v1/models payload: {parsed!r}")
    ids: List[str] = []
    for item in data:
        if isinstance(item, dict):
            mid = item.get("id")
            if mid:
                ids.append(str(mid))
    return sorted(set(ids))


def pick_default_openai_compatible_model(
    model_ids: Sequence[str],
    api_base_url: str,
) -> str:
    """
    在 /v1/models 返回的 id 列表中选一个更适合「聊天/校对字幕」的默认模型。
    DeepSeek 优先 deepseek-v4-flash、deepseek-v4-pro；OpenAI 优先 gpt-4o-mini 等；
    其它兼容服取首个疑似聊天模型。DeepSeek 旧版 id 仅作过渡期回退。
    """
    ids = [str(x).strip() for x in model_ids if str(x).strip()]
    if not ids:
        return DEFAULT_DEEPSEEK_MODEL
    id_set = set(ids)
    base_l = (api_base_url or "").strip().lower()

    def first_match(candidates: Sequence[str]) -> Optional[str]:
        for c in candidates:
            if c in id_set:
                return c
        return None

    # DeepSeek 官方与其它指向 DeepSeek 的兼容端点
    if "deepseek" in base_l:
        picked = first_match(DEEPSEEK_CHAT_MODEL_CANDIDATES_V4)
        if picked:
            return picked
        for mid in ids:
            low = mid.lower()
            if "embed" in low:
                continue
            if "v4" in low and "deepseek" in low:
                return mid
        picked = first_match(DEEPSEEK_CHAT_MODEL_LEGACY_FALLBACK)
        if picked:
            return picked
        for mid in ids:
            low = mid.lower()
            if "embed" in low:
                continue
            if "deepseek" in low and ("chat" in low or "coder" in low):
                return mid
        return ids[0]

    if "openai.com" in base_l:
        picked = first_match(
            (
                "gpt-4o-mini",
                "gpt-4o",
                "gpt-4-turbo",
                "chatgpt-4o-latest",
                "gpt-3.5-turbo",
            )
        )
        if picked:
            return picked
        for mid in ids:
            low = mid.lower()
            if low.startswith("gpt-") and "embed" not in low:
                return mid
        return ids[0]

    if DEFAULT_DEEPSEEK_MODEL in id_set:
        return DEFAULT_DEEPSEEK_MODEL
    for mid in ids:
        low = mid.lower()
        if "embed" in low or "whisper" in low:
            continue
        if "chat" in low or "gpt" in low or "deepseek" in low:
            return mid
    return ids[0]


def _unwrap_json_fence(raw: str) -> str:
    s = raw.strip()
    if s.startswith("```"):
        s = re.sub(r"^```(?:json)?\s*", "", s, flags=re.IGNORECASE)
        s = re.sub(r"\s*```\s*$", "", s)
    return s.strip()


def _parse_polish_response(raw: str, ids: Sequence[str]) -> Dict[str, str]:
    data = json.loads(_unwrap_json_fence(raw))
    lines = data.get("lines") or data.get("items")
    if not isinstance(lines, list):
        raise ValueError("missing lines array")
    out: Dict[str, str] = {}
    for row in lines:
        if not isinstance(row, dict):
            continue
        rid = row.get("id")
        txt = row.get("text") or row.get("fixed") or ""
        if rid is not None:
            out[str(rid)] = _normalize_space_symbols(str(txt))
    if len(out) < len(ids):
        raise ValueError("incomplete ids in response")
    return out


def _post_chat(
    cfg: SubtitlePolisherConfig,
    messages: List[Dict[str, str]],
    *,
    temperature: float = 0.2,
    timeout_sec: float = 120.0,
) -> str:
    """调用 OpenAI 兼容的 /v1/chat/completions（带自动重试）。

    基于 core.llm_client.call_llm，自动获得：
    - tenacity 指数退避重试（速率限制时最多 10 次）
    - base_url 自动规范化（补全 /v1 后缀）
    """
    response = call_llm(
        messages=messages,
        model=cfg.model,
        temperature=temperature,
        base_url=cfg.api_base_url,
        api_key=cfg.api_key,
    )
    return str(response.choices[0].message.content)


STRATEGY_LIMITS = {
    "merge_max_gap_sec": (0.4, 15.0),
    "merge_min_ratio": (0.50, 0.98),
    "merge_min_overlap_len": (3, 22),
}


def _clamp_strategy_param(key: str, value: Any) -> Optional[float]:
    if key not in STRATEGY_LIMITS:
        return None
    lo, hi = STRATEGY_LIMITS[key]
    try:
        v = float(value) if key != "merge_min_overlap_len" else int(float(value))
    except (TypeError, ValueError):
        return None
    if key == "merge_min_overlap_len":
        return float(max(lo, min(hi, int(v))))
    return max(lo, min(hi, float(v)))


def normalize_strategy_params(payload: Dict[str, Any]) -> Tuple[Dict[str, float], str]:
    rationale = ""
    if isinstance(payload.get("rationale"), str):
        rationale = payload["rationale"].strip()
    elif isinstance(payload.get("notes"), str):
        rationale = payload["notes"].strip()
    out: Dict[str, float] = {}
    for k in STRATEGY_LIMITS:
        if k not in payload or payload[k] is None:
            continue
        c = _clamp_strategy_param(k, payload[k])
        if c is not None:
            out[k] = c
    return out, rationale[:2000]


def _compact_events_for_strategy(events: Sequence[Dict[str, Any]], limit: int) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    for e in events:
        rows.append(
            {
                "roi": e.get("roi", ""),
                "start": e.get("start_time", ""),
                "end": e.get("end_time", ""),
                "text": str(e.get("body", ""))[:220],
                "style": e.get("style", ""),
            }
        )
    if len(rows) <= limit:
        return rows
    n2 = limit // 2
    omit = len(rows) - (n2 * 2)
    return rows[:n2] + [{"_truncated_middle": omit}] + rows[-n2:]


def deepseek_suggest_merge_params(
    cfg: SubtitlePolisherConfig,
    pre_merge_events: Sequence[Dict[str, Any]],
    merged_events: Sequence[Dict[str, Any]],
    current_params: Dict[str, float],
    *,
    cancel_check: CancelCheck = lambda: False,
) -> Optional[Tuple[Dict[str, float], str]]:
    """根据合并前后快照与当前参数给出下一轮合并阈值；返回 None 表示跳过或请求失败。"""
    if not cfg.api_key or not cfg.api_key.strip():
        return None
    if cancel_check():
        return None
    package = {
        "current_params": current_params,
        "counts": {"pre_merge": len(pre_merge_events), "after_merge": len(merged_events)},
        "pre_merge_samples": _compact_events_for_strategy(pre_merge_events, 70),
        "merged_results": _compact_events_for_strategy(merged_events, 70),
    }
    user_blob = json.dumps(package, ensure_ascii=False)
    user_msg = (
        "分析以下 JSON：pre_merge_samples 是时间轴合并前的字幕清单，merged_results 是应用当前规则后的结果，"
        "current_params 为当下 merge 阈值。\n"
        "若发现明显碎片化、误合并或该合未合，请给出更合适的 merge_max_gap_sec（秒，通常 0.8～10）、"
        "merge_min_ratio（0.5～0.95，越高越保守）、merge_min_overlap_len（4～16 的整数）。\n"
        "若认为当前设置已合理，可原样返回同一参数。\n"
        "只输出纯 JSON 对象，键为 merge_max_gap_sec、merge_min_ratio、merge_min_overlap_len、rationale，"
        "不要 Markdown，不要其它文字。\n\n"
        + user_blob
    )
    try:
        raw = _post_chat(
            cfg,
            [
                {
                    "role": "system",
                    "content": get_prompt("strategy_review"),
                },
                {"role": "user", "content": user_msg},
            ],
            temperature=0.35,
            timeout_sec=150.0,
        )
        patch, rationale = normalize_strategy_params(json.loads(_unwrap_json_fence(raw)))
        if rationale:
            _cfg_log(cfg, f"[策略复核] 模型说明：{_short_text(rationale, 500)}")
        if patch:
            _cfg_log(cfg, f"[策略复核] 建议调整参数：{patch}")
        else:
            _cfg_log(cfg, "[策略复核] 建议：保持当前合并参数。")
        if not patch:
            return ({}, rationale)
        return (patch, rationale)
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, ValueError, KeyError, RuntimeError) as e:
        logger.warning("[subtitle_llm_polish] strategy review failed: %s", e, exc_info=logger.isEnabledFor(logging.DEBUG))
        return None


def params_close(a: Dict[str, float], b: Dict[str, float], eps_ratio: float = 0.004, eps_gap: float = 0.05) -> bool:
    """判断模型建议是否与当前参数足够接近，用于提前结束迭代。"""
    keys = ("merge_max_gap_sec", "merge_min_ratio", "merge_min_overlap_len")
    for k in keys:
        if k not in a or k not in b:
            return False
        va, vb = float(a[k]), float(b[k])
        if k == "merge_min_ratio":
            if abs(va - vb) > eps_ratio:
                return False
        elif k == "merge_max_gap_sec":
            if abs(va - vb) > eps_gap:
                return False
        else:
            if int(va) != int(vb):
                return False
    return True


# ── Agent Loop 验证 ───────────────────────────────────────────

MAX_POLISH_RETRIES = 3


def _validate_polish_result(
    original_batch: List[Dict[str, str]],
    optimized_dict: Dict[str, str],
    original_ids: List[str],
) -> Tuple[bool, str]:
    """验证 LLM 润色结果。

    检查：
    1. 键（id）是否完全匹配
    2. 每条文本改动幅度是否超过阈值（防止 LLM 过度修改）

    Args:
        original_batch: 原始字幕列表 [{"id": "0", "text": "..."}, ...]
        optimized_dict: 优化后字典 {"0": "fixed text", ...}
        original_ids: 期望的 id 列表

    Returns:
        (是否有效, 错误反馈信息)
    """
    expected_keys = set(original_ids)
    actual_keys = set(optimized_dict.keys())

    # 1. 键匹配检查
    if expected_keys != actual_keys:
        missing = expected_keys - actual_keys
        extra = actual_keys - expected_keys
        error_parts = []
        if missing:
            error_parts.append(f"Missing keys: {sorted(missing)}")
        if extra:
            error_parts.append(f"Extra keys: {sorted(extra)}")
        error_msg = (
            "\n".join(error_parts)
            + f"\nRequired keys: {sorted(expected_keys)}\n"
            f"Please return the COMPLETE optimized dictionary "
            f"with ALL {len(original_ids)} keys."
        )
        return False, error_msg

    # 2. 逐条改动幅度检查
    orig_map = {item["id"]: item["text"] for item in original_batch}
    excessive_changes = []
    for key in sorted(expected_keys, key=int):
        original_text = orig_map.get(key, "")
        optimized_text = optimized_dict.get(key, "")

        # 清理文本用于比较
        orig_clean = re.sub(r"\s+", " ", original_text).strip()
        opt_clean = re.sub(r"\s+", " ", optimized_text).strip()

        matcher = difflib.SequenceMatcher(None, orig_clean, opt_clean)
        similarity = matcher.ratio()
        similarity_threshold = 0.3 if count_words(original_text) <= 10 else 0.7

        if similarity < similarity_threshold:
            excessive_changes.append(
                f"Key '{key}': "
                f"similarity {similarity:.1%} < {similarity_threshold:.0%}. "
                f"Original: '{_short_text(original_text, 80)}' → "
                f"Optimized: '{_short_text(optimized_text, 80)}'"
            )

    if excessive_changes:
        error_msg = ";\n".join(excessive_changes)
        error_msg += (
            "\n\nYour optimizations changed the text too much. "
            "Keep high similarity (≥70% for normal text) "
            "by making MINIMAL changes: "
            "only fix recognition errors and improve clarity, "
            "but preserve the original wording, length and structure "
            "as much as possible."
        )
        return False, error_msg

    return True, ""


def polish_subtitle_texts(
    texts: Sequence[str],
    cfg: SubtitlePolisherConfig,
    *,
    cancel_check: CancelCheck = lambda: False,
    on_batch_done: Optional[BatchCb] = None,
) -> List[str]:
    flat = list(texts)
    if not cfg.api_key or not cfg.api_key.strip():
        logger.warning("[subtitle_llm_polish] API key empty; skip polishing.")
        return flat
    n = len(flat)
    out = list(flat)
    bs = max(1, min(int(cfg.batch_size), 120))
    start = 0
    batch_idx = 0
    total_batches = (n + bs - 1) // bs
    while start < n:
        if cancel_check():
            logger.info("[subtitle_llm_polish] cancelled; keeping remaining originals.")
            break
        end = min(start + bs, n)
        batch_ids = [str(i) for i in range(start, end)]
        batch_objs = [{"id": batch_ids[j - start], "text": flat[j]} for j in range(start, end)]
        user_json = json.dumps({"lines": batch_objs}, ensure_ascii=False)
        user_content = (
            "请校对以下 JSON 对象中的 lines[].text。\n"
            "输出严格为 JSON：`{\"lines\":[{\"id\":\"...\",\"text\":\"...\"}]}`，不要 Markdown。\n"
            "对每条 text：若含 OCR 误输出的「反斜杠+小写 n/r/t」或多余换行/制表符，请改为句内单个半角空格；"
            "「反斜杠+大写 N」的 ASS 硬换行可保留。\n\n"
            + user_json
        )
        raw = ""
        try:
            # ── Agent Loop: LLM → 验证 → 反馈 → 重试（最多 MAX_POLISH_RETRIES 轮）──
            mapping: Dict[str, str] = {}
            last_error = ""
            # 构建初始消息（仅在首轮构造，后续追加反馈）
            messages = [
                {
                    "role": "system",
                    "content": get_prompt("subtitle_polish"),
                },
                {"role": "user", "content": user_content},
            ]
            for attempt in range(MAX_POLISH_RETRIES):
                if cancel_check():
                    break

                raw = _post_chat(
                    cfg, messages,
                    temperature=0.2,
                    timeout_sec=120.0,
                )
                mapping = _parse_polish_response(raw, batch_ids)

                # 验证结果
                is_valid, error_msg = _validate_polish_result(
                    batch_objs, mapping, batch_ids
                )
                if is_valid:
                    break  # 验证通过

                # 验证失败，追加 assistant 响应 + 反馈，下一轮重试
                last_error = error_msg
                messages.append({"role": "assistant", "content": raw})
                messages.append(
                    {
                        "role": "user",
                        "content": (
                            f"Validation failed:\n{error_msg}\n"
                            f"Please fix ALL errors and output ONLY a valid JSON "
                            f'with the exact format: {{"lines":[{{"id":"...","text":"..."}}]}}'
                        ),
                    }
                )
                logger.debug(
                    "[subtitle_llm_polish] batch [%s,%s) attempt %d/%d failed validation: %s",
                    start, end, attempt + 1, MAX_POLISH_RETRIES,
                    error_msg[:200],
                )

            # ── 应用优化结果 ──
            for lid in batch_ids:
                if lid in mapping:
                    out[int(lid)] = mapping[lid]
            n_changed = 0
            samples: List[str] = []
            for j in range(start, end):
                if out[j] != flat[j]:
                    n_changed += 1
                    if len(samples) < 35:
                        samples.append(
                            f"  行[{j}] 「{_short_text(flat[j], 88)}」→「{_short_text(out[j], 88)}」"
                        )
            _cfg_log(
                cfg,
                f"[字幕润色] 批次 {batch_idx + 1}/{total_batches}（行 {start}–{end - 1}），"
                f"共 {n_changed} 条与 OCR 原文不同。",
            )
            for line in samples:
                _cfg_log(cfg, line)
            if n_changed == 0:
                _cfg_log(cfg, "  （本批模型输出与原文一致，无字面变更。）")
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, ValueError, KeyError, RuntimeError) as e:
            logger.warning(
                "[subtitle_llm_polish] batch [%s,%s) failed (%s); keeping originals. raw_head=%s",
                start,
                end,
                e,
                (raw[:200] + "...") if len(raw) > 200 else raw,
                exc_info=logger.isEnabledFor(logging.DEBUG),
            )
            _cfg_log(cfg, f"[字幕润色] 批次 {batch_idx + 1}/{total_batches} 请求失败，已保留本批原文：{e}")
        batch_idx += 1
        if on_batch_done:
            on_batch_done(batch_idx, total_batches)
        start = end
    return out


def classify_text_source(
    cfg: SubtitlePolisherConfig,
    text: str,
    context: Optional[Dict[str, Any]] = None,
    *,
    cancel_check: CancelCheck = lambda: False,
    timeout_sec: float = 60.0,
) -> Optional[Dict[str, Any]]:
    """Use LLM to classify a text region as OVERLAY or SCENE.

    Called when the rule-based classifier has low confidence (< 0.7).
    Sends text + spatial/temporal context to the LLM for semantic judgment.

    Args:
        cfg: LLM configuration.
        text: The recognized text content.
        context: Optional dict with keys like 'position', 'duration_sec',
                 'frame_width', 'frame_height', 'nearby_texts'.

    Returns:
        Dict with 'source' ('overlay'|'scene'|'unknown') and 'confidence' (0-1),
        or None on failure.
    """
    if not cfg.api_key or not cfg.api_key.strip():
        return None
    if cancel_check():
        return None

    ctx = context or {}
    ctx_json = json.dumps({
        "text": text,
        "position": ctx.get("position", "unknown"),
        "duration_sec": ctx.get("duration_sec", 0),
        "frame_size": f"{ctx.get('frame_width', 1920)}x{ctx.get('frame_height', 1080)}",
        "nearby_texts": ctx.get("nearby_texts", []),
    }, ensure_ascii=False)

    user_msg = (
        "你是一个视频字幕分析专家。你需要判断视频画面中的文字属于哪种来源：\n\n"
        "1. OVERLAY（后期人工叠加）：视频拍摄完成后添加的字幕、标题、台标、"
        "水印、UI按钮、弹幕、特效文字。特征包括：位于画面固定位置、有描边/"
        "投影/背景色块、规律出现/消失、无透视变形。\n\n"
        "2. SCENE（实拍环境原生）：拍摄时场景中自然存在的文字。特征包括：招牌、"
        "路牌、海报、书本、手机屏幕、电视画面内的文字。位于画面任意位置、"
        "可能有透视变形、与场景光照一致。\n\n"
        "请分析以下 OCR 识别到的文字区域，给出你的判定和置信度。\n\n"
        "文字区域信息：\n"
        + ctx_json
        + "\n\n请判定该文字的来源（OVERLAY/SCENE/UNKNOWN）并给出置信度(0-1)。\n"
        "只输出纯 JSON：{\"source\": \"...\", \"confidence\": 0.xx, \"reasoning\": \"...\"}，不要 Markdown，不要其它文字。"
    )

    try:
        raw = _post_chat(
            cfg,
            [
                {
                    "role": "system",
                    "content": get_prompt("text_classify"),
                },
                {"role": "user", "content": user_msg},
            ],
            temperature=0.2,
            timeout_sec=timeout_sec,
        )
        data = json.loads(_unwrap_json_fence(raw))
        source_raw = str(data.get("source", "unknown")).lower()
        if source_raw in ("overlay", "scene"):
            source = source_raw
        else:
            source = "unknown"
        confidence = float(data.get("confidence", 0.5))
        confidence = max(0.0, min(1.0, confidence))
        reasoning = str(data.get("reasoning", ""))[:500]

        _cfg_log(
            cfg,
            f"[来源分类] 文本：「{_short_text(text, 80)}」→ {source} (conf={confidence:.2f}) {reasoning}",
        )
        return {"source": source, "confidence": confidence, "reasoning": reasoning}

    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, ValueError, KeyError, RuntimeError) as e:
        logger.warning("[subtitle_llm_polish] text source classification failed: %s", e,
                       exc_info=logger.isEnabledFor(logging.DEBUG))
        return None
