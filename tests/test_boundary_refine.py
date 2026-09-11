# tests/test_boundary_refine.py
"""Unit tests for core/boundary_refine.py (frame-accurate edge refinement).

The tests simulate a video where a subtitle is actually visible on frames
10..20 (inclusive), while plain OCR can only read it from frame 12 (fade-in)
until frame 18 (fade-out). An enhanced probe (like the pipeline's 2x-upscale
retry) can read the faint frames too. Refinement must move the boundaries to
the true visible range without changing text content or timestamps.
"""

from __future__ import annotations

import sys
import os

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from core import boundary_refine


TRUE_FIRST, TRUE_LAST = 10, 20
OCR_FIRST, OCR_LAST = 12, 18
SUBTITLE_TEXT = "学成归来"


def make_ocr_data(text, score=0.9):
    return {
        "dt_polys": [],
        "rec_polys": [],
        "rec_texts": [text] if text else [],
        "rec_scores": [score] if text else [],
        "rec_boxes": [],
    }


def make_item(roi_entry, frame_num, text, time_sec=None):
    if time_sec is None:
        time_sec = frame_num * 0.04  # 25 fps
    return (roi_entry, make_ocr_data(text), frame_num, "roi_0", time_sec)


class FakeVideo:
    """Subtitle truly visible on TRUE_FIRST..TRUE_LAST; OCR reads only part."""

    def __init__(self, probe_can_read_faint=True, neighbour_text=None, neighbour_from=None):
        self.probe_can_read_faint = probe_can_read_faint
        self.neighbour_text = neighbour_text
        self.neighbour_from = neighbour_from
        self.ocr_calls = []

    def _true_text(self, frame_num):
        if TRUE_FIRST <= frame_num <= TRUE_LAST:
            return SUBTITLE_TEXT
        if self.neighbour_text and self.neighbour_from is not None and frame_num >= self.neighbour_from:
            return self.neighbour_text
        return ""

    def _plain_readable(self, frame_num):
        if OCR_FIRST <= frame_num <= OCR_LAST:
            return True
        return bool(self.neighbour_from is not None and frame_num >= self.neighbour_from)

    def ocr_frame_func(self, roi_entry, roi_id, frame_num):
        """Plain single-frame OCR: blind to faint frames."""
        self.ocr_calls.append(("ocr", frame_num))
        text = self._true_text(frame_num) if self._plain_readable(frame_num) else ""
        return make_item(roi_entry, frame_num, text)

    def probe_frame_func(self, roi_entry, roi_id, frame_num):
        """Enhanced probe: also reads faint frames when enabled."""
        self.ocr_calls.append(("probe", frame_num))
        text = self._true_text(frame_num)
        if text and not (OCR_FIRST <= frame_num <= OCR_LAST) and not self.probe_can_read_faint:
            return make_item(roi_entry, frame_num, "")
        return make_item(roi_entry, frame_num, text)


def build_results(frames_with_text):
    roi_entry = {"start_frame": 0, "end_frame": 60, "fade_in_refine_enabled": True,
                 "type": "rect", "points": [0, 0, 100, 40]}
    results = []
    for f in range(0, 61):
        text = SUBTITLE_TEXT if f in frames_with_text else ""
        results.append(make_item(roi_entry, f, text))
    return roi_entry, results


def text_at(results, frame_num):
    for item in results:
        if int(item[2]) == frame_num:
            return "".join(item[1].get("rec_texts", []))
    return None


def time_at(results, frame_num):
    for item in results:
        if int(item[2]) == frame_num:
            return float(item[4])
    return None


def test_refine_extends_fade_in_and_fade_out_to_true_edges():
    roi_entry, results = build_results(range(OCR_FIRST, OCR_LAST + 1))
    video = FakeVideo()
    refined = boundary_refine.refine_boundaries(
        results, [roi_entry],
        ocr_frame_func=video.ocr_frame_func,
        probe_frame_func=video.probe_frame_func,
    )
    # Boundary moved to the true visible range.
    assert text_at(refined, TRUE_FIRST) == SUBTITLE_TEXT
    assert text_at(refined, TRUE_LAST) == SUBTITLE_TEXT
    assert text_at(refined, TRUE_FIRST - 1) == ""
    assert text_at(refined, TRUE_LAST + 1) == ""
    # The previously-readable range keeps its text too.
    assert text_at(refined, OCR_FIRST) == SUBTITLE_TEXT
    assert text_at(refined, OCR_LAST) == SUBTITLE_TEXT


