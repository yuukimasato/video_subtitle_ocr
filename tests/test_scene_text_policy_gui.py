# tests/test_scene_text_policy_gui.py
r"""场景文字显示策略接入完整版 GUI 与主流水线的集成测试。

覆盖:
- OCRToASSOptimizer(静态 \pos 路径):每 ROI 策略 mask/external/whitespace
  替换该 ROI 的 SCENE 事件;策略事件写 Layer 列与 policy 标记;豁免
  _filter_events 与 _merge_temporal_near_duplicate_events;overlap(缺省)
  输出与不传策略逐字节一致(回归);
- pipeline_worker.collect_roi_scene_text_options:roi_data 收集策略与外接
  矩形、merge_rois 合并语义;
- RoiDefinitionWidget.scene_text_policy_combo:选项/currentData/选中回填;
  RoiEditingMixin._create_roi_entry_from_ui 组装 scene_text_policy 键。

生成器测试用 cv2.VideoWriter 写合成视频(MJPG .avi,与
test_pipeline_refine_integration.py 同法);GUI 测试需 QApplication(QWidget),
必须先于 conftest 的 QCoreApplication 创建(offscreen 平台)。
"""

from __future__ import annotations

import logging
import os
import re
import sys
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import cv2  # noqa: E402
import numpy as np  # noqa: E402

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from PySide6.QtWidgets import QApplication, QComboBox, QListWidgetItem  # noqa: E402

_app = QApplication.instance() or QApplication(sys.argv[:1])

import pytest  # noqa: E402

from core.pipeline_worker import collect_roi_scene_text_options  # noqa: E402
from core.subtitle_generator import OCRToASSOptimizer  # noqa: E402
from main_window.roi_editing import RoiEditingMixin  # noqa: E402

W, H, FPS = 320, 240, 25.0
TEXT_BOX = (100, 85, 220, 115)  # 画面中部文字(y 中心 100 → SCENE 区)


# ---------------------------------------------------------------------------
# 合成数据:白底视频 + 中部黑色「招牌」矩形;OCR 条目同 test_roi_pose_tags
# ---------------------------------------------------------------------------

@pytest.fixture
def scene_video(tmp_path):
    """白底视频(FFV1 无损),文字区画两条细墨条模拟笔画——真实文本的
    墨水占比低,中位色停在背景上,与 test_scene_text_policy 的平面同构。"""
    path = tmp_path / "in.avi"
    writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"FFV1"),
                             FPS, (W, H))
    assert writer.isOpened()
    frame = np.full((H, W, 3), 250, np.uint8)
    x1, y1, x2, y2 = TEXT_BOX
    frame[y1 + 5:y1 + 11, x1 + 4:x2 - 4] = 20
    frame[y1 + 19:y1 + 25, x1 + 4:x2 - 4] = 20
    for _ in range(40):
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


def _scene_items():
    return (_make_ocr_item(f, "店铺招牌", TEXT_BOX) for f in range(5, 21))


def _build_converter(video, out_path, policies=None, rects=None):
    return OCRToASSOptimizer(
        video_path=str(video),
        output_path=str(out_path),
        fps=FPS,
        width=W,
        height=H,
        roi_scene_text_policies=policies,
        roi_analysis_rects=rects,
    )


def _dialogues(text):
    return [ln for ln in text.splitlines() if ln.startswith("Dialogue:")]


# ---------------------------------------------------------------------------
# 生成器:三种策略模式
# ---------------------------------------------------------------------------

