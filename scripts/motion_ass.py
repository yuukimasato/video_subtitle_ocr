#!/usr/bin/env python3
# scripts/motion_ass.py
"""移动文字轨迹 → ASS 轨迹字幕:端到端 CLI(阶段一,手动触发)。

串接 core 各模块,完成「视频 + quad → 逐帧平面跟踪 → 清晰关键帧选取 →
关键帧 OCR(统一坐标)→ 按位置投票融合 → 轨迹合成 ASS 事件 → 写 .ass」:

1. ``scene_plane_tracker.track_plane``:逐帧单应跟踪(质量门限,lost 不外推);
2. ``keyframe_selector.select_keyframes``:展开图 Laplacian 方差选 top-K 清晰帧;
3. 关键帧 ``unwarp_canonical`` 展开到统一坐标(初始帧平面坐标)后 OCR;
4. ``ocr_optimizer.fuse_samples_by_position``:锚定帧 = 最清晰关键帧,按行框
   位置对齐投票(阶段一不接 VLM,``--vlm-min-confidence`` 缺省 0.0 使难行
   列表尽量少);
5. ``motion_ass.build_line_tracks`` → ``pose_verify.verify_line_tracks``
   (模板匹配实测行在画面中的真实位置,锚回单应轨迹;静止行吸附常量
   位姿,不可见跨度删位姿)→ ``smooth_line_track``(显式调用,合成器
   内部不平滑)→ ``synthesize_events``:标签阶梯(静止段单条 ``\\pos`` /
   单段 \\move / 分段 \\move / \\t 旋转缩放 / 帧级 \\pos 兜底,lost 切段);
6. ``scene_text_policy.apply_policy``(--scene-text-policy,默认 overlap 不改
   变任何输出):mask 生成 \\p1 纯色遮罩盖原文字、识别文本升 layer 1;
   mask_only 只出遮罩——识别文本写成 Comment 行(不渲染),layer 1 留给
   用户自行排版覆写(typesetting);external 挪出区域放底带
   NoteBox、whitespace 放进原文字空白带;所选模式不可用时按
   whitespace→mask→external、mask_only→external 自动回退(stderr 告警);
7. 写 .ass(UTF-8-sig;头与主流水线默认样式一致,事件 Name=motion,按解析
   start 时间排序)。

统一坐标约定(与 core/motion_ass.build_line_tracks 对齐):初始帧平面坐标,
即「关键帧(= start_frame)画面」的屏幕坐标。unwarp_canonical 的输出像素
(i, j) 恰为平面点 (i, j)(见 core/scene_plane_tracker.unwarp_canonical);
本脚本再把窗口锚定到 init quad 的外接矩形左上角 (qx1, qy1)、尺寸由 quad
边长推导(与 keyframe_selector 缺省逻辑一致),因此 OCR 行框(窗口像素
坐标)加回常量偏移 (qx1, qy1) 即平面坐标行框——是常量平移,非坐标还原。

用法(四角顺序:左上 → 右上 → 右下 → 左下,顺时针):
    python scripts/motion_ass.py --video clip.mp4 \
        (--quad-file quad.json | --quad "x1,y1 x2,y2 x3,y3 x4,y4") \
        --out motion.ass \
        [--start-frame 0] [--end-frame N] [--trajectory-json traj.json] \
        [--config-json cfg.json] [--ocr-engine rapid|paddle] \
        [--keyframe-count 3] [--min-gap-sec 0.33] [--vlm-min-confidence 0.0] \
        [--auto-brightness] \
        [--scene-text-policy overlap|mask|mask_only|external|whitespace]

quad 文件格式:{"video": "...", "frame": 0, "quad": [[x, y] × 4]}
(兼容裸 4×2 列表,与 scripts/track_plane.py 一致)。顶点顺序必须为顺时针
(与 cv2.boxPoints 的 TL,TR,BR,BL 一致);逆时针会使展开图镜像,检测到
即报错并以非零码退出。

OCR 通过 ``ocr_fn`` 注入(``main(..., ocr_fn=...)``),缺省用引擎管理器构造
独立引擎实例;单测注入 mock,不加载真模型。阶段一不接 VLM 复核。
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import os
import sys
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

OcrFn = Callable[[Any], Dict[str, Any]]

# 关键帧候选池下限:按清晰度降序分批 OCR,最清晰批无文字行时换下一批
# (空白引导段的最清晰帧先于文字出现,见 select_keyframes 调用处注释)。
_KEYFRAME_POOL = 12


# ---------------------------------------------------------------------------
# quad 输入与校验
# ---------------------------------------------------------------------------

def parse_quad_spec(spec: str) -> List[List[float]]:
    """解析 "x1,y1 x2,y2 x3,y3 x4,y4"(兼容逗号/空白混合分隔的 8 个数)。"""
    nums = [v for chunk in str(spec).replace(",", " ").split() for v in [chunk]]
    try:
        values = [float(v) for v in nums]
    except ValueError:
        raise ValueError(f"invalid quad spec: {spec!r}")
    if len(values) != 8:
        raise ValueError(
            f"quad needs 8 numbers (4 points), got {len(values)}: {spec!r}")
    return [[values[i], values[i + 1]] for i in range(0, 8, 2)]


def load_quad_file(path: str) -> List[List[float]]:
    """读 quad 文件:{"video": ..., "frame": ..., "quad": [[x, y] × 4]}。

    兼容裸 4×2 列表(与 scripts/track_plane.py 的 quad 文件一致)。
    """
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    if isinstance(data, dict):
        if "quad" not in data:
            raise ValueError(f"quad file must contain a 'quad' field: {path}")
        return data["quad"]
    return data


def validate_quad(quad: Sequence[Sequence[float]]) -> List[List[float]]:
    """校验 4×2 / 有限数值 / 最小面积 / 凸性(复用 tracker 的
    ``_validate_init_quad``,与其凸性校验保持兼容),并强制顶点顺序为顺时针。

    图像坐标系 y 向下,TL,TR,BR,BL(顺时针,与 cv2.boxPoints 一致)的
    有向面积(鞋带公式)为正;逆时针会使 unwarp 展开图镜像、跟踪几何
    翻转,检测到即抛 :class:`ValueError`。
    """
    import numpy as np

    from core.scene_plane_tracker import _validate_init_quad

    try:
        arr = _validate_init_quad(quad)
    except TypeError as exc:  # 结构非法(如 dict)统一按输入错误处理
        raise ValueError(f"invalid quad structure: {quad!r} ({exc})") from exc
    pts = arr.reshape(-1, 2)
    x, y = pts[:, 0], pts[:, 1]
    signed2 = float(np.sum(x * np.roll(y, -1) - np.roll(x, -1) * y))
    if signed2 <= 0:
        raise ValueError(
            "quad vertex order must be clockwise (TL,TR,BR,BL, like "
            f"cv2.boxPoints); got counter-clockwise quad (signed area "
            f"{0.5 * signed2:.1f}) which would mirror the unwarped plane")
    return [[float(px), float(py)] for px, py in pts]


def normalize_quad_winding(quad: Sequence[Sequence[float]]) -> List[List[float]]:
    """顶点顺序自动纠正为顺时针(TL,TR,BR,BL),返回新列表。

    手绘多边形(GUI 画布/CLI 手输)不保证旋向;逆时针 quad 会被
    :func:`validate_quad` 拒绝并使展开图镜像——集成路径(主流水线/项目
    CLI)对用户输入先做此处纠正,鞋带面积为负时反转顶点序,再交给
    ``validate_quad`` 做结构/面积/凸性校验。
    """
    import numpy as np

    pts = [[float(p[0]), float(p[1])] for p in quad]
    arr = np.asarray(pts, dtype=np.float64).reshape(-1, 2)
    if arr.shape[0] != 4:
        return pts
    x, y = arr[:, 0], arr[:, 1]
    signed2 = float(np.sum(x * np.roll(y, -1) - np.roll(x, -1) * y))
    if signed2 < 0:
        pts.reverse()
    return pts


# ---------------------------------------------------------------------------
# 配置
# ---------------------------------------------------------------------------

def build_config(config_data: Optional[Dict[str, Any]]):
    """dict → (MotionAssConfig, SceneTextPolicyConfig);未知键直接报错(防拼写
    错误静默失效)。

    ``policy_`` 前缀的键路由到 ``SceneTextPolicyConfig`` 对应字段(设计 §5,
    如 ``policy_bg_max_std``),``scene_text_policy`` 键对应所选模式,其余键
    对齐 ``MotionAssConfig`` 字段。
    """
    from core.motion_ass import MotionAssConfig
    from core.scene_text_policy import SceneTextPolicyConfig

    motion_cfg = MotionAssConfig()
    policy_cfg = SceneTextPolicyConfig()
    if not config_data:
        return motion_cfg, policy_cfg
    motion_known = {f.name for f in dataclasses.fields(MotionAssConfig)}
    policy_known = {f"policy_{f.name}": f.name
                    for f in dataclasses.fields(SceneTextPolicyConfig)
                    if f.name != "mode"}
    motion_kw: Dict[str, Any] = {}
    policy_kw: Dict[str, Any] = {}
    unknown = []
    for key, value in dict(config_data).items():
        key = str(key)
        if key in motion_known:
            motion_kw[key] = value
        elif key in policy_known:
            policy_kw[policy_known[key]] = value
        elif key == "scene_text_policy":
            policy_cfg.mode = str(value)
        else:
            unknown.append(key)
    if unknown:
        raise ValueError(
            f"unknown MotionAssConfig field(s): {', '.join(sorted(unknown))}; "
            f"valid fields: "
            f"{', '.join(sorted(motion_known | set(policy_known) | {'scene_text_policy'}))}")
    if motion_kw:
        motion_cfg = MotionAssConfig(**motion_kw)
    if policy_kw:
        policy_cfg = dataclasses.replace(policy_cfg, **policy_kw)
    return motion_cfg, policy_cfg


# ---------------------------------------------------------------------------
# 视频读取与统一坐标展开
# ---------------------------------------------------------------------------

def _video_size(video_path: str) -> Tuple[int, int]:
    """视频分辨率 (width, height),打不开或读到 0 时抛 RuntimeError。"""
    import cv2

    cap = cv2.VideoCapture(str(video_path))
    if not cap.isOpened():
        raise RuntimeError(f"Cannot open video: {video_path}")
    try:
        width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    finally:
        cap.release()
    if width <= 0 or height <= 0:
        raise RuntimeError(f"cannot read video resolution: {video_path}")
    return width, height


def _read_keyframe_frames(
    video_path: str, frame_nums: Sequence[int],
) -> Dict[int, Any]:
    """顺序解码一遍取关键帧图像(不随机 seek,与 keyframe_selector 一致)。"""
    import cv2

    want = {int(f) for f in frame_nums}
    got: Dict[int, Any] = {}
    cap = cv2.VideoCapture(str(video_path))
    if not cap.isOpened():
        raise RuntimeError(f"Cannot open video: {video_path}")
    try:
        idx = 0
        while len(got) < len(want):
            ok, frame = cap.read()
            if not ok or frame is None:
                break
            if idx in want:
                got[idx] = frame
            idx += 1
    finally:
        cap.release()
    missing = sorted(want - set(got))
    if missing:
        raise RuntimeError(f"cannot decode keyframe(s) from video: {missing}")
    return got


def _unwarp_quad_window(
    frame_bgr: Any,
    homography_inv: Sequence[Sequence[float]],
    plane_size: Tuple[int, int],
    origin: Tuple[int, int],
) -> Any:
    """关键帧 → 统一坐标(初始帧平面坐标)展开,并裁出 quad 外接矩形窗口。

    unwarp_canonical 的输出像素 (i, j) 即平面点 (i, j);先按 (qx1+pw,
    qy1+ph) 展开再裁出左上角 (qx1, qy1) 起、plane_size(由 init quad 边长
    推导,与 keyframe_selector 缺省逻辑一致)的窗口,故窗口像素 (u, v) =
    平面点 (qx1+u, qy1+v)。
    """
    from core.scene_plane_tracker import unwarp_canonical

    qx1, qy1 = int(origin[0]), int(origin[1])
    pw, ph = int(plane_size[0]), int(plane_size[1])
    full = unwarp_canonical(frame_bgr, homography_inv, (qx1 + pw, qy1 + ph))
    return full[qy1:qy1 + ph, qx1:qx1 + pw]


# ---------------------------------------------------------------------------
# OCR 融合结果 → 行框
# ---------------------------------------------------------------------------

def _fused_line_rows(best_ocr_data: Dict[str, Any]) -> List[Tuple[str, Tuple[float, float, float, float]]]:
    """融合 OCR dict → [(text, (x1, y1, x2, y2))] 行框列表(展开窗口像素坐标)。

    行框优先取 ``rec_polys`` 的轴对齐外接框(浮点精度),缺失时回退
    ``rec_boxes``([x1, y1, x2, y2]);两者都缺的行丢弃(无几何不成轨迹)。
    与 ``motion_ass.build_line_tracks`` 的 (x1, y1, x2, y2) 行框约定对齐。
    """
    from core.ocr_optimizer import _poly_to_aabb

    texts = best_ocr_data.get("rec_texts") or []
    polys = best_ocr_data.get("rec_polys") or []
    rec_boxes = best_ocr_data.get("rec_boxes") or []
    rows: List[Tuple[str, Tuple[float, float, float, float]]] = []
    for i, text in enumerate(texts):
        box: Optional[Tuple[float, float, float, float]] = None
        if i < len(polys):
            box = _poly_to_aabb(polys[i])
        if box is None and i < len(rec_boxes):
            rb = rec_boxes[i]
            try:
                if rb is not None and len(rb) >= 4:
                    box = (float(rb[0]), float(rb[1]), float(rb[2]), float(rb[3]))
            except (TypeError, ValueError):
                box = None
        if box is None:
            print(f"warning: drop OCR line {i} ({text!r}): no line geometry",
                  file=sys.stderr)
            continue
        rows.append((str(text), box))
    return rows


def dedupe_rows(
    rows: List[Tuple[str, Tuple[float, float, float, float]]],
    iou_thresh: float = 0.5,
) -> Tuple[List[Tuple[str, Tuple[float, float, float, float]]], int]:
    """同文本且行框重叠(IoU ≥ 阈值)的融合行去重,保留先出现的行。

    不同关键帧对同一视觉行的近重复读法若未被位置对齐合并,会生成两条
    几乎同位的行轨迹——各自独立判定对齐后一个锚左缘、一个锚右缘,输出
    事件在时间轴上交错跳变。按文本归一 + IoU 合并即可消除。
    返回 (去重后的行, 删除的行数)。
    """
    kept: List[Tuple[str, Tuple[float, float, float, float]]] = []

    def _norm(text: str) -> str:
        return "".join(str(text).split())

    def _iou(a: Tuple[float, float, float, float],
             b: Tuple[float, float, float, float]) -> float:
        ix = min(a[2], b[2]) - max(a[0], b[0])
        iy = min(a[3], b[3]) - max(a[1], b[1])
        if ix <= 0 or iy <= 0:
            return 0.0
        inter = ix * iy
        area_a = (a[2] - a[0]) * (a[3] - a[1])
        area_b = (b[2] - b[0]) * (b[3] - b[1])
        return inter / max(1e-9, area_a + area_b - inter)

    removed = 0
    for text, box in rows:
        dup = False
        for kt, kb in kept:
            if _norm(kt) == _norm(text) and _iou(kb, box) >= iou_thresh:
                dup = True
                break
        if dup:
            removed += 1
            continue
        kept.append((text, box))
    return kept, removed


def _default_ocr_fn(engine_id: Optional[str]) -> Tuple[OcrFn, Any]:
    """用引擎管理器构造独立引擎实例(不占用进程级单例),返回 (ocr_fn, engine)。

    图像 → 统一 OCR dict(rec_texts/rec_scores/rec_boxes/rec_polys/dt_polys),
    与 ``ocr_engine_manager.run_batch_ocr`` 的取数方式一致。

    指定的引擎依赖缺失时(如安装版未带 rapidocr)不直接失败:告警后回退到
    注册表默认可用引擎,保持任务链可用;完全无可用引擎才抛错退出。
    """
    from core.ocr_engine_base import OCREngineRegistry
    from core.ocr_engine_manager import build_standalone_engine

    resolved = engine_id
    if resolved:
        engine_cls = OCREngineRegistry.get(resolved)
        if engine_cls is not None and not engine_cls.is_available():
            fallback = OCREngineRegistry.get_default()
            if fallback and fallback != resolved:
                print(
                    f"warning: OCR engine '{resolved}' is not available "
                    f"(dependency missing?); falling back to '{fallback}'",
                    file=sys.stderr,
                )
                resolved = fallback
    engine = build_standalone_engine(resolved)

    def _predict(img):
        return engine.normalize_result(engine.predict(img))

    return _predict, engine


# ---------------------------------------------------------------------------
# ASS 输出(与主流水线 generator/styling 默认行为一致)
# ---------------------------------------------------------------------------

def build_ass_header(width: int, height: int, title: str) -> str:
    """默认 ASS 头:与 core/subtitle_generator/styling.py ``_get_ass_header``
    的默认样式一致(含 Scene 行,fontsize=height*0.04),PlayRes 取视频分辨率。

    另含 NoteBox 行(external 模式展示框:Scene 同字号、BorderStyle=3、
    半透明黑 BackColour)——所有模式都写上,避免运行时回退到 external 而
    样式缺失。
    """
    fs_body = height * 0.06
    fs_top = height * 0.05
    fs_scene = height * 0.04
    # 各样式共有的颜色/缩放/边框前缀(至 BorderStyle),便于对齐原文件逐行样式
    common = "&H00FFFFFF,&H000000FF,&H00000000,&H00000000,0,0,0,0,100,100,0,0,1"
    # NoteBox:同前缀但 BackColour 换半透明黑、BorderStyle=3(不透明底框)
    note_common = "&H00FFFFFF,&H000000FF,&H00000000,&H80000000&,0,0,0,0,100,100,0,0,3"
    return f"""[Script Info]
