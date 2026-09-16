# tests/test_pipeline_scene_text_policy.py
r"""Task 4:统一静态路径的 ROI 策略语义和读取缓存。

覆盖(先红后绿):
- 混合 ROI 策略逐 ROI 处理:两个 ROI 分别配 mask/external 时各自产出
  策略事件,不静默取第一个策略;
- 未知策略名在管线收集入口(pipeline_worker.collect_roi_scene_text_options,
  CLI 静态路径与 GUI 主流水线共用)明确报 ValueError;CLI 旗标由 argparse
  choices 拒绝;
- CLI 显式策略覆盖 ROI JSON、CLI 缺省 overlap 时保留 ROI 配置(优先级
  固定,pipeline_worker.apply_cli_scene_text_policy);
- 策略 ROI 里混合 Scene/对白行:对白行保持原样、场景行走策略,两类都不
  丢失(不被 ROI 画像过滤静默丢弃);
- 多 ROI 只复用视频读取:单个 VideoCapture + 按帧号缓存(FrameReader),
  打开/seek 次数不随 ROI 数线性增长,读取结果与独立读取逐字节一致;
- 分析图时间邻域多帧中位合成为可选模式,默认单帧且静态视频下两种模式
  输出逐字节一致。

视频与 OCR 条目的构造方法与 test_scene_text_policy_gui.py 同构
(FFV1 无损 .avi;白底 + 细墨条模拟笔画)。
"""

from __future__ import annotations

import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import cv2  # noqa: E402
import numpy as np  # noqa: E402

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

import pytest  # noqa: E402

from core.pipeline_worker import collect_roi_scene_text_options  # noqa: E402
from core.subtitle_generator import OCRToASSOptimizer  # noqa: E402

W, H, FPS = 320, 240, 25.0
LEFT_BOX = (30, 85, 130, 115)    # roi_0 场景文字(招牌),中心 (80,100) → SCENE 区
RIGHT_BOX = (190, 85, 290, 115)  # roi_1 场景文字,中心 (240,100) → SCENE 区
DIALOG_BOX = (100, 200, 280, 230)  # 底部对白行,y 中心 215 > 0.75H → BOTTOM
RECT_L = (10, 60, 150, 140)      # roi_0 策略分析图外接矩形
RECT_R = (170, 60, 310, 140)     # roi_1 策略分析图外接矩形

# 单 ROI 探测帧数:frames 5..20 → 等距候选恰 7 帧;多 ROI 复用读取后
# seek 总数不应超过该“帧数级别”,更不得变成 ROI 数 × 帧数。
EXPECTED_PROBES_PER_ROI = 7


# ---------------------------------------------------------------------------
# 合成数据:白底视频,左右两块「招牌」各画两条细墨条
# ---------------------------------------------------------------------------

def _sign_frame() -> np.ndarray:
    frame = np.full((H, W, 3), 250, np.uint8)
    for x1, y1, x2, y2 in (LEFT_BOX, RIGHT_BOX):
        frame[y1 + 5:y1 + 11, x1 + 4:x2 - 4] = 20
        frame[y1 + 19:y1 + 25, x1 + 4:x2 - 4] = 20
    return frame


@pytest.fixture
def sign_video(tmp_path):
    path = tmp_path / "in.avi"
    frame = _sign_frame()
    writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"FFV1"),
                             FPS, (W, H))
    assert writer.isOpened()
    for _ in range(60):
        writer.write(frame)
    writer.release()
    return path


def _make_ocr_item(frame_num, text, box, roi_id="roi_0"):
    poly = [[box[0], box[1]], [box[2], box[1]], [box[2], box[3]], [box[0], box[3]]]
    data = {
        "dt_polys": [poly],
        "rec_polys": [poly],
        "rec_texts": [text],
        "rec_scores": [0.95],
        "rec_boxes": [list(box)],
    }
    return (data, frame_num, roi_id, frame_num / FPS)


def _dialogues(text):
    return [ln for ln in text.splitlines() if ln.startswith("Dialogue:")]


def _build_converter(video, out_path, policies=None, rects=None, **kwargs):
    return OCRToASSOptimizer(
        video_path=str(video),
        output_path=str(out_path),
        fps=FPS,
        width=W,
        height=H,
        roi_scene_text_policies=policies,
        roi_analysis_rects=rects,
        **kwargs,
    )


# ---------------------------------------------------------------------------
# a. 混合 ROI 策略逐 ROI 处理
# ---------------------------------------------------------------------------

