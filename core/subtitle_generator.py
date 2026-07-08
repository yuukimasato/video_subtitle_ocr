# core/subtitle_generator.py
import os
import json
import cv2
import math
import re
import Levenshtein
import logging
from pathlib import Path
from typing import List, Dict, Tuple, Optional, Generator, Callable, Sequence

from core.subtitle_llm_polish import (
    SubtitlePolisherConfig,
    polish_subtitle_texts,
    deepseek_suggest_merge_params,
    deepseek_merge_fragment_text,
    params_close,
)
from dataclasses import dataclass, field
from collections import defaultdict

from PySide6.QtCore import QCoreApplication

logger = logging.getLogger(__name__)

_tr = QCoreApplication.translate

@dataclass
class TextLine:
    text: str
    score: float
    box: Tuple[int, int, int, int]
    polygon: List[Tuple[int, int]]
    source_label: str = ""          # "overlay", "scene", "unknown"
    source_confidence: float = 0.0  # Classification confidence
    @property
    def center(self) -> Tuple[float, float]:
        x_coords = [p[0] for p in self.polygon]; y_coords = [p[1] for p in self.polygon]
        if not x_coords: return 0.0, 0.0
        return sum(x_coords) / len(x_coords), sum(y_coords) / len(y_coords)
    @property
    def bounding_height(self) -> float:
        y_coords = [p[1] for p in self.polygon]
        if not y_coords: return 0.0
        return max(y_coords) - min(y_coords)

@dataclass
class FrameData:
    frame_num: int
    time_sec: float = 0.0
    lines: List[TextLine] = field(default_factory=list)
    @property
    def text_fingerprint(self) -> Tuple[str, ...]:
        return tuple(sorted([line.text for line in self.lines]))
    @property
    def is_empty(self) -> bool:
        return not self.lines

@dataclass
class SubtitleGroup:
    start_frame: int
    end_frame: int
    lines: List[TextLine]
    @property
    def duration_frames(self) -> int:
        return self.end_frame - self.start_frame + 1
    def get_avg_box(self) -> Optional[Tuple[float, float, float, float]]:
        if not self.lines: return None
        x1 = sum(line.box[0] for line in self.lines) / len(self.lines)
        y1 = sum(line.box[1] for line in self.lines) / len(self.lines)
        x2 = sum(line.box[2] for line in self.lines) / len(self.lines)
        y2 = sum(line.box[3] for line in self.lines) / len(self.lines)
        return (x1, y1, x2, y2)

