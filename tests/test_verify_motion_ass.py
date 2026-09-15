# tests/test_verify_motion_ass.py
r"""Unit tests for scripts/verify_motion_ass.py (Task 7: render deviation check).

Coverage (no ffmpeg required):
- bright_centroid on synthetic grayscale images (known centroids, all-black -> None)
- deviation_stats math (median / p95 linear interpolation / max)
- load_expect_json parsing + normalization of the expectation JSON
- weighted mean of expected points and per-frame Euclidean deviation
- uniform frame sampling, retry-command template expansion, budget decision
- CLI exit code 3 when ffmpeg is missing (shutil.which monkeypatched)

Optional smoke test (real ffmpeg + libass burn of a single event):
- centroid of `{\an5\pos(960,540)}TEST` on color=black must be within 10 px
  of (960, 540) and the CLI must exit 0.
"""

from __future__ import annotations

import json
import math
import os
import shutil
import sys

import numpy as np
import pytest

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from scripts import verify_motion_ass as vma  # noqa: E402


# ---------------------------------------------------------------------------
# bright_centroid
# ---------------------------------------------------------------------------

class TestBrightCentroid:
    def test_single_bright_pixel(self):
        img = np.zeros((100, 80), dtype=np.uint8)
        img[40, 30] = 255  # row 40 (y), col 30 (x)
        assert vma.bright_centroid(img) == (30.0, 40.0)

    def test_symmetric_rectangle(self):
        img = np.zeros((60, 60), dtype=np.uint8)
        img[10:20, 20:40] = 255  # rows 10..19, cols 20..39
        cx, cy = vma.bright_centroid(img)
        assert cx == pytest.approx((20 + 39) / 2)
        assert cy == pytest.approx((10 + 19) / 2)

    def test_asymmetric_blob_known_centroid(self):
        # 3 pixels: (10,10) value 200, (11,10) value 200, (10,20) value 255
        img = np.zeros((32, 32), dtype=np.uint8)
        img[10, 10] = 200
        img[11, 10] = 200
        img[10, 20] = 255
        # binary mask centroid (all three above any threshold in [160, 200])
        cx, cy = vma.bright_centroid(img, thresh=160)
        assert cx == pytest.approx((10 + 10 + 20) / 3)
        assert cy == pytest.approx((10 + 11 + 10) / 3)

    def test_thresh_boundary_exclusive(self):
        img = np.zeros((10, 10), dtype=np.uint8)
        img[5, 5] = 160  # exactly at threshold -> not counted (strict >)
        assert vma.bright_centroid(img, thresh=160) is None
        img[5, 5] = 161
        assert vma.bright_centroid(img, thresh=160) == (5.0, 5.0)

    def test_all_black_returns_none(self):
        img = np.zeros((40, 50), dtype=np.uint8)
        assert vma.bright_centroid(img) is None

    def test_all_below_threshold_returns_none(self):
        img = np.full((40, 50), 120, dtype=np.uint8)
        assert vma.bright_centroid(img, thresh=160) is None


# ---------------------------------------------------------------------------
# deviation_stats
# ---------------------------------------------------------------------------

class TestDeviationStats:
    def test_odd_count(self):
        stats = vma.deviation_stats([3.0, 1.0, 2.0])
        assert stats["median"] == pytest.approx(2.0)
        assert stats["max"] == pytest.approx(3.0)

    def test_even_count_median_is_mean_of_middle_two(self):
        stats = vma.deviation_stats([1.0, 2.0, 3.0, 4.0])
        assert stats["median"] == pytest.approx(2.5)

    def test_p95_linear_interpolation(self):
        # numpy percentile default ("linear"): rank = 0.95*(n-1)
        devs = list(range(1, 11))  # 1..10 -> rank 8.55 -> 1 + 0.55*8... check below
        expected_p95 = float(np.percentile(np.asarray(devs, dtype=float), 95))
        stats = vma.deviation_stats(devs)
        assert stats["p95"] == pytest.approx(expected_p95)
        # hand-computed: sorted 1..10, rank = 0.95*9 = 8.55 -> 9 + 0.55*(10-9)
        assert stats["p95"] == pytest.approx(9.55)

    def test_p95_single_value(self):
        stats = vma.deviation_stats([7.0])
        assert stats["median"] == pytest.approx(7.0)
        assert stats["p95"] == pytest.approx(7.0)
        assert stats["max"] == pytest.approx(7.0)

    def test_max(self):
        stats = vma.deviation_stats([0.5, 12.25, 4.0])
        assert stats["max"] == pytest.approx(12.25)

    def test_keys(self):
        stats = vma.deviation_stats([1.0, 2.0])
        assert set(stats.keys()) == {"median", "p95", "max"}
        assert all(isinstance(v, float) for v in stats.values())

    def test_empty_raises(self):
        with pytest.raises(ValueError):
            vma.deviation_stats([])


