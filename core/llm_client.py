"""OpenAI-compatible LLM client.

轻量级 LLM 客户端，支持任意 OpenAI 兼容 API。
"""

import os
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

# Bounded backoff: max 10 attempts AND max 5 minutes total wait; after that the
# caller degrades gracefully (e.g. keeps unpolished subtitles) instead of hanging.
_LLM_RETRY_MAX_ATTEMPTS = 10
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
    wait=wait_random_exponential(multiplier=1, min=5, max=60),
    retry=retry_if_exception_type(openai.RateLimitError),
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
