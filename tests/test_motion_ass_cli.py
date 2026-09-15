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

from core.scene_plane_tracker import load_trajectory  # noqa: E402
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
