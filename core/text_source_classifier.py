# core/text_source_classifier.py
"""
Text source classifier — distinguishes OVERLAY from SCENE text.

Uses a 5-layer cascade classifier:
  1. Spatial position features
  2. Temporal stability features
  3. Visual features (stroke, shadow, perspective)
  4. Text semantic features
  5. Optional LLM assist for borderline cases

The classifier accepts scene preset parameters to dynamically adjust
classification bias and weights per video type.
"""

from __future__ import annotations

import logging
from enum import Enum
from typing import List, Dict, Optional, Tuple

from core.classification_features import TextRegionFeatures

logger = logging.getLogger(__name__)


class TextSource(Enum):
    OVERLAY = "overlay"  # Post-production overlaid text
    SCENE = "scene"  # In-scene real-world text
    UNKNOWN = "unknown"  # Cannot determine


class ClassificationResult:
    """Result of text source classification for a single text region."""

    __slots__ = ("source", "confidence", "rationale", "feature_scores")

    def __init__(
        self,
        source: TextSource,
        confidence: float,
        rationale: str = "",
        feature_scores: Optional[Dict[str, float]] = None,
    ):
        self.source = source
        self.confidence = confidence
        self.rationale = rationale
        self.feature_scores = feature_scores or {}

    def to_dict(self) -> dict:
        return {
            "source": self.source.value,
            "confidence": self.confidence,
            "rationale": self.rationale,
        }


