# tests/test_cli_scan_args.py
"""CLI argument surface for scan mode (no OCR engine needed)."""

from __future__ import annotations

import pytest

from cli import parse_args, parse_roi_spec


def test_scan_defaults():
    args = parse_args(["video.mp4", "--scan"])
    assert args.scan is True
    assert args.scan_samples == 24
    assert args.watermark_mode == "auto"
    assert args.no_scene_roi is False
    assert args.preset is None


def test_scan_custom_samples_and_watermark_keep():
    args = parse_args([
        "video.mp4", "--scan", "--scan-samples", "36",
        "--watermark-mode", "keep", "--no-scene-roi",
    ])
    assert args.scan_samples == 36
    assert args.watermark_mode == "keep"
    assert args.no_scene_roi is True


def test_scan_rejects_bad_sample_count():
    with pytest.raises(SystemExit):
        parse_args(["video.mp4", "--scan", "--scan-samples", "0"])


def test_roi_entries_are_keep_all_by_policy_default():
    """Manual --roi specs carry no policy field → default keep_all downstream."""
    spec = parse_roi_spec("0,560,1280,160@5-20")
    assert spec.get("text_filter_policy") is None
    # 帧级边界精修默认开启。
    assert spec.get("fade_in_refine_enabled") is True