# ---------------------------------------------------------------------------
# load_expect_json
# ---------------------------------------------------------------------------

class TestLoadExpectJson:
    def _write(self, tmp_path, payload):
        p = tmp_path / "expect.json"
        p.write_text(json.dumps(payload), encoding="utf-8")
        return str(p)

    def test_round_trip_full_format(self, tmp_path):
        payload = {
            "width": 1920, "height": 1080, "fps": 23.976,
            "frames": {"12": {"points": [[822.5, 309.0, 1.0], [957.0, 580.0, 1.5]]}},
        }
        loaded = vma.load_expect_json(self._write(tmp_path, payload))
        assert loaded["width"] == 1920
        assert loaded["height"] == 1080
        assert loaded["fps"] == pytest.approx(23.976)
        # frame keys normalized to int
        assert 12 in loaded["frames"] and "12" not in loaded["frames"]
        pts = loaded["frames"][12]["points"]
        assert pts[0] == pytest.approx([822.5, 309.0, 1.0])
        assert pts[1] == pytest.approx([957.0, 580.0, 1.5])

    def test_missing_weight_defaults_to_one(self, tmp_path):
        payload = {
            "width": 640, "height": 360, "fps": 24.0,
            "frames": {"3": {"points": [[10.0, 20.0]]}},
        }
        loaded = vma.load_expect_json(self._write(tmp_path, payload))
        assert loaded["frames"][3]["points"][0] == pytest.approx([10.0, 20.0, 1.0])

    def test_missing_frames_key_raises(self, tmp_path):
        p = self._write(tmp_path, {"width": 1, "height": 1, "fps": 1.0})
        with pytest.raises(ValueError):
            vma.load_expect_json(p)

    def test_missing_header_fields_raise(self, tmp_path):
        p = self._write(tmp_path, {"frames": {"0": {"points": [[1, 2, 1.0]]}}})
        with pytest.raises(ValueError):
            vma.load_expect_json(p)

    def test_empty_points_raise(self, tmp_path):
        p = self._write(tmp_path, {"width": 1, "height": 1, "fps": 1.0,
                                   "frames": {"0": {"points": []}}})
        with pytest.raises(ValueError):
            vma.load_expect_json(p)

    def test_non_dict_payload_raises(self, tmp_path):
        p = tmp_path / "bad.json"
        p.write_text("[1, 2, 3]", encoding="utf-8")
        with pytest.raises(ValueError):
            vma.load_expect_json(str(p))


# ---------------------------------------------------------------------------
# weighted mean point + per-frame deviation
# ---------------------------------------------------------------------------

class TestWeightedMeanAndDeviation:
    def test_single_point(self):
        assert vma.weighted_mean_point([[5.0, 7.0, 2.0]]) == (5.0, 7.0)

    def test_weighted_mean(self):
        # weights 1 and 3 -> (0*1 + 10*3)/4 = 7.5
        pt = vma.weighted_mean_point([[0.0, 0.0, 1.0], [10.0, 4.0, 3.0]])
        assert pt[0] == pytest.approx(7.5)
        assert pt[1] == pytest.approx(3.0)

    def test_zero_total_weight_falls_back_to_plain_mean(self):
        pt = vma.weighted_mean_point([[0.0, 2.0, 0.0], [4.0, 2.0, 0.0]])
        assert pt == (2.0, 2.0)

    def test_empty_points_raise(self):
        with pytest.raises(ValueError):
            vma.weighted_mean_point([])

    def test_point_distance_euclidean(self):
        assert vma.point_distance((0.0, 0.0), (3.0, 4.0)) == pytest.approx(5.0)

    def test_frame_deviation_from_synthetic_image(self):
        # image whose bright centroid is known, vs expected weighted point
        img = np.zeros((100, 100), dtype=np.uint8)
        img[30:40, 60:70] = 255  # centroid ≈ (64.5, 34.5)
        cx, cy = vma.bright_centroid(img)
        expected = vma.weighted_mean_point([[70.0, 40.0, 2.0], [60.0, 30.0, 2.0]])
        dev = vma.point_distance((cx, cy), expected)
        assert dev == pytest.approx(math.hypot(cx - expected[0], cy - expected[1]))
        # expected point = (65, 35); centroid = (64.5, 34.5) -> dev = sqrt(.5)
        assert expected == (pytest.approx(65.0), pytest.approx(35.0))
        assert dev < 1.0


