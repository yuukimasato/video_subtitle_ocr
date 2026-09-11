# tests/test_llm_polish_concurrency.py
"""并发润色（polish_subtitle_texts）行为测试。

用本地假 OpenAI 兼容服务器（benchmarks/fake_llm_server.py，回显文本并追加
「!」标记）验证：
- 并发开启时请求确实重叠（max_active >= 2），结果仍按正确下标写回；
- 串行模式（max_concurrent_requests=1）行为不变；
- 单批失败不影响其它批次（失败批保留原文）；
- 取消后不再润色剩余批次。
"""

from __future__ import annotations

import os
import sys
import time
import urllib.error

import pytest

TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(TESTS_DIR)
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)
sys.path.insert(0, os.path.join(PROJECT_ROOT, "benchmarks"))

from fake_llm_server import serve  # noqa: E402

import core.subtitle_llm_polish as polish_mod  # noqa: E402
from core.subtitle_llm_polish import SubtitlePolisherConfig, polish_subtitle_texts  # noqa: E402


def _make_cfg(port: str, **overrides) -> SubtitlePolisherConfig:
    kwargs = dict(
        api_key="test-key",
        api_base_url=f"http://127.0.0.1:{port}/v1",
        model="fake-model",
        text_polish_enabled=True,
    )
    kwargs.update(overrides)
    return SubtitlePolisherConfig(**kwargs)


def test_concurrent_batches_overlap_and_apply_to_correct_indices():
    n = 320  # 8 批（batch_size=40）
    texts = [f"L{i}" for i in range(n)]
    server, port, stats = serve(latency_sec=0.2)
    try:
        cfg = _make_cfg(port, max_concurrent_requests=4)
        t0 = time.perf_counter()
        out = polish_subtitle_texts(texts, cfg)
        elapsed = time.perf_counter() - t0
    finally:
        server.shutdown()

    # 每条都被润色且写回正确下标（服务器给每条追加了「!」）。
    assert out == [f"L{i}!" for i in range(n)]
    assert stats["total_requests"] == 8
    # 并发确实发生了重叠。
    assert stats["max_active"] >= 2
    # 8 批 × 0.2s 全串行 ≈ 1.6s；4 并发理想 ≈ 0.4s。留足调度余量。
    assert elapsed < 1.2, f"expected ~0.4s with concurrency=4, got {elapsed:.2f}s"


def test_serial_mode_when_concurrency_is_one():
    n = 120  # 3 批
    texts = [f"L{i}" for i in range(n)]
    server, port, stats = serve(latency_sec=0.05)
    try:
        cfg = _make_cfg(port, max_concurrent_requests=1)
        out = polish_subtitle_texts(texts, cfg)
    finally:
        server.shutdown()

    assert out == [f"L{i}!" for i in range(n)]
    assert stats["total_requests"] == 3
    assert stats["max_active"] == 1


def test_single_batch_failure_keeps_its_originals_only(monkeypatch):
    n = 120  # 3 批；第 2 批（行 40–79）注入失败标记
    texts = [f"L{i}" if i < 40 or i >= 80 else f"FAILMARK{i}" for i in range(n)]

    real_post_chat = polish_mod._post_chat

    def flaky_post_chat(cfg, messages, **kwargs):
        user = messages[-1]["content"]
        if "FAILMARK" in user:
            raise urllib.error.URLError("simulated network failure")
        return real_post_chat(cfg, messages, **kwargs)

    monkeypatch.setattr(polish_mod, "_post_chat", flaky_post_chat)

    server, port, stats = serve(latency_sec=0.02)
    try:
        cfg = _make_cfg(port, max_concurrent_requests=3)
        out = polish_subtitle_texts(texts, cfg)
    finally:
        server.shutdown()

    # 第 2 批保留原文，其余两批已润色。
    assert out[:40] == [f"L{i}!" for i in range(40)]
    assert out[40:80] == [f"FAILMARK{i}" for i in range(40, 80)]
    assert out[80:] == [f"L{i}!" for i in range(80, 120)]
    assert stats["total_requests"] == 2  # 失败批只调了一次（异常，不重试）


def test_cancel_prevents_all_requests():
    n = 80
    texts = [f"L{i}" for i in range(n)]
    server, port, stats = serve(latency_sec=0.05)
    try:
        cfg = _make_cfg(port, max_concurrent_requests=4)
        out = polish_subtitle_texts(texts, cfg, cancel_check=lambda: True)
    finally:
        server.shutdown()

    assert out == texts  # 全部保留原文
    assert stats["total_requests"] == 0


def test_progress_reports_are_monotonic():
    n = 160  # 4 批
    texts = [f"L{i}" for i in range(n)]
    reports = []

    server, port, _stats = serve(latency_sec=0.05)
    try:
        cfg = _make_cfg(port, max_concurrent_requests=4)
        polish_subtitle_texts(
            texts, cfg, on_batch_done=lambda idx, total: reports.append((idx, total))
        )
    finally:
        server.shutdown()

    assert [idx for idx, _ in reports] == [1, 2, 3, 4]  # 完成计数单调递增
    assert all(total == 4 for _, total in reports)


@pytest.mark.parametrize("bad_value", [0, -3])
def test_invalid_concurrency_falls_back_to_serial(bad_value):
    n = 80
    texts = [f"L{i}" for i in range(n)]
    server, port, stats = serve(latency_sec=0.01)
    try:
        cfg = _make_cfg(port, max_concurrent_requests=bad_value)
        out = polish_subtitle_texts(texts, cfg)
    finally:
        server.shutdown()

    assert out == [f"L{i}!" for i in range(n)]
    assert stats["max_active"] == 1
