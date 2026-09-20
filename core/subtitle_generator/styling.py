# core/subtitle_generator/styling.py
import logging
import re
from collections import defaultdict
from typing import Dict, List, Optional

from PySide6.QtCore import QCoreApplication

from core.line_restoration import merge_restoration_tags
from core.text_alignment import ALIGN_LEFT, ALIGN_RIGHT, detect_line_alignments

from .models import SubtitleGroup

logger = logging.getLogger("core.subtitle_generator")

_tr = QCoreApplication.translate

class _StylingMixin:
    """Language detection / pose tags / style placement / ASS header helpers moved from core/subtitle_generator.py (L621-L1147)."""

    def _detect_language(self, text: str) -> str:
        counts = defaultdict(int)
        text_for_detection = re.sub(r'[ ,.!?\'"(){}\[\]\d-]', '', text)
        if not text_for_detection:
            return 'EN'

        for char in text_for_detection:
            code = ord(char)
            if 0x4E00 <= code <= 0x9FFF:
                counts['CH'] += 1
            elif 0x3040 <= code <= 0x309F or 0x30A0 <= code <= 0x30FF:
                counts['JP'] += 1
            elif 0xAC00 <= code <= 0xD7A3:
                counts['KO'] += 1
            elif 0x0400 <= code <= 0x04FF:
                counts['RU'] += 1
            elif 0x0020 <= code <= 0x007E:
                counts['EN'] += 1
        
        # defaultdict 取值会物化键，须用 .get：否则查 'JP' 会把 JP:0 塞进
        # counts，下方 `if not counts` 永远不成立——全字符都在已知范围外
        # 的文本（希腊/阿拉伯/泰文等）会被误判成 'JP' 而套用日文字体，
        # 缺字形整行渲染成豆腐块。
        if counts.get('JP', 0) > 0:
            return 'JP'
        if not counts:
            return 'EN'
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
        # ASS 数值标签(\frz/\frx/\fry/\fs)参数直接跟数值,不带圆括号:
        # 只有 \pos/\move/\t/\clip 等才用括号;写成 \frz(8.0) 会被 libass
        # 静默忽略(与 core/motion_ass.py 的 \frz 写法保持一致)。
        return f"{{\\an5\\pos({px:.1f},{py:.1f})\\frz{frz:.1f}\\frx{frx:.1f}\\fry{fry:.1f}}}"

    def _apply_roi_pose_tags(
        self,
        styled_lines: List[Dict],
        pose: Dict,
        frame_num: Optional[int] = None,
    ) -> List[Dict]:
        """Apply the ROI's pose tags to a group's styled lines.

        - Scene lines already carry their own per-line anchor tag ({\\an5\\pos}
          for centered blocks, {\\an4\\pos}/{\\an6\\pos} at the box edge for
          left/right-aligned blocks, from the restored OCR box): keep that
          position and append a per-line rotation block — the \\frz is each
          line's own polygon angle (screen coordinates), not the ROI-level
          tilt; a hand-drawn quad's overall tilt differs from individual
          line angles on tilted panels. \\frx/\\fry stay at the ROI pose
          values (auto perspective estimation is not renderer-faithful).
        - Scene lines additionally gain sampled color/outline tags
          (\\1c/\\3c/\\bord) from the frame at ``frame_num`` when that frame
          can be decoded and the sampling is trustworthy.
        - Bottom/Top lines are re-pinned to the ROI pose center ({\\an5\\pos})
          with rotation, replacing their margin-based placement.
        """
        frz_roi = float(pose.get("frz", 0.0) or 0.0)
        frx = float(pose.get("frx", 0.0) or 0.0)
        fry = float(pose.get("fry", 0.0) or 0.0)
        # pose 启用即整体写入旋转块(含 0 值):与选项提示「附带 \pos \frz
        # \frx \fry」一致;零倾角(正放矩形 ROI)此前不追加任何标签,勾选
        # 前后输出完全相同,用户无法确认选项已生效。
        # 数值标签不带圆括号(见 _format_pose_tag 同步说明)。
        rotation_roi = "\\frz{:.1f}\\frx{:.1f}\\fry{:.1f}".format(frz_roi, frx, fry)

        frame_img = None
        if frame_num is not None:
            try:
                frame_img = self._get_analysis_reader().read(int(frame_num))
            except Exception:  # 取帧失败只跳过取色,不影响几何标签
                frame_img = None

        out = []
        for line in styled_lines:
            tags = line.get("tags") or ""
            poly = line.get("poly")
            if "\\pos(" in tags and poly:
                # Scene 行:逐行 frz(行多边形方向角)+ 可信时的取色标签。
                # 标签合并逻辑与策略路径共用 core.line_restoration.
                # merge_restoration_tags(旋转/颜色并入既有 override 块内部,
                # 块外会被 libass 当字面文本渲染出来)。
                line["tags"] = merge_restoration_tags(
                    tags, poly, frame_img, float(line.get("height") or 0.0),
                    frx, fry)
            elif "\\pos(" in tags:
                # Scene 行但无多边形(理论不出现):保持 ROI 级旋转块。
                if tags.endswith("}"):
                    line["tags"] = tags[:-1] + rotation_roi + "}"
                else:
                    line["tags"] = tags + "{" + rotation_roi + "}"
            else:
                # Re-pin to the ROI pose (pos + rotation).
                line["tags"] = self._format_pose_tag(pose)
            out.append(line)
        return out

    def _determine_style_and_position(self, group: SubtitleGroup) -> List[Dict]:
        avg_box = group.get_avg_box()
        if not avg_box:
            return []
        avg_y_center = (avg_box[1] + avg_box[3]) / 2
        if avg_y_center > self.height * self.VIDEO_BOTTOM_AREA:
            location_type = 'BOTTOM'
        elif avg_y_center < self.height * self.VIDEO_TOP_AREA:
            location_type = 'TOP'
        else:
            location_type = 'SCENE'
        dialogue_lines = []
        sorted_lines = sorted(group.lines, key=lambda line: line.box[1])
        
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
            # 逐行投票检测原文对齐(左→\an4 锚行框左缘,右→\an6 锚右缘,中→
            # \an5 锚中心):识别行以行高回贴,渲染宽度与原框必有出入,居中
            # 锚点会让左/右对齐的行失去公共边距(邮件正文等短行浮到中间)。
            # 聊天界面左右气泡混排 → 同屏两种对齐并存,须逐行判定而非整组
            # 一票;孤行(无同边距行可贴合)归中、锚自身原位。见 core.text_alignment。
            # 组行数 ≥5 才开剪切去趋势:SCENE 组可以是 10 行邮件正文,斜放
            # 平面在视频坐标里竖直列随 y 倾斜,不去趋势整组误归中;小组
            # (2-4 行)样本不足以可靠估计斜率,保持不去趋势(旧行为)。
            align_diag: Dict[str, object] = {}
            aligns = detect_line_alignments(
                [line.box for line in sorted_lines],
                detrend_shear=len(sorted_lines) >= 5,
                diagnostics=align_diag)
            if logger.isEnabledFor(logging.DEBUG):
                # 排查「为什么判成 left」:逐行判定结果 + 三边票数(DEBUG 级,
                # 缺省日志级别不产生输出,不改事件输出)。
                diag_rows = align_diag.get("rows") or []
                logger.debug(
                    "Scene line alignments (row/align/votes): %s (shear_slope=%s)",
                    [(d.get("row"), d.get("align"), d.get("votes"))
                     for d in diag_rows],
                    align_diag.get("shear_slope"))
            for line, align in zip(sorted_lines, aligns):
                y = int(line.center[1])
                if align == ALIGN_LEFT:
                    x = int(line.box[0])
                    an = 4
                elif align == ALIGN_RIGHT:
                    x = int(line.box[2])
                    an = 6
                else:
                    x = int(line.center[0])
                    an = 5
                tags = f"{{\\an{an}\\pos({x},{y})}}"
                dialogue_lines.append({
                    'style': 'Scene', 'text': line.text, 'tags': tags,
                    # 逐行还原(_apply_roi_pose_tags)用:屏幕坐标多边形与
                    # 行高(描边估计的字号参照)。
                    'poly': [tuple(p) for p in line.polygon],
                    'height': line.bounding_height,
                })
        return dialogue_lines

    def _note_box_style_line(self) -> str:
        """external 模式展示框样式行(与 scripts/motion_ass 的默认头一致):

        Scene 同字号、BorderStyle=3(不透明底框)、半透明黑 BackColour、
        对齐 2。仅当任一 ROI 配置了非 overlap 策略时写入(策略可能运行时
        回退到 external,样式需常备);全 overlap 时保持原头部不变。
        """
        fs_scene = self.height * 0.04
        common = "&H00FFFFFF,&H000000FF,&H00000000,&H80000000&,0,0,0,0,100,100,0,0,3"
        return (f"Style: NoteBox,思源黑体 CN,{fs_scene:.0f},{common},"
                f"1,0,2,10,10,10,1")

    def _note_style_line_if_needed(self, header_text: str = "") -> str:
        """需常备 NoteBox 样式时返回样式行(带换行),否则空串。

        任一 ROI 配置了非 overlap 的场景文字策略(策略可能运行时回退到
        external),或轨迹事件里存在 NoteBox 样式(轨迹管线策略回退链
        产出)→ 样式需常备;模板已自定义 NoteBox 时不覆盖。
        """
        needed = (
            any(str(p) != "overlap"
                for p in getattr(self, "roi_scene_text_policies", {}).values())
            or any(ev.get("style") == "NoteBox"
                   for ev in getattr(self, "motion_events", []))
        )
        if not needed or "NoteBox" in header_text:
            return ""
        return self._note_box_style_line() + "\n"

    def _get_ass_header(self) -> str:
        if self.template_path and self.template_path.exists():
            try:
                with open(self.template_path, 'r', encoding='utf-8') as f:
                    content = f.read()
                content = re.sub(r'(?i)^PlayResX:.*', f'PlayResX: {self.width}', content, flags=re.MULTILINE)
                content = re.sub(r'(?i)^PlayResY:.*', f'PlayResY: {self.height}', content, flags=re.MULTILINE)
                if '[Events]' in content:
                    header = content.split('[Events]')[0]
                else:
                    logger.warning(_tr("OCRToASSOptimizer", "No '[Events]' tag found in template file. Events will be appended at the end of the file."))
                    header = content
                # 策略回退可能产出 NoteBox 事件,模板头缺该样式时补齐,
                # 否则 libass 回退 Default,丢失不透明底框渲染。
                header += self._note_style_line_if_needed(header)
                return header.strip() + '\n\n[Events]\nFormat: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n'
            except Exception as e:
                logger.error(_tr("OCRToASSOptimizer", "Failed to read template file {}: {}. Using default styles.").format(self.template_path, e))

        # 任一 ROI 配置了非 overlap 的场景文字策略,或轨迹事件里存在
        # NoteBox 样式(轨迹管线策略回退链产出)→ 追加 NoteBox 样式行
        # (样式行不是事件,不触碰逐事件一致性;缺省路径头部逐字节不变)。
        note_style = self._note_style_line_if_needed()

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
{note_style}
[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
