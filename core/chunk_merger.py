# core/chunk_merger.py
"""Merge per-window frame records from chunk-parallel OCR workers.

Each chunk worker runs stages 1-3 over its (overlapping) work window and
emits restored frame records ``(data, frame_num, roi_identifier, time_sec)``.
Because work windows overlap, frames in an overlap zone are processed twice;
this module reduces the per-window streams back into the single record
stream the single-process pipeline would have produced:

1. index every record by the global key ``(roi_identifier, frame_num)``;
2. resolve duplicates with the planner's core-range ownership rule — the
   record from the window that OWNS the frame by core range wins (pure
   determinism: no content comparison, so OCR nondeterminism can never
   flip the outcome);
3. emit records sorted by ``(frame_num, roi_identifier)`` for stage 4.
"""
from __future__ import annotations

from typing import Dict, List, Tuple

from core.chunk_planner import ChunkPlan, owner_window


def merge_frame_records(
    per_window_records: Dict[int, List[tuple]],
    plan: ChunkPlan,
) -> List[tuple]:
    """Deduplicate and order restored frame records from all workers.

    ``per_window_records`` maps ``window.index`` -> that worker's records.
    Raises ValueError when a single window's stream contains the same
    ``(roi_identifier, frame_num)`` twice — extraction guarantees
    uniqueness, so duplicates indicate an upstream bug.
    """
    # (roi_id, frame_num) -> {window_index: record}
    by_key: Dict[Tuple[str, int], Dict[int, tuple]] = {}
    for window_index, records in per_window_records.items():
        for record in records:
            key = (record[2], int(record[1]))
            slot = by_key.setdefault(key, {})
            if window_index in slot:
                raise ValueError(
                    f"duplicate record for key {key} within window {window_index}"
                )
            slot[window_index] = record

    merged: List[tuple] = []
    for (roi_id, frame_num), candidates in by_key.items():
        record = _pick_record(candidates, plan, frame_num)
        merged.append(record)

    merged.sort(key=lambda r: (int(r[1]), r[2]))
    return merged


def _pick_record(candidates: Dict[int, tuple], plan: ChunkPlan, frame_num: int) -> tuple:
    if len(candidates) == 1:
        return next(iter(candidates.values()))
    owner = owner_window(plan, frame_num)
    if owner.index in candidates:
        return candidates[owner.index]
    # Defensive: the owning window should always have produced this frame
    # (it is inside its core, hence inside its work window). If it somehow
    # didn't, take the lowest-index available stream deterministically.
    return candidates[min(candidates)]
