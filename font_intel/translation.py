# font_intel/translation.py
"""字幕翻译模块（T2.1 + T2.2）：三级提供方降级链 + 字幕特化。

定位（方案 §4.2/§4.3，docs/feature_plan_font_translation_compliance.md）
------------------------------------------------------------------------
流水线阶段 4（LLM 润色之后）的可选翻译阶段；本模块只做"文本列表 → 文本
列表"的纯翻译，不接流水线（T2.3 负责接线）、不做字体映射（翻译完成后由
流水线另查 ``font_intel.matching`` 做中日字体映射，本模块只留注释接入点）。

三级提供方按序降级
------------------
1. 云端 API：任意 OpenAI 兼容端点（默认复用润色配置的 DeepSeek 常量）；
2. 本地 Sakura：Sakura-13B/14B-Galgame（日中 ACGN 特化）。经 llama.cpp /
   sglang / vLLM / Ollama 部署后即暴露 OpenAI 兼容 /v1/chat/completions，
   **零代码适配**——只需 ``sakura_base_url`` 指向本地端点；GGUF 量化版
   （显存不足时）与 Ollama ``ollama serve`` 同样可用；
3. VLM 兜底：复用 ``core/vlm_refine.py`` 的既有配置（``VLM_REFINE_*``
   环境变量，不新增配置面），带图像消息直读图翻译（无图时退化为纯文本）。

某级**未配置**或**调用失败**（含 429 有界退避耗尽归一的 ``LlmApiError``）
自动降到下一级；三级全失败 → 该批保留原文并在结果中标注失败原因。
**任何路径都不抛异常中断调用方**（全局硬约束：服务端故障绝不击穿任务链）。

硬约束（工作区 AGENTS.md）
--------------------------
所有服务端调用统一经 ``core.llm_client.call_llm``（429 有界退避：最多
10 次 / 单次等待 ≤60s / 总等待 ≤300s / 尊重 Retry-After），耗尽抛出的
``LlmApiError`` 只让当前批保留原文，不重试、不中断。测试通过 monkeypatch
``core.llm_client.call_llm`` 验证调用确实经由该入口。

字幕特化
--------
- 上下文窗口：逐批携带前后 N 行原文，prompt 注明"仅供参考，只翻译目标行"；
- 术语表：``glossary.json``（{"术语": "译名"}）读入后写入 prompt 强制一致；
  文件缺失/坏 JSON 降级为无术语表并告警；
- 行长约束：按可配置的最大字符数（``\\N`` 断行后按最长段估算）判定译文
  超长，超长批二次请求"缩译"（一次即可，失败保留首次译文）；
- 输出校验：模型须返回 JSON 字符串数组且行数与批一致（json_repair 兜底，
  参考 ``vlm_refine._parse_text_array`` 模式），不一致视为该级失败；
- 换行规范化：译文中的真实换行与字面量 ``\\n`` 统一为 ASS 硬换行 ``\\N``。

批处理骨架直接平移 ``polish_subtitle_texts``（批次切分 + ThreadPoolExecutor
并发 + 取消检查 + 进度回调 + 单批失败只影响该批），不新写并发调度。
与润色通道的差异仅在 prompt 与输出校验；``_post_chat`` 无法直接复用
（其签名绑定 ``SubtitlePolisherConfig``），故本模块经 ``call_llm`` 自建
等价调用（见 ``_chat``），退避语义完全归 ``core.llm_client``。
"""

from __future__ import annotations

import json
import logging
import os
import re
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

from core import llm_client
from core.llm_client import LlmApiError
from core.subtitle_llm_polish import (
    DEFAULT_DEEPSEEK_BASE,
    DEFAULT_DEEPSEEK_MODEL,
    _MAX_CONCURRENT_REQUESTS_CAP,  # 复用润色通道的并发硬上限（同一 LLM 配额）
)

try:
    import json_repair
except ImportError:  # pragma: no cover - json_repair is a declared dependency
    json_repair = None

try:  # 惰性可选：cv2 缺失时 VLM 通道自动不可用，不影响其余两级
    from core.vlm_refine import DEFAULT_VLM_MODEL as _VLM_DEFAULT_MODEL
