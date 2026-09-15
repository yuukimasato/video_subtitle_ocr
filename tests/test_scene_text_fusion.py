# tests/test_scene_text_fusion.py
"""场景文字（手机/信件/招牌）逐行融合改进的单元测试。

对应 test/phone-scene-analysis-20260915/analysis.md 的阶段一落地：
- 优化器按行框位置对齐融合：首帧漏读/空读可被后续清晰帧救回，某帧漏读
  一行不再让整段放弃投票，缺失观测不做负向投票，歧义行收敛到行级送 VLM。
- 代表行投票按 y 重叠对齐行槽，不再要求行数一致时索引一一对应。
- 全帧扫描的场景文字候选按"归一化文本一致 + 空间相邻/重叠"合并。

全部离线运行：OCR 引擎由脚本化的假引擎替代。
"""

from __future__ import annotations

import copy
import os
import sys

import numpy as np
import pytest

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from core import ocr_engine_manager, vlm_refine
from core.fullframe_scanner import _merge_scene_candidates
from core.ocr_optimizer import OcrOptimizer, _line_aabbs, fuse_samples_by_position
from core.subtitle_generator import OCRToASSOptimizer
from core.subtitle_generator.models import FrameData, SubtitleGroup, TextLine

ROI_ENTRY = {"type": "rect", "points": [0, 100, 640, 360], "start_time": 0.0, "end_time": 10.0}


# ---------------------------------------------------------------------------
# 脚本化假引擎：按调用顺序吐出完整的 unified OCR 结果
# ---------------------------------------------------------------------------

def _raw(lines):
    """lines: [(text, score, (x1, y1, x2, y2)), ...] → unified OCR dict。"""
    polys = [
        [[float(x1), float(y1)], [float(x2), float(y1)],
         [float(x2), float(y2)], [float(x1), float(y2)]]
        for _, _, (x1, y1, x2, y2) in lines
    ]
    return {
        "dt_polys": polys,
        "rec_polys": polys,
        "rec_texts": [t for t, _, _ in lines],
        "rec_scores": [float(s) for _, s, _ in lines],
        "rec_boxes": [[int(x1), int(y1), int(x2), int(y2)] for _, _, (x1, y1, x2, y2) in lines],
    }


EMPTY = _raw([])


class ScriptableEngine:
    """predict_batch 按调用顺序返回预设结果，耗尽后回落到 default。"""

    def __init__(self, scripted=None, default=None):
        self.scripted = list(scripted or [])
        self.default = default if default is not None else _raw([])
        self.batch_calls = 0

    def predict_batch(self, images):
        self.batch_calls += 1
        out = []
        for _ in images:
            out.append(self.scripted.pop(0) if self.scripted else copy.deepcopy(self.default))
        return out

    def predict(self, img):
        return self.scripted.pop(0) if self.scripted else copy.deepcopy(self.default)

    def normalize_batch_result(self, raw_list):
        return [copy.deepcopy(r) for r in raw_list]

    def normalize_result(self, raw):
        return copy.deepcopy(raw)


@pytest.fixture
def install_engine(monkeypatch):
    def _install(engine):
        monkeypatch.setattr(ocr_engine_manager, "get_engine", lambda: engine)
        return engine
    return _install


def make_optimizer(tmp_path, **overrides) -> OcrOptimizer:
    params = dict(
        work_dir=str(tmp_path),
        visualize=False,
        in_memory_mode=True,
        save_ocr_json=False,
        motion_sentinel_enabled=False,
    )
    params.update(overrides)
    return OcrOptimizer(**params)


def make_frames(n, start=0):
    return [
        (ROI_ENTRY, np.full((160, 320, 3), 200, dtype=np.uint8), start + i, "roi_0", (start + i) / 30.0)
        for i in range(n)
    ]


# ---------------------------------------------------------------------------
# a) 位置对齐融合：锚定帧漏读被救回
# ---------------------------------------------------------------------------

def test_empty_anchor_rescued_by_later_samples(tmp_path, install_engine):
    """首采样完全空读、后两个采样读到两行 → 融合结果必须包含两行。"""
    body = [
        ("第一行正文", 0.97, (40, 40, 280, 70)),
        ("第二行正文", 0.96, (40, 90, 280, 120)),
    ]
    engine = install_engine(ScriptableEngine(scripted=[EMPTY, _raw(body), _raw(body)]))
    opt = make_optimizer(tmp_path)

    result = opt._get_best_ocr_result_from_sequence(make_frames(9))

    assert result[1]["rec_texts"] == ["第一行正文", "第二行正文"]
    assert result[1]["rec_scores"] == [0.97, 0.96]
    # 几何来自提供该行文本的真实采样帧。
    assert result[1]["dt_polys"][0][0] == [40.0, 40.0]


