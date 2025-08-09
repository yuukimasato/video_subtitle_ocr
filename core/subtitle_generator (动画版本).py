# core/subtitle_generator.py
import math
import re
import logging
import numpy as np
import cv2
import Levenshtein
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
        return (self.box[0] + self.box[2]) / 2, (self.box[1] + self.box[3]) / 2

    @property
    def area(self) -> float:
        return (self.box[2] - self.box[0]) * (self.box[3] - self.box[1])

    @property
    def rotation(self) -> float:
        if len(self.polygon) < 3: return 0.0
        rect = cv2.minAreaRect(np.array(self.polygon, dtype=np.float32))
        angle = rect[2]
        if rect[1][0] < rect[1][1]:
            angle = 90 + angle
        return angle if angle <= 45 else angle - 90

@dataclass
class FrameData:
    frame_num: int
    lines: List[TextLine] = field(default_factory=list)

    @property
    def text_fingerprint(self) -> str:
        sorted_lines = sorted(self.lines, key=lambda l: l.center[1])
        return "".join([line.text.strip() for line in sorted_lines])

    @property
    def is_empty(self) -> bool:
        return not self.lines

@dataclass
class SubtitleGroup:
    start_frame: int
    end_frame: int
    frames: List[FrameData] = field(default_factory=list)

    @property
    def duration_frames(self) -> int:
        return self.end_frame - self.start_frame + 1

    @property
    def representative_text(self) -> str:
        text_counts = defaultdict(int)
        for frame in self.frames:
            fp = frame.text_fingerprint
            if fp: 
                text_counts[fp] += 1
        if not text_counts: return ""
        return max(text_counts, key=text_counts.get)


