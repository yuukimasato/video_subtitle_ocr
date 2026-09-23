# tests/test_text_alignment.py
"""原文对齐检测(core.text_alignment)与各 ASS 产出路径的锚点选用。

用户验收背景(2026-09-19 邮件画面):原画面文字左对齐,识别行此前一律
``\an5`` 锚行框中心,短行的左缘浮到中间、整块看起来全部居中。现按块检测
原文对齐方式选锚:左→``\an4`` 锚行框左缘、右→``\an6`` 锚右缘、中→
``\an5`` 锚中心(与旧行为一致;等宽/单行块归中,输出逐字节不变)。

覆盖三条产出路径:
- 主流水线 SCENE 分支(styling._determine_style_and_position,整组检测);
- 静态场景文字策略(apply_policy_static 的 text spec,逐块检测);
- 轨迹管线(synthesize_events,ref_box 并块检测,锚点随帧跟踪行框边缘)。
"""

from __future__ import annotations

import math
import os
import sys

import numpy as np
import pytest

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from core.text_alignment import (  # noqa: E402
    detect_line_alignments,
    merge_line_blocks,
)

# ── 逐行投票判定(detect_line_alignments)─────────────────────────


def test_detect_line_alignments_groups_by_block_and_keeps_input_order():
    # 输入乱序:块 A(左对齐,y 0-56)、块 B(右对齐,y 200+),中间大间隔分块
    boxes = [(210.0, 220.0, 300.0, 236.0),   # B 行 1(右对齐)
             (40.0, 40.0, 280.0, 56.0),      # A 行 2(左对齐)
             (40.0, 20.0, 120.0, 36.0),      # A 行 1
             (150.0, 240.0, 300.0, 256.0)]   # B 行 2(右缘对齐,左缘参差)
    assert detect_line_alignments(boxes) == [
        "right", "left", "left", "right"]


def test_voting_ignores_vertical_spacing_mail_body():
    # 邮件正文(验收截图 1):行距 ≥ 行高、行间不垂直相邻,左缘一致 → 仍判左。
    # 若按垂直相邻并块,每行会拆成孤块全部退回居中。
    boxes = [(460.0, 300.0, 1100.0, 320.0),
             (460.0, 350.0, 900.0, 370.0),
             (460.0, 400.0, 700.0, 420.0)]
    assert detect_line_alignments(boxes) == ["left"] * 3


def test_chat_mixed_sides_vote_per_line():
    # 聊天界面(验收截图 2):左列接收气泡与右列发送气泡混排,同屏两种
    # 对齐并存,逐行投票各归其位;孤行(三边都无贴合)归中。
    boxes = [
        (500.0, 350.0, 790.0, 370.0),  # 左列(左缘 500)
        (500.0, 390.0, 800.0, 410.0),  # 左列
        (700.0, 430.0, 810.0, 450.0),  # 右列(右缘 810)
        (660.0, 470.0, 810.0, 490.0),  # 右列(宽度不同,右缘对齐)
        (500.0, 510.0, 690.0, 530.0),  # 左列
        (540.0, 550.0, 700.0, 570.0),  # 孤行:各边缘都无贴合
    ]
    assert detect_line_alignments(boxes) == [
        "left", "left", "right", "right", "left", "center"]


def test_motion_plane_shear_detrend_recovers_right_column():
    # 轨迹管线 quad 展开平面(12.mp4 验收):手选 quad 偏差让右对齐列随 y
    # 线性漂移(0.15 px/px,36px/240px),绝对容差(0.35×行高=10.5)内
    # 首尾不贴合 → 不去趋势判不出右;去趋势后整列归右。
    boxes = []
    for k in range(7):
        y = 200.0 + 40.0 * k
        x2 = 500.0 + 0.15 * (y - 200.0)
        x1 = x2 - (60 + 13 * (k % 3))   # 行长不一 → 左/中缘不贴合
        boxes.append((x1, y, x2, y + 30.0))
    plain = detect_line_alignments(boxes)
    assert set(plain) != {"right"}
    detrended = detect_line_alignments(boxes, detrend_shear=True)
    assert set(detrended) == {"right"}


def test_detrend_shear_noop_on_straight_columns():
    # 平面无剪切(所有成对斜率≈0)时开关等价于不去趋势:邮件式左对齐仍判左
    boxes = [(460.0, 300.0, 1100.0, 320.0),
             (460.0, 350.0, 900.0, 370.0),
             (460.0, 400.0, 700.0, 420.0)]
    assert (detect_line_alignments(boxes, detrend_shear=True)
            == detect_line_alignments(boxes) == ["left"] * 3)


def test_static_style_shear_needs_detrend_to_vote_left():
    # 静态路径同款剪切数据(斜放平面的竖直列随 y 线性漂移):不去趋势时
    # 左缘参差判不出 left;去趋势后整列归左(与轨迹管线同一修复)。
    boxes = [(100.0, 40.0, 200.0, 60.0),
             (112.0, 80.0, 252.0, 100.0),
             (124.0, 120.0, 214.0, 140.0),
             (136.0, 160.0, 256.0, 180.0)]
    plain = detect_line_alignments(boxes)
    assert set(plain) != {"left"}
    assert detect_line_alignments(boxes, detrend_shear=True) == ["left"] * 4


