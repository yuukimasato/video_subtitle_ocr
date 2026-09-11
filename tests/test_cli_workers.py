# tests/test_cli_workers.py
"""Tests for the CLI --workers option and its chunk-plan decision."""

from __future__ import annotations

import os
import sys

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

import pytest

import cli


def _args(**kw):
    argv = ["video.mp4"]
    for k, v in kw.items():
        argv += [k, str(v)]
    return cli.parse_args(argv)


def test_default_workers_is_auto():
    assert cli.parse_args(["video.mp4"]).workers == 0


def test_workers_accepts_zero_and_positive():
    assert cli.parse_args(["video.mp4", "--workers", "0"]).workers == 0
    assert cli.parse_args(["video.mp4", "--workers", "4"]).workers == 4


def test_workers_rejects_negative():
    with pytest.raises(SystemExit):
        cli.parse_args(["video.mp4", "--workers", "-1"])


def test_plan_workers_single_forces_none():
    args = _args(**{"--workers": 1})
    info = {"total_frames": 43200, "fps": 30.0}
    assert cli._plan_workers(args, info) is None


def test_plan_workers_short_video_bypasses(monkeypatch):
    monkeypatch.setattr("core.chunk_parallel_runner.probe_available_ram_mb",
                        lambda: 32768)
    args = _args()
    args.workers = 0
    info = {"total_frames": 480, "fps": 30.0}  # 16 s video
    assert cli._plan_workers(args, info) is None


def test_plan_workers_long_video_plans(monkeypatch):
    monkeypatch.setattr("core.chunk_parallel_runner.probe_available_ram_mb",
                        lambda: 32768)
    args = _args()
    args.workers = 0
    info = {"total_frames": 43200, "fps": 30.0}  # 24 min
    plan = cli._plan_workers(args, info)
    assert plan is not None and plan.workers >= 2
