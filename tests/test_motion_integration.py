# tests/test_motion_integration.py
r"""移动文字轨迹管线接入项目 CLI 与主流水线(GUI)的集成测试。

覆盖:
- scripts/motion_ass.normalize_quad_winding:手绘多边形顶点顺序自动纠正;
- cli.parse_motion_quad_spec:"x1,y1 ... x4,y4[@start-end]" 解析(秒区间,
  与 --roi 同语法);
- pipeline_worker.collect_motion_roi_specs:pose 勾选 + 四点多边形才走
  轨迹管线,矩形/非四点/未勾选保持静态路径;
- 生成器:轨迹事件(Name=motion,layer)在过滤/合并/润色之后原样并入,
  motion_roi_ids 抑制对应 ROI 的静态事件,空主流水线时仅写轨迹事件,
  NoteBox 样式按需追加;
- PipelineWorker._run_motion_stage:成功接管 / 单 ROI 失败回退不中断;
- 项目 CLI 端到端:--motion-quad + mock OCR 引擎 → \move 事件并入输出,
  坏 quad 告警降级不失败。

端到端用 cv2.VideoWriter 写合成移动卡片视频(与 test_motion_ass_cli 同法),
OCR 以 monkeypatch 注入,不加载真模型。
"""

from __future__ import annotations

import logging
import os
import sys
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import numpy as np  # noqa: E402
import pytest  # noqa: E402

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from PySide6.QtWidgets import QApplication  # noqa: E402

# 复选框等 QWidget 需要 QApplication(先于 conftest 的 QCoreApplication 创建,
# offscreen 平台;跨测试文件共存时 instance() 复用既有实例)。
_app = QApplication.instance() or QApplication(sys.argv[:1])

from scripts import motion_ass as motion_cli  # noqa: E402
from cli import load_roi_file, parse_motion_quad_spec  # noqa: E402
from core.pipeline_worker import (  # noqa: E402
    PipelineWorker,
    collect_motion_roi_specs,
)
from core.subtitle_generator import OCRToASSOptimizer  # noqa: E402

# 复用 test_motion_ass_cli 的合成移动卡片视频与 mock OCR(同目录,pytest
# rootdir 已把 tests/ 加入 sys.path)。
from test_motion_ass_cli import build_case, make_mock_ocr  # noqa: E402

W, H, FPS = 320, 240, 25.0


def _motion_event(start="0:00:06.54", end="0:00:10.21",
                  body="受信メール", tags="{\\an5\\fs29\\move(822.0,306.8)}",
                  style="Scene", layer=0):
    return {
        "start_time": start, "end_time": end, "style": style,
        "name": "motion", "tags": tags, "body": body, "layer": layer,
    }


def _dialogues(text):
    return [ln for ln in text.splitlines() if ln.startswith("Dialogue:")]


# ---------------------------------------------------------------------------
# normalize_quad_winding
# ---------------------------------------------------------------------------

class TestNormalizeQuadWinding:
    def test_counter_clockwise_is_reversed(self):
        ccw = [[100.0, 200.0], [100.0, 100.0], [200.0, 100.0], [200.0, 200.0]]
        fixed = motion_cli.normalize_quad_winding(ccw)
        # 纠正后鞋带面积为正(顺时针),与 cv2.boxPoints 的 TL,TR,BR,BL 一致
        arr = np.asarray(fixed)
        x, y = arr[:, 0], arr[:, 1]
        assert float(np.sum(x * np.roll(y, -1) - np.roll(x, -1) * y)) > 0

    def test_clockwise_kept_and_input_not_mutated(self):
        cw = [[10, 10], [50, 10], [50, 40], [10, 40]]
        snapshot = [list(p) for p in cw]
        assert motion_cli.normalize_quad_winding(cw) == snapshot
        assert cw == snapshot

    def test_non_four_points_returned_unchanged(self):
        tri = [[0, 0], [10, 0], [5, 5]]
        assert motion_cli.normalize_quad_winding(tri) == tri


# ---------------------------------------------------------------------------
# cli.parse_motion_quad_spec
# ---------------------------------------------------------------------------

