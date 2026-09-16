# tests/test_llm_api_error_degradation.py
"""LLM 服务端失败必须优雅降级，而不是击穿整条任务链。

工作区规则：429 有界退避耗尽后应优雅降级或返回可恢复错误。
core.llm_client.call_llm 把 SDK 级失败（openai.OpenAIError 家族、
tenacity.RetryError）归一化为 LlmApiError(RuntimeError)，
下游 subtitle_llm_polish 的各 except RuntimeError 分支才能接住。
"""

from __future__ import annotations

import urllib.error

import pytest

import core.llm_client as llm_client
import core.subtitle_llm_polish as sp
from core.llm_client import LlmApiError, call_llm
from core.subtitle_llm_polish import SubtitlePolisherConfig


def _polisher_cfg() -> SubtitlePolisherConfig:
    return SubtitlePolisherConfig(api_key="sk-test")


def _raise_llm_api_error(*args, **kwargs):
    raise LlmApiError("LLM API call failed after bounded retries: RateLimitError: 429")


# ── call_llm 归一化 SDK 异常 ──────────────────────────────────


def test_llm_api_error_is_recoverable_runtime_error():
    assert issubclass(LlmApiError, RuntimeError)


def test_call_llm_wraps_openai_connection_error(monkeypatch):
    openai = pytest.importorskip("openai")
    if not hasattr(openai, "APIConnectionError"):
        pytest.skip("stub openai module")

    class _FakeCompletions:
        def create(self, **kwargs):
            raise openai.APIConnectionError(request=None)

    class _FakeChat:
        completions = _FakeCompletions()

    class _FakeClient:
        chat = _FakeChat()

    monkeypatch.setattr(llm_client, "get_llm_client", lambda **kw: _FakeClient())
    with pytest.raises(RuntimeError) as excinfo:
        call_llm(messages=[{"role": "user", "content": "x"}])
    assert not isinstance(excinfo.value, openai.APIConnectionError)
    assert isinstance(excinfo.value, LlmApiError)


def test_call_llm_wraps_retry_exhaustion(monkeypatch):
    """429 重试耗尽后必须抛 LlmApiError（RuntimeError），而不是 tenacity.RetryError。"""
    openai = pytest.importorskip("openai")
    tenacity = pytest.importorskip("tenacity")
    retrying = getattr(llm_client._call_llm_api, "retry", None)
    if retrying is None:
        pytest.skip("stub tenacity: retry policy not active")

    class _FakeCompletions:
        def create(self, **kwargs):
            # 绕过 SDK __init__（需要真实 httpx.Response）：仅构造类型实例
            # 供 tenacity 的 isinstance 重试判定与重试耗尽路径使用。
            err = openai.RateLimitError.__new__(openai.RateLimitError)
            BaseException.__init__(err, "429 Too Many Requests")
            raise err

    class _FakeChat:
        completions = _FakeCompletions()

    class _FakeClient:
        chat = _FakeChat()

    monkeypatch.setattr(llm_client, "get_llm_client", lambda **kw: _FakeClient())
    monkeypatch.setattr(retrying, "wait", tenacity.wait_fixed(0))
    monkeypatch.setattr(retrying, "stop", tenacity.stop_after_attempt(2))
    with pytest.raises(LlmApiError) as excinfo:
        call_llm(messages=[{"role": "user", "content": "x"}])
    assert "429" in str(excinfo.value)


# ── subtitle_llm_polish 各入口的优雅降级 ──────────────────────


def test_fragment_merge_degrades_on_llm_api_error(monkeypatch):
    monkeypatch.setattr(sp, "call_llm", _raise_llm_api_error)
    out = sp.deepseek_merge_fragment_text(_polisher_cfg(), ["甲", "乙"])
    assert out is None


def test_strategy_review_degrades_on_llm_api_error(monkeypatch):
    monkeypatch.setattr(sp, "call_llm", _raise_llm_api_error)
    out = sp.deepseek_suggest_merge_params(_polisher_cfg(), [], [], {})
    assert out is None