Title: {title} - Generated by Subtitle-OCR
ScriptType: v4.00+
WrapStyle: 0
PlayResX: {width}
PlayResY: {height}
ScaledBorderAndShadow: yes
YCbCr Matrix: TV.709

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,思源黑体 CN,{fs_body:.0f},{common},2,1,2,10,10,10,1
Style: CH,思源黑体 CN,{fs_body:.0f},{common},2,1,2,10,10,10,1
Style: JP,源ノ角ゴシック JP,{fs_body:.0f},{common},2,1,2,10,10,10,1
Style: KO,Malgun Gothic,{fs_body:.0f},{common},2,1,2,10,10,10,1
Style: RU,Arial,{fs_body:.0f},{common},2,1,2,10,10,10,1
Style: Top,思源黑体 CN,{fs_top:.0f},{common},2,1,8,10,10,10,1
Style: Scene,思源黑体 CN,{fs_scene:.0f},{common},1,0,5,10,10,10,1
Style: NoteBox,思源黑体 CN,{fs_scene:.0f},{note_common},1,0,2,10,10,10,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""


def _sanitize_ass_body(body: str) -> str:
    """OCR 文本安全化(与 generator._sanitize_ass_body 行为一致):
    CR/LF/TAB 换空格,ASCII 花括号映射为全角(避免被当 override 标签)。"""
    text = str(body or "").replace("\r", " ").replace("\n", " ").replace("\t", " ")
    return text.replace("{", "｛").replace("}", "｝")


