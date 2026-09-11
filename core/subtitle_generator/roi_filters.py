# core/subtitle_generator/roi_filters.py
import logging
from collections import defaultdict
from typing import Dict, List

from PySide6.QtCore import QCoreApplication

from .models import FrameData, SubtitleGroup

logger = logging.getLogger("core.subtitle_generator")

_tr = QCoreApplication.translate

class _RoiFiltersMixin:
    """ROI profile learning / ROI filtering helpers moved from core/subtitle_generator.py (L713-L855)."""

    def _classify_location_type(self, group: SubtitleGroup) -> str:
        avg_box = group.get_avg_box()
        if not avg_box:
            return "UNKNOWN"
        avg_y_center = (avg_box[1] + avg_box[3]) / 2
        if avg_y_center > self.height * self.VIDEO_BOTTOM_AREA:
            return "BOTTOM"
        if avg_y_center < self.height * self.VIDEO_TOP_AREA:
            return "TOP"
        return "SCENE"

    def _group_text_height(self, group: SubtitleGroup) -> float:
        if not group.lines:
            return 0.0
        hs = [float(max(0.0, line.bounding_height)) for line in group.lines]
        hs = [h for h in hs if h > 0]
        if not hs:
            return 0.0
        hs.sort()
        return hs[len(hs) // 2]

    def _build_roi_profile(self, groups: List[SubtitleGroup]) -> Dict[str, float | str]:
        """
        从该 ROI 的字幕组中学习：
        - dominant_location: BOTTOM/TOP/SCENE（按组数加权）
        - dominant_height: 主字幕高度（bounding box 高度的中位数）
        """
        if not groups:
            return {"dominant_location": "UNKNOWN", "dominant_height": 0.0}

        locs: List[str] = []
        heights_by_loc: Dict[str, List[float]] = defaultdict(list)
        for g in groups:
            loc = self._classify_location_type(g)
            locs.append(loc)
            h = self._group_text_height(g)
            if h > 0:
                heights_by_loc[loc].append(h)

        from collections import Counter

        cnt = Counter(locs)
        dominant_loc, dominant_votes = cnt.most_common(1)[0]
        if len(groups) >= int(self.ROI_PROFILE_MIN_GROUPS):
            if (dominant_votes / max(1, len(groups))) < float(self.ROI_PROFILE_DOMINANT_RATIO):
                dominant_loc = "MIXED"

        # 高度取主导位置类型的中位数；若不可用则退化为所有可用高度的中位数
        cand = heights_by_loc.get(dominant_loc) if dominant_loc in heights_by_loc else None
        if not cand:
            all_h: List[float] = []
            for xs in heights_by_loc.values():
                all_h.extend(xs)
            cand = all_h
        dom_h = 0.0
        if cand:
            cand = [float(x) for x in cand if x > 0]
            cand.sort()
            dom_h = float(cand[len(cand) // 2]) if cand else 0.0

        return {"dominant_location": dominant_loc, "dominant_height": dom_h}

    def _filter_groups_by_roi_profile(self, groups: List[SubtitleGroup]) -> List[SubtitleGroup]:
        if not groups:
            return []
        prof = self._build_roi_profile(groups)
        dom_loc = str(prof.get("dominant_location") or "UNKNOWN")
        dom_h = float(prof.get("dominant_height") or 0.0)
        if dom_loc in ("UNKNOWN", "MIXED") or dom_h <= 0:
            return groups

        min_r = float(self.ROI_HEIGHT_FILTER_MIN_RATIO)
        max_r = float(self.ROI_HEIGHT_FILTER_MAX_RATIO)

        kept: List[SubtitleGroup] = []
        for g in groups:
            loc = self._classify_location_type(g)
            # ROI 主导是底部/顶部字幕时，优先排除“场景”杂项
            if dom_loc in ("BOTTOM", "TOP") and loc == "SCENE":
                continue
            # 主导是场景字时，不去过滤 BOTTOM/TOP（避免误杀真正场景字偶尔靠近边缘）
            if dom_loc == "SCENE" and loc in ("BOTTOM", "TOP"):
                continue

            h = self._group_text_height(g)
            if h <= 0:
                continue
            r = h / dom_h if dom_h > 0 else 1.0
            if r < min_r or r > max_r:
                continue
            kept.append(g)

        # 避免把 ROI 过滤空：如果过于严格导致无输出，回退到原始 groups
        return kept if kept else groups

    def _apply_watermark_filter(
        self, organized_data: Dict[str, List[FrameData]]
    ) -> Dict[str, List[FrameData]]:
        """Drop watermark lines matched against the deep-scan watermark registry.

        Runs before scene classification and is independent of its switch:
        watermark removal applies to every ROI (manual keep_all ROIs included)
        — the review dialog decides which watermark candidates actually get
        registered.
        """
        from core.watermark_filter import build_watermark_filter

        watermark_filter = build_watermark_filter(self.watermark_filter_config)
        if watermark_filter is None:
            return organized_data

        dropped = 0
        for roi_id, frame_list in organized_data.items():
            for frame_data in frame_list:
                kept_lines = []
                for text_line in frame_data.lines:
                    hit, _reason = watermark_filter.should_drop(
                        text_line.text, text_line.box
                    )
                    if hit:
                        dropped += 1
                        continue
                    kept_lines.append(text_line)
                frame_data.lines = kept_lines

        for roi_id in list(organized_data.keys()):
            organized_data[roi_id] = [
                fd for fd in organized_data[roi_id] if not fd.is_empty
            ]
            if not organized_data[roi_id]:
                del organized_data[roi_id]

        logger.info(
            _tr(
                "OCRToASSOptimizer",
                "Watermark filter removed {} text line(s)."
            ).format(dropped)
        )
        return organized_data

    def _roi_filter_policy(self, roi_id: str) -> str:
        """Per-ROI text filter policy; unknown ROIs default to keep_all."""
        return str(self.roi_text_filter_policies.get(str(roi_id), "keep_all"))
