# core/subtitle_generator/timeline.py
import logging
from typing import Dict, List

from .models import FrameData

logger = logging.getLogger("core.subtitle_generator")

class _TimelineMixin:
    """ASS time parsing / formatting helpers moved from core/subtitle_generator.py (L144-L619)."""

    def _parse_ass_time_to_seconds(self, ts: str) -> float:
        parts = ts.strip().split(":")
        if len(parts) != 3:
            raise ValueError(f"bad ASS time: {ts!r}")
        h, m, s = parts
        return float(int(h)) * 3600.0 + float(int(m)) * 60.0 + float(s)

    def _dialogue_time_gap_seconds(self, end_ts: str, start_ts_next: str) -> float:
        return self._parse_ass_time_to_seconds(start_ts_next) - self._parse_ass_time_to_seconds(end_ts)

    def _event_duration_sec(self, ev: Dict[str, str]) -> float:
        try:
            return self._parse_ass_time_to_seconds(ev["end_time"]) - self._parse_ass_time_to_seconds(ev["start_time"])
        except Exception:
            return 0.0

    def _format_time(self, frame_num: int) -> str:
        if not self.fps or self.fps <= 0:
            return self._format_time_seconds(0.0)
        # Route through the centisecond formatter so rounding carries over
        # (59.9667s → 0:01:00.00) instead of being truncated (…59.96).
        return self._format_time_seconds(frame_num / self.fps)

    def _format_time_seconds(self, total_seconds: float) -> str:
        if total_seconds < 0:
            total_seconds = 0.0
        # Event boundaries are frame timestamps (or frame timestamp + one frame
        # duration). Truncating to centiseconds guarantees the stored time never
        # lands PAST the frame boundary it describes: with round-half-up a
        # boundary like 57.099 could print as 57.10, making the event paint one
        # extra frame — visible as the old subtitle lingering on the first
        # frame of the next hardsubbed line. Truncation keeps the carry-over
        # behaviour (…99.99 → next s) via the divmod below.
        cs_total = int(total_seconds * 100)
        h, rem = divmod(cs_total, 360000)
        m, rem = divmod(rem, 6000)
        s, cs = divmod(rem, 100)
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