# ── 边界情形加固(n<2 / 退化框 / 非有限坐标)─────────────────────


def test_voting_tiny_inputs_and_two_row_pairs():
    # n=0/1 无可判对齐 → 归中;n=2 贴合边互投多数票;等宽两行三边同票 → 归中
    assert detect_line_alignments([]) == []
    assert detect_line_alignments([(10.0, 0.0, 50.0, 16.0)]) == ["center"]
    pair = [(40.0, 0.0, 120.0, 16.0), (40.0, 20.0, 200.0, 36.0)]
    assert detect_line_alignments(pair) == ["left", "left"]
    equal = [(40.0, 0.0, 120.0, 16.0), (40.0, 20.0, 120.0, 36.0)]
    assert detect_line_alignments(equal) == ["center", "center"]


def test_zero_height_rows_fall_back_to_center_without_raising():
    # 全零框(avg_h ≤ 0)整组归中;混入零高行不抛异常,正常行照常判定
    assert detect_line_alignments([(0.0, 0.0, 0.0, 0.0)] * 3) == ["center"] * 3
    mixed = [(40.0, 20.0, 120.0, 36.0), (40.0, 50.0, 80.0, 50.0),
             (40.0, 60.0, 280.0, 76.0)]
    assert detect_line_alignments(mixed) == ["left", "left", "left"]


def test_non_finite_rows_centered_and_excluded_from_voting():
    # NaN/inf 行归中且不参与投票(不污染均值/容差),其余行按有限坐标判定;
    # 全部坐标非有限(或畸形)时整组无有效行可判 → 归中,不抛异常。
    nan, inf = float("nan"), float("inf")
    boxes = [(40.0, 20.0, 120.0, 36.0),
             (nan, 0.0, inf, 16.0),
             (40.0, 60.0, 280.0, 76.0)]
    assert detect_line_alignments(boxes) == ["left", "center", "left"]
    assert detect_line_alignments([None, (40.0, 20.0, 120.0, 36.0)]) == [
        "center", "center"]


# ── 诊断可见性(逐行判定结果带出)────────────────────────────────


def test_detect_line_alignments_diagnostics_reports_votes():
    # 仅关键字参数 diagnostics 带出行 idx → 对齐边 + 三边票数,不影响返回值
    boxes = [
        (500.0, 350.0, 790.0, 370.0),  # 左列
        (500.0, 390.0, 800.0, 410.0),  # 左列
        (700.0, 430.0, 810.0, 450.0),  # 右列
        (660.0, 470.0, 810.0, 490.0),  # 右列
        (500.0, 510.0, 690.0, 530.0),  # 左列
        (540.0, 550.0, 700.0, 570.0),  # 孤行(三边各 1 票 → 归中)
    ]
    diag: dict = {}
    aligns = detect_line_alignments(boxes, diagnostics=diag)
    assert aligns == detect_line_alignments(boxes)
    assert diag["shear_slope"] == 0.0
    assert diag["shear_slope_reason"] == "detrend_off"
    assert diag["rows"] == [
        {"row": 0, "align": "left", "votes": {"left": 3, "center": 2, "right": 1}},
        {"row": 1, "align": "left", "votes": {"left": 3, "center": 2, "right": 1}},
        {"row": 2, "align": "right", "votes": {"left": 1, "center": 1, "right": 2}},
        {"row": 3, "align": "right", "votes": {"left": 1, "center": 1, "right": 2}},
        {"row": 4, "align": "left", "votes": {"left": 3, "center": 1, "right": 1}},
        {"row": 5, "align": "center", "votes": {"left": 1, "center": 1, "right": 1}},
    ]


def test_detect_line_alignments_diagnostics_reports_shear_slope():
    # 去趋势启用时带出估计的剪切斜率;非有限行 votes 全 0、align 归中
    boxes = []
    for k in range(5):
        y = 200.0 + 40.0 * k
        x1 = 300.0 + 0.15 * (y - 200.0)
        boxes.append((x1, y, x1 + 60.0 + 13.0 * (k % 3), y + 30.0))
    diag: dict = {}
    aligns = detect_line_alignments(boxes, detrend_shear=True, diagnostics=diag)
    assert aligns == ["left"] * 5
    assert diag["shear_slope"] == pytest.approx(0.15, abs=1e-6)
    assert diag["shear_slope_reason"].startswith("adopted")
    nan_diag: dict = {}
    detect_line_alignments(
        [(40.0, 20.0, 120.0, 36.0), (float("nan"), 0.0, 80.0, 16.0)],
        diagnostics=nan_diag)
    assert nan_diag["rows"][1] == {
        "row": 1, "align": "center",
        "votes": {"left": 0, "center": 0, "right": 0}}