# ---------------------------------------------------------------------------
# frame sampling / retry template / budget decision
# ---------------------------------------------------------------------------

class TestSamplingAndHelpers:
    def test_sample_all_when_count_ge_available(self):
        frame_map = {i: {"points": [[0.0, 0.0, 1.0]]} for i in [5, 3, 9, 1]}
        assert vma.sample_frame_numbers(frame_map, 10) == [1, 3, 5, 9]

    def test_sample_uniform_deterministic_sorted(self):
        frame_map = {i: {"points": [[0.0, 0.0, 1.0]]} for i in range(100)}
        got = vma.sample_frame_numbers(frame_map, 20)
        assert len(got) == len(set(got)) == 20
        assert got == sorted(got)
        assert got[0] == 0 and got[-1] == 99
        # deterministic
        assert got == vma.sample_frame_numbers(frame_map, 20)

    def test_sample_single_picks_middle(self):
        frame_map = {i: {"points": [[0.0, 0.0, 1.0]]} for i in range(10)}
        assert vma.sample_frame_numbers(frame_map, 1) == [5]

    def test_sample_empty_raises(self):
        with pytest.raises(ValueError):
            vma.sample_frame_numbers({}, 5)

    def test_expand_retry_cmd_replaces_placeholder(self):
        cmd = vma.expand_retry_cmd("python synth.py --ass {ass} --tol 1.0", "/tmp/out.ass")
        assert cmd == "python synth.py --ass /tmp/out.ass --tol 1.0"

    def test_expand_retry_cmd_no_placeholder_unchanged(self):
        assert vma.expand_retry_cmd("true", "/x.ass") == "true"

    def test_budget_exceeded(self):
        stats = {"median": 3.0, "p95": 7.9, "max": 9.0}
        assert vma.budget_exceeded(stats, 4.0, 8.0) is False
        assert vma.budget_exceeded(stats, 2.9, 8.0) is True   # median over
        assert vma.budget_exceeded(stats, 4.0, 7.8) is True   # p95 over
        assert vma.budget_exceeded(stats, 4.0, 7.9) is False  # exactly at tol -> within

    def test_assess_pass_requires_no_missing_frames(self):
        stats = {"median": 1.0, "p95": 2.0, "max": 3.0}
        assert vma.assess(stats, missing_frames=[], tol_median=4.0, tol_p95=8.0)["passed"] is True
        verdict = vma.assess(stats, missing_frames=[7], tol_median=4.0, tol_p95=8.0)
        assert verdict["passed"] is False


# ---------------------------------------------------------------------------
# CLI: ffmpeg missing -> exit code 3 (no real ffmpeg needed)
# ---------------------------------------------------------------------------

class TestCliFfmpegMissing:
    def test_exit_code_3_without_ffmpeg(self, tmp_path, monkeypatch):
        expect = tmp_path / "expect.json"
        expect.write_text(json.dumps({
            "width": 64, "height": 48, "fps": 24.0,
            "frames": {"0": {"points": [[32.0, 24.0, 1.0]]}},
        }), encoding="utf-8")
        ass = tmp_path / "s.ass"
        ass.write_text("[Script Info]\n", encoding="utf-8")

        monkeypatch.setattr(vma.shutil, "which", lambda name: None)
        rc = vma.main([
            "--video", "black",
            "--ass", str(ass),
            "--expect-json", str(expect),
        ])
        assert rc == 3

    def test_exit_code_3_on_bad_expect_json(self, tmp_path, monkeypatch):
        # ffmpeg exists in this environment; load error must map to exit 3
        bad = tmp_path / "bad.json"
        bad.write_text("{}", encoding="utf-8")
        rc = vma.main([
            "--video", "black",
            "--ass", str(tmp_path / "s.ass"),
            "--expect-json", str(bad),
        ])
        assert rc == 3