def _parse_ass_time(value: str) -> float:
    """ASS ``H:MM:SS.CC`` → 秒(排序用)。"""
    h, m, rest = str(value).split(":")
    return int(h) * 3600 + int(m) * 60 + float(rest)


def write_ass(
    path: str,
    events: List[Dict[str, str]],
    width: int,
    height: int,
    title: str,
) -> int:
    """事件列表 → .ass 文件(UTF-8-sig),按解析 start 时间排序。

    事件行格式(阶段一脚本直写,Name=motion 作为合并豁免标记;事件 dict
    可选 ``layer`` 字段写入 Layer 列,缺省 0,不影响既有输出;可选
    ``comment`` 真值 → 写 ``Comment:`` 行(mask_only 策略的排版参考行,
    播放器不渲染),缺省 ``Dialogue:``):
    ``Dialogue: {layer},{start},{end},{style},{name},0,0,0,,{tags}{body}``
    返回写出的事件数。
    """
    ordered = sorted(events, key=lambda ev: _parse_ass_time(ev["start_time"]))
    entries = []
    for ev in ordered:
        entries.append(
            "{}: {},{},{},{},{},0,0,0,,{}{}".format(
                "Comment" if ev.get("comment") else "Dialogue",
                int(ev.get("layer", 0) or 0),
                ev["start_time"], ev["end_time"], ev["style"],
                ev.get("name", ""), ev.get("tags", ""),
                _sanitize_ass_body(ev.get("body", ""))))
    content = build_ass_header(width, height, title) + "\n".join(entries)
    with open(path, "w", encoding="utf-8-sig") as f:
        f.write(content)
    return len(entries)


