# tests/test_roi_filters.py
"""ROI 画像过滤回归测试(core/subtitle_generator/roi_filters.py)。

Regression 背景:_filter_groups_by_roi_profile 中「主导是场景字时,不去
过滤 BOTTOM/TOP」的注释与实现相反——旧代码 ``continue`` 会把组中心
落在画面顶/底位置带的组静默丢弃,恰好误杀「真正场景字偶尔靠近边缘」
(generator.py 常量注释明确:主导为 SCENE 时保留场景文字逻辑)。
"""
from __future__ import annotations

import os
import sys

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from core.subtitle_generator.models import SubtitleGroup, TextLine
from core.subtitle_generator.roi_filters import _RoiFiltersMixin


class _Host(_RoiFiltersMixin):
    """仅提供画像过滤所需常量的最小宿主(无 QWidget,headless 安全)。"""

    height = 1000.0
    VIDEO_BOTTOM_AREA = 0.75
    VIDEO_TOP_AREA = 0.15
    ROI_PROFILE_MIN_GROUPS = 3
    ROI_PROFILE_DOMINANT_RATIO = 0.55
    ROI_HEIGHT_FILTER_MIN_RATIO = 0.65
    ROI_HEIGHT_FILTER_MAX_RATIO = 1.65


def _group(y1: float, y2: float, text: str = "场景字") -> SubtitleGroup:
    line = TextLine(
        text=text, score=0.9, box=(100, y1, 400, y2),
        polygon=[(100, y1), (400, y1), (400, y2), (100, y2)],
    )
    return SubtitleGroup(start_frame=0, end_frame=1, lines=[line])


def test_scene_dominant_roi_keeps_edge_band_groups():
    """SCENE 主导的 ROI:落在 BOTTOM/TOP 位置带的组(真实场景字靠近画面
    边缘)不得被位置过滤丢弃,仅保留高度比过滤兜底。"""
    host = _Host()
    groups = [
        _group(200, 250),   # SCENE(主导)
        _group(300, 350),   # SCENE
        _group(400, 450),   # SCENE
        _group(850, 900),   # 位置分类 BOTTOM(画面底部带)
        _group(50, 100),    # 位置分类 TOP(画面顶部带)
    ]
    kept = host._filter_groups_by_roi_profile(groups)
    assert len(kept) == 5  # 全部保留(高度一致,仅位置带不同)


def test_bottom_dominant_roi_drops_scene_groups():
    """BOTTOM 主导的 ROI:仍应排除位置分类为 SCENE 的杂项(原逻辑不变)。"""
    host = _Host()
    groups = [
        _group(850, 900),   # BOTTOM(主导)
        _group(860, 910),   # BOTTOM
        _group(870, 920),   # BOTTOM
        _group(300, 350),   # SCENE → 排除
    ]
    kept = host._filter_groups_by_roi_profile(groups)
    assert len(kept) == 3


def test_height_ratio_filter_still_applies_for_scene_roi():
    """SCENE 主导 ROI 的高度比过滤不受影响:过高/过矮的杂项仍被剔除。"""
    host = _Host()
    groups = [
        _group(200, 250),   # 主导高度 50px
        _group(300, 350),
        _group(400, 450),
        _group(820, 940),   # 高 120px,比值 2.4 > 1.65 → 剔除
    ]
    kept = host._filter_groups_by_roi_profile(groups)
    assert len(kept) == 3
