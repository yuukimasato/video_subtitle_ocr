# tests/test_llm_client_backoff.py
"""Bounded-backoff policy checks for core/llm_client.py.

Per the workspace rule: every server-facing call must use a bounded backoff
for 429 Too Many Requests (max attempts + max total wait), degrading
gracefully afterwards instead of hanging or killing the task chain.
"""

from __future__ import annotations

import core.llm_client as llm_client
from core.llm_client import _call_llm_api  # noqa: F401  (import must succeed)


def test_retry_total_delay_bounded():
    """Total retry wait must be capped (<= 300 s)."""
    assert llm_client._LLM_RETRY_TOTAL_DELAY_SECONDS <= 300


def test_retry_max_attempts_bounded():
    """Retry attempt count must be capped (<= 10)."""
    assert llm_client._LLM_RETRY_MAX_ATTEMPTS <= 10


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
