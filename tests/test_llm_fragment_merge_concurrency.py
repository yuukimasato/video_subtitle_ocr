# tests/test_llm_fragment_merge_concurrency.py
"""碎片合并（_llm_merge_fragmented_events）分组/组装/并发行为测试。

用最小宿主类桩掉 mixin 依赖的几何/时间辅助方法，配合本地假 OpenAI 兼容
服务器（真实 deepseek_merge_fragment_text，取最长候选为合并结果）验证：
- 分组后各组并发请求且互不串扰；
- 合并事件按原顺序组装，时间轴取组首/组尾，tags 取中位数桩返回值；
- 取消时返回未合并的原始排序事件。
"""

from __future__ import annotations

import os
import sys

TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(TESTS_DIR)
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)
sys.path.insert(0, os.path.join(PROJECT_ROOT, "benchmarks"))

from fake_llm_server import serve  # noqa: E402

from core.subtitle_generator.llm_merge import _LlmMergeMixin  # noqa: E402
from core.subtitle_llm_polish import SubtitlePolisherConfig  # noqa: E402


class _Host(_LlmMergeMixin):
    """只桩掉本 mixin 用到的辅助方法，不依赖 OCRToASSOptimizer 其余部分。"""

    def _parse_ass_time_to_seconds(self, t: str) -> float:
        h, m, s = str(t).split(":")
        return int(h) * 3600 + int(m) * 60 + float(s)

    def _scene_tags_close(self, a: str, b: str) -> bool:
        return True

    def _dialogue_time_gap_seconds(self, end: str, start: str) -> float:
        return self._parse_ass_time_to_seconds(start) - self._parse_ass_time_to_seconds(end)

    def _is_noise_body(self, body: str) -> bool:
        return False

    def _median_scene_tags(self, grp) -> str:
        return grp[0].get("tags", "")


def _scene(roi, start, end, body, tags="pos(100,900)"):
    return {
        "roi": roi,
        "start_time": start,
        "end_time": end,
        "style": "Scene",
        "tags": tags,
        "body": body,
    }


def _dialogue(roi, start, end, body):
    ev = _scene(roi, start, end, body)
    ev["style"] = "Dialog"
    return ev


def _make_cfg(port, **overrides):
    kwargs = dict(
        api_key="test-key",
        api_base_url=f"http://127.0.0.1:{port}/v1",
        model="fake-model",
        fragment_merge_enabled=True,
    )
    kwargs.update(overrides)
    return SubtitlePolisherConfig(**kwargs)


def test_groups_merged_in_order_with_concurrent_requests():
    # 两个多事件 Scene 组 + 中间一条 Dialog + 末尾单事件 Scene。
    events = [
        _scene("roi_0", "0:00:01.00", "0:00:01.10", "短"),
        _scene("roi_0", "0:00:01.10", "0:00:01.30", "短一点的那个"),
        _dialogue("roi_0", "0:00:02.00", "0:00:03.00", "正常台词"),
        _scene("roi_1", "0:00:05.00", "0:00:05.20", "甲"),
        _scene("roi_1", "0:00:05.20", "0:00:05.40", "乙比甲长"),
        _scene("roi_1", "0:00:05.40", "0:00:05.60", "丙是最长的一条"),
        _scene("roi_2", "0:00:09.00", "0:00:09.50", "孤立的场景字"),
    ]
    server, port, stats = serve(latency_sec=0.2)
    try:
        host = _Host()
        merged = host._llm_merge_fragmented_events(
            events, _make_cfg(port, max_concurrent_requests=4)
        )
    finally:
        server.shutdown()

    # 7 条 -> 4 条；服务器回显「最长候选」为合并结果。
    assert [ev["body"] for ev in merged] == [
        "短一点的那个",
        "正常台词",
        "丙是最长的一条",
        "孤立的场景字",
    ]
    # 时间轴：组首 start / 组尾 end。
    assert merged[0]["start_time"] == "0:00:01.00"
    assert merged[0]["end_time"] == "0:00:01.30"
    assert merged[2]["start_time"] == "0:00:05.00"
    assert merged[2]["end_time"] == "0:00:05.60"
    # 两个多事件组各发一次请求，且并发重叠。
    assert stats["total_requests"] == 2
    assert stats["max_active"] >= 2


def test_cancel_returns_unmerged_events():
    events = [
        _scene("roi_0", "0:00:01.00", "0:00:01.10", "甲"),
        _scene("roi_0", "0:00:01.10", "0:00:01.30", "乙乙乙乙"),
    ]
    server, port, stats = serve(latency_sec=0.05)
    try:
        host = _Host()
        merged = host._llm_merge_fragmented_events(
            events, _make_cfg(port), cancel_check=lambda: True
        )
    finally:
        server.shutdown()

    assert merged is events or merged == events  # 原样返回（仅排序后的副本）
    assert stats["total_requests"] == 0