def test_mixed_roi_policies_processed_per_roi(sign_video, tmp_path):
    """roi_0=mask、roi_1=external:两个 ROI 各自产出策略结果/事件。"""
    conv = _build_converter(
        sign_video, tmp_path / "out.ass",
        {"roi_0": "mask", "roi_1": "external"},
        {"roi_0": RECT_L, "roi_1": RECT_R})
    items = [_make_ocr_item(f, "店铺招牌", LEFT_BOX, "roi_0")
             for f in range(5, 21)]
    items += [_make_ocr_item(f, "限时特惠", RIGHT_BOX, "roi_1")
              for f in range(5, 21)]
    conv.convert_from_memory(iter(items))
    text = (tmp_path / "out.ass").read_text(encoding="utf-8-sig")

    # roi_0 走 mask:遮罩 + 原位文本(视频坐标 = 平面坐标 + 外接框原点)
    masks = [ln for ln in _dialogues(text)
             if ln.startswith("Dialogue: 0,") and "\\p1" in ln]
    assert len(masks) == 1
    texts = [ln for ln in _dialogues(text) if ln.startswith("Dialogue: 1,")]
    assert len(texts) == 1 and "店铺招牌" in texts[0]
    assert "\\pos(80,100)" in texts[0]

    # roi_1 走 external:单条 NoteBox 展示框,且不再有原位场景事件
    notes = [ln for ln in _dialogues(text) if ",NoteBox," in ln]
    assert len(notes) == 1 and "限时特惠" in notes[0]
    assert "\\pos(240,100)" not in text  # roi_1 原 SCENE 事件被替换


# ---------------------------------------------------------------------------
# 未知策略名在入口明确报错
# ---------------------------------------------------------------------------

def test_unknown_roi_policy_rejected_at_collection():
    """未知策略名在 CLI/GUI 共用的收集入口必须 ValueError,不得静默透传。"""
    roi_data = [{"type": "rect", "points": [0, 0, 10, 10],
                 "scene_text_policy": "sepia"}]
    with pytest.raises(ValueError, match="sepia"):
        collect_roi_scene_text_options(roi_data)


def test_cli_flag_rejects_unknown_scene_text_policy():
    """CLI --scene-text-policy 未知取值 → argparse 参数校验错误。"""
    from cli import parse_args

    with pytest.raises(SystemExit):
        parse_args(["video.mp4", "--scene-text-policy", "bogus"])


# ---------------------------------------------------------------------------
# b. CLI 参数覆盖 ROI 配置的优先级固定
# ---------------------------------------------------------------------------

def test_cli_explicit_policy_overrides_roi_json():
    from core.pipeline_worker import apply_cli_scene_text_policy

    entries = [{"type": "rect", "scene_text_policy": "external"},
               {"type": "rect"}]
    apply_cli_scene_text_policy(entries, "mask")
    assert [e.get("scene_text_policy") for e in entries] == ["mask", "mask"]


def test_cli_default_policy_keeps_roi_json_config():
    from core.pipeline_worker import apply_cli_scene_text_policy

    entries = [{"scene_text_policy": "external"},
               {"scene_text_policy": "mask"},
               {"type": "rect"}]
    apply_cli_scene_text_policy(entries, "overlap")  # CLI 缺省
    assert [e.get("scene_text_policy") for e in entries] == [
        "external", "mask", None]


# ---------------------------------------------------------------------------
# c. 混合 Scene/Subtitle 行不丢失
# ---------------------------------------------------------------------------

def test_policy_roi_keeps_scene_and_dialogue_rows(sign_video, tmp_path):
    """同一 ROI 既有场景文字组又有底部对白组:对白行保持原样、场景行走
    mask 策略,两类都不被 ROI 画像过滤静默丢弃。"""
    conv = _build_converter(sign_video, tmp_path / "out.ass",
                            {"roi_0": "mask"}, {"roi_0": RECT_L})
    items = [_make_ocr_item(f, "店铺招牌", LEFT_BOX, "roi_0")
             for f in range(5, 13)]          # 场景组 1
    items += [_make_ocr_item(f, "店铺招牌", LEFT_BOX, "roi_0")
              for f in range(25, 33)]        # 场景组 2(间隔 > 桥接窗口)
    items += [_make_ocr_item(f, "谢谢观看", DIALOG_BOX, "roi_0")
              for f in range(45, 61)]        # 底部对白组
    conv.convert_from_memory(iter(items))
    text = (tmp_path / "out.ass").read_text(encoding="utf-8-sig")

    # 场景行走策略:遮罩 + 原位文本
    assert any("\\p1" in ln for ln in _dialogues(text))
    assert "\\pos(80,100)" in text and "店铺招牌" in text
    # 对白行保持原样(未被画像过滤丢弃,也未被策略改写成 NoteBox)
    dialog = [ln for ln in _dialogues(text) if "谢谢观看" in ln]
    assert len(dialog) == 1
    assert ",NoteBox," not in dialog[0]
    assert ",CH," in dialog[0]  # 底部对白走原样式路径