# ---------------------------------------------------------------------------
# 端到端管线
# ---------------------------------------------------------------------------

def build_motion_events(
    video_path: str,
    quad: Sequence[Sequence[float]],
    *,
    start_frame: int = 0,
    end_frame: Optional[int] = None,
    config_data: Optional[Dict[str, Any]] = None,
    ocr_engine: Optional[str] = None,
    keyframe_count: int = 3,
    min_gap_sec: float = 0.33,
    vlm_min_confidence: float = 0.0,
    auto_brightness: bool = False,
    brightness_per_line: Optional[bool] = None,
    occlusion_clip: Optional[bool] = None,
    scene_text_policy: str = "overlap",
    ocr_fn: Optional[OcrFn] = None,
    log: Optional[Callable[[str], None]] = None,
) -> Tuple[List[Dict[str, str]], Dict[str, Any]]:
    """全链路:跟踪 → 关键帧 → OCR → 融合 → 合成事件(± 策略/亮度标签)。

    与 :func:`run_pipeline` 相同的管线,但不写任何文件,返回
    ``(events, summary)`` 供调用方(项目 CLI / 主流水线 worker)把轨迹
    事件合并进自己的 .ass 输出。``quad`` 顶点顺序不保证时先经
    :func:`normalize_quad_winding` 纠正。

    ``ocr_fn``(图像 → 统一 OCR dict)可注入;缺省用 ``ocr_engine`` 指定的
    引擎(未指定时取注册表默认)构造独立实例并在结束时清理。行融合后按
    ``cfg.junk_line_filter``(默认开)用 :func:`core.text_utils.is_noise_text`
    剔除噪声行(纯符号/纯数字/单字非标点——手机状态栏与导航栏图标的
    典型误读),被剔除的行经 ``log`` 留痕。
    ``auto_brightness`` 开启时,合成事件后逐 ok 帧测量文字平面亮度,对每条
    事件追加独立的亮度 override 块(``{原有tags}{\\1c/\\alpha \\t 链}body``,
    不改动既有标签);无 ok 帧 / 曲线退化(基线 ≤ 0 / 全程恒亮)时告警并
    静默跳过。``scene_text_policy`` 为场景文字显示策略(默认 overlap 不改变
    任何输出;mask 生成遮罩+识别文本升 layer 1;mask_only 只出遮罩、识别
    文本写成 Comment 行供排版覆写;mask/mask_only/external/whitespace
    不可用时按 whitespace→mask→external、mask_only→external
    自动回退并经 ``log`` 告警)。``log`` 缺省打到 stderr。
    返回 ``(events, summary)``,summary 含 ok_frames / total_frames /
    keyframes / hard_lines / policy / width / height / lines / plane_size /
    quad_window_origin / ocr_engine。
    """
    if log is None:
        def log(message: str) -> None:
            print(message, file=sys.stderr)

    quad = normalize_quad_winding(quad)
    quad = validate_quad(quad)
    cfg, policy_cfg = build_config(config_data)
    if brightness_per_line is not None:  # CLI 显式开关覆盖 config
        cfg.brightness_per_line = bool(brightness_per_line)
    if occlusion_clip is not None:
        cfg.occlusion_clip = bool(occlusion_clip)
    from core.keyframe_selector import _plane_size_from_quad, select_keyframes
    from core.motion_ass import (
        build_line_tracks,
        smooth_line_track,
        synthesize_events,
    )
    from core.ocr_optimizer import _line_aabbs, fuse_samples_by_position
    from core.scene_plane_tracker import track_plane
    from core.scene_text_policy import POLICY_MODES, SceneTextPolicyConfig

    # 模式走 CLI 参数;replace 触发 SceneTextPolicyConfig.__post_init__ 校验
    policy_cfg = dataclasses.replace(
        policy_cfg, mode=str(scene_text_policy) if scene_text_policy
        else SceneTextPolicyConfig().mode)
    if policy_cfg.mode not in POLICY_MODES:
        raise ValueError(
            f"unknown scene text policy mode: {policy_cfg.mode!r}; "
            f"valid modes: {', '.join(POLICY_MODES)}")

    # 1. 逐帧平面跟踪(全范围;lost 帧无 quad/单应,不外推)
    end_desc = str(end_frame) if end_frame is not None else "end"
    log(f"[1/5] tracking plane on frames [{start_frame}, {end_desc}] ...")
    tracks = track_plane(
        video_path, quad, start_frame=start_frame, end_frame=end_frame)
    ok_tracks = [t for t in tracks if t.status == "ok"]
    if not ok_tracks:
        raise RuntimeError(
            "plane tracking produced no ok frames; cannot build motion subtitles")
    log(f"      {len(ok_tracks)}/{len(tracks)} frames ok")

    # 2. 清晰关键帧(展开图 Laplacian 方差;top-K,分数降序,首个为锚定帧)。
    #    plane_size 由 init quad 边长推导,与 keyframe_selector 缺省逻辑一致
    #    (显式传入,避免依赖 ok_tracks[0].quad 的隐式推导)。
    #    实际取 max(keyframe_count, _KEYFRAME_POOL) 的候选池,按清晰度降序
    #    分批 OCR:最清晰的帧可能恰好落在文字出现之前(聊天界面文字逐条
    #    浮现、信纸逐行显影的空白引导段),该批识别不出文字行时换下一批,
    #    直到取到文字或候选池耗尽。
    plane_size = _plane_size_from_quad(quad)
    keyframe_pool = select_keyframes(
        video_path, tracks, k=max(max(1, int(keyframe_count)), _KEYFRAME_POOL),
        min_gap_sec=max(0.0, float(min_gap_sec)), plane_size=plane_size)
    if not keyframe_pool:  # 防御:有 ok 帧则必非空
        raise RuntimeError("no keyframes selected from tracking result")
    log(f"[2/5] keyframe pool (sharpest first): {keyframe_pool}")

    # 3. 关键帧 OCR(统一坐标 = 初始帧平面坐标;窗口锚定 quad 外接矩形)
    xs = [float(p[0]) for p in quad]
    ys = [float(p[1]) for p in quad]
    origin = (int(min(xs)), int(min(ys)))

    owned_engine = None
    if ocr_fn is None:
        ocr_fn, owned_engine = _default_ocr_fn(ocr_engine)
    by_frame = {t.frame_num: t for t in ok_tracks}
    frames: Dict[int, Any] = {}

    def _ocr_batch(batch: List[int]) -> List[Tuple[int, Dict[str, Any]]]:
        results: List[Tuple[int, Dict[str, Any]]] = []
        for frame_num in batch:  # 顺序 = 清晰度降序,首个为锚定帧
            track = by_frame[frame_num]
            window = _unwarp_quad_window(
                frames[frame_num], track.homography_inv, plane_size, origin)
            ocr_data = ocr_fn(window)
            if not isinstance(ocr_data, dict):
                raise RuntimeError(
                    f"ocr_fn must return a unified OCR dict, got "
                    f"{type(ocr_data).__name__}")
            results.append((frame_num, ocr_data))
        return results

    try:
        step = max(1, int(keyframe_count))
        # 候选池一次性顺序解码:分批循环只查内存,选定批之后的遮挡/策略
        # 也不再重复解码(≤12 帧 1080p 约 75MB)。池级解码失败(容器帧数
        # 虚标、尾部帧坏)不放弃整条链:留痕后退回按批补解,单批仍失败
        # 则换下一批,保住分批兜底的初衷。
        try:
            frames.update(_read_keyframe_frames(video_path, keyframe_pool))
        except RuntimeError as exc:
            log(f"warning: keyframe pool decode failed ({exc}); "
                "falling back to per-batch decode")
        sample_results: List[Tuple[int, Dict[str, Any]]] = []
        for i in range(0, len(keyframe_pool), step):
            batch = keyframe_pool[i:i + step]
            missing = [f for f in batch if f not in frames]
            if missing:
                try:
                    frames.update(_read_keyframe_frames(video_path, missing))
                except RuntimeError as exc:
                    log(f"warning: keyframe batch {batch} decode failed "
                        f"({exc}); trying next batch")
                    continue
            batch_results = _ocr_batch(batch)
            if any(res[1].get("rec_texts") for res in batch_results):
                sample_results = batch_results
                if i:
                    log(f"      sharpest keyframes had no text; using batch "
                        f"{batch}")
                break
        if not sample_results:
            raise RuntimeError(
                f"no text lines recognized on any of {len(keyframe_pool)} "
                "candidate keyframes")
    finally:
        if owned_engine is not None:
            try:
                owned_engine.cleanup()
            except Exception:  # 引擎清理失败不影响主流程
                pass

    # 选定批(有文字的首批)的锚定帧 = 批内最清晰帧;帧与轨迹均已在手
    # (池级帧缓存 / by_frame),后续遮挡/策略/汇总直接复用。
    anchor_frame = int(sample_results[0][0])
    chosen_keyframes = [int(f) for f, _ in sample_results]

    anchor_aabbs = _line_aabbs(sample_results[0][1])
    if anchor_aabbs is None:
        raise RuntimeError(
            "anchor keyframe OCR result has no usable line geometry (dt_polys)")

    # 4. 按位置投票融合(锚定 = 最清晰关键帧;阶段一不接 VLM,宽松阈值)
    best_ocr_data, hard_lines, _candidates = fuse_samples_by_position(
        sample_results, anchor_aabbs,
        vlm_refine_min_confidence=max(0.0, float(vlm_min_confidence)))
    if hard_lines:
        log(f"      {len(hard_lines)} hard line(s) (stage-1: no VLM refine): "
            f"{hard_lines}")

    rows = _fused_line_rows(best_ocr_data)
    if cfg.junk_line_filter:
        # 与静态路径同一噪声判据:剔除图标/状态栏误读(<、>、000、单字象形)。
        from core.text_utils import is_noise_text

        kept_rows = [row for row in rows if not is_noise_text(row[0])]
        dropped = [row[0] for row in rows if is_noise_text(row[0])]
        if dropped:
            log(f"      junk filter: dropped {len(dropped)} noise line(s): {dropped}")
        rows = kept_rows
    rows, n_dup = dedupe_rows(rows)
    if n_dup:
        log(f"      dedupe: merged {n_dup} near-duplicate line(s)")
    if not rows:
        raise RuntimeError("no text lines recognized on any keyframe")
    # 窗口像素坐标 + 外接矩形偏移 = 初始帧平面坐标(常量平移,见模块 docstring)
    qx1, qy1 = origin
    line_boxes = [
        (x1 + qx1, y1 + qy1, x2 + qx1, y2 + qy1) for _t, (x1, y1, x2, y2) in rows
    ]
    texts = [text for text, _box in rows]
    log(f"[3/5] fused OCR lines: {texts}")

    # 5. 行轨迹 → 屏幕空间实测校正(默认开;模板匹配把轨迹锚回真实屏幕
    #    位置,静止行吸附为常量位姿)→ 平滑(显式调用)→ 合成事件 → 写 .ass
    line_tracks = build_line_tracks(
        line_boxes, texts, tracks, ref_frame=int(start_frame))
    if cfg.verify_screen_pose:
        from core.pose_verify import VerifyConfig, verify_line_tracks

        vcfg = VerifyConfig(
            sample_max=cfg.verify_sample_max,
            search_radius_px=cfg.verify_search_radius_px,
            min_score=cfg.verify_min_score,
            drop_score=cfg.verify_drop_score,
            static_tol_px=cfg.verify_static_tol_px,
        )
        try:
            line_tracks, reports = verify_line_tracks(
                video_path, tracks, line_tracks, vcfg, log=log)
            n_static = sum(1 for r in reports.values() if r.static)
            n_corrected = sum(1 for r in reports.values()
                              if r.corrected and not r.static)
            log(f"      pose-verify: {n_static} static, {n_corrected} "
                f"corrected, {len(reports)} line(s) checked")
        except RuntimeError as exc:
            log(f"warning: pose verification failed ({exc}); "
                "keeping homography-derived tracks")
    for line_track in line_tracks:
        smooth_line_track(line_track, window=cfg.smooth_window)
    width, height = _video_size(video_path)
    # 逐行对齐判定留痕(行 idx → left/center/right + 三边票数 + 剪切斜率):
    # quad 展开平面坐标排查「为什么判成 left/center」时对照 OCR 行框看。
    align_diag: Dict[str, object] = {}
    events = synthesize_events(line_tracks, tracks, cfg, style="Scene",
                               video_height=height, diagnostics=align_diag)
    log(f"      line alignments: shear_slope={align_diag.get('shear_slope')} "
        f"{[(d.get('row'), d.get('align')) for d in align_diag.get('rows') or []]}")

    # 5.4 \iclip 手部遮挡蒙版(可选;策略回放前追加,whitespace/external
    #     重建事件时自然丢弃,mask 的文本事件标签原样保留):展开图 vs 锚定
    #     帧灰度差 → 遮挡多边形 → 事件跨度内与行框相交者以 \iclip(± \t
    #     动画)裁掉,字幕不再渲染到手上。
    if cfg.occlusion_clip:
        import cv2 as _cv2

        from core.occlusion_mask import (
            OcclusionConfig,
            attach_occlusion_clips,
            collect_occlusions,
        )

        anchor_window = _unwarp_quad_window(
            frames[anchor_frame], by_frame[anchor_frame].homography_inv,
            plane_size, origin)
        occl_cfg = OcclusionConfig(
            diff_tol=cfg.occlusion_diff_tol,
            min_area_px=cfg.occlusion_min_area_px,
            min_line_overlap=cfg.occlusion_min_line_overlap,
            max_coverage=cfg.occlusion_max_coverage,
            sample_max_frames=cfg.occlusion_sample_max_frames)
        anchor_gray = _cv2.cvtColor(anchor_window, _cv2.COLOR_BGR2GRAY)
        occlusions = collect_occlusions(
            video_path, tracks, anchor_gray=anchor_gray,
            plane_size=plane_size, origin=origin, cfg=occl_cfg, log=log)
        events = attach_occlusion_clips(
            events, tracks=tracks, line_tracks=line_tracks,
            occlusions=occlusions, cfg=occl_cfg)

    # 5.5 场景文字显示策略(默认 overlap:原样返回,输出与既有版本逐事件一致)。
    #     需要锚定关键帧(最清晰帧)的统一坐标展开图与平面坐标行框——均为
    #     run_pipeline 内已有数据:展开窗口尺寸取全平面(原点 (0,0))。
    applied_policy = policy_cfg.mode
    if applied_policy != "overlap":
        from core.scene_text_policy import apply_policy

        plane_img = _unwarp_quad_window(
            frames[anchor_frame], by_frame[anchor_frame].homography_inv,
            (origin[0] + plane_size[0], origin[1] + plane_size[1]), (0, 0))
        events, applied_policy, policy_notes = apply_policy(
            events, list(zip(texts, line_boxes)), plane_img, tracks,
            policy_cfg, width, height, motion_cfg=cfg,
            analysis_box=(int(qx1), int(qy1),
                          int(qx1 + plane_size[0]), int(qy1 + plane_size[1])))
        for note in policy_notes:
            log(f"warning: scene-text-policy: {note}")
        log(f"      scene-text-policy: {policy_cfg.mode} -> {applied_policy}")

    # 5.6 屏幕亮度自适应(可选):测亮度曲线 → DP 简化 → 每条事件追加
    #     独立的亮度 override 块({原有tags}{亮度标签}body),不改既有标签。
    #     遮罩事件(带 base_color)按采样色做通道缩放、不用 \alpha,与字幕
    #     一起忠实调暗。
    #     brightness_per_line(屏幕局部调暗的背景适配):逐行独立测亮度曲线,
    #     事件经 line_idx 取所属行的曲线;整平面曲线仍作为缺省(行曲线缺失、
    #     遮罩事件)与 whitespace/external(文本被重新布局,行归属失效)回退。
    if auto_brightness:
        from core.motion_ass import brightness_tag_chain, simplify_luma_curve
        from core.screen_luma import (
            measure_line_luma_curves,
            measure_luma_curve_with_baseline,
        )

        curve, baseline_luma = measure_luma_curve_with_baseline(
            video_path, tracks,
            baseline_percentile=cfg.brightness_baseline_percentile)
        if not curve:
            log("      brightness: no ok-frame luma samples; skip")
        elif baseline_luma <= 0.0:
            log("      brightness: degenerate baseline (all-black plane); skip")
        else:
            simplified = simplify_luma_curve(
                curve, tol_luma=cfg.brightness_tol, baseline_luma=baseline_luma)
            per_line = None
            if cfg.brightness_per_line:
                if applied_policy in ("whitespace", "external"):
                    log("      brightness: per-line skipped (policy "
                        f"{applied_policy} repositions text; using plane curve)")
                else:
                    raw_curves, line_baselines = measure_line_luma_curves(
                        video_path, tracks, line_boxes,
                        ref_frame=int(start_frame),
                        baseline_percentile=cfg.brightness_baseline_percentile)
                    per_line = [
                        simplify_luma_curve(
                            c, tol_luma=cfg.brightness_tol, baseline_luma=bl)
                        if c and bl > 0 else []
                        for c, bl in zip(raw_curves, line_baselines)
                    ]
                    log("      brightness: per-line curves for "
                        f"{sum(1 for c in per_line if c)}/{len(per_line)} line(s)")
            n_tagged = 0
            for ev in events:
                base_color = ev.pop("base_color", None)
                ev_curve = simplified
                if per_line:
                    li = ev.get("line_idx")
                    if (isinstance(li, int) and 0 <= li < len(per_line)
                            and per_line[li]):
                        ev_curve = per_line[li]
                chain = brightness_tag_chain(
                    ev_curve,
                    _parse_ass_time(ev["start_time"]),
                    _parse_ass_time(ev["end_time"]),
                    use_color=cfg.brightness_use_color,
                    use_alpha=cfg.brightness_use_alpha
                    and base_color is None,
                    base_color=base_color,
                )
                if chain:
                    ev["tags"] = f"{ev['tags']}{{{chain}}}"
                    n_tagged += 1
            log(f"      brightness: baseline {baseline_luma:.1f}, "
                f"{len(simplified)} keyframe(s), "
                f"tagged {n_tagged}/{len(events)} event(s)")

    return events, {
        "ok_frames": len(ok_tracks),
        "total_frames": len(tracks),
        "keyframes": chosen_keyframes,
        "hard_lines": list(hard_lines),
        "policy": applied_policy,
        "lines": len(line_tracks),
        "width": width,
        "height": height,
        "plane_size": [int(plane_size[0]), int(plane_size[1])],
        "quad_window_origin": [int(qx1), int(qy1)],
        "ocr_engine": str(ocr_engine) if ocr_engine else "default",
        # 轨迹导出(run_pipeline --trajectory-json)用;集成调用方无需持久化。
        "tracks": tracks,
    }