def test_shear_slope_dominance_check_rejects_cross_column_noise():
    # 占优度检验(对齐判错主源):无真实剪切的混排布局(y 跨度大,同列
    # 左缘配对超出斜率窗口)里,3 对跨列噪声斜率同落一 bin 即成最大 bin。
    # 检验前该噪声斜率(≈-0.07)会被采纳,y 跨 1000px 去趋势把整组推成
    # 全 center;检验后(best < max(3, 1/4×总对数))斜率按 0 处理,投票
    # 与不去趋势一致。
    boxes = [(100.0, 0.0, 904.0, 20.0),
             (240.0, 200.0, 800.0, 220.0),
             (146.0, 800.0, 800.0, 820.0),
             (100.0, 1000.0, 820.0, 1020.0)]
    diag: dict = {}
    detrended = detect_line_alignments(boxes, detrend_shear=True,
                                       diagnostics=diag)
    assert detrended == detect_line_alignments(boxes) == [
        "left", "right", "right", "left"]
    assert diag["shear_slope"] == 0.0
    assert diag["shear_slope_reason"] == "insufficient_dominance(best=3/total=13)"


def test_shear_slope_dominance_check_adopts_strong_real_shear():
    # 强真实剪切(整列共享同一斜率,最大 bin 占绝对多数)仍被采纳
    boxes = []
    for k in range(7):
        y = 200.0 + 40.0 * k
        x2 = 500.0 + 0.15 * (y - 200.0)
        x1 = x2 - (60 + 13 * (k % 3))
        boxes.append((x1, y, x2, y + 30.0))
    diag: dict = {}
    assert detect_line_alignments(boxes, detrend_shear=True,
                                  diagnostics=diag) == ["right"] * 7
    assert diag["shear_slope"] == pytest.approx(0.15)
    assert diag["shear_slope_reason"] == "adopted(best=28/total=56)"


def test_detect_line_alignments_diagnostics_reports_fit_stats():
    # 诊断补全:平均行高/贴合容差/有效行数/被排除行 idx(只增键)
    boxes = [(100.0, 0.0, 904.0, 20.0),
             (240.0, 200.0, 800.0, 220.0),
             (146.0, 800.0, 800.0, 820.0),
             (100.0, 1000.0, 820.0, 1020.0)]
    diag: dict = {}
    detect_line_alignments(boxes, diagnostics=diag)
    assert diag["avg_h"] == pytest.approx(20.0)
    assert diag["tol"] == pytest.approx(7.0)  # 0.35 × avg_h
    assert diag["n_valid"] == 4
    assert diag["excluded_rows"] == []
    nan_diag: dict = {}
    detect_line_alignments(
        [(40.0, 20.0, 120.0, 36.0), (float("nan"), 0.0, 80.0, 16.0),
         (None, 0, 0, 0)],
        diagnostics=nan_diag)
    assert nan_diag["n_valid"] == 1
    assert nan_diag["excluded_rows"] == [1, 2]


# ── 早退路径诊断键完整性与诚实取值 ─────────────────────────────

_FIXED_DIAG_KEYS = ("shear_slope", "shear_slope_reason", "avg_h", "tol",
                    "n_valid", "excluded_rows", "min_vote_margin",
                    "low_margin_rows", "rows")


def test_diagnostics_keys_complete_on_every_return_path():
    # 早退路径(n<2 行 / 坐标全非有限 / 混合)此前缺 avg_h 与 tol 两个键、
    # 也不写 shear_slope_reason:消费方 .get() 打日志出 None,分不清「没
    # 估计」与「没采纳」;直接取 diag["tol"] 的调用方 KeyError。现固定键
    # 在所有返回路径都写入,未估计的数值是 NaN(键在、值不编造)。
    nan, inf = float("nan"), float("inf")
    for boxes, n_valid, excluded in (
            ([], 0, []),
            ([(10.0, 0.0, 50.0, 16.0)], 1, []),
            ([(nan, 0.0, inf, 16.0), (None, 0, 0, 0)], 0, [0, 1])):
        diag: dict = {}
        detect_line_alignments(boxes, diagnostics=diag)
        assert all(k in diag for k in _FIXED_DIAG_KEYS), (boxes, sorted(diag))
        assert diag["shear_slope_reason"] == "no_rows"
        assert diag["shear_slope"] == 0.0
        assert math.isnan(diag["avg_h"]) and math.isnan(diag["tol"])
        assert diag["avg_h"] != 0.0 and diag["tol"] != 0.0  # 未估计不冒充 0
        assert diag["n_valid"] == n_valid
        assert diag["excluded_rows"] == excluded
        assert diag["min_vote_margin"] == 1
        assert diag["low_margin_rows"] == []
    # 开启去趋势也走同一条早退:斜率根本没估计,原因仍是 no_rows(不回退
    # 成估计器的 too_few_rows——那会误导成「估计过但样本不足」)。
    detrend_diag: dict = {}
    detect_line_alignments([], detrend_shear=True, diagnostics=detrend_diag)
    assert all(k in detrend_diag for k in _FIXED_DIAG_KEYS)
    assert detrend_diag["shear_slope_reason"] == "no_rows"
    assert math.isnan(detrend_diag["tol"])
    # 全非有限:有效行 0、被排除行如实列出
    excluded_diag: dict = {}
    detect_line_alignments([(nan, 0.0, inf, 16.0), (None, 0, 0, 0)],
                           diagnostics=excluded_diag)
    assert excluded_diag["n_valid"] == 0
    assert excluded_diag["excluded_rows"] == [0, 1]
    assert excluded_diag["shear_slope_reason"] == "no_rows"


