"""OpenAI-compatible LLM client.

轻量级 LLM 客户端，支持任意 OpenAI 兼容 API。
"""

import logging
import os
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Any, List, Optional

import openai
import tenacity
from openai import OpenAI
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    stop_after_delay,
    wait_random_exponential,
)

logger = logging.getLogger(__name__)

# Bounded backoff: max 10 attempts, each individual wait <= 60s, and total wait
# <= 5 minutes; after that the caller degrades gracefully (e.g. keeps
# unpolished subtitles) instead of hanging. 429 responses may carry
# Retry-After; it is honored but clamped to the same per-wait/total bounds.
_LLM_RETRY_MAX_ATTEMPTS = 10
_LLM_RETRY_MAX_WAIT_SECONDS = 60
_LLM_RETRY_TOTAL_DELAY_SECONDS = 300


class LlmApiError(RuntimeError):
    """LLM 服务端调用失败（连接失败/超时/429 有界退避耗尽等）。

    统一归一化为 RuntimeError 子类，调用方按可恢复错误处理并优雅降级
    （如保留未润色的原字幕），避免服务端故障导致整条任务链退出。
    """


def _llm_api_failure_types() -> tuple:
    """SDK 级失败异常集合；离线 stub 环境缺失的属性自动跳过。"""
    types: list = []
    seen: set = set()
    for mod in (openai, tenacity):
        for attr in ("OpenAIError", "APIError", "RateLimitError", "RetryError"):
            exc = getattr(mod, attr, None)
            if (
                isinstance(exc, type)
                and issubclass(exc, BaseException)
                and exc not in seen
            ):
                seen.add(exc)
                types.append(exc)
    return tuple(types)


def parse_retry_after_value(raw: Any) -> Optional[float]:
    """解析 Retry-After 头为等待秒数（支持 delay-seconds 与 HTTP-date）。

    负值钳为 0；无法解析返回 None，由调用方的有界退避兜底。
    """
    if raw is None:
        return None
    text = str(raw).strip()
    if not text:
        return None
    try:
        return max(0.0, float(text))
    except ValueError:
        pass
    try:
        dt = parsedate_to_datetime(text)
        if dt is None:
            return None
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return max(0.0, (dt - datetime.now(timezone.utc)).total_seconds())
    except Exception:
        return None


def retry_after_seconds_from_exception(exc: Optional[BaseException]) -> Optional[float]:
    """从异常携带的 HTTP 响应头读取 Retry-After 秒数（取不到返回 None）。

    openai SDK 的 APIError 把响应放在 ``.response.headers``（httpx，大小写
    不敏感），urllib 的 HTTPError 直接放在 ``.headers``；两种形态都兼容。
    """
    if exc is None:
        return None
    headers = getattr(getattr(exc, "response", None), "headers", None)
    if headers is None:
        headers = getattr(exc, "headers", None)
    if headers is None:
        return None
    raw = None
    for name in ("Retry-After", "retry-after"):
        try:
            raw = headers.get(name)
        except Exception:
            return None
        if raw is not None:
            break
    return parse_retry_after_value(raw)


# full-jitter 指数退避作为等待基线（实例无状态，可在多次调用间复用）。
_jitter_wait = wait_random_exponential(
    multiplier=1, min=5, max=_LLM_RETRY_MAX_WAIT_SECONDS
)


def _llm_rate_limit_wait(retry_state: "tenacity.RetryCallState") -> float:
    """计算 429 退避的单次等待时长。

    以 full-jitter 指数退避为基线；若 429 响应携带 Retry-After 则尊重其指引
    （仍受单次等待上限约束）。所有等待同时受两级预算约束：单次不超过
    ``_LLM_RETRY_MAX_WAIT_SECONDS``，累计不超过 ``_LLM_RETRY_TOTAL_DELAY_SECONDS``
    ——tenacity 的 stop_after_delay 允许最后一次 sleep 超出总预算，这里按剩余
    预算截断每次等待，保证总等待时间精确有界。
    """
    wait = float(_jitter_wait(retry_state))
    outcome = retry_state.outcome
    retry_after = retry_after_seconds_from_exception(
        outcome.exception() if outcome is not None else None
    )
    if retry_after is not None:
        wait = max(wait, min(retry_after, float(_LLM_RETRY_MAX_WAIT_SECONDS)))
    remaining = _LLM_RETRY_TOTAL_DELAY_SECONDS - (retry_state.seconds_since_start or 0.0)
    return max(0.0, min(wait, remaining))


