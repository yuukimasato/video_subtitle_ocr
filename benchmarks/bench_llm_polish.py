# benchmarks/bench_llm_polish.py
"""计时 harness：polish_subtitle_texts 在假服务器下的墙钟耗时。

用法：
    .venv/bin/python benchmarks/bench_llm_polish.py [N条字幕] [延迟秒]
"""
from __future__ import annotations

import sys
import time
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from fake_llm_server import serve  # noqa: E402

from core.subtitle_llm_polish import SubtitlePolisherConfig, polish_subtitle_texts  # noqa: E402


def main() -> None:
    n_lines = int(sys.argv[1]) if len(sys.argv) > 1 else 400
    latency = float(sys.argv[2]) if len(sys.argv) > 2 else 0.3

    server, port, _stats = serve(latency)
    try:
        texts = [f"这是第{i}条测试字幕文本，用于计时。" for i in range(n_lines)]
        cfg = SubtitlePolisherConfig(
            api_key="test-key",
            api_base_url=f"http://127.0.0.1:{port}/v1",
            model="fake-model",
            text_polish_enabled=True,
        )
        t0 = time.perf_counter()
        out = polish_subtitle_texts(texts, cfg)
        elapsed = time.perf_counter() - t0
        assert len(out) == n_lines, f"length mismatch: {len(out)} != {n_lines}"
        batches = (n_lines + 39) // 40
        print(
            f"lines={n_lines} latency={latency}s batches={batches} "
            f"elapsed={elapsed:.2f}s throughput={n_lines / elapsed:.0f} lines/s"
        )
    finally:
        server.shutdown()


if __name__ == "__main__":
    main()
