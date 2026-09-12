# tests/test_source_classifier_visual_gate.py
"""Visual layer must not vote on unmeasured features.

The generation pipeline never extracts visual features (stroke/shadow/
edge density), so TextRegionFeatures carries sentinel defaults. The
classifier used to feed those defaults into the visual layer, where
edge_density=0.0 scored as "blurry → scene" (-0.2) and systematically
pushed bottom-band dialogue below the OVERLAY threshold (fused 0.650 vs
required >0.65) — with `--preset anime` (keep_unknown=False) that
dropped every dialogue line except ones ending in punctuation.

Contract: unmeasured visual layer is excluded from the fusion with its
weight renormalized across the measured layers; a *measured* visual
layer still contributes (penalties included).
"""

from __future__ import annotations

from core.classification_features import TextRegionFeatures
from core.text_source_classifier import TextSource, TextSourceClassifier


def _bottom_dialogue(**overrides) -> TextRegionFeatures:
    """Typical hard-sub dialogue: bottom safe zone, 1-8 s, stationary."""
    fields = dict(
        frame_width=1920,
        frame_height=1080,
        bbox=(451, 950, 1500, 1010),
        center_x=975.0,
        center_y=980.0,
        relative_y=980.0 / 1080.0,
        relative_height=60.0 / 1080.0,
        is_safe_zone=True,
        duration_sec=4.0,
        is_stationary=True,
        text_stability=0.02,
        raw_text="空野你醒啦",
        text_length=6,
        has_punctuation_at_end=False,
    )
    fields.update(overrides)
    return TextRegionFeatures(**fields)


def test_unmeasured_visual_layer_keeps_dialogue_overlay():
    clf = TextSourceClassifier()
    result = clf.classify(_bottom_dialogue())
    assert result.source == TextSource.OVERLAY, (
        f"expected OVERLAY, got {result.source.value} "
        f"(scores={result.feature_scores})"
    )


def test_unmeasured_visual_layer_renormalizes_weights():
    """Excluding the visual layer must not shrink the fused score scale.

    spatial +0.6, temporal +0.8, semantic 0 → renormalized fused score
    (0.6+0.8)/3 = 0.467 → confidence 0.733. With the old behavior the
    unmeasured visual −0.2 dragged it to 0.65 → UNKNOWN.
    """
    clf = TextSourceClassifier()
    result = clf.classify(_bottom_dialogue())
    assert abs(result.feature_scores["spatial"] - 0.6) < 1e-9
    assert result.confidence > 0.65
    expected_normalized = ((0.6 + 0.8) / 3 + 1) / 2
    assert abs(result.confidence - expected_normalized) < 1e-6


def test_measured_visual_layer_still_penalizes_blur():
    """When visual features were really measured, low edge density scores
    and drags the same dialogue back into the UNKNOWN band (fused 0.3 →
    confidence 0.65, not OVERLAY) — the penalty must keep working for
    pipelines that do measure the visual layer."""
    clf = TextSourceClassifier()
    result = clf.classify(_bottom_dialogue(visual_measured=True, edge_density=0.0))
    assert abs(result.feature_scores["visual"] - (-0.2)) < 1e-9
    assert result.source != TextSource.OVERLAY


def test_scene_bias_survives_visual_exclusion():
    """Central-area static long-duration text stays SCENE-tended without
    the (absent) visual layer dragging the decision toward UNKNOWN."""
    clf = TextSourceClassifier(min_confidence=0.25)
    feats = _bottom_dialogue(
        relative_y=0.5,
        center_y=540.0,
        is_safe_zone=False,
        duration_sec=45.0,
        raw_text="営業中",
        text_length=3,
    )
    result = clf.classify(feats)
    assert result.source in (TextSource.SCENE, TextSource.UNKNOWN)
    assert result.source != TextSource.OVERLAY