except Exception:  # pragma: no cover - cv2/numpy 缺失的极简环境
    _VLM_DEFAULT_MODEL = "HunyuanOCR"

logger = logging.getLogger(__name__)

__all__ = [
    "PROVIDER_CLOUD",
    "PROVIDER_SAKURA",
    "PROVIDER_VLM",
    "PROVIDER_NONE",
    "FAIL_NO_PROVIDER",
    "FAIL_CANCELLED",
    "FAIL_LLM_API",
    "FAIL_OUTPUT_INVALID",
    "FAIL_UNEXPECTED",
    "DEFAULT_TARGET_LANGUAGE",
    "DEFAULT_SAKURA_MODEL",
    "TranslationConfig",
    "BatchReport",
    "TranslationStats",
    "TranslationResult",
    "load_glossary",
    "translate_subtitle_texts",
]

# ── 常量 ─────────────────────────────────────────────────────────

# 提供方链级标识（BatchReport.provider / provider_batches 键）。
PROVIDER_CLOUD = "cloud"
PROVIDER_SAKURA = "sakura"
PROVIDER_VLM = "vlm"
PROVIDER_NONE = "none"

# 单批失败原因（BatchReport.reason），供流水线日志与报告消费。
FAIL_NO_PROVIDER = "no_provider_configured"
FAIL_CANCELLED = "cancelled"
FAIL_LLM_API = "llm_api_error"
FAIL_OUTPUT_INVALID = "llm_output_invalid"
FAIL_UNEXPECTED = "unexpected_error"

DEFAULT_TARGET_LANGUAGE = "简体中文"
# Sakura-13B/14B-Galgame 的 llama.cpp/Ollama 部署常用模型 id；可按部署改。
DEFAULT_SAKURA_MODEL = "sakura-14b"
# 本地端点通常无鉴权，但 call_llm/get_llm_client 硬要求 api_key 非空：
# 为空时用占位 key，让 OpenAI 兼容本地端点零配置可用。
_LOCAL_API_KEY_PLACEHOLDER = "sk-sakura-local"
_SAKURA_TEMPERATURE = 0.1

DEFAULT_CONTEXT_LINES = 2
DEFAULT_MAX_LINE_CHARS = 42
DEFAULT_BATCH_SIZE = 20
DEFAULT_MAX_CONCURRENT_REQUESTS = 2  # 并发默认小：本地 Sakura 单卡易打满
DEFAULT_TIMEOUT_SEC = 120.0
_MAX_BATCH_SIZE_CAP = 120  # 与润色通道的批次上限一致

SYSTEM_PROMPT = (
    "你是专业的视频字幕翻译引擎。把给定的字幕行忠实翻译成目标语言："
    "保持口语化、简洁、符合字幕语域；不解释、不增删信息、不合并或拆分行。"
    "必须按输入顺序输出与目标行数相同的 JSON 字符串数组；"
    "只输出 JSON 数组，不要 Markdown，不要任何其他文字。"
)
SHORTEN_SYSTEM_PROMPT = (
    "你是字幕缩译助手。只压缩超长行的译文长度，保留原意与语气；"
    "其余行必须原样保留。只输出 JSON 字符串数组，不要 Markdown。"
)

# 译文换行规范化：真实 CR/LF 与字面量「\n / \r」统一为 ASS 硬换行「\N」
# （大写 \N 原样保留——对齐既有 Backlog 项"多行文本换行标签规范化"）。
_REAL_NEWLINE_RE = re.compile(r"\r\n|\r|\n")
_LIT_NEWLINE_RE = re.compile(r"\\[nr]")

CancelCheck = Callable[[], bool]
BatchCb = Callable[[int, int], None]
LogLine = Callable[[str], None]


# ── 配置 ─────────────────────────────────────────────────────────