def test_mask_policy_replaces_scene_events(scene_video, tmp_path):
    conv = _build_converter(scene_video, tmp_path / "out.ass",
                            {"roi_0": "mask"}, {"roi_0": (40, 60, 280, 140)})
    conv.convert_from_memory(_scene_items())
    text = (tmp_path / "out.ass").read_text(encoding="utf-8-sig")

    masks = [ln for ln in _dialogues(text) if ln.startswith("Dialogue: 0,") and "\\p1" in ln]
    assert len(masks) == 1
    assert "\\an7\\pos(" in masks[0] and "\\bord0" in masks[0]
    assert "\\1c&HFAFAFA&" in masks[0]  # 白底采样色
    assert "\\move" not in masks[0]     # 静态路径

    texts = [ln for ln in _dialogues(text) if ln.startswith("Dialogue: 1,")]
    assert len(texts) == 1
    assert "店铺招牌" in texts[0]
    assert "\\pos(160,100)" in texts[0]  # 视频坐标 = 平面坐标 + 外接框原点

    # 该 ROI 原 SCENE styled 事件被策略事件替换(其余 Dialogue 为零)
    others = [ln for ln in _dialogues(text) if ln not in masks and ln not in texts]
    assert others == []
    # 头部含 NoteBox 样式(mask 运行时可能回退 external,样式需常备)
    assert "Style: NoteBox," in text


def test_external_policy_single_notebox_event(scene_video, tmp_path):
    conv = _build_converter(scene_video, tmp_path / "out.ass",
                            {"roi_0": "external"}, {"roi_0": (40, 60, 280, 140)})
    conv.convert_from_memory(_scene_items())
    text = (tmp_path / "out.ass").read_text(encoding="utf-8-sig")

    notes = [ln for ln in _dialogues(text) if ",NoteBox," in ln]
    assert len(notes) == 1
    assert "\\an2\\pos(160.0,200.0)\\fs" in notes[0]  # W/2, H-margin(40)
    assert "店铺招牌" in notes[0]
    assert "\\p1" not in text
    # 原 SCENE 事件被替换:不再有 {\an5\pos(160,100)} 的原位事件
    assert "\\pos(160,100)" not in text


def test_whitespace_policy_places_text_in_band(scene_video, tmp_path):
    # 外接框取得更高:原文字下方留出 ≥2 行高的空白带
    conv = _build_converter(scene_video, tmp_path / "out.ass",
                            {"roi_0": "whitespace"}, {"roi_0": (40, 40, 280, 200)})
    conv.convert_from_memory(_scene_items())
    text = (tmp_path / "out.ass").read_text(encoding="utf-8-sig")

    ws = [ln for ln in _dialogues(text) if "店铺招牌" in ln and "\\fs" in ln]
    assert len(ws) == 1
    assert "\\an5" in ws[0]
    m = re.search(r"\\pos\(([-\d.]+),([-\d.]+)\)", ws[0])
    assert m and float(m.group(2)) > float(TEXT_BOX[3])  # 落在原文字下方
    assert "\\p1" not in text


def test_missing_analysis_rect_falls_back_to_overlap(scene_video, tmp_path):
    """无外接矩形(无法取分析图)→ 回退原路径,输出与 overlap 一致。"""
    conv = _build_converter(scene_video, tmp_path / "out.ass", {"roi_0": "mask"})
    conv.convert_from_memory(_scene_items())
    text = (tmp_path / "out.ass").read_text(encoding="utf-8-sig")
    assert "\\p1" not in text
    assert "\\pos(160,100)" in text


def test_unreadable_video_falls_back_to_overlap(tmp_path):
    conv = _build_converter(tmp_path / "missing.avi", tmp_path / "out.ass",
                            {"roi_0": "mask"}, {"roi_0": (40, 60, 280, 140)})
    conv.convert_from_memory(_scene_items())
    text = (tmp_path / "out.ass").read_text(encoding="utf-8-sig")
    assert "\\p1" not in text
    assert "\\pos(160,100)" in text


def test_overlap_policy_output_identical_to_default(scene_video, tmp_path):
    """overlap(缺省)回归:显式 overlap 与不传策略输出逐字节一致。"""
    plain = _build_converter(scene_video, tmp_path / "plain.ass")
    plain.convert_from_memory(_scene_items())
    explicit = _build_converter(scene_video, tmp_path / "explicit.ass",
                                {"roi_0": "overlap"}, {"roi_0": (40, 60, 280, 140)})
    explicit.convert_from_memory(_scene_items())
    a = (tmp_path / "plain.ass").read_text(encoding="utf-8-sig")
    b = (tmp_path / "explicit.ass").read_text(encoding="utf-8-sig")
    assert a == b
    assert "Style: NoteBox," not in a  # 无非 overlap 策略时头部不变
    assert "\\pos(160,100)" in a