class OCRToASSOptimizer:
    MIN_SCORE_THRESHOLD = 0.6
    MIN_DURATION_FRAMES = 4
    MERGE_TEXT_SIMILARITY = 0.9
    MERGE_POS_TOLERANCE_RATIO = 0.05
    BOTTOM_AREA_RATIO = 0.75
    TOP_AREA_RATIO = 0.15

    def __init__(self, video_path: str, output_path: str, fps: float, width: int, height: int, template_path: Optional[str] = None):
        self.video_path = Path(video_path)
        self.output_path = Path(output_path)
        self.fps = fps
        self.width = width
        self.height = height
        self.template_path = Path(template_path) if template_path else None
        self.pos_tolerance = self.height * self.MERGE_POS_TOLERANCE_RATIO
        logger.info(_tr("OCRToASSOptimizer", "Subtitle generator initialized: {}x{} @ {:.2f} FPS").format(self.width, self.height, self.fps))
        if self.template_path and self.template_path.exists():
            logger.info(_tr("OCRToASSOptimizer", "Using style template: {}").format(self.template_path))
        else:
            logger.info(_tr("OCRToASSOptimizer", "No style template used, generating a rich set of default styles."))

    def convert_from_memory(self, restored_data_generator: Generator):
        logger.info(_tr("OCRToASSOptimizer", "--- Starting conversion from in-memory data to ASS subtitles ---"))
        organized_data = self._load_and_organize_ocr_data(restored_data_generator)
        all_dialogues = []

        for roi_id, frames in organized_data.items():
            logger.info(_tr("OCRToASSOptimizer", "Processing ROI: {}, containing {} valid frames.").format(roi_id, len(frames)))
            groups = self._group_consecutive_frames(frames)
            logger.info(_tr("OCRToASSOptimizer", "ROI: {} generated {} subtitle groups.").format(roi_id, len(groups)))
            
            for group in groups:
                start_time = self._format_time(group.start_frame)
                end_time = self._format_time(group.end_frame + 1)
                
                dialogue_lines = self._analyze_group_for_effects(group)
                
                for line_info in dialogue_lines:
                    dialogue = (
                        f"Dialogue: 0,{start_time},{end_time},{line_info['style']},"
                        f",0,0,0,,{line_info['tags']}{line_info['text']}"
                    )
                    all_dialogues.append(dialogue)

        if not all_dialogues:
            logger.warning(_tr("OCRToASSOptimizer", "No valid subtitle groups formed for any ROI, an empty ASS file will be generated."))
            with open(self.output_path, 'w', encoding='utf-8-sig') as f:
                f.write(self._get_ass_header())
            return

        self._write_ass_file(all_dialogues)
        logger.info(_tr("OCRToASSOptimizer", "--- Conversion successful ---"))


    def _load_and_organize_ocr_data(self, restored_data_generator: Generator) -> Dict[str, List[FrameData]]:
        roi_to_frame_data: Dict[str, Dict[int, FrameData]] = defaultdict(dict)
        for data, frame_num, roi_identifier in restored_data_generator:
            try:
                if frame_num not in roi_to_frame_data[roi_identifier]:
                    roi_to_frame_data[roi_identifier][frame_num] = FrameData(frame_num=frame_num)
                
                texts, scores, boxes, polygons = data.get('rec_texts', []), data.get('rec_scores', []), data.get('rec_boxes', []), data.get('rec_polys', [])
                
                for text, score, box, poly in zip(texts, scores, boxes, polygons):
                    if score >= self.MIN_SCORE_THRESHOLD and text.strip():
                        polygon_points = [(int(p[0]), int(p[1])) for p in poly]
                        box_points = tuple(int(b) for b in box)
                        roi_to_frame_data[roi_identifier][frame_num].lines.append(
                            TextLine(text=text, score=score, box=box_points, polygon=polygon_points)
                        )
            except Exception as e:
                logger.warning(_tr("OCRToASSOptimizer", "Error processing data for frame {} (ROI: {}): {}").format(frame_num, roi_identifier, e))

        final_organized_data: Dict[str, List[FrameData]] = {}
        for roi_id, frame_map in roi_to_frame_data.items():
            valid_frames = [fd for fd in frame_map.values() if not fd.is_empty]
            if valid_frames:
                valid_frames.sort(key=lambda f: f.frame_num)
                final_organized_data[roi_id] = valid_frames
        
        logger.info(_tr("OCRToASSOptimizer", "Successfully loaded and organized OCR data by {} ROIs.").format(len(final_organized_data)))
        return final_organized_data

    def _group_consecutive_frames(self, frames: List[FrameData]) -> List[SubtitleGroup]:
        if not frames: return []
        groups = []
        current_group = SubtitleGroup(start_frame=frames[0].frame_num, end_frame=frames[0].frame_num, frames=[frames[0]])
        
        for i in range(1, len(frames)):
            prev_frame_data = frames[i-1]
            curr_frame_data = frames[i]
            
            if curr_frame_data.frame_num == prev_frame_data.frame_num + 1 and self._are_frames_similar(current_group, curr_frame_data):
                current_group.end_frame = curr_frame_data.frame_num
                current_group.frames.append(curr_frame_data)
            else:
                if current_group.duration_frames >= self.MIN_DURATION_FRAMES:
                    groups.append(current_group)
                current_group = SubtitleGroup(start_frame=curr_frame_data.frame_num, end_frame=curr_frame_data.frame_num, frames=[curr_frame_data])
        
        if current_group.duration_frames >= self.MIN_DURATION_FRAMES:
            groups.append(current_group)
        
        logger.debug(_tr("OCRToASSOptimizer", "Merged into {} subtitle groups.").format(len(groups)))
        return groups

    def _are_frames_similar(self, group: SubtitleGroup, next_frame: FrameData) -> bool:
        last_frame = group.frames[-1]
        if len(last_frame.lines) != len(next_frame.lines): return False
        
        text1 = last_frame.text_fingerprint
        text2 = next_frame.text_fingerprint
        if not text1 or not text2: return False
        
        text_sim = Levenshtein.ratio(text1, text2)
        if text_sim < self.MERGE_TEXT_SIMILARITY: return False

        if not last_frame.lines or not next_frame.lines: return False
        avg_center1 = np.mean([line.center for line in last_frame.lines], axis=0)
        avg_center2 = np.mean([line.center for line in next_frame.lines], axis=0)
        dist = np.linalg.norm(avg_center1 - avg_center2)
        
        return dist < self.pos_tolerance

    def _format_time(self, frame_num: int) -> str:
        total_seconds = frame_num / self.fps
        h = int(total_seconds / 3600)
        m = int((total_seconds % 3600) / 60)
        s = int(total_seconds % 60)
        cs = int((total_seconds - int(total_seconds)) * 100)
        return f"{h}:{m:02d}:{s:02d}.{cs:02d}"

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

    def _is_in_bottom_area(self, y: float) -> bool:
        return y > self.height * self.BOTTOM_AREA_RATIO

    def _is_in_top_area(self, y: float) -> bool:
        return y < self.height * self.TOP_AREA_RATIO

    def _analyze_group_for_effects(self, group: SubtitleGroup) -> List[Dict]:
        if not group.frames: return []
        
        rep_text = group.representative_text
        stable_frames = [f for f in group.frames if f.text_fingerprint == rep_text]
        if not stable_frames: stable_frames = group.frames
        
        ref_frame = stable_frames[0]
        num_lines = len(ref_frame.lines)
        if num_lines == 0: return []

        lines_over_time = [[] for _ in range(num_lines)]
        for frame in stable_frames:
            if len(frame.lines) == num_lines:
                sorted_lines = sorted(frame.lines, key=lambda l: l.center[1])
                for i in range(num_lines):
                    lines_over_time[i].append(sorted_lines[i])

        dialogue_lines = []
        for line_history in lines_over_time:
            if not line_history: continue
            
            start_line, end_line = line_history[0], line_history[-1]
            pos_start, pos_end = start_line.center, end_line.center
            area_start, area_end = start_line.area, end_line.area
            rot_start, rot_end = start_line.rotation, end_line.rotation

            is_moving = np.linalg.norm(np.array(pos_start) - np.array(pos_end)) > 10
            is_zooming = abs(area_start - area_end) / area_start > 0.1 if area_start > 0 else False
            is_rotating = abs(rot_start - rot_end) > 2.0

            style, alignment_tag = self._get_style_and_alignment(start_line.center, start_line.text)
            tags = "{" + alignment_tag
            
            if style == "Scene":
                if is_moving and not is_zooming and not is_rotating:
                    tags += f"\\move({int(pos_start[0])},{int(pos_start[1])},{int(pos_end[0])},{int(pos_end[1])})"
                else:
                    tags += f"\\pos({int(pos_start[0])},{int(pos_start[1])})"
                    anim_tags = []
                    if is_zooming:
                        scale_end = int(math.sqrt(area_end / area_start) * 100) if area_start > 0 else 100
                        anim_tags.append(f"\\fscx100\\fscy100\\fscx{scale_end}\\fscy{scale_end}")
                    if is_rotating:
                        anim_tags.append(f"\\frz{-rot_start:.2f}\\frz{-rot_end:.2f}")
                    
                    if anim_tags:
                        tags += f"\\t({''.join(anim_tags)})"
                    elif abs(rot_start) > 1:
                        tags += f"\\frz{-rot_start:.2f}"
            
            tags += "}"
            dialogue_lines.append({'style': style, 'text': start_line.text, 'tags': tags, 'center': pos_start})
            
        if len(dialogue_lines) > 1 and any(d['style'] != 'Scene' for d in dialogue_lines):
            y_coords = [d['center'][1] for d in dialogue_lines]
            max_vertical_spread = max(y_coords) - min(y_coords)
            
            if max_vertical_spread < (self.height * 0.08) * 2.5:
                base_style = dialogue_lines[0]['style']
                sorted_dialogues = sorted(dialogue_lines, key=lambda d: d['center'][1])
                full_text = "\\n ".join([d['text'] for d in sorted_dialogues])
                base_tags = "{" + self._get_alignment_tag(base_style) + "}"
                return [{'style': base_style, 'text': full_text, 'tags': base_tags}]

        return dialogue_lines

    def _get_style_and_alignment(self, center_pos: Tuple[float, float], text: str) -> Tuple[str, str]:
        x, y = center_pos
        lang = self._detect_language(text)
        
        if self._is_in_bottom_area(y):
            return (lang, "\\an2") if lang in ['CH', 'JP', 'KO', 'RU'] else ('Default', "\\an2")
        
        if self._is_in_top_area(y):
            return 'Top', "\\an8"
        
        if y < self.height / 3: 
            return 'Top', "\\an8"
        
        return 'Scene', "\\an5"

    def _get_alignment_tag(self, style: str) -> str:
        if style == 'Top': return "\\an8"
        if style == 'Scene': return "\\an5"
        return "\\an2"

    def _get_ass_header(self) -> str:
        if self.template_path and self.template_path.exists():
            try:
                with open(self.template_path, 'r', encoding='utf-8') as f: content = f.read()
                content = re.sub(r'(?i)^PlayResX:.*', f'PlayResX: {self.width}', content, flags=re.MULTILINE)
                content = re.sub(r'(?i)^PlayResY:.*', f'PlayResY: {self.height}', content, flags=re.MULTILINE)
                if '[Events]' in content:
                    return content.split('[Events]')[0].strip() + '\n\n[Events]\nFormat: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n'
                logger.warning(_tr("OCRToASSOptimizer", "No '[Events]' tag found in template file. Events will be appended at the end of the file."))
                return content.strip() + '\n\n[Events]\nFormat: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n'
            except Exception as e:
                logger.error(_tr("OCRToASSOptimizer", "Failed to read template file: {}. Using default styles.").format(e))

        return _tr("OCRToASSOptimizer", """[Script Info]
Title: {video_stem} - Generated by Subtitle-OCR
ScriptType: v4.00+
WrapStyle: 0
PlayResX: {width}
PlayResY: {height}
ScaledBorderAndShadow: yes
YCbCr Matrix: TV.709

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,思源黑体 CN,{default_font_size:.0f},&H00FFFFFF,&H000000FF,&H00000000,&H00000000,0,0,0,0,100,100,0,0,1,2,1,2,10,10,10,1
Style: CH,思源黑体 CN,{default_font_size:.0f},&H00FFFFFF,&H000000FF,&H00000000,&H00000000,0,0,0,0,100,100,0,0,1,2,1,2,10,10,10,1
Style: JP,源ノ角ゴシック JP,{default_font_size:.0f},&H00FFFFFF,&H000000FF,&H00000000,&H00000000,0,0,0,0,100,100,0,0,1,2,1,2,10,10,10,1
Style: KO,Malgun Gothic,{default_font_size:.0f},&H00FFFFFF,&H000000FF,&H00000000,&H00000000,0,0,0,0,100,100,0,0,1,2,1,2,10,10,10,1
Style: RU,Arial,{default_font_size:.0f},&H00FFFFFF,&H000000FF,&H00000000,&H00000000,0,0,0,0,100,100,0,0,1,2,1,2,10,10,10,1
Style: Top,思源黑体 CN,{top_font_size:.0f},&H00FFFFFF,&H000000FF,&H00000000,&H00000000,0,0,0,0,100,100,0,0,1,2,1,8,10,10,10,1
Style: Scene,思源黑体 CN,{scene_font_size:.0f},&H00FFFFFF,&H000000FF,&H00000000,&H00000000,0,0,0,0,100,100,0,0,1,1,0,5,10,10,10,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
""").format(
            video_stem=self.video_path.stem,
            width=self.width,
            height=self.height,
            default_font_size=(self.height*0.06),
            top_font_size=(self.height*0.05),
            scene_font_size=(self.height*0.04)
        )

    def _write_ass_file(self, dialogues: List[str]):
        header = self._get_ass_header()
        dialogues.sort(key=lambda x: x.split(',')[1])
        try:
            with open(self.output_path, 'w', encoding='utf-8-sig') as f:
                f.write(header)
                for line in dialogues:
                    f.write(line + '\n')
            logger.info(_tr("OCRToASSOptimizer", "ASS subtitle file successfully written to: {}").format(self.output_path))
        except Exception as e:
            logger.error(_tr("OCRToASSOptimizer", "Error writing ASS file: {}").format(e), exc_info=True)
