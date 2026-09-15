# core/subtitle_generator/source_classification.py
import logging
from typing import TYPE_CHECKING, Any, Dict, List, Optional

from PySide6.QtCore import QCoreApplication

from .models import FrameData, TextLine

if TYPE_CHECKING:
    from core.classification_features import TextRegionFeatures

logger = logging.getLogger("core.subtitle_generator")

_tr = QCoreApplication.translate

class _SourceClassificationMixin:
    """Text source classification / filtering helpers moved from core/subtitle_generator.py (L857-L1108)."""

    def _classify_and_filter_text_lines(
        self, organized_data: Dict[str, List[FrameData]]
    ) -> Dict[str, List[FrameData]]:
        """Classify each TextLine as OVERLAY/SCENE/UNKNOWN and filter by preset.

        This is the integration point for the text source classifier into
        the subtitle generation pipeline. Only ROIs whose
        ``text_filter_policy`` is ``"auto"`` participate: manually drawn ROIs
        (``"keep_all"``) are never filtered — scene text is a first-class
        output of this tool.
        """
        from core.text_source_classifier import (
            TextSource,
            create_classifier_from_config,
        )

        classifier = create_classifier_from_config(self.source_filter_config)
        config = self.source_filter_config or {}

        keep_overlay = config.get("keep_overlay", True)
        keep_scene = config.get("keep_scene", True)
        keep_unknown = config.get("keep_unknown", True)

        total_before = 0
        total_after = 0
        classified_overlay = 0
        classified_scene = 0
        classified_unknown = 0
        skipped_rois: List[str] = []
        temporal_stats = self._build_temporal_stats(organized_data)
        sem_cache: Dict[str, Dict[str, Any]] = {}

        for roi_id, frame_list in organized_data.items():
            if self._roi_filter_policy(roi_id) != "auto":
                skipped_rois.append(str(roi_id))
                continue
            for frame_data in frame_list:
                for text_line in frame_data.lines:
                    total_before += 1

                    # Build feature vector
                    features = self._build_text_region_features(
                        text_line, frame_data, temporal_stats, sem_cache=sem_cache
                    )

                    # Classify
                    result = classifier.classify(features)

                    # Store result on TextLine
                    text_line.source_label = result.source.value
                    text_line.source_confidence = result.confidence

                    if result.source == TextSource.OVERLAY:
                        classified_overlay += 1
                    elif result.source == TextSource.SCENE:
                        classified_scene += 1
                    else:
                        classified_unknown += 1

        # Filter based on preset (auto-policy ROIs only)
        for roi_id, frame_list in organized_data.items():
            if self._roi_filter_policy(roi_id) != "auto":
                continue
            for frame_data in frame_list:
                frame_data.lines = [
                    line for line in frame_data.lines
                    if (
                        (line.source_label == "overlay" and keep_overlay)
                        or (line.source_label == "scene" and keep_scene)
                        or (line.source_label == "unknown" and keep_unknown)
                        or (not line.source_label)  # Keep if not classified
                    )
                ]
                total_after += len(frame_data.lines)

        # Remove empty frames after filtering
        for roi_id in list(organized_data.keys()):
            if self._roi_filter_policy(roi_id) != "auto":
                continue
            organized_data[roi_id] = [
                fd for fd in organized_data[roi_id] if not fd.is_empty
            ]
            if not organized_data[roi_id]:
                del organized_data[roi_id]

        logger.info(
            _tr(
                "OCRToASSOptimizer",
                "Text source classification: {} OVERLAY, {} SCENE, {} UNKNOWN. "
                "Filtered {} -> {} text lines (keep_overlay={}, keep_scene={}, keep_unknown={})."
            ).format(
                classified_overlay, classified_scene, classified_unknown,
                total_before, total_after,
                keep_overlay, keep_scene, keep_unknown,
            )
        )
        if skipped_rois:
            logger.info(
                _tr(
                    "OCRToASSOptimizer",
                    "Text source filter skipped keep-all ROIs: {}."
                ).format(", ".join(sorted(skipped_rois)))
            )

        return organized_data

    @staticmethod
    def _normalize_for_stats(text: str) -> str:
        """文本统计键：去空白（保留标点，比水印匹配的归一化更保守）。"""
        return "".join(ch for ch in str(text) if not ch.isspace())

    def _build_temporal_stats(
        self, organized_data: Dict[str, List[FrameData]]
    ) -> Dict[str, Any]:
        """跨帧统计：文本键（首末帧/出现次数/坐标离散度）+ 垂直带键（文本分布）。

        为分类器的时间层提供真实数据（此前 duration/appearance_count 等全是
        占位常量）：对话字幕时长 1–8s、同带文本随时间变化；水印/台标恒定
        文本长时间占据同一位置。
        """
        by_text: Dict[str, Dict[str, Any]] = {}
        by_band: Dict[int, Dict[str, int]] = {}
        band_grid = 48.0
        for frame_list in organized_data.values():
            for frame_data in frame_list:
                for line in frame_data.lines:
                    key = self._normalize_for_stats(line.text)
                    if not key:
                        continue
                    x1, y1, x2, y2 = line.box
                    cx, cy = (x1 + x2) / 2.0, (y1 + y2) / 2.0
                    entry = by_text.setdefault(
                        key,
                        {"first": frame_data.frame_num, "last": frame_data.frame_num,
                         "count": 0, "xs": [], "ys": []},
                    )
                    entry["first"] = min(entry["first"], frame_data.frame_num)
                    entry["last"] = max(entry["last"], frame_data.frame_num)
                    entry["count"] += 1
                    entry["xs"].append(cx)
                    entry["ys"].append(cy)
                    band = int(cy / band_grid)
                    band_stats = by_band.setdefault(band, {})
                    band_stats[key] = band_stats.get(key, 0) + 1

        # Precompute per-text position aggregates once. Recomputing mean and
        # spread per occurrence is O(K²) for a text seen K times — for a
        # watermark present on every frame of a 2-hour video that is tens of
        # billions of Python-level operations.
        for entry in by_text.values():
            xs, ys = entry.pop("xs"), entry.pop("ys")
            mean_x = sum(xs) / len(xs)
            mean_y = sum(ys) / len(ys)
            spread = max(
                max(abs(x - mean_x) for x in xs),
                max(abs(y - mean_y) for y in ys),
            )
            entry["spread"] = spread
            entry["is_stationary"] = spread <= 24.0
        return {"by_text": by_text, "by_band": by_band, "band_grid": band_grid}

    def _build_text_region_features(
        self,
        text_line: TextLine,
        frame_data: FrameData,
        temporal_stats: Optional[Dict[str, Any]] = None,
        sem_cache: Optional[Dict[str, Dict[str, Any]]] = None,
    ) -> "TextRegionFeatures":
        """Build a TextRegionFeatures vector from a TextLine and its frame context."""
        from core.classification_features import (
            TextRegionFeatures,
            extract_semantic_features,
        )

        x1, y1, x2, y2 = text_line.box
        center_x = (x1 + x2) / 2
        center_y = (y1 + y2) / 2

        # Spatial
        relative_y = center_y / max(self.height, 1)
        relative_height = (y2 - y1) / max(self.height, 1)
        is_edge_aligned = (
            x1 < 5 or y1 < 5
            or x2 > self.width - 5
            or y2 > self.height - 5
        )
        is_safe_zone = (
            relative_y > 0.80 or relative_y < 0.15
        )

        # Semantic（同一文本的语义特征按文本缓存，避免每帧重复提取）
        if sem_cache is not None and text_line.text in sem_cache:
            sem_feats = sem_cache[text_line.text]
        else:
            sem_feats = extract_semantic_features(text_line.text)
            if sem_cache is not None:
                sem_cache[text_line.text] = sem_feats

        # Temporal（真实跨帧统计；无统计时退化为单帧占位值）
        stats_key = self._normalize_for_stats(text_line.text)
        stats = (temporal_stats or {}).get("by_text", {}).get(stats_key)
        if stats:
            first_seen_frame = stats["first"]
            last_seen_frame = stats["last"]
            appearance_count = stats["count"]
            duration_sec = (
                (last_seen_frame - first_seen_frame + 1) / self.fps
                if self.fps > 0 else 0.0
            )
            is_stationary = stats.get("is_stationary", True)
        else:
            first_seen_frame = frame_data.frame_num
            last_seen_frame = frame_data.frame_num
            duration_sec = 0.0
            appearance_count = 1
            is_stationary = True

        # 同一垂直带内该文本的占比：随时间变化的文本占比低（典型字幕），
        # 恒定文本占比趋于 1（水印/台标）。
        text_stability = 0.0
        if temporal_stats:
            band_grid = temporal_stats.get("band_grid", 48.0)
            band_stats = (temporal_stats.get("by_band") or {}).get(
                int(center_y / band_grid)
            ) or {}
            band_total = sum(band_stats.values())
            if band_total > 0:
                text_stability = band_stats.get(stats_key, 0) / band_total

        features = TextRegionFeatures(
            frame_width=self.width,
            frame_height=self.height,
            bbox=(x1, y1, x2, y2),
            center_x=center_x,
            center_y=center_y,
            relative_y=relative_y,
            relative_height=relative_height,
            is_edge_aligned=is_edge_aligned,
            is_safe_zone=is_safe_zone,
            # Temporal (real cross-frame statistics when available)
            first_seen_frame=first_seen_frame,
            last_seen_frame=last_seen_frame,
            duration_sec=duration_sec,
            appearance_count=appearance_count,
            is_stationary=is_stationary,
            text_stability=text_stability,
            # Semantic
            raw_text=text_line.text,
            text_length=sem_feats["text_length"],
            is_single_line=sem_feats["is_single_line"],
            line_count=sem_feats["line_count"],
            has_punctuation_at_end=sem_feats["has_punctuation_at_end"],
            dialogue_pattern_score=sem_feats["dialogue_pattern_score"],
            proper_noun_ratio=sem_feats["proper_noun_ratio"],
            contains_price=sem_feats["contains_price"],
            contains_address=sem_feats["contains_address"],
            contains_slogan=sem_feats["contains_slogan"],
        )

        return features