class TestParseMotionQuadSpec:
    def test_plain_quad(self):
        quad, t0, t1 = parse_motion_quad_spec("1,2 3,4 5,6 7,8")
        assert quad == [[1.0, 2.0], [3.0, 4.0], [5.0, 6.0], [7.0, 8.0]]
        assert t0 is None and t1 is None

    def test_closed_time_range(self):
        quad, t0, t1 = parse_motion_quad_spec("1,2 3,4 5,6 7,8@6.5-10.5")
        assert quad == [[1.0, 2.0], [3.0, 4.0], [5.0, 6.0], [7.0, 8.0]]
        assert t0 == 6.5 and t1 == 10.5

    def test_open_ranges(self):
        assert parse_motion_quad_spec("1,2 3,4 5,6 7,8@5-")[1:] == (5.0, None)
        assert parse_motion_quad_spec("1,2 3,4 5,6 7,8@-20")[1:] == (None, 20.0)

    def test_invalid_range_rejected(self):
        with pytest.raises(ValueError):
            parse_motion_quad_spec("1,2 3,4 5,6 7,8@9-5")
        with pytest.raises(ValueError):
            parse_motion_quad_spec("1,2 3,4 5,6 7,8@x-y")

    def test_bad_geometry_rejected(self):
        with pytest.raises(ValueError):
            parse_motion_quad_spec("1,2 3,4 5,6")


# ---------------------------------------------------------------------------
# pipeline_worker.collect_motion_roi_specs
# ---------------------------------------------------------------------------

class TestCollectMotionRoiSpecs:
    def _poly(self, points=None, **extra):
        roi = {
            "type": "poly",
            "points": points or [[100, 100], [180, 100], [180, 160], [100, 160]],
            "start_frame": 12,
            "end_frame": 96,
            "write_pose_tags": True,
        }
        roi.update(extra)
        return roi

    def test_quad_poly_with_pose_selected(self):
        specs = collect_motion_roi_specs([self._poly(
            scene_text_policy="mask", motion_auto_brightness=True)])
        assert len(specs) == 1
        spec = specs[0]
        assert spec["roi_id"] == "roi_0"
        assert spec["quad"] == [[100.0, 100.0], [180.0, 100.0],
                                [180.0, 160.0], [100.0, 160.0]]
        assert spec["start_frame"] == 12 and spec["end_frame"] == 96
        assert spec["scene_text_policy"] == "mask"
        assert spec["auto_brightness"] is True

    def test_rect_with_pose_expanded_to_quad(self):
        specs = collect_motion_roi_specs([{
            "type": "rect", "points": [0, 0, 10, 10],
            "start_frame": 0, "end_frame": 5, "write_pose_tags": True}])
        assert len(specs) == 1
        assert specs[0]["quad"] == [[0.0, 0.0], [10.0, 0.0],
                                    [10.0, 10.0], [0.0, 10.0]]

    def test_closed_poly_dedupes_closure_click(self):
        # GUI 手绘四边形会把「回到起点」的闭合点击也存进来(末点≈首点,
        # 允许几像素误差);去掉闭合点后应还原出 4 角 quad。
        closed = [[100, 100], [180, 100], [180, 160], [100, 160],
                  [100.5, 100.5]]
        specs = collect_motion_roi_specs([self._poly(points=closed)])
        assert len(specs) == 1
        assert specs[0]["quad"] == [[100.0, 100.0], [180.0, 100.0],
                                    [180.0, 160.0], [100.0, 160.0]]

    def test_overcomplete_poly_reduced_by_min_area_rect(self):
        messy = [[100, 100], [180, 101], [179, 160],
                 [100, 159], [101, 101], [180.5, 159.5]]
        specs = collect_motion_roi_specs([self._poly(points=messy)])
        assert len(specs) == 1
        quad = specs[0]["quad"]
        assert len(quad) == 4
        xs = [p[0] for p in quad]
        ys = [p[1] for p in quad]
        assert max(xs) - min(xs) == pytest.approx(80.0, abs=2.0)
        assert max(ys) - min(ys) == pytest.approx(60.0, abs=2.0)

    def test_static_cases_excluded(self):
        tri = self._poly(points=[[0, 0], [10, 0], [5, 5]])
        no_pose = self._poly(write_pose_tags=False)
        assert collect_motion_roi_specs([tri, no_pose]) == []

    def test_malformed_points_skipped(self):
        bad = self._poly(points=[[0, "x"], [10, 0], [10, 10], [0, 10]])
        assert collect_motion_roi_specs([bad]) == []
        assert collect_motion_roi_specs([None, "junk"]) == []


