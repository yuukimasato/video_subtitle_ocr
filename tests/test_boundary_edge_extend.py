# tests/test_boundary_edge_extend.py
"""edge_extend_frames: the boundary walk may cross the ROI's own range.

Uses injected fake OCR/probe callbacks (no video) to verify that:
- with edge_extend_frames > 0, head/tail clipped by the ROI range are
  recovered up to the extension limit;
- with edge_extend_frames = 0 (legacy), the walk stays inside the ROI range;
- a tail that runs to the ROI's last frame is extended even though no
  empty frame follows inside the range.
"""

from __future__ import annotations

from core.boundary_refine import refine_boundaries


TEXT = {"rec_texts": ["字幕内容"], "rec_scores": [0.9]}
EMPTY = {"rec_texts": [], "rec_scores": []}

ROI_START, ROI_END = 50, 100
TRUE_FIRST, TRUE_LAST = 40, 110  # text truly visible beyond the ROI range


def _roi_entry():
    return {"type": "rect", "points": [0, 500, 1280, 160],
            "start_frame": ROI_START, "end_frame": ROI_END,
            "fade_in_refine_enabled": True}


def _results():
    # OCR results only exist inside the ROI range; text detected on every frame.
    return [
        (_roi_entry(), dict(TEXT), f, "roi_0", f * 0.04)
        for f in range(ROI_START, ROI_END + 1)
    ]


def _make_funcs(visible_from=TRUE_FIRST, visible_to=TRUE_LAST):
    def ocr_frame_func(roi_entry, roi_id, frame_num):
        if visible_from <= frame_num <= visible_to:
            return (roi_entry, dict(EMPTY if False else TEXT), frame_num, roi_id, frame_num * 0.04)
        return (roi_entry, dict(EMPTY), frame_num, roi_id, frame_num * 0.04)

    def probe_frame_func(roi_entry, roi_id, frame_num):
        if visible_from <= frame_num <= visible_to:
            return (roi_entry, dict(TEXT), frame_num, roi_id, frame_num * 0.04)
        return None

    return ocr_frame_func, probe_frame_func


def _text_at(results, frame_num):
    for item in results:
        if int(item[2]) == frame_num and isinstance(item[1], dict):
            return "".join(str(t) for t in item[1].get("rec_texts", []))
    return ""


def test_edge_extend_recovers_clipped_head_and_tail():
    ocr_func, probe_func = _make_funcs()
    refined = refine_boundaries(
        _results(), [_roi_entry()],
        ocr_frame_func=ocr_func, probe_frame_func=probe_func,
        max_backtrack_frames=25, max_forward_frames=12,
        edge_extend_frames=20,
    )
    # Head walked back to TRUE_FIRST (40 = 50-10, within ±20).
    assert _text_at(refined, TRUE_FIRST) == "字幕内容"
    assert _text_at(refined, TRUE_FIRST - 1) == ""
    # Tail walked forward to TRUE_LAST (110 = 100+10, within ±20) — recovered
    # even though no empty frame followed inside the ROI range.
    assert _text_at(refined, TRUE_LAST) == "字幕内容"


def test_zero_extend_keeps_legacy_behaviour():
    ocr_func, probe_func = _make_funcs()
    refined = refine_boundaries(
        _results(), [_roi_entry()],
        ocr_frame_func=ocr_func, probe_frame_func=probe_func,
        max_backtrack_frames=25, max_forward_frames=12,
        edge_extend_frames=0,
    )
    assert _text_at(refined, ROI_START - 1) == ""
    assert _text_at(refined, ROI_END + 1) == ""
    assert _text_at(refined, ROI_START) == "字幕内容"
    assert _text_at(refined, ROI_END) == "字幕内容"


def test_edge_extend_is_bounded():
    ocr_func, probe_func = _make_funcs(visible_from=0, visible_to=1000)
    refined = refine_boundaries(
        _results(), [_roi_entry()],
        ocr_frame_func=ocr_func, probe_frame_func=probe_func,
        max_backtrack_frames=25, max_forward_frames=12,
        edge_extend_frames=20,
    )
    # Bounded by ROI range ± edge_extend and max_forward (100+min(20,12)=112),
    # not by text visibility.
    assert _text_at(refined, 30) == "字幕内容"
    assert _text_at(refined, 29) == ""
    assert _text_at(refined, 112) == "字幕内容"
    assert _text_at(refined, 113) == ""


def test_tail_refill_trims_faded_out_tail():
    """SSIM fill may propagate text over faint fade-out frames up to the ROI
    edge; re-grounding the tail with real per-frame OCR must trim it."""
    TRUE_TAIL = 98  # real per-frame OCR says text ends at frame 98

    def ocr_frame_func(roi_entry, roi_id, frame_num):
        data = TEXT if ROI_START <= frame_num <= TRUE_TAIL else EMPTY
        return (roi_entry, dict(data), frame_num, roi_id, frame_num * 0.04)

    def probe_frame_func(roi_entry, roi_id, frame_num):
        if ROI_START <= frame_num <= TRUE_TAIL:
            return (roi_entry, dict(TEXT), frame_num, roi_id, frame_num * 0.04)
        return None

    refined = refine_boundaries(
        _results(), [_roi_entry()],
        ocr_frame_func=ocr_frame_func, probe_frame_func=probe_frame_func,
        max_backtrack_frames=25, max_forward_frames=12,
        edge_extend_frames=20,
    )
    # Filled tail frames 99/100 are corrected to empty by the real re-OCR.
    assert _text_at(refined, TRUE_TAIL) == "字幕内容"
    assert _text_at(refined, TRUE_TAIL + 1) == ""
    assert _text_at(refined, ROI_END) == ""