def test_diagnostics_degenerate_avg_h_honest_and_tol_unset():
    # 退化几何(平均行高 ≤ 0):avg_h 是**真实算出**的均值(0.0 / 负数,
    # 如实上报),tol 按 0 行高无意义 → NaN(未估计),原因 degenerate。
    flat: dict = {}
    assert detect_line_alignments([(0.0, 0.0, 0.0, 0.0)] * 2,
                                  diagnostics=flat) == ["center", "center"]
    assert all(k in flat for k in _FIXED_DIAG_KEYS)
    assert flat["shear_slope_reason"] == "degenerate"
    assert flat["avg_h"] == 0.0
    assert math.isnan(flat["tol"])
    assert flat["n_valid"] == 2 and flat["excluded_rows"] == []
    # 行高为负(y2 < y1,畸形框):均值照实上报为负,不钳成 0
    inverted: dict = {}
    detect_line_alignments([(10.0, 30.0, 50.0, 20.0)] * 2,
                           diagnostics=inverted)
    assert inverted["shear_slope_reason"] == "degenerate"
    assert inverted["avg_h"] == pytest.approx(-10.0)
    assert math.isnan(inverted["tol"])


def test_singular_detect_line_alignment_api_removed():
    # 孤立单数 API(逐块整组取极差最小边)与逐行投票路径功能重叠且生产
    # 代码零调用点,已移除:模块不再有该属性、__all__ 无条目、docstring
    # 不提(防再次被当成对齐入口误用)。
    import re

    import core.text_alignment as ta
    assert not hasattr(ta, "detect_line_alignment")
    assert "detect_line_alignment" not in ta.__all__
    assert re.search(r"detect_line_alignment(?!s)", ta.__doc__ or "") is None


# ── 低置信边际检查(min_vote_margin)────────────────────────────


def test_low_margin_undecided_tie_falls_back_to_center_with_diagnostics():
    # 无证据并列回退 center:行 A 与 B 共享左缘、与 C 共享右缘(left 2 =
    # right 2 > center 1,两簇极差同为 0,极差打不开并列)——旧行为按
    # 中 > 左 > 右 偏好把这种零证据并列硬判成 left;现边际 0 < 默认 1,
    # 显式回退 center 并在诊断留痕。B/C 各自的 2 票是严格多数(边际 1),
    # 照常判 left/right——低置信检查不伤真对齐。
    boxes = [(100.0, 0.0, 200.0, 20.0),
             (100.0, 30.0, 180.0, 50.0),
             (120.0, 60.0, 200.0, 80.0)]
    diag: dict = {}
    assert detect_line_alignments(boxes, diagnostics=diag) == [
        "center", "left", "right"]
    assert diag["min_vote_margin"] == 1
    assert diag["low_margin_rows"] == [0]
    assert diag["rows"][0] == {
        "row": 0, "align": "center",
        "votes": {"left": 2, "center": 1, "right": 2},
        "low_margin": True, "rejected_align": "left"}
    assert all("low_margin" not in r for r in diag["rows"][1:])


def test_low_margin_min_vote_margin_zero_restores_legacy_behavior():
    # min_vote_margin=0 关闭检查:同一并列按旧行为(极差并列 → 偏好序)
    # 判 left,诊断无回退留痕——旧行为完全可恢复。
    boxes = [(100.0, 0.0, 200.0, 20.0),
             (100.0, 30.0, 180.0, 50.0),
             (120.0, 60.0, 200.0, 80.0)]
    diag: dict = {}
    assert detect_line_alignments(boxes, min_vote_margin=0,
                                  diagnostics=diag) == ["left", "left", "right"]
    assert diag["min_vote_margin"] == 0
    assert diag["low_margin_rows"] == []
    assert all("low_margin" not in r for r in diag["rows"])


def test_low_margin_spread_decided_tie_counts_as_margin_one():
    # 票数三边并列(3/3/3)但左缘残差全重合(极差 0)、中/右缘被容差桥接
    # (极差 2.5/5):极差严格打破并列 → 证据强度记 1,默认阈值下仍判
    # left(去趋势剪切列「票数并列 + 极差判真列」同一机制,回归基准不得
    # 翻转);阈值收紧到 2 时该 1 级证据也回退 center。
    boxes = [(100.0, 0.0, 200.0, 20.0),
             (100.0, 30.0, 200.0, 50.0),
             (100.0, 60.0, 205.0, 80.0)]
    assert detect_line_alignments(boxes) == ["left"] * 3
    assert detect_line_alignments(boxes, min_vote_margin=2) == ["center"] * 3


