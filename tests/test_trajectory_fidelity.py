# tests/test_trajectory_fidelity.py
"""A3:轨迹接管的文本保真门控(core.trajectory_fidelity)单元测试。"""

from __future__ import annotations

import os
import sys

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from core.trajectory_fidelity import (  # noqa: E402
    FidelityResult,
    TextSpan,
    check_text_fidelity,
)


def test_full_coverage_is_not_text_fidelity():
    static = [TextSpan(0, 100, "甲"), TextSpan(100, 200, "乙")]
    motion = [TextSpan(0, 200, "甲")]
    result = check_text_fidelity(static, motion)
    assert not result.ok
    assert "100" in result.reason  # 首个 mismatch 片段位置
    assert check_text_fidelity(static, static).ok
    unverified = check_text_fidelity([], motion)
    assert not unverified.ok
    assert unverified.reason.startswith("unverified")
    # 行序也是证据:同字不同序拒绝(不用字符多重集)。
    assert not check_text_fidelity(
        [TextSpan(0, 100, "甲乙")], [TextSpan(0, 100, "乙甲")]).ok


def test_identical_multiline_paths_accepted():
    static = [TextSpan(0, 200, "行一", 0), TextSpan(0, 200, "行二", 1)]
    motion = [TextSpan(0, 200, "行一", 0), TextSpan(0, 200, "行二", 1)]
    assert check_text_fidelity(static, motion).ok


def test_missing_line_in_motion_rejected():
    static = [TextSpan(0, 200, "行一", 0), TextSpan(0, 200, "行二", 1)]
    motion = [TextSpan(0, 200, "行一", 0)]
    result = check_text_fidelity(static, motion)
    assert not result.ok
    assert "rejected" in result.reason


def test_motion_extra_text_rejected():
    static = [TextSpan(0, 100, "甲", 0)]
    motion = [TextSpan(0, 200, "甲", 0), TextSpan(0, 200, "幽灵行", 1)]
    assert not check_text_fidelity(static, motion).ok


def test_normalization_tolerates_punct_and_width():
    static = [TextSpan(0, 100, "ｇｏｍｅｎ…", 0)]
    motion = [TextSpan(0, 100, "gomen!", 0)]
    assert check_text_fidelity(static, motion).ok


def test_gap_on_one_side_rejected():
    # 静态两段实例之间有离场间隔,轨迹声称全程连续 → 拒绝。
    static = [TextSpan(0, 100, "甲", 0), TextSpan(200, 300, "甲", 0)]
    motion = [TextSpan(0, 300, "甲", 0)]
    assert not check_text_fidelity(static, motion).ok


def test_matching_gapped_instances_accepted():
    static = [TextSpan(0, 100, "甲", 0), TextSpan(200, 300, "甲", 0)]
    motion = [TextSpan(0, 100, "甲", 0), TextSpan(200, 300, "甲", 0)]
    assert check_text_fidelity(static, motion).ok


def test_non_positive_span_rejected():
    assert not check_text_fidelity(
        [TextSpan(100, 100, "甲")], [TextSpan(0, 100, "甲")]).ok
    assert not check_text_fidelity(
        [TextSpan(0, 100, "甲")], [TextSpan(200, 100, "甲")]).ok


def test_empty_motion_rejected():
    result = check_text_fidelity([TextSpan(0, 100, "甲")], [])
    assert not result.ok
    assert isinstance(result, FidelityResult)
