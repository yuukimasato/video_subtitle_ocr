# tests/test_text_filter_policy.py
"""Per-ROI text filter policy + watermark filter integration in the generator.

Manual ROIs (policy "keep_all") must never lose lines to the text-source
filter; auto-detected band ROIs (policy "auto") do. Watermark removal runs
independently of the source-filter switch and of the ROI policy.
"""

from __future__ import annotations

from core.subtitle_generator import FrameData, OCRToASSOptimizer, TextLine


def _make_optimizer(tmp_path, policies=None, watermark_config=None,
                    source_filter_config=None) -> OCRToASSOptimizer:
    return OCRToASSOptimizer(
        video_path="video.mp4",
        output_path=str(tmp_path / "out.ass"),
        fps=24.0,
        width=1280,
        height=720,
        source_filter_config=source_filter_config,
        roi_text_filter_policies=policies,
        watermark_filter_config=watermark_config,
    )


def _frame(roi_id: str, frame_num: int, text: str) -> FrameData:
    line = TextLine(
        text=text,
        score=0.95,
        box=(100, 600, 500, 640),
        polygon=[(100, 600), (500, 600), (500, 640), (100, 640)],
    )
    return FrameData(frame_num=frame_num, time_sec=frame_num / 24.0, lines=[line])


FILTER_OFF = {"enabled": False}
FILTER_ON_DROP_ALL = {"enabled": True, "keep_overlay": False, "keep_scene": False, "keep_unknown": False}


def test_keep_all_roi_survives_aggressive_filter(tmp_path):
    optimizer = _make_optimizer(
        tmp_path,
        policies={"roi_0": "keep_all", "roi_1": "auto"},
        source_filter_config=FILTER_ON_DROP_ALL,
    )
    organized = {
        "roi_0": [_frame("roi_0", 10, "场景招牌文字")],
        "roi_1": [_frame("roi_1", 10, "场景招牌文字")],
    }
    result = optimizer._classify_and_filter_text_lines(organized)

    assert len(result["roi_0"]) == 1      # manual ROI: never filtered
    assert "roi_1" not in result          # auto ROI: dropped by preset


def test_filter_disabled_keeps_everything(tmp_path):
    optimizer = _make_optimizer(
        tmp_path,
        policies={"roi_0": "auto"},
        source_filter_config=FILTER_OFF,
    )
    organized = {"roi_0": [_frame("roi_0", 10, "任意文本")]}
    result = optimizer._classify_and_filter_text_lines(organized)
    # keep_* 全开（FILTER_OFF 默认）时行全部保留。
    assert len(result["roi_0"]) == 1
    assert result["roi_0"][0].lines[0].text == "任意文本"


def test_unknown_roi_defaults_to_keep_all(tmp_path):
    optimizer = _make_optimizer(tmp_path, source_filter_config=FILTER_ON_DROP_ALL)
    organized = {"roi_7": [_frame("roi_7", 5, "未登记策略的 ROI")]}
    result = optimizer._classify_and_filter_text_lines(organized)
    assert len(result["roi_7"]) == 1


def test_temporal_stats_fill_real_values(tmp_path):
    optimizer = _make_optimizer(tmp_path)
    frames = [_frame("roi_0", f, "同一句台词") for f in range(24, 96)]
    stats = optimizer._build_temporal_stats({"roi_0": frames})
    entry = stats["by_text"]["同一句台词"]
    assert entry["first"] == 24 and entry["last"] == 95 and entry["count"] == 72

    features = optimizer._build_text_region_features(
        frames[0].lines[0], frames[0], stats
    )
    assert features.first_seen_frame == 24
    assert features.last_seen_frame == 95
    assert features.appearance_count == 72
    assert features.duration_sec == pytest_approx(72 / 24.0)
    assert features.is_stationary is True
    assert 0.0 <= features.text_stability <= 1.0


def pytest_approx(expected):
    import pytest
    return pytest.approx(expected)


def test_watermark_filter_drops_lines_regardless_of_policy(tmp_path):
    watermark_config = {
        "enabled": True,
        "entries": [{"text": "示例水印", "bbox": (800, 40, 1100, 60)}],
    }
    optimizer = _make_optimizer(tmp_path, watermark_config=watermark_config)

    def frame_with_watermark(roi_id, frame_num):
        frame = _frame(roi_id, frame_num, "正常台词内容")
        frame.lines.append(TextLine(
            text="示例水印",
            score=0.95,
            box=(800, 40, 1100, 60),
            polygon=[(800, 40), (1100, 40), (1100, 60), (800, 60)],
        ))
        return frame

    organized = {
        "roi_0": [frame_with_watermark("roi_0", 10)],
        "roi_1": [frame_with_watermark("roi_1", 10)],
    }
    result = optimizer._apply_watermark_filter(organized)

    for roi_id in ("roi_0", "roi_1"):
        assert len(result[roi_id][0].lines) == 1
        assert result[roi_id][0].lines[0].text == "正常台词内容"


def test_watermark_filter_removes_empty_frames(tmp_path):
    watermark_config = {
        "enabled": True,
        "entries": [{"text": "示例水印", "bbox": (0, 0, 10, 10)}],
    }
    optimizer = _make_optimizer(tmp_path, watermark_config=watermark_config)
    organized = {"roi_0": [_frame("roi_0", 10, "示例水印")]}
    result = optimizer._apply_watermark_filter(organized)
    assert "roi_0" not in result