def test_low_margin_default_one_keeps_strict_plurality_and_center_rows():
    # 默认阈值 1 的标定锚点:2 行全左小组(每行 left 2 票 vs 次高 1 票,
    # 边际恰为 1)必须采纳——阈值 ≥2 会把真对齐小组也回退 center(功能
    # 失效);等宽两行三边同票(2/2/2)是偏好序归中,非「回退」——不留
    # low_margin 痕;n<2 早退路径也带出边际阈值键。
    pair = [(40.0, 0.0, 120.0, 16.0), (40.0, 20.0, 200.0, 36.0)]
    diag: dict = {}
    assert detect_line_alignments(pair, diagnostics=diag) == ["left", "left"]
    assert diag["low_margin_rows"] == []
    assert all("low_margin" not in r for r in diag["rows"])
    equal = [(40.0, 0.0, 120.0, 16.0), (40.0, 20.0, 120.0, 36.0)]
    ediag: dict = {}
    assert detect_line_alignments(equal, diagnostics=ediag) == [
        "center", "center"]
    assert ediag["low_margin_rows"] == []
    tiny: dict = {}
    detect_line_alignments([(10.0, 0.0, 50.0, 16.0)], diagnostics=tiny)
    assert tiny["min_vote_margin"] == 1
    assert tiny["low_margin_rows"] == []


def test_merge_line_blocks_reexport_from_scene_text_policy():
    # merge_line_blocks 实现迁至 core.text_alignment,旧导入路径保持不变
    from core.scene_text_policy import merge_line_blocks as via_policy
    boxes = [(20.0, 20.0, 200.0, 36.0),
             (20.0, 60.0, 200.0, 76.0),
             (20.0, 80.0, 200.0, 96.0)]
    assert via_policy is merge_line_blocks
    assert merge_line_blocks(boxes) == [(0, 0), (1, 2)]


# ── 主流水线 SCENE 分支 ─────────────────────────────────────────


def _text_line(text, box):
    from core.subtitle_generator.models import TextLine
    poly = [[box[0], box[1]], [box[2], box[1]],
            [box[2], box[3]], [box[0], box[3]]]
    return TextLine(text=text, score=0.95, box=tuple(box), polygon=poly)


def _scene_group(lines):
    from core.subtitle_generator.models import SubtitleGroup
    return SubtitleGroup(start_frame=0, end_frame=10, lines=lines)


def _make_optimizer(tmp_path):
    from core.subtitle_generator import OCRToASSOptimizer
    return OCRToASSOptimizer(
        video_path=str(tmp_path / "in.mp4"),
        output_path=str(tmp_path / "out.ass"),
        fps=25.0, width=1280, height=720)


def test_scene_group_left_aligned_uses_an4_at_box_left_edge(tmp_path):
    conv = _make_optimizer(tmp_path)
    lines = [_text_line("受信メール一覧", (460, 300, 800, 320)),
             _text_line("久しぶり。", (460, 340, 560, 360)),
             _text_line("早速だけど。", (460, 380, 1100, 400))]
    out = conv._determine_style_and_position(_scene_group(lines))
    assert [d["tags"] for d in out] == [
        "{\\an4\\pos(460,310)\\fs20}",
        "{\\an4\\pos(460,350)\\fs20}",
        "{\\an4\\pos(460,390)\\fs20}",
    ]


def test_scene_group_right_aligned_uses_an6_at_box_right_edge(tmp_path):
    conv = _make_optimizer(tmp_path)
    lines = [_text_line("右一", (400, 300, 700, 320)),
             _text_line("右边第二行更长", (200, 340, 700, 360))]
    out = conv._determine_style_and_position(_scene_group(lines))
    assert all(d["tags"].startswith("{\\an6\\pos(700,") for d in out)


def test_scene_group_chat_mixed_sides_pos_pipeline(tmp_path):
    # \pos 主流水线:聊天混排屏幕逐行各归其位(左列 \an4 / 右列 \an6)
    conv = _make_optimizer(tmp_path)
    lines = [_text_line("左一", (500, 350, 790, 370)),
             _text_line("右一", (700, 430, 810, 450)),
             _text_line("右二更长些", (660, 470, 810, 490)),
             _text_line("左二", (500, 510, 690, 530))]
    out = conv._determine_style_and_position(_scene_group(lines))
    assert [d["tags"] for d in out] == [
        "{\\an4\\pos(500,360)\\fs20}",
        "{\\an6\\pos(810,440)\\fs20}",
        "{\\an6\\pos(810,480)\\fs20}",
        "{\\an4\\pos(500,520)\\fs20}",
    ]