def test_classify_text_source_degrades_on_llm_api_error(monkeypatch):
    monkeypatch.setattr(sp, "call_llm", _raise_llm_api_error)
    out = sp.classify_text_source(_polisher_cfg(), "你好")
    assert out is None


def test_polish_one_batch_degrades_on_llm_api_error(monkeypatch):
    monkeypatch.setattr(sp, "call_llm", _raise_llm_api_error)
    mapping = sp._polish_one_batch(
        _polisher_cfg(), 0, 1, 0, 2, ["0", "1"],
        [{"id": "0", "text": "甲"}, {"id": "1", "text": "乙"}],
        cancel_check=lambda: False,
    )
    assert mapping == {}


def test_polish_concurrent_path_degrades_on_llm_api_error(monkeypatch):
    """并发路径下单个批次 API 失败不得击穿 polish_subtitle_texts。"""
    monkeypatch.setattr(sp, "call_llm", _raise_llm_api_error)
    texts = ["甲", "乙", "丙", "丁"]
    out = sp.polish_subtitle_texts(
        texts,
        SubtitlePolisherConfig(api_key="sk-test", batch_size=1, max_concurrent_requests=2),
    )
    assert out == texts


# ── GET /v1/models 的 429 有界退避 ────────────────────────────


class _Fake429Response:
    code = 429
    reason = "Too Many Requests"
    fp = None

    def read(self) -> bytes:
        return b""

    def close(self) -> None:
        pass


def test_fetch_models_retries_429_then_succeeds(monkeypatch):
    payload = b'{"data": [{"id": "m1"}, {"id": "m2"}]}'
    attempts = {"n": 0}

    def fake_urlopen(req, timeout, context):
        attempts["n"] += 1
        if attempts["n"] == 1:
            raise urllib.error.HTTPError("u", 429, "Too Many Requests", {}, _Fake429Response())
        import io

        return io.BytesIO(payload)

    monkeypatch.setattr(sp.urllib.request, "urlopen", fake_urlopen)
    sleeps: list = []
    monkeypatch.setattr(sp.time, "sleep", lambda s: sleeps.append(s))

    ids = sp.fetch_openai_compatible_model_ids("sk-test", "https://api.example.com")
    assert ids == ["m1", "m2"]
    assert attempts["n"] == 2
    assert sleeps and 0 < sleeps[0] <= 5.0


def test_fetch_models_429_exhaustion_returns_recoverable_error(monkeypatch):
    attempts = {"n": 0}

    def fake_urlopen(req, timeout, context):
        attempts["n"] += 1
        raise urllib.error.HTTPError("u", 429, "Too Many Requests", {}, _Fake429Response())

    monkeypatch.setattr(sp.urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setattr(sp.time, "sleep", lambda s: None)

    with pytest.raises(RuntimeError, match="429"):
        sp.fetch_openai_compatible_model_ids("sk-test", "https://api.example.com")
    assert attempts["n"] == sp._MODELS_RETRY_MAX_ATTEMPTS + 1


def test_fetch_models_non_429_fails_fast(monkeypatch):
    attempts = {"n": 0}

    def fake_urlopen(req, timeout, context):
        attempts["n"] += 1
        raise urllib.error.HTTPError("u", 401, "Unauthorized", {}, _Fake429Response())

    monkeypatch.setattr(sp.urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setattr(sp.time, "sleep", lambda s: None)

    with pytest.raises(RuntimeError, match="401"):
        sp.fetch_openai_compatible_model_ids("sk-test", "https://api.example.com")
    assert attempts["n"] == 1


def test_fetch_models_retry_bounds_documented():
    """有界退避常量必须存在且受限（工作区规则）。"""
    assert sp._MODELS_RETRY_MAX_ATTEMPTS <= 5
    assert sp._MODELS_RETRY_TOTAL_WAIT_SECONDS <= 60
