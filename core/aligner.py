"""字幕文本对齐器

用于对齐两个文本序列，支持基于相似度的匹配。
当目标文本缺少某项时，会使用其上一项进行填充。
"""

import difflib
from typing import Iterator, List, Optional, Tuple


class SubtitleAligner:
    """
    字幕文本对齐器，用于对齐两个文本序列，支持基于相似度的匹配。

    当目标文本缺少某项时，会使用其上一项进行填充。

    使用示例:
        >>> text1 = ['ab', 'b', 'c', 'd', 'e', 'f', 'g', 'h', 'i']
        >>> text2 = ['a',  'b', 'c', 'd', 'f', 'g', 'h', 'i']
        >>> aligner = SubtitleAligner()
        >>> aligned_source, aligned_target = aligner.align_texts(text1, text2)
        >>> # aligned_source: ['ab', 'b', 'c', 'd', 'e', 'f', 'g', 'h', 'i']
        >>> # aligned_target: ['a',  'b', 'c', 'd', 'd', 'f', 'g', 'h', 'i']
    """

    def __init__(self):
        self.line_numbers = [0, 0]

    def align_texts(
        self, source_text: List[str], target_text: List[str]
    ) -> Tuple[List[str], List[str]]:
        """对齐两个文本序列。

        Args:
            source_text: 源文本行列表
            target_text: 目标文本行列表

        Returns:
            (对齐后的源文本, 对齐后的目标文本)
        """
        diff_iterator = difflib.ndiff(source_text, target_text)
        return self._pair_lines(diff_iterator)

    def _pair_lines(
        self, diff_iterator: Iterator[str]
    ) -> Tuple[List[str], List[str]]:
        source_lines = []
        target_lines = []
        flag = 0

        for source_line, target_line, _ in self._line_iterator(diff_iterator):
            if source_line is not None:
                if source_line[1] == "\n":
                    flag += 1
                    continue
                source_lines.append(source_line[1])
            if target_line is not None:
                if flag > 0:
                    flag -= 1
                    continue
                target_lines.append(target_line[1])

        for i in range(1, len(target_lines)):
            if target_lines[i] == "\n":
                target_lines[i] = target_lines[i - 1]

        return source_lines, target_lines

    def _line_iterator(
        self, diff_iterator: Iterator[str]
    ) -> Iterator[Tuple[Optional[Tuple[int, str]], Optional[Tuple[int, str]], bool]]:
        """迭代 diff 行，产出配对的行。

        Yields:
            (source_line, target_line, has_diff)
        """
        lines: List[str] = []
        blank_lines_pending = 0
        blank_lines_to_yield = 0

        while True:
            while len(lines) < 4:
                lines.append(next(diff_iterator, "X"))

            diff_type = "".join([line[0] for line in lines])

            if diff_type.startswith("X"):
                blank_lines_to_yield = blank_lines_pending
            elif diff_type.startswith("-?+?"):
                yield (
                    self._format_line(lines, "?", 0),
                    self._format_line(lines, "?", 1),
                    True,
                )
                continue
            elif diff_type.startswith("--++"):
                blank_lines_pending -= 1
                yield self._format_line(lines, "-", 0), None, True
                continue
            elif diff_type.startswith(("--?+", "--+", "- ")):
                source_line, target_line = self._format_line(lines, "-", 0), None
                blank_lines_to_yield, blank_lines_pending = blank_lines_pending - 1, 0
            elif diff_type.startswith("-+?"):
                yield (
                    self._format_line(lines, None, 0),
                    self._format_line(lines, "?", 1),
                    True,
                )
                continue
            elif diff_type.startswith("-?+"):
                yield (
                    self._format_line(lines, "?", 0),
                    self._format_line(lines, None, 1),
                    True,
                )
                continue
            elif diff_type.startswith("-"):
                blank_lines_pending -= 1
                yield self._format_line(lines, "-", 0), None, True
                continue
            elif diff_type.startswith("+--"):
                blank_lines_pending += 1
                yield None, self._format_line(lines, "+", 1), True
                continue
            elif diff_type.startswith(("+ ", "+-")):
                source_line, target_line = None, self._format_line(lines, "+", 1)
                blank_lines_to_yield, blank_lines_pending = blank_lines_pending + 1, 0
            elif diff_type.startswith("+"):
                blank_lines_pending += 1
                yield None, self._format_line(lines, "+", 1), True
                continue
            elif diff_type.startswith(" "):
                yield (
                    self._format_line(lines[:], None, 0),
                    self._format_line(lines, None, 1),
                    False,
                )
                continue

            while blank_lines_to_yield < 0:
                blank_lines_to_yield += 1
                yield None, ("", "\n"), True
            while blank_lines_to_yield > 0:
                blank_lines_to_yield -= 1
                yield ("", "\n"), None, True

            if diff_type.startswith("X"):
                return
            else:
                yield source_line, target_line, True

    def _format_line(
        self,
        lines: List[str],
        format_key: Optional[str],
        side: int,
    ) -> Tuple[int, str]:
        """格式化一行文本。

        Args:
            lines: 待处理的行列表
            format_key: 格式化键 ('?', '-', '+', 或 None)
            side: 0 为源，1 为目标

        Returns:
            (行号, 格式化后的文本)
        """
        self.line_numbers[side] += 1
        if format_key is None:
            return self.line_numbers[side], lines.pop(0)[2:]
        if format_key == "?":
            text = lines.pop(0)
            lines.pop(0)  # Skip markers line
            text = text[2:]
        else:
            text = lines.pop(0)[2:]
            if not text:
                text = ""
        return self.line_numbers[side], text
