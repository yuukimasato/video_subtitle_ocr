# core/video_type_detector.py
"""
Automatic video type detection engine.

Analyzes video metadata + sampled frames to determine the most likely
video type (film, variety show, gameplay, etc.) and recommends the
optimal scene preset for text source filtering.

Four-stage detection pipeline:
  A. Metadata analysis (zero decode, milliseconds)
  B. Intelligent frame sampling (decode 15-30 frames, 1-3 seconds)
  C. Multi-dimensional feature extraction
  D. Multi-layer classification (rules → prototype matching → optional LLM)
"""

from __future__ import annotations

import os
import re
import logging
from dataclasses import dataclass, field
from typing import List, Dict, Optional, Tuple

import numpy as np

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════
# Data structures
# ═══════════════════════════════════════════════════════════════

@dataclass
class VideoMetadata:
    """Video metadata extracted without decoding."""

    file_path: str
    file_name: str
    duration_sec: float
    width: int
    height: int
    fps: float
    total_frames: int
    aspect_ratio: float
    has_audio: bool
    audio_stream_count: int
    codec_name: str
    file_size_mb: float
    file_name_keywords: List[str] = field(default_factory=list)


@dataclass
class VideoTypeFeatures:
    """Complete feature vector for video type classification."""

    # Motion features (from frame differential analysis)
    avg_motion_intensity: float = 0.0
    motion_variance: float = 0.0
    scene_cut_count: int = 0
    scene_cut_frequency: float = 0.0
    static_ratio: float = 0.0

    # Visual composition features
    dominant_color_saturation: float = 0.0
    color_histogram_spread: float = 0.0
    brightness_variance: float = 0.0
    letterbox_ratio: float = 0.0
    pillar_box_ratio: float = 0.0

    # Text composition features (from lightweight text detection)
    text_density: float = 0.0
    text_location_entropy: float = 0.0
    overlay_vs_scene_ratio: float = 0.5
    avg_text_duration_sec: float = 0.0
    multi_font_score: float = 0.0

    # Audio features (optional)
    has_background_music: Optional[bool] = None
    speech_density: Optional[float] = None
    language_hint: Optional[str] = None


@dataclass
class DetectionResult:
    """Result of video type auto-detection."""

    detected_type: str  # Detected type ID (e.g., "film_tv")
    confidence: float  # Confidence 0-1
    recommended_preset: object  # ScenePreset object
    rationale: str  # Human-readable reasoning
    alternative_presets: List[object] = field(default_factory=list)
    all_scores: Dict[str, float] = field(default_factory=dict)

    @property
    def is_high_confidence(self) -> bool:
        return self.confidence >= 0.80

    @property
    def is_medium_confidence(self) -> bool:
        return 0.50 <= self.confidence < 0.80

    @property
    def is_low_confidence(self) -> bool:
        return self.confidence < 0.50


# ═══════════════════════════════════════════════════════════════
# Helper: filename keyword analysis
# ═══════════════════════════════════════════════════════════════

KEYWORD_MAP: Dict[str, List[str]] = {
    "film_tv": [
        "EP", "S01", "S02", "E01", "E02", "第", "集", "季",
        "1080p", "2160p", "BluRay", "WEB-DL", "NF", "Netflix",
        "HDR", "DDP", "中英双字", "BDRip", "WEBRip",
    ],
    "anime": [
        "ANIME", "番", "字幕组", "BD", "TV", "OVA", "NCOP", "NCED",
        "アニメ", "メニュー",
    ],
    "variety_show": [
        "综艺", "真人秀", "running", "奔跑", "脱口秀",
    ],
    "education": [
        "教程", "课程", "Lecture", "课", "培训", "Tutorial",
        "入门", "实战", "系列", "Coursera",
    ],
    "news": [
        "新闻", "News", "报道", "联播", "快讯",
    ],
    "documentary": [
        "纪录片", "Documentary", "探索", "纪实",
        "National Geographic", "BBC", "CCTV",
    ],
    "live_stream": [
        "直播", "Live", "录播", "回放", "实况", "Stream",
    ],
    "gameplay": [
        "Game", "游戏", "实况", "攻略", "Playthrough", "boss", "通关",
    ],
    "surveillance": [
        "cam", "CAM", "监控", "DVR", "ch0", "ch1", "camera", "IPC",
    ],
    "short_video": [
        "抖音", "TikTok", "快手", "Reel", "Short", "vlog", "VLOG",
    ],
    "conference": [
        "会议", "Conference", "演讲", "Keynote", "峰会", "论坛", "Webinar",
        "腾讯会议", "zoom", "teams",
    ],
}