@dataclass
class TranslationConfig:
    """翻译阶段配置（风格仿 ``SubtitlePolisherConfig``，全部有默认值）。

    三级提供方各自独立开关：对应字段留空即该级"未配置"，降级链自动跳过。
    """

    # 第一级：云端 OpenAI 兼容 API（默认复用润色配置的 DeepSeek 常量）。
    cloud_api_key: str = ""
    cloud_base_url: str = DEFAULT_DEEPSEEK_BASE
    cloud_model: str = DEFAULT_DEEPSEEK_MODEL
    cloud_temperature: float = 0.2

    # 第二级：本地 Sakura（OpenAI 兼容端点零代码适配）。GGUF 量化版
    # （llama.cpp / vLLM）与 Ollama 部署均可，只需指向本地 /v1 地址；
    # 端点无鉴权时 api_key 留空即可（内部自动用占位 key）。
    sakura_base_url: str = ""
    sakura_model: str = DEFAULT_SAKURA_MODEL
    sakura_api_key: str = ""

    # 第三级：VLM 兜底不新增配置面，直接复用 core/vlm_refine 的
    # VLM_REFINE_BASE_URL / VLM_REFINE_API_KEY / VLM_REFINE_MODEL 环境变量。

    # 目标语言（默认简体中文；源语言路由与逐 ROI 切分是 T2.3 的事）。
    target_language: str = DEFAULT_TARGET_LANGUAGE
    # 源语言提示（T2.3 双语路由：流水线按所属 ROI 的 ``ocr_lang`` 分组传入
    # 人类可读语言名，如"日语"；空 = 不提示，由模型自动识别源语言——
    # 即既有行为，缺省不变）。
    source_language: str = ""

    # 字幕特化：前后 N 行上下文窗口。
    context_window_lines: int = DEFAULT_CONTEXT_LINES
    # 术语表路径（{"术语": "译名"}）；空 = 不启用。
    glossary_path: str = ""
    # 行长约束：译文超过该字符数（按 \N 断行后的最长段计）触发一次缩译；
    # <=0 = 不启用。
    max_line_chars: int = DEFAULT_MAX_LINE_CHARS

    # 批处理：批大小与批间并发数（1 = 串行；上限复用润色通道硬上限）。
    batch_size: int = DEFAULT_BATCH_SIZE
    max_concurrent_requests: int = DEFAULT_MAX_CONCURRENT_REQUESTS
    # 单次请求超时秒数（透传给 call_llm → openai 客户端）。
    timeout_sec: float = DEFAULT_TIMEOUT_SEC

    # 可选：面向 GUI/流水线的逐条日志回调（线程安全由实现方保证）。
    log_line: Optional[LogLine] = None


# ── 结果结构 ─────────────────────────────────────────────────────


@dataclass
class BatchReport:
    """单批处理结果：提供方、成败、失败原因与译文（失败时为 None）。"""

    batch_index: int = 0
    start: int = 0
    end: int = 0
    provider: str = PROVIDER_NONE
    ok: bool = False
    reason: str = ""
    shortened_lines: int = 0
    texts: Optional[List[str]] = None


@dataclass
class TranslationStats:
    """批处理统计：成功批数 / 降级批数 / 各提供方批次分布 / 失败原因。"""

    total_lines: int = 0
    batches_total: int = 0
    batches_attempted: int = 0
    batches_success: int = 0
    batches_failed: int = 0
    # 降级批数：成功但由降级层（非链首已配置提供方）完成的批次数。
    batches_degraded: int = 0
    # 链首提供方（provider == top 的成功批不算降级）。
    top_provider: str = PROVIDER_NONE
    provider_batches: Dict[str, int] = field(default_factory=dict)
    shortened_lines: int = 0
    batches: List[BatchReport] = field(default_factory=list)


@dataclass
class TranslationResult:
    """translate_subtitle_texts 的返回：与输入等长的译文列表 + 统计。"""

    texts: List[str] = field(default_factory=list)
    stats: TranslationStats = field(default_factory=TranslationStats)


# ── 基础设施：日志回调 / 术语表 / 解析 / 归一 ────────────────────


def _cfg_log(cfg: TranslationConfig, msg: str) -> None:
    if not msg:
        return
    fn = cfg.log_line
    if fn is None:
        return
    try:
        fn(msg)
    except Exception:
        pass