# ---------------------------------------------------------------------------
# 生成器:轨迹事件并入与静态事件抑制
# ---------------------------------------------------------------------------

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


def _build_converter(video, out_path, **extra):
    return OCRToASSOptimizer(
        video_path=str(video),
        output_path=str(out_path),
        fps=FPS,
        width=W,
        height=H,
        **extra,
    )


class TestGeneratorMotionEvents:
    def test_motion_events_written_with_name_and_layer(self, tmp_path):
        conv = _build_converter("no-video.avi", tmp_path / "out.ass",
                                motion_events=[_motion_event(layer=1)])
        conv.convert_from_memory(iter([]))
        text = (tmp_path / "out.ass").read_text(encoding="utf-8-sig")
        lines = _dialogues(text)
        assert len(lines) == 1
        # Layer 列=1、Name 列=motion(轨迹豁免标记)
        assert lines[0].startswith(
            "Dialogue: 1,0:00:06.54,0:00:10.21,Scene,motion,0,0,0,,")
        assert "\\move(822.0,306.8)" in lines[0]
        assert "受信メール" in lines[0]

    def test_motion_roi_ids_suppress_static_events(self, tmp_path):
        conv = _build_converter(
            "no-video.avi", tmp_path / "out.ass",
            motion_events=[_motion_event()],
            motion_roi_ids={"roi_0"})
        items = [_make_ocr_item(f, "店铺招牌", (100, 85, 220, 115))
                 for f in range(5, 21)]
        conv.convert_from_memory(iter(items))
        text = (tmp_path / "out.ass").read_text(encoding="utf-8-sig")
        lines = _dialogues(text)
        assert len(lines) == 1
        assert "motion,0,0,0" in lines[0]
        assert "店铺招牌" not in text

    def test_static_events_survive_for_other_rois(self, tmp_path):
        conv = _build_converter(
            "no-video.avi", tmp_path / "out.ass",
            motion_events=[_motion_event()],
            motion_roi_ids={"roi_1"})
        items = [_make_ocr_item(f, "底部台词", (20, 200, 300, 230), roi_id="roi_0")
                 for f in range(5, 21)]
        conv.convert_from_memory(iter(items))
        text = (tmp_path / "out.ass").read_text(encoding="utf-8-sig")
        bodies = "".join(_dialogues(text))
        assert "底部台词" in bodies
        assert "受信メール" in bodies

    def test_notebox_style_added_when_motion_event_uses_it(self, tmp_path):
        conv = _build_converter(
            "no-video.avi", tmp_path / "out.ass",
            motion_events=[_motion_event(style="NoteBox")])
        conv.convert_from_memory(iter([]))
        text = (tmp_path / "out.ass").read_text(encoding="utf-8-sig")
        assert "Style: NoteBox," in text

    def test_default_header_unchanged_without_motion(self, tmp_path):
        conv = _build_converter("no-video.avi", tmp_path / "out.ass")
        conv.convert_from_memory(iter([]))
        text = (tmp_path / "out.ass").read_text(encoding="utf-8-sig")
        assert "Style: NoteBox," not in text


# ---------------------------------------------------------------------------
# PipelineWorker._run_motion_stage
# ---------------------------------------------------------------------------

def _make_worker(roi_data, tmp_path):
    return PipelineWorker(
        video_path=str(tmp_path / "in.avi"),
        roi_data=roi_data,
        total_frames=100,
        fps=FPS,
        video_width=W,
        video_height=H,
        output_ass_path=str(tmp_path / "out.ass"),
        debug_mode=False,
        template_path=None,
    )