def test_non_scene_groups_unaffected_by_policy(scene_video, tmp_path):
    """BOTTOM 组不受策略影响(策略只作用于 SCENE 组)。"""
    conv = _build_converter(scene_video, tmp_path / "out.ass",
                            {"roi_0": "external"}, {"roi_0": (40, 60, 280, 140)})
    items = [_make_ocr_item(f, "学成归来", (100, 200, 400, 230), "roi_0")
             for f in range(30, 40)]  # y 中心 215 > 0.75H → BOTTOM
    conv.convert_from_memory(iter(items))
    text = (tmp_path / "out.ass").read_text(encoding="utf-8-sig")
    assert "学成归来" in text
    assert ",NoteBox," not in text  # BOTTOM 事件不走 NoteBox


# ---------------------------------------------------------------------------
# 生成器:policy 事件豁免过滤与近重复合并
# ---------------------------------------------------------------------------

def test_policy_events_exempt_from_filter_and_merge(tmp_path):
    conv = _build_converter(tmp_path / "unused.avi", tmp_path / "out.ass")
    base = {"roi": "roi_0", "style": "Scene", "policy": True}
    events = [
        dict(base, start_time="0:00:01.00", end_time="0:00:02.00",
             tags="{\\an5\\pos(300,325)}", body="店铺招牌", layer=1),
        dict(base, start_time="0:00:03.00", end_time="0:00:04.00",
             tags="{\\an5\\pos(301,326)}", body="店铺招牌", layer=1),
        dict(base, start_time="0:00:05.00", end_time="0:00:06.00",
             tags="{\\an7\\pos(1.0,1.0)\\p1}", body="", layer=0),
    ]
    merged = conv._merge_temporal_near_duplicate_events([dict(e) for e in events])
    assert len(merged) == 3  # 不被近重复合并
    assert all(e.get("policy") for e in merged)
    assert [e.get("layer") for e in merged] == [1, 1, 0]  # 标记/图层未被改写

    kept = conv._filter_events([dict(e) for e in events])
    assert len(kept) == 3  # 空 body 遮罩事件不被噪声过滤剔除

    # 对照:普通事件仍按原规则过滤/合并
    normal_empty = {"roi": "roi_0", "style": "Scene", "start_time": "0:00:01.00",
                    "end_time": "0:00:02.00", "tags": "", "body": ""}
    assert conv._filter_events([normal_empty]) == []
    pair = [
        {"roi": "roi_0", "style": "Scene", "start_time": "0:00:01.00",
         "end_time": "0:00:02.00", "tags": "{\\an5\\pos(300,325)}", "body": "店铺招牌"},
        {"roi": "roi_0", "style": "Scene", "start_time": "0:00:03.00",
         "end_time": "0:00:04.00", "tags": "{\\an5\\pos(301,326)}", "body": "店铺招牌"},
    ]
    assert len(conv._merge_temporal_near_duplicate_events([dict(e) for e in pair])) == 1


def test_notebox_style_only_with_active_policy(tmp_path):
    """默认/全 overlap 时头部无 NoteBox 样式;任一 ROI 非 overlap 时追加。"""
    conv = _build_converter(tmp_path / "unused.avi", tmp_path / "a.ass")
    assert "Style: NoteBox," not in conv._get_ass_header()
    conv2 = _build_converter(tmp_path / "unused.avi", tmp_path / "b.ass",
                             {"roi_0": "whitespace"})
    header = conv2._get_ass_header()
    assert "Style: NoteBox," in header
    # Scene 同字号、BorderStyle=3、半透明黑 BackColour、对齐 2
    m = re.search(r"^Style: NoteBox,思源黑体 CN,(\d+),(.*),3,1,0,2,10,10,10,1$", header, re.M)
    assert m and "&H80000000&" in m.group(2)
    scene_fs = re.search(r"^Style: Scene,思源黑体 CN,(\d+),", header, re.M)
    assert scene_fs and m.group(1) == scene_fs.group(1)


# ---------------------------------------------------------------------------
# worker:收集逻辑(抽出的模块级函数)
# ---------------------------------------------------------------------------

