# tests/test_llm_client_backoff.py
"""Bounded-backoff policy checks for core/llm_client.py.

Per the workspace rule: every server-facing call must use a bounded backoff
for 429 Too Many Requests (max attempts + max total wait), degrading
gracefully afterwards instead of hanging or killing the task chain.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

import core.llm_client as llm_client
from core.llm_client import _call_llm_api  # noqa: F401  (import must succeed)


def _real_tenacity_or_skip() -> None:
    """Wait-strategy tests need the real retry loop; constants still hold on stubs."""
    if getattr(_call_llm_api, "retry", None) is None:
        pytest.skip("stub tenacity: retry loop not active")


class _WithRetryAfter(Exception):
    """Duck-typed 429 carrying urllib-style headers (HTTPError 形态)."""

    def __init__(self, headers):
        super().__init__("429 Too Many Requests")
        self.headers = headers


def _retry_state(elapsed: float, exc: BaseException | None = None) -> SimpleNamespace:
    outcome = None if exc is None else SimpleNamespace(exception=lambda: exc)
    return SimpleNamespace(
        attempt_number=3,
        outcome=outcome,
        seconds_since_start=elapsed,
        next_action=None,
    )


def test_retry_total_delay_bounded():
    """Total retry wait must be capped (<= 300 s)."""
    assert llm_client._LLM_RETRY_TOTAL_DELAY_SECONDS <= 300


def test_retry_max_attempts_bounded():
    """Retry attempt count must be capped (<= 10)."""
    assert llm_client._LLM_RETRY_MAX_ATTEMPTS <= 10


def test_retry_per_wait_bounded():
    """Each individual retry wait must be capped (<= 60 s)."""
    assert llm_client._LLM_RETRY_MAX_WAIT_SECONDS <= 60


def test_retry_stop_policy_combines_attempt_and_delay_limits():
    """The tenacity stop policy must combine stop_after_attempt AND stop_after_delay."""
    retrying = getattr(_call_llm_api, "retry", None)
    if retrying is None:
        # Stub tenacity (dependency not installed): constants already verified.
        policy = llm_client._call_llm_api._retry_policy
        assert policy.get("stop") is not None
        return

    stop = retrying.stop
    # stop_after_attempt(10) | stop_after_delay(300) -> tenacity.stop_any
    stops = getattr(stop, "stops", [stop])
    kinds = {type(s).__name__ for s in stops}
    assert "stop_after_attempt" in kinds
    assert "stop_after_delay" in kinds
    for s in stops:
        if type(s).__name__ == "stop_after_attempt":
            assert s.max_attempt_number <= llm_client._LLM_RETRY_MAX_ATTEMPTS
        if type(s).__name__ == "stop_after_delay":
            assert s.max_delay <= llm_client._LLM_RETRY_TOTAL_DELAY_SECONDS


def test_retry_only_retries_rate_limit_errors():
    """Only 429 RateLimitError is retried; anything else fails fast."""
    import openai
    from tenacity import retry_if_exception_type

    retrying = getattr(_call_llm_api, "retry", None)
    if retrying is None:
        return  # stub tenacity: nothing to introspect
    condition = retrying.retry
    assert isinstance(condition, retry_if_exception_type)
    stored = condition.exception_types
    stored = (stored,) if isinstance(stored, type) else tuple(stored)
    assert stored == (openai.RateLimitError,)


# ── 等待策略：单次上限 / 总预算 / Retry-After ─────────────────


def test_wait_without_retry_after_stays_in_bounds():
    """纯 full-jitter 等待也必须落在 [5, 60]s 区间内。"""
    _real_tenacity_or_skip()
    wait = llm_client._llm_rate_limit_wait(_retry_state(0.0))
    assert 5.0 <= wait <= llm_client._LLM_RETRY_MAX_WAIT_SECONDS


def test_wait_clamps_to_per_wait_cap():
    """Retry-After 再大，单次等待也不超过 60s 上限。"""
    _real_tenacity_or_skip()
    state = _retry_state(0.0, _WithRetryAfter({"Retry-After": "500"}))
    assert llm_client._llm_rate_limit_wait(state) == pytest.approx(
        llm_client._LLM_RETRY_MAX_WAIT_SECONDS
    )


def test_wait_honors_retry_after_header():
    """Retry-After 在上限内时被尊重（30s 指引 → 等待 30s）。"""
    _real_tenacity_or_skip()
    state = _retry_state(0.0, _WithRetryAfter({"Retry-After": "30"}))
    assert llm_client._llm_rate_limit_wait(state) == pytest.approx(30.0)


def test_wait_never_overshoots_total_wait_budget():
    """tenacity stop_after_delay 允许最后一次 sleep 超预算；等待策略必须按剩余预算截断。"""
    _real_tenacity_or_skip()
    elapsed = llm_client._LLM_RETRY_TOTAL_DELAY_SECONDS - 1.0
    state = _retry_state(elapsed, _WithRetryAfter({"Retry-After": "500"}))
    wait = llm_client._llm_rate_limit_wait(state)
    assert 0.0 <= wait <= llm_client._LLM_RETRY_TOTAL_DELAY_SECONDS - elapsed


# ── Retry-After 头解析 ────────────────────────────────────────


def test_parse_retry_after_value():
    assert llm_client.parse_retry_after_value("5") == 5.0
    assert llm_client.parse_retry_after_value(" 0.5 ") == pytest.approx(0.5)
    assert llm_client.parse_retry_after_value("-2") == 0.0
    # 过去的 HTTP-date 钳为 0（立即重试，交给上限兜底）
    assert llm_client.parse_retry_after_value("Wed, 21 Oct 2015 07:28:00 GMT") == 0.0
    assert llm_client.parse_retry_after_value("not-a-date") is None
    assert llm_client.parse_retry_after_value(None) is None
    assert llm_client.parse_retry_after_value("") is None


def test_retry_after_extracted_from_openai_rate_limit_error():
    """openai SDK 的 429（.response.headers，大小写不敏感）也能取到 Retry-After。"""
    openai = pytest.importorskip("openai")
    httpx = pytest.importorskip("httpx")
    _real_tenacity_or_skip()

    request = httpx.Request("POST", "https://api.example.com/v1/chat/completions")
    response = httpx.Response(429, headers={"retry-after": "7"}, request=request)
    try:
        err = openai.RateLimitError("429 Too Many Requests", response=response, body=None)
    except TypeError:
        pytest.skip("stub openai: RateLimitError signature differs")
    assert llm_client.retry_after_seconds_from_exception(err) == 7.0


def test_sdk_retries_disabled_so_outer_budget_counts_http_attempts(monkeypatch):
    import httpx
    import openai
    from tenacity import stop_after_attempt, wait_none

    _real_tenacity_or_skip()
    attempts = []

    def respond(request):
        attempts.append(request)
        return httpx.Response(429, json={
            'error': {'message': 'rate limited', 'type': 'rate_limit_error',
                      'code': 'rate_limit_exceeded'}})

    real_openai = openai.OpenAI
    transport = httpx.MockTransport(respond)
    monkeypatch.setattr(llm_client, 'OpenAI', lambda **kw: real_openai(
        **kw, http_client=httpx.Client(transport=transport)))
    with llm_client.get_llm_client('https://test.invalid/v1', 'test-key') as client:
        assert client.max_retries == 0
        call = _call_llm_api.retry_with(stop=stop_after_attempt(3), wait=wait_none())
        with pytest.raises(openai.RateLimitError):
            call(client, [{'role': 'user', 'content': 'hello'}], 'test', 0)
    assert len(attempts) == 3