def test_scene_group_sheared_long_group_detrend_votes_left(tmp_path):
    # 斜放版式回归(≥5 行):SCENE 组按行数启用剪切去趋势——竖直列随 y
    # 线性漂移(slope 0.3),不去趋势整组误归中;≥5 行才开(样本充足),
    # 开后各行锚自身(漂移后的)行框左缘。
    conv = _make_optimizer(tmp_path)
    lines = []
    for k in range(5):
        y = 200 + 40 * k
        x1 = 300 + 0.3 * (y - 200)
        lines.append(_text_line(f"行{k}", (x1, y, x1 + 60 + 13 * (k % 3), y + 30)))
    out = conv._determine_style_and_position(_scene_group(lines))
    assert [d["tags"] for d in out] == [
        "{\\an4\\pos(300,215)\\fs30}",
        "{\\an4\\pos(312,255)\\fs30}",
        "{\\an4\\pos(324,295)\\fs30}",
        "{\\an4\\pos(336,335)\\fs30}",
        "{\\an4\\pos(348,375)\\fs30}",
    ]


def test_scene_group_equal_widths_keeps_an5_center(tmp_path):
    conv = _make_optimizer(tmp_path)
    lines = [_text_line("甲甲甲", (400, 300, 600, 320)),
             _text_line("乙乙乙", (400, 340, 600, 360))]
    out = conv._determine_style_and_position(_scene_group(lines))
    assert [d["tags"] for d in out] == [
        "{\\an5\\pos(500,310)\\fs20}",
        "{\\an5\\pos(500,350)\\fs20}",
    ]


def test_scene_group_debug_log_reports_line_alignments(tmp_path, caplog):
    # DEBUG 级日志带出逐行判定结果(排查「为什么判成 left」),INFO 下静默
    import logging

    conv = _make_optimizer(tmp_path)
    lines = [_text_line("受信メール一覧", (460, 300, 800, 320)),
             _text_line("久しぶり。", (460, 340, 560, 360)),
             _text_line("早速だけど。", (460, 380, 1100, 400))]
    with caplog.at_level(logging.INFO, logger="core.subtitle_generator"):
        conv._determine_style_and_position(_scene_group(lines))
    assert not any("Scene line alignments" in r.message
                   for r in caplog.records)
    with caplog.at_level(logging.DEBUG, logger="core.subtitle_generator"):
        conv._determine_style_and_position(_scene_group(lines))
    assert any("Scene line alignments" in r.message
               for r in caplog.records)


# ── 静态场景文字策略(text spec)─────────────────────────────────

ROWS_LEFT = [
    ("第一行短", (40.0, 20.0, 120.0, 36.0)),
    ("第二行比第一个长不少", (40.0, 40.0, 280.0, 56.0)),
    ("短", (40.0, 60.0, 70.0, 76.0)),
]


def _white_plane():
    return np.full((400, 300, 3), 250, np.uint8)


def test_static_mask_text_specs_left_block_anchor_at_left_edge():
    from core.scene_text_policy import SceneTextPolicyConfig, apply_policy_static
    specs, applied, _notes = apply_policy_static(
        ROWS_LEFT, _white_plane(), SceneTextPolicyConfig(mode="mask"),
        300.0, 400.0)
    assert applied == "mask"
    texts = [s for s in specs if s["kind"] == "text"]
    assert [s["tags"] for s in texts] == [
        "{\\an4\\pos(40,28)\\fs16}",
        "{\\an4\\pos(40,48)\\fs16}",
        "{\\an4\\pos(40,68)\\fs16}",
    ]


def test_static_mask_chat_mixed_sides_votes_per_line():
    # mask 静态路径:同屏左右两种对齐逐行判定(与主流水线同一投票,
    # 与遮罩并块无关);对称外扩的遮罩框同时盖住左/右锚渲染的文本。
    from core.scene_text_policy import SceneTextPolicyConfig, apply_policy_static
    plane = np.full((600, 900, 3), 250, np.uint8)
    rows = [("左一", (500.0, 350.0, 790.0, 370.0)),
            ("右一", (700.0, 430.0, 810.0, 450.0)),
            ("右二更长些", (660.0, 470.0, 810.0, 490.0)),
            ("左二", (500.0, 510.0, 690.0, 530.0))]
    specs, applied, _notes = apply_policy_static(
        rows, plane, SceneTextPolicyConfig(mode="mask"), 900.0, 600.0)
    assert applied == "mask"
    texts = [s for s in specs if s["kind"] == "text"]
    assert [s["tags"] for s in texts] == [
        "{\\an4\\pos(500,360)\\fs20}",
        "{\\an6\\pos(810,440)\\fs20}",
        "{\\an6\\pos(810,480)\\fs20}",
        "{\\an4\\pos(500,520)\\fs20}",
    ]


def test_static_mask_single_row_keeps_an5_center():
    # 单行块归中:旧行为逐字节不变
    from core.scene_text_policy import SceneTextPolicyConfig, apply_policy_static
    specs, _applied, _notes = apply_policy_static(
        ROWS_LEFT[:1], _white_plane(), SceneTextPolicyConfig(mode="mask"),
        300.0, 400.0)
    texts = [s for s in specs if s["kind"] == "text"]
    assert texts[0]["tags"] == "{\\an5\\pos(80,28)\\fs16}"