# Strong signals: if any keyword matches, high-confidence return
STRONG_SIGNALS: List[Tuple[List[str], str, float]] = [
    (["监控", "cam01", "cam02", "dvr_", "ch01_", "ch02_", "ipc_", "nvr_"], "surveillance", 0.95),
    (["直播回放", "直播录播", "live_room", "douyu_", "huya_", "bilibili_live"], "live_stream", 0.92),
    (["会议录制", "zoom_", "teams_", "tencent_meeting", "webinar", "腾讯会议"], "conference", 0.90),
    (["gameplay", "playthrough", "boss战", "通关", "实况解说"], "gameplay", 0.88),
    (["抖音", "tiktok", "快手", "reel_", "short_"], "short_video", 0.85),
]


# ═══════════════════════════════════════════════════════════════
# Stage A: Metadata analysis
# ═══════════════════════════════════════════════════════════════

def analyze_metadata(meta: VideoMetadata) -> Dict[str, float]:
    """Score each preset based on metadata alone (no decoding needed).

    Returns a dict of preset_id -> score [0, 1].
    """
    scores: Dict[str, float] = {pid: 0.0 for pid in _ALL_PRESET_IDS}

    # ── Resolution cues ──
    if meta.width >= 3840:
        scores["film_tv"] += 0.15
        scores["documentary"] += 0.10
    elif meta.width <= 720:
        scores["surveillance"] += 0.25
        scores["live_stream"] += 0.05

    # ── Duration cues ──
    if meta.duration_sec > 3600:
        scores["film_tv"] += 0.10
        scores["conference"] += 0.15
        scores["education"] += 0.10
    elif meta.duration_sec < 60:
        scores["short_video"] += 0.25
    elif meta.duration_sec < 300:
        scores["short_video"] += 0.10
        scores["news"] += 0.05

    # ── Aspect ratio cues ──
    if meta.aspect_ratio < 1.1:
        scores["short_video"] += 0.30
        scores["live_stream"] += 0.20
    elif meta.aspect_ratio > 2.0:
        scores["film_tv"] += 0.25
    elif 1.3 < meta.aspect_ratio < 1.45:
        scores["surveillance"] += 0.15
        scores["education"] += 0.10

    # ── FPS cues ──
    if meta.fps < 15:
        scores["surveillance"] += 0.20
    elif 23.9 <= meta.fps <= 24.1:
        scores["film_tv"] += 0.15
        scores["anime"] += 0.10

    # ── Filename keyword cues ──
    name_lower = meta.file_name.lower()
    for preset_id, keywords in KEYWORD_MAP.items():
        for kw in keywords:
            if kw.lower() in name_lower:
                scores[preset_id] += 0.30

    return scores


# All preset IDs (avoid circular import from scene_presets)
_ALL_PRESET_IDS = [
    "film_tv", "variety_show", "anime", "live_stream", "gameplay",
    "education", "news", "documentary", "conference", "short_video",
    "surveillance", "custom",
]


# ═══════════════════════════════════════════════════════════════
# Stage B: Intelligent frame sampling
# ═══════════════════════════════════════════════════════════════