class OCRToASSOptimizer:
    MIN_SCORE_THRESHOLD = 0.6
    MIN_DURATION_FRAMES = 3
    MERGE_TEXT_SIMILARITY = 0.85
    MERGE_POS_TOLERANCE = 20
    # 帧号不连续但间隔很小时，若总字数相同且文本/位置一致，仍合并为一条字幕。
    MERGE_BRIDGE_MAX_FRAMES = 8
    MERGE_BRIDGE_MAX_SEC = 0.45
    VIDEO_BOTTOM_AREA = 0.75
    VIDEO_TOP_AREA = 0.15
    # ROI 内字幕“主字号”学习与过滤（用于排除小场景字/杂项）。
    # 过滤只在“该 ROI 的主导位置类型为 BOTTOM/TOP”时更激进；
    # 若 ROI 主导为 SCENE，则保留场景文字逻辑。
    ROI_PROFILE_MIN_GROUPS = 3
    ROI_PROFILE_DOMINANT_RATIO = 0.55
    ROI_HEIGHT_FILTER_MIN_RATIO = 0.65
    ROI_HEIGHT_FILTER_MAX_RATIO = 1.65
    # ASS 条目级合并：压制同一 ROI 内因 OCR 抖动产生的时间轴碎片或多字错字副本。
    DIALOG_MERGE_MAX_GAP_SEC = 4.0
    DIALOG_MERGE_MIN_RATIO = 0.78
    DIALOG_MERGE_MIN_OVERLAP_LEN = 6
    # Scene 噪声过滤与合并（常见：单字/纯数字/空括号/极短闪烁）
    SCENE_EVENT_MIN_DURATION_SEC = 0.35
    SCENE_EVENT_MIN_TEXT_LEN = 3
    SCENE_POS_TOLERANCE_PX = 24

    def __init__(
        self,
        video_path: str,
        output_path: str,
        fps: float,
        width: int,
        height: int,
        template_path: Optional[str] = None,
        subtitle_polisher: Optional[SubtitlePolisherConfig] = None,
        source_filter_config: Optional[Dict] = None,
    ):
        self.video_path = Path(video_path)
        self.output_path = Path(output_path)
        self.fps = fps
        self.width = width
        self.height = height
        self.template_path = Path(template_path) if template_path else None
        self.source_filter_config = source_filter_config
        logger.info(_tr("OCRToASSOptimizer", "Subtitle generator initialized: {}x{} @ {:.2f} FPS").format(self.width, self.height, self.fps))
        if self.template_path and self.template_path.exists():
            logger.info(_tr("OCRToASSOptimizer", "Using style template: {}").format(self.template_path))
        else:
            logger.info(_tr("OCRToASSOptimizer", "No style template used, generating a rich set of default styles."))
        self.subtitle_polisher = subtitle_polisher

    def _parse_ass_time_to_seconds(self, ts: str) -> float:
        parts = ts.strip().split(":")
        if len(parts) != 3:
            raise ValueError(f"bad ASS time: {ts!r}")
        h, m, s = parts
        return float(int(h)) * 3600.0 + float(int(m)) * 60.0 + float(s)

    def _canonical_subtitle_plain_for_compare(self, body: str) -> str:
        """去除 ASS \\n/\\N 断行并把疑似 OCR 单行噪声（如尾随单字 ''中''）从对比用字符串中摘掉。"""
        normalized = body.replace("\r", "")
        segs = re.split(r"(?i)\\[nN]", normalized)
        out: List[str] = []
        for seg in segs:
            z = re.sub(r"\s+", "", seg.strip())
            if not z:
                continue
            if len(z) == 1 and out and sum(len(x) for x in out) >= self.DIALOG_MERGE_MIN_OVERLAP_LEN:
                continue
            out.append(z)
        return "".join(out)

    def _dialogue_time_gap_seconds(self, end_ts: str, start_ts_next: str) -> float:
        return self._parse_ass_time_to_seconds(start_ts_next) - self._parse_ass_time_to_seconds(end_ts)

    def _parse_pos_from_tags(self, tags: str) -> Optional[Tuple[int, int]]:
        # tags like "{\\an5\\pos(978,529)}"
        m = re.search(r"\\pos\(\s*(-?\d+)\s*,\s*(-?\d+)\s*\)", tags or "")
        if not m:
            return None
        try:
            return int(m.group(1)), int(m.group(2))
        except Exception:
            return None

    def _scene_tags_close(self, tags_a: str, tags_b: str) -> bool:
        pa = self._parse_pos_from_tags(tags_a)
        pb = self._parse_pos_from_tags(tags_b)
        if pa is None or pb is None:
            return tags_a == tags_b
        tol = int(max(0, self.SCENE_POS_TOLERANCE_PX))
        return abs(pa[0] - pb[0]) <= tol and abs(pa[1] - pb[1]) <= tol

    def _is_noise_body(self, body: str) -> bool:
        b = (body or "").replace("\r", "").strip()
        if not b:
            return True
        # Remove ASS explicit line breaks for judgement
        z = re.sub(r"(?i)\\[nN]", "", b)
        z = re.sub(r"\s+", "", z)
        if not z:
            return True
        if z in ("()", "（）", "[]", "【】", "{}"):
            return True
        if re.fullmatch(r"[\(\)\[\]\{\}（）【】]+", z):
            return True
        if re.fullmatch(r"[0-9]+", z):
            return True
        if re.fullmatch(r"[0-9]+[A-Za-z]+", z) or re.fullmatch(r"[A-Za-z]+[0-9]+", z):
            # 常见 OCR 垃圾：短促闪烁的编号/序号
            return len(z) <= 4
        if len(z) == 1 and z not in ("，", "。", "！", "？", ".", "!", "?"):
            return True
        return False

    def _event_duration_sec(self, ev: Dict[str, str]) -> float:
        try:
            return self._parse_ass_time_to_seconds(ev["end_time"]) - self._parse_ass_time_to_seconds(ev["start_time"])
        except Exception:
            return 0.0

    def _filter_events(self, events: List[Dict[str, str]]) -> List[Dict[str, str]]:
        if not events:
            return []
        out: List[Dict[str, str]] = []
        for ev in events:
            body = ev.get("body", "")
            style = str(ev.get("style", ""))
            if style == "Scene":
                dur = self._event_duration_sec(ev)
                canon = self._canonical_subtitle_plain_for_compare(body)
                if self._is_noise_body(body):
                    continue
                if dur > 0 and dur < float(self.SCENE_EVENT_MIN_DURATION_SEC) and len(canon) < int(self.SCENE_EVENT_MIN_TEXT_LEN):
                    continue
            else:
                # 过滤全局明显垃圾
                if self._is_noise_body(body):
                    continue
            out.append(ev)
        return out

    def _dialogue_bodies_similar(self, body_a: str, body_b: str) -> bool:
        ca = self._canonical_subtitle_plain_for_compare(body_a)
        cb = self._canonical_subtitle_plain_for_compare(body_b)
        if not ca or not cb:
            return False
        if len(ca) < self.DIALOG_MERGE_MIN_OVERLAP_LEN or len(cb) < self.DIALOG_MERGE_MIN_OVERLAP_LEN:
            shorter, longer = (ca, cb) if len(ca) <= len(cb) else (cb, ca)
            if shorter in longer:
                return True
        ratio = Levenshtein.ratio(ca, cb)
        if ratio >= self.DIALOG_MERGE_MIN_RATIO:
            return True
        if len(ca) >= self.DIALOG_MERGE_MIN_OVERLAP_LEN and len(cb) >= self.DIALOG_MERGE_MIN_OVERLAP_LEN:
            return ca in cb or cb in ca
        return False

    def _pick_majority_dialogue_body(self, bodies: Sequence[str]) -> str:
        from collections import Counter

        keyed = [(self._canonical_subtitle_plain_for_compare(b), b) for b in bodies]
        counter = Counter(c for c, _ in keyed)
        best_votes = max(counter.values())
        top_canons = sorted(
            (c for c, v in counter.items() if v == best_votes),
            key=lambda s: (-len(s), s),
        )
        best_canon = top_canons[0]
        candidates = [raw for canon, raw in keyed if canon == best_canon]
        return max(candidates, key=lambda s: len(s))

    def _merge_temporal_near_duplicate_events(self, events: List[Dict[str, str]]) -> List[Dict[str, str]]:
        if len(events) < 2:
            return events
        events_sorted = sorted(
            events,
            key=lambda e: (
                str(e["roi"]),
                self._parse_ass_time_to_seconds(e["start_time"]),
            ),
        )
        merged: List[Dict[str, str]] = []
        i = 0
        while i < len(events_sorted):
            grp = [events_sorted[i]]
            i += 1
            while i < len(events_sorted):
                prev = grp[-1]
                cur = events_sorted[i]
                if (
                    prev["roi"] != cur["roi"]
                    or prev["style"] != cur["style"]
                ):
                    break
                # Scene: allow small position jitter when merging.
                if prev["style"] == "Scene":
                    if not self._scene_tags_close(prev.get("tags", ""), cur.get("tags", "")):
                        break
                else:
                    if prev.get("tags", "") != cur.get("tags", ""):
                        break
                gap = self._dialogue_time_gap_seconds(prev["end_time"], cur["start_time"])
                if gap > self.DIALOG_MERGE_MAX_GAP_SEC or gap < -0.12:
                    break
                if not self._dialogue_bodies_similar(prev["body"], cur["body"]):
                    break
                grp.append(cur)
                i += 1
            if len(grp) == 1:
                merged.append(dict(grp[0]))
                continue
            body = self._pick_majority_dialogue_body([g["body"] for g in grp])
            tags = grp[0].get("tags", "")
            if grp[0].get("style") == "Scene":
                # pick a representative position (median) to stabilize pos jitter
                poss = [self._parse_pos_from_tags(g.get("tags", "")) for g in grp]
                poss = [p for p in poss if p is not None]
                if poss:
                    xs = sorted(p[0] for p in poss)
                    ys = sorted(p[1] for p in poss)
                    x = xs[len(xs) // 2]
                    y = ys[len(ys) // 2]
                    tags = f"{{\\an5\\pos({x},{y})}}"
            merged.append(
                {
                    "roi": grp[0]["roi"],
                    "start_time": grp[0]["start_time"],
                    "end_time": grp[-1]["end_time"],
                    "style": grp[0]["style"],
                    "tags": tags,
                    "body": body,
                }
            )
        if len(merged) < len(events):
            logger.info(
                _tr("OCRToASSOptimizer", "Merged {} fragmented ASS lines into {} dialogue events.").format(
                    len(events), len(merged)
                )
            )
        return merged

    def _llm_merge_fragmented_events(
        self,
        events: List[Dict[str, str]],
        cfg: SubtitlePolisherConfig,
        *,
        cancel_check: Callable[[], bool] = lambda: False,
        polish_progress_callback: Optional[Callable[[int, str], None]] = None,
    ) -> List[Dict[str, str]]:
        """
        Use LLM to merge fragmented events by choosing the most complete/accurate body
        and merging time range (min start, max end). This targets short "Scene" fragments.
        """
        if not events or cfg is None or not getattr(cfg, "fragment_merge_enabled", False):
            return events
        if not getattr(cfg, "api_key", ""):
            return events

        # Sort by ROI then start time.
        events_sorted = sorted(
            events,
            key=lambda e: (
                str(e.get("roi", "")),
                self._parse_ass_time_to_seconds(str(e.get("start_time", "0:00:00.00"))),
            ),
        )

        merged: List[Dict[str, str]] = []
        llm_fragment_calls = 0
        i = 0
        while i < len(events_sorted):
            if cancel_check():
                return events_sorted
            cur = events_sorted[i]
            # Only attempt for Scene by default (most jittery fragments).
            if str(cur.get("style", "")) != "Scene":
                merged.append(dict(cur))
                i += 1
                continue

            grp = [cur]
            i += 1
            while i < len(events_sorted):
                if cancel_check():
                    return events_sorted
                nxt = events_sorted[i]
                if nxt.get("roi") != cur.get("roi") or nxt.get("style") != cur.get("style"):
                    break
                if not self._scene_tags_close(str(cur.get("tags", "")), str(nxt.get("tags", ""))):
                    break
                try:
                    gap = self._dialogue_time_gap_seconds(str(grp[-1]["end_time"]), str(nxt["start_time"]))
                except Exception:
                    break
                # Keep a fairly tight window to avoid merging different sentences.
                if gap > 0.35 or gap < -0.12:
                    break
                # If next is pure noise, don't merge it in.
                if self._is_noise_body(str(nxt.get("body", ""))):
                    break
                grp.append(nxt)
                i += 1

            if len(grp) == 1:
                merged.append(dict(grp[0]))
                continue

            llm_fragment_calls += 1
            if polish_progress_callback:
                # Step 4 / LLM: reserve 92–93 for fragment-merge API calls
                pct = min(93, 91 + llm_fragment_calls)
                polish_progress_callback(
                    pct,
                    _tr(
                        "OCRToASSOptimizer",
                        "Step 4/4: DeepSeek merging fragmented Scene subtitles (call {})...",
                    ).format(llm_fragment_calls),
                )

            cands = [g.get("body", "") for g in grp]
            best = deepseek_merge_fragment_text(cfg, cands, cancel_check=cancel_check)
            if not best:
                # Fallback: choose the longest non-empty candidate.
                best = max((str(x) for x in cands), key=lambda s: len(s.strip()), default=str(grp[-1].get("body", "")))

            merged.append(
                {
                    "roi": grp[0]["roi"],
                    "start_time": grp[0]["start_time"],
                    "end_time": grp[-1]["end_time"],
                    "style": grp[0]["style"],
                    # Use a representative position (median) to stabilize pos jitter.
                    "tags": (self._merge_temporal_near_duplicate_events(grp)[0].get("tags") if grp else cur.get("tags", "")) or cur.get("tags", ""),
                    "body": best,
                }
            )

        if len(merged) < len(events):
            logger.info(
                _tr("OCRToASSOptimizer", "DeepSeek merged fragmented events: {} -> {}.").format(len(events), len(merged))
            )
        return merged

    def _merge_strategy_params_snapshot(self) -> Dict[str, float]:
        return {
            "merge_max_gap_sec": float(self.DIALOG_MERGE_MAX_GAP_SEC),
            "merge_min_ratio": float(self.DIALOG_MERGE_MIN_RATIO),
            "merge_min_overlap_len": float(int(self.DIALOG_MERGE_MIN_OVERLAP_LEN)),
        }

    def _apply_merge_strategy_params(self, params: Dict[str, float]) -> None:
        if "merge_max_gap_sec" in params:
            self.DIALOG_MERGE_MAX_GAP_SEC = float(params["merge_max_gap_sec"])
        if "merge_min_ratio" in params:
            self.DIALOG_MERGE_MIN_RATIO = float(params["merge_min_ratio"])
        if "merge_min_overlap_len" in params:
            self.DIALOG_MERGE_MIN_OVERLAP_LEN = int(params["merge_min_overlap_len"])

    def _load_and_organize_ocr_data(self, restored_data_generator: Generator) -> Dict[str, List[FrameData]]:
        roi_to_frame_data: Dict[str, Dict[int, FrameData]] = defaultdict(dict)

        for item in restored_data_generator:
            data, frame_num, roi_identifier = item[:3]
            frame_time_sec = float(item[3]) if len(item) >= 4 and item[3] is not None else 0.0
            try:
                if frame_num not in roi_to_frame_data[roi_identifier]:
                    roi_to_frame_data[roi_identifier][frame_num] = FrameData(frame_num=frame_num, time_sec=frame_time_sec)
                else:
                    # Keep the first non-zero timestamp if we see duplicates.
                    if roi_to_frame_data[roi_identifier][frame_num].time_sec <= 0 and frame_time_sec > 0:
                        roi_to_frame_data[roi_identifier][frame_num].time_sec = frame_time_sec
                
                texts = data.get('rec_texts', []); scores = data.get('rec_scores', [])
                boxes = data.get('rec_boxes', []); polygons = data.get('rec_polys', [])
                
                if not (len(texts) == len(scores) == len(boxes) == len(polygons)):
                    logger.warning(_tr("OCRToASSOptimizer", "Frame {} (ROI: {}) data list length mismatch, skipped.").format(frame_num, roi_identifier))
                    continue

                for text, score, box, poly in zip(texts, scores, boxes, polygons):
                    if score >= self.MIN_SCORE_THRESHOLD and text.strip():
                        polygon_points = [(int(p[0]), int(p[1])) for p in poly]
                        box_points = tuple(int(b) for b in box)
                        roi_to_frame_data[roi_identifier][frame_num].lines.append(
                            TextLine(text=text, score=score, box=box_points, polygon=polygon_points)
                        )
            except Exception as e:
                logger.warning(_tr("OCRToASSOptimizer", "Error processing in-memory data for frame {} (ROI: {}): {}").format(frame_num, roi_identifier, e))

        final_organized_data: Dict[str, List[FrameData]] = {}
        for roi_id, frame_map in roi_to_frame_data.items():
            if not frame_map: continue
            valid_frames = [fd for fd in frame_map.values() if not fd.is_empty]
            if valid_frames:
                valid_frames.sort(key=lambda f: f.frame_num)
                final_organized_data[roi_id] = valid_frames
        
        logger.info(_tr("OCRToASSOptimizer", "Successfully loaded and organized OCR data by {} ROIs.").format(len(final_organized_data)))
        return final_organized_data

    def _group_consecutive_frames(self, frames: List[FrameData]) -> List[SubtitleGroup]:
        if not frames: return []
        groups = []
        current_group = SubtitleGroup(start_frame=frames[0].frame_num, end_frame=frames[0].frame_num, lines=frames[0].lines)
        for i in range(1, len(frames)):
            prev_frame = frames[i-1]; curr_frame = frames[i]
            gap = curr_frame.frame_num - prev_frame.frame_num
            merged = False
            if gap == 1 and self._are_frames_similar(current_group, curr_frame):
                merged = True
            elif 1 < gap <= self.MERGE_BRIDGE_MAX_FRAMES and self._are_frames_similar_bridged(
                current_group, prev_frame, curr_frame
            ):
                merged = True
            if merged:
                current_group.end_frame = curr_frame.frame_num
            else:
                if current_group.duration_frames >= self.MIN_DURATION_FRAMES:
                    groups.append(current_group)
                current_group = SubtitleGroup(start_frame=curr_frame.frame_num, end_frame=curr_frame.frame_num, lines=curr_frame.lines)
        if current_group.duration_frames >= self.MIN_DURATION_FRAMES:
            groups.append(current_group)
        logger.debug(_tr("OCRToASSOptimizer", "Merged into {} subtitle groups.").format(len(groups)))
        return groups

    def _are_frames_similar(self, group: SubtitleGroup, frame2: FrameData) -> bool:
        frame1_lines = group.lines; frame2_lines = frame2.lines
        if len(frame1_lines) != len(frame2_lines): return False
        fp1 = tuple(sorted([l.text for l in frame1_lines])); fp2 = tuple(sorted([l.text for l in frame2_lines]))
        if fp1 == fp2:
            sorted_lines1 = sorted(frame1_lines, key=lambda l: l.box[1]); sorted_lines2 = sorted(frame2_lines, key=lambda l: l.box[1])
            for line1, line2 in zip(sorted_lines1, sorted_lines2):
                dist = math.hypot(line1.center[0] - line2.center[0], line1.center[1] - line2.center[1])
                if dist > self.MERGE_POS_TOLERANCE: return False
            return True
        text1 = "".join(fp1).replace(" ", ""); text2 = "".join(fp2).replace(" ", "")
        if not text1 or not text2: return False
        return Levenshtein.ratio(text1, text2) >= self.MERGE_TEXT_SIMILARITY

    def _lines_center_distance_ok(self, lines1: List[TextLine], lines2: List[TextLine]) -> bool:
        sorted_lines1 = sorted(lines1, key=lambda l: l.box[1])
        sorted_lines2 = sorted(lines2, key=lambda l: l.box[1])
        for line1, line2 in zip(sorted_lines1, sorted_lines2):
            dist = math.hypot(line1.center[0] - line2.center[0], line1.center[1] - line2.center[1])
            if dist > self.MERGE_POS_TOLERANCE:
                return False
        return True

    def _are_frames_similar_bridged(
        self, group: SubtitleGroup, prev_frame: FrameData, curr_frame: FrameData
    ) -> bool:
        """帧号有小间隔时：总字数相同、时间间隔短、文本与位置一致则合并。"""
        g_lines = group.lines
        c_lines = curr_frame.lines
        if len(g_lines) != len(c_lines):
            return False
        text_g = "".join(l.text for l in sorted(g_lines, key=lambda l: l.box[1])).replace(" ", "")
        text_c = "".join(l.text for l in sorted(c_lines, key=lambda l: l.box[1])).replace(" ", "")
        if not text_g or not text_c or len(text_g) != len(text_c):
            return False
        if prev_frame.time_sec > 0 and curr_frame.time_sec > 0:
            dt = curr_frame.time_sec - prev_frame.time_sec
            if dt > self.MERGE_BRIDGE_MAX_SEC:
                return False
        fp_g = tuple(sorted(l.text for l in g_lines))
        fp_c = tuple(sorted(l.text for l in c_lines))
        if fp_g == fp_c:
            return self._lines_center_distance_ok(g_lines, c_lines)
        if Levenshtein.ratio(text_g, text_c) < self.MERGE_TEXT_SIMILARITY:
            return False
        return self._lines_center_distance_ok(g_lines, c_lines)

    def _format_time(self, frame_num: int) -> str:
        total_seconds = frame_num / self.fps
        h = int(total_seconds / 3600); m = int((total_seconds % 3600) / 60)
        s = int(total_seconds % 60); cs = int((total_seconds - int(total_seconds)) * 100)
        return f"{h}:{m:02d}:{s:02d}.{cs:02d}"

    def _format_time_seconds(self, total_seconds: float) -> str:
        if total_seconds < 0:
            total_seconds = 0.0
        h = int(total_seconds / 3600)
        m = int((total_seconds % 3600) / 60)
        s = int(total_seconds % 60)
        cs = int(round((total_seconds - math.floor(total_seconds)) * 100))
        if cs >= 100:
            cs = 99
        return f"{h}:{m:02d}:{s:02d}.{cs:02d}"

    def _estimate_end_time(self, frames_in_group: List[FrameData]) -> float:
        if not frames_in_group:
            return 0.0
        times = [f.time_sec for f in frames_in_group if f.time_sec and f.time_sec > 0]
        if not times:
            # Fallback to fps-based timing.
            last_frame = frames_in_group[-1].frame_num
            frame_dt = (1.0 / self.fps) if self.fps and self.fps > 0 else 0.04
            return (last_frame / self.fps) + frame_dt if self.fps and self.fps > 0 else 0.0

        times_sorted = sorted(times)
        deltas = [b - a for a, b in zip(times_sorted, times_sorted[1:]) if (b - a) > 0]
        if deltas:
            deltas.sort()
            frame_dt = deltas[len(deltas) // 2]
        else:
            frame_dt = (1.0 / self.fps) if self.fps and self.fps > 0 else 0.04
        return max(times) + frame_dt

    def _detect_language(self, text: str) -> str:
        counts = defaultdict(int)
        text_for_detection = re.sub(r'[ ,.!?\'"(){}\[\]\d-]', '', text)
        if not text_for_detection: return 'EN'

        for char in text_for_detection:
            code = ord(char)
            if 0x4E00 <= code <= 0x9FFF: counts['CH'] += 1
            elif 0x3040 <= code <= 0x309F or 0x30A0 <= code <= 0x30FF: counts['JP'] += 1
            elif 0xAC00 <= code <= 0xD7A3: counts['KO'] += 1
            elif 0x0400 <= code <= 0x04FF: counts['RU'] += 1
            elif 0x0020 <= code <= 0x007E: counts['EN'] += 1
        
        if counts['JP'] > 0: return 'JP'
        if not counts: return 'EN'
        return max(counts, key=counts.get)

    def _determine_style_and_position(self, group: SubtitleGroup) -> List[Dict]:
        avg_box = group.get_avg_box()
        if not avg_box: return []
        avg_y_center = (avg_box[1] + avg_box[3]) / 2
        if avg_y_center > self.height * self.VIDEO_BOTTOM_AREA: location_type = 'BOTTOM'
        elif avg_y_center < self.height * self.VIDEO_TOP_AREA: location_type = 'TOP'
        else: location_type = 'SCENE'
        dialogue_lines = []; sorted_lines = sorted(group.lines, key=lambda line: line.box[1])
        
        if location_type == 'BOTTOM':
            raw_text_for_detection = " ".join([line.text for line in sorted_lines])
            lang = self._detect_language(raw_text_for_detection)

            if lang in ['CH', 'JP', 'KO', 'RU']:
                style_name = lang
            else:
                style_name = 'Default'

            full_text = "\\n".join([line.text for line in sorted_lines])
            dialogue_lines.append({'style': style_name, 'text': full_text, 'tags': ''})

        elif location_type == 'TOP':
            full_text = "\\n".join([line.text for line in sorted_lines])
            dialogue_lines.append({'style': 'Top', 'text': full_text, 'tags': '{\\an8}'})
        elif location_type == 'SCENE':
            for line in sorted_lines:
                x = int(line.center[0]); y = int(line.center[1])
                tags = f"{{\\an5\\pos({x},{y})}}"
                dialogue_lines.append({'style': 'Scene', 'text': line.text, 'tags': tags})
        return dialogue_lines

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

    def _classify_and_filter_text_lines(
        self, organized_data: Dict[str, List[FrameData]]
    ) -> Dict[str, List[FrameData]]:
        """Classify each TextLine as OVERLAY/SCENE/UNKNOWN and filter by preset.

        This is the integration point for the text source classifier into
        the subtitle generation pipeline.
        """
        from core.text_source_classifier import (
            TextSourceClassifier,
            TextSource,
            ClassificationResult,
            create_classifier_from_config,
        )
        from core.classification_features import (
            TextRegionFeatures,
            extract_visual_features,
            extract_semantic_features,
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

        for roi_id, frame_list in organized_data.items():
            for frame_data in frame_list:
                for text_line in frame_data.lines:
                    total_before += 1

                    # Build feature vector
                    features = self._build_text_region_features(
                        text_line, frame_data
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

        # Filter based on preset
        for roi_id, frame_list in organized_data.items():
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

        return organized_data

    def _build_text_region_features(
        self, text_line: "TextLine", frame_data: "FrameData"
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

        # Semantic
        sem_feats = extract_semantic_features(text_line.text)

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
            # Temporal (basic from frame data)
            first_seen_frame=frame_data.frame_num,
            last_seen_frame=frame_data.frame_num,
            duration_sec=0.0,
            appearance_count=1,
            is_stationary=True,
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

    def _get_ass_header(self) -> str:
        if self.template_path and self.template_path.exists():
            try:
                with open(self.template_path, 'r', encoding='utf-8') as f:
                    content = f.read()
                content = re.sub(r'(?i)^PlayResX:.*', f'PlayResX: {self.width}', content, flags=re.MULTILINE)
                content = re.sub(r'(?i)^PlayResY:.*', f'PlayResY: {self.height}', content, flags=re.MULTILINE)
                if '[Events]' in content:
                    header = content.split('[Events]')[0]
                    return header.strip() + '\n\n[Events]\nFormat: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n'
                else:
                    logger.warning(_tr("OCRToASSOptimizer", "No '[Events]' tag found in template file. Events will be appended at the end of the file."))
                    return content.strip() + '\n\n[Events]\nFormat: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n'
            except Exception as e:
                logger.error(_tr("OCRToASSOptimizer", "Failed to read template file {}: {}. Using default styles.").format(self.template_path, e))

        return f"""[Script Info]
Title: {self.video_path.stem} - Generated by Subtitle-OCR
ScriptType: v4.00+
WrapStyle: 0
PlayResX: {self.width}
PlayResY: {self.height}
ScaledBorderAndShadow: yes
YCbCr Matrix: TV.709

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,思源黑体 CN,{(self.height*0.06):.0f},&H00FFFFFF,&H000000FF,&H00000000,&H00000000,0,0,0,0,100,100,0,0,1,2,1,2,10,10,10,1
Style: CH,思源黑体 CN,{(self.height*0.06):.0f},&H00FFFFFF,&H000000FF,&H00000000,&H00000000,0,0,0,0,100,100,0,0,1,2,1,2,10,10,10,1
Style: JP,源ノ角ゴシック JP,{(self.height*0.06):.0f},&H00FFFFFF,&H000000FF,&H00000000,&H00000000,0,0,0,0,100,100,0,0,1,2,1,2,10,10,10,1
Style: KO,Malgun Gothic,{(self.height*0.06):.0f},&H00FFFFFF,&H000000FF,&H00000000,&H00000000,0,0,0,0,100,100,0,0,1,2,1,2,10,10,10,1
Style: RU,Arial,{(self.height*0.06):.0f},&H00FFFFFF,&H000000FF,&H00000000,&H00000000,0,0,0,0,100,100,0,0,1,2,1,2,10,10,10,1
Style: Top,思源黑体 CN,{(self.height*0.05):.0f},&H00FFFFFF,&H000000FF,&H00000000,&H00000000,0,0,0,0,100,100,0,0,1,2,1,8,10,10,10,1
Style: Scene,思源黑体 CN,{(self.height*0.04):.0f},&H00FFFFFF,&H000000FF,&H00000000,&H00000000,0,0,0,0,100,100,0,0,1,1,0,5,10,10,10,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""

    def convert_from_memory(
        self,
        restored_data_generator: Generator,
        *,
        polish_progress_callback: Optional[Callable[[int, str], None]] = None,
        polish_cancel_check: Optional[Callable[[], bool]] = None,
    ):
        logger.info(_tr("OCRToASSOptimizer", "--- Starting conversion from in-memory data to ASS subtitles ---"))
        try:
            organized_data = self._load_and_organize_ocr_data(restored_data_generator)

            # ── New: Text source classification + filtering ──
            if self.source_filter_config and self.source_filter_config.get("enabled"):
                organized_data = self._classify_and_filter_text_lines(organized_data)

            if not organized_data:
                logger.warning(_tr("OCRToASSOptimizer", "No valid OCR data found, an empty ASS file will be generated."))
                with open(self.output_path, 'w', encoding='utf-8-sig') as f:
                    f.write(self._get_ass_header())
                return

            subtitle_events: List[Dict[str, str]] = []
            for roi_id, frame_list in organized_data.items():
                logger.info(_tr("OCRToASSOptimizer", "Processing ROI: {}, containing {} valid frames.").format(roi_id, len(frame_list)))
                groups = self._group_consecutive_frames(frame_list)
                groups = self._filter_groups_by_roi_profile(groups)
                logger.info(_tr("OCRToASSOptimizer", "ROI: {} generated {} subtitle groups.").format(roi_id, len(groups)))

                for group in groups:
                    # Prefer real timestamps when available.
                    group_frames = [f for f in frame_list if group.start_frame <= f.frame_num <= group.end_frame]
                    start_sec = next((f.time_sec for f in group_frames if f.time_sec and f.time_sec > 0), 0.0)
                    if start_sec > 0:
                        end_sec = self._estimate_end_time(group_frames)
                        start_time = self._format_time_seconds(start_sec)
                        end_time = self._format_time_seconds(end_sec)
                    else:
                        start_time = self._format_time(group.start_frame)
                        end_time = self._format_time(group.end_frame + 1)
                    styled_lines = self._determine_style_and_position(group)
                    for line_info in styled_lines:
                        subtitle_events.append(
                            {
                                "roi": str(roi_id),
                                "start_time": start_time,
                                "end_time": end_time,
                                "style": line_info["style"],
                                "tags": line_info["tags"],
                                "body": line_info["text"],
                            }
                        )

            subtitle_events_pre_merge = [dict(e) for e in subtitle_events]
            subtitle_events_pre_merge = self._filter_events(subtitle_events_pre_merge)
            subtitle_events = self._merge_temporal_near_duplicate_events(subtitle_events_pre_merge)

            if self.subtitle_polisher is not None and polish_progress_callback:
                polish_progress_callback(
                    91,
                    _tr(
                        "OCRToASSOptimizer",
                        "Step 4/4: Starting ASS generation and DeepSeek post-processing...",
                    ),
                )

            if subtitle_events and self.subtitle_polisher is not None and getattr(
                self.subtitle_polisher, "fragment_merge_enabled", False
            ):
                subtitle_events = self._llm_merge_fragmented_events(
                    subtitle_events,
                    self.subtitle_polisher,
                    cancel_check=polish_cancel_check if polish_cancel_check else (lambda: False),
                    polish_progress_callback=polish_progress_callback,
                )

            if subtitle_events and self.subtitle_polisher is not None and getattr(
                self.subtitle_polisher, "strategy_review_enabled", False
            ):
                max_rounds = max(
                    1,
                    min(5, int(getattr(self.subtitle_polisher, "strategy_max_iterations", 3))),
                )
                current_params = self._merge_strategy_params_snapshot()

                for rnd in range(max_rounds):
                    if polish_cancel_check and polish_cancel_check():
                        break

                    if polish_progress_callback:
                        polish_progress_callback(
                            min(96, 93 + rnd),
                            _tr(
                                "OCRToASSOptimizer",
                                "Step 4/4: DeepSeek reviewing merge strategy (round {}/{})..."
                            ).format(rnd + 1, max_rounds),
                        )

                    sug = deepseek_suggest_merge_params(
                        self.subtitle_polisher,
                        subtitle_events_pre_merge,
                        subtitle_events,
                        current_params,
                        cancel_check=polish_cancel_check if polish_cancel_check else lambda: False,
                    )

                    if sug is None:
                        logger.info(
                            _tr("OCRToASSOptimizer", "DeepSeek strategy review skipped or failed; keeping merge parameters unchanged.")
                        )
                        break

                    patch, rationale = sug
                    if rationale:
                        logger.info(_tr("OCRToASSOptimizer", "DeepSeek strategy note: {}").format(rationale))

                    if not patch:
                        break

                    merged_params = dict(current_params)
                    merged_params.update(patch)
                    if "merge_min_overlap_len" in merged_params:
                        merged_params["merge_min_overlap_len"] = float(
                            int(merged_params["merge_min_overlap_len"])
                        )

                    if params_close(merged_params, current_params):
                        logger.info(_tr("OCRToASSOptimizer", "DeepSeek merge parameters converged."))
                        break

                    self._apply_merge_strategy_params(merged_params)
                    current_params = self._merge_strategy_params_snapshot()
                    subtitle_events = self._merge_temporal_near_duplicate_events(
                        [dict(e) for e in subtitle_events_pre_merge]
                    )

                    logger.info(
                        _tr(
                            "OCRToASSOptimizer",
                            "Re-merged subtitles with tuned parameters (gap {:.2f}s ratio {:.3f} overlap {})."
                        ).format(
                            current_params["merge_max_gap_sec"],
                            current_params["merge_min_ratio"],
                            int(current_params["merge_min_overlap_len"]),
                        )
                    )

            if (
                subtitle_events
                and self.subtitle_polisher is not None
                and getattr(self.subtitle_polisher, "text_polish_enabled", True)
            ):
                bodies = [ev["body"] for ev in subtitle_events]

                def _on_batch(idx: int, total: int) -> None:
                    if polish_progress_callback:
                        pct = 96 + max(1, min(3, int(3 * idx / max(total, 1))))
                        polish_progress_callback(
                            min(99, pct),
                            _tr(
                                "OCRToASSOptimizer",
                                "Step 4/4: DeepSeek polishing subtitles ({}/{} batches)...",
                            ).format(idx, total),
                        )

                polished = polish_subtitle_texts(
                    bodies,
                    self.subtitle_polisher,
                    cancel_check=polish_cancel_check if polish_cancel_check else lambda: False,
                    on_batch_done=_on_batch,
                )
                if len(polished) != len(subtitle_events):
                    logger.warning(
                        _tr("OCRToASSOptimizer", "Polish output length mismatch, using original subtitles.")
                    )
                else:
                    for ev, nt in zip(subtitle_events, polished):
                        ev["body"] = nt
                    logger.info(_tr("OCRToASSOptimizer", "DeepSeek subtitle polishing applied."))

            all_dialogue_entries = []
            for ev in subtitle_events:
                text = ev["tags"] + ev["body"]
                entry = f"Dialogue: 0,{ev['start_time']},{ev['end_time']},{ev['style']},,0,0,0,,{text}"
                all_dialogue_entries.append(entry)

            if not all_dialogue_entries:
                logger.warning(_tr("OCRToASSOptimizer", "No valid subtitle groups formed for any ROI, an empty ASS file will be generated."))
                with open(self.output_path, 'w', encoding='utf-8-sig') as f:
                    f.write(self._get_ass_header())
                return

            all_dialogue_entries.sort(key=lambda x: x.split(',')[1])

            header_content = self._get_ass_header()
            final_content = header_content + "\n".join(all_dialogue_entries)
            
            with open(self.output_path, 'w', encoding='utf-8-sig') as f:
                f.write(final_content)

            logger.info(_tr("OCRToASSOptimizer", "--- Conversion successful ---"))
            logger.info(_tr("OCRToASSOptimizer", "ASS subtitle file saved to: {}").format(self.output_path))

        except Exception as e:
            logger.error(_tr("OCRToASSOptimizer", "--- Conversion failed ---"))
            logger.error(_tr("OCRToASSOptimizer", "Error: {}").format(e), exc_info=True)
            raise