def test_refined_frames_keep_anchor_content_but_own_timestamps():
    roi_entry, results = build_results(range(OCR_FIRST, OCR_LAST + 1))
    video = FakeVideo()
    refined = boundary_refine.refine_boundaries(
        results, [roi_entry],
        ocr_frame_func=video.ocr_frame_func,
        probe_frame_func=video.probe_frame_func,
    )
    # Extended faint frames reuse the anchor content with their OWN time.
    assert time_at(refined, TRUE_FIRST) == TRUE_FIRST * 0.04
    assert time_at(refined, TRUE_LAST) == TRUE_LAST * 0.04


def test_refine_disabled_roi_is_untouched():
    roi_entry, results = build_results(range(OCR_FIRST, OCR_LAST + 1))
    roi_entry["fade_in_refine_enabled"] = False
    before = ["".join(it[1]["rec_texts"]) for it in results]
    video = FakeVideo()
    refined = boundary_refine.refine_boundaries(
        results, [roi_entry],
        ocr_frame_func=video.ocr_frame_func,
        probe_frame_func=video.probe_frame_func,
    )
    after = ["".join(it[1]["rec_texts"]) for it in refined]
    assert before == after
    assert video.ocr_calls == []


def test_refine_enabled_by_default_when_flag_missing():
    """边界精修默认开启：ROI 未写 fade_in_refine_enabled 时同样逐帧复核。"""
    roi_entry, results = build_results(range(OCR_FIRST, OCR_LAST + 1))
    roi_entry.pop("fade_in_refine_enabled")
    video = FakeVideo()
    refined = boundary_refine.refine_boundaries(
        results, [roi_entry],
        ocr_frame_func=video.ocr_frame_func,
        probe_frame_func=video.probe_frame_func,
    )
    assert text_at(refined, TRUE_FIRST) == SUBTITLE_TEXT
    assert text_at(refined, TRUE_LAST) == SUBTITLE_TEXT
    assert video.ocr_calls, "expected frame-by-frame probe calls to happen"


def test_probe_blind_to_faint_frames_keeps_original_timing():
    """No improvement possible -> boundaries stay at OCR-readable range."""
    roi_entry, results = build_results(range(OCR_FIRST, OCR_LAST + 1))
    video = FakeVideo(probe_can_read_faint=False)
    refined = boundary_refine.refine_boundaries(
        results, [roi_entry],
        ocr_frame_func=video.ocr_frame_func,
        probe_frame_func=video.probe_frame_func,
    )
    assert text_at(refined, TRUE_FIRST) == ""
    assert text_at(refined, OCR_FIRST) == SUBTITLE_TEXT
    assert text_at(refined, OCR_LAST) == SUBTITLE_TEXT


def test_walk_never_crosses_into_different_subtext():
    """A following subtitle with different text must not absorb this one."""
    roi_entry, results = build_results(range(OCR_FIRST, OCR_LAST + 1))
    video = FakeVideo(
        neighbour_text="全新的下一句话",
        neighbour_from=TRUE_LAST + 1,
    )
    refined = boundary_refine.refine_boundaries(
        results, [roi_entry],
        ocr_frame_func=video.ocr_frame_func,
        probe_frame_func=video.probe_frame_func,
    )
    # The neighbour stays untouched and this subtitle does not extend into it.
    assert text_at(refined, TRUE_LAST + 1) == "全新的下一句话"
    assert text_at(refined, TRUE_LAST) == SUBTITLE_TEXT
    assert text_at(refined, TRUE_LAST + 2) == "全新的下一句话"


