# tests/test_motion_ass_cli.py
"""端到端 CLI(scripts/motion_ass.py)离线集成测试(Task 6)。

用合成"文字卡片"视频(白底卡片 + 两行黑"文字"条,纯平移 + 轻微缩放,
quad 取卡片四角)跑 main(),OCR 以 mock 注入(在展开图上阈值找暗色"文字"
条返回行框,不加载真模型),断言全链路:

- 产出 .ass 存在:含 \\move、无帧级 \\pos 碎片段(该合成场景应走标签阶梯
  3「单段直线」);Name 字段为 motion;事件时间格式合法且按 start 有序;
- ASS 头与主流水线默认样式一致(Scene 行、PlayRes = 视频分辨率),
  以 UTF-8-sig 写出;
- \\move 端点与真值一致(验证「OCR 行框(quad 窗口坐标)+ 外接矩形偏移
  = 初始帧平面坐标 → build_line_tracks(ref=start_frame) → 画面坐标」
  整条坐标链无镜像、无错位);
- --trajectory-json 写出且 save/load 往返一致;
- 逆时针 quad(会镜像展开图)被拒绝、以非零码退出;
- --config-json 非法键被拒绝;OCR 全空时优雅退出非零。

视频用 FFV1 无损编码(与 tests/test_keyframe_selector.py 同一构造模式);
轨迹由真实 track_plane 产出(管线第一步不做 mock)。
"""

from __future__ import annotations

import json
import os
import re
import sys

import cv2
import numpy as np
import pytest

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from core import keyframe_selector  # noqa: E402
from core.scene_plane_tracker import load_trajectory  # noqa: E402
from core.text_utils import is_noise_text  # noqa: E402
from scripts import motion_ass as motion_cli  # noqa: E402

FRAME_W, FRAME_H = 320, 240
CARD_W, CARD_H = 160, 100
CARD_X0, CARD_Y0 = 90, 80      # 卡片左上角在初始帧画面(=统一平面坐标)中的位置
FPS = 8
N_FRAMES = 24
TX_STEP = 2.5                  # 每帧水平位移(px)
SCALE_STEP = 0.001             # 每帧缩放增量(轻微)

# 两行"文字"黑条:卡片局部坐标(= 统一平面坐标 - (CARD_X0, CARD_Y0))。
# 第一行更宽、第二行更短且靠右,保证卡片左右不对称(可检出镜像/错位)。
TEXT_BARS = [
    (18, 22, 126, 36),
    (40, 62, 96, 76),
]

DIALOGUE_RE = re.compile(
    r"^Dialogue: 0,(\d+:\d{2}:\d{2}\.\d{2}),(\d+:\d{2}:\d{2}\.\d{2}),"
    r"Scene,motion,0,0,0,,")


# ---------------------------------------------------------------------------
# 合成视频构造(参考 tests/test_scene_plane_tracker.py / test_keyframe_selector.py)
# ---------------------------------------------------------------------------

def make_card() -> np.ndarray:
    """白底卡片:中灰随机色块提供 ORB 跟踪纹理,再叠两行纯黑"文字"条。"""
    card = np.full((CARD_H, CARD_W, 3), 245, np.uint8)
    rng = np.random.default_rng(7)
    for row in range(4):
        y = 6 + row * 24
        x = 8
        while x < CARD_W - 24:
            seg = int(rng.integers(18, 50))
            color = tuple(int(c) for c in rng.integers(100, 180, 3))
            cv2.rectangle(card, (x, y), (min(x + seg, CARD_W - 8), y + 12), color, -1)
            x += seg + int(rng.integers(6, 14))
    for x1, y1, x2, y2 in TEXT_BARS:
        cv2.rectangle(card, (x1, y1), (x2, y2), (10, 10, 10), -1)
    return card


def motion_mat(tx: float = 0.0, scale: float = 1.0) -> np.ndarray:
    """卡片局部像素 → 画面的 3x3 变换:平移到基准位后再施加运动。"""
    cx, cy = (CARD_W - 1) / 2.0, (CARD_H - 1) / 2.0
    to_origin = np.array([[1, 0, -cx], [0, 1, -cy], [0, 0, 1]], float)
    scale_m = np.diag([scale, scale, 1.0])
    to_center = np.array([
        [1, 0, CARD_X0 + cx + tx],
        [0, 1, CARD_Y0 + cy],
        [0, 0, 1],
    ], float)
    return to_center @ scale_m @ to_origin


def compose_scene(card: np.ndarray, m: np.ndarray):
    frame = np.full((FRAME_H, FRAME_W, 3), 200, np.uint8)
    cv2.warpPerspective(
        card, m, (FRAME_W, FRAME_H), dst=frame,
        flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_TRANSPARENT)
    corners = np.array([
        [0, 0], [CARD_W - 1, 0], [CARD_W - 1, CARD_H - 1], [0, CARD_H - 1]
    ], dtype=np.float32)
    gt = cv2.perspectiveTransform(
        corners.reshape(-1, 1, 2), m.astype(np.float32)).reshape(-1, 2)
    return frame, gt


def build_case(tmp_path, n: int = N_FRAMES, scale_step: float = SCALE_STEP):
    """写合成视频(FFV1),返回 (video_path, 初始帧四角 quad)。"""
    card = make_card()
    frames, quad0 = [], None
    for i in range(n):
        frame, gt = compose_scene(
            card, motion_mat(tx=TX_STEP * i, scale=1.0 + scale_step * i))
        frames.append(frame)
        if i == 0:
            quad0 = [[float(x), float(y)] for x, y in gt]
    video_path = str(tmp_path / "move.avi")
    writer = cv2.VideoWriter(
        video_path, cv2.VideoWriter_fourcc(*"FFV1"), float(FPS),
        (FRAME_W, FRAME_H))
    if not writer.isOpened():
        pytest.skip("no usable video codec (FFV1) for motion ass cli tests")
    for frame in frames:
        writer.write(frame)
    writer.release()
    return video_path, quad0


# ---------------------------------------------------------------------------
# mock OCR:在展开图上阈值找暗色"文字"条(不加载真模型)
# ---------------------------------------------------------------------------