def run_pipeline(
    video_path: str,
    out_path: str,
    quad: Sequence[Sequence[float]],
    *,
    start_frame: int = 0,
    end_frame: Optional[int] = None,
    trajectory_json: Optional[str] = None,
    config_data: Optional[Dict[str, Any]] = None,
    ocr_engine: Optional[str] = None,
    keyframe_count: int = 3,
    min_gap_sec: float = 0.33,
    vlm_min_confidence: float = 0.0,
    auto_brightness: bool = False,
    brightness_per_line: Optional[bool] = None,
    occlusion_clip: Optional[bool] = None,
    scene_text_policy: str = "overlap",
    ocr_fn: Optional[OcrFn] = None,
    quiet: bool = False,
) -> Dict[str, Any]:
    """全链路:跟踪 → 关键帧 → OCR → 融合 → 合成 → 写 .ass(± 轨迹 JSON)。

    :func:`build_motion_events` 的文件输出包装(独立 CLI 用);事件构建细节
    见其 docstring。返回摘要 dict(events / ok_frames / total_frames /
    keyframes / hard_lines / policy)。
    """
    def log(message: str) -> None:
        if not quiet:
            print(message, file=sys.stderr)

    events, summary = build_motion_events(
        video_path, quad,
        start_frame=start_frame, end_frame=end_frame,
        config_data=config_data, ocr_engine=ocr_engine,
        keyframe_count=keyframe_count, min_gap_sec=min_gap_sec,
        vlm_min_confidence=vlm_min_confidence,
        auto_brightness=auto_brightness,
        brightness_per_line=brightness_per_line,
        occlusion_clip=occlusion_clip,
        scene_text_policy=scene_text_policy,
        ocr_fn=ocr_fn, log=log)
    title = os.path.splitext(os.path.basename(str(video_path)))[0]
    n_written = write_ass(
        out_path, events, summary["width"], summary["height"], title)
    log(f"[4/5] synthesized {len(events)} event(s) for {summary['lines']} line(s)")
    log(f"[5/5] wrote {n_written} dialogue line(s) -> {out_path}")

    if trajectory_json:
        from core.scene_plane_tracker import save_trajectory

        save_trajectory(
            trajectory_json, summary["tracks"],
            video_path=os.path.abspath(str(video_path)),
            init_quad=quad,
            meta={
                "motion_ass": os.path.abspath(str(out_path)),
                "keyframes": summary["keyframes"],
                "plane_size": summary["plane_size"],
                "quad_window_origin": summary["quad_window_origin"],
                "ocr_engine": summary["ocr_engine"],
            })

    return {
        "events": n_written,
        "ok_frames": summary["ok_frames"],
        "total_frames": summary["total_frames"],
        "keyframes": summary["keyframes"],
        "hard_lines": summary["hard_lines"],
        "policy": summary["policy"],
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main(argv: Optional[Sequence[str]] = None, ocr_fn: Optional[OcrFn] = None) -> int:
    """CLI 入口。``ocr_fn`` 供测试注入 mock(图像 → 统一 OCR dict),注入后
    不构造任何真引擎。返回进程退出码:0 成功,2 输入/管线错误。"""
    parser = argparse.ArgumentParser(
        prog="motion_ass",
        description="Track a hand-picked text plane (quad) frame-by-frame, OCR "
                    "only the sharpest keyframes in unified (initial-frame plane) "
                    "coordinates, fuse readings by position voting, and emit "
                    "motion-trajectory ASS events (\\move segments / \\t / dense "
                    "\\pos fallback).",
    )
    parser.add_argument("--video", required=True, help="input video file")
    parser.add_argument("--out", required=True, help="output .ass file")
    parser.add_argument("--quad-file", metavar="Q.json",
                        help='JSON file {"video": ..., "frame": ..., "quad": [[x,y]x4]} '
                             "(a bare 4x2 list is also accepted)")
    parser.add_argument("--quad", metavar='"x1,y1 x2,y2 x3,y3 x4,y4"',
                        help="four corners as clockwise TL,TR,BR,BL (keyframe coords)")
    parser.add_argument("--start-frame", type=int, default=0,
                        help="first frame to track (default: 0)")
    parser.add_argument("--end-frame", type=int, default=None,
                        help="last frame (inclusive; default: video end)")
    parser.add_argument("--trajectory-json", metavar="OUT.json", default=None,
                        help="also write the per-frame trajectory JSON")
    parser.add_argument("--config-json", metavar="CFG.json", default=None,
                        help="JSON object with core.motion_ass.MotionAssConfig fields")
    parser.add_argument("--ocr-engine", choices=["rapid", "paddle"], default=None,
                        help="OCR engine for keyframe recognition (default: registry default)")
    parser.add_argument("--keyframe-count", type=int, default=3,
                        help="number of sharp keyframes to OCR & vote (default: 3)")
    parser.add_argument("--min-gap-sec", type=float, default=0.33,
                        help="min time gap between chosen keyframes in seconds (default: 0.33)")
    parser.add_argument("--vlm-min-confidence", type=float, default=0.0,
                        help="hard-line confidence threshold; stage-1 runs without VLM "
                             "refine, keep lenient (default: 0.0)")
    parser.add_argument("--auto-brightness", action="store_true",
                        help="measure text-plane brightness per ok frame and append "
                             "\\1c/\\alpha \\t chains so subtitles faithfully follow "
                             "screen dimming/brightening (default: off)")
    parser.add_argument("--brightness-per-line", action="store_true",
                        help="with --auto-brightness: measure each text line's "
                             "brightness separately so subtitles follow partial "
                             "dimming (a darkened top bar leaves bright lines "
                             "bright; default: off)")
    parser.add_argument("--occlusion-clip", action="store_true",
                        help="detect partial hand occlusion (unwarped frame vs "
                             "anchor-keyframe difference) and clip affected events "
                             "with \\iclip(\\t-animated) so subtitles never render "
                             "over the occluder (default: off)")
    parser.add_argument("--scene-text-policy",
                        choices=["overlap", "mask", "mask_only", "external", "whitespace"],
                        default="overlap",
                        help="how to display recognized text over the scene text: "
                             "overlap = on top of the original glyphs (default, "
                             "unchanged output); mask = solid \\p1 patch over the "
                             "original text; mask_only = patch only, recognized "
                             "text written as Comment lines (not rendered) so the "
                             "patch can be re-typeset by hand; external = bottom "
                             "NoteBox outside the region; whitespace = reuse an "
                             "empty band of the plane. mask/mask_only/external/"
                             "whitespace fall back automatically (whitespace -> "
                             "mask -> external, mask_only -> external) when "
                             "unavailable")
    args = parser.parse_args(argv)

    if bool(args.quad) == bool(args.quad_file):
        parser.error("provide exactly one of --quad / --quad-file")

    try:
        quad = validate_quad(
            load_quad_file(args.quad_file) if args.quad_file
            else parse_quad_spec(args.quad))
        config_data = None
        if args.config_json:
            with open(args.config_json, "r", encoding="utf-8") as f:
                config_data = json.load(f)
            if not isinstance(config_data, dict):
                raise ValueError(
                    "--config-json must contain a JSON object of MotionAssConfig fields")
        summary = run_pipeline(
            args.video, args.out, quad,
            start_frame=args.start_frame, end_frame=args.end_frame,
            trajectory_json=args.trajectory_json, config_data=config_data,
            ocr_engine=args.ocr_engine, keyframe_count=args.keyframe_count,
            min_gap_sec=args.min_gap_sec,
            vlm_min_confidence=args.vlm_min_confidence,
            auto_brightness=args.auto_brightness,
            brightness_per_line=args.brightness_per_line or None,
            occlusion_clip=args.occlusion_clip or None,
            scene_text_policy=args.scene_text_policy,
            ocr_fn=ocr_fn,
        )
    except (OSError, ValueError, RuntimeError, KeyError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    policy_desc = "" if summary["policy"] == "overlap" \
        else f", policy {summary['policy']}"
    print(
        f"motion-ass: {summary['events']} event(s) "
        f"({summary['ok_frames']}/{summary['total_frames']} frames ok, "
        f"keyframes {summary['keyframes']}{policy_desc}) -> {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