class TestCollectRoiSceneTextOptions:
    def test_rect_and_poly_bounding_boxes(self):
        roi_data = [
            {"type": "rect", "points": [10, 20, 100, 50], "scene_text_policy": "mask"},
            {"type": "poly", "points": [[0, 0], [100, 0], [100, 40], [0, 60]]},
        ]
        policies, rects = collect_roi_scene_text_options(roi_data)
        assert policies == {"roi_0": "mask"}
        assert rects["roi_0"] == (10, 20, 110, 70)
        assert rects["roi_1"] == (0, 0, 100, 60)

    def test_overlap_and_missing_not_collected_as_policies(self):
        roi_data = [
            {"type": "rect", "points": [0, 0, 10, 10]},
            {"type": "rect", "points": [5, 5, 10, 10], "scene_text_policy": "overlap"},
            {"type": "rect", "points": [5, 5, 10, 10], "scene_text_policy": "external"},
        ]
        policies, rects = collect_roi_scene_text_options(roi_data)
        assert policies == {"roi_2": "external"}
        assert set(rects) == {"roi_0", "roi_1", "roi_2"}

    def test_empty_and_invalid_inputs(self):
        assert collect_roi_scene_text_options([]) == ({}, {})
        assert collect_roi_scene_text_options(None) == ({}, {})
        policies, rects = collect_roi_scene_text_options(
            [{"type": "rect", "points": [1, 2], "scene_text_policy": "mask"}])
        assert policies == {"roi_0": "mask"} and rects == {}

    def test_merge_rois_takes_first_non_overlap_with_warning(self, caplog):
        roi_data = [
            {"type": "rect", "points": [0, 0, 10, 10], "scene_text_policy": "mask"},
            {"type": "rect", "points": [20, 0, 10, 10]},  # overlap,不参与
            {"type": "rect", "points": [40, 0, 10, 10], "scene_text_policy": "external"},
        ]
        with caplog.at_level(logging.WARNING, logger="core.pipeline_worker"):
            policies, rects = collect_roi_scene_text_options(roi_data, merge_rois=True)
        assert policies == {"roi_merged": "mask"}
        assert rects == {"roi_merged": (0, 0, 50, 10)}  # 全部 ROI 外接矩形并集
        assert any("mask" in r.message and "external" in r.message
                   for r in caplog.records)

    def test_merge_rois_single_policy_no_warning(self, caplog):
        roi_data = [
            {"type": "rect", "points": [0, 0, 10, 10], "scene_text_policy": "mask"},
            {"type": "rect", "points": [20, 0, 10, 10], "scene_text_policy": "mask"},
        ]
        with caplog.at_level(logging.WARNING, logger="core.pipeline_worker"):
            policies, rects = collect_roi_scene_text_options(roi_data, merge_rois=True)
        assert policies == {"roi_merged": "mask"}
        assert rects == {"roi_merged": (0, 0, 30, 10)}
        assert not [r for r in caplog.records if r.levelno >= logging.WARNING]


# ---------------------------------------------------------------------------
# GUI:RoiDefinitionWidget 组合框 + ROI 条目组装/回填
# ---------------------------------------------------------------------------

class _FakeVideoLabel:
    def __init__(self, mode="rect", rect=None, poly=None):
        self._mode = mode
        self._rect = rect
        self._poly = poly

    def get_draw_mode(self):
        return self._mode

    def get_base_pixmap(self):
        return object()  # 非空即可

    def get_current_drawing_roi(self):
        return self._rect

    def get_current_drawing_poly(self):
        return self._poly

    def is_roi_ready(self):
        return True

    def clear_current_drawing(self):
        pass