class TestWorkerMotionStage:
    def test_no_motion_rois_is_noop(self, tmp_path):
        worker = _make_worker([{"type": "rect", "points": [0, 0, 10, 10],
                                "write_pose_tags": True}], tmp_path)
        assert worker._run_motion_stage() == ([], set())

    def test_success_collects_events_and_suppression(self, tmp_path, monkeypatch):
        calls = []

        def fake_build(video_path, quad, **kwargs):
            calls.append((str(video_path), [list(map(float, p)) for p in quad],
                          kwargs))
            return [_motion_event()], {"ok_frames": 10, "total_frames": 10,
                                       "keyframes": [1, 2], "policy": "overlap",
                                       "lines": 1}

        monkeypatch.setattr(motion_cli, "build_motion_events", fake_build)
        roi = {"type": "poly", "write_pose_tags": True,
               "points": [[100, 100], [180, 100], [180, 160], [100, 160]],
               "start_frame": 5, "end_frame": 50,
               "scene_text_policy": "whitespace",
               "motion_auto_brightness": True}
        worker = _make_worker([roi], tmp_path)
        events, roi_ids = worker._run_motion_stage()
        assert roi_ids == {"roi_0"}
        assert len(events) == 1
        assert len(calls) == 1
        video_path, quad, kwargs = calls[0]
        assert quad == [[100.0, 100.0], [180.0, 100.0],
                        [180.0, 160.0], [100.0, 160.0]]
        assert kwargs["start_frame"] == 5 and kwargs["end_frame"] == 50
        assert kwargs["scene_text_policy"] == "whitespace"
        assert kwargs["auto_brightness"] is True

    def test_failure_falls_back_without_raising(self, tmp_path, monkeypatch):
        def fake_build(video_path, quad, **kwargs):
            raise RuntimeError("no ok frames")

        monkeypatch.setattr(motion_cli, "build_motion_events", fake_build)
        roi = {"type": "poly", "write_pose_tags": True,
               "points": [[100, 100], [180, 100], [180, 160], [100, 160]],
               "start_frame": 0, "end_frame": 10}
        worker = _make_worker([roi], tmp_path)
        events, roi_ids = worker._run_motion_stage()
        assert events == [] and roi_ids == set()


# ---------------------------------------------------------------------------
# 项目 CLI 端到端(--motion-quad,mock 引擎)
# ---------------------------------------------------------------------------

@pytest.fixture
def moving_video(tmp_path):
    video_path, quad0 = build_case(tmp_path)
    return video_path, quad0


@pytest.fixture
def fake_engine(monkeypatch):
    """替换 _default_ocr_fn:返回阈值找黑条的 mock OCR,不加载真模型。"""
    engine = SimpleNamespace(cleanup=lambda: None)
    monkeypatch.setattr(
        motion_cli, "_default_ocr_fn",
        lambda engine_id=None: (make_mock_ocr([]), engine))
    return engine