# ---------------------------------------------------------------------------
# d. 多 ROI 只复用视频读取
# ---------------------------------------------------------------------------

def test_multi_roi_analysis_shares_single_video_reader(
        sign_video, tmp_path, monkeypatch):
    """两个 ROI 共用单个 VideoCapture:打开 1 次、seek 次数 = 唯一探测帧数,
    不随 ROI 数线性增长;且策略结果与独立读取一致。"""
    opens = {"n": 0}
    frames = [_sign_frame() for _ in range(60)]

    class FakeCapture:
        def __init__(self, path):
            opens["n"] += 1
            self._pos = 0

        def isOpened(self):
            return True

        def set(self, prop, value):
            assert prop == cv2.CAP_PROP_POS_FRAMES
            self._pos = int(value)

        def read(self):
            return True, frames[self._pos].copy()

        def release(self):
            pass

    monkeypatch.setattr(cv2, "VideoCapture", FakeCapture)

    conv = _build_converter(
        sign_video, tmp_path / "out.ass",
        {"roi_0": "mask", "roi_1": "external"},
        {"roi_0": RECT_L, "roi_1": RECT_R})
    items = [_make_ocr_item(f, "店铺招牌", LEFT_BOX, "roi_0")
             for f in range(5, 21)]
    items += [_make_ocr_item(f, "限时特惠", RIGHT_BOX, "roi_1")
              for f in range(5, 21)]
    conv.convert_from_memory(iter(items))

    assert opens["n"] == 1  # 不再每 ROI×每探测帧各开一次 VideoCapture
    reader = conv._analysis_reader
    assert reader is not None
    assert reader.seeks <= EXPECTED_PROBES_PER_ROI  # 帧数级别,非 ROI 数×帧数

    # 结果与独立读取一致:两个 ROI 的策略事件都在
    text = (tmp_path / "out.ass").read_text(encoding="utf-8-sig")
    assert any("\\p1" in ln for ln in _dialogues(text))
    assert any("限时特惠" in ln and ",NoteBox," in ln for ln in _dialogues(text))


def test_frame_reader_matches_independent_reads(sign_video):
    """FrameReader 读取结果与“独立 VideoCapture+seek”逐字节一致,缓存命中
    返回相同内容。"""
    from core.subtitle_generator.generator import FrameReader

    reader = FrameReader(str(sign_video))
    try:
        for frame_num in (0, 7, 7, 20, 59):  # 含重复帧号(缓存命中)
            got = reader.read(frame_num)
            cap = cv2.VideoCapture(str(sign_video))
            assert cap.isOpened()
            cap.set(cv2.CAP_PROP_POS_FRAMES, frame_num)
            ok, ref = cap.read()
            cap.release()
            assert ok and got is not None
            assert np.array_equal(got, ref)
    finally:
        reader.close()


# ---------------------------------------------------------------------------
# 分析图时间邻域中位合成(可选;默认单帧,行为不变)
# ---------------------------------------------------------------------------

def test_median_analysis_mode_optional_single_default(sign_video, tmp_path):
    """默认单帧模式;median 模式在静态视频下输出与单帧逐字节一致。"""
    conv_default = _build_converter(sign_video, tmp_path / "single.ass",
                                    {"roi_0": "mask"}, {"roi_0": RECT_L})
    assert conv_default.analysis_frame_mode == "single"
    items = [_make_ocr_item(f, "店铺招牌", LEFT_BOX, "roi_0")
             for f in range(5, 21)]
    conv_default.convert_from_memory(iter(items))

    conv_median = _build_converter(sign_video, tmp_path / "median.ass",
                                   {"roi_0": "mask"}, {"roi_0": RECT_L},
                                   analysis_frame_mode="median")
    conv_median.convert_from_memory(iter(items))

    single = (tmp_path / "single.ass").read_text(encoding="utf-8-sig")
    median = (tmp_path / "median.ass").read_text(encoding="utf-8-sig")
    assert "\\p1" in single
    assert single == median  # 静态视频:邻域中位 = 单帧

    with pytest.raises(ValueError):
        _build_converter(sign_video, tmp_path / "bad.ass",
                         analysis_frame_mode="bogus")