def build_sample_frame_list(
    total_frames: int,
    duration_sec: float,
    roi_data: Optional[List[Dict]] = None,
) -> List[int]:
    """Generate a list of frame indices for intelligent sampling.

    Strategy:
    1. Head 5%: 3 frames (opening/title)
    2. Body 80%: 15-20 evenly distributed frames
    3. Tail 15%: 3 frames (ending/credits)
    4. ROI ranges: +2 frames per ROI
    5. Short videos (< 10s): sample all frames
    """
    samples: set = set()

    if total_frames <= 300:
        step = max(1, total_frames // 20)
        return list(range(0, total_frames, step))

    # Head
    head_end = max(1, int(total_frames * 0.05))
    samples.update([0, head_end // 2, head_end])

    # Body (evenly distributed)
    body_start = head_end
    body_end = int(total_frames * 0.85)
    body_len = body_end - body_start
    if body_len > 0:
        body_samples = max(15, min(20, total_frames // 500))
        step = max(1, body_len // body_samples)
        samples.update(range(body_start, body_end, step))

    # Tail
    tail_start = body_end
    samples.update([
        tail_start,
        tail_start + (total_frames - tail_start) // 2,
        total_frames - 1,
    ])

    # ROI ranges
    for roi in (roi_data or []):
        s = roi.get("start_frame", 0)
        e = roi.get("end_frame", total_frames)
        mid = (s + e) // 2
        samples.update([s, mid, e])

    return sorted(samples)[:30]


# ═══════════════════════════════════════════════════════════════
# Stage C: Feature extraction helpers
# ═══════════════════════════════════════════════════════════════

def extract_motion_features(
    sample_frames: List[np.ndarray],
    fps: float,
) -> Tuple[float, float, int, float, float]:
    """Extract motion features from sampled frames.

    Returns:
        (avg_motion, motion_variance, cut_count, cut_frequency, static_ratio)
    """
    motions = []
    cut_count = 0
    static_count = 0

    for i in range(1, len(sample_frames)):
        try:
            import cv2
            gray_prev = cv2.cvtColor(sample_frames[i - 1], cv2.COLOR_BGR2GRAY)
            gray_curr = cv2.cvtColor(sample_frames[i], cv2.COLOR_BGR2GRAY)

            diff = cv2.absdiff(gray_prev, gray_curr)
            motion = np.count_nonzero(diff > 25) / diff.size
            motions.append(motion)

            if motion < 0.005:
                static_count += 1

            # Scene cut: structural similarity drops sharply
            try:
                from skimage.metrics import structural_similarity as ssim
                ssim_val = ssim(gray_prev, gray_curr)
                if ssim_val < 0.3:
                    cut_count += 1
            except ImportError:
                pass
        except Exception:
            continue

    avg_motion = float(np.mean(motions)) if motions else 0.0
    motion_var = float(np.var(motions)) if motions else 0.0
    static_ratio = static_count / max(1, len(motions))
    est_duration = len(sample_frames) / max(fps, 0.001)
    cut_freq = cut_count / max(1.0, est_duration)

    return avg_motion, motion_var, cut_count, cut_freq, static_ratio


def compute_text_location_entropy(
    text_boxes: List[Tuple[int, int, int, int]],
    frame_w: int,
    frame_h: int,
) -> float:
    """Compute entropy of text location distribution.

    Low entropy = text concentrated in few positions (typical OVERLAY)
    High entropy = text scattered everywhere (typical SCENE or variety show)

    Returns value in [0, 1].
    """
    if not text_boxes:
        return 0.0

    grid = np.zeros((5, 5))
    for (x1, y1, x2, y2) in text_boxes:
        cx = (x1 + x2) / 2 / max(frame_w, 1)
        cy = (y1 + y2) / 2 / max(frame_h, 1)
        gx = min(4, int(cx * 5))
        gy = min(4, int(cy * 5))
        grid[gy][gx] += 1

    total = grid.sum()
    if total == 0:
        return 0.0
    prob = grid.flatten() / total
    prob = prob[prob > 0]

    entropy = -np.sum(prob * np.log2(prob + 1e-10))
    max_entropy = np.log2(25)
    return float(entropy / max_entropy)


def compute_multi_font_score(text_regions: List[np.ndarray]) -> float:
    """Assess text style diversity within a frame.

    High variance in height, color, stroke width → variety show / short video.
    """
    if len(text_regions) < 3:
        return 0.0

    heights = []
    colors = []
    stroke_widths = []

    for region in text_regions:
        try:
            import cv2
            h, w = region.shape[:2]
            heights.append(h)
            colors.append(float(region.mean(axis=(0, 1))))

            gray = cv2.cvtColor(region, cv2.COLOR_BGR2GRAY)
            _, binary = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
            runs = []
            for row in binary:
                run_len = 0
                for px in row:
                    if px > 0:
                        run_len += 1
                    else:
                        if run_len > 0:
                            runs.append(run_len)
                        run_len = 0
            if runs:
                stroke_widths.append(float(np.median(runs)))
        except Exception:
            continue

    if not heights or not stroke_widths:
        return 0.0

    height_var = np.var(heights) / (np.mean(heights) ** 2 + 1e-6)
    color_var = float(np.var(colors) / 10000.0)
    stroke_var = np.var(stroke_widths) / (np.mean(stroke_widths) ** 2 + 1e-6)

    return float(np.clip((height_var + color_var + stroke_var) / 3, 0, 1))


def detect_letterbox(sample_frames: List[np.ndarray]) -> Tuple[float, float]:
    """Detect letterbox (top/bottom black bars) and pillarbox (left/right).

    Returns (letterbox_ratio, pillar_box_ratio).
    """
    if not sample_frames:
        return 0.0, 0.0

    try:
        import cv2
        gray = cv2.cvtColor(sample_frames[0], cv2.COLOR_BGR2GRAY)
        h, w = gray.shape

        # Top 5 rows
        top_strip = gray[:max(1, h // 20), :]
        top_dark = np.count_nonzero(top_strip < 15) / top_strip.size

        # Bottom 5 rows
        bottom_strip = gray[-max(1, h // 20):, :]
        bottom_dark = np.count_nonzero(bottom_strip < 15) / bottom_strip.size

        letterbox = (top_dark + bottom_dark) / 2

        # Left/right 5 columns
        left_strip = gray[:, :max(1, w // 20)]
        left_dark = np.count_nonzero(left_strip < 15) / left_strip.size
        right_strip = gray[:, -max(1, w // 20):]
        right_dark = np.count_nonzero(right_strip < 15) / right_strip.size

        pillarbox = (left_dark + right_dark) / 2

        return float(letterbox), float(pillarbox)
    except Exception:
        return 0.0, 0.0


# ═══════════════════════════════════════════════════════════════
# Stage D: Feature prototypes for statistical matching
# ═══════════════════════════════════════════════════════════════

# (mean, std) for each feature × preset
PROTOTYPES: Dict[str, Dict[str, Tuple[float, float]]] = {
    "film_tv": {
        "motion_intensity": (0.08, 0.25),
        "scene_cut_frequency": (0.15, 0.10),
        "text_density": (0.05, 0.02),
        "text_location_entropy": (0.15, 0.08),
        "static_ratio": (0.30, 0.15),
        "color_saturation": (0.40, 0.15),
        "letterbox_ratio": (0.05, 0.05),
        "multi_font_score": (0.10, 0.08),
    },
    "variety_show": {
        "motion_intensity": (0.20, 0.15),
        "scene_cut_frequency": (0.40, 0.20),
        "text_density": (0.12, 0.05),
        "text_location_entropy": (0.60, 0.20),
        "static_ratio": (0.10, 0.08),
        "color_saturation": (0.65, 0.15),
        "letterbox_ratio": (0.01, 0.02),
        "multi_font_score": (0.75, 0.15),
    },
    "anime": {
        "motion_intensity": (0.12, 0.15),
        "scene_cut_frequency": (0.25, 0.15),
        "text_density": (0.06, 0.03),
        "text_location_entropy": (0.25, 0.15),
        "static_ratio": (0.25, 0.15),
        "color_saturation": (0.55, 0.20),
        "letterbox_ratio": (0.02, 0.03),
        "multi_font_score": (0.30, 0.20),
    },
    "education": {
        "motion_intensity": (0.04, 0.05),
        "scene_cut_frequency": (0.05, 0.05),
        "text_density": (0.15, 0.08),
        "text_location_entropy": (0.50, 0.25),
        "static_ratio": (0.60, 0.20),
        "color_saturation": (0.35, 0.15),
        "letterbox_ratio": (0.01, 0.02),
        "multi_font_score": (0.40, 0.20),
    },
    "news": {
        "motion_intensity": (0.10, 0.10),
        "scene_cut_frequency": (0.20, 0.12),
        "text_density": (0.10, 0.04),
        "text_location_entropy": (0.40, 0.20),
        "static_ratio": (0.20, 0.12),
        "color_saturation": (0.45, 0.15),
        "letterbox_ratio": (0.01, 0.02),
        "multi_font_score": (0.25, 0.15),
    },
    "documentary": {
        "motion_intensity": (0.06, 0.08),
        "scene_cut_frequency": (0.08, 0.06),
        "text_density": (0.04, 0.02),
        "text_location_entropy": (0.30, 0.18),
        "static_ratio": (0.45, 0.20),
        "color_saturation": (0.40, 0.18),
        "letterbox_ratio": (0.03, 0.04),
        "multi_font_score": (0.15, 0.10),
    },
    "live_stream": {
        "motion_intensity": (0.18, 0.15),
        "scene_cut_frequency": (0.03, 0.03),
        "text_density": (0.10, 0.05),
        "text_location_entropy": (0.55, 0.20),
        "static_ratio": (0.15, 0.10),
        "color_saturation": (0.55, 0.15),
        "letterbox_ratio": (0.01, 0.02),
        "multi_font_score": (0.60, 0.20),
    },
    "gameplay": {
        "motion_intensity": (0.30, 0.20),
        "scene_cut_frequency": (0.08, 0.08),
        "text_density": (0.08, 0.04),
        "text_location_entropy": (0.45, 0.20),
        "static_ratio": (0.10, 0.08),
        "color_saturation": (0.50, 0.20),
        "letterbox_ratio": (0.01, 0.02),
        "multi_font_score": (0.45, 0.20),
    },
    "surveillance": {
        "motion_intensity": (0.02, 0.03),
        "scene_cut_frequency": (0.00, 0.01),
        "text_density": (0.02, 0.01),
        "text_location_entropy": (0.10, 0.08),
        "static_ratio": (0.85, 0.10),
        "color_saturation": (0.15, 0.10),
        "letterbox_ratio": (0.00, 0.01),
        "multi_font_score": (0.05, 0.05),
    },
    "short_video": {
        "motion_intensity": (0.25, 0.20),
        "scene_cut_frequency": (0.50, 0.30),
        "text_density": (0.10, 0.06),
        "text_location_entropy": (0.55, 0.25),
        "static_ratio": (0.08, 0.05),
        "color_saturation": (0.60, 0.15),
        "letterbox_ratio": (0.05, 0.08),
        "multi_font_score": (0.55, 0.20),
    },
    "conference": {
        "motion_intensity": (0.03, 0.04),
        "scene_cut_frequency": (0.04, 0.04),
        "text_density": (0.12, 0.06),
        "text_location_entropy": (0.40, 0.20),
        "static_ratio": (0.55, 0.25),
        "color_saturation": (0.30, 0.15),
        "letterbox_ratio": (0.01, 0.02),
        "multi_font_score": (0.35, 0.20),
    },
}


# ═══════════════════════════════════════════════════════════════
# Main detector class
# ═══════════════════════════════════════════════════════════════

class VideoTypeDetector:
    """Automatic video type detector.

    Multi-layer classification:
      Layer 0: Literal filename keyword match (highest priority)
      Layer 1: Deterministic rules (hard-feature thresholds)
      Layer 2: Statistical prototype matching
      Layer 3: Optional LLM assist for borderline cases
    """

    def __init__(self, use_llm: bool = False):
        self.use_llm = use_llm

    def detect(
        self,
        video_path: str,
        metadata: VideoMetadata,
        roi_data: Optional[List[Dict]] = None,
    ) -> DetectionResult:
        """Run full auto-detection pipeline.

        Args:
            video_path: Path to the video file.
            metadata: Pre-extracted VideoMetadata.
            roi_data: Optional ROI definitions (for smarter sampling).

        Returns:
            DetectionResult with recommended type, confidence, and alternatives.
        """
        # ── Layer 0: Literal filename keyword match ──
        literal_result = self._literal_keyword_match(metadata)
        if literal_result and literal_result.confidence >= 0.90:
            return literal_result

        # ── Stage A+B+C: feature extraction ──
        try:
            features = self._extract_features(video_path, metadata, roi_data)
        except Exception as e:
            logger.warning(f"Feature extraction failed: {e}; falling back to metadata-only")
            features = VideoTypeFeatures()

        # ── Layer 1: Deterministic rules ──
        rule_result = self._apply_deterministic_rules(features, metadata)
        if rule_result and rule_result.confidence >= 0.85:
            return rule_result

        # ── Layer 2: Statistical prototype matching ──
        stat_result = self._prototype_matching(features, metadata)
        if stat_result.confidence >= 0.70 or not self.use_llm:
            return stat_result

        # ── Layer 3: LLM assist not implemented in this version ──
        return stat_result

    # ── Layer 0: Filename keywords ──────────────────────────

    def _literal_keyword_match(self, meta: VideoMetadata) -> Optional[DetectionResult]:
        """Check filename for strong indicator keywords."""
        name_lower = meta.file_name.lower()

        for keywords, preset_id, confidence in STRONG_SIGNALS:
            if any(kw in name_lower for kw in keywords):
                from core.scene_presets import get_preset_by_id
                preset = get_preset_by_id(preset_id)
                return DetectionResult(
                    detected_type=preset_id,
                    confidence=confidence,
                    recommended_preset=preset,
                    rationale=f"文件名包含强信号关键词: {keywords}",
                    alternative_presets=[],
                )
        return None

    # ── Feature extraction pipeline ────────────────────────

    def _extract_features(
        self,
        video_path: str,
        meta: VideoMetadata,
        roi_data: Optional[List[Dict]] = None,
    ) -> VideoTypeFeatures:
        """Run stages A-C: sample frames → extract features."""
        features = VideoTypeFeatures()

        # Build sample frame list
        sample_indices = build_sample_frame_list(
            meta.total_frames, meta.duration_sec, roi_data
        )

        if not sample_indices:
            return features

        # Decode sample frames
        sample_frames = self._decode_frames(video_path, sample_indices)
        if not sample_frames:
            return features

        # Motion features
        try:
            (
                features.avg_motion_intensity,
                features.motion_variance,
                features.scene_cut_count,
                features.scene_cut_frequency,
                features.static_ratio,
            ) = extract_motion_features(sample_frames, meta.fps)
        except Exception as e:
            logger.debug(f"Motion feature extraction failed: {e}")

        # Visual composition
        features.letterbox_ratio, features.pillar_box_ratio = detect_letterbox(sample_frames)

        # Color features from first frame
        try:
            import cv2
            hsv = cv2.cvtColor(sample_frames[0], cv2.COLOR_BGR2HSV)
            features.dominant_color_saturation = float(hsv[:, :, 1].mean() / 255.0)
            # Color spread: variance of hue histogram
            hist = cv2.calcHist([hsv], [0], None, [32], [0, 180])
            hist_norm = hist / hist.sum()
            features.color_histogram_spread = float(1.0 - max(hist_norm)[0])
            features.brightness_variance = float(hsv[:, :, 2].var() / (255.0 ** 2))
        except Exception as e:
            logger.debug(f"Color feature extraction failed: {e}")

        # Text density estimate (heuristic: edge density as proxy)
        try:
            import cv2
            text_densities = []
            for frame in sample_frames[:10]:
                gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
                edges = cv2.Canny(gray, 50, 150)
                text_densities.append(np.count_nonzero(edges) / edges.size)
            features.text_density = float(np.mean(text_densities)) if text_densities else 0.0
        except Exception:
            pass

        # Multi-font score (sampled frames)
        try:
            font_scores = []
            for frame in sample_frames[:5]:
                # Crude text region detection via MSER
                import cv2
                gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
                mser = cv2.MSER_create()
                regions, _ = mser.detectRegions(gray)
                if len(regions) >= 3:
                    region_imgs = []
                    for r in regions[:10]:
                        x, y, w, h = cv2.boundingRect(r)
                        if w > 10 and h > 10:
                            region_imgs.append(frame[y:y+h, x:x+w])
                    if len(region_imgs) >= 3:
                        font_scores.append(compute_multi_font_score(region_imgs))
            features.multi_font_score = float(np.mean(font_scores)) if font_scores else 0.0
        except Exception:
            pass

        return features

    def _decode_frames(
        self, video_path: str, frame_indices: List[int]
    ) -> List[np.ndarray]:
        """Decode specific frames from a video."""
        try:
            import cv2
            cap = cv2.VideoCapture(video_path)
            if not cap.isOpened():
                return []

            frames = []
            for idx in frame_indices:
                cap.set(cv2.CAP_PROP_POS_FRAMES, idx)
                ret, frame = cap.read()
                if ret and frame is not None:
                    frames.append(frame)
                if len(frames) >= 30:
                    break

            cap.release()
            return frames
        except Exception as e:
            logger.warning(f"Frame decoding failed: {e}")
            return []

    # ── Layer 1: Deterministic rules ───────────────────────

    def _apply_deterministic_rules(
        self, feats: VideoTypeFeatures, meta: VideoMetadata
    ) -> Optional[DetectionResult]:
        """Apply hard-coded rules for high-confidence classification."""
        from core.scene_presets import get_preset_by_id

        # Rule 1: Ultra-low motion + no cuts + low color → surveillance
        if (
            feats.avg_motion_intensity < 0.02
            and feats.scene_cut_frequency < 0.02
            and feats.static_ratio > 0.80
            and feats.color_histogram_spread < 0.25
        ):
            return DetectionResult(
                detected_type="surveillance",
                confidence=0.90,
                recommended_preset=get_preset_by_id("surveillance"),
                rationale="极低运动强度、无场景切换、低色彩丰富度，高度符合监控录像特征",
                alternative_presets=[get_preset_by_id("conference")],
            )

        # Rule 2: High multi-font + high cut frequency + high color → variety
        if (
            feats.multi_font_score > 0.65
            and feats.scene_cut_frequency > 0.25
            and feats.color_histogram_spread > 0.50
        ):
            return DetectionResult(
                detected_type="variety_show",
                confidence=0.85,
                recommended_preset=get_preset_by_id("variety_show"),
                rationale="高多字体得分、高场景切换频率、高色彩丰富度，高度符合综艺节目特征",
                alternative_presets=[get_preset_by_id("short_video")],
            )

        # Rule 3: High motion + low cuts + no letterbox → gameplay
        if (
            feats.avg_motion_intensity > 0.20
            and feats.scene_cut_frequency < 0.10
            and feats.letterbox_ratio < 0.02
            and feats.static_ratio < 0.15
        ):
            return DetectionResult(
                detected_type="gameplay",
                confidence=0.82,
                recommended_preset=get_preset_by_id("gameplay"),
                rationale="高运动强度、低场景切换、无电影黑边，高度符合游戏录屏特征",
                alternative_presets=[
                    get_preset_by_id("live_stream"),
                    get_preset_by_id("short_video"),
                ],
            )

        # Rule 4: High text density + high static + moderate motion → education
        if (
            feats.text_density > 0.10
            and feats.static_ratio > 0.50
            and 0.02 < feats.avg_motion_intensity < 0.12
        ):
            return DetectionResult(
                detected_type="education",
                confidence=0.82,
                recommended_preset=get_preset_by_id("education"),
                rationale="高文字密度、高静态画面比例、中等运动强度，高度符合教学视频特征",
                alternative_presets=[
                    get_preset_by_id("conference"),
                    get_preset_by_id("documentary"),
                ],
            )

        # Rule 5: Letterbox + wide aspect + moderate motion → film_tv
        if (
            feats.letterbox_ratio > 0.03
            and meta.aspect_ratio > 1.7
            and 0.05 < feats.avg_motion_intensity < 0.30
        ):
            return DetectionResult(
                detected_type="film_tv",
                confidence=0.85,
                recommended_preset=get_preset_by_id("film_tv"),
                rationale="检测到电影黑边、宽屏幕比例、适度运动强度，高度符合影视剧集特征",
                alternative_presets=[get_preset_by_id("documentary")],
            )

        # Rule 6: Low static + no cuts + high FPS → live stream
        if (
            feats.static_ratio < 0.12
            and feats.scene_cut_frequency < 0.05
            and meta.fps >= 25
            and feats.text_location_entropy > 0.40
        ):
            return DetectionResult(
                detected_type="live_stream",
                confidence=0.80,
                recommended_preset=get_preset_by_id("live_stream"),
                rationale="持续非静态、无场景切换、高帧率、文字分布离散，高度符合直播特征",
                alternative_presets=[get_preset_by_id("gameplay")],
            )

        return None

    # ── Layer 2: Statistical prototype matching ────────────

    def _prototype_matching(
        self, feats: VideoTypeFeatures, meta: VideoMetadata
    ) -> DetectionResult:
        """Match feature vector against scene prototypes using normalized Euclidean distance."""
        from core.scene_presets import get_preset_by_id

        feature_vector = {
            "motion_intensity": feats.avg_motion_intensity,
            "scene_cut_frequency": feats.scene_cut_frequency,
            "text_density": feats.text_density,
            "text_location_entropy": feats.text_location_entropy,
            "static_ratio": feats.static_ratio,
            "color_saturation": feats.dominant_color_saturation,
            "letterbox_ratio": feats.letterbox_ratio,
            "multi_font_score": feats.multi_font_score,
        }

        scores: Dict[str, float] = {}
        for preset_id, prototype in PROTOTYPES.items():
            distance = 0.0
            valid_dims = 0
            for dim, (mean, std) in prototype.items():
                val = feature_vector.get(dim)
                if val is not None:
                    d = (val - mean) / max(std, 0.01)
                    distance += d ** 2
                    valid_dims += 1
            if valid_dims > 0:
                avg_distance = np.sqrt(distance / valid_dims)
                scores[preset_id] = float(np.exp(-avg_distance))

        # Blend with metadata scores (30% metadata, 70% features)
        meta_scores = analyze_metadata(meta)
        for pid in scores:
            scores[pid] = 0.70 * scores[pid] + 0.30 * meta_scores.get(pid, 0.0)

        # Rank
        ranked = sorted(scores.items(), key=lambda x: x[1], reverse=True)
        best_id, best_score = ranked[0]

        # Alternatives within 15% of best score
        alternatives = []
        for pid, score in ranked[1:4]:
            if score > best_score * 0.85:
                alternatives.append(get_preset_by_id(pid))

        return DetectionResult(
            detected_type=best_id,
            confidence=best_score,
            recommended_preset=get_preset_by_id(best_id),
            rationale=f"特征原型匹配: 与 {best_id} 原型距离最近 (score={best_score:.2f})",
            alternative_presets=alternatives,
            all_scores=dict(ranked),
        )