def make_mock_ocr(calls: list):
    """返回可注入的 ocr_fn:阈值找黑条 → 逐行 rec_* 结构(窗口像素坐标)。"""

    def ocr_fn(img):
        calls.append(img.shape)
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        mask = (gray < 60).astype(np.uint8)
        contours, _ = cv2.findContours(
            mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        rows = []
        for contour in contours:
            x, y, w, h = cv2.boundingRect(contour)
            if w * h < 30:
                continue
            rows.append((y, x, x + w, y + h))
        rows.sort()
        ocr = {"dt_polys": [], "rec_polys": [], "rec_texts": [],
               "rec_scores": [], "rec_boxes": []}
        for i, (y1, x1, x2, y2) in enumerate(rows):
            poly = [[float(x1), float(y1)], [float(x2), float(y1)],
                    [float(x2), float(y2)], [float(x1), float(y2)]]
            ocr["dt_polys"].append([list(p) for p in poly])
            ocr["rec_polys"].append(poly)
            ocr["rec_texts"].append(f"LINE{i}")
            ocr["rec_scores"].append(0.95)
            ocr["rec_boxes"].append([int(x1), int(y1), int(x2), int(y2)])
        return ocr

    return ocr_fn


def _ass_time_to_sec(s: str) -> float:
    h, m, rest = s.split(":")
    return int(h) * 3600 + int(m) * 60 + float(rest)


def _dialogue_lines(text: str) -> list:
    return [line for line in text.splitlines() if line.startswith("Dialogue:")]


# ---------------------------------------------------------------------------
# quad 输入解析与校验(纯函数)
# ---------------------------------------------------------------------------

class TestQuadInput:
    def test_parse_quad_spec(self):
        assert motion_cli.parse_quad_spec("1,2 3,4 5,6 7,8") == [
            [1.0, 2.0], [3.0, 4.0], [5.0, 6.0], [7.0, 8.0]]

    def test_parse_quad_spec_rejects_wrong_count(self):
        with pytest.raises(ValueError):
            motion_cli.parse_quad_spec("1,2 3,4 5,6")

    def test_load_quad_file_dict_and_bare_list(self, tmp_path):
        dict_path = tmp_path / "quad.json"
        dict_path.write_text(json.dumps(
            {"video": "clip.mp4", "frame": 0,
             "quad": [[1, 2], [3, 4], [5, 6], [7, 8]]}), encoding="utf-8")
        assert motion_cli.load_quad_file(str(dict_path)) == [
            [1, 2], [3, 4], [5, 6], [7, 8]]

        bare_path = tmp_path / "bare.json"
        bare_path.write_text(json.dumps([[1, 2], [3, 4], [5, 6], [7, 8]]),
                             encoding="utf-8")
        assert motion_cli.load_quad_file(str(bare_path)) == [
            [1, 2], [3, 4], [5, 6], [7, 8]]

    def test_validate_accepts_clockwise(self):
        quad = motion_cli.validate_quad(
            [[80, 70], [240, 70], [240, 170], [80, 170]])
        assert quad == [[80.0, 70.0], [240.0, 70.0],
                        [240.0, 170.0], [80.0, 170.0]]

    def test_validate_rejects_counter_clockwise(self):
        with pytest.raises(ValueError, match="counter-clockwise"):
            motion_cli.validate_quad(
                [[80, 170], [240, 170], [240, 70], [80, 70]])

    def test_validate_rejects_bad_geometry(self):
        with pytest.raises(ValueError):
            motion_cli.validate_quad([[0, 0], [10, 0], [0, 10], [10, 10]])  # 自交
        with pytest.raises(ValueError):
            motion_cli.validate_quad([[0, 0], [5, 0], [5, 5], [0, 5]])     # 面积过小
        with pytest.raises(ValueError):
            motion_cli.validate_quad([[0, 0], [10, 0], [10, 10]])          # 不是 4 点
        with pytest.raises(ValueError):
            motion_cli.validate_quad(
                [[0, 0], [float("nan"), 0], [10, 10], [0, 10]])            # 非有限值
        with pytest.raises(ValueError):
            motion_cli.validate_quad(
                {"bad": "structure"})                                      # 结构非法


# ---------------------------------------------------------------------------
# 端到端(main + 注入 mock OCR)
# ---------------------------------------------------------------------------

def test_end_to_end_quad_file_writes_motion_ass(tmp_path):
    video_path, quad0 = build_case(tmp_path)
    quad_file = tmp_path / "quad.json"
    quad_file.write_text(json.dumps(
        {"video": os.path.basename(video_path), "frame": 0, "quad": quad0}),
        encoding="utf-8")
    out_ass = tmp_path / "out.ass"
    traj_path = tmp_path / "traj.json"
    calls: list = []

    # --ocr-engine 显式给出但 ocr_fn 已注入:绝不能构造真引擎
    rc = motion_cli.main(
        ["--video", video_path, "--out", str(out_ass),
         "--quad-file", str(quad_file),
         "--trajectory-json", str(traj_path),
         "--ocr-engine", "rapid"],
        ocr_fn=make_mock_ocr(calls))

    assert rc == 0, "main() should exit 0 on the synthetic pipeline"
    assert out_ass.exists()

    # UTF-8-sig(与主流水线一致)
    raw = out_ass.read_bytes()
    assert raw.startswith(b"\xef\xbb\xbf")
    text = raw.decode("utf-8-sig")

    # ASS 头:与主流水线默认样式一致(Scene 行 fontsize=height*0.04=10)
    assert "[Script Info]" in text
    assert f"PlayResX: {FRAME_W}" in text
    assert f"PlayResY: {FRAME_H}" in text
    assert "Style: Scene,思源黑体 CN,10," \
        "&H00FFFFFF,&H000000FF,&H00000000,&H00000000,0,0,0,0,100,100,0,0,1,1,0,5,10,10,10,1" \
        in text
    assert "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text" in text

    # 事件行:Name=motion、时间格式合法、含 \move、无 \pos 碎片段
    dialogue = _dialogue_lines(text)
    assert len(dialogue) == 2, f"expected 1 event per text line, got:\n{dialogue}"
    starts = []
    text_fields = []
    for line in dialogue:
        m = DIALOGUE_RE.match(line)
        assert m, f"bad Dialogue line: {line}"
        st, en = m.group(1), m.group(2)
        assert _ass_time_to_sec(en) > _ass_time_to_sec(st)
        starts.append(_ass_time_to_sec(st))
        text_field = line.split(",", 9)[9]
        assert "\\move(" in text_field
        assert "\\pos(" not in text_field
        text_fields.append(text_field)
    assert starts == sorted(starts)
    # 正文 = 文本字段去掉前导 {...} override 块
    bodies = [re.sub(r"^\{[^}]*\}", "", b) for b in text_fields]
    assert {"LINE0", "LINE1"} == set(bodies)

    # 坐标链真值:LINE0 平面中心 (CARD_X0+72, CARD_Y0+29) = (162, 109);
    # 逐帧 +2.5px 平移,平滑(窗口 5,首帧窗口 [0..2])后首端 ≈ 164.5,末端 ≈ 219.5。
    line0 = next(b for b in text_fields if b.endswith("LINE0"))
    m = re.search(r"\\move\((-?[\d.]+),(-?[\d.]+),(-?[\d.]+),(-?[\d.]+)", line0)
    assert m, line0
    x1, y1, x2, y2 = (float(g) for g in m.groups())
    assert x1 == pytest.approx(CARD_X0 + 72 + TX_STEP, abs=4.0)
    assert y1 == pytest.approx(CARD_Y0 + 29, abs=4.0)
    assert x2 == pytest.approx(CARD_X0 + 72 + TX_STEP * (N_FRAMES - 1), abs=4.0)
    assert y2 == pytest.approx(CARD_Y0 + 29, abs=4.0)

    # mock OCR:每个关键帧一次,展开图即 quad 窗口尺寸(159x99)
    assert len(calls) == 3
    assert set(calls) == {(CARD_H - 1, CARD_W - 1, 3)}

    # 轨迹 JSON 往返
    assert traj_path.exists()
    loaded = load_trajectory(str(traj_path))
    assert loaded["video"] == os.path.abspath(video_path)
    assert len(loaded["frames"]) == N_FRAMES
    assert all(fr["status"] == "ok" for fr in loaded["frames"])
    assert np.allclose(loaded["init_quad"], quad0)  # 轨迹保存的 init_quad 与输入一致


def test_end_to_end_quad_string_with_start_frame(tmp_path):
    video_path, quad0 = build_case(tmp_path, n=14, scale_step=0.0)
    quad_spec = " ".join(f"{x:.0f},{y:.0f}" for x, y in quad0)
    out_ass = tmp_path / "out2.ass"
    calls: list = []

    rc = motion_cli.main(
        ["--video", video_path, "--out", str(out_ass),
         "--quad", quad_spec, "--start-frame", "2", "--end-frame", "11",
         "--keyframe-count", "2", "--min-gap-sec", "0.25"],
        ocr_fn=make_mock_ocr(calls))

    assert rc == 0
    assert out_ass.exists()
    text = out_ass.read_text(encoding="utf-8-sig")
    dialogue = _dialogue_lines(text)
    assert dialogue
    for line in dialogue:
        assert DIALOGUE_RE.match(line), line
        assert "\\move(" in line
    assert len(calls) == 2  # --keyframe-count 2


def test_counter_clockwise_quad_rejected(tmp_path, capsys):
    video_path, quad0 = build_case(tmp_path, n=6, scale_step=0.0)
    ccw = list(reversed(quad0))  # 逆时针:会镜像展开图
    quad_spec = " ".join(f"{x:.0f},{y:.0f}" for x, y in ccw)
    out_ass = tmp_path / "bad.ass"

    rc = motion_cli.main(
        ["--video", video_path, "--out", str(out_ass), "--quad", quad_spec],
        ocr_fn=make_mock_ocr([]))

    assert rc != 0
    assert not out_ass.exists()
    assert "counter-clockwise" in capsys.readouterr().err


def test_invalid_config_key_rejected(tmp_path, capsys):
    cfg = tmp_path / "cfg.json"
    cfg.write_text(json.dumps({"bogus_key": 1}), encoding="utf-8")
    out_ass = tmp_path / "o.ass"

    rc = motion_cli.main(
        ["--video", "whatever.avi", "--out", str(out_ass),
         "--quad", "0,0 10,0 10,10 0,10", "--config-json", str(cfg)],
        ocr_fn=None)

    assert rc != 0
    assert not out_ass.exists()
    assert "bogus_key" in capsys.readouterr().err


def test_no_text_lines_exits_nonzero(tmp_path):
    video_path, quad0 = build_case(tmp_path, n=6, scale_step=0.0)
    quad_spec = " ".join(f"{x:.0f},{y:.0f}" for x, y in quad0)
    out_ass = tmp_path / "empty.ass"

    def empty_ocr(_img):
        return {"dt_polys": [], "rec_polys": [], "rec_texts": [],
                "rec_scores": [], "rec_boxes": []}

    rc = motion_cli.main(
        ["--video", video_path, "--out", str(out_ass), "--quad", quad_spec],
        ocr_fn=empty_ocr)

    assert rc != 0
    assert not out_ass.exists()


# ---------------------------------------------------------------------------
# 屏幕亮度自适应(--auto-brightness)
# ---------------------------------------------------------------------------

# 仿真实数据(基线 247)的亮度比值折线,缩放到该合成视频时长(~3s)内:
# 恒亮 → 1.2s 变暗至 174/247 → 1.5s 谷底 30/247 → 2.0s 保持 → 2.6s 回亮。
BRIGHT_CURVE = [
    (0.0, 1.0),
    (1.2, 174.0 / 247.0),
    (1.5, 30.0 / 247.0),
    (2.0, 30.0 / 247.0),
    (2.6, 1.0),
]


def _text_fields(text: str) -> list:
    """Dialogue 行的 Text 字段(前 9 列之后),按文件序。"""
    return [line.split(",", 9)[9] for line in _dialogue_lines(text)]


def _dialogue_records(text: str) -> list:
    """[(start_sec, end_sec, text_field)],按文件序。"""
    out = []
    for line in _dialogue_lines(text):
        m = DIALOGUE_RE.match(line)
        assert m, line
        out.append((_ass_time_to_sec(m.group(1)), _ass_time_to_sec(m.group(2)),
                    line.split(",", 9)[9]))
    return out


def test_auto_brightness_appends_independent_override_block(tmp_path, monkeypatch):
    video_path, quad0 = build_case(tmp_path)
    quad_file = tmp_path / "quad.json"
    quad_file.write_text(json.dumps(
        {"video": os.path.basename(video_path), "frame": 0, "quad": quad0}),
        encoding="utf-8")
    out_plain = tmp_path / "plain.ass"
    out_bright = tmp_path / "bright.ass"
    # 亮度测量不重解码视频(接线测试注入真值曲线;曲线正确性归
    # test_screen_luma / test_motion_ass 覆盖)
    monkeypatch.setattr(
        "core.screen_luma.measure_luma_curve_with_baseline",
        lambda *a, **k: (list(BRIGHT_CURVE), 247.0))

    common = ["--video", video_path, "--quad-file", str(quad_file)]
    rc0 = motion_cli.main(
        common + ["--out", str(out_plain)], ocr_fn=make_mock_ocr([]))
    assert rc0 == 0

    # brightness_* 为合法 config 键(--config-json 可覆盖)
    cfg = tmp_path / "cfg.json"
    cfg.write_text(json.dumps({
        "brightness_tol": 8.0,
        "brightness_baseline_percentile": 90.0,
        "brightness_use_color": True,
        "brightness_use_alpha": True,
    }), encoding="utf-8")
    rc = motion_cli.main(
        common + ["--out", str(out_bright), "--auto-brightness",
                  "--config-json", str(cfg)],
        ocr_fn=make_mock_ocr([]))
    assert rc == 0

    # 开启状态:作为独立的附加 override 块接在既有 tags 之后
    # ({原有tags}{亮度链}body),既有标签内容逐字符不变、事件时间不变;
    # 亮度链与直接调用 brightness_tag_chain(同一解析事件时间)完全一致。
    from core.motion_ass import brightness_tag_chain as _chain

    plain_recs = _dialogue_records(out_plain.read_text(encoding="utf-8-sig"))
    bright_recs = _dialogue_records(out_bright.read_text(encoding="utf-8-sig"))
    assert len(plain_recs) == len(bright_recs) == 2
    for (st, en, plain), (st2, en2, bright) in zip(plain_recs, bright_recs):
        # 关闭状态(回归):无任何颜色/透明度标签;两次运行事件时间一致
        assert (st, en) == (st2, en2)
        assert "\\1c" not in plain and "\\alpha&" not in plain
        body = re.sub(r"^\{[^}]*\}", "", plain)
        prefix = plain[:len(plain) - len(body)]  # 原有 override 块
        expected = "{" + _chain(BRIGHT_CURVE, st, en) + "}"
        assert "\\1c&H" in expected and "\\alpha&H" in expected
        assert bright == prefix + expected + body
        # 链内 \t 毫秒端点相接
        spans = [(int(a), int(b))
                 for a, b in re.findall(r"\\t\((\d+),(\d+),", expected)]
        for (_a, b), (a1, _b1) in zip(spans, spans[1:]):
            assert b == a1


def test_auto_brightness_flat_curve_skips_silently(tmp_path, monkeypatch):
    video_path, quad0 = build_case(tmp_path, n=8, scale_step=0.0)
    quad_spec = " ".join(f"{x:.0f},{y:.0f}" for x, y in quad0)
    out_ass = tmp_path / "flat.ass"
    monkeypatch.setattr(
        "core.screen_luma.measure_luma_curve_with_baseline",
        lambda *a, **k: ([(0.0, 1.0), (5.0, 1.0)], 247.0))  # 全程恒亮

    rc = motion_cli.main(
        ["--video", video_path, "--out", str(out_ass), "--quad", quad_spec,
         "--auto-brightness"],
        ocr_fn=make_mock_ocr([]))

    assert rc == 0
    for field in _text_fields(out_ass.read_text(encoding="utf-8-sig")):
        assert "\\1c" not in field and "\\alpha&" not in field


def test_auto_brightness_no_ok_frames_skips(tmp_path, monkeypatch):
    video_path, quad0 = build_case(tmp_path, n=8, scale_step=0.0)
    quad_spec = " ".join(f"{x:.0f},{y:.0f}" for x, y in quad0)
    out_ass = tmp_path / "nook.ass"
    monkeypatch.setattr(
        "core.screen_luma.measure_luma_curve_with_baseline",
        lambda *a, **k: ([], 0.0))  # 无 ok 帧 → 静默跳过

    rc = motion_cli.main(
        ["--video", video_path, "--out", str(out_ass), "--quad", quad_spec,
         "--auto-brightness"],
        ocr_fn=make_mock_ocr([]))

    assert rc == 0
    text = out_ass.read_text(encoding="utf-8-sig")
    assert _dialogue_lines(text)
    assert "\\1c" not in text and "\\alpha&" not in text


def test_auto_brightness_use_color_off_via_config(tmp_path, monkeypatch):
    video_path, quad0 = build_case(tmp_path, n=8, scale_step=0.0)
    quad_spec = " ".join(f"{x:.0f},{y:.0f}" for x, y in quad0)
    out_ass = tmp_path / "alphaonly.ass"
    monkeypatch.setattr(
        "core.screen_luma.measure_luma_curve_with_baseline",
        lambda *a, **k: (list(BRIGHT_CURVE), 247.0))
    cfg = tmp_path / "cfg.json"
    cfg.write_text(json.dumps({"brightness_use_color": False}), encoding="utf-8")

    rc = motion_cli.main(
        ["--video", video_path, "--out", str(out_ass), "--quad", quad_spec,
         "--auto-brightness", "--config-json", str(cfg)],
        ocr_fn=make_mock_ocr([]))

    assert rc == 0
    for field in _text_fields(out_ass.read_text(encoding="utf-8-sig")):
        assert "\\1c" not in field
        assert "\\alpha&H" in field


def test_auto_brightness_per_line_uses_line_curves(tmp_path, monkeypatch):
    """brightness_per_line(屏幕局部调暗的背景适配):事件经 line_idx 取
    所属行的亮度曲线;行曲线缺失/恒亮时回退整平面曲线。"""
    video_path, quad0 = build_case(tmp_path)
    quad_file = tmp_path / "quad.json"
    quad_file.write_text(json.dumps(
        {"video": os.path.basename(video_path), "frame": 0, "quad": quad0}),
        encoding="utf-8")
    out = tmp_path / "perline.ass"
    # 整平面曲线恒亮(若被误用,两行都不带标签);行曲线:行 0 恒亮、
    # 行 1 恒暗 0.5 → 只有行 1 的事件带 \1c&H808080&\alpha&H80& 基值。
    monkeypatch.setattr(
        "core.screen_luma.measure_luma_curve_with_baseline",
        lambda *a, **k: ([(0.0, 1.0), (3.0, 1.0)], 247.0))
    monkeypatch.setattr(
        "core.screen_luma.measure_line_luma_curves",
        lambda *a, **k: ([[(0.0, 1.0), (3.0, 1.0)], [(0.0, 0.5)]],
                         [247.0, 247.0]))
    cfg = tmp_path / "cfg.json"
    cfg.write_text(json.dumps({"brightness_per_line": True}), encoding="utf-8")

    rc = motion_cli.main(
        ["--video", video_path, "--quad-file", str(quad_file),
         "--out", str(out), "--auto-brightness", "--config-json", str(cfg)],
        ocr_fn=make_mock_ocr([]))

    assert rc == 0
    texts = _text_fields(out.read_text(encoding="utf-8-sig"))
    assert len(texts) == 2
    tagged = [t for t in texts if "\\1c&H808080&\\alpha&H80&" in t]
    assert len(tagged) == 1


def test_occlusion_clip_end_to_end(tmp_path, monkeypatch):
    r"""--occlusion-clip:遮挡多边形(平面坐标)映射进事件 \iclip 标签。"""
    video_path, quad0 = build_case(tmp_path)
    quad_file = tmp_path / "quad.json"
    quad_file.write_text(json.dumps(
        {"video": os.path.basename(video_path), "frame": 0, "quad": quad0}),
        encoding="utf-8")
    out_plain = tmp_path / "noclip.ass"
    out_clip = tmp_path / "clip.ass"
    # 手工遮挡表:多边形(平面坐标)盖住两行行框(≈108..216/102..116 与
    # 130..186/142..156);遮挡帧取事件跨度内的采样帧 0 与 23(链尾结束
    # 时间延伸 1 帧,跨度覆盖到 23;末时间按 centisecond 截断回解析)
    # → 动画路径;真检测归 test_occlusion_mask。
    poly = [[130.0, 100.0], [200.0, 100.0], [200.0, 160.0], [130.0, 160.0]]
    import copy

    occl = {f: [copy.deepcopy(poly)] for f in (0, 23)}
    monkeypatch.setattr(
        "core.occlusion_mask.collect_occlusions", lambda *a, **k: dict(occl))
    common = ["--video", video_path, "--quad-file", str(quad_file)]
    assert motion_cli.main(common + ["--out", str(out_plain)],
                           ocr_fn=make_mock_ocr([])) == 0
    assert motion_cli.main(common + ["--out", str(out_clip),
                                     "--occlusion-clip"],
                           ocr_fn=make_mock_ocr([])) == 0
    plain = _text_fields(out_plain.read_text(encoding="utf-8-sig"))
    clipped = _text_fields(out_clip.read_text(encoding="utf-8-sig"))
    assert len(plain) == len(clipped) == 2
    assert all("\\iclip(" not in t for t in plain)
    assert all("\\iclip(" in t for t in clipped)
    # 首末采样帧(0、23)都有遮挡且多边形个数相同 → 动画形式
    # \iclip + \t(0,段长ms,\iclip(...));段长 = 0.00 → 3.00s(链尾 +1 帧)
    assert all(t.count("\\iclip(") == 2 for t in clipped)
    assert all("\\t(0,3000,\\iclip(" in t for t in clipped)


# ---------------------------------------------------------------------------
# 场景文字显示策略(--scene-text-policy,设计 §3/§4/§5)
# ---------------------------------------------------------------------------

def make_clean_card() -> np.ndarray:
    """mask 友好卡片:白底 + 空心黑"文字"条(笔画占比低,采样中位停在背景);
    ORB 纹理色块只放在卡片上下边缘,远离文字块(否则采样区杂色会触发回退)。"""
    card = np.full((CARD_H, CARD_W, 3), 245, np.uint8)
    rng = np.random.default_rng(11)
    for x in range(8, CARD_W - 24, 30):
        color = tuple(int(c) for c in rng.integers(100, 180, 3))
        cv2.rectangle(card, (x, 2), (min(x + 22, CARD_W - 8), 12), color, -1)
        cv2.rectangle(card, (x, CARD_H - 12), (min(x + 22, CARD_W - 8), CARD_H - 2),
                      color, -1)
    for x1, y1, x2, y2 in TEXT_BARS:
        cv2.rectangle(card, (x1, y1), (x2, y2), (10, 10, 10), 2)  # 空心条
    return card


def motion_mat_at(tx: float, scale: float, x0: int, y0: int,
                  w: int, h: int) -> np.ndarray:
    """卡片局部像素 → 画面的 3x3 变换(原点/尺寸可参量化)。"""
    cx, cy = (w - 1) / 2.0, (h - 1) / 2.0
    to_origin = np.array([[1, 0, -cx], [0, 1, -cy], [0, 0, 1]], float)
    scale_m = np.diag([scale, scale, 1.0])
    to_center = np.array([
        [1, 0, x0 + cx + tx],
        [0, 1, y0 + cy],
        [0, 0, 1],
    ], float)
    return to_center @ scale_m @ to_origin


def build_case_custom(tmp_path, card, n: int = N_FRAMES,
                      scale_step: float = SCALE_STEP,
                      x0: int = CARD_X0, y0: int = CARD_Y0):
    """用给定卡片写合成视频(FFV1),返回 (video_path, 初始帧四角 quad)。"""
    ch, cw = card.shape[:2]
    frames, quad0 = [], None
    for i in range(n):
        m = motion_mat_at(TX_STEP * i, 1.0 + scale_step * i, x0, y0, cw, ch)
        frame = np.full((FRAME_H, FRAME_W, 3), 200, np.uint8)
        cv2.warpPerspective(
            card, m, (FRAME_W, FRAME_H), dst=frame,
            flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_TRANSPARENT)
        corners = np.array([
            [0, 0], [cw - 1, 0], [cw - 1, ch - 1], [0, ch - 1]
        ], dtype=np.float32)
        gt = cv2.perspectiveTransform(
            corners.reshape(-1, 1, 2), m.astype(np.float32)).reshape(-1, 2)
        frames.append(frame)
        if i == 0:
            quad0 = [[float(x), float(y)] for x, y in gt]
    video_path = str(tmp_path / "case.avi")
    writer = cv2.VideoWriter(
        video_path, cv2.VideoWriter_fourcc(*"FFV1"), float(FPS),
        (FRAME_W, FRAME_H))
    if not writer.isOpened():
        pytest.skip("no usable video codec (FFV1) for motion ass cli tests")
    for f in frames:
        writer.write(f)
    writer.release()
    return video_path, quad0


# whitespace 卡片:文字条 + 纹理都在上半部,下半部留大片纯白空白带
WS_CARD_W, WS_CARD_H = 160, 200
WS_X0, WS_Y0 = 90, 20
WS_TEXT_BARS = [
    (20, 60, 120, 74),
    (30, 88, 110, 102),
]


def make_ws_card() -> np.ndarray:
    card = np.full((WS_CARD_H, WS_CARD_W, 3), 245, np.uint8)
    rng = np.random.default_rng(13)
    for x in range(8, WS_CARD_W - 24, 30):  # 纹理行(上半部,跟踪用)
        color = tuple(int(c) for c in rng.integers(100, 180, 3))
        cv2.rectangle(card, (x, 6), (min(x + 22, WS_CARD_W - 8), 18), color, -1)
        cv2.rectangle(card, (x, 30), (min(x + 22, WS_CARD_W - 8), 42), color, -1)
    for x1, y1, x2, y2 in WS_TEXT_BARS:
        cv2.rectangle(card, (x1, y1), (x2, y2), (10, 10, 10), -1)
    return card


def test_default_overlap_output_identical(tmp_path):
    """默认(无参数)与显式 --scene-text-policy overlap 输出逐字节一致。"""
    video_path, quad0 = build_case(tmp_path)
    quad_spec = " ".join(f"{x:.0f},{y:.0f}" for x, y in quad0)
    out_default = tmp_path / "default.ass"
    out_overlap = tmp_path / "overlap.ass"

    common = ["--video", video_path, "--quad", quad_spec]
    assert motion_cli.main(
        common + ["--out", str(out_default)], ocr_fn=make_mock_ocr([])) == 0
    assert motion_cli.main(
        common + ["--out", str(out_overlap), "--scene-text-policy", "overlap"],
        ocr_fn=make_mock_ocr([])) == 0

    assert out_default.read_bytes() == out_overlap.read_bytes()
    # 既有回归:Dialogue 行仍是 layer 0 的 Scene/motion;NoteBox 样式行存在
    text = out_default.read_text(encoding="utf-8-sig")
    assert "Style: NoteBox,思源黑体 CN,10," in text
    for line in _dialogue_lines(text):
        assert DIALOGUE_RE.match(line), line


def test_policy_config_keys_validated(tmp_path, capsys):
    """policy_ 前缀键进 build_config 校验体系:合法键通过、未知键报错。"""
    video_path, quad0 = build_case(tmp_path, n=8, scale_step=0.0)
    quad_spec = " ".join(f"{x:.0f},{y:.0f}" for x, y in quad0)
    out_ass = tmp_path / "ok.ass"
    cfg = tmp_path / "cfg.json"
    cfg.write_text(json.dumps({
        "policy_bg_max_std": 18.0,
        "policy_external_margin": 40,
        "policy_block_vgap_ratio": 0.35,
    }), encoding="utf-8")
    assert motion_cli.main(
        ["--video", video_path, "--out", str(out_ass), "--quad", quad_spec,
         "--config-json", str(cfg)],
        ocr_fn=make_mock_ocr([])) == 0
    assert out_ass.exists()

    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps({"policy_bogus": 1}), encoding="utf-8")
    out_bad = tmp_path / "bad.ass"
    rc = motion_cli.main(
        ["--video", video_path, "--out", str(out_bad), "--quad", quad_spec,
         "--config-json", str(bad)],
        ocr_fn=make_mock_ocr([]))
    assert rc != 0 and not out_bad.exists()
    assert "policy_bogus" in capsys.readouterr().err