def test_anchor_missing_middle_line_rescued(tmp_path, install_engine):
    """锚定帧漏读中间行：其余行仍按位置对齐，漏读行由后续帧补齐。"""
    top = ("顶部标题", 0.97, (40, 30, 280, 60))
    mid = ("中间正文", 0.96, (40, 80, 280, 110))
    bottom = ("底部落款", 0.95, (40, 130, 280, 160))
    engine = install_engine(ScriptableEngine(scripted=[
        _raw([top, bottom]),            # 锚定帧：漏读中间行
        _raw([top, mid, bottom]),       # 中采样：三行齐全
        _raw([top, mid, bottom]),       # 尾采样：三行齐全
    ]))
    opt = make_optimizer(tmp_path)

    result = opt._get_best_ocr_result_from_sequence(make_frames(9))

    assert result[1]["rec_texts"] == ["顶部标题", "中间正文", "底部落款"]


def test_missing_observation_does_not_vote_negatively(tmp_path, install_engine, monkeypatch):
    """某行只在 2/3 采样中被读到且两处一致 → 观测一致即确认，不送 VLM。"""
    line_a = ("未遮挡行", 0.97, (40, 40, 280, 70))
    line_b = ("被遮挡行", 0.96, (40, 90, 280, 120))
    engine = install_engine(ScriptableEngine(scripted=[
        _raw([line_a, line_b]),
        _raw([line_a]),                 # 中采样：手挡住 B 行
        _raw([line_a, line_b]),
    ]))
    opt = make_optimizer(tmp_path)

    called = []
    monkeypatch.setattr(vlm_refine, "is_configured", lambda: True)
    monkeypatch.setattr(vlm_refine, "refine_subtitle_text", lambda *a, **k: called.append(a) or None)

    result = opt._get_best_ocr_result_from_sequence(make_frames(9))

    assert result[1]["rec_texts"] == ["未遮挡行", "被遮挡行"]
    assert called == []


def test_disagreement_routes_ambiguous_line_to_vlm(tmp_path, install_engine, monkeypatch):
    """同一位置三个采样读出三种文本 → 无多数，行级送 VLM 裁决。"""
    box = (40, 40, 280, 70)
    engine = install_engine(ScriptableEngine(scripted=[
        _raw([("讀法甲", 0.9, box)]),
        _raw([("讀法乙", 0.9, box)]),
        _raw([("讀法丙", 0.9, box)]),
    ]))
    opt = make_optimizer(tmp_path)

    captured = {}

    def fake_refine(images, candidates):
        captured["candidates"] = candidates
        return ["VLM 裁决文本"]

    monkeypatch.setattr(vlm_refine, "is_configured", lambda: True)
    monkeypatch.setattr(vlm_refine, "refine_subtitle_text", fake_refine)

    result = opt._get_best_ocr_result_from_sequence(make_frames(9))

    assert captured["candidates"] == [["讀法甲", "讀法乙", "讀法丙"]]
    assert result[1]["rec_texts"] == ["VLM 裁决文本"]


def test_no_geometry_falls_back_to_strict_index(tmp_path, install_engine):
    """无行几何的引擎结果：行数不一致时保持旧行为（锚定帧原样返回）。"""
    def no_geo(text):
        raw = _raw([(text, 0.95, (40, 40, 280, 70))]) if text else copy.deepcopy(EMPTY)
        raw["dt_polys"] = []
        raw["rec_polys"] = []
        return raw

    two_lines = copy.deepcopy(no_geo("第一行"))
    two_lines["rec_texts"] = ["第一行", "第二行"]
    two_lines["rec_scores"] = [0.95, 0.94]
    two_lines["rec_boxes"] = [[40, 40, 280, 70], [40, 90, 280, 120]]
    engine = install_engine(ScriptableEngine(scripted=[
        two_lines, no_geo("第一行"), no_geo("第一行"),
    ]))
    opt = make_optimizer(tmp_path)

    result = opt._get_best_ocr_result_from_sequence(make_frames(9))

    assert result[1]["rec_texts"] == ["第一行", "第二行"]


# ---------------------------------------------------------------------------
# b) 代表行投票：按 y 重叠对齐行槽
# ---------------------------------------------------------------------------

