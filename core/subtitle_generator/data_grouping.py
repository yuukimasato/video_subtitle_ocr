# core/subtitle_generator/data_grouping.py
import logging
import math
import re
import Levenshtein
from collections import defaultdict
from typing import Dict, Generator, List

from PySide6.QtCore import QCoreApplication

from .models import FrameData, SubtitleGroup, TextLine

logger = logging.getLogger("core.subtitle_generator")

_tr = QCoreApplication.translate

# OCR 在字幕淡入/淡出或背景干扰下会读丢/读错尾部的省略号，产生 "好熱" / "好熱…"
# / "好熱·." 一类只有句尾标点差异的抖动读法。比较文本时先把这些尾部标点摘掉，
# 避免帧分组与事件合并因 1 个标点之差而碎裂。
_FLICKER_PUNCT_TAIL_RE = re.compile(r"[·・.。…﹒∼~\-–—\s]+$")


def strip_flicker_punctuation(text: str) -> str:
    return _FLICKER_PUNCT_TAIL_RE.sub("", str(text or ""))

class _DataGroupingMixin:
    """OCR data loading / frame grouping helpers moved from core/subtitle_generator.py (L464-L583)."""

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
            for frame_data in frame_map.values():
                # 同一行文本被 OCR 拆成多个框（中间有间隙/空格）时先并回一行，
                # 否则帧分组按行数不一致碎裂、生成的事件体里出现假 \N 断行。
                frame_data.lines = self._merge_same_row_lines(frame_data.lines)
                # 底部/顶部字幕带上的单字/纯 ASCII 噪声读法（幻影框）直接剔除。
                frame_data.lines = [
                    line for line in frame_data.lines
                    if not self._is_band_junk_line(line)
                ]
            valid_frames = [fd for fd in frame_map.values() if not fd.is_empty]
            if valid_frames:
                valid_frames.sort(key=lambda f: f.frame_num)
                final_organized_data[roi_id] = valid_frames
        
        logger.info(_tr("OCRToASSOptimizer", "Successfully loaded and organized OCR data by {} ROIs.").format(len(final_organized_data)))
        return final_organized_data

    def _line_in_dialogue_band(self, line: TextLine) -> bool:
        """行中心落在底部/顶部字幕带内（对白区，应用噪声行过滤/同行合并）。"""
        y_center = (line.box[1] + line.box[3]) / 2
        return (
            y_center > self.height * self.VIDEO_BOTTOM_AREA
            or y_center < self.height * self.VIDEO_TOP_AREA
        )

    def _is_band_junk_line(self, line: TextLine) -> bool:
        """字幕带内的幻影行：单字（除常用标点）或 ≤2 位纯 ASCII 字母数字。

        这类读法几乎都是淡入淡出边缘/背景纹理产生的噪声框；真实单字对白
        （如「嗯。」）通常带标点成对出现，不受影响。场景区（画面文字）不做
        此过滤，保留招牌/插字内容。
        """
        if not self._line_in_dialogue_band(line):
            return False
        z = re.sub(r"\s+", "", str(line.text))
        if not z:
            return True
        if len(z) == 1 and z not in "，。！？．.!?:：…、,·・~～":
            return True
        if len(z) <= 2 and re.fullmatch(r"[0-9A-Za-z]+", z):
            return True
        return False

    def _same_text_row(self, a: TextLine, b: TextLine) -> bool:
        ay0, ay1 = a.box[1], a.box[3]
        by0, by1 = b.box[1], b.box[3]
        inter = min(ay1, by1) - max(ay0, by0)
        min_h = max(1, min(ay1 - ay0, by1 - by0))
        return (inter / min_h) >= 0.55

    def _merge_same_row_lines(self, lines: List[TextLine]) -> List[TextLine]:
        """把垂直方向明显重叠（同一行）的相邻框合并为一行。

        OCR 常把含空格/间隙的一行对白拆成两个框（左右并排、y 范围相同），按
        y 排序再用 ASS 硬断行拼接会渲染成假两行、顺序也不稳定；合并时按 x
        顺序拼接，间隙达到约 1/8 字高时补一个空格。只作用于底部/顶部对白带
        内的行，场景区的画面文字保留各自位置。
        """
        if len(lines) <= 1:
            return lines
        merged: List[TextLine] = []
        for line in sorted(lines, key=lambda l: (l.box[1], l.box[0])):
            if (
                merged
                and self._line_in_dialogue_band(merged[-1])
                and self._line_in_dialogue_band(line)
                and self._same_text_row(merged[-1], line)
            ):
                merged[-1] = self._concat_row_lines(merged[-1], line)
            else:
                merged.append(line)
        return merged

    def _concat_row_lines(self, a: TextLine, b: TextLine) -> TextLine:
        left, right = (a, b) if a.center[0] <= b.center[0] else (b, a)
        gap = right.box[0] - left.box[2]
        ref_h = max(1, min(a.box[3] - a.box[1], b.box[3] - b.box[1]))
        # 外框含描边/背景余量，真实字间隙被低估：约 1/8 字高的净空即视为
        # 原文有空格（如「什麼意思 我是哪種人」），更小的只是拆框缝隙。
        sep = " " if gap > 0.12 * ref_h else ""
        box = (
            min(a.box[0], b.box[0]),
            min(a.box[1], b.box[1]),
            max(a.box[2], b.box[2]),
            max(a.box[3], b.box[3]),
        )
        polygon = [
            (box[0], box[1]), (box[2], box[1]),
            (box[2], box[3]), (box[0], box[3]),
        ]
        return TextLine(
            text=left.text + sep + right.text,
            score=min(a.score, b.score),
            box=box,
            polygon=polygon,
            source_label=a.source_label or b.source_label,
            source_confidence=min(a.source_confidence, b.source_confidence)
            if a.source_confidence and b.source_confidence
            else (a.source_confidence or b.source_confidence),
        )

    def _group_consecutive_frames(self, frames: List[FrameData]) -> List[SubtitleGroup]:
        if not frames: return []
        groups = []
        current_group = SubtitleGroup(
            start_frame=frames[0].frame_num, end_frame=frames[0].frame_num,
            lines=frames[0].lines, frames=[frames[0]],
        )
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
                current_group.frames.append(curr_frame)
            else:
                groups.append(current_group)
                current_group = SubtitleGroup(
                    start_frame=curr_frame.frame_num, end_frame=curr_frame.frame_num,
                    lines=curr_frame.lines, frames=[curr_frame],
                )
        groups.append(current_group)
        # 短组（淡入第一帧/淡出最后一帧的孤立读法）先尝试并入相邻同文本组，
        # 剩下不相似的短组才按最短帧数丢弃，避免字幕头尾被截掉 1-2 帧。
        groups = self._absorb_short_groups(groups)
        groups = [g for g in groups if g.duration_frames >= self.MIN_DURATION_FRAMES]
        logger.debug(_tr("OCRToASSOptimizer", "Merged into {} subtitle groups.").format(len(groups)))
        return groups

    def _normalized_group_text(self, group: SubtitleGroup) -> str:
        return strip_flicker_punctuation(
            "".join(l.text for l in sorted(group.lines, key=lambda l: l.box[1]))
        ).replace(" ", "")

    def _frame_gap_between_groups(self, a: SubtitleGroup, b: SubtitleGroup) -> int:
        if a.start_frame > b.end_frame:
            return a.start_frame - b.end_frame
        if b.start_frame > a.end_frame:
            return b.start_frame - a.end_frame
        return 0

    def _absorb_short_groups(self, groups: List[SubtitleGroup]) -> List[SubtitleGroup]:
        """把与相邻稳定组文本一致（含标点抖动/子串）的短组并入对方。

        只向"已达标"的组吸收，且要求帧号相邻（间隔 ≤2 帧），防止把真正的
        闪烁噪声并入字幕，也不改变任何已有组的文本内容。
        """
        if len(groups) < 2:
            return groups
        min_frames = self.MIN_DURATION_FRAMES
        changed = True
        merged_groups = list(groups)
        while changed:
            changed = False
            for idx, short in enumerate(merged_groups):
                if short.duration_frames >= min_frames:
                    continue
                best_j, best_sim = None, 0.0
                for j, other in enumerate(merged_groups):
                    if j == idx or other.duration_frames < min_frames:
                        continue
                    if self._frame_gap_between_groups(short, other) > 2:
                        continue
                    ta = self._normalized_group_text(short)
                    tb = self._normalized_group_text(other)
                    if not ta or not tb:
                        continue
                    sim = Levenshtein.ratio(ta, tb)
                    if sim > best_sim:
                        best_sim, best_j = sim, j
                if best_j is None or best_sim < self.MERGE_TEXT_SIMILARITY:
                    continue
                other = merged_groups[best_j]
                other.start_frame = min(other.start_frame, short.start_frame)
                other.end_frame = max(other.end_frame, short.end_frame)
                other.frames = sorted(other.frames + short.frames, key=lambda f: f.frame_num)
                merged_groups.pop(idx)
                changed = True
                break
        return merged_groups

    def _select_representative_lines(self, group: SubtitleGroup) -> List[TextLine]:
        """按组内全部帧投票选出代表性文本行，替代“首帧行”作为事件体来源。

        首帧可能恰好读丢一行（双行字幕只剩单行）或混入幻影行；对组内帧按
        行数取众数、再对每个行槽做多数投票（票数并列取更长者，保留省略号等
        完整读法），得到稳定且完整的行集。行对象仍取自真实帧，保证坐标有效。
        """
        from collections import Counter

        per_frame: List[List[TextLine]] = []
        for frame in group.frames or []:
            if frame.lines:
                per_frame.append(sorted(frame.lines, key=lambda l: l.box[1]))
        if not per_frame:
            return group.lines
        count_votes = Counter(len(ls) for ls in per_frame)
        best_n = max(count_votes, key=lambda n: (count_votes[n], n))
        cands = [ls for ls in per_frame if len(ls) == best_n]
        out: List[TextLine] = []
        for slot in range(best_n):
            votes = Counter(ls[slot].text for ls in cands)
            best_text = sorted(votes.items(), key=lambda kv: (-kv[1], -len(kv[0])))[0][0]
            src = next((ls[slot] for ls in cands if ls[slot].text == best_text), cands[0][slot])
            out.append(src)
        return out

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
        text1 = strip_flicker_punctuation("".join(fp1).replace(" ", ""))
        text2 = strip_flicker_punctuation("".join(fp2).replace(" ", ""))
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
        text_g = strip_flicker_punctuation(
            "".join(l.text for l in sorted(g_lines, key=lambda l: l.box[1]))
        ).replace(" ", "")
        text_c = strip_flicker_punctuation(
            "".join(l.text for l in sorted(c_lines, key=lambda l: l.box[1]))
        ).replace(" ", "")
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
