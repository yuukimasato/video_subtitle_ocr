# core/subtitle_generator.py
import os
import json
import cv2
import math
import re
import Levenshtein
import logging
from pathlib import Path
from typing import List, Dict, Tuple, Optional, Generator
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
    VIDEO_BOTTOM_AREA = 0.75
    VIDEO_TOP_AREA = 0.15

    def __init__(self, video_path: str, output_path: str, fps: float, width: int, height: int, template_path: Optional[str] = None):
        self.video_path = Path(video_path)
        self.output_path = Path(output_path)
        self.fps = fps
        self.width = width
        self.height = height
        self.template_path = Path(template_path) if template_path else None
        logger.info(_tr("OCRToASSOptimizer", "Subtitle generator initialized: {}x{} @ {:.2f} FPS").format(self.width, self.height, self.fps))
        if self.template_path and self.template_path.exists():
            logger.info(_tr("OCRToASSOptimizer", "Using style template: {}").format(self.template_path))
        else:
            logger.info(_tr("OCRToASSOptimizer", "No style template used, generating default styles."))

    def _load_and_organize_ocr_data(self, restored_data_generator: Generator) -> Dict[str, List[FrameData]]:
        roi_to_frame_data: Dict[str, Dict[int, FrameData]] = defaultdict(dict)

        for data, frame_num, roi_identifier in restored_data_generator:
            try:
                if frame_num not in roi_to_frame_data[roi_identifier]:
                    roi_to_frame_data[roi_identifier][frame_num] = FrameData(frame_num=frame_num)
                
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
            if curr_frame.frame_num == prev_frame.frame_num + 1 and self._are_frames_similar(current_group, curr_frame):
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

    def _format_time(self, frame_num: int) -> str:
        total_seconds = frame_num / self.fps
        h = int(total_seconds / 3600); m = int((total_seconds % 3600) / 60)
        s = int(total_seconds % 60); cs = int((total_seconds - int(total_seconds)) * 100)
        return f"{h}:{m:02d}:{s:02d}.{cs:02d}"

    def _calculate_rotation(self, polygon: List[Tuple[int, int]]) -> float:
        if len(polygon) < 2: return 0.0
        max_length_sq = 0; best_angle = 0.0
        for i in range(len(polygon)):
            for j in range(i + 1, len(polygon)):
                p1 = polygon[i]; p2 = polygon[j]; dx = p2[0] - p1[0]; dy = p2[1] - p1[1]
                length_sq = dx*dx + dy*dy
                if length_sq > max_length_sq:
                    max_length_sq = length_sq
                    raw_angle = math.degrees(math.atan2(dy, dx)); angle = raw_angle % 180
                    if angle > 90: angle -= 180
                    elif angle < -90: angle += 180
                    best_angle = angle
        return best_angle

    def _determine_style_and_position(self, group: SubtitleGroup) -> List[Dict]:
        avg_box = group.get_avg_box()
        if not avg_box: return []
        avg_y_center = (avg_box[1] + avg_box[3]) / 2
        if avg_y_center > self.height * self.VIDEO_BOTTOM_AREA: location_type = 'BOTTOM'
        elif avg_y_center < self.height * self.VIDEO_TOP_AREA: location_type = 'TOP'
        else: location_type = 'SCENE'
        dialogue_lines = []; sorted_lines = sorted(group.lines, key=lambda line: line.box[1])
        
        if location_type == 'BOTTOM':
            full_text = "\\n".join([line.text for line in sorted_lines])
            dialogue_lines.append({'style': 'Default', 'text': full_text, 'tags': ''})
        elif location_type == 'TOP':
            full_text = "\\n".join([line.text for line in sorted_lines])
            dialogue_lines.append({'style': 'Top', 'text': full_text, 'tags': '{\\an8}'})
        elif location_type == 'SCENE':
            for line in sorted_lines:
                x = int(line.center[0]); y = int(line.center[1])
                font_height = int(line.bounding_height * 0.8); font_size = max(12, min(font_height, 72))
                rotation = self._calculate_rotation(line.polygon)
                tags = f"{{\\an5\\pos({x},{y})\\fs{font_size}"
                if abs(rotation) > 1: tags += f"\\frz{-rotation}"
                tags += "}"
                dialogue_lines.append({'style': 'Scene', 'text': line.text, 'tags': tags})
        return dialogue_lines

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
Title: {self.video_path.stem}
ScriptType: v4.00+
WrapStyle: 0
PlayResX: {self.width}
PlayResY: {self.height}
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,Source Han Sans Medium,42,&H00FFFFFF,&H00FFFFFF,&H00000000,&H00000000,0,0,0,0,100,100,0,0,1,0.9,0.4,2,4,4,32,1
Style: Top,Source Han Sans Medium,23,&H00FFFFFF,&H000000FF,&H00000000,&H00000000,0,0,0,0,100,100,0,0,1,0.222222,0.222222,8,4,4,5,1
Style: Scene,Source Han Sans Medium,19,&H009D4D01,&H000000FF,&H00E6FBFF,&H00FFFFFF,0,0,0,0,100,100,0,0,1,0,0,5,4,4,4,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""

    def convert_from_memory(self, restored_data_generator: Generator):
        logger.info(_tr("OCRToASSOptimizer", "--- Starting conversion from in-memory data to ASS subtitles ---"))
        try:
            organized_data = self._load_and_organize_ocr_data(restored_data_generator)
            
            if not organized_data:
                logger.warning(_tr("OCRToASSOptimizer", "No valid OCR data found, an empty ASS file will be generated."))
                with open(self.output_path, 'w', encoding='utf-8') as f:
                    f.write(self._get_ass_header())
                return

            all_dialogue_entries = []
            for roi_id, frame_list in organized_data.items():
                logger.info(_tr("OCRToASSOptimizer", "Processing ROI: {}, containing {} valid frames.").format(roi_id, len(frame_list)))
                groups = self._group_consecutive_frames(frame_list)
                logger.info(_tr("OCRToASSOptimizer", "ROI: {} generated {} subtitle groups.").format(roi_id, len(groups)))

                for group in groups:
                    start_time = self._format_time(group.start_frame)
                    end_time = self._format_time(group.end_frame + 1) 
                    styled_lines = self._determine_style_and_position(group)
                    for line_info in styled_lines:
                        text = line_info['tags'] + line_info['text']
                        entry = f"Dialogue: 0,{start_time},{end_time},{line_info['style']},,0,0,0,,{text}"
                        all_dialogue_entries.append(entry)

            if not all_dialogue_entries:
                logger.warning(_tr("OCRToASSOptimizer", "No valid subtitle groups formed for any ROI, an empty ASS file will be generated."))
                with open(self.output_path, 'w', encoding='utf-8') as f:
                    f.write(self._get_ass_header())
                return

            all_dialogue_entries.sort(key=lambda x: x.split(',')[1])

            header_content = self._get_ass_header()
            final_content = header_content + "\n".join(all_dialogue_entries)
            
            with open(self.output_path, 'w', encoding='utf-8') as f:
                f.write(final_content)

            logger.info(_tr("OCRToASSOptimizer", "--- Conversion successful ---"))
            logger.info(_tr("OCRToASSOptimizer", "ASS subtitle file saved to: {}").format(self.output_path))

        except Exception as e:
            logger.error(_tr("OCRToASSOptimizer", "--- Conversion failed ---"))
            logger.error(_tr("OCRToASSOptimizer", "Error: {}").format(e), exc_info=True)
            raise
