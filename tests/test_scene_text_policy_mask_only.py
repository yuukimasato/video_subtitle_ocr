# tests/test_scene_text_policy_mask_only.py
"""mask_only(仅遮罩 / typesetting 遮罩蒙版)策略模式单元测试。

覆盖《仅遮罩(mask_only)设计》:

- 动态路径 apply_policy(mode="mask_only"):遮罩事件与 mask 模式完全一致
  (\\p1/layer 0/\\an7/\\move/采样色),识别文本事件改写为 comment=True
  (writer 输出 Comment 行,播放器不渲染)且保留时间/正文/layer 1;
- 回退链:背景杂色 → external(notes 记 mask_only->external);空轨迹 →
  external(与 mask 同条件);
- 静态路径 apply_policy_static(mode="mask_only"):mask spec 与 mask 模式
  一致,text spec 附 comment=True;applied == "mask_only";杂色回退 external;
- Writer:scripts/motion_ass.write_ass 对 comment 事件写 ``Comment:`` 行、
  其余仍 ``Dialogue:``;主流水线端到端(convert_from_memory + roi 策略
  mask_only)输出遮罩 Dialogue(layer 0)+ 原文 Comment(layer 1),
  不再渲染原位文本 Dialogue;
- 管线接入:collect_roi_scene_text_options 接受 mask_only;项目 CLI
  parse_args 接受 --scene-text-policy mask_only。

动态/静态用例的合成轨迹与平面构造与 test_scene_text_policy.py 同构
(纯平移单应,不读视频、不加载模型);端到端用例与
test_pipeline_scene_text_policy.py 同构(FFV1 无损 .avi + 白底墨条)。
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

from core.motion_ass import format_ass_time  # noqa: E402
from core.scene_plane_tracker import TrackedQuad  # noqa: E402
from core.scene_text_policy import (  # noqa: E402
    apply_policy,
)

FPS = 25.0
PLANE_W, PLANE_H = 300, 400
ROWS = [
    ("标题", (20.0, 20.0, 200.0, 36.0)),
    ("正文一", (20.0, 60.0, 200.0, 76.0)),
    ("正文二", (20.0, 80.0, 200.0, 96.0)),
]


def make_translation_tracks(n: int = 10, dx: float = 2.0) -> list:
    tracks = []
    for i in range(n):
        h_mat = np.array([
            [1.0, 0.0, dx * i],
            [0.0, 1.0, 0.0],
            [0.0, 0.0, 1.0],
        ], dtype=np.float64)
        tracks.append(TrackedQuad(
            frame_num=i, time_sec=i / FPS, status="ok",
            quad=[[0.0, 0.0], [1.0, 0.0], [1.0, 1.0], [0.0, 1.0]],
            homography=[[float(v) for v in row] for row in h_mat],
            homography_inv=[[float(v) for v in row]
                            for row in np.linalg.inv(h_mat)],
        ))
    return tracks


def simple_event(text: str, start: float, end: float) -> dict:
    return {
        "start_time": format_ass_time(start),
        "end_time": format_ass_time(end),
        "style": "Scene",
        "name": "motion",
        "tags": "{\\an5\\move(0.0,0.0,0.0,0.0)}",
        "body": text,
    }


def make_white_plane() -> np.ndarray:
    plane = np.full((PLANE_H, PLANE_W, 3), 250, np.uint8)
    for _text, (x1, y1, x2, _y2) in ROWS:
        plane[int(y1) + 4:int(y1) + 8, int(x1) + 4:int(x2) - 4] = 10
    return plane


def make_noisy_plane(seed: int = 7) -> np.ndarray:
    rng = np.random.default_rng(seed)
    return rng.integers(0, 256, (PLANE_H, PLANE_W, 3), dtype=np.uint8)


def _run_dynamic(mode, plane, tracks=None, **kw):
    if tracks is None:
        tracks = make_translation_tracks()
    events = [simple_event(t, 0.0, 9 / FPS) for t, _b in ROWS]
    return apply_policy(
        events, ROWS, plane, tracks, _cfg(mode, **kw), PLANE_W, PLANE_H)


def _cfg(mode: str, **kw):
    from core.scene_text_policy import SceneTextPolicyConfig

    return SceneTextPolicyConfig(mode=mode, **kw)


# ---------------------------------------------------------------------------
# 动态路径:mask_only = mask 的遮罩 + 文本 Comment 化
# ---------------------------------------------------------------------------

class TestApplyMaskOnlyDynamic:
    def test_masks_identical_to_mask_mode_text_become_comments(self):
        mask_out = _run_dynamic("mask", make_white_plane())
        only_out = _run_dynamic("mask_only", make_white_plane())
        assert only_out.applied_mode == "mask_only" and only_out.notes == []
        masks_only = [ev for ev in only_out.events if "\\p1" in ev["tags"]]
        texts_only = [ev for ev in only_out.events if "\\p1" not in ev["tags"]]
        masks_ref = [ev for ev in mask_out.events if "\\p1" in ev["tags"]]
        texts_ref = [ev for ev in mask_out.events if "\\p1" not in ev["tags"]]
        # 遮罩事件与 mask 模式逐字段一致(几何/取色/时间/轨迹标签)
        assert [{k: v for k, v in ev.items() if k != "line_idx"}
                for ev in masks_only] == \
               [{k: v for k, v in ev.items() if k != "line_idx"}
                for ev in masks_ref]
        assert len(masks_only) == 2  # 两块(标题 / 正文两行并块)
        for ev in masks_only:
            assert ev["layer"] == 0
            assert ev["tags"].startswith("{\\an7\\p1")
            assert ev["base_color"] == (250, 250, 250)
        # 文本事件:layer 1 保留、comment=True、时间/正文原样
        assert len(texts_only) == len(texts_ref) == 3
        for ev in texts_only:
            assert ev["layer"] == 1
            assert ev.get("comment") is True
            assert ev["body"] in {"标题", "正文一", "正文二"}
            assert ev["start_time"] == "0:00:00.00"
            assert ev["end_time"] == format_ass_time(9 / FPS)

    def test_mask_mode_events_not_commented(self):
        # 回归护栏:mask 模式的文本事件不带 comment 键(既有输出不变)
        out = _run_dynamic("mask", make_white_plane())
        assert all("comment" not in ev for ev in out.events)

    def test_noisy_background_falls_back_to_external(self):
        out = _run_dynamic("mask_only", make_noisy_plane())
        assert out.applied_mode == "external"
        assert any("mask_only->external" in note for note in out.notes)
        # 回退后无遮罩、无 comment 事件(external 单条 NoteBox)
        assert not [ev for ev in out.events if "\\p1" in ev["tags"]]
        assert not [ev for ev in out.events if ev.get("comment")]

    def test_empty_tracks_falls_back_to_external(self):
        out = _run_dynamic("mask_only", make_white_plane(), tracks=[])
        assert out.applied_mode == "external"
        assert any("no tracking frames" in note for note in out.notes)


# ---------------------------------------------------------------------------
# 静态路径
# ---------------------------------------------------------------------------

class TestApplyMaskOnlyStatic:
    def test_specs_match_mask_mode_text_commented(self):
        from core.scene_text_policy import apply_policy_static as static

        mask_specs, mask_applied, _ = static(
            ROWS, make_white_plane(), _cfg("mask"), PLANE_W, PLANE_H)
        only_specs, only_applied, notes = static(
            ROWS, make_white_plane(), _cfg("mask_only"), PLANE_W, PLANE_H)
        assert only_applied == "mask_only" and notes == []
        only_masks = [s for s in only_specs if s["kind"] == "mask"]
        ref_masks = [s for s in mask_specs if s["kind"] == "mask"]
        assert only_masks == ref_masks
        assert len(only_masks) == 2
        texts = [s for s in only_specs if s["kind"] == "text"]
        assert len(texts) == 3
        for s in texts:
            assert s["layer"] == 1
            assert s.get("comment") is True
            assert s["body"] in {"标题", "正文一", "正文二"}

    def test_noisy_background_falls_back_to_external(self):
        from core.scene_text_policy import apply_policy_static as static

        specs, applied, notes = static(
            ROWS, make_noisy_plane(), _cfg("mask_only"), PLANE_W, PLANE_H)
        assert applied == "external"
        assert any("mask_only->external" in n for n in notes)
        assert len(specs) == 1 and specs[0]["kind"] == "note"


# ---------------------------------------------------------------------------
# Writer:Comment 行
# ---------------------------------------------------------------------------

class TestWriters:
    def test_motion_write_ass_comment_lines(self, tmp_path):
        from scripts.motion_ass import write_ass

        events = [
            {  # 遮罩事件:Dialogue
                "start_time": "0:00:00.00", "end_time": "0:00:00.36",
                "style": "Scene", "name": "motion",
                "tags": "{\\an7\\p1\\bord0\\1c&HFAFAFA&}", "body": "",
                "layer": 0,
            },
            {  # mask_only 文本参考行:Comment
                "start_time": "0:00:00.00", "end_time": "0:00:00.36",
                "style": "Scene", "name": "motion",
                "tags": "{\\an5\\move(0.0,0.0,0.0,0.0)}", "body": "标题",
                "layer": 1, "comment": True,
            },
        ]
        out = tmp_path / "out.ass"
        n = write_ass(str(out), events, 300, 400, "t")
        assert n == 2
        lines = [ln for ln in out.read_text(encoding="utf-8-sig").splitlines()
                 if ln.startswith(("Dialogue:", "Comment:"))]
        assert lines[0].startswith("Dialogue: 0,")
        assert lines[1].startswith("Comment: 1,")
        assert "标题" in lines[1]

    def test_generator_mask_only_end_to_end(self, tmp_path):
        """主流水线:roi 策略 mask_only → 遮罩 Dialogue(layer 0)+ 原文
        Comment(layer 1),不再渲染原位文本 Dialogue。"""
        W, H, fps = 320, 240, 25.0
        sign_box = (30, 85, 130, 115)
        frame = np.full((H, W, 3), 250, np.uint8)
        x1, y1, x2, y2 = sign_box
        frame[y1 + 5:y1 + 11, x1 + 4:x2 - 4] = 20
        frame[y1 + 19:y1 + 25, x1 + 4:x2 - 4] = 20
        video = tmp_path / "in.avi"
        writer = cv2.VideoWriter(str(video), cv2.VideoWriter_fourcc(*"FFV1"),
                                 fps, (W, H))
        assert writer.isOpened()
        for _ in range(60):
            writer.write(frame)
        writer.release()

        from core.subtitle_generator import OCRToASSOptimizer

        items = []
        for f in range(5, 21):
            poly = [[x1, y1], [x2, y1], [x2, y2], [x1, y2]]
            data = {
                "dt_polys": [poly], "rec_polys": [poly],
                "rec_texts": ["店铺招牌"], "rec_scores": [0.95],
                "rec_boxes": [list(sign_box)],
            }
            items.append((data, f, "roi_0", f / fps))

        out = tmp_path / "out.ass"
        conv = OCRToASSOptimizer(
            video_path=str(video), output_path=str(out), fps=fps,
            width=W, height=H,
            roi_scene_text_policies={"roi_0": "mask_only"},
            roi_analysis_rects={"roi_0": (10, 60, 150, 140)})
        conv.convert_from_memory(iter(items))
        text = out.read_text(encoding="utf-8-sig")

        masks = [ln for ln in text.splitlines()
                 if ln.startswith("Dialogue: 0,") and "\\p1" in ln]
        assert len(masks) == 1
        comments = [ln for ln in text.splitlines()
                    if ln.startswith("Comment: 1,") and "店铺招牌" in ln]
        assert len(comments) == 1
        # 原文不再以 Dialogue 渲染(播放器画面只剩遮罩)
        assert not [ln for ln in text.splitlines()
                    if ln.startswith("Dialogue: 1,") and "店铺招牌" in ln]


# ---------------------------------------------------------------------------
# 管线接入:收集入口与 CLI 参数
# ---------------------------------------------------------------------------

class TestPolicyPlumbing:
    def test_collect_roi_scene_text_options_accepts_mask_only(self):
        from core.pipeline_worker import collect_roi_scene_text_options

        policies, _rects = collect_roi_scene_text_options(
            [{"type": "rect", "points": [0, 0, 10, 10],
              "scene_text_policy": "mask_only"}])
        assert policies == {"roi_0": "mask_only"}

    def test_cli_parse_args_accepts_mask_only(self):
        from cli import parse_args

        args = parse_args(["in.mp4", "--scene-text-policy", "mask_only"])
        assert args.scene_text_policy == "mask_only"

    def test_scene_text_policy_config_accepts_mask_only(self):
        # dataclass __post_init__ 模式校验(构建 config 的唯一入口)
        assert _cfg("mask_only").mode == "mask_only"