def test_static_mask_only_comment_lines_keep_alignment():
    # mask_only 的 Comment 参考行与 mask 文本行同锚点逻辑
    from core.scene_text_policy import SceneTextPolicyConfig, apply_policy_static
    specs, applied, _notes = apply_policy_static(
        ROWS_LEFT, _white_plane(), SceneTextPolicyConfig(mode="mask_only"),
        300.0, 400.0)
    assert applied == "mask_only"
    texts = [s for s in specs if s["kind"] == "text"]
    assert all(s["tags"].startswith("{\\an4\\pos(40,") for s in texts)
    assert all(s.get("comment") for s in texts)


def test_static_mask_sheared_rows_detrend_votes_left():
    # 静态 mask 路径行框带线性剪切(斜放平面的竖直列随 y 漂移):与轨迹
    # 管线同一去趋势开关投票后仍判左,各行锚自身(漂移后的)左缘。
    from core.scene_text_policy import SceneTextPolicyConfig, apply_policy_static
    plane = np.full((220, 320, 3), 250, np.uint8)
    rows = [("第一行", (100.0, 40.0, 200.0, 60.0)),
            ("第二行更长一些", (112.0, 80.0, 252.0, 100.0)),
            ("三", (124.0, 120.0, 214.0, 140.0)),
            ("第四行内容", (136.0, 160.0, 256.0, 180.0))]
    specs, applied, _notes = apply_policy_static(
        rows, plane, SceneTextPolicyConfig(mode="mask"), 320.0, 220.0)
    assert applied == "mask"
    texts = [s for s in specs if s["kind"] == "text"]
    assert [s["tags"] for s in texts] == [
        "{\\an4\\pos(100,50)\\fs20}",
        "{\\an4\\pos(112,90)\\fs20}",
        "{\\an4\\pos(124,130)\\fs20}",
        "{\\an4\\pos(136,170)\\fs20}",
    ]


def test_static_mask_diagnostics_carry_line_alignments():
    # 逐行判定结果并入 PolicyResult.diagnostics["line_alignments"]
    from core.scene_text_policy import SceneTextPolicyConfig, apply_policy_static
    res = apply_policy_static(
        ROWS_LEFT, _white_plane(), SceneTextPolicyConfig(mode="mask"),
        300.0, 400.0)
    diag = res.diagnostics["line_alignments"]
    assert diag["shear_slope"] == 0.0
    assert [r["align"] for r in diag["rows"]] == ["left"] * 3
    assert diag["rows"][0]["votes"] == {"left": 3, "center": 1, "right": 1}


def test_static_whitespace_diagnostics_carry_source_line_alignments():
    # apply_policy_static docstring 契约:whitespace 路径 diagnostics 也有
    # line_alignments——这是**源行框**的判定留痕;单块放置按约定固定
    # \an4 左对齐于带左缘,放置锚点不受该判定影响。
    from core.scene_text_policy import SceneTextPolicyConfig, apply_policy_static
    rows = [("标题", (20.0, 20.0, 200.0, 36.0)),
            ("正文一", (20.0, 60.0, 200.0, 76.0)),
            ("正文二", (20.0, 80.0, 200.0, 96.0))]
    plane = _white_plane()
    for _text, (x1, y1, x2, _y2) in rows:  # 行框内画细墨条,供空白带检测
        plane[int(y1) + 4:int(y1) + 8, int(x1) + 4:int(x2) - 4] = 10
    res = apply_policy_static(
        rows, plane, SceneTextPolicyConfig(mode="whitespace"), 300.0, 400.0)
    assert res.applied_mode == "whitespace"
    assert res.events and res.events[0]["tags"].startswith("{\\an4\\pos(")
    diag = res.diagnostics["line_alignments"]
    # 源行等宽(三边极差同时为 0)→ 逐行判定归中,但放置锚点仍是 \an4
    assert [r["align"] for r in diag["rows"]] == ["center"] * 3
    assert diag["n_valid"] == 3


# ── 轨迹管线(synthesize_events)────────────────────────────────


def test_synthesize_events_left_block_tracks_left_edge():
    from core.motion_ass import (MotionAssConfig, build_line_tracks,
                                 synthesize_events)
    from test_motion_ass import translation_tracks
    tracks = translation_tracks(10, dx=2.0)
    boxes = [(100.0, 200.0, 300.0, 216.0),   # 紧凑行距 → 同块,左对齐
             (100.0, 220.0, 220.0, 236.0)]
    lts = build_line_tracks(boxes, ["一行短", "第二行长一些"], tracks,
                            ref_frame=0)
    events = synthesize_events(lts, tracks, MotionAssConfig())
    assert events and all("{\\an4" in ev["tags"] for ev in events)
    for ev in events:
        m = _move_points(ev["tags"])
        # 未平滑的原始位姿:行框左缘中点随平移轨迹 +2px/帧(帧 0 → 帧 9)
        assert m[0] == pytest.approx(100.0, abs=1e-6)
        assert m[1] == pytest.approx(m[3], abs=1e-6)  # 行内 y 不变
        assert m[2] == pytest.approx(118.0, abs=1e-6)