# ---------------------------------------------------------------------------
# Optional smoke test: real ffmpeg + libass burn of a single event
# ---------------------------------------------------------------------------

MINIMAL_ASS = """[Script Info]
ScriptType: v4.00+
PlayResX: 1920
PlayResY: 1080
WrapStyle: 2
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,Arial,60,&H00FFFFFF,&H000000FF,&H00000000,&H00000000,0,0,0,0,100,100,0,0,1,0,0,5,10,10,10,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
Dialogue: 0,0:00:00.00,0:00:02.00,Default,,0,0,0,,{\\an5\\pos(960,540)}TEST
"""


@pytest.mark.skipif(shutil.which("ffmpeg") is None,
                    reason="ffmpeg not available on PATH")
class TestFfmpegSmoke:
    def test_single_event_centroid_near_expected(self, tmp_path):
        ass = tmp_path / "smoke.ass"
        ass.write_text(MINIMAL_ASS, encoding="utf-8")
        expect = tmp_path / "expect.json"
        expect.write_text(json.dumps({
            "width": 1920, "height": 1080, "fps": 24.0,
            "frames": {"12": {"points": [[960.0, 540.0, 4.0]]}},
        }), encoding="utf-8")
        report_path = tmp_path / "report.json"

        rc = vma.main([
            "--video", "black",
            "--ass", str(ass),
            "--expect-json", str(expect),
            "--frames", "1",
            # libass \an5 centers the text bounding box (incl. descent), so the
            # ink centroid sits a few px off the anchor; widen the budget here
            # and assert the <10px centroid deviation on the report instead.
            "--tol-median", "8.0", "--tol-p95", "16.0",
            "--report", str(report_path),
        ])
        assert rc == 0
        assert report_path.exists()

        report = json.loads(report_path.read_text(encoding="utf-8"))
        frames = report["attempts"][0]["frames"]
        assert len(frames) == 1
        rec = frames[0]
        assert rec["frame"] == 12
        assert rec["centroid"] is not None
        # burned centroid must be within 10 px of the expected (960, 540)
        assert rec["deviation"] < 10.0
        assert report["passed"] is True

    def test_over_budget_exit_2_and_retry_cmd_runs(self, tmp_path):
        # Expectation far from the actual burn position -> over budget on pass 1;
        # the retry command moves the subtitle to the expected position so the
        # second verification passes and the CLI exits 0.
        ass = tmp_path / "smoke2.ass"
        ass.write_text(MINIMAL_ASS, encoding="utf-8")
        expect = tmp_path / "expect.json"
        expect.write_text(json.dumps({
            "width": 1920, "height": 1080, "fps": 24.0,
            "frames": {"6": {"points": [[400.0, 300.0, 4.0]]}},
        }), encoding="utf-8")
        report_path = tmp_path / "report.json"

        fixed_ass = MINIMAL_ASS.replace("\\pos(960,540)", "\\pos(400,300)")
        retry_script = tmp_path / "retry.sh"
        retry_script.write_text(
            f"#!/bin/sh\ncat > \"$1\" <<'EOF'\n{fixed_ass}EOF\n",
            encoding="utf-8",
        )
        retry_cmd = f"sh {retry_script} {{ass}}"

        rc = vma.main([
            "--video", "black",
            "--ass", str(ass),
            "--expect-json", str(expect),
            "--frames", "1",
            "--tol-median", "8.0", "--tol-p95", "16.0",
            "--retry-cmd", retry_cmd,
            "--report", str(report_path),
        ])
        assert rc == 0
        report = json.loads(report_path.read_text(encoding="utf-8"))
        assert report["retry"]["attempted"] is True
        assert len(report["attempts"]) == 2
        assert report["attempts"][0]["passed"] is False
        assert report["attempts"][1]["passed"] is True

    def test_over_budget_without_retry_exits_2(self, tmp_path):
        ass = tmp_path / "smoke3.ass"
        ass.write_text(MINIMAL_ASS, encoding="utf-8")
        expect = tmp_path / "expect.json"
        expect.write_text(json.dumps({
            "width": 1920, "height": 1080, "fps": 24.0,
            "frames": {"3": {"points": [[100.0, 100.0, 1.0]]}},
        }), encoding="utf-8")

        rc = vma.main([
            "--video", "black",
            "--ass", str(ass),
            "--expect-json", str(expect),
            "--frames", "1",
        ])
        assert rc == 2
