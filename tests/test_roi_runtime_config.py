# tests/test_roi_runtime_config.py
"""ROI 运行时配置统一解析(core.roi_runtime_config)的单元测试。

覆盖:
- read_roi_config:GUI 保存格式(顶层 ocr_lang)与裸数组、非法输入、
  load_roi_file 兼容接口;
- effective_ocr_options:ROI 语言 > 显式 CLI 语言 > 文件顶层语言 > ch,
  model_tier 等其他键原样保留且不被后续 ROI 修改;
- resolve_roi_policies:显式策略覆盖保存值、未显式时保留保存值、缺省
  overlap;输入不被就地修改;
- collect_roi_pose_tags:与 GUI 主流水线相同的收集判断。
"""

from __future__ import annotations

import json
import os
import sys

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

import pytest  # noqa: E402

from core.roi_runtime_config import (  # noqa: E402
    RoiFileConfig,
    collect_roi_pose_tags,
    effective_ocr_options,
    read_roi_config,
    resolve_roi_policies,
)
import cli  # noqa: E402


# ---------------------------------------------------------------------------
# read_roi_config
# ---------------------------------------------------------------------------

def _write_roi_file(tmp_path, payload):
    path = tmp_path / "rois.json"
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return str(path)


def test_read_roi_config_keeps_top_level_language(tmp_path):
    path = _write_roi_file(tmp_path, {
        "ocr_lang": "japan",
        "rois": [
            {"type": "rect", "points": [0, 0, 100, 50], "ocr_lang": "en"},
            {"type": "rect", "points": [0, 60, 100, 50]},
        ],
    })
    cfg = read_roi_config(path)
    assert isinstance(cfg, RoiFileConfig)
    assert cfg.ocr_lang == "japan"
    assert [r.get("ocr_lang") for r in cfg.rois] == ["en", None]


def test_read_roi_config_bare_list_has_no_file_language(tmp_path):
    path = _write_roi_file(tmp_path, [
        {"type": "rect", "points": [0, 0, 10, 10]}])
    cfg = read_roi_config(path)
    assert cfg.ocr_lang is None
    assert len(cfg.rois) == 1


@pytest.mark.parametrize("payload", [
    {"rois": []},
    {"rois": ["not-a-dict"]},
    {"rois": [{"points": [0, 0, 1, 1]}]},
    [],
    {"unexpected": 1},
])
def test_read_roi_config_rejects_invalid_payload(tmp_path, payload):
    path = _write_roi_file(tmp_path, payload)
    with pytest.raises(ValueError):
        read_roi_config(path)


def test_read_roi_config_missing_file_raises_oserror(tmp_path):
    with pytest.raises(OSError):
        read_roi_config(str(tmp_path / "missing.json"))


def test_load_roi_file_stays_list_compatible(tmp_path):
    path = _write_roi_file(tmp_path, {
        "ocr_lang": "korean",
        "rois": [{"type": "rect", "points": [1, 2, 3, 4]}],
    })
    entries = cli.load_roi_file(path)
    assert isinstance(entries, list)
    assert entries[0]["type"] == "rect"


# ---------------------------------------------------------------------------
# effective_ocr_options
# ---------------------------------------------------------------------------

def test_motion_options_keep_tier_and_roi_language():
    base = {"model_tier": "small", "lang": "ch"}
    out = effective_ocr_options(base, {"ocr_lang": "japan"},
                                file_lang="ch", cli_lang="en")
    assert out["model_tier"] == "small"
    assert out["lang"] == "japan"
    assert base == {"model_tier": "small", "lang": "ch"}


def test_language_priority_cli_over_file_over_default():
    base = {"model_tier": "small"}
    roi = {}
    assert effective_ocr_options(base, roi, file_lang="japan",
                                 cli_lang="en")["lang"] == "en"
    assert effective_ocr_options(base, roi, file_lang="japan",
                                 cli_lang=None)["lang"] == "japan"
    assert effective_ocr_options(base, roi, file_lang=None,
                                 cli_lang=None)["lang"] == "ch"


def test_roi_lang_wins_even_with_whitespace():
    out = effective_ocr_options({"lang": "ch"}, {"ocr_lang": " japan "},
                                cli_lang="en")
    assert out["lang"] == "japan"


def test_roi_without_lang_returns_new_dict_each_call():
    base = {"model_tier": "small", "lang": "ch"}
    first = effective_ocr_options(base, {})
    second = effective_ocr_options(base, {})
    first["lang"] = "en"
    assert second["lang"] == "ch"
    assert base["lang"] == "ch"