class TestCliMotionQuad:
    def _run(self, tmp_path, video_path, *extra):
        import cli as cli_mod

        out = str(tmp_path / "out.ass")
        code = cli_mod.main(
            [video_path, "-o", out, "--workers", "1", "-q", *extra])
        text = open(out, encoding="utf-8-sig").read() if os.path.exists(out) else ""
        return code, text

    def test_motion_quad_merges_trajectory_events(self, tmp_path, moving_video,
                                                  fake_engine):
        video_path, quad0 = moving_video
        quad_spec = ",".join(
            f"{quad0[i][0]:g},{quad0[i][1]:g}" for i in range(4))
        code, text = self._run(
            tmp_path, video_path,
            "--motion-quad", f"{quad_spec}@0-3",
            "--roi", "0,0,8,8@0-0.25",
        )
        assert code == 0
        motion_lines = [ln for ln in _dialogues(text) if ",Scene,motion," in ln]
        assert len(motion_lines) == 2  # 两行"文字"黑条 → 两条轨迹事件
        assert all("\\move(" in ln for ln in motion_lines)
        assert "LINE0" in motion_lines[0] and "LINE1" in motion_lines[1]

    def test_counter_clockwise_quad_auto_corrected(self, tmp_path, moving_video,
                                                   fake_engine):
        video_path, quad0 = moving_video
        quad_spec = ",".join(
            f"{quad0[i][0]:g},{quad0[i][1]:g}" for i in (3, 2, 1, 0))  # 逆时针
        code, text = self._run(
            tmp_path, video_path,
            "--motion-quad", f"{quad_spec}@0-3",
            "--roi", "0,0,8,8@0-0.25",
        )
        assert code == 0
        assert ",Scene,motion," in text

    def test_degenerate_quad_degrades_gracefully(self, tmp_path, moving_video,
                                                 fake_engine):
        video_path, _ = moving_video
        # 零面积 quad(共线三点)→ 校验失败 → 告警跳过,主流水线照常
        code, text = self._run(
            tmp_path, video_path,
            "--motion-quad", "10,10 20,20 30,30 40,40@0-3",
            "--roi", "0,0,8,8@0-0.25",
        )
        assert code == 0
        assert ",Scene,motion," not in text

    def test_motion_quad_file(self, tmp_path, moving_video, fake_engine):
        import json

        video_path, quad0 = moving_video
        quad_path = tmp_path / "quad.json"
        quad_path.write_text(json.dumps({"quad": quad0}), encoding="utf-8")
        code, text = self._run(
            tmp_path, video_path,
            "--motion-quad-file", str(quad_path),
            "--roi", "0,0,8,8@0-0.25",
        )
        assert code == 0
        assert ",Scene,motion," in text

    def test_bad_motion_quad_spec_exit_code_2(self, tmp_path, moving_video,
                                              fake_engine):
        video_path, _ = moving_video
        code, _ = self._run(
            tmp_path, video_path,
            "--motion-quad", "1,2 3,4 5,6",
            "--roi", "0,0,8,8@0-0.25",
        )
        assert code == 2


# ---------------------------------------------------------------------------
# GUI:亮度自适应复选框(pose 联动)与 ROI 条目持久化
# ---------------------------------------------------------------------------

def _make_roi_def():
    from components.roi_definition import RoiDefinitionWidget

    w = RoiDefinitionWidget()
    w.start_time_edit.setText("10")
    w.end_time_edit.setText("20")
    return w


class TestGuiBrightnessCheckbox:
    def test_defaults_and_pose_binding(self):
        roi_def = _make_roi_def()
        cb = roi_def.motion_brightness_checkbox
        assert cb.isChecked() is False
        assert cb.isEnabled() is False  # pose 未勾选时不可用
        roi_def.pose_tags_checkbox.setChecked(True)
        assert cb.isEnabled() is True
        roi_def.pose_tags_checkbox.setChecked(False)
        assert cb.isEnabled() is False

    def test_backfill_from_roi(self):
        roi_def = _make_roi_def()
        roi_def.set_color_restrict_from_roi(
            {"write_pose_tags": True, "motion_auto_brightness": True})
        assert roi_def.motion_brightness_checkbox.isChecked() is True
        assert roi_def.motion_brightness_checkbox.isEnabled() is True

        # pose 关闭的旧配置:值保留但控件禁用
        roi_def.set_color_restrict_from_roi({"motion_auto_brightness": True})
        assert roi_def.motion_brightness_checkbox.isChecked() is True
        assert roi_def.motion_brightness_checkbox.isEnabled() is False

        roi_def.set_color_restrict_from_roi(None)  # 新 ROI 复位
        assert roi_def.motion_brightness_checkbox.isChecked() is False
        assert roi_def.motion_brightness_checkbox.isEnabled() is False

    def test_roi_entry_assembly_carries_motion_brightness(self):
        """走真实 RoiEditingMixin._create_roi_entry_from_ui(多边形路径)。"""
        from main_window.roi_editing import RoiEditingMixin

        class _FakeVideoLabel:
            def __init__(self):
                self._poly = [SimpleNamespace(x=lambda: 90, y=lambda: 80),
                              SimpleNamespace(x=lambda: 250, y=lambda: 80),
                              SimpleNamespace(x=lambda: 250, y=lambda: 180),
                              SimpleNamespace(x=lambda: 90, y=lambda: 180)]

            def get_draw_mode(self):
                return "poly"

            def get_base_pixmap(self):
                return object()

            def get_current_drawing_poly(self):
                return self._poly

        class _FakeWindow(RoiEditingMixin):
            def __init__(self, roi_def):
                self.roi_def_widget = roi_def
                self.video_display_widget = SimpleNamespace(
                    video_label=_FakeVideoLabel())
                self.roi_list_widget = SimpleNamespace(
                    roi_list_widget=SimpleNamespace(
                        currentItem=lambda: None, row=lambda item: -1,
                        setCurrentRow=lambda i: None))
                self.control_panel_widget = SimpleNamespace(
                    invalidate_color_gate_confirmation=lambda: None)
                self.cap = object()
                self.fps = FPS
                self.roi_data = []
                self.logger = logging.getLogger("test_motion_integration")
                self.clipboard_roi = None
                self._suppress_selection_seek = True
                self.seek_video = lambda frame: None
                self.update_all_rois_visibility = lambda: None
                self.update_ui_state = lambda: None
                self.update_roi_list = lambda: None

            def parse_time_or_frame(self, text):
                text = text.strip()
                return int(text) if text.isdigit() else 0

        roi_def = _make_roi_def()
        win = _FakeWindow(roi_def)
        roi_def.pose_tags_checkbox.setChecked(True)
        roi_def.motion_brightness_checkbox.setChecked(True)
        entry = win._create_roi_entry_from_ui()
        assert entry is not None
        assert entry["type"] == "poly"
        assert entry["write_pose_tags"] is True
        assert entry["motion_auto_brightness"] is True
        assert entry["pose"]["pos"] == [170.0, 130.0]  # 多边形外接中心

        roi_def.motion_brightness_checkbox.setChecked(False)
        entry = win._create_roi_entry_from_ui()
        assert entry["motion_auto_brightness"] is False