def test_synthesize_events_chat_mixed_sides():
    # 轨迹管线(验收截图 2:倾斜手机屏幕的聊天界面):左/右列逐行各归其位,
    # 锚点随帧跟踪(此处帧 0 原始位姿,平移轨迹)
    from core.motion_ass import (MotionAssConfig, build_line_tracks,
                                 synthesize_events)
    from test_motion_ass import translation_tracks
    tracks = translation_tracks(10, dx=2.0)
    boxes = [
        (500.0, 350.0, 790.0, 370.0),  # 左列(行距大,不垂直相邻)
        (700.0, 430.0, 810.0, 450.0),  # 右列
        (660.0, 470.0, 810.0, 490.0),  # 右列(右缘对齐)
        (500.0, 510.0, 690.0, 530.0),  # 左列
    ]
    lts = build_line_tracks(boxes, ["左一", "右一", "右二更长些", "左二"],
                            tracks, ref_frame=0)
    events = synthesize_events(lts, tracks, MotionAssConfig())
    by_line = {ev["line_idx"]: ev for ev in events}
    expect = {0: (4, 500.0), 1: (6, 810.0), 2: (6, 810.0), 3: (4, 500.0)}
    for idx, (an, ax) in expect.items():
        ev = by_line[idx]
        assert f"\\an{an}" in ev["tags"], ev
        m = _move_points(ev["tags"])
        assert m[0] == pytest.approx(ax, abs=1e-6), ev


def test_synthesize_events_single_line_keeps_an5_center():
    from core.motion_ass import (MotionAssConfig, build_line_tracks,
                                 synthesize_events)
    from test_motion_ass import translation_tracks
    tracks = translation_tracks(10, dx=2.0)
    lts = build_line_tracks([(100.0, 200.0, 300.0, 216.0)], ["单行"], tracks,
                            ref_frame=0)
    events = synthesize_events(lts, tracks, MotionAssConfig())
    assert events and all(ev["tags"].startswith("{\\an5\\fs16") for ev in events)


def test_synthesize_events_punct_offset_follows_line_direction():
    # 行尾标点补偿与 \an4 锚点同沿行方向:文本随轨迹旋转 30° 后,补偿按
    # (x_off·cos30, x_off·sin30) 分解,不再按屏幕 x 平移(旧近似在斜边行
    # 欠/过补偿);帧 0 无旋转,与旧行为一致。
    import math

    from core.motion_ass import (MotionAssConfig, build_line_tracks,
                                 synthesize_events)
    from test_motion_ass import (BOX_CENTER, make_tracks, rotation_about,
                                 translation)
    ang = 30.0
    tracks = make_tracks([translation(),
                          rotation_about(*BOX_CENTER, ang)])
    boxes = [(100.0, 100.0, 200.0, 150.0),   # 同块左对齐(左缘一致)
             (100.0, 160.0, 240.0, 200.0)]
    lts = build_line_tracks(boxes, ["あいう。", "第二行"], tracks, ref_frame=0)
    events = synthesize_events(lts, tracks, MotionAssConfig())
    m = _move_points(events[0]["tags"])
    x_off = 7.0  # punct_comp_offset_px("あ。", fs=50) = min(7, 0.25×50)
    # 帧 0 角度 0:锚 = 中心 + (−半框宽 + x_off)·(cos 0, sin 0)
    assert m[0] == pytest.approx(150.0 - 50.0 + x_off, abs=1e-6)
    assert m[1] == pytest.approx(125.0, abs=1e-6)
    # 帧 1 行角度 30°:半框宽与标点补偿同沿行方向(标签坐标 1 位小数)
    along = -50.0 + x_off
    assert m[2] == pytest.approx(150.0 + along * math.cos(math.radians(ang)),
                                 abs=0.05)
    assert m[3] == pytest.approx(125.0 + along * math.sin(math.radians(ang)),
                                 abs=0.05)


def test_synthesize_events_diagnostics_reports_per_line_aligns():
    # diagnostics 仅关键字参数带出逐行判定结果,不影响事件输出
    from core.motion_ass import (MotionAssConfig, build_line_tracks,
                                 synthesize_events)
    from test_motion_ass import translation_tracks
    tracks = translation_tracks(6)
    boxes = [(100.0, 200.0, 300.0, 216.0),   # 紧凑行距 → 左对齐块
             (100.0, 220.0, 220.0, 236.0)]
    lts = build_line_tracks(boxes, ["一行短", "第二行长一些"], tracks,
                            ref_frame=0)
    diag: dict = {}
    events = synthesize_events(lts, tracks, MotionAssConfig(), diagnostics=diag)
    assert events
    assert diag["shear_slope"] == 0.0
    assert [r["align"] for r in diag["rows"]] == ["left", "left"]
    assert diag["rows"][0]["votes"] == {"left": 2, "center": 1, "right": 1}


def _move_points(tags: str):
    import re
    m = re.search(r"\\move\((-?[\d.]+),(-?[\d.]+),(-?[\d.]+),(-?[\d.]+)", tags)
    assert m, tags
    return [float(g) for g in m.groups()]