def _tl(text, box, score=0.99):
    x0, y0, x1, y1 = box
    return TextLine(text=text, score=score, box=box,
                    polygon=[(x0, y0), (x1, y0), (x1, y1), (x0, y1)])


def _sgroup(frames):
    frames = sorted(frames, key=lambda f: f.frame_num)
    return SubtitleGroup(start_frame=frames[0].frame_num, end_frame=frames[-1].frame_num,
                         lines=list(frames[0].lines), frames=list(frames))


def _make_gen(tmp_path):
    return OCRToASSOptimizer(
        video_path=str(tmp_path / "in.mp4"), output_path=str(tmp_path / "out.ass"),
        fps=24.0, width=1280, height=720,
    )


def test_representative_lines_align_by_position_on_count_tie(tmp_path):
    """行数并列众数且两帧行位置不同：按位置对齐投票，而不是按索引硬凑。"""
    gen = _make_gen(tmp_path)
    frames = []
    for i in range(4):
        lines = (
            [_tl("第一行", (100, 100, 500, 140)), _tl("第二行", (100, 200, 500, 240))]
            if i % 2 == 0 else
            [_tl("第二行", (100, 200, 500, 240)), _tl("第三行", (100, 300, 500, 340))]
        )
        frames.append(FrameData(frame_num=i, time_sec=i / 24.0, lines=lines))

    lines = gen._select_representative_lines(_sgroup(frames))

    # 行槽 0（y≈100）只有"第一行"的证据，行槽 1（y≈200）四帧全一致；
    # 旧行索引投票会把 (第一行,第二行) vs (第二行,第三行) 按槽位硬凑。
    assert [l.text for l in lines] == ["第一行", "第二行"]


def test_representative_lines_partial_frames_contribute_votes(tmp_path):
    """行数不足众数的帧也能为它实际读到的行槽提供证据。"""
    gen = _make_gen(tmp_path)
    frames = []
    for i in range(6):
        if i < 2:
            lines = [_tl("第二行", (100, 200, 500, 240))]
        else:
            lines = [_tl("第一行", (100, 100, 500, 140)), _tl("第二行", (100, 200, 500, 240))]
        frames.append(FrameData(frame_num=i, time_sec=i / 24.0, lines=lines))

    lines = gen._select_representative_lines(_sgroup(frames))

    assert [l.text for l in lines] == ["第一行", "第二行"]


# ---------------------------------------------------------------------------
# c) 场景文字候选合并
# ---------------------------------------------------------------------------

def _cand(text, bbox, hits=3, first=10, last=20):
    x1, y1, x2, y2 = bbox
    return {
        "text": text, "bbox": bbox, "hit_count": hits,
        "first_frame": first, "last_frame": last, "snapshot_jpeg": b"",
        "roi_entry": {
            "start_time": "0:00:00.30", "end_time": "0:00:00.60",
            "start_frame": first, "end_frame": last,
            "type": "rect", "points": [int(x1), int(y1), int(x2 - x1), int(y2 - y1)],
            "source": "auto", "text_filter_policy": "keep_all",
            "fade_in_refine_enabled": False, "band_region": "middle",
        },
    }


def test_scene_candidates_same_text_nearby_merge():
    cands = [
        _cand("郵件正文", (100.0, 100.0, 300.0, 140.0), hits=3, first=10, last=20),
        _cand("郵件正文", (150.0, 110.0, 350.0, 150.0), hits=2, first=30, last=40),
    ]
    merged = _merge_scene_candidates(cands, fps=10.0)

    assert len(merged) == 1
    m = merged[0]
    assert m["hit_count"] == 5
    assert m["bbox"] == (100.0, 100.0, 350.0, 150.0)
    assert m["first_frame"] == 10 and m["last_frame"] == 40
    # ROI 合并：矩形取并集，时间范围覆盖两个候选。
    assert m["roi_entry"]["points"] == [100, 100, 250, 50]
    assert m["roi_entry"]["start_frame"] == 10 and m["roi_entry"]["end_frame"] == 40
    assert m["roi_entry"]["start_time"] == "00:00:01.000"
    assert m["roi_entry"]["end_time"] == "00:00:04.000"


def test_scene_candidates_adjacent_same_text_merge():
    """不重叠但紧邻（间隙小于外扩容差）的同文本候选也合并。"""
    cands = [
        _cand("招牌", (100.0, 100.0, 300.0, 140.0)),
        _cand("招牌", (330.0, 100.0, 530.0, 140.0)),
    ]
    assert len(_merge_scene_candidates(cands, fps=10.0)) == 1