# ---------------------------------------------------------------------------
# cli.load_roi_file(--roi-file)
# ---------------------------------------------------------------------------

class TestLoadRoiFile:
    def _write(self, tmp_path, payload, name="rois.json"):
        import json
        p = tmp_path / name
        p.write_text(json.dumps(payload, ensure_ascii=False),
                     encoding="utf-8")
        return str(p)

    def test_gui_wrapper_format(self, tmp_path):
        payload = {"ocr_lang": "ch", "rois": [
            {"type": "poly", "points": [[1, 1], [9, 1], [9, 9], [1, 9]],
             "start_frame": 0, "end_frame": 10, "write_pose_tags": True,
             "motion_auto_brightness": True}]}
        entries = load_roi_file(self._write(tmp_path, payload))
        assert len(entries) == 1
        assert entries[0]["write_pose_tags"] is True
        assert entries[0]["motion_auto_brightness"] is True

    def test_bare_list_format(self, tmp_path):
        payload = [{"type": "rect", "points": [0, 0, 5, 5]}]
        entries = load_roi_file(self._write(tmp_path, payload))
        assert entries[0]["type"] == "rect"

    def test_malformed_rejected(self, tmp_path):
        with pytest.raises(ValueError):
            load_roi_file(self._write(tmp_path, {"nope": 1}))
        with pytest.raises(ValueError):
            load_roi_file(self._write(tmp_path, [{"type": "rect"}]))
        with pytest.raises(ValueError):
            load_roi_file(self._write(tmp_path, {"rois": []}))

    def test_missing_file_raises_oserror(self, tmp_path):
        with pytest.raises(OSError):
            load_roi_file(str(tmp_path / "absent.json"))

    def test_loaded_pose_poly_yields_motion_spec(self, tmp_path):
        # 端到端小闭环:GUI 保存的 5 点闭合多边形(带 pose + 亮度)经
        # load_roi_file -> collect_motion_roi_specs 应产出轨迹规格。
        from core.pipeline_worker import collect_motion_roi_specs
        payload = {"rois": [
            {"type": "poly",
             "points": [[632, 282], [1288, 280], [1324, 1075],
                        [599, 1079], [630, 280]],
             "start_frame": 0, "end_frame": 245,
             "write_pose_tags": True, "motion_auto_brightness": True}]}
        entries = load_roi_file(self._write(tmp_path, payload))
        specs = collect_motion_roi_specs(entries)
        assert len(specs) == 1
        assert len(specs[0]["quad"]) == 4
        assert specs[0]["auto_brightness"] is True
        assert specs[0]["start_frame"] == 0 and specs[0]["end_frame"] == 245