class _FakeWindow(RoiEditingMixin):
    """只装配 RoiEditingMixin 依赖的最小宿主(不实例化完整主窗口)。"""

    def __init__(self, roi_def, label):
        self.roi_def_widget = roi_def
        self.video_display_widget = SimpleNamespace(video_label=label)
        self.roi_list_widget = SimpleNamespace(
            roi_list_widget=SimpleNamespace(
                currentItem=lambda: None, row=lambda item: -1,
                setCurrentRow=lambda i: None))
        self.control_panel_widget = SimpleNamespace(
            invalidate_color_gate_confirmation=lambda: None)
        self.cap = object()
        self.fps = FPS
        self.roi_data = []
        self.logger = logging.getLogger("test_scene_text_policy_gui")
        self.clipboard_roi = None
        self._suppress_selection_seek = True
        self.seek_video = lambda frame: None
        self.update_all_rois_visibility = lambda: None
        self.update_ui_state = lambda: None
        self.update_roi_list = lambda: None

    def parse_time_or_frame(self, text: str) -> int:
        # 时间框在测试里填帧号;选中回填拿到的 "0:00:00.400" 之类显示串按 0 处理。
        text = text.strip()
        return int(text) if text.isdigit() else 0


def _make_roi_def():
    from components.roi_definition import RoiDefinitionWidget

    w = RoiDefinitionWidget()
    w.start_time_edit.setText("10")
    w.end_time_edit.setText("20")
    return w


def test_policy_combo_options_and_default():
    roi_def = _make_roi_def()
    combo = roi_def.scene_text_policy_combo
    assert isinstance(combo, QComboBox)
    assert combo.count() == 4
    assert [combo.itemData(i) for i in range(combo.count())] == [
        "overlap", "mask", "external", "whitespace"]
    assert combo.currentIndex() == 0
    assert combo.currentData() == "overlap"
    assert combo.toolTip()  # tooltip 说明作用范围与回退链


def test_policy_combo_backfill_from_roi():
    roi_def = _make_roi_def()
    roi_def.set_color_restrict_from_roi({"scene_text_policy": "external"})
    assert roi_def.scene_text_policy_combo.currentData() == "external"
    roi_def.set_color_restrict_from_roi({"scene_text_policy": "whitespace"})
    assert roi_def.scene_text_policy_combo.currentData() == "whitespace"
    roi_def.set_color_restrict_from_roi({})  # 旧配置无该键 → 默认
    assert roi_def.scene_text_policy_combo.currentData() == "overlap"
    roi_def.set_color_restrict_from_roi({"scene_text_policy": "bogus"})
    assert roi_def.scene_text_policy_combo.currentData() == "overlap"
    roi_def.set_color_restrict_from_roi(None)  # 新 ROI 复位
    assert roi_def.scene_text_policy_combo.currentData() == "overlap"


def test_roi_entry_assembly_carries_policy():
    roi_def = _make_roi_def()
    roi_def.scene_text_policy_combo.setCurrentIndex(2)  # external
    win = _FakeWindow(roi_def, _FakeVideoLabel(rect=(10, 20, 100, 50)))
    entry = win._create_roi_entry_from_ui()
    assert entry is not None
    assert entry["scene_text_policy"] == "external"
    assert entry["blur_enabled"] is False
    assert entry["points"] == [10, 20, 100, 50]

    roi_def.scene_text_policy_combo.setCurrentIndex(0)  # overlap(默认)
    entry = win._create_roi_entry_from_ui()
    assert entry["scene_text_policy"] == "overlap"


def test_selected_roi_backfills_combo_and_update_roundtrip():
    roi_def = _make_roi_def()
    win = _FakeWindow(roi_def, _FakeVideoLabel(rect=(10, 20, 100, 50)))
    win.roi_data = [{"start_time": "0:00:00.400", "end_time": "0:00:00.800",
                     "start_frame": 10, "end_frame": 20, "type": "rect",
                     "points": [10, 20, 100, 50],
                     "scene_text_policy": "mask"}]

    row = 0
    item = "item"
    win.roi_list_widget.roi_list_widget = SimpleNamespace(
        currentItem=lambda: item, row=lambda it: row,
        setCurrentRow=lambda i: None)
    win.on_roi_selection_changed(QListWidgetItem("x"), None)
    assert roi_def.scene_text_policy_combo.currentData() == "mask"

    # 编辑写回:组合框状态决定新条目的策略键,不丢失
    win.update_selected_roi()
    assert win.roi_data[0]["scene_text_policy"] == "mask"