class TextSourceClassifier:
    """Text source classifier with configurable weights and bias.

    Classification scores range from -1.0 (strong SCENE) to +1.0 (strong OVERLAY).
    These are normalized to [0, 1] confidence for the final decision.

    Weights and bias can be overridden per scene preset.
    """

    HIGH_CONFIDENCE_THRESHOLD = 0.85

    DEFAULT_WEIGHTS = {
        "spatial": 0.25,
        "temporal": 0.25,
        "visual": 0.25,
        "semantic": 0.25,
    }

    def __init__(
        self,
        weights: Optional[Dict[str, float]] = None,
        classification_bias: float = 0.0,
        min_confidence: float = 0.35,
        llm_config: Optional[dict] = None,
    ):
        self.weights = weights or dict(self.DEFAULT_WEIGHTS)
        self.classification_bias = classification_bias
        self.min_confidence = min_confidence
        self.llm_config = llm_config

    def classify(
        self,
        features: TextRegionFeatures,
        context: Optional[List[TextRegionFeatures]] = None,
    ) -> ClassificationResult:
        """Classify a single text region.

        Args:
            features: Feature vector for the text region.
            context: Other text regions in the same video (for context-aware decisions).

        Returns:
            ClassificationResult with source, confidence, and rationale.
        """
        scores: Dict[str, float] = {}
        reasons: List[str] = []

        # Layer 1: Spatial position
        spatial_score = self._spatial_classify(features)
        scores["spatial"] = spatial_score
        if abs(spatial_score) > self.HIGH_CONFIDENCE_THRESHOLD:
            reasons.append(self._spatial_reason(features, spatial_score))

        # Layer 2: Temporal stability
        temporal_score = self._temporal_classify(features)
        scores["temporal"] = temporal_score
        if abs(temporal_score) > self.HIGH_CONFIDENCE_THRESHOLD:
            reasons.append(self._temporal_reason(features, temporal_score))

        # Layer 3: Visual features
        visual_score = self._visual_classify(features)
        scores["visual"] = visual_score
        if abs(visual_score) > self.HIGH_CONFIDENCE_THRESHOLD:
            reasons.append(self._visual_reason(features, visual_score))

        # Layer 4: Text semantics
        semantic_score = self._semantic_classify(features)
        scores["semantic"] = semantic_score
        if abs(semantic_score) > self.HIGH_CONFIDENCE_THRESHOLD:
            reasons.append(self._semantic_reason(features, semantic_score))

        # Weighted fusion
        weighted = sum(scores[k] * self.weights.get(k, 0.25) for k in scores)
        # Apply classification bias
        weighted += self.classification_bias
        # Map [-1, 1] to [0, 1] (1 = OVERLAY)
        normalized = (weighted + 1) / 2
        normalized = max(0.0, min(1.0, normalized))

        # Decision
        if normalized > 0.65:
            source = TextSource.OVERLAY
            confidence = normalized
        elif normalized < 0.35:
            source = TextSource.SCENE
            confidence = 1.0 - normalized
        else:
            source = TextSource.UNKNOWN
            confidence = 0.5

        # Layer 5: LLM assist (optional, triggered when rule confidence is low)
        if confidence < 0.7 and self.llm_config:
            llm_result = self._llm_assist(features, context)
            if llm_result is not None:
                llm_source, llm_conf = llm_result
                source = llm_source
                confidence = llm_conf
                reasons.append("LLM 辅助判定")

        return ClassificationResult(
            source=source,
            confidence=confidence,
            rationale="; ".join(reasons) if reasons else "综合特征判定",
            feature_scores=scores,
        )

    def _llm_assist(
        self, features: TextRegionFeatures,
        context: Optional[List[TextRegionFeatures]] = None,
    ) -> Optional[tuple]:
        """LLM-assisted classification for borderline cases.

        Called when the rule engine confidence is below 0.7.
        Sends text + context to the LLM via the existing DeepSeek infrastructure.

        Args:
            features: Feature vector for the text region.
            context: Other text regions nearby (for semantic context).

        Returns:
            (TextSource, confidence) tuple, or None on failure/timeout.
        """
        if not self.llm_config:
            return None

        config = self.llm_config

        # Build context dict for the LLM prompt
        position_desc = "unknown"
        if features.is_safe_zone:
            if features.relative_y > 0.80:
                position_desc = "bottom_safe_zone"
            elif features.relative_y < 0.15:
                position_desc = "top_safe_zone"
        elif 0.25 < features.relative_y < 0.70:
            position_desc = "center_area"
        else:
            position_desc = f"y={features.relative_y:.0%}"

        nearby_texts = []
        if context:
            for f in context[:5]:  # Limit to 5 nearby texts
                nearby_texts.append(
                    getattr(f, 'text_length', 0) > 0
                    and f"text_len={f.text_length}"
                    or ""
                )
            nearby_texts = [t for t in nearby_texts if t]

        llm_context = {
            "position": position_desc,
            "duration_sec": features.duration_sec,
            "frame_width": features.frame_width,
            "frame_height": features.frame_height,
            "nearby_texts": nearby_texts,
        }

        try:
            from core.subtitle_llm_polish import SubtitlePolisherConfig, classify_text_source

            # Build a temporary polisher config from llm_config
            polisher_cfg = SubtitlePolisherConfig(
                api_key=config.get("api_key", ""),
                api_base_url=config.get("api_base", "https://api.deepseek.com"),
                model=config.get("model", "deepseek-v4-flash"),
            )

            # The text content for classification — derive from available feature fields.
            # This is a heuristic since TextRegionFeatures doesn't store the raw text directly.
            # Callers should provide raw text via a custom extension if needed.
            raw_text = getattr(features, "raw_text", None)
            if not raw_text:
                return None

            result = classify_text_source(
                polisher_cfg,
                raw_text,
                context=llm_context,
            )

            if result is None:
                return None

            source_str = result.get("source", "unknown")
            confidence_val = float(result.get("confidence", 0.5))

            if source_str == "overlay":
                return (TextSource.OVERLAY, confidence_val)
            elif source_str == "scene":
                return (TextSource.SCENE, confidence_val)
            else:
                return (TextSource.UNKNOWN, confidence_val)

        except ImportError:
            logger.debug("LLM assist skipped: subtitle_llm_polish not available")
            return None
        except Exception as e:
            logger.warning(f"LLM assist failed: {e}")
            return None

    # ── Layer 1: Spatial ─────────────────────────────────────

    def _spatial_classify(self, f: TextRegionFeatures) -> float:
        """Classify based on spatial position.

        Returns -1.0 (strong SCENE) to +1.0 (strong OVERLAY).
        """
        score = 0.0

        # Bottom subtitle safe zone → strong OVERLAY
        if f.is_safe_zone and f.relative_y > 0.80:
            score += 0.6
        # Top subtitle area
        elif f.is_safe_zone and f.relative_y < 0.15:
            score += 0.5

        # Edge-aligned → likely overlay (UI elements, watermarks)
        if f.is_edge_aligned:
            score += 0.2

        # Central area → SCENE tendency
        if 0.25 < f.relative_y < 0.70:
            score -= 0.3

        # Very small text (distant signs) → SCENE
        if f.relative_height < 0.02:
            score -= 0.2

        return max(-1.0, min(1.0, score))

    def _spatial_reason(self, f, score) -> str:
        if score > 0:
            loc = "底部" if f.relative_y > 0.8 else "顶部"
            return f"位于画面{loc}固定位置，符合后期叠加特征"
        return "位于画面中央，可能为实拍场景文字"

    # ── Layer 2: Temporal ────────────────────────────────────

    def _temporal_classify(self, f: TextRegionFeatures) -> float:
        """Classify based on temporal stability.

        Subtitle text: regular appearance/disappearance, 1-8s duration.
        Scene text: long static presence, or random flickering.
        """
        score = 0.0

        # Typical subtitle duration: 1-8 seconds
        if 1.0 < f.duration_sec < 8.0:
            score += 0.3

        # Very long duration → likely scene text
        if f.duration_sec > 30.0:
            score -= 0.4

        # Stationary position → common for overlay
        if f.is_stationary:
            score += 0.2

        # Text content changes over time at same position → typical subtitle
        if f.text_stability < 0.3:
            score += 0.3

        # Fade effects → post-production
        if f.entrance_style in ("fade",) or f.exit_style in ("fade",):
            score += 0.2

        return max(-1.0, min(1.0, score))

    def _temporal_reason(self, f, score) -> str:
        if score > 0:
            return f"持续时间 {f.duration_sec:.1f}s 符合字幕节奏"
        return f"长时间静止 ({f.duration_sec:.0f}s)，可能为场景文字"

    # ── Layer 3: Visual ──────────────────────────────────────

    def _visual_classify(self, f: TextRegionFeatures) -> float:
        """Classify based on visual features.

        Overlay text: stroke, shadow, bg box, high contrast, no perspective.
        Scene text: perspective distortion, low contrast, blends with background.
        """
        score = 0.0

        if f.has_stroke_border:
            score += 0.35
        if f.has_drop_shadow:
            score += 0.25
        if f.has_bg_box:
            score += 0.3
        if f.contrast_with_bg > 0.7:
            score += 0.2

        # Perspective distortion → scene
        if f.perspective_distortion > 0.3:
            score -= 0.4

        # Low edge density → blurry, likely scene
        if f.edge_density < 0.3:
            score -= 0.2

        # High saturation variance → scene (diverse colors)
        if f.saturation_variance > 0.4:
            score -= 0.15

        return max(-1.0, min(1.0, score))

    def _visual_reason(self, f, score) -> str:
        parts = []
        if f.has_stroke_border:
            parts.append("描边")
        if f.has_drop_shadow:
            parts.append("投影")
        if f.has_bg_box:
            parts.append("背景色块")
        if parts:
            return f"检测到后期特效特征：{', '.join(parts)}"
        if f.perspective_distortion > 0.3:
            return "存在透视变形，符合实拍特征"
        return "视觉特征综合判定"

    # ── Layer 4: Semantic ────────────────────────────────────

    def _semantic_classify(self, f: TextRegionFeatures) -> float:
        """Classify based on text content semantics.

        Dialogue patterns → OVERLAY.
        Prices/addresses/slogans → SCENE.
        """
        score = 0.0

        # Dialogue pattern: "Name: line" or "—— quote"
        if f.dialogue_pattern_score > 0.5:
            score += 0.4

        # End punctuation (common in subtitles, less common in signs)
        if f.has_punctuation_at_end and f.text_length < 50:
            score += 0.15

        # Price → scene (menus, price tags)
        if f.contains_price:
            score -= 0.3

        # Address → scene (road signs, shop signs)
        if f.contains_address:
            score -= 0.3

        # Slogan → scene (banners, posters)
        if f.contains_slogan:
            score -= 0.2

        # High proper noun ratio → scene
        if f.proper_noun_ratio > 0.3:
            score -= 0.2

        return max(-1.0, min(1.0, score))

    def _semantic_reason(self, f, score) -> str:
        if f.dialogue_pattern_score > 0.5:
            return "文本格式符合对白/台词模式"
        if f.contains_price or f.contains_address:
            return "文本内容为价格/地址等实拍信息"
        return "文本语义综合判定"


# ═══════════════════════════════════════════════════════════════
# Factory function
# ═══════════════════════════════════════════════════════════════

def create_classifier_from_config(source_filter_config: Optional[Dict] = None) -> TextSourceClassifier:
    """Create a TextSourceClassifier from a source filter config dict.

    Args:
        source_filter_config: Config dict from ControlPanelWidget.get_source_filter_config().

    Returns:
        Configured TextSourceClassifier instance.
    """
    weights = None
    bias = 0.0
    min_conf = 0.35
    llm_config = None

    if source_filter_config:
        weights = source_filter_config.get("classifier_weights")
        bias = float(source_filter_config.get("classification_bias", 0.0))
        min_conf = float(source_filter_config.get("min_classification_confidence", 0.35))
        if source_filter_config.get("llm_assist_enabled"):
            llm_config = source_filter_config.get("llm_config") or {}

    return TextSourceClassifier(
        weights=weights,
        classification_bias=bias,
        min_confidence=min_conf,
        llm_config=llm_config,
    )