def _log_rate_limit_retry(retry_state: "tenacity.RetryCallState") -> None:
    """429 每次退避等待前打点，让日志能解释管线里的长时间停顿。"""
    action = retry_state.next_action
    outcome = retry_state.outcome
    exc = outcome.exception() if outcome is not None else None
    detail = f"{type(exc).__name__}: {exc}"[:200] if exc is not None else ""
    logger.warning(
        "LLM API 429: retry %d/%d in %.1fs (elapsed %.1fs of %ds wait budget) %s",
        retry_state.attempt_number,
        _LLM_RETRY_MAX_ATTEMPTS,
        action.sleep if action is not None else 0.0,
        retry_state.seconds_since_start or 0.0,
        _LLM_RETRY_TOTAL_DELAY_SECONDS,
        detail,
    )


def normalize_base_url(base_url: str) -> str:
    """规范化 API base URL，确保 /v1 后缀"""
    from urllib.parse import urlparse, urlunparse

    url = base_url.strip()
    parsed = urlparse(url)
    path = parsed.path.rstrip("/")

    if not path:
        path = "/v1"

    normalized = urlunparse(
        (
            parsed.scheme,
            parsed.netloc,
            path,
            parsed.params,
            parsed.query,
            parsed.fragment,
        )
    )

    return normalized


def get_llm_client(
    base_url: Optional[str] = None,
    api_key: Optional[str] = None,
    timeout: Optional[float] = None,
) -> OpenAI:
    """获取 LLM 客户端实例。

    优先使用传入的参数，否则从环境变量读取。

    Args:
        base_url: API 基础 URL（可选，默认读取 OPENAI_BASE_URL）
        api_key: API 密钥（可选，默认读取 OPENAI_API_KEY）
        timeout: 请求超时秒数（可选；None/<=0 使用 openai 库默认值）

    Returns:
        OpenAI 客户端实例
    """
    base_url = base_url or os.getenv("OPENAI_BASE_URL", "").strip()
    api_key = api_key or os.getenv("OPENAI_API_KEY", "").strip()

    if not base_url or not api_key:
        raise ValueError(
            "OPENAI_BASE_URL and OPENAI_API_KEY must be provided "
            "either as arguments or as environment variables"
        )

    base_url = normalize_base_url(base_url)

    client_kwargs: dict = {
        "base_url": base_url,
        "api_key": api_key,
    }
    if timeout is not None and timeout > 0:
        client_kwargs["timeout"] = float(timeout)
    return OpenAI(**client_kwargs)


@retry(
    stop=(stop_after_attempt(_LLM_RETRY_MAX_ATTEMPTS) | stop_after_delay(_LLM_RETRY_TOTAL_DELAY_SECONDS)),
    wait=_llm_rate_limit_wait,
    retry=retry_if_exception_type(openai.RateLimitError),
    before_sleep=_log_rate_limit_retry,
    reraise=True,
)
def _call_llm_api(
    client: OpenAI,
    messages: List[dict],
    model: str,
    temperature: float = 1,
    timeout: Optional[float] = None,
    **kwargs: Any,
) -> Any:
    """实际调用 LLM API（带速率限制重试）"""
    if timeout is not None and timeout > 0:
        kwargs["timeout"] = float(timeout)
    response = client.chat.completions.create(
        model=model,
        messages=messages,  # pyright: ignore[reportArgumentType]
        temperature=temperature,
        **kwargs,
    )
    return response


def call_llm(
    messages: List[dict],
    model: str = "gpt-4o-mini",
    temperature: float = 1,
    base_url: Optional[str] = None,
    api_key: Optional[str] = None,
    client: Optional[OpenAI] = None,
    timeout: Optional[float] = None,
    **kwargs: Any,
) -> Any:
    """调用 LLM API。

    Args:
        messages: 对话消息列表 [{"role": "system", "content": "..."}, ...]
        model: 模型名称（默认 "gpt-4o-mini"）
        temperature: 温度参数（默认 1）
        base_url: API 基础 URL（可选）
        api_key: API 密钥（可选）
        client: 预初始化的 OpenAI 客户端（可选，优先级最高）
        timeout: 单次请求超时秒数（可选；None/<=0 使用 openai 库默认值）
        **kwargs: 传递给 API 的其他参数

    Returns:
        API 响应对象

    Raises:
        LlmApiError: SDK 级失败（连接失败/超时/429 有界退避耗尽）
        ValueError: API 返回空响应
    """
    if client is None:
        client = get_llm_client(base_url=base_url, api_key=api_key, timeout=timeout)

    try:
        response = _call_llm_api(client, messages, model, temperature, timeout=timeout, **kwargs)
    except _llm_api_failure_types() as e:
        raise LlmApiError(
            f"LLM API call failed after bounded retries: {type(e).__name__}: {e}"
        ) from e

    if not (
        response
        and hasattr(response, "choices")
        and response.choices
        and len(response.choices) > 0
        and hasattr(response.choices[0], "message")
        and response.choices[0].message.content
    ):
        raise ValueError("Invalid API response: empty choices or content")

    return response