# ---------------------------------------------------------------------------
# resolve_roi_policies
# ---------------------------------------------------------------------------

def test_explicit_overlap_overrides_saved_mask():
    rois = [{"scene_text_policy": "mask"}]
    resolved = resolve_roi_policies(rois, "overlap")
    assert resolved[0]["scene_text_policy"] == "overlap"
    assert resolve_roi_policies(rois, None)[0]["scene_text_policy"] == "mask"
    assert rois[0]["scene_text_policy"] == "mask"


def test_missing_policy_defaults_to_overlap():
    resolved = resolve_roi_policies([{"type": "rect"}], None)
    assert resolved[0]["scene_text_policy"] == "overlap"


def test_explicit_non_overlap_policy_applied_to_every_roi():
    rois = [{"scene_text_policy": "mask"}, {}]
    resolved = resolve_roi_policies(rois, "external")
    assert [r["scene_text_policy"] for r in resolved] == ["external", "external"]


# ---------------------------------------------------------------------------
# collect_roi_pose_tags
# ---------------------------------------------------------------------------

def test_collect_roi_pose_tags_follows_gui_rule():
    rois = [
        {"write_pose_tags": True, "pose": {"pos": [10, 20]}},
        {"write_pose_tags": True, "pose": "not-a-dict"},
        {"write_pose_tags": True},
        {"pose": {"pos": [1, 1]}},
        "not-a-dict",
    ]
    tags = collect_roi_pose_tags(rois)
    assert tags == {"roi_0": {"pos": [10, 20]}}


def test_collect_roi_pose_tags_empty_without_pose_rois():
    assert collect_roi_pose_tags([{"type": "rect"}]) == {}
    assert collect_roi_pose_tags([]) == {}
    assert collect_roi_pose_tags(None) == {}


# ---------------------------------------------------------------------------
# 入口级 spy:实际传给静态/运动引擎构建与转换器的完整参数
# ---------------------------------------------------------------------------

def _run_cli_with_spies(monkeypatch, tmp_path, argv, roi_payload=None):
    """跑 cli.run_pipeline(全 mock),捕获各阶段实际收到的关键字参数。"""
    from types import SimpleNamespace

    from core import pipeline_stages as stages
    import scripts.motion_ass as motion_cli

    video = tmp_path / "video.mp4"
    video.touch()
    captured = {}
    monkeypatch.setattr(cli, "probe_video", lambda _: {
        "width": 320, "height": 240, "fps": 25, "total_frames": 100})

    def fake_extract(ctx, **kw):
        captured["static_engine_options"] = dict(ctx.engine_options)
        return [], {"total_ocr_calls": 0}

    def fake_restore(ctx, raw, **kw):
        return []

    def fake_build(video_path, quad, **kwargs):
        captured.setdefault("motion_kwargs", []).append(dict(kwargs))
        return [], {"ok_frames": 1, "total_frames": 1, "keyframes": [],
                    "policy": "overlap", "lines": 0}

    out_ass = video.with_suffix(".ass")

    def fake_optimizer(**kw):
        captured["converter_kwargs"] = kw
        return SimpleNamespace(
            convert_from_memory=lambda records: out_ass.write_text("output"))

    monkeypatch.setattr(stages, "extract_and_ocr_stage", fake_extract)
    monkeypatch.setattr(stages, "restore_stage", fake_restore)
    monkeypatch.setattr(motion_cli, "build_motion_events", fake_build)
    monkeypatch.setattr("core.subtitle_generator.OCRToASSOptimizer",
                        fake_optimizer)
    full_argv = [str(video), "--workers", "1", "-q", *argv]
    if roi_payload is not None:
        full_argv += ["--roi-file", _write_roi_file(tmp_path, roi_payload)]
    args = cli.parse_args(full_argv)
    assert cli.run_pipeline(args) == 0
    return captured


def test_cli_motion_inherits_tier_and_language_priority(monkeypatch, tmp_path):
    captured = _run_cli_with_spies(
        monkeypatch, tmp_path, ["--model-tier", "small"],
        roi_payload={
            "ocr_lang": "japan",
            "rois": [{
                "type": "poly", "write_pose_tags": True,
                "points": [[0, 0], [100, 0], [100, 50], [0, 50]],
                "start_frame": 0, "end_frame": 50, "ocr_lang": "en",
            }],
        })
    # 运动路径:ROI 语言覆盖文件顶层语言,model_tier 不再丢失。
    motion_kwargs = captured["motion_kwargs"][0]
    assert motion_kwargs["engine_options"] == {
        "lang": "en", "model_tier": "small"}
    # 静态全局引擎:显式/顶层语言回退链生效,small 同样生效。
    assert captured["static_engine_options"] == {
        "lang": "japan", "model_tier": "small"}