def test_mask_policy_end_to_end(tmp_path, capsys):
    """mask:遮罩事件(layer 0,`\\p1`+`\\move`+`\\1c` 采样色)+ 原文本事件升 layer 1。"""
    video_path, quad0 = build_case_custom(tmp_path, make_clean_card())
    quad_spec = " ".join(f"{x:.0f},{y:.0f}" for x, y in quad0)
    out_ass = tmp_path / "mask.ass"

    rc = motion_cli.main(
        ["--video", video_path, "--out", str(out_ass), "--quad", quad_spec,
         "--scene-text-policy", "mask"],
        ocr_fn=make_mock_ocr([]))

    assert rc == 0
    err = capsys.readouterr().err
    assert "scene-text-policy" in err  # applied 日志(mask == 所选,无回退)
    assert "warning: scene-text-policy" not in err
    text = out_ass.read_text(encoding="utf-8-sig")
    dialogue = _dialogue_lines(text)
    masks = [l for l in dialogue if "\\p1" in l]
    texts = [l for l in dialogue if "\\p1" not in l]
    assert len(masks) == 2 and len(texts) == 2  # 两块遮罩 + 两行文本
    for line in masks:
        assert line.startswith("Dialogue: 0,"), line
        assert "\\an7\\p1" in line and "\\move(" in line
        assert "\\1c&HF5F5F5&" in line          # 采样色 = 白底 245 = 0xF5
        assert "m 0 0 l " in line and line.rstrip().endswith("{\\p0}")
    for line in texts:
        assert line.startswith("Dialogue: 1,"), line
        field = line.split(",", 9)[9]
        assert "\\move(" in field
        assert re.sub(r"^\{[^}]*\}", "", field) in {"LINE0", "LINE1"}


def test_external_policy_end_to_end(tmp_path):
    """external:全部块合并单条 NoteBox 事件,底带居中、行序自上而下。"""
    video_path, quad0 = build_case(tmp_path, n=10, scale_step=0.0)
    quad_spec = " ".join(f"{x:.0f},{y:.0f}" for x, y in quad0)
    out_ass = tmp_path / "external.ass"

    rc = motion_cli.main(
        ["--video", video_path, "--out", str(out_ass), "--quad", quad_spec,
         "--scene-text-policy", "external"],
        ocr_fn=make_mock_ocr([]))

    assert rc == 0
    text = out_ass.read_text(encoding="utf-8-sig")
    assert "Style: NoteBox,思源黑体 CN,10," in text
    dialogue = _dialogue_lines(text)
    assert len(dialogue) == 1
    line = dialogue[0]
    assert line.startswith("Dialogue: 0,") and "NoteBox,motion," in line
    field = line.split(",", 9)[9]
    assert field.startswith("{\\an2\\pos(160.0,200.0)\\fs")
    assert re.sub(r"^\{[^}]*\}", "", field) == "LINE0\\NLINE1"


def test_whitespace_policy_places_text_in_band(tmp_path, capsys):
    """whitespace:折行文本落入下半空白带、随轨迹平移、字号按带适配。"""
    video_path, quad0 = build_case_custom(
        tmp_path, make_ws_card(), n=16, scale_step=0.0,
        x0=WS_X0, y0=WS_Y0)
    quad_spec = " ".join(f"{x:.0f},{y:.0f}" for x, y in quad0)
    out_ass = tmp_path / "ws.ass"

    rc = motion_cli.main(
        ["--video", video_path, "--out", str(out_ass), "--quad", quad_spec,
         "--scene-text-policy", "whitespace"],
        ocr_fn=make_mock_ocr([]))

    assert rc == 0
    assert "warning: scene-text-policy" not in capsys.readouterr().err
    dialogue = _dialogue_lines(out_ass.read_text(encoding="utf-8-sig"))
    # LINE0/LINE1 按带宽(159//40=3 字)折行 → 4 行
    assert len(dialogue) == 4
    bars_bottom = WS_Y0 + max(y2 for _x1, y1, _x2, y2 in WS_TEXT_BARS)
    card_bottom = WS_Y0 + WS_CARD_H - 1
    for line in dialogue:
        field = line.split(",", 9)[9]
        assert "\\fs24" in field  # fit_font_size(4 行, 带高≈92) 夹到下限 24
        m = re.search(r"\\move\((-?[\d.]+),(-?[\d.]+),(-?[\d.]+),(-?[\d.]+)", field)
        assert m, field
        x1, y1, x2, y2 = (float(g) for g in m.groups())
        assert abs(y1 - y2) <= 2.0             # 带内水平排布(忽略跟踪噪声)
        assert bars_bottom < y1 < card_bottom  # 落在原文字下方的空白带
        assert x2 - x1 == pytest.approx(TX_STEP * 13, abs=4.0)  # 随轨迹平移
    bodies = {re.sub(r"^\{[^}]*\}", "", l.split(",", 9)[9]) for l in dialogue}
    assert bodies == {"LIN", "E0", "E1"}


def test_whitespace_without_band_falls_back_to_mask(tmp_path, capsys):
    """回退链:标准卡片无空白带 → whitespace 降级 mask(白底干净,可用)。"""
    video_path, quad0 = build_case_custom(tmp_path, make_clean_card())
    quad_spec = " ".join(f"{x:.0f},{y:.0f}" for x, y in quad0)
    out_ass = tmp_path / "wsfallback.ass"

    rc = motion_cli.main(
        ["--video", video_path, "--out", str(out_ass), "--quad", quad_spec,
         "--scene-text-policy", "whitespace"],
        ocr_fn=make_mock_ocr([]))

    assert rc == 0
    err = capsys.readouterr().err
    assert "whitespace->mask" in err  # 回退原因写入 warn 日志
    assert "mask->external" not in err
    dialogue = _dialogue_lines(out_ass.read_text(encoding="utf-8-sig"))
    assert sum(1 for l in dialogue if "\\p1" in l) == 2  # 降级后遮罩生效
    assert sum(1 for l in dialogue if l.startswith("Dialogue: 1,")) == 2


def test_full_fallback_chain_whitespace_mask_external(tmp_path, capsys):
    """完整回退链:标准卡片(纹理贴着文字)无空白带 + 背景杂色 → external。"""
    video_path, quad0 = build_case(tmp_path, n=10, scale_step=0.0)
    quad_spec = " ".join(f"{x:.0f},{y:.0f}" for x, y in quad0)
    out_ass = tmp_path / "chain.ass"

    rc = motion_cli.main(
        ["--video", video_path, "--out", str(out_ass), "--quad", quad_spec,
         "--scene-text-policy", "whitespace"],
        ocr_fn=make_mock_ocr([]))

    assert rc == 0
    err = capsys.readouterr().err
    assert "whitespace->mask" in err
    assert "mask->external" in err
    dialogue = _dialogue_lines(out_ass.read_text(encoding="utf-8-sig"))
    assert len(dialogue) == 1
    assert "NoteBox,motion," in dialogue[0]
    assert re.sub(r"^\{[^}]*\}", "",
                  dialogue[0].split(",", 9)[9]) == "LINE0\\NLINE1"


# ---------------------------------------------------------------------------
# _default_ocr_fn:指定引擎不可用时优雅回退(安装版未带 rapidocr 等场景)
# ---------------------------------------------------------------------------

class _UnavailableEngineCls:
    @staticmethod
    def is_available():
        return False


class _AvailableEngineCls:
    @staticmethod
    def is_available():
        return True


