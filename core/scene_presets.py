# core/scene_presets.py
"""
Scene presets for automatic video type detection and source filtering.

Defines 12 preset configurations that control which text sources (OVERLAY/SCENE/UNKNOWN)
are kept in the output, along with classifier parameter overrides for each scenario.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional


@dataclass
class ScenePreset:
    """A preset configuration for a specific video type/scenario."""

    preset_id: str  # Unique ID: "film_tv", "variety_show", etc.
    name: str  # Display name (Chinese)
    description: str  # Usage description (shown in UI tooltip)
    category: str  # "overlay_focused" | "both" | "scene_focused" | "custom"

    # Which text sources to keep
    keep_overlay: bool = True
    keep_scene: bool = True
    keep_unknown: bool = True

    # Classifier weight overrides (None = use defaults)
    classifier_weight_override: Optional[Dict[str, float]] = None

    # Spatial zone thresholds
    safe_zone_bottom_ratio: float = 0.75
    safe_zone_top_ratio: float = 0.15

    # Classification bias: positive = bias toward OVERLAY, negative = bias toward SCENE
    classification_bias: float = 0.0

    # Minimum confidence threshold (results below this are rounded to UNKNOWN)
    min_classification_confidence: float = 0.35

    # Whether to enable LLM-assisted classification for edge cases
    llm_assist_enabled: bool = False


# ═══════════════════════════════════════════════════════
# Complete preset list (12 presets)
# ═══════════════════════════════════════════════════════

PRESETS: Dict[str, ScenePreset] = {

    # ── OVERLAY-focused (core content is overlaid text) ──────────

    "film_tv": ScenePreset(
        preset_id="film_tv",
        name="影视/剧集",
        description="适合电影、电视剧、网剧。仅保留对白字幕和片头片尾标题，"
                    "过滤店铺招牌、路牌、背景海报等一切实拍场景文字。",
        category="overlay_focused",
        keep_overlay=True,
        keep_scene=False,
        keep_unknown=False,
        classification_bias=+0.15,
        min_classification_confidence=0.25,
    ),

    "variety_show": ScenePreset(
        preset_id="variety_show",
        name="综艺/真人秀",
        description="适合综艺节目、真人秀。保留花字、特效字幕、后期吐槽，"
                    "过滤现场广告牌、参赛者名牌等实拍文字。",
        category="overlay_focused",
        keep_overlay=True,
        keep_scene=False,
        keep_unknown=False,
        classifier_weight_override={
            "visual": 0.35,
            "spatial": 0.20,
            "temporal": 0.20,
            "semantic": 0.25,
        },
        classification_bias=+0.20,
        min_classification_confidence=0.25,
    ),

    "anime": ScenePreset(
        preset_id="anime",
        name="动漫/番剧",
        description="适合动画、番剧。保留对白字幕，保留画面中出现的日文/中文"
                    "告示牌、手机屏幕文字（通常与剧情相关）。",
        category="overlay_focused",
        keep_overlay=True,
        keep_scene=True,
        keep_unknown=False,
        classification_bias=0.0,
    ),

    "live_stream": ScenePreset(
        preset_id="live_stream",
        name="直播/实况",
        description="适合直播录制回放。保留弹幕、礼物提示、PK进度条、主播字幕，"
                    "过滤店铺招牌、路牌等实拍场景文字。",
        category="overlay_focused",
        keep_overlay=True,
        keep_scene=False,
        keep_unknown=False,
        classification_bias=+0.10,
        min_classification_confidence=0.25,
    ),

    "gameplay": ScenePreset(
        preset_id="gameplay",
        name="游戏录屏",
        description="适合游戏实况/攻略/电竞录像。保留游戏UI（伤害数字、技能说明、"
                    "聊天框、计分板），过滤过场动画中的虚拟'场景文字'。",
        category="overlay_focused",
        keep_overlay=True,
        keep_scene=False,
        keep_unknown=False,
        classifier_weight_override={
            "spatial": 0.30,
            "temporal": 0.30,
            "visual": 0.20,
            "semantic": 0.20,
        },
        classification_bias=+0.10,
    ),

    # ── BOTH important (scene text also has informational value) ──

    "education": ScenePreset(
        preset_id="education",
        name="教学/培训",
        description="适合在线课程、培训视频、教程讲解。同时保留课件字幕和"
                    "板书/PPT/教材翻拍中的全部文字内容。",
        category="both",
        keep_overlay=True,
        keep_scene=True,
        keep_unknown=True,
        classification_bias=-0.05,
        min_classification_confidence=0.30,
        llm_assist_enabled=True,
    ),

    "news": ScenePreset(
        preset_id="news",
        name="新闻/资讯",
        description="适合新闻节目、资讯报道。保留标题字幕、滚动字幕条，"
                    "同时保留有新闻价值的现场标牌、数据图表、文件翻拍。",
        category="both",
        keep_overlay=True,
        keep_scene=True,
        keep_unknown=True,
        classifier_weight_override={
            "semantic": 0.40,
            "spatial": 0.20,
            "temporal": 0.15,
            "visual": 0.25,
        },
        classification_bias=0.0,
        llm_assist_enabled=True,
    ),

    "documentary": ScenePreset(
        preset_id="documentary",
        name="纪录片",
        description="适合纪录片、纪实节目。保留解说字幕，同时保留实地采访中的"
                    "标牌、文献翻拍、地图标注、档案文件等实拍文字。",
        category="both",
        keep_overlay=True,
        keep_scene=True,
        keep_unknown=True,
        classification_bias=-0.05,
    ),

    "conference": ScenePreset(
        preset_id="conference",
        name="会议/演讲录制",
        description="适合会议录制、演讲回放、线上研讨会。保留幻灯片/投屏内容、"
                    "演讲者名牌、录制叠加的日期/会议名称等全部文字。",
        category="both",
        keep_overlay=True,
        keep_scene=True,
        keep_unknown=True,
        classifier_weight_override={
            "temporal": 0.35,
            "spatial": 0.25,
            "visual": 0.20,
            "semantic": 0.20,
        },
        classification_bias=-0.08,
    ),

    "short_video": ScenePreset(
        preset_id="short_video",
        name="短视频/Vlog",
        description="适合抖音、快手、Vlog 等自媒体短视频。保留字幕贴纸和花字，"
                    "同时保留有内容价值的路牌、店招（Vlog 中这些是内容本身）。"
                    "但不保留无法判定的模糊区域。",
        category="both",
        keep_overlay=True,
        keep_scene=True,
        keep_unknown=False,
        classification_bias=0.0,
    ),

    # ── SCENE-focused (scene text is the real target) ──────────

    "surveillance": ScenePreset(
        preset_id="surveillance",
        name="监控/安防",
        description="适合监控录像、行车记录仪、执法记录仪。仅保留时间戳、"
                    "车牌号码、门牌号、仪表读数等实拍场景文字。"
                    "过滤所有后期叠加的 UI 和水印。",
        category="scene_focused",
        keep_overlay=False,
        keep_scene=True,
        keep_unknown=False,
        classifier_weight_override={
            "temporal": 0.40,
            "spatial": 0.25,
            "visual": 0.20,
            "semantic": 0.15,
        },
        classification_bias=-0.25,
        min_classification_confidence=0.30,
    ),

    # ── Custom ─────────────────────────────

    "custom": ScenePreset(
        preset_id="custom",
        name="自定义",
        description="完全手动控制。勾选你需要保留的文字类别，并可微调分类器参数。",
        category="custom",
        keep_overlay=True,
        keep_scene=True,
        keep_unknown=True,
    ),
}

# All preset IDs in display order
ALL_PRESET_IDS = [
    "film_tv", "variety_show", "anime", "live_stream", "gameplay",
    "education", "news", "documentary", "conference", "short_video",
    "surveillance", "custom",
]


def get_preset_by_id(preset_id: str) -> Optional[ScenePreset]:
    """Look up a preset by its ID."""
    return PRESETS.get(preset_id)


def get_default_preset() -> ScenePreset:
    """Return the default preset (film_tv — most common use case)."""
    return PRESETS["film_tv"]