def test_cli_bare_array_roi_file_still_works(monkeypatch, tmp_path):
    captured = _run_cli_with_spies(
        monkeypatch, tmp_path, ["--model-tier", "small"],
        roi_payload=[{
            "type": "poly", "write_pose_tags": True,
            "points": [[0, 0], [100, 0], [100, 50], [0, 50]],
            "start_frame": 0, "end_frame": 50, "ocr_lang": "korean",
        }])
    assert captured["motion_kwargs"][0]["engine_options"] == {
        "lang": "korean", "model_tier": "small"}
    assert captured["static_engine_options"]["lang"] == "ch"


def test_cli_saved_pose_tags_reach_converter(monkeypatch, tmp_path):
    captured = _run_cli_with_spies(
        monkeypatch, tmp_path, [],
        roi_payload={"rois": [{
            "type": "rect", "points": [0, 0, 100, 50],
            "write_pose_tags": True, "pose": {"pos": [50.0, 25.0]},
        }]})
    assert captured["converter_kwargs"]["roi_pose_tags"] == {
        "roi_0": {"pos": [50.0, 25.0]}}


def test_cli_no_pose_rois_passes_no_pose_tags(monkeypatch, tmp_path):
    captured = _run_cli_with_spies(
        monkeypatch, tmp_path, [],
        roi_payload={"rois": [{"type": "rect", "points": [0, 0, 100, 50]}]})
    assert not captured["converter_kwargs"]["roi_pose_tags"]


def test_cli_explicit_overlap_overrides_saved_mask(monkeypatch, tmp_path):
    captured = _run_cli_with_spies(
        monkeypatch, tmp_path, ["--scene-text-policy", "overlap"],
        roi_payload={"rois": [{
            "type": "rect", "points": [0, 0, 100, 50],
            "scene_text_policy": "mask",
        }]})
    # 显式 overlap:mask 不再收集,生成器按无策略(重叠显示)处理。
    assert not captured["converter_kwargs"]["roi_scene_text_policies"]


def test_cli_omitted_policy_keeps_saved_mask(monkeypatch, tmp_path):
    captured = _run_cli_with_spies(
        monkeypatch, tmp_path, [],
        roi_payload={"rois": [{
            "type": "rect", "points": [0, 0, 100, 50],
            "scene_text_policy": "mask",
        }]})
    assert captured["converter_kwargs"]["roi_scene_text_policies"] == {
        "roi_0": "mask"}


def test_worker_motion_stage_shares_tier_with_roi_lang(tmp_path, monkeypatch):
    """GUI 主流水线:同一 effective_ocr_options,ROI 语言覆盖全局语言。"""
    from core.pipeline_worker import PipelineWorker
    import scripts.motion_ass as motion_cli

    calls = []

    def fake_build(video_path, quad, **kwargs):
        calls.append(dict(kwargs))
        # 事件需覆盖 ROI 时间范围(5..50 帧 @25fps = 1.8s)才能通过接管门限。
        event = {"start_time": "0:00:00.20", "end_time": "0:00:02.00",
                 "style": "Scene", "name": "motion",
                 "tags": r"{\an5\move(100.0,100.0)}", "body": "テスト",
                 "layer": 0}
        return [event], {"ok_frames": 10, "total_frames": 10,
                         "keyframes": [], "policy": "overlap", "lines": 0}

    monkeypatch.setattr(motion_cli, "build_motion_events", fake_build)
    roi = {"type": "poly", "write_pose_tags": True,
           "points": [[100, 100], [180, 100], [180, 160], [100, 160]],
           "start_frame": 5, "end_frame": 50, "ocr_lang": "japan"}
    worker = PipelineWorker(
        video_path=str(tmp_path / "in.avi"), roi_data=[roi],
        total_frames=100, fps=25.0, video_width=320, video_height=240,
        output_ass_path=str(tmp_path / "out.ass"), debug_mode=False,
        template_path=None, engine_options={"lang": "ch",
                                            "model_tier": "small"})
    events, roi_ids = worker._run_motion_stage()
    assert roi_ids == {"roi_0"}
    assert calls[0]["engine_options"] == {"lang": "japan",
                                          "model_tier": "small"}