class _FakeEngine:
    def normalize_result(self, raw):
        return raw

    def predict(self, img):
        return img

    def cleanup(self):
        pass


def test_default_ocr_fn_falls_back_when_engine_unavailable(monkeypatch, capsys):
    """--ocr-engine 指定的引擎依赖缺失 → 告警并回退注册表默认引擎,
    而不是 ImportError 让整条任务链退出。"""
    import core.ocr_engine_base as engine_base
    import core.ocr_engine_manager as engine_manager

    monkeypatch.setattr(
        engine_base.OCREngineRegistry, "get",
        classmethod(lambda cls, eid: _UnavailableEngineCls if eid == "rapid" else None))
    monkeypatch.setattr(
        engine_base.OCREngineRegistry, "get_default",
        classmethod(lambda cls: "paddle"))

    captured = {}

    def _fake_build(engine_id, options=None):
        captured["engine_id"] = engine_id
        return _FakeEngine()

    monkeypatch.setattr(engine_manager, "build_standalone_engine", _fake_build)

    ocr_fn, engine = motion_cli._default_ocr_fn("rapid")
    assert captured["engine_id"] == "paddle"
    assert engine.predict("x") == "x"
    assert "falling back to 'paddle'" in capsys.readouterr().err


def test_default_ocr_fn_keeps_available_engine(monkeypatch, capsys):
    """指定引擎可用 → 原样使用,不产生回退告警。"""
    import core.ocr_engine_base as engine_base
    import core.ocr_engine_manager as engine_manager

    monkeypatch.setattr(
        engine_base.OCREngineRegistry, "get",
        classmethod(lambda cls, eid: _AvailableEngineCls if eid == "rapid" else None))
    monkeypatch.setattr(
        engine_base.OCREngineRegistry, "get_default",
        classmethod(lambda cls: "paddle"))

    captured = {}

    def _fake_build(engine_id, options=None):
        captured["engine_id"] = engine_id
        return _FakeEngine()

    monkeypatch.setattr(engine_manager, "build_standalone_engine", _fake_build)

    motion_cli._default_ocr_fn("rapid")
    assert captured["engine_id"] == "rapid"
    assert "falling back" not in capsys.readouterr().err


# ---------------------------------------------------------------------------
# 融合行噪声过滤(junk_line_filter;手机状态栏/导航栏图标误读)
# ---------------------------------------------------------------------------

def _fixed_ocr(texts_boxes):
    """返回恒定输出的 ocr_fn:每次关键帧都读出同一组(文本, 行框)。"""

    def ocr_fn(img):
        ocr = {"dt_polys": [], "rec_polys": [], "rec_texts": [],
               "rec_scores": [], "rec_boxes": []}
        for text, (x1, y1, x2, y2) in texts_boxes:
            poly = [[float(x1), float(y1)], [float(x2), float(y1)],
                    [float(x2), float(y2)], [float(x1), float(y2)]]
            ocr["dt_polys"].append([list(p) for p in poly])
            ocr["rec_polys"].append(poly)
            ocr["rec_texts"].append(text)
            ocr["rec_scores"].append(0.95)
            ocr["rec_boxes"].append([int(x1), int(y1), int(x2), int(y2)])
        return ocr

    return ocr_fn


# 两行真实文字 + 三行噪声(导航键 <、时钟 000、单字象形误读 血)
_JUNK_TEXT_BOXES = [
    ("メール一覧", (18, 22, 126, 36)),
    ("次回ミーティング", (40, 62, 96, 76)),
    ("<", (8, 88, 20, 98)),
    ("000", (60, 88, 84, 98)),
    ("血", (110, 88, 124, 98)),
]


def test_junk_lines_filtered_from_motion_events(tmp_path):
    video_path, quad0 = build_case(tmp_path, n=8, scale_step=0.0)
    quad_spec = " ".join(f"{x:.0f},{y:.0f}" for x, y in quad0)
    out_ass = tmp_path / "junk.ass"

    rc = motion_cli.main(
        ["--video", video_path, "--out", str(out_ass), "--quad", quad_spec],
        ocr_fn=_fixed_ocr(_JUNK_TEXT_BOXES))

    assert rc == 0
    text = out_ass.read_bytes().decode("utf-8-sig")
    bodies = re.findall(r"^Dialogue: .*,,\{[^}]*\}(.*)$", text, re.M)
    assert set(bodies) == {"メール一覧", "次回ミーティング"}, (
        "icon/clock misreads (<, 000, single glyph) must not become events")


def test_junk_filter_disabled_via_config(tmp_path):
    video_path, quad0 = build_case(tmp_path, n=8, scale_step=0.0)
    quad_spec = " ".join(f"{x:.0f},{y:.0f}" for x, y in quad0)
    out_ass = tmp_path / "nojunk.ass"
    cfg = tmp_path / "cfg.json"
    cfg.write_text(json.dumps({"junk_line_filter": False}), encoding="utf-8")

    rc = motion_cli.main(
        ["--video", video_path, "--out", str(out_ass), "--quad", quad_spec,
         "--config-json", str(cfg)],
        ocr_fn=_fixed_ocr(_JUNK_TEXT_BOXES))

    assert rc == 0
    text = out_ass.read_bytes().decode("utf-8-sig")
    bodies = set(re.findall(r"^Dialogue: .*,,\{[^}]*\}(.*)$", text, re.M))
    assert bodies == {"メール一覧", "次回ミーティング", "<", "000", "血"}


# ---------------------------------------------------------------------------
# 关键帧候选池分批 OCR(首批无文字换批 / 批级解码失败兜底 / 池耗尽)
# ---------------------------------------------------------------------------

_EMPTY_OCR = {"dt_polys": [], "rec_polys": [], "rec_texts": [],
              "rec_scores": [], "rec_boxes": []}


def test_keyframe_pool_first_batch_without_text_advances_to_next(tmp_path, monkeypatch):
    """最清晰的首批识别不出文字(空白引导段)→ 留痕后换下一批;summary 的
    keyframes 即选中批,锚定帧 = 选中批最清晰帧;候选池只顺序解码一次。"""
    video_path, quad0 = build_case(tmp_path)
    logs: list = []
    mock_calls: list = []
    state = {"n": 0}
    mock = make_mock_ocr(mock_calls)

    def ocr_fn(img):
        state["n"] += 1
        if state["n"] <= 2:  # 首批(keyframe_count=2)两帧都返回空
            return dict(_EMPTY_OCR)
        return mock(img)

    real_read = motion_cli._read_keyframe_frames
    reads: list = []

    def spy_read(vp, frame_nums):
        reads.append([int(f) for f in frame_nums])
        return real_read(vp, frame_nums)

    monkeypatch.setattr(motion_cli, "_read_keyframe_frames", spy_read)

    events, summary = motion_cli.build_motion_events(
        video_path, quad0, keyframe_count=2, min_gap_sec=0.0,
        ocr_fn=ocr_fn, log=logs.append)

    assert events
    assert len(reads) == 1  # 池级一次性解码:分批与选定批后都不再重复解码
    pool = reads[0]
    batch2 = pool[2:4]
    # 换批:选中批 = 候选池第二批;锚定帧 = 选中批最清晰帧(批首)
    assert summary["keyframes"] == batch2
    assert summary["keyframes"][0] == batch2[0]
    assert any("sharpest keyframes had no text" in m for m in logs)
    assert f"{batch2}" in "\n".join(logs)
    assert state["n"] == 4        # 两批 × 每批 2 帧
    assert len(mock_calls) == 2   # 只有第二批走真实黑条检测


def test_keyframe_batch_decode_failure_tries_next_batch(tmp_path, monkeypatch):
    """某批解码失败(容器帧数虚标/尾部帧坏)→ 留痕换下一批,整体仍成功,
    不再让整条 build 失败。"""
    video_path, quad0 = build_case(tmp_path, n=10, scale_step=0.0)
    logs: list = []
    real_read = motion_cli._read_keyframe_frames
    pool_reads: list = []
    batch_attempts = {"n": 0}

    def flaky_read(vp, frame_nums):
        frames = [int(f) for f in frame_nums]
        if len(frames) > 2:  # 池级一次性解码调用:模拟整体解码失败
            pool_reads.append(frames)
            raise RuntimeError("simulated overstated frame count")
        batch_attempts["n"] += 1
        if batch_attempts["n"] == 1:  # 首个按批补解(=首批)失败
            raise RuntimeError("simulated undecodable tail frame")
        return real_read(vp, frame_nums)

    monkeypatch.setattr(motion_cli, "_read_keyframe_frames", flaky_read)

    events, summary = motion_cli.build_motion_events(
        video_path, quad0, keyframe_count=2, min_gap_sec=0.0,
        ocr_fn=make_mock_ocr([]), log=logs.append)

    assert events
    assert batch_attempts["n"] == 2  # 首批解码失败后换批成功
    assert summary["keyframes"] == pool_reads[0][2:4]
    joined = "\n".join(logs)
    assert "keyframe pool decode failed" in joined
    assert "decode failed" in joined and "trying next batch" in joined


def test_keyframe_ocr_fn_contract_violation_propagates(tmp_path):
    """ocr_fn 返回非 dict 是编程错误:不得被批级兜底吞掉,保持上抛。"""
    video_path, quad0 = build_case(tmp_path, n=6, scale_step=0.0)

    def bad_ocr(_img):
        return ["not", "a", "dict"]

    with pytest.raises(RuntimeError, match="unified OCR dict"):
        motion_cli.build_motion_events(
            video_path, quad0, keyframe_count=2, min_gap_sec=0.0,
            ocr_fn=bad_ocr, log=lambda _m: None)


def test_keyframe_pool_exhausted_without_text_raises(tmp_path):
    """候选池耗尽且没有任何批取到文字 → 抛现有的 RuntimeError。"""
    video_path, quad0 = build_case(tmp_path, n=6, scale_step=0.0)

    def empty_ocr(_img):
        return dict(_EMPTY_OCR)

    with pytest.raises(RuntimeError, match="no text lines recognized"):
        motion_cli.build_motion_events(
            video_path, quad0, keyframe_count=2, min_gap_sec=0.0,
            ocr_fn=empty_ocr, log=lambda _m: None)


# ---------------------------------------------------------------------------
# 候选池时间覆盖：前缀批全无文字时继续走到池尾的覆盖代表帧
# ---------------------------------------------------------------------------

def _spy_keyframe_selection(monkeypatch) -> dict:
    """打桩 ``core.keyframe_selector.select_keyframes``（build 内函数级 import，
    打桩模块属性即生效），记录调用 kwargs / 池 / diagnostics 实据。"""
    captured: dict = {}
    real_select = keyframe_selector.select_keyframes

    def spy_select(video, tracks, **kwargs):
        pool = real_select(video, tracks, **kwargs)
        captured["kwargs"] = kwargs
        captured["pool"] = list(pool)
        captured["diag"] = dict(kwargs.get("diagnostics") or {})
        return pool

    monkeypatch.setattr(keyframe_selector, "select_keyframes", spy_select)
    return captured


