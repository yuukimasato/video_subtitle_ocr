# core/classification_features.py
"""
Feature extraction for text source classification.

Extracts spatial, temporal, visual, and semantic features from text regions
detected by OCR. These features are consumed by TextSourceClassifier to
determine whether text is OVERLAY (post-production) or SCENE (in-scene).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Tuple, Optional

import numpy as np


@dataclass
class TextRegionFeatures:
    """Feature vector for a single text region."""

    # ── Spatial position features ──
    frame_width: int = 1920
    frame_height: int = 1080
    bbox: Tuple[int, int, int, int] = (0, 0, 0, 0)  # (x1, y1, x2, y2)
    center_x: float = 0.0
    center_y: float = 0.0
    relative_y: float = 0.0  # y_center / height, 0=top, 1=bottom
    relative_height: float = 0.0  # bbox height / frame height
    is_edge_aligned: bool = False  # Within 5px of frame edge
    is_safe_zone: bool = False  # In traditional subtitle safe zone (bottom 20% or top 15%)

    # ── Temporal stability features ──
    first_seen_frame: int = 0
    last_seen_frame: int = 0
    duration_sec: float = 0.0
    appearance_count: int = 0
    is_stationary: bool = False
    text_stability: float = 0.0  # Mean text similarity over time (0-1)
    entrance_style: str = "cut"  # "cut" | "fade" | "scroll" | "static"
    exit_style: str = "cut"

    # ── Visual features ──
    contrast_with_bg: float = 0.0
    has_stroke_border: bool = False
    has_drop_shadow: bool = False
    has_bg_box: bool = False
    font_monospace_score: float = 0.0
    perspective_distortion: float = 0.0  # 0=no distortion, 1=severe
    edge_density: float = 0.0
    saturation_variance: float = 0.0

    # ── Text semantic features ──
    raw_text: str = ""  # Original OCR text (for LLM assist)
    text_length: int = 0
    is_single_line: bool = True
    line_count: int = 1
    has_punctuation_at_end: bool = False
    dialogue_pattern_score: float = 0.0
    proper_noun_ratio: float = 0.0
    contains_price: bool = False
    contains_address: bool = False
    contains_slogan: bool = False


# ═══════════════════════════════════════════════════════════════
# Visual feature extraction functions
# ═══════════════════════════════════════════════════════════════

def detect_stroke_border(text_region: np.ndarray) -> bool:
    """Detect whether a text region has a stroke/outline effect.

    Uses morphological dilation - erosion to find border ring.
    A uniform ring distribution indicates artificial stroke.
    """
    try:
        import cv2
        gray = cv2.cvtColor(text_region, cv2.COLOR_BGR2GRAY)
    except Exception:
        if len(text_region.shape) == 2:
            gray = text_region
        else:
            return False

    try:
        _, binary = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)

        kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5))
        dilated = cv2.dilate(binary, kernel, iterations=2)
        eroded = cv2.erode(binary, kernel, iterations=2)
        border_ring = cv2.subtract(dilated, eroded)

        ring_pixels = cv2.countNonZero(border_ring)
        text_pixels = cv2.countNonZero(binary)
        ring_ratio = ring_pixels / max(text_pixels, 1)

        # Stroke typically occupies 10-50% of text area
        return 0.10 < ring_ratio < 0.50
    except Exception:
        return False


def detect_perspective_distortion(bbox_points: np.ndarray) -> float:
    """Detect perspective distortion by measuring parallel-ness of opposite edges.

    Args:
        bbox_points: (4, 2) array of corner coordinates.

    Returns:
        0 (perfectly rectangular) to 1 (severe perspective distortion).
    """
    if bbox_points.shape != (4, 2):
        return 0.0

    try:
        v1 = bbox_points[1] - bbox_points[0]  # top edge
        v2 = bbox_points[2] - bbox_points[3]  # bottom edge (reversed)
        v3 = bbox_points[3] - bbox_points[0]  # left edge
        v4 = bbox_points[2] - bbox_points[1]  # right edge (reversed)

        def angle_between(v1, v2):
            cos = np.dot(v1, v2) / (np.linalg.norm(v1) * np.linalg.norm(v2) + 1e-8)
            return np.arccos(np.clip(abs(cos), 0, 1))

        a1 = angle_between(v1, -v2)  # top-bottom parallelism
        a2 = angle_between(v3, -v4)  # left-right parallelism

        # Normalize: 0 rad = perfect parallel, π/4+ rad = obvious perspective
        distortion = (a1 + a2) / (np.pi / 2)
        return float(min(1.0, distortion))
    except Exception:
        return 0.0


def detect_background_box(text_region: np.ndarray) -> bool:
    """Detect semi-transparent background box behind text (common in subtitles).

    Samples strips above/below the text region and checks for uniform color
    with a sharp boundary to the surrounding video.
    """
    try:
        import cv2
        gray = cv2.cvtColor(text_region, cv2.COLOR_BGR2GRAY)
    except Exception:
        if len(text_region.shape) == 2:
            gray = text_region
        else:
            return False

    h, w = gray.shape[:2]
    if h < 6:
        return False

    # Sample top and bottom strips
    strip_h = max(1, h // 6)
    top_strip = gray[0:strip_h, :]
    bottom_strip = gray[-strip_h:, :]

    def strip_uniformity(strip):
        if strip.size == 0:
            return 0.0
        mean_val = strip.mean()
        diff = np.abs(strip.astype(float) - mean_val).mean()
        return float(1.0 - min(1.0, diff / 50.0))

    top_uniform = strip_uniformity(top_strip)
    bottom_uniform = strip_uniformity(bottom_strip)

    # High uniformity in the border strips suggests a bg box
    return max(top_uniform, bottom_uniform) > 0.75


def compute_contrast_with_background(text_region: np.ndarray) -> float:
    """Compute contrast between text region and its border area.

    High contrast = likely overlay text (designed to be readable).
    Low contrast = likely scene text (integrated with background).
    """
    try:
        import cv2
        gray = cv2.cvtColor(text_region, cv2.COLOR_BGR2GRAY)
    except Exception:
        if len(text_region.shape) == 2:
            gray = text_region
        else:
            return 0.0

    h, w = gray.shape[:2]
    if h < 6 or w < 6:
        return 0.0

    # Interior (center 60%)
    cy, cx = h // 2, w // 2
    inner_h, inner_w = max(1, h // 3), max(1, w // 3)
    inner = gray[cy - inner_h:cy + inner_h, cx - inner_w:cx + inner_w]

    # Border (outer 20% on each side)
    border_h, border_w = max(1, h // 10), max(1, w // 10)
    top = gray[0:border_h, :]
    bottom = gray[-border_h:, :]
    left = gray[:, 0:border_w]
    right = gray[:, -border_w:]

    inner_mean = inner.mean()
    border_vals = np.concatenate([
        top.flatten(), bottom.flatten(),
        left.flatten(), right.flatten(),
    ])
    border_mean = border_vals.mean()

    contrast = abs(inner_mean - border_mean) / 255.0
    return float(min(1.0, contrast))


def extract_visual_features(
    text_region: np.ndarray,
    bbox_points: Optional[np.ndarray] = None,
) -> dict:
    """Extract all visual features from a text region image.

    Returns a dict of feature values suitable for TextRegionFeatures.
    """
    features = {
        "has_stroke_border": False,
        "has_bg_box": False,
        "has_drop_shadow": False,
        "contrast_with_bg": 0.0,
        "perspective_distortion": 0.0,
        "edge_density": 0.0,
    }

    if text_region is None or text_region.size == 0:
        return features

    try:
        features["has_stroke_border"] = detect_stroke_border(text_region)
    except Exception:
        pass

    try:
        features["has_bg_box"] = detect_background_box(text_region)
    except Exception:
        pass

    try:
        features["contrast_with_bg"] = compute_contrast_with_background(text_region)
    except Exception:
        pass

    if bbox_points is not None and bbox_points.shape == (4, 2):
        try:
            features["perspective_distortion"] = detect_perspective_distortion(bbox_points)
        except Exception:
            pass

    # Edge density
    try:
        import cv2
        if len(text_region.shape) == 3:
            gray = cv2.cvtColor(text_region, cv2.COLOR_BGR2GRAY)
        else:
            gray = text_region
        edges = cv2.Canny(gray, 50, 150)
        features["edge_density"] = float(np.count_nonzero(edges) / max(edges.size, 1))
    except Exception:
        pass

    return features


# ═══════════════════════════════════════════════════════════════
# Semantic feature extraction
# ═══════════════════════════════════════════════════════════════

def extract_semantic_features(text: str) -> dict:
    """Extract semantic features from recognized text.

    Returns a dict of feature values.
    """
    import re

    features = {
        "text_length": len(text),
        "is_single_line": "\n" not in text,
        "line_count": text.count("\n") + 1,
        "has_punctuation_at_end": False,
        "dialogue_pattern_score": 0.0,
        "proper_noun_ratio": 0.0,
        "contains_price": False,
        "contains_address": False,
        "contains_slogan": False,
    }

    text_stripped = text.strip()
    if not text_stripped:
        return features

    # End punctuation
    features["has_punctuation_at_end"] = text_stripped.endswith(
        ("。", "！", "？", ".", "!", "?", "…", "~")
    )

    # Dialogue pattern: "Name: ..." or "—— ..." or quotes
    dialogue_score = 0.0
    if re.match(r'^[A-Za-z一-鿿]{1,6}[:：]', text_stripped):
        dialogue_score += 0.6
    if text_stripped.startswith("——") or text_stripped.startswith("──"):
        dialogue_score += 0.4
    if text_stripped.startswith('"') or text_stripped.startswith('"'):
        dialogue_score += 0.2
    if text_stripped.startswith("「") or text_stripped.startswith("『"):
        dialogue_score += 0.3
    features["dialogue_pattern_score"] = min(1.0, dialogue_score)

    # Proper noun ratio (uppercase English, numbers, CJK proper nouns)
    total_chars = len(text_stripped)
    if total_chars > 0:
        proper_count = 0
        # Uppercase letters
        proper_count += sum(1 for c in text_stripped if c.isupper())
        # Digits as parts of names/addresses
        digits = sum(1 for c in text_stripped if c.isdigit())
        proper_count += digits * 0.5
        features["proper_noun_ratio"] = proper_count / total_chars

    # Price patterns: ¥123, $45, 100元, etc.
    features["contains_price"] = bool(
        re.search(r'[¥￥$€£]\s*\d+', text_stripped)
        or re.search(r'\d+\s*[元块]', text_stripped)
    )

    # Address patterns
    features["contains_address"] = bool(
        re.search(r'(省|市|区|县|镇|乡|村|路|街|巷|号|弄|楼|层|室|栋)', text_stripped)
        or re.search(r'(Road|Street|Avenue|District|Building|Room)', text_stripped, re.I)
    )

    # Slogan patterns
    features["contains_slogan"] = bool(
        re.search(r'(欢迎|禁止|请勿|注意|安全|消防|紧急|出口|入口)', text_stripped)
        or len(text_stripped) >= 8 and text_stripped.endswith(("！", "!"))
    )

    return features