def test_scene_candidates_different_text_not_merged():
    cands = [
        _cand("主題", (100.0, 100.0, 300.0, 140.0)),
        _cand("正文", (100.0, 150.0, 300.0, 190.0)),
    ]
    merged = _merge_scene_candidates(cands, fps=10.0)
    assert len(merged) == 2
    assert {c["text"] for c in merged} == {"主題", "正文"}


def test_scene_candidates_same_text_far_apart_not_merged():
    cands = [
        _cand("郵件正文", (100.0, 100.0, 300.0, 140.0)),
        _cand("郵件正文", (900.0, 500.0, 1100.0, 540.0)),
    ]
    merged = _merge_scene_candidates(cands, fps=10.0)
    assert len(merged) == 2


# ---------------------------------------------------------------------------
# d) 模块级按位置融合函数（供运动轨迹脚本复用，优化器方法为纯委托）
# ---------------------------------------------------------------------------

def test_fuse_samples_by_position_missing_observation_not_negative():
    """模块级函数：某帧漏读一行只算缺失观测；一致观测不产生难帧。"""
    line_a = ("未遮挡行", 0.97, (40, 40, 280, 70))
    line_b = ("被遮挡行", 0.96, (40, 90, 280, 120))
    sample_results = [
        (None, _raw([line_a, line_b])),
        (None, _raw([line_a])),          # 中采样：手挡住 B 行 → 缺失观测
        (None, _raw([line_a, line_b])),
    ]
    anchor_aabbs = _line_aabbs(sample_results[0][1])

    best, hard_lines, candidates = fuse_samples_by_position(
        sample_results, anchor_aabbs, vlm_refine_min_confidence=0.6)

    assert best["rec_texts"] == ["未遮挡行", "被遮挡行"]
    # 缺失观测不投票、不计入分母：两处一致观测即确认，不送 VLM。
    assert hard_lines == []
    assert candidates == [["未遮挡行"], ["被遮挡行"]]


def test_fuse_samples_by_position_rescues_anchor_missing_line():
    """模块级函数：锚定帧漏读的行由其余采样帧按位置补为额外行槽。"""
    top = ("顶部标题", 0.97, (40, 30, 280, 60))
    mid = ("中间正文", 0.96, (40, 80, 280, 110))
    bottom = ("底部落款", 0.95, (40, 130, 280, 160))
    sample_results = [
        (None, _raw([top, bottom])),     # 锚定帧：漏读中间行
        (None, _raw([top, mid, bottom])),
        (None, _raw([top, mid, bottom])),
    ]
    anchor_aabbs = _line_aabbs(sample_results[0][1])

    best, hard_lines, candidates = fuse_samples_by_position(
        sample_results, anchor_aabbs, vlm_refine_min_confidence=0.6)

    # 被救回的行按 y 中心插回读取顺序，几何/分数来自提供证据的采样帧。
    assert best["rec_texts"] == ["顶部标题", "中间正文", "底部落款"]
    assert best["rec_scores"] == [0.97, 0.96, 0.95]
    assert best["dt_polys"][1][0] == [40.0, 80.0]
    assert hard_lines == []
    assert candidates == [["顶部标题"], ["中间正文"], ["底部落款"]]


def test_optimizer_method_delegates_to_module_function(tmp_path):
    """行为保持：优化器方法与模块级函数输出完全一致（纯委托）。"""
    box = (40, 40, 280, 70)
    sample_results = [
        (None, _raw([("讀法甲", 0.9, box)])),
        (None, _raw([("讀法乙", 0.9, box)])),
        (None, _raw([("讀法丙", 0.9, box)])),
    ]
    anchor_aabbs = _line_aabbs(sample_results[0][1])
    opt = make_optimizer(tmp_path)

    method_out = opt._fuse_samples_by_position(sample_results, anchor_aabbs)
    module_out = fuse_samples_by_position(
        sample_results, anchor_aabbs,
        vlm_refine_min_confidence=opt.vlm_refine_min_confidence,
    )

    # 三读法无多数 → 行级送审难帧，候选按序去重透传。
    assert module_out[0]["rec_texts"] == ["讀法甲"]
    assert module_out[1] == [0]
    assert module_out[2] == [["讀法甲", "讀法乙", "讀法丙"]]
    assert method_out == module_out