def test_keyframe_coverage_frames_used_when_prefix_has_no_text(tmp_path, monkeypatch):
    """前缀批全无文字（空白引导段）→ 继续走到池尾的覆盖代表帧并成功。

    纯清晰度候选池没有任何时间覆盖保证：池整段落在空白引导段（清晰度占优）
    时，后段明明有文字却没有一帧候选，分批兜底全部落空（12.mp4 实测报
    ``no text lines recognized on any of N candidate keyframes``）。开启覆盖后
    池尾追加了按 ok 帧时间跨度分桶的代表帧，分批循环得以继续到覆盖帧。
    """
    video_path, quad0 = build_case(tmp_path)
    logs: list = []
    mock = make_mock_ocr([])
    captured = _spy_keyframe_selection(monkeypatch)
    calls = {"n": 0}

    def ocr_fn(img):
        calls["n"] += 1
        if calls["n"] <= captured["diag"]["prefix_len"]:  # 前缀帧全部读不出文字
            return dict(_EMPTY_OCR)
        return mock(img)

    events, summary = motion_cli.build_motion_events(
        video_path, quad0, keyframe_count=2, min_gap_sec=0.0,
        ocr_fn=ocr_fn, log=logs.append)

    assert events, "覆盖帧批应取到文字（旧版在此抛池耗尽 RuntimeError）"
    assert captured["kwargs"]["ensure_coverage"] is True   # 调用处显式开启覆盖
    pool, prefix_len = captured["pool"], captured["diag"]["prefix_len"]
    assert len(pool) > prefix_len, "合成视频上覆盖必须追加了代表帧"
    assert captured["diag"]["coverage"]["added"] == pool[prefix_len:]

    step = 2                                    # 批步 = keyframe_count
    first_covered = (prefix_len // step) * step  # 首个含覆盖帧的批下标
    assert summary["keyframes"] == pool[first_covered:first_covered + step]
    assert calls["n"] == first_covered + step   # 前缀批耗尽后即命中覆盖帧批
    joined = "\n".join(logs)
    assert "trying next batch" in joined
    assert "sharpest keyframes had no text; using batch" in joined
    bodies = {ev["body"] for ev in events}
    assert bodies == {"LINE0", "LINE1"}


def test_keyframe_coverage_keeps_first_batch_acceptance(tmp_path, monkeypatch):
    """前缀批有文字时，覆盖帧一次都不 OCR（接受批与前缀不变）。"""
    video_path, quad0 = build_case(tmp_path)
    captured = _spy_keyframe_selection(monkeypatch)
    calls = {"n": 0}

    def ocr_fn(img):
        calls["n"] += 1
        return make_mock_ocr([])(img)

    events, summary = motion_cli.build_motion_events(
        video_path, quad0, keyframe_count=2, min_gap_sec=0.0,
        ocr_fn=ocr_fn, log=lambda _m: None)

    assert events
    pool = captured["pool"]
    assert len(pool) > captured["diag"]["prefix_len"]   # 池里确实有覆盖帧
    assert calls["n"] == 2                              # 首批命中即停
    assert summary["keyframes"] == pool[:2] == captured["diag"]["prefix"][:2]


def test_keyframe_pool_log_reports_sharpness_and_coverage(tmp_path):
    """``[2/5] keyframe pool`` 留痕：逐帧清晰度分数 + 判定 + 时间覆盖情况。"""
    video_path, quad0 = build_case(tmp_path)
    logs: list = []

    motion_cli.build_motion_events(
        video_path, quad0, keyframe_count=2, min_gap_sec=0.0,
        ocr_fn=make_mock_ocr([]), log=logs.append)

    joined = "\n".join(logs)
    pool_line = next(m for m in logs if m.startswith("[2/5] keyframe pool"))
    assert pool_line.endswith("]")
    assert "keyframe sharpness [pool]:" in joined
    assert "keyframe sharpness [candidates]:" in joined
    assert "score-ranked" in joined          # 合成视频分数有区分度（未退化）
    assert "keyframe coverage:" in joined
    assert "buckets=12" in joined            # 桶数 = k = max(keyframe_count, 12)
    assert "appended +[" in joined and "of ok span)" in joined


def test_score_ranking_verdict_flags_time_order_degeneracy():
    """并列判定：分数并列（如清晰度度量在真实视频上无区分度）→ 池实为
    min_gap 时间网格，日志必须点明「不是按清晰度排的」。"""
    assert "score-ranked" in motion_cli._score_ranking_verdict({0: 10.0, 1: 20.0})
    assert "score-ranked" in motion_cli._score_ranking_verdict({0: 10.0, 1: 10.5})
    tied = motion_cli._score_ranking_verdict({0: 100.0, 1: 100.0, 2: 100.0})
    assert "tied" in tied and "min_gap time grid" in tied
    near = motion_cli._score_ranking_verdict({0: 100.0, 1: 100.5})  # 差 0.5% < 1%
    assert "tied" in near
    assert "no scored candidate frame" in motion_cli._score_ranking_verdict({})
    # 无实据（未提供 diagnostics）时不产出任何行（旧调用方零影响）
    assert motion_cli._describe_keyframe_pool([1, 2, 3], None) == []
    assert motion_cli._describe_keyframe_pool([1, 2, 3], {}) == []


# ---------------------------------------------------------------------------
# 批接受判据 = 「过滤噪声后仍有非空行」(与 junk filter 等价)
# ---------------------------------------------------------------------------

# 纯噪声批:空白引导段/状态栏误读(<、000、空串)
_NOISE_TEXT_BOXES = [
    ("<", (8, 22, 20, 32)),
    ("000", (60, 22, 84, 32)),
    ("", (40, 62, 96, 76)),
]


def test_batch_has_text_equivalent_to_junk_filter():
    """_batch_has_text 与 junk filter 等价 + 类型防御(脏引擎输出不抛错)。

    判据 = rec_texts 中至少一条 not is_noise_text(text);junk filter 会把
    噪声行全剔空,故「批内无任何非噪声条目」⇔「过滤后行集为空」。"""
    assert motion_cli._batch_has_text([(0, {"rec_texts": ["メール"]})])
    assert motion_cli._batch_has_text([(0, {"rec_texts": ["<", "本文"]})])
    assert motion_cli._batch_has_text([(0, {"rec_texts": "本文"})])  # str = 单条
    for texts in ([], [""], ["000"], ["<"], ["血"], None, 42):
        assert not motion_cli._batch_has_text([(0, {"rec_texts": texts})])
    assert not motion_cli._batch_has_text([(0, {})])  # 键缺失
    assert not motion_cli._batch_has_text([(0, {"rec_texts": [None, 3]})])
    assert not motion_cli._batch_has_text([])
    # 判据与 is_noise_text 逐条一致
    assert all(is_noise_text(t) for t in _noise_texts())
    assert any(_raw_rec_texts())


def _noise_texts() -> list:
    return [t for t, _box in _NOISE_TEXT_BOXES]


def _raw_rec_texts() -> list:
    return _fixed_ocr(_NOISE_TEXT_BOXES)(None)["rec_texts"]


def test_keyframe_pool_noise_only_batch_advances_to_next(tmp_path, monkeypatch):
    """首批只读出噪声行 → 判为「无可用文字」留痕换下一批。

    旧判据只看 rec_texts 列表是否非空:纯噪声批会被选中并 break,随后
    junk filter 把所有行剔空,在融合行断言处抛 RuntimeError 且**不再**
    尝试下一批(12.mp4 的空白引导段/状态栏误读正是这个场景)。"""
    video_path, quad0 = build_case(tmp_path)
    logs: list = []
    state = {"n": 0}
    mock = make_mock_ocr([])
    noise_ocr = _fixed_ocr(_NOISE_TEXT_BOXES)

    def ocr_fn(img):
        state["n"] += 1
        if state["n"] <= 2:  # 首批(keyframe_count=2)两帧只读出噪声
            return noise_ocr(img)
        return mock(img)

    real_read = motion_cli._read_keyframe_frames
    reads: list = []

    def spy_read(vp, frame_nums):
        reads.append([int(f) for f in frame_nums])
        return real_read(vp, frame_nums)

    monkeypatch.setattr(motion_cli, "_read_keyframe_frames", spy_read)

    events, summary = motion_cli.build_motion_events(
        video_path, quad0, keyframe_count=2, min_gap_sec=0.0,
        ocr_fn=ocr_fn, log=logs.append)

    assert events, "换批后应取到真实文字(旧行为在此抛错退出)"
    assert state["n"] == 4                  # 首批 2 帧被跳过 + 第二批 2 帧被接受
    assert summary["keyframes"] == reads[0][2:4]
    joined = "\n".join(logs)
    assert "noise-only" in joined and "trying next batch" in joined
    bodies = {ev["body"] for ev in events}
    assert bodies == {"LINE0", "LINE1"}


def test_first_batch_with_real_text_accepted_unchanged(tmp_path, monkeypatch):
    """首批既有真实文字又有噪声行 → 首批即接受:判据只要求存在非噪声
    条目,命中批行为不变(不再 OCR 后续批,噪声行仍由 junk filter 剔除)。"""
    video_path, quad0 = build_case(tmp_path)
    logs: list = []
    state = {"n": 0}
    base = _fixed_ocr(_JUNK_TEXT_BOXES)

    def ocr_fn(img):
        state["n"] += 1
        return base(img)

    real_read = motion_cli._read_keyframe_frames
    reads: list = []

    def spy_read(vp, frame_nums):
        reads.append([int(f) for f in frame_nums])
        return real_read(vp, frame_nums)

    monkeypatch.setattr(motion_cli, "_read_keyframe_frames", spy_read)

    events, summary = motion_cli.build_motion_events(
        video_path, quad0, keyframe_count=2, min_gap_sec=0.0,
        ocr_fn=ocr_fn, log=logs.append)

    assert state["n"] == 2                  # 首批命中即 break
    assert summary["keyframes"] == reads[0][:2]
    joined = "\n".join(logs)
    assert "trying next batch" not in joined and "noise-only" not in joined
    bodies = {ev["body"] for ev in events}
    assert bodies == {"メール一覧", "次回ミーティング"}  # 噪声行仍被剔除


# ---------------------------------------------------------------------------
# verify_* 配置透传(--config-json 可调)
# ---------------------------------------------------------------------------

def test_verify_min_static_samples_plumbed_to_verify_config(tmp_path, monkeypatch):
    """verify_min_static_samples 与其它 verify_* 一样可经 --config-json 调整
    并透传 VerifyConfig(此前没有对应字段,静止判定最少采样数无法调);
    调用处同时传入视频高度,供像素阈值按 video_height/1080 归一。"""
    video_path, quad0 = build_case(tmp_path, n=8, scale_step=0.0)
    from core import pose_verify

    captured: dict = {}
    real = pose_verify.verify_line_tracks

    def spy(v_path, tracks, line_tracks, cfg, log=None, video_height=None,
            roi_quad=None, roi_margin_px=None):
        captured["cfg"] = cfg
        captured["video_height"] = video_height
        return real(v_path, tracks, line_tracks, cfg, log=log,
                    video_height=video_height)

    monkeypatch.setattr(pose_verify, "verify_line_tracks", spy)

    motion_cli.build_motion_events(
        video_path, quad0, keyframe_count=2, min_gap_sec=0.0,
        config_data={"verify_min_static_samples": 9,
                     "verify_static_tol_px": 3.5},
        ocr_fn=make_mock_ocr([]), log=lambda _m: None)

    vcfg = captured["cfg"]
    assert vcfg.min_static_samples == 9      # 透传(config-json 可调)
    assert vcfg.static_tol_px == 3.5         # 既有 verify_* 不受影响
    assert captured["video_height"] == FRAME_H  # 像素阈值按高度归一


# ---------------------------------------------------------------------------
# dedupe 时间阈值 = 关键帧池网格步(与实测批间距无关;有意的保守合并方向)
# ---------------------------------------------------------------------------

def test_dedupe_merge_gap_is_pool_grid_not_batch_spacing():
    """阈值语义固化:``_keyframe_pool_frame_gap`` = 池内相邻帧号的最大间隔
    (时间网格步),与「选中批内实测到的帧间距」无关——池比批稀疏时阈值
    不会因批间距更窄而收窄。这是有意的保守合并方向:阈值偏松只多合并
    同实例的近重复槽(\an4/\an6 跳变修复),偏紧才会把同一实例拆成两行。"""
    pool = [10, 20, 30, 40, 50, 60, 70, 80, 90, 100]   # 网格步 = 10
    assert motion_cli._keyframe_pool_frame_gap(pool) == 10
    assert motion_cli._keyframe_pool_frame_gap([7]) == 0        # 单帧批
    assert motion_cli._keyframe_pool_frame_gap([5, 5, 9, 12]) == 4  # 去重后取最大间隔

    gap = motion_cli._keyframe_pool_frame_gap(pool)
    batch_spacing = 1                       # 选中批内相邻帧距(实测)远小于池网格步
    assert gap > batch_spacing

    rows = [("同一实例", (0.0, 0.0, 50.0, 10.0)),
            ("同一实例", (0.0, 0.0, 50.0, 10.0))]
    # 交叉帧距 2:超过批内相邻帧距(1)但不超过池网格步(10)→ 仍合并
    kept, removed = motion_cli.dedupe_rows(
        rows, frames_seen=[{10}, {12}], merge_frame_gap=gap)
    assert removed == 1 and len(kept) == 1
    # 超过一个网格步(11 > 10)→ 不同时刻的合法重复:双份保留并留痕
    logs: list = []
    kept2, removed2 = motion_cli.dedupe_rows(
        rows, frames_seen=[{10}, {21}], merge_frame_gap=gap, log=logs.append)
    assert removed2 == 0 and len(kept2) == 2
    assert any("time-separated duplicate" in m for m in logs)


# ---------------------------------------------------------------------------
# 跟踪覆盖率告警（12.mp4 轨迹 ROI 的失败模式）
# ---------------------------------------------------------------------------

def test_low_track_coverage_warns_with_consequence(tmp_path, monkeypatch):
    """ok 帧占比过低时告警并说明后果。

    平面跟踪没有丢锁重捕机制：画面内容大变（聊天文字出现）后可能永久丢锁，
    轨迹管线只在 ok 窗口内有位姿，窗口内无文字时整个 ROI 回退静态策略。
    实测 12.mp4 轨迹 ROI 即此模式（79/1871 ok，全部落在空白引导段），
    告警须让原因自解释，而不是让人去猜关键帧选择。
    """
    from core.scene_plane_tracker import TrackedQuad
    from scripts.motion_ass import build_motion_events

    video_path, quad0 = build_case(tmp_path, n=6, scale_step=0.0)

    def fake_track(_video, quad, start_frame=0, end_frame=None, **kw):
        eye = [[1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0]]
        out = []
        for i in range(48):
            tracked = i < 3                      # 仅前 3 帧 ok（6.25%）
            out.append(TrackedQuad(
                frame_num=i, time_sec=i / float(FPS),
                status="ok" if tracked else "lost",
                quad=[[float(x), float(y)] for x, y in quad] if tracked else None,
                homography=eye if tracked else None,
                homography_inv=eye if tracked else None,
                inlier_ratio=0.9 if tracked else 0.0,
                reproj_error=0.1 if tracked else -1.0,
                matches=50 if tracked else 0,
            ))
        return out

    monkeypatch.setattr("core.scene_plane_tracker.track_plane", fake_track)

    poly = [[10.0, 10.0], [60.0, 10.0], [60.0, 30.0], [10.0, 30.0]]

    def one_line_ocr(_img):
        return {"dt_polys": [poly], "rec_polys": [poly],
                "rec_texts": ["字幕"], "rec_scores": [0.95],
                "rec_boxes": [[10.0, 10.0, 60.0, 30.0]]}

    messages = []
    build_motion_events(
        video_path, [[float(x), float(y)] for x, y in quad0],
        start_frame=0, end_frame=5, ocr_fn=one_line_ocr,
        log=messages.append)

    joined = "\n".join(messages)
    assert "plane tracked on only" in joined, joined
    assert "6.2%" in joined, joined
    assert "falls back to the static policy" in joined, joined


def test_high_track_coverage_stays_quiet(tmp_path, monkeypatch):
    """ok 占比正常时不打覆盖率告警（避免误导）。"""
    from core.scene_plane_tracker import TrackedQuad
    from scripts.motion_ass import build_motion_events

    video_path, quad0 = build_case(tmp_path, n=6, scale_step=0.0)

    def fake_track(_video, quad, start_frame=0, end_frame=None, **kw):
        eye = [[1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0]]
        out = []
        for i in range(48):
            out.append(TrackedQuad(
                frame_num=i, time_sec=i / float(FPS), status="ok",
                quad=[[float(x), float(y)] for x, y in quad],
                homography=eye, homography_inv=eye,
                inlier_ratio=0.9, reproj_error=0.1, matches=50))
        return out

    monkeypatch.setattr("core.scene_plane_tracker.track_plane", fake_track)

    poly = [[10.0, 10.0], [60.0, 10.0], [60.0, 30.0], [10.0, 30.0]]

    def one_line_ocr(_img):
        return {"dt_polys": [poly], "rec_polys": [poly],
                "rec_texts": ["字幕"], "rec_scores": [0.95],
                "rec_boxes": [[10.0, 10.0, 60.0, 30.0]]}

    messages = []
    build_motion_events(
        video_path, [[float(x), float(y)] for x, y in quad0],
        start_frame=0, end_frame=5, ocr_fn=one_line_ocr,
        log=messages.append)

    assert "plane tracked on only" not in "\n".join(messages)


# ---------------------------------------------------------------------------
# 轨迹诊断报告(summary["lost_reasons"] / summary["chains"]):纯报告字段
# ---------------------------------------------------------------------------

# fake 轨迹:帧号 → 状态。ok 段 [0,1,2] / [4,5] / [9,10] 被 lost 缺口切开
# (3 与 6-8、11),用于断言链数与丢锁原因直方图;12 帧 < _TRACK_SPAN_MIN_FRAMES
# (24),故不触发低覆盖率告警,诊断与告警互不干扰。
_DIAG_FRAMES = 12
_DIAG_OK_FRAMES = {0, 1, 2, 4, 5, 9, 10}
_DIAG_LOST_REASONS = {3: "few_matches", 6: "few_matches",
                      7: "high_reproj", 8: "high_reproj", 11: "high_reproj"}

_EYE_H = [[1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0]]


def _tracked_state(frame_num: int, ok: bool, quad) -> dict:
    """ok / lost 帧的公共字段值(与既有覆盖率测试里的 fake 轨迹一致)。"""
    return dict(
        frame_num=int(frame_num), time_sec=int(frame_num) / float(FPS),
        status="ok" if ok else "lost",
        quad=[[float(x), float(y)] for x, y in quad] if ok else None,
        homography=_EYE_H if ok else None,
        homography_inv=_EYE_H if ok else None,
        inlier_ratio=0.9 if ok else 0.0,
        reproj_error=0.1 if ok else -1.0,
        matches=50 if ok else 0,
    )


class _LegacyTrack:
    """``lost_reason`` 字段落地前的 ``TrackedQuad``。

    字段名与旧版 dataclass 逐一对应,``__slots__`` 保证对象上**没有**
    ``lost_reason`` 属性——用于模拟老 tracker 产出的轨迹对象。
    """

    __slots__ = ("frame_num", "time_sec", "status", "quad", "homography",
                 "homography_inv", "inlier_ratio", "reproj_error", "matches")

    def __init__(self, **kwargs):
        for key, value in kwargs.items():
            setattr(self, key, value)


def _make_tracked(frame_num: int, ok: bool, quad, reason=None):
    """构造 ``TrackedQuad``;``reason`` 非 None 时带上 ``lost_reason``。

    ``lost_reason`` 是 tracker 侧并行新增的字段:已落地时走构造参数,未落地
    时按实例属性写入(旧 dataclass 无 ``__slots__``)——两种形态下本函数都
    可用,测试不依赖该字段是否已合入。
    """
    import dataclasses

    from core.scene_plane_tracker import TrackedQuad

    kwargs = _tracked_state(frame_num, ok, quad)
    if reason is not None and "lost_reason" in {
            f.name for f in dataclasses.fields(TrackedQuad)}:
        return TrackedQuad(lost_reason=reason, **kwargs)
    track = TrackedQuad(**kwargs)
    if reason is not None:
        setattr(track, "lost_reason", reason)
    return track


def _fake_track_plane(n_frames: int, ok_frames: set, reasons: dict,
                      *, legacy: bool = False):
    """fake ``track_plane``:``ok_frames`` 内的帧 status=ok(quad/单应取自
    init quad),其余 lost、原因取自 ``reasons``(帧号 → reason)。

    ``legacy=True`` 时产出 :class:`_LegacyTrack`(没有 ``lost_reason`` 属性)。
    """
    def fake_track(_video, quad, start_frame=0, end_frame=None, **kw):
        out = []
        for i in range(n_frames):
            ok = i in ok_frames
            if legacy:
                out.append(_LegacyTrack(**_tracked_state(i, ok, quad)))
            else:
                out.append(_make_tracked(
                    i, ok, quad, reason=reasons.get(i)))
        return out

    return fake_track


def _run_diagnostics(tmp_path, monkeypatch, *, reasons=None, legacy=False,
                     n_frames=_DIAG_FRAMES, ok_frames=None):
    """跑一次 build_motion_events(合成视频 + fake 轨迹 + mock OCR 固定一行)。

    返回 ``(events, summary, messages, quad0)``。
    """
    from scripts.motion_ass import build_motion_events

    video_path, quad0 = build_case(tmp_path, n=n_frames, scale_step=0.0)
    monkeypatch.setattr(
        "core.scene_plane_tracker.track_plane",
        _fake_track_plane(
            n_frames,
            set(_DIAG_OK_FRAMES if ok_frames is None else ok_frames),
            dict(reasons or {}), legacy=legacy))

    poly = [[10.0, 10.0], [60.0, 10.0], [60.0, 30.0], [10.0, 30.0]]

    def one_line_ocr(_img):
        return {"dt_polys": [poly], "rec_polys": [poly],
                "rec_texts": ["字幕"], "rec_scores": [0.95],
                "rec_boxes": [[10.0, 10.0, 60.0, 30.0]]}

    messages: list = []
    events, summary = build_motion_events(
        video_path, [[float(x), float(y)] for x, y in quad0],
        start_frame=0, end_frame=n_frames - 1, ocr_fn=one_line_ocr,
        log=messages.append)
    return events, summary, messages, quad0


def test_summary_lost_reasons_counts_known_lost_frames(tmp_path, monkeypatch):
    """(i) 直方图 = 合成轨迹里各 lost_reason 的实际帧数(降序;零出现不出现)。"""
    events, summary, messages, _quad0 = _run_diagnostics(
        tmp_path, monkeypatch, reasons=_DIAG_LOST_REASONS)

    assert summary["lost_reasons"] == {"high_reproj": 3, "few_matches": 2}
    # 顺序 = 帧数降序(并列按首次出现),日志与 summary 同源
    assert list(summary["lost_reasons"]) == ["high_reproj", "few_matches"]
    joined = "\n".join(messages)
    assert "lost reasons: high_reproj=3, few_matches=2" in joined, joined
    # 报告字段不影响产物:事件照常合成
    assert events


def test_summary_chains_counts_ok_runs_split_by_lost_gap(tmp_path, monkeypatch):
    """(ii) ok 链 = 被 lost 缺口切开的连续 ok 帧段:链数 + 逐链窗口(秒/ok 帧数)。"""
    events, summary, messages, _quad0 = _run_diagnostics(
        tmp_path, monkeypatch, reasons=_DIAG_LOST_REASONS)

    chains = summary["chains"]
    assert chains["count"] == 3
    assert chains["windows"] == [
        {"start_sec": 0.0, "end_sec": 0.25, "ok_frames": 3},      # 帧 0,1,2
        {"start_sec": 0.5, "end_sec": 0.625, "ok_frames": 2},     # 帧 4,5
        {"start_sec": 1.125, "end_sec": 1.25, "ok_frames": 2},    # 帧 9,10
    ]
    # 逐链 ok 帧数之和 = ok_frames 总数(链的定义不重不漏)
    assert sum(w["ok_frames"] for w in chains["windows"]) == summary["ok_frames"]
    assert summary["ok_frames"] == len(_DIAG_OK_FRAMES)
    # [1/5] 诊断日志:ok 占比 + 链数 + 逐链窗口
    joined = "\n".join(messages)
    assert "ok ratio 58.3%, 3 ok chain(s)" in joined, joined
    assert "t=0.00–0.25s (3 ok frames)" in joined, joined
    assert "t=0.50–0.62s (2 ok frames)" in joined, joined
    assert events


def test_trajectory_without_lost_reason_attribute_reports_empty(tmp_path,
                                                               monkeypatch):
    """(iii) 老 tracker 的轨迹对象没有 lost_reason:不崩,直方图为空(日志 none)。"""
    events, summary, messages, _quad0 = _run_diagnostics(
        tmp_path, monkeypatch, reasons=_DIAG_LOST_REASONS, legacy=True)

    assert summary["lost_reasons"] == {}
    joined = "\n".join(messages)
    assert "lost reasons: none" in joined, joined
    # ok 链不依赖 lost_reason,照常统计
    assert summary["chains"]["count"] == 3
    assert events


def test_summary_existing_keys_unchanged_by_reporting_fields(tmp_path,
                                                            monkeypatch):
    """(iv) 附加性保证:键集合 = 旧键 + {lost_reasons, chains};旧键的值与
    事件在「带 lost_reason」与「老 tracker 无该属性」两次运行间完全一致。"""
    events_new, summary_new, _msgs_new, quad0 = _run_diagnostics(
        tmp_path, monkeypatch, reasons=_DIAG_LOST_REASONS)
    events_old, summary_old, _msgs_old, _q = _run_diagnostics(
        tmp_path, monkeypatch, reasons=_DIAG_LOST_REASONS, legacy=True)

    legacy_keys = {
        "ok_frames", "total_frames", "keyframes", "hard_lines", "policy",
        "lines", "width", "height", "plane_size", "quad_window_origin",
        "ocr_engine", "tracks"}
    assert set(summary_new) == legacy_keys | {"lost_reasons", "chains"}

    # 旧键的值(与改动前的语义一致)
    assert summary_new["ok_frames"] == len(_DIAG_OK_FRAMES) == 7
    assert summary_new["total_frames"] == _DIAG_FRAMES
    assert summary_new["keyframes"]
    assert summary_new["hard_lines"] == []
    assert summary_new["policy"] == "overlap"
    assert summary_new["lines"] == 1
    assert (summary_new["width"], summary_new["height"]) == (FRAME_W, FRAME_H)
    assert summary_new["plane_size"] == [CARD_W - 1, CARD_H - 1]
    assert summary_new["quad_window_origin"] == [
        int(min(p[0] for p in quad0)), int(min(p[1] for p in quad0))]
    assert summary_new["ocr_engine"] == "default"
    assert len(summary_new["tracks"]) == _DIAG_FRAMES

    # 轨迹对象与事件不受报告字段影响(逐项/逐字节一致)
    assert [(t.frame_num, t.status) for t in summary_new["tracks"]] == [
        (t.frame_num, t.status) for t in summary_old["tracks"]]
    assert events_new == events_old
    for key in legacy_keys - {"tracks"}:
        assert summary_new[key] == summary_old[key], key


def test_low_coverage_warning_names_dominant_reason_and_chains(tmp_path,
                                                              monkeypatch):
    """覆盖率告警扩展:带上主导丢锁原因与链数;触发条件不变(≥24 帧且 <50%)。"""
    reasons = {i: "few_matches" for i in range(3, 40)}
    reasons.update({40: "high_reproj", 41: "high_reproj"})
    events, summary, messages, _quad0 = _run_diagnostics(
        tmp_path, monkeypatch, ok_frames={0, 1, 2}, reasons=reasons,
        n_frames=48)

    joined = "\n".join(messages)
    assert "plane tracked on only 6.2% of frames" in joined, joined
    assert "1 chain(s)" in joined, joined
    assert "dominant loss reason few_matches" in joined, joined
    assert "falls back to the static policy" in joined, joined
    assert summary["lost_reasons"] == {"few_matches": 37, "high_reproj": 2}
    assert summary["chains"]["count"] == 1
    assert events


# ---------------------------------------------------------------------------
# 按链重锚定:纯规划 / 事件合并 / run_pipeline 编排(4K 歌词条丢锁形态)
# ---------------------------------------------------------------------------

# 4K 实测(见 scripts/motion_ass.py 的 REANCHOR_* 注释):首趟锚帧 230,ok 帧 =
# 230-396 与 471-558;内容窗口(quad 内部特征数 ≥ _INIT_ROI_MIN_FEATURES = 192,
# 扫描器缺省判据)按「一行歌词一个窗口」给出 230-396、414-556、579-718、721-800
# ——帧 719-720 的计数(111/105)落在两行歌词之间,192 恰好把窗口切开;其中
# 230-396 首趟 100% 覆盖(整窗不碰),414-556 只有 86/143 = 60.1%(整窗重跑),
# 579-718 与 721-800 为 0%。
_FOUR_K_PASS1_OK = set(range(230, 397)) | set(range(471, 559))
_FOUR_K_CONTENT = [(230, 396), (414, 556), (579, 718), (721, 800)]


class TestReanchorPlanning:
    def test_trigger_boundary_is_strictly_below_threshold(self):
        """恰好等于门限(0.6)不触发;0.599 触发(严格小于)。"""
        assert motion_cli.should_reanchor(0.599) is True
        assert motion_cli.should_reanchor(0.6) is False
        assert motion_cli.should_reanchor(0.601) is False
        assert motion_cli.should_reanchor(1.0) is False

    def test_no_chunks_when_coverage_adequate(self):
        """门限之上不规划任何补跑段(零回归路径:连扫描都不该跑)。"""
        assert motion_cli.plan_reanchor_chunks(
            0.6, {230}, [(230, 396), (414, 556)]) == []
        assert motion_cli.plan_reanchor_chunks(
            0.9, set(), [(0, 100)]) == []

    def test_window_coverage_boundary(self):
        """窗口覆盖率恰好等于 0.8 不替换;0.799 替换(严格小于)。"""
        window = [(100, 199)]  # 100 帧
        ok = set(range(100, 180))  # 80/100 = 0.80 → 达标,整窗不碰
        assert motion_cli.plan_reanchor_windows(ok, window) == []
        ok_less = set(range(100, 179))  # 79/100 = 0.79 → 整窗重跑
        windows = motion_cli.plan_reanchor_windows(ok_less, window)
        assert [w["start_frame"] for w in windows] == [100]
        assert windows[0]["chunks"] == [(100, 199)]

    def test_four_k_case_replaces_whole_windows(self):
        """4K 实测形态:整窗替换 → 5 段(414-556 两段 + 579-718 两段 + 721-800 一段)。

        414-556 首趟只覆盖 86/143(60.1%)= 窗口尾部挂着上一行的错字,故整窗
        (含首趟已 ok 的部分)重跑,切成 414-533(120)+ 534-556(23);579-718
        (140 帧,ゆっくり… 一行)切成 579-698(120)+ 699-718(20);721-800
        (80 帧,また増やして… 一行)一段;230-396 覆盖 100% 整窗不碰。窗口按行
        切开(dip-aware),故没有一段跨在行变更上。
        """
        windows = motion_cli.plan_reanchor_windows(
            _FOUR_K_PASS1_OK, _FOUR_K_CONTENT)
        assert [(w["start_frame"], w["end_frame"]) for w in windows] == [
            (414, 556), (579, 718), (721, 800)]
        assert windows[0]["chunks"] == [(414, 533), (534, 556)]
        assert windows[0]["coverage"] == pytest.approx(86 / 143)
        assert windows[1]["chunks"] == [(579, 698), (699, 718)]
        assert windows[2]["chunks"] == [(721, 800)]
        assert windows[1]["coverage"] == 0.0 and windows[2]["coverage"] == 0.0

        chunks = motion_cli.plan_reanchor_chunks(
            255 / 801.0, _FOUR_K_PASS1_OK, _FOUR_K_CONTENT)
        assert chunks == [(414, 533), (534, 556), (579, 698), (699, 718),
                          (721, 800)]
        # 5 段落在 REANCHOR_MAX_PASSES 之内(实测需要 5,上限留一个窗口余量)
        assert len(chunks) <= motion_cli.REANCHOR_MAX_PASSES
        # 段落在自己的窗口内、段间互不重叠(替换按窗口跨度删除的前提)
        spans = [(w["start_frame"], w["end_frame"]) for w in windows
                 if w["chunks"]]
        seen: set = set()
        for first, last in chunks:
            frames = set(range(first, last + 1))
            assert any(a <= first and last <= b for a, b in spans)
            assert not (frames & seen)
            seen |= frames
        # 窗口内首趟已 ok 的帧也进入替换段(整窗重跑,不是只补缺口)
        assert set(range(471, 534)) & seen

    def test_short_chunks_are_dropped(self):
        """短于 ``REANCHOR_MIN_CHUNK_FRAMES`` 的块丢弃(含切分产生的短尾块)。"""
        # 单窗口 16 帧、上限 120:整窗一段,保留
        assert motion_cli.plan_reanchor_chunks(
            0.2, set(), [(10, 25)]) == [(10, 25)]
        # 单窗口 4 帧:短于 8,丢弃 → 空
        assert motion_cli.plan_reanchor_chunks(0.2, set(), [(10, 13)]) == []
        # 单窗口 121 帧、上限 120:120 帧一段保留,1 帧尾段丢弃
        assert motion_cli.plan_reanchor_chunks(
            0.2, set(), [(0, 120)]) == [(0, 119)]
        # 单窗口 128 帧:120 + 8,尾段恰好等于下限,保留
        assert motion_cli.plan_reanchor_chunks(
            0.2, set(), [(0, 127)]) == [(0, 119), (120, 127)]

    def test_max_passes_bound_keeps_earliest_chunks(self):
        """补跑趟数硬上限:按窗口序/帧序取最早的前 N 段,其余窗口留空。"""
        content = [(10, 19), (30, 39), (50, 59), (70, 79)]
        assert motion_cli.plan_reanchor_chunks(
            0.2, set(), content) == [(10, 19), (30, 39), (50, 59), (70, 79)]
        assert motion_cli.plan_reanchor_chunks(
            0.2, set(), content, max_chunks=2) == [(10, 19), (30, 39)]
        assert motion_cli.plan_reanchor_chunks(
            0.2, set(), content, max_chunks=0) == []
        # 窗口级:上限截断后,末尾窗口的 chunks 为空(调用方据此判「未替换」)
        windows = motion_cli.plan_reanchor_windows(
            set(), content, max_chunks=3)
        assert [len(w["chunks"]) for w in windows] == [1, 1, 1, 0]
        # 缺省上限 = REANCHOR_MAX_PASSES = 6(4K 实测需要 5,留一个窗口余量)
        assert motion_cli.REANCHOR_MAX_PASSES == 6
        seven = [(i * 10, i * 10 + 9) for i in range(7)]
        assert len(motion_cli.plan_reanchor_chunks(0.2, set(), seven)) == 6

    def test_partial_window_truncation_keeps_prefix(self):
        """上限切到一个窗口的后半截:保留前缀段,尾部留空(调用方告警)。"""
        windows = motion_cli.plan_reanchor_windows(
            set(), [(0, 239)], max_chunks=1)  # 240 帧 → 两段,只留第一段
        assert windows[0]["chunks"] == [(0, 119)]
        assert windows[0]["chunks"][-1][1] < windows[0]["end_frame"]

    def test_windows_need_replacement_only(self):
        """只有覆盖率不足的窗口进入计划(窗口外的 lost 帧不参与切段)。"""
        assert motion_cli.plan_reanchor_windows(
            {0, 1, 2}, [(20, 29)]) == [{
                "start_frame": 20, "end_frame": 29, "coverage": 0.0,
                "chunks": [(20, 29)]}]
        # 窗口 10-29(20 帧)内 20-25 为 ok(6/20 = 0.3 < 0.8)→ 整窗一段
        assert motion_cli.plan_reanchor_windows(
            {20, 21, 22, 23, 24, 25}, [(10, 29)]) == [{
                "start_frame": 10, "end_frame": 29, "coverage": 0.3,
                "chunks": [(10, 29)]}]

    def test_window_time_span_uses_pass1_timestamps(self):
        """窗口时间跨度取首趟轨迹里首/末帧的 time_sec;越界帧取最近可用帧。"""
        times = {f: f / 25.0 for f in range(10, 21)}
        assert motion_cli._window_time_span(10, 20, times) == (0.4, 0.8)
        assert motion_cli._window_time_span(5, 30, times) == (0.4, 0.8)
        assert motion_cli._window_time_span(10, 20, {}) is None


class TestWindowReplacement:
    """替换语义的纯函数:删除与「删除后不得残留」的校验。"""

    def _events(self):
        return [
            {"start_time": "0:00:09.59", "end_time": "0:00:16.55", "body": "早期"},
            {"start_time": "0:00:19.64", "end_time": "0:00:19.72", "body": "窗口内"},
            {"start_time": "0:00:22.60", "end_time": "0:00:23.31", "body": "跨界"},
            {"start_time": "0:00:23.19", "end_time": "0:00:24.00", "body": "相接"},
        ]

    def test_drop_events_over_window(self):
        """重叠(正长度交集)的事件删除;端点相接的不算重叠。"""
        kept, dropped = motion_cli.drop_events_over_window(
            self._events(), (17.27, 23.19))
        assert [ev["body"] for ev in kept] == ["早期", "相接"]
        assert dropped == 2

    def test_surviving_window_overlaps_flags_other_passes_only(self):
        """校验只看「非本窗口替换趟」的事件;替换趟自身落在窗口内不算违规。"""
        window_events = [{"start_time": "0:00:19.00", "end_time": "0:00:20.00"}]
        stale = [{"start_time": "0:00:19.64", "end_time": "0:00:19.72"}]
        records = [(None, stale), (0, window_events)]
        assert motion_cli.surviving_window_overlaps(
            records, 0, (17.27, 23.19)) == stale
        assert motion_cli.surviving_window_overlaps(
            [(None, []), (0, window_events)], 0, (17.27, 23.19)) == []


def _ass_event(start_sec: float, body: str) -> dict:
    """最小可用事件 dict(字段与 synthesize_events 产物同构,供 write_ass 用)。"""
    h = int(start_sec // 3600)
    m = int(start_sec % 3600 // 60)
    s = start_sec % 60
    stamp = f"{h}:{m:02d}:{s:05.2f}"
    return {"start_time": stamp, "end_time": stamp, "style": "Scene",
            "name": "motion", "tags": "", "body": body}


class TestMergePassEvents:
    def test_orders_by_parsed_start_time_across_passes(self):
        """多趟事件按 start 时间合并(趟序不参与排序,时间才是主序)。"""
        pass1 = [_ass_event(20.0, "A"), _ass_event(24.0, "B")]
        chunk1 = [_ass_event(10.0, "C")]
        chunk2 = [_ass_event(50.0, "D"), _ass_event(21.0, "E")]

        merged = motion_cli.merge_pass_events([pass1, chunk1, chunk2])
        assert [ev["body"] for ev in merged] == ["C", "A", "E", "B", "D"]
        assert [_ass_time_to_sec(ev["start_time"]) for ev in merged] == \
            sorted(_ass_time_to_sec(ev["start_time"]) for ev in merged)

    def test_equal_starts_keep_pass_order(self):
        """start 完全相同的并列事件:稳定排序,首趟在前、其后按趟序。"""
        merged = motion_cli.merge_pass_events([
            [_ass_event(5.0, "pass1")],
            [_ass_event(5.0, "chunk1"), _ass_event(5.0, "chunk1b")],
            [_ass_event(5.0, "chunk2")],
        ])
        assert [ev["body"] for ev in merged] == \
            ["pass1", "chunk1", "chunk1b", "chunk2"]

    def test_empty_and_single_pass(self):
        assert motion_cli.merge_pass_events([]) == []
        assert motion_cli.merge_pass_events([[]]) == []
        only = [_ass_event(1.0, "X"), _ass_event(2.0, "Y")]
        assert motion_cli.merge_pass_events([only]) == only


class TestLazyOcrFn:
    def test_engine_loaded_on_first_call_and_released_once(self, monkeypatch):
        """引擎延迟到首次调用才构造(前置失败路径不加载模型),且只构造一次。"""
        built: list = []

        class _Engine:
            def __init__(self):
                self.cleaned = 0

            def cleanup(self):
                self.cleaned += 1

        def fake_default(engine_id=None, engine_options=None):
            engine = _Engine()
            built.append(engine)
            return (lambda _img: {"engine": engine_id, "loads": len(built)}), engine

        monkeypatch.setattr(motion_cli, "_default_ocr_fn", fake_default)
        fn = motion_cli._LazyOcrFn("rapid")

        assert built == []  # 构造可调用对象本身不碰模型
        assert fn("img") == {"engine": "rapid", "loads": 1}
        assert len(built) == 1
        assert fn("img") == {"engine": "rapid", "loads": 1}  # 复用同一引擎
        assert len(built) == 1

        fn.release()
        fn.release()  # 重复释放无副作用
        assert built[0].cleaned == 1

    def test_release_without_use_is_noop(self, monkeypatch):
        monkeypatch.setattr(
            motion_cli, "_default_ocr_fn",
            lambda engine_id=None, engine_options=None: (_ for _ in ()).throw(
                AssertionError("must not load the engine")))
        motion_cli._LazyOcrFn(None).release()  # 没调用过 → 不构造也不释放


def _fake_motion_pass(ok_by_span, events_by_span):
    """构造 fake ``build_motion_events``:按 (start_frame, end_frame) 返回
    (事件, summary);summary 含 run_pipeline 编排用到的全部键。

    ``ok_by_span(span)`` → 该趟 ok 帧集合;``events_by_span(span)`` → 该趟事件。
    """
    from core.scene_plane_tracker import TrackedQuad

    def fake_build(video_path, quad, **kwargs):
        start = int(kwargs["start_frame"])
        end = int(kwargs["end_frame"])
        ok = {int(f) for f in ok_by_span((start, end))}
        tracks = [
            TrackedQuad(frame_num=f, time_sec=f / 25.0,
                        status="ok" if f in ok else "lost")
            for f in range(start, end + 1)
        ]
        # 故障注入:ok 集合里落在本趟区间外的帧也产出 ok 轨迹(run_pipeline
        # 的「本趟 ok 帧越界」校验应能看见并留痕)。
        tracks += [TrackedQuad(frame_num=f, time_sec=f / 25.0, status="ok")
                   for f in sorted(ok - set(range(start, end + 1)))]
        return events_by_span((start, end)), {
            "ok_frames": len(ok), "total_frames": end - start + 1,
            "keyframes": [start], "hard_lines": [], "policy": "overlap",
            "lines": 1, "width": FRAME_W, "height": FRAME_H,
            "plane_size": [10, 10], "quad_window_origin": [0, 0],
            "ocr_engine": "default", "tracks": tracks,
        }

    return fake_build


def _run_orchestration(tmp_path, monkeypatch, *, content_windows, ok_first,
                       events_first, events_chunks, ok_chunks=None,
                       scan_error=None, trajectory_json=None):
    """在 run_pipeline 上跑一次(全 mock)重锚定编排。

    首趟喂 ``ok_first`` / ``events_first``(帧区间固定 [0, 99]);补跑趟按
    ``events_chunks`` 给事件(值为 Exception 表示该趟失败),ok 帧默认整段 ok,
    ``ok_chunks`` 可逐段覆盖(用于构造「补跑段与首趟 ok 帧重叠」的破坏场景);
    ``scan_error`` 让内容扫描抛错。返回 ``(summary, scan_calls)``:scan_calls =
    扫描器被调用的 (start, end)。
    """
    scan_calls: list = []
    scan_kwargs: list = []
    overrides = dict(ok_chunks or {})

    def ok_by_span(span):
        if span == (0, 99):
            return ok_first
        if span in overrides:
            return overrides[span]
        return set(range(span[0], span[1] + 1))

    def events_by_span(span):
        if span == (0, 99):
            return list(events_first)
        if span in events_chunks:
            chunk_events = events_chunks[span]
            if isinstance(chunk_events, Exception):
                raise chunk_events
            return list(chunk_events)
        return []

    def spy_scan(video_path, quad, start_frame=0, end_frame=None, **kwargs):
        scan_calls.append((int(start_frame), end_frame))
        scan_kwargs.append(dict(kwargs))
        if scan_error is not None:
            raise scan_error
        return list(content_windows)

    monkeypatch.setattr(motion_cli, "build_motion_events",
                        _fake_motion_pass(ok_by_span, events_by_span))
    monkeypatch.setattr("core.scene_plane_tracker.scan_content_windows", spy_scan)

    quad = [[10.0, 10.0], [60.0, 10.0], [60.0, 30.0], [10.0, 30.0]]
    summary = motion_cli.run_pipeline(
        "synthetic.avi", str(tmp_path / "orch.ass"), quad,
        start_frame=0, end_frame=99, ocr_fn=lambda _img: {},
        trajectory_json=trajectory_json,
        quiet=False)
    summary["_scan_kwargs"] = scan_kwargs  # 测试用:扫描器收到的关键字参数
    return summary, scan_calls


def test_run_pipeline_replaces_undercovered_windows_and_merges(tmp_path,
                                                               monkeypatch,
                                                               capsys):
    """编排:首趟 20% → 扫描 → 整窗替换 2 段 → 旧事件删除 + 合并 + 报告字段。

    场景:mocked 帧→秒为 ``f/25``,内容窗口 (0,19)/(20,39)/(60,79)。窗口 1 首趟
    100% 覆盖(整窗不碰,其事件保留);窗口 2/3 覆盖 0% → 各一段替换趟。首趟的
    3 条事件里有 2 条落在被替换窗口的时间跨度内(1.00s ∈ [0.80, 1.56]、2.80s ∈
    [2.40, 3.16]),必须被删掉;替换趟事件照常并入,合并按 start 排序写盘。
    """
    events_chunks = {(20, 39): [_ass_event(1.20, "chunkA")],
                     (60, 79): [_ass_event(3.00, "chunkB")]}
    traj_path = tmp_path / "orch_traj.json"
    summary, scan_calls = _run_orchestration(
        tmp_path, monkeypatch, content_windows=[(0, 19), (20, 39), (60, 79)],
        ok_first=set(range(0, 20)),
        events_first=[_ass_event(0.40, "p1a"), _ass_event(1.00, "p1w2"),
                      _ass_event(2.80, "p1w3")],
        events_chunks=events_chunks, trajectory_json=str(traj_path))
    err = capsys.readouterr().err

    assert scan_calls == [(0, 99)]  # 触发后扫一遍[start, end]
    # 扫描判据用缺省值(192,逐行歌词的 dip-aware 判据),不覆盖 min_features
    assert summary["_scan_kwargs"] == [{}]
    assert "coverage 20.0% < 60%: re-anchoring: replacing 2 of 3 content " \
        "window(s) [[20, 39], [60, 79]] with 2 pass(es) " \
        "[(20, 39), (60, 79)]" in err, err
    assert "re-anchor pass on frames [20, 39]: 20/20 frame(s) ok, 1 event(s)" in err
    assert "re-anchor pass on frames [60, 79]: 20/20 frame(s) ok, 1 event(s)" in err
    # 替换留痕:窗口时间跨度取首趟轨迹的 time_sec(帧 20/39 → 0.80/1.56 s)
    assert "replaced window [20, 39] (t=0.80-1.56 s, coverage 0%): " \
        "dropped 1 event(s)" in err, err
    assert "replaced window [60, 79] (t=2.40-3.16 s, coverage 0%): " \
        "dropped 1 event(s)" in err, err
    # [4/5] 报替换后的合并总量(3 事件 / 3 行)
    assert "[4/5] synthesized 3 event(s) for 3 line(s)" in err, err

    assert summary["reanchored"] is True
    assert summary["ok_frames"] == 20 + 20 + 20  # 各趟 ok 帧并集(此处不重叠)
    assert summary["total_frames"] == 100
    assert summary["merged_lines"] == 3
    assert summary["replaced_windows"] == [
        {"start_frame": 20, "end_frame": 39, "coverage": 0.0,
         "dropped_events": 1},
        {"start_frame": 60, "end_frame": 79, "coverage": 0.0,
         "dropped_events": 1},
    ]
    assert summary["passes"] == [
        {"start_frame": 0, "end_frame": 99, "ok_frames": 20, "events": 3},
        {"start_frame": 20, "end_frame": 39, "ok_frames": 20, "events": 1},
        {"start_frame": 60, "end_frame": 79, "ok_frames": 20, "events": 1},
    ]
    # 写盘内容 = 替换后的事件,按 start 排序:首趟的过期两条已不在
    bodies = [line.split(",", 9)[9] for line in
              _dialogue_lines(open(str(tmp_path / "orch.ass"),
                                   encoding="utf-8-sig").read())]
    assert [b.split("}")[-1] for b in bodies] == ["p1a", "chunkA", "chunkB"]
    # --trajectory-json 仍是**首趟**轨迹(补跑趟不并入:tracks 只含 [0, 99])
    loaded = load_trajectory(str(traj_path))
    assert [fr["frame_num"] for fr in loaded["frames"]] == list(range(0, 100))
    assert loaded["meta"]["keyframes"] == [0]


def test_run_pipeline_skips_reanchor_when_coverage_adequate(tmp_path,
                                                            monkeypatch):
    """覆盖率达标:不扫描、不补跑,报告字段退化为单趟(零回归路径)。"""
    summary, scan_calls = _run_orchestration(
        tmp_path, monkeypatch, content_windows=[(0, 19), (20, 39)],
        ok_first=set(range(0, 90)),
        events_first=[_ass_event(20.0, "p1")],
        events_chunks={})

    assert scan_calls == []  # 连内容扫描都没跑(不额外解码)
    assert summary["reanchored"] is False
    assert summary["ok_frames"] == 90
    assert summary["merged_lines"] == 1
    assert summary["replaced_windows"] == []
    assert summary["passes"] == [
        {"start_frame": 0, "end_frame": 99, "ok_frames": 90, "events": 1}]


def test_run_pipeline_empty_content_windows_skips_reanchor(tmp_path,
                                                           monkeypatch):
    """扫描无内容窗口 ⇒ 无段可补:不补跑、不报错,产物仍是首趟事件。"""
    summary, scan_calls = _run_orchestration(
        tmp_path, monkeypatch, content_windows=[], ok_first=set(range(0, 20)),
        events_first=[_ass_event(20.0, "p1")], events_chunks={})

    assert scan_calls == [(0, 99)]
    assert summary["reanchored"] is False
    assert summary["passes"] == [
        {"start_frame": 0, "end_frame": 99, "ok_frames": 20, "events": 1}]
    assert summary["merged_lines"] == 1


def test_run_pipeline_survives_failing_chunk_pass(tmp_path, monkeypatch,
                                                  capsys):
    """某段补跑失败(无 ok 帧 / 无文字行)只留痕跳过,其余段照常替换合并。

    失败的那段所属窗口仍按「已决定替换」处理:其旧事件照删(宁可留空,不挂
    错字),``replaced_windows`` 如实记下 dropped 数而没有任何替换事件。
    """
    events_chunks = {(20, 39): RuntimeError("no ok frames"),
                     (60, 79): [_ass_event(3.00, "chunkB")]}
    summary, _scan_calls = _run_orchestration(
        tmp_path, monkeypatch, content_windows=[(0, 19), (20, 39), (60, 79)],
        ok_first=set(range(0, 20)),
        events_first=[_ass_event(0.40, "p1a"), _ass_event(1.00, "p1w2"),
                      _ass_event(2.80, "p1w3")],
        events_chunks=events_chunks)
    err = capsys.readouterr().err

    assert "warning: re-anchor pass on frames [20, 39] failed " \
        "(no ok frames); skipping" in err, err
    assert "re-anchor pass on frames [60, 79]" in err, err
    assert summary["reanchored"] is True
    assert summary["ok_frames"] == 20 + 20
    assert summary["replaced_windows"] == [
        {"start_frame": 20, "end_frame": 39, "coverage": 0.0,
         "dropped_events": 1},
        {"start_frame": 60, "end_frame": 79, "coverage": 0.0,
         "dropped_events": 1},
    ]
    assert summary["passes"] == [
        {"start_frame": 0, "end_frame": 99, "ok_frames": 20, "events": 3},
        {"start_frame": 60, "end_frame": 79, "ok_frames": 20, "events": 1},
    ]
    assert "[4/5] synthesized 2 event(s) for 2 line(s)" in err, err


def test_run_pipeline_short_window_is_not_replaced(tmp_path, monkeypatch,
                                                   capsys):
    """窗口短到切不出段(或预算不够):不替换、不删旧事件,留痕说明。"""
    summary, _scan_calls = _run_orchestration(
        tmp_path, monkeypatch, content_windows=[(0, 19), (20, 23)],
        ok_first=set(range(0, 20)),
        events_first=[_ass_event(0.40, "p1a"), _ass_event(1.00, "p1")],
        events_chunks={})
    err = capsys.readouterr().err

    assert "warning: 1 window(s) not replaced" in err, err
    assert "[20, 23]" in err, err
    assert summary["reanchored"] is False
    assert summary["replaced_windows"] == []
    # 旧事件全部保留(没有替换趟,就没有删除)
    bodies = [line.split(",", 9)[9] for line in
              _dialogue_lines(open(str(tmp_path / "orch.ass"),
                                   encoding="utf-8-sig").read())]
    assert [b.split("}")[-1] for b in bodies] == ["p1a", "p1"]


def test_run_pipeline_partial_window_replacement_warns(tmp_path, monkeypatch,
                                                       capsys):
    """段数上限切掉窗口后半截:前缀仍替换(尾部留空),留痕说明不是完整替换。"""
    summary, _scan_calls = _run_orchestration(
        tmp_path, monkeypatch, content_windows=[(0, 839)],  # 840 帧 → 7 段
        ok_first=set(range(0, 20)),
        events_first=[_ass_event(0.40, "p1a")],
        events_chunks={})
    err = capsys.readouterr().err

    assert "warning: pass budget exhausted: 1 window(s) only partially " \
        "replaced (tail stays blank)" in err, err
    # 840 帧 → 7 段,上限 6:只跑前 6 段(0-119 … 600-719)
    assert [p["start_frame"] for p in summary["passes"]] == [
        0, 0, 120, 240, 360, 480, 600]
    assert summary["passes"][-1]["end_frame"] == 719


def test_run_pipeline_scan_failure_falls_back_to_first_pass(tmp_path, monkeypatch,
                                                            capsys):
    """内容扫描失败:留痕跳过重锚定,首趟字幕照常写出(不拖垮整条链)。"""
    summary, scan_calls = _run_orchestration(
        tmp_path, monkeypatch, content_windows=[],
        ok_first=set(range(0, 20)),
        events_first=[_ass_event(20.0, "p1")], events_chunks={},
        scan_error=RuntimeError("cannot decode frame 500"))
    err = capsys.readouterr().err

    assert scan_calls == [(0, 99)]
    assert "warning: content scan failed (cannot decode frame 500); " \
        "skipping re-anchoring" in err, err
    assert summary["reanchored"] is False
    assert summary["passes"] == [
        {"start_frame": 0, "end_frame": 99, "ok_frames": 20, "events": 1}]
    assert "[4/5] synthesized 1 event(s) for 1 line(s)" in err, err


def test_run_pipeline_warns_when_stale_events_survive_replacement(tmp_path,
                                                                  monkeypatch,
                                                                  capsys):
    """替换后校验:删除漏掉时(此处把删除替换成 no-op)必须告警而不是静默。

    ``drop_events_over_window`` 被替换成不删任何事件,于是首趟那条落在窗口跨度
    内的事件存活下来——校验必须报出「该窗口仍有其他趟的事件重叠」。
    """
    monkeypatch.setattr(motion_cli, "drop_events_over_window",
                        lambda events, span: (list(events), 0))
    summary, _scan_calls = _run_orchestration(
        tmp_path, monkeypatch, content_windows=[(0, 19), (20, 39)],
        ok_first=set(range(0, 20)),
        events_first=[_ass_event(0.40, "p1a"), _ass_event(1.00, "p1w2")],
        events_chunks={(20, 39): [_ass_event(1.20, "chunkA")]})
    err = capsys.readouterr().err

    assert "replaced window [20, 39] (t=0.80-1.56 s, coverage 0%): " \
        "dropped 0 event(s)" in err, err
    assert "warning: replaced window [20, 39] still has 1 event(s) from " \
        "other passes overlapping its span" in err, err
    assert summary["replaced_windows"] == [
        {"start_frame": 20, "end_frame": 39, "coverage": 0.0,
         "dropped_events": 0}]
    # 告警但不停机:产物照写(此刻窗口内既有旧事件也有替换事件)
    assert summary["events"] == 3


def test_run_pipeline_warns_when_chunk_falls_outside_its_window(tmp_path,
                                                                monkeypatch,
                                                                capsys):
    """不变量:补跑段必须落在它所替换的窗口内(规划保证;破坏时留痕)。"""
    monkeypatch.setattr(
        motion_cli, "plan_reanchor_windows",
        lambda *a, **kw: [{"start_frame": 20, "end_frame": 25,
                           "coverage": 0.0, "chunks": [(20, 39)]}])
    _summary, _scan_calls = _run_orchestration(
        tmp_path, monkeypatch, content_windows=[(0, 19), (20, 39)],
        ok_first=set(range(0, 20)),
        events_first=[_ass_event(0.40, "p1a")],
        events_chunks={(20, 39): [_ass_event(1.20, "chunkA")]})
    err = capsys.readouterr().err

    assert "warning: re-anchor pass on frames [20, 39] falls outside " \
        "window [20, 25]" in err, err


def test_run_pipeline_warns_when_chunk_reports_out_of_range_ok(tmp_path,
                                                               monkeypatch,
                                                               capsys):
    """补跑趟自报区间外的 ok 帧:留痕并从合并 ok 总数里剔除(不串趟计数)。"""
    summary, _scan_calls = _run_orchestration(
        tmp_path, monkeypatch, content_windows=[(0, 19), (20, 39)],
        ok_first=set(range(0, 20)),
        events_first=[_ass_event(20.0, "p1")],
        events_chunks={(20, 39): [_ass_event(30.0, "chunkA")]},
        ok_chunks={(20, 39): {25, 26, 77}})  # 77 不在本趟区间内
    err = capsys.readouterr().err

    assert "warning: re-anchor pass on frames [20, 39] reports ok frame(s) " \
        "outside its own range (e.g. [77])" in err, err
    assert summary["passes"][1]["ok_frames"] == 2  # 只算区间内的 25/26
    assert summary["ok_frames"] == 20 + 2
