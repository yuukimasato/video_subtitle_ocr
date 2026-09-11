# core/subtitle_generator/models.py
import logging

from dataclasses import dataclass, field
from typing import List, Optional, Tuple

logger = logging.getLogger("core.subtitle_generator")

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
    # Frames that were grouped here (sorted by frame_num). Lets consumers
    # avoid rescanning the whole ROI frame list per group (O(N*G) otherwise).
    frames: List[FrameData] = field(default_factory=list)
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