def load_glossary(path: str) -> Dict[str, str]:
    """读入术语表（{"术语": "译名"}）；缺失/坏 JSON/非 dict 降级为空表并告警。

    术语表是"强制一致"的增强手段而非硬依赖：任何读取问题都不阻断翻译。
    """
    p = (path or "").strip()
    if not p:
        return {}
    try:
        with open(p, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (json.JSONDecodeError, UnicodeDecodeError, OSError) as exc:
        logger.warning(
            "[font_intel.translation] 术语表读取失败，降级为无术语表：%s（%s）", p, exc
        )
        return {}
    if not isinstance(data, dict):
        logger.warning(
            "[font_intel.translation] 术语表格式应为 {\"术语\": \"译名\"}，"
            "降级为无术语表：%s",
            p,
        )
        return {}
    out: Dict[str, str] = {}
    for k, v in data.items():
        ks, vs = str(k).strip(), str(v).strip()
        if ks and vs:
            out[ks] = vs
    return out


def _strip_code_fence(raw: str) -> str:
    s = str(raw).strip()
    if s.startswith("```"):
        s = re.sub(r"^```(?:json)?\s*", "", s, flags=re.IGNORECASE)
        s = re.sub(r"\s*```\s*$", "", s)
    return s.strip()


def _parse_text_array(raw: str) -> Optional[List[str]]:
    """把模型输出解析为非空字符串数组；任何不符预期返回 None。

    参考 ``core/vlm_refine.py`` 的 ``_parse_text_array`` + json_repair 模式：
    json.loads 失败再用 json_repair 兜底（容忍尾逗号等常见畸形）。
    """
    s = _strip_code_fence(raw)
    data: Any = None
    try:
        data = json.loads(s)
    except (ValueError, TypeError):
        if json_repair is None:
            return None
        try:
            data = json_repair.loads(s)
        except Exception:
            return None
    if not isinstance(data, list):
        return None
    out: List[str] = []
    for item in data:
        if not isinstance(item, str):
            return None
        text = item.strip()
        if not text:  # 合理性检查：每行必须是非空字符串
            return None
        out.append(text)
    return out


def _normalize_translated_line(text: str) -> str:
    """行级清洗：去首尾空白；真实换行/字面量 \\n 统一为 ASS 硬换行 \\N。"""
    t = str(text).strip()
    t = _REAL_NEWLINE_RE.sub(r"\\N", t)
    t = _LIT_NEWLINE_RE.sub(r"\\N", t)
    return t


def _visual_line_len(text: str) -> int:
    """估算渲染行长：按 \\N 断行后的最长段计（ASS 一行一段）。"""
    segments = str(text).split("\\N")
    return max((len(seg) for seg in segments), default=0)


# ── LLM 调用（全部经 core.llm_client.call_llm） ──────────────────


def _call_llm(
    messages: List[dict],
    *,
    model: str,
    base_url: str,
    api_key: str,
    temperature: float,
    timeout: Optional[float],
) -> Any:
    """薄封装：所有服务端调用统一经 ``core.llm_client.call_llm``。

    工作区硬约束：429 有界退避语义归 llm_client；这里只把退避耗尽抛出的
    ``LlmApiError`` 之外的残漏异常（空响应 ValueError、SDK 异常形态等）
    一并收口为 ``LlmApiError``，保证上层只面对单一异常面。
    """
    try:
        return llm_client.call_llm(
            messages=messages,
            model=model,
            base_url=(base_url or "").strip() or None,
            api_key=(api_key or "").strip() or None,
            temperature=temperature,
            timeout=timeout if timeout is not None and timeout > 0 else None,
        )
    except LlmApiError:
        raise
    except Exception as exc:
        raise LlmApiError(
            f"LLM API call failed: {type(exc).__name__}: {exc}"
        ) from exc


def _chat(provider: str, cfg: TranslationConfig, messages: List[dict]) -> Any:
    """按提供方路由一次 chat 调用（云端/本地为 OpenAI 兼容 chat）。"""
    if provider == PROVIDER_CLOUD:
        return _call_llm(
            messages,
            model=cfg.cloud_model,
            base_url=cfg.cloud_base_url,
            api_key=cfg.cloud_api_key,
            temperature=cfg.cloud_temperature,
            timeout=cfg.timeout_sec,
        )
    if provider == PROVIDER_SAKURA:
        return _call_llm(
            messages,
            model=cfg.sakura_model,
            base_url=cfg.sakura_base_url,
            # 本地端点无鉴权时用占位 key（call_llm 硬要求非空）。
            api_key=cfg.sakura_api_key or _LOCAL_API_KEY_PLACEHOLDER,
            temperature=_SAKURA_TEMPERATURE,
            timeout=cfg.timeout_sec,
        )
    if provider == PROVIDER_VLM:
        # 复用 vlm_refine 的 VLM_REFINE_* 环境变量，不新增配置面。
        # 与 vlm_refine 不同，这里把 base_url/api_key 显式传给 call_llm，
        # 使该通道凭 VLM_REFINE_* 自足可用（不改 core/ 既有文件）。
        return _call_llm(
            messages,
            model=os.getenv("VLM_REFINE_MODEL", "").strip() or _VLM_DEFAULT_MODEL,
            base_url=os.getenv("VLM_REFINE_BASE_URL", "").strip(),
            api_key=os.getenv("VLM_REFINE_API_KEY", "").strip(),
            temperature=0.0,
            timeout=cfg.timeout_sec,
        )
    raise ValueError(f"unknown provider: {provider}")


def _vlm_configured() -> bool:
    """VLM 通道是否已配置（复用 vlm_refine.is_configured，惰性导入）。"""
    try:
        from core.vlm_refine import is_configured
    except Exception:  # pragma: no cover - cv2/numpy 缺失的极简环境
        return False
    try:
        return bool(is_configured())
    except Exception:
        return False


def _provider_chain(cfg: TranslationConfig) -> List[str]:
    """按降级序返回当前配置下可用的提供方链。"""
    chain: List[str] = []
    if cfg.cloud_api_key.strip() and cfg.cloud_base_url.strip():
        chain.append(PROVIDER_CLOUD)
    if cfg.sakura_base_url.strip():
        chain.append(PROVIDER_SAKURA)
    if _vlm_configured():
        chain.append(PROVIDER_VLM)
    return chain


# ── prompt 组装 ──────────────────────────────────────────────────


def _build_instruction(
    batch_texts: List[str],
    before: List[str],
    after: List[str],
    glossary: Dict[str, str],
    cfg: TranslationConfig,
) -> str:
    parts: List[str] = []
    # 源语言提示（T2.3 双语路由）：仅当流水线按 ROI ocr_lang 显式给出时
    # 注入，缺省留空 = 模型自行识别（既有 prompt，逐字节不变）。
    source = str(getattr(cfg, "source_language", "") or "").strip()
    if source:
        parts.append(f"源语言：{source}（目标行均为该语言，直接翻译，无需检测）")
    parts.append(f"目标语言：{cfg.target_language}")
    if glossary:
        parts.append(
            "[术语表]（以下词条的译名必须严格一致，全文不得改写）：\n"
            + json.dumps(glossary, ensure_ascii=False, sort_keys=True)
        )
    if before or after:
        ctx = ["[上下文]（相邻行原文，仅供参考；只翻译目标行，不要翻译或输出上下文）"]
        if before:
            ctx.append("前文：\n" + "\n".join(f"{i + 1}. {t}" for i, t in enumerate(before)))
        if after:
            ctx.append("后文：\n" + "\n".join(f"{i + 1}. {t}" for i, t in enumerate(after)))
        parts.append("\n".join(ctx))
    parts.append(
        "[目标行]（共 {} 行，按顺序翻译）：{}".format(
            len(batch_texts), json.dumps(batch_texts, ensure_ascii=False)
        )
    )
    parts.append(
        "输出：只返回一个 JSON 字符串数组，长度必须为 {}，与目标行一一对应；"
        "不要 Markdown，不要任何其他文字。".format(len(batch_texts))
    )
    return "\n\n".join(parts)


def _build_shorten_instruction(
    translated: List[str], over_indices: List[int], cfg: TranslationConfig
) -> str:
    over = [{"index": i, "text": translated[i]} for i in over_indices]
    return (
        "以下是刚翻译完成的字幕行 JSON 数组：\n{}\n\n"
        "其中 index {} 的行超过最大行长 {} 字符（按 \\N 断行后的最长段计）。\n"
        "请仅对这些行缩译：保留原意与语气，压缩到不超过 {} 字符；"
        "其余行必须原样保留。\n"
        "只输出完整 JSON 字符串数组（长度保持 {}），不要 Markdown，不要其他文字。"
    ).format(
        json.dumps(translated, ensure_ascii=False),
        ",".join(str(i) for i in over_indices),
        cfg.max_line_chars,
        cfg.max_line_chars,
        len(translated),
    )


def _build_messages(
    batch_texts: List[str],
    before: List[str],
    after: List[str],
    glossary: Dict[str, str],
    cfg: TranslationConfig,
    frame_images: Optional[Sequence[Any]],
) -> List[dict]:
    """组装消息；VLM 通道带图像消息（vlm_refine 同款 content 分段构造）。"""
    instruction = _build_instruction(batch_texts, before, after, glossary, cfg)
    if frame_images:
        data_urls = _vlm_data_urls(frame_images)
        if data_urls:
            content: List[dict] = [{"type": "text", "text": instruction}]
            for url in data_urls:
                content.append({"type": "image_url", "image_url": {"url": url}})
            return [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": content},
            ]
    return [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": instruction},
    ]


def _vlm_data_urls(frame_images: Sequence[Any]) -> List[str]:
    """复用 vlm_refine 的 JPEG data URL 编码；编码失败的帧跳过。"""
    try:
        from core.vlm_refine import _encode_image_jpeg
    except Exception:  # pragma: no cover - cv2/numpy 缺失
        return []
    urls: List[str] = []
    for image in frame_images:
        try:
            url = _encode_image_jpeg(image)
        except Exception:
            url = None
        if url:
            urls.append(url)
    return urls


# ── 行长约束：二次缩译（一次即可，失败保留首次译文） ─────────────


def _shorten_overlong(
    provider: str,
    cfg: TranslationConfig,
    translated: List[str],
    cancel_check: CancelCheck,
) -> Tuple[List[str], int]:
    """对超长行发起一次批级缩译请求；任何失败保留首次译文。"""
    limit = int(cfg.max_line_chars or 0)
    if limit <= 0:
        return translated, 0
    over = [i for i, t in enumerate(translated) if _visual_line_len(t) > limit]
    if not over or cancel_check():
        return translated, 0
    messages = [
        {"role": "system", "content": SHORTEN_SYSTEM_PROMPT},
        {"role": "user", "content": _build_shorten_instruction(translated, over, cfg)},
    ]
    try:
        response = _chat(provider, cfg, messages)
        lines = _parse_text_array(str(response.choices[0].message.content))
        if lines is None or len(lines) != len(translated):
            return translated, 0
        out = list(translated)
        for i in over:
            cand = _normalize_translated_line(lines[i])
            if cand:
                out[i] = cand
        return out, len(over)
    except Exception as exc:
        logger.debug("[font_intel.translation] shorten pass failed, keep first pass: %s", exc)
        return translated, 0


# ── 单批处理：三级降级链 ─────────────────────────────────────────


def _translate_one_batch(
    batch_idx: int,
    total_batches: int,
    start: int,
    end: int,
    batch_texts: List[str],
    before: List[str],
    after: List[str],
    glossary: Dict[str, str],
    cfg: TranslationConfig,
    providers: Sequence[str],
    frame_images: Optional[Sequence[Any]],
    cancel_check: CancelCheck,
) -> BatchReport:
    """翻译单批：按链序尝试各提供方，全失败返回 ok=False 的报告。

    线程安全性：只读 cfg/glossary/providers，只写自身局部状态；log_line 的
    实现方需自行保证线程安全（与润色通道同一约定）。
    """
    report = BatchReport(batch_index=batch_idx, start=start, end=end)
    for provider in providers:
        if cancel_check():
            report.reason = FAIL_CANCELLED
            return report
        try:
            messages = _build_messages(
                batch_texts, before, after, glossary, cfg, frame_images
            )
            response = _chat(provider, cfg, messages)
            lines = _parse_text_array(str(response.choices[0].message.content))
            if lines is None or len(lines) != len(batch_texts):
                # 行数与批不一致：该级输出作废，降下一级。
                report.reason = FAIL_OUTPUT_INVALID
                logger.warning(
                    "[font_intel.translation] 批次 %d/%d %s 通道输出行数不符"
                    "（期望 %d），降级下一通道。",
                    batch_idx + 1, total_batches, provider, len(batch_texts),
                )
                continue
            translated = [_normalize_translated_line(t) for t in lines]
            translated, shortened = _shorten_overlong(
                provider, cfg, translated, cancel_check
            )
            report.provider = provider
            report.texts = translated
            report.shortened_lines = shortened
            report.ok = True
            report.reason = ""
            return report
        except LlmApiError as exc:
            # 429 有界退避耗尽等：本通道放弃，降下一级；绝不抛出。
            report.reason = FAIL_LLM_API
            logger.warning(
                "[font_intel.translation] 批次 %d/%d %s 通道 LLM 调用失败，"
                "降级下一通道：%s",
                batch_idx + 1, total_batches, provider, exc,
            )
            continue
        except Exception as exc:  # 兜底：任何异常都不允许中断任务链
            report.reason = FAIL_UNEXPECTED
            logger.warning(
                "[font_intel.translation] 批次 %d/%d %s 通道未预期异常，"
                "降级下一通道：%s",
                batch_idx + 1, total_batches, provider, exc,
                exc_info=logger.isEnabledFor(logging.DEBUG),
            )
            continue
    report.provider = PROVIDER_NONE
    return report


# ── 批处理骨架（平移 polish_subtitle_texts） ─────────────────────


def translate_subtitle_texts(
    texts: Sequence[str],
    cfg: TranslationConfig,
    *,
    cancel_check: CancelCheck = lambda: False,
    on_batch_done: Optional[BatchCb] = None,
    frame_images: Optional[Sequence[Any]] = None,
) -> TranslationResult:
    """批量翻译字幕文本，返回与输入等长的译文列表 + 批处理统计。

    三级全失败/取消/未配置的批次保留原文；本函数任何路径都不抛异常。

    Args:
        texts: 源字幕文本列表（顺序即播放顺序）。
        cfg: 翻译配置。
        cancel_check: 取消检查（每次批次派发与每级请求前调用）。
        on_batch_done: 进度回调 ``(已完成批数, 总批数)``。
        frame_images: 可选 BGR 帧图（供 VLM 通道带图翻译；无图时该通道
            退化为纯文本）。帧选取与按批分配是 T2.3 流水线接入的事。

    Returns:
        ``TranslationResult``：``texts`` 与输入等长（失败批保留原文），
        ``stats`` 供流水线日志与报告使用。
    """
    flat: List[str] = [t if isinstance(t, str) else str(t) for t in (texts or [])]
    out = list(flat)
    stats = TranslationStats(total_lines=len(flat))
    if not flat:
        return TranslationResult(out, stats)

    providers = _provider_chain(cfg)
    bs = max(1, min(int(cfg.batch_size), _MAX_BATCH_SIZE_CAP))
    n = len(flat)
    window = max(0, int(cfg.context_window_lines))
    batches: List[Tuple[int, int, List[str], List[str], List[str]]] = []
    for start in range(0, n, bs):
        end = min(start + bs, n)
        before = flat[max(0, start - window):start]
        after = flat[end:end + window]
        batches.append((start, end, flat[start:end], before, after))
    stats.batches_total = len(batches)
    if not batches:
        return TranslationResult(out, stats)

    if not providers:
        # 三级全未配置：绝不触网，整链保留原文并标注原因。
        logger.warning(
            "[font_intel.translation] 云端/本地/VLM 提供方均未配置；全部保留原文。"
        )
        _cfg_log(cfg, "[翻译] 未配置任何翻译提供方，保留全部原文。")
        for idx, (start, end, _bt, _bf, _af) in enumerate(batches):
            report = BatchReport(
                batch_index=idx, start=start, end=end,
                provider=PROVIDER_NONE, ok=False, reason=FAIL_NO_PROVIDER,
            )
            _record_batch(stats, report, PROVIDER_NONE)
        return TranslationResult(out, stats)

    glossary = load_glossary(cfg.glossary_path)
    if glossary:
        _cfg_log(cfg, f"[翻译] 术语表已载入 {len(glossary)} 条。")
    top_provider = providers[0]
    stats.top_provider = top_provider

    max_workers = max(
        1,
        min(
            int(getattr(cfg, "max_concurrent_requests", DEFAULT_MAX_CONCURRENT_REQUESTS) or 1),
            _MAX_CONCURRENT_REQUESTS_CAP,
            len(batches),
        ),
    )

    completed_lock = threading.Lock()
    completed_count = 0

    def _apply_and_report(report: BatchReport) -> None:
        if report.ok and report.texts is not None:
            for j, text in enumerate(report.texts):
                idx = report.start + j
                if 0 <= idx < n:
                    out[idx] = text
        _record_batch(stats, report, top_provider)

    def _report_done() -> None:
        nonlocal completed_count
        with completed_lock:
            completed_count += 1
            idx = completed_count
        if on_batch_done:
            on_batch_done(idx, len(batches))

    def _run_one(idx: int, batch: Tuple[int, int, List[str], List[str], List[str]]) -> BatchReport:
        start, end, batch_texts, before, after = batch
        report = _translate_one_batch(
            idx, len(batches), start, end, batch_texts, before, after,
            glossary, cfg, providers, frame_images, cancel_check,
        )
        if report.ok:
            _cfg_log(
                cfg,
                f"[翻译] 批次 {idx + 1}/{len(batches)}（行 {start}–{end - 1}）"
                f"→ {report.provider}"
                + (f"，缩译 {report.shortened_lines} 行" if report.shortened_lines else ""),
            )
        else:
            _cfg_log(
                cfg,
                f"[翻译] 批次 {idx + 1}/{len(batches)}（行 {start}–{end - 1}）"
                f"失败（{report.reason}），已保留本批原文。",
            )
        return report

    if max_workers <= 1:
        for idx, batch in enumerate(batches):
            if cancel_check():
                logger.info("[font_intel.translation] cancelled; keeping remaining originals.")
                break
            report = _run_one(idx, batch)
            _apply_and_report(report)
            _report_done()
    else:
        # 并发路径：批次之间相互独立（各自写回互不重叠的下标区间）。
        logger.info(
            "[font_intel.translation] translating %d lines in %d batches (concurrency=%d).",
            n, len(batches), max_workers,
        )
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = {}
            for idx, batch in enumerate(batches):
                if cancel_check():
                    logger.info("[font_intel.translation] cancelled; keeping remaining originals.")
                    break
                fut = executor.submit(_run_one, idx, batch)
                futures[fut] = idx
            for fut in as_completed(futures):
                report = fut.result()
                _apply_and_report(report)
                _report_done()

    _cfg_log(
        cfg,
        "[翻译] 完成：{} 批成功 / {} 批失败 / {} 批降级（共 {} 批，"
        "提供方分布 {}，缩译 {} 行）。".format(
            stats.batches_success, stats.batches_failed, stats.batches_degraded,
            stats.batches_total, stats.provider_batches or "无", stats.shortened_lines,
        ),
    )
    return TranslationResult(out, stats)


def _record_batch(stats: TranslationStats, report: BatchReport, top_provider: str) -> None:
    """主线程侧记账（并发路径下由 as_completed 循环串行调用，无竞争）。"""
    stats.batches.append(report)
    stats.batches_attempted += 1
    if report.ok:
        stats.batches_success += 1
        stats.provider_batches[report.provider] = (
            stats.provider_batches.get(report.provider, 0) + 1
        )
        stats.shortened_lines += report.shortened_lines
        if report.provider != top_provider:
            stats.batches_degraded += 1
    else:
        stats.batches_failed += 1