def test_fade_in_anchor_not_hijacked_by_previous_subtitle():
    """Regression: the fade-in anchor search must start at the edge and scan
    backwards. Scanning from the window start let a previous subtitle still
    lingering inside the ground window hijack the anchor, so the new
    subtitle's faint head was never refined."""
    roi_entry = {"start_frame": 0, "end_frame": 60, "fade_in_refine_enabled": True,
                 "type": "rect", "points": [0, 0, 100, 40]}
    PREV_TEXT = "前一句话"
    # OCR-filled data: previous subtitle readable on 5..9, current subtitle
    # readable on 12..18 only (faint head 10..11 invisible to plain OCR).
    results = []
    for f in range(0, 61):
        if 5 <= f <= 9:
            text = PREV_TEXT
        elif OCR_FIRST <= f <= OCR_LAST:
            text = SUBTITLE_TEXT
        else:
            text = ""
        results.append(make_item(roi_entry, f, text))

    class TwoSubsVideo(FakeVideo):
        def _true_text(self, frame_num):
            if 5 <= frame_num <= 9:
                return PREV_TEXT
            if TRUE_FIRST <= frame_num <= TRUE_LAST:
                return SUBTITLE_TEXT
            return ""

        def _plain_readable(self, frame_num):
            if 5 <= frame_num <= 9:
                return True
            return OCR_FIRST <= frame_num <= OCR_LAST

    video = TwoSubsVideo()
    refined = boundary_refine.refine_boundaries(
        results, [roi_entry],
        ocr_frame_func=video.ocr_frame_func,
        probe_frame_func=video.probe_frame_func,
    )
    # The new subtitle's faint head is refined up to the true first frame.
    assert text_at(refined, TRUE_FIRST) == SUBTITLE_TEXT
    assert text_at(refined, TRUE_FIRST + 1) == SUBTITLE_TEXT
    # The previous subtitle is untouched.
    assert text_at(refined, 9) == PREV_TEXT
    assert text_at(refined, 5) == PREV_TEXT
    assert text_at(refined, 4) == ""


def test_low_score_lines_are_treated_as_absent():
    roi_entry, results = build_results([])
    # Frames 12..18 hold low-confidence garbage below the 0.6 threshold.
    for f in range(12, 19):
        results[f] = (roi_entry, make_ocr_data("残渣", 0.3), f, "roi_0", f * 0.04)
    video = FakeVideo()
    video.probe_can_read_faint = False
    refined = boundary_refine.refine_boundaries(
        results, [roi_entry],
        ocr_frame_func=video.ocr_frame_func,
        probe_frame_func=video.probe_frame_func,
    )
    # Nothing counted as text, so nothing was refined into a subtitle event.
    for f in range(0, 61):
        assert text_at(refined, f) in ("", "残渣")


def test_ocr_text_present_respects_min_score():
    assert boundary_refine.ocr_text_present(make_ocr_data("abc", 0.9), 0.6) is True
    assert boundary_refine.ocr_text_present(make_ocr_data("abc", 0.3), 0.6) is False
    assert boundary_refine.ocr_text_present(make_ocr_data("", 0.9), 0.0) is False
    assert boundary_refine.ocr_text_present(None, 0.0) is False


def test_progress_callback_reports_monotonic_done_with_stable_total():
    roi_entry, results = build_results(range(OCR_FIRST, OCR_LAST + 1))
    video = FakeVideo()
    reports = []
    refined = boundary_refine.refine_boundaries(
        results, [roi_entry],
        ocr_frame_func=video.ocr_frame_func,
        probe_frame_func=video.probe_frame_func,
        progress_callback=lambda done, total: reports.append((done, total)),
    )
    # One report per scanned frame of the refine-enabled ROI(s).
    assert len(reports) == 61
    assert reports[0] == (1, 61)
    assert reports[-1] == (61, 61)
    dones = [d for d, _ in reports]
    totals = {t for _, t in reports}
    assert dones == sorted(dones)  # monotonically increasing
    assert totals == {61}          # stable total


def test_parse_roi_index():
    assert boundary_refine.parse_roi_index("roi_12") == 12
    assert boundary_refine.parse_roi_index("roi_merged") is None
    assert boundary_refine.parse_roi_index("") is None
    assert boundary_refine.parse_roi_index(None) is None
