import subprocess
import re
import logging
from dataclasses import dataclass
from typing import List, Tuple, Optional

from PySide6.QtCore import QCoreApplication

logger = logging.getLogger(__name__)


@dataclass
class Segment:
    start_sec: float
    end_sec: float

    @property
    def duration(self) -> float:
        return max(0.0, self.end_sec - self.start_sec)


def _run_ffmpeg(args: List[str]) -> str:
    p = subprocess.run(
        args,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    # ffmpeg writes most logs to stderr
    return (p.stdout or "") + "\n" + (p.stderr or "")


def detect_static_segments_freezedetect(
    video_path: str,
    crop_xywh: Tuple[int, int, int, int],
    min_freeze_duration: float = 0.25,
    noise: float = 0.003,
    max_analyze_sec: Optional[float] = None,
) -> List[Segment]:
    """
    Use ffmpeg `freezedetect` on a cropped ROI to find time segments where the ROI
    stays visually static (common for subtitles).

    Returns segments in seconds. You can later sample 1-3 frames per segment to OCR,
    and fill/bridge across the segment like the existing optimizer does.
    """
    x, y, w, h = [int(v) for v in crop_xywh]
    d = max(0.05, float(min_freeze_duration))
    n = max(1e-6, float(noise))

    vf = f"crop={w}:{h}:{x}:{y},freezedetect=n={n}:d={d}"
    cmd = ["ffmpeg", "-hide_banner", "-nostats", "-i", video_path, "-vf", vf, "-an", "-f", "null", "-"]
    if max_analyze_sec is not None and max_analyze_sec > 0:
        cmd = ["ffmpeg", "-hide_banner", "-nostats", "-t", str(float(max_analyze_sec)), "-i", video_path, "-vf", vf, "-an", "-f", "null", "-"]

    out = _run_ffmpeg(cmd)
    # Example lines:
    # [freezedetect @ ...] freeze_start: 12.345
    # [freezedetect @ ...] freeze_end: 13.678 | freeze_duration: 1.333
    start_re = re.compile(r"freeze_start:\s*([0-9]*\.?[0-9]+)")
    end_re = re.compile(r"freeze_end:\s*([0-9]*\.?[0-9]+)")

    segments: List[Segment] = []
    current_start: Optional[float] = None
    for line in out.splitlines():
        m1 = start_re.search(line)
        if m1:
            try:
                current_start = float(m1.group(1))
            except Exception:
                current_start = None
            continue
        m2 = end_re.search(line)
        if m2 and current_start is not None:
            try:
                end_t = float(m2.group(1))
                if end_t >= current_start:
                    segments.append(Segment(start_sec=current_start, end_sec=end_t))
            except Exception:
                pass
            current_start = None

    if segments:
        logger.info(
            QCoreApplication.translate(
                "ffmpeg_roi_segmenter",
                "freezedetect found {} static segments in ROI crop."
            ).format(len(segments))
        )
    else:
        logger.info(QCoreApplication.translate("ffmpeg_roi_segmenter", "freezedetect found no static segments in ROI crop."))

    return segments


def sample_times_for_segments(
    segments: List[Segment],
    samples_per_segment: int = 2,
    margin_sec: float = 0.03,
) -> List[float]:
    """
    Pick representative timestamps (seconds) within each segment.
    Default: 2 samples (near start and near end).
    """
    k = max(1, int(samples_per_segment))
    margin = max(0.0, float(margin_sec))
    times: List[float] = []
    for seg in segments:
        if seg.duration <= 0:
            continue
        s = seg.start_sec + margin
        e = seg.end_sec - margin
        if e <= s:
            mid = (seg.start_sec + seg.end_sec) / 2.0
            times.append(mid)
            continue
        if k == 1:
            times.append((s + e) / 2.0)
        else:
            for i in range(k):
                t = s + (e - s) * (i / (k - 1))
                times.append(t)
    return times

