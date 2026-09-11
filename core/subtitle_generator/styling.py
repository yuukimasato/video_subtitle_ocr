# core/subtitle_generator/styling.py
import logging
import re
from collections import defaultdict
from typing import Dict, List

from PySide6.QtCore import QCoreApplication

from .models import SubtitleGroup

logger = logging.getLogger("core.subtitle_generator")

_tr = QCoreApplication.translate

class _StylingMixin:
    """Language detection / pose tags / style placement / ASS header helpers moved from core/subtitle_generator.py (L621-L1147)."""

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

    def _format_pose_tag(self, pose: Dict) -> str:
        """Build an ASS override block placing text at the ROI's pose."""
        pos = pose.get("pos") or [0, 0]
        try:
            px, py = float(pos[0]), float(pos[1])
        except (TypeError, ValueError, IndexError):
            px = py = 0.0
        frz = float(pose.get("frz", 0.0) or 0.0)
        frx = float(pose.get("frx", 0.0) or 0.0)
        fry = float(pose.get("fry", 0.0) or 0.0)
        return f"{{\\an5\\pos({px:.1f},{py:.1f})\\frz({frz:.1f})\\frx({frx:.1f})\\fry({fry:.1f})}}"

    def _apply_roi_pose_tags(self, styled_lines: List[Dict], pose: Dict) -> List[Dict]:
        """Apply the ROI's pose tags to a group's styled lines.

        - Scene lines already carry their own per-line {\\an5\\pos} (from the
          restored OCR box): keep that position and only append the rotation
          tags, so each line stays where it was detected.
        - Bottom/Top lines are re-pinned to the ROI pose center ({\\an5\\pos})
          with rotation, replacing their margin-based placement.
        """
        rotation = ""
        frz = float(pose.get("frz", 0.0) or 0.0)
        frx = float(pose.get("frx", 0.0) or 0.0)
        fry = float(pose.get("fry", 0.0) or 0.0)
        if abs(frz) > 1e-3:
            rotation += "\\frz({:.1f})".format(frz)
        if abs(frx) > 1e-3:
            rotation += "\\frx({:.1f})".format(frx)
        if abs(fry) > 1e-3:
            rotation += "\\fry({:.1f})".format(fry)

        out = []
        for line in styled_lines:
            tags = line.get("tags") or ""
            if "\\pos(" in tags:
                # Keep the line's own detected position, add rotation only.
                line["tags"] = tags + rotation
            else:
                # Re-pin to the ROI pose (pos + rotation).
                line["tags"] = self._format_pose_tag(pose)
            out.append(line)
        return out

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

            full_text = "\\N".join([line.text for line in sorted_lines])
            dialogue_lines.append({'style': style_name, 'text': full_text, 'tags': ''})

        elif location_type == 'TOP':
            full_text = "\\N".join([line.text for line in sorted_lines])
            dialogue_lines.append({'style': 'Top', 'text': full_text, 'tags': '{\\an8}'})
        elif location_type == 'SCENE':
            for line in sorted_lines:
                x = int(line.center[0]); y = int(line.center[1])
                tags = f"{{\\an5\\pos({x},{y})}}"
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
