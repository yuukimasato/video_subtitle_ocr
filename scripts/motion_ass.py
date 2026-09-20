#!/usr/bin/env python3
# scripts/motion_ass.py
"""移动文字轨迹 → ASS 轨迹字幕:端到端 CLI(阶段一,手动触发)。

串接 core 各模块,完成「视频 + quad → 逐帧平面跟踪 → 清晰关键帧选取 →
关键帧 OCR(统一坐标)→ 按位置投票融合 → 轨迹合成 ASS 事件 → 写 .ass」:

1. ``scene_plane_tracker.track_plane``:逐帧单应跟踪(质量门限,lost 不外推);
2. ``keyframe_selector.select_keyframes``:展开图 Laplacian 方差选 top-K 清晰帧,
   再按 ok 帧时间跨度分桶追加覆盖代表帧(``ensure_coverage``,只扩池不动前缀);
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

按链重锚定(``run_pipeline`` 的文件输出路径):首趟 ok 占比低于
:data:`REANCHOR_MAX_OK_RATIO` 时,用
``scene_plane_tracker.scan_content_windows`` 扫描「quad 内部有内容」的区间,
把「内容窗口内但首趟未 ok」的帧切成至多 ``REANCHOR_MAX_PASSES`` 个、每段至多
``REANCHOR_CHUNK_FRAMES`` 帧的连续段,每段用同一 config / OCR 可调用对象 / log
各跑一趟独立跟踪(自带锚帧与坐标系的单链轨迹),事件按 start 合并写出。覆盖率
达标的素材(门限以上)一行都不多跑,输出与改动前逐字节一致;``tracks`` 仍只是
首趟轨迹(每趟坐标系不同,见 :func:`run_pipeline`)。

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
import time
from typing import Any, Callable, Collection, Dict, List, Optional, Sequence, Set, Tuple

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

OcrFn = Callable[[Any], Dict[str, Any]]

# 关键帧候选池下限:按清晰度降序分批 OCR,最清晰批无文字行时换下一批
# (空白引导段的最清晰帧先于文字出现,见 select_keyframes 调用处注释)。
_KEYFRAME_POOL = 12

# 跟踪覆盖率告警门限:ok 帧占比低于该值、且总帧数达到下限时告警。
# 平面跟踪没有丢锁重捕机制(失败帧只保留上一帧参考状态继续尝试),
# 画面内容大变(聊天文字出现、切镜)后可能永久丢锁。
_TRACK_LOW_COVERAGE = 0.5
_TRACK_SPAN_MIN_FRAMES = 24

# ── 按链重锚定(chunked re-anchoring):首趟覆盖率过低时按内容窗口补跑 ──────
# 实测(一周的朋友 NCOP(虹のかけら) 4K,3840x2160,801 帧,23.976fps,quad
# 1000,1990 2900,1990 2900,2145 1000,2145,歌词条固定屏幕位置、背景水彩在动):
# - 内容窗口(quad 内部特征数 ≥ _INIT_ROI_MIN_FEATURES(192)的连续区间)为
#   230-396、414-556、579-718、721-800,合计 530/801 = 66.2%——这是 ok 覆盖率
#   上限。窗口判据沿用扫描器缺省的 192(不覆盖):它是**逐行歌词**的判据——一行
#   歌词淡出时帧 719-720 的内部计数降到 111/105,192 恰好在两行之间把窗口切开
#   (579-718 一行、721-800 一行);阈值降到 48 会把两行并成一个 579-800 窗口,
#   窗口内跨行变字,而按 120 帧上限切出来的段又会正跨在行变更上,那段的关键帧
#   OCR 只读得到变更前那行(实测 699-800 段的融合行只有 ゆっくり… 与 君との印し,
#   また増やしてゆこう 从未进入融合集)——窗口与行一一对应是这套机制要的形状;
# - 单趟跟踪(锚帧 230)只覆盖 230-396 与 471-558,ok 255/801 = 31.8%,丢锁原因
#   few_matches=264 / pre_anchor=230 / low_inlier=40 / non_convex=9 / area_jump=3;
# - 414-556 窗口内只有 471-556 是首趟 ok(86/143 = 60.1% 覆盖),所以窗口**尾部**
#   19.64-23.19s 会沿用首趟那条已过期的行(明日もこうして / 君のそばにいて),
#   而屏幕上此时是 同じ時を過ごしていたいな —— 挂错字比留窟窿更糟;
# - 579-718 与 721-800 窗口 0% 覆盖(分别对应 ゆっくりページをめくるように 与
#   また増やしてゆこう 君との印し 两行歌词,约帧 585 / 750 起)。
# 成因:歌词逐行出现/消失,行间空白处丢锁,下一行与上一行无共同特征,单链
# 轨迹接不上。故按**内容窗口**为单位重锚定:窗口覆盖率 < 0.8 的整窗重跑
# (不是只补窗口内没 ok 的那几帧——那只会在窗口尾部留下上一行的错字),每趟
# 自带锚帧(h_total = I)、坐标系自洽,与 build_line_tracks 的单 ref_frame 约定
# 不冲突;替换时把更早各趟落在该窗口时间跨度内的事件删掉再并入新事件,故不需要
# 去重(见 run_pipeline 的 replaced_windows)。
# 门限取值:只在首趟 ok 占比低于 REANCHOR_MAX_OK_RATIO 时才付出「扫描一遍 +
# 补跑若干趟」的代价(11.mp4 1.0、DMG 0.707、12.mp4 0.924 都远在其上,零回归
# 路径一行代码都不多跑);窗口覆盖率达标(≥ REANCHOR_WINDOW_COVERED_RATIO)的
# 窗口完全不碰(4K 的 230-396 窗口覆盖 100%,其事件原样保留);窗口内再按
# REANCHOR_CHUNK_FRAMES 切段(实测 414-556=143 帧、579-718=140 帧、
# 721-800=80 帧各自独立跑均 100% ok,120 帧的段足以让关键帧池与姿态校正工作);
# 短于 REANCHOR_MIN_CHUNK_FRAMES 的段不值得一趟跟踪(锚帧 + 关键帧池都撑不起来);
# 补跑趟数硬上限 REANCHOR_MAX_PASSES —— 4K 形态实测需要 5 趟:414-556 窗口
# 2 段(414-533、534-556)+ 579-718 窗口 2 段(579-698、699-718)+ 721-800 窗口
# 1 段(721-800),取 6 留一个窗口的余量(再出现第五个内容窗口时整窗替换而不是
# 被预算截断);超出上限时按窗口序/帧序保留最早的若干段,未替换/只替换了前缀的
# 窗口经 log 告警留痕(不会静默丢字幕)。
REANCHOR_MAX_OK_RATIO = 0.6        # only re-anchor when the first pass covers less than this
REANCHOR_WINDOW_COVERED_RATIO = 0.8  #窗口覆盖率 ≥ 该值即认为已覆盖,整窗不替换
REANCHOR_MAX_PASSES = 6            # hard bound on extra passes (4K 实测需要 5)
REANCHOR_CHUNK_FRAMES = 120        # 窗口按至多这么多帧切段
REANCHOR_MIN_CHUNK_FRAMES = 8      # shorter chunks are skipped


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

def _ocr_dict_line_rows(
    ocr_data: Dict[str, Any],
) -> Tuple[List[Tuple[str, Tuple[float, float, float, float]]],
           List[Tuple[int, str]]]:
    """统一 OCR dict → (行框列表, 无几何被丢弃的行列表)。

    行框优先取 ``rec_polys`` 的轴对齐外接框(浮点精度),缺失时回退
    ``rec_boxes``([x1, y1, x2, y2])。融合行提取(``_fused_line_rows``)与
    frames_seen 回配(``_rows_frames_seen``)必须共用同一提取路径——两处
    行框几何口径一致,回配 IoU 才可比。
    """
    from core.ocr_optimizer import _poly_to_aabb

    texts = ocr_data.get("rec_texts") or []
    polys = ocr_data.get("rec_polys") or []
    rec_boxes = ocr_data.get("rec_boxes") or []
    rows: List[Tuple[str, Tuple[float, float, float, float]]] = []
    dropped: List[Tuple[int, str]] = []
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
            dropped.append((i, str(text)))
            continue
        rows.append((str(text), box))
    return rows, dropped


def _fused_line_rows(best_ocr_data: Dict[str, Any]) -> List[Tuple[str, Tuple[float, float, float, float]]]:
    """融合 OCR dict → [(text, (x1, y1, x2, y2))] 行框列表(展开窗口像素坐标)。

    无几何的行丢弃(无几何不成轨迹)并告警留痕。与
    ``motion_ass.build_line_tracks`` 的 (x1, y1, x2, y2) 行框约定对齐。
    """
    rows, dropped = _ocr_dict_line_rows(best_ocr_data)
    for i, text in dropped:
        print(f"warning: drop OCR line {i} ({text!r}): no line geometry",
              file=sys.stderr)
    return rows


def _norm_text(text: str) -> str:
    """文本归一:去全部空白(dedupe 与 frames_seen 回配共用同一口径)。"""
    return "".join(str(text).split())


def _iou_box(a: Tuple[float, float, float, float],
             b: Tuple[float, float, float, float]) -> float:
    """两个轴对齐行框的 IoU(交并比)。"""
    ix = min(a[2], b[2]) - max(a[0], b[0])
    iy = min(a[3], b[3]) - max(a[1], b[1])
    if ix <= 0 or iy <= 0:
        return 0.0
    inter = ix * iy
    area_a = (a[2] - a[0]) * (a[3] - a[1])
    area_b = (b[2] - b[0]) * (b[3] - b[1])
    return inter / max(1e-9, area_a + area_b - inter)


def _rows_frames_seen(
    rows: List[Tuple[str, Tuple[float, float, float, float]]],
    sample_results: List[Tuple[int, Dict[str, Any]]],
    iou_thresh: float = 0.5,
) -> List[Set[int]]:
    """回配每个融合行的关键帧来源:该行被哪些关键帧读到过。

    ``fuse_samples_by_position`` 内部虽有逐槽观测(含来源 ocr_data),但
    ``best_ocr_data`` 只输出每槽获胜读法的文本与几何,来源帧号在融合边界
    丢失(随输出补齐需改 core/)。这里在脚本侧用现成的 ``sample_results``
    (帧号 + 逐帧 OCR)回配:某帧存在与融合行同文本(空白归一)且行框
    IoU ≥ iou_thresh 的读法,即计入该行的 frames_seen。获胜读法所在帧必然
    精确命中(与融合行共用同一几何提取路径);同实例的跨帧行位漂移 IoU
    略低但通常仍过阈值;不同时刻的重复实例只要框位错开 ≥ 阈值即被排除
    ——这正是 dedupe 区分「同一实例」与「合法重复」的依据。
    """
    rows_norm = [_norm_text(text) for text, _box in rows]
    seen: List[Set[int]] = [set() for _ in rows]
    for frame_num, ocr_data in sample_results:
        frame_rows, _dropped = _ocr_dict_line_rows(ocr_data)
        for text, box in frame_rows:
            norm = _norm_text(text)
            for ri, row_norm in enumerate(rows_norm):
                if row_norm == norm and _iou_box(rows[ri][1], box) >= iou_thresh:
                    seen[ri].add(int(frame_num))
    return seen


_SCORE_TIE_TOL = 0.01  # 清晰度分数「并列」判定容差（相对最大分；只报告不改选择）


def _score_ranking_verdict(scores: Dict[int, float]) -> str:
    """候选帧分数分布 → 「按清晰度」还是「并列退化成按时间纳取」的判定串。

    分数完全/近乎并列（极差 ≤ :data:`_SCORE_TIE_TOL` × 最大分）时，
    ``select_keyframes`` 的排序实为帧号升序，选出来的池就是 min_gap_sec
    时间网格上的前几帧——真实视频上「池全落在空白引导段」的两种成因之一
    （另一种是 ok 帧本身只覆盖那一段，见 ``_describe_keyframe_pool``）。
    """
    values = list(scores.values())
    if not values:
        return "no scored candidate frame"
    lo, hi = min(values), max(values)
    spread_pct = 100.0 * (hi - lo) / hi if hi > 0 else 0.0
    tied = (hi - lo) <= _SCORE_TIE_TOL * hi
    verdict = ("tied (scores within 1% of max) -> pool is a min_gap time grid, "
               "not sharpness-ranked" if tied else "score-ranked")
    return (f"n={len(values)} distinct={len(set(values))} "
            f"spread={lo:.1f}..{hi:.1f} ({spread_pct:.1f}% of max) -> {verdict}")


def _describe_keyframe_pool(
    keyframe_pool: Sequence[int],
    diagnostics: Optional[Dict[str, Any]] = None,
) -> List[str]:
    """``[2/5] keyframe pool`` 的实据行：池内逐帧分数 + 判定 + 时间覆盖情况。

    真实视频上「池为什么只落在这几帧」靠这两三行判断（调用方加 ``      ``
    缩进后打到 stderr）：

    - 分数行：池内逐帧清晰度分数 + 全候选分布与「并列 → 退化成按时间纳取」
      判定（见 :func:`_score_ranking_verdict`）；
    - 覆盖行：池时间跨度 / ok 帧时间跨度（含百分比）+ 桶覆盖数 + 追加的覆盖帧。
      ``池跨度远小于 ok 跨度`` 就是覆盖缺陷——旧版选帧没有任何覆盖保证，
      池可能整段落在空白引导段里（后段明明有文字却没有候选帧）。
    """
    lines: List[str] = []
    diag = diagnostics or {}
    scores = {int(f): float(s) for f, s in (diag.get("scores") or {}).items()}
    times = {int(f): float(t) for f, t in (diag.get("times") or {}).items()}
    pool = [int(f) for f in keyframe_pool]
    if scores:
        lines.append("keyframe sharpness [pool]: " + ", ".join(
            f"{f}:{scores[f]:.1f}" for f in pool if f in scores))
        lines.append("keyframe sharpness [candidates]: "
                     + _score_ranking_verdict(scores))
    pool_times = [times[f] for f in pool if f in times]
    coverage = diag.get("coverage") or {}
    span = coverage.get("span_sec")
    if pool_times:
        line = (f"keyframe coverage: pool t=[{min(pool_times):.2f}, "
                f"{max(pool_times):.2f}]s / ok t="
                + (f"[{float(span[0]):.2f}, {float(span[1]):.2f}]s" if span
                   else "n/a"))
        if span and float(span[1]) > float(span[0]):
            ok_span = float(span[1]) - float(span[0])
            pooled_span = max(pool_times) - min(pool_times)
            line += f" ({100.0 * pooled_span / ok_span:.0f}% of ok span)"
        if coverage.get("enabled"):
            buckets = coverage.get("buckets")
            line += (f"; buckets={buckets} covered="
                     f"{coverage.get('covered_buckets')}/{buckets} appended +"
                     f"{coverage.get('added')}")
        else:
            line += "; coverage off"
        lines.append(line)
    return lines


def _keyframe_pool_frame_gap(keyframe_pool: Sequence[int]) -> int:
    """dedupe 的时间阈值 = 关键帧候选池的时间网格步（帧）：排序后相邻帧号的最大间隔。

    池由 ``select_keyframes`` 的**清晰度前缀**给出（min_gap_sec 贪心时间分散），
    是该批 OCR 证据的最细时间采样网格；最大相邻间隔即「相邻两个池帧最远隔
    多久」。**该网格步与实测到的批间距无关**——``_rows_frames_seen``
    只回配**选中批**的帧（批通常比池稀疏:选中批是池的一段,跨度可能远小于
    全池），而阈值恒取全池网格步，故实际合并阈值偏松于「选中批内相邻帧距」，
    这属**有意的保守合并方向**：阈值偏松只会多合并同实例的近重复槽
    （修掉 \\an4/\\an6 交错跳变），偏紧才会把同一实例拆成两行输出。
    少于 2 帧（单帧批）时为 0 → 仅共享帧来源的行才合并。

    覆盖帧（``ensure_coverage`` 追加的时间覆盖代表帧，每桶一帧、远稀疏于
    min_gap 网格）**不计入**这个网格步：调用方传的是前缀（见调用处），
    避免为「原池一帧文字都没有」才用得上的追加帧改变既有视频的合并阈值
    ——11.mp4/DMG 的接受批完全落在前缀里，其产物必须逐字节不变。
    """

    frames = sorted({int(f) for f in keyframe_pool})
    if len(frames) < 2:
        return 0
    return max(b - a for a, b in zip(frames, frames[1:]))


def dedupe_rows(
    rows: List[Tuple[str, Tuple[float, float, float, float]]],
    iou_thresh: float = 0.5,
    frames_seen: Optional[List[Collection[int]]] = None,
    merge_frame_gap: Optional[int] = None,
    log: Optional[Callable[[str], None]] = None,
) -> Tuple[List[Tuple[str, Tuple[float, float, float, float]]], int]:
    """同文本且行框重叠(IoU ≥ 阈值)的融合行去重,保留先出现的行。

    不同关键帧对同一视觉行的近重复读法若未被位置对齐合并,会生成两条
    几乎同位的行轨迹——各自独立判定对齐后一个锚左缘、一个锚右缘,输出
    事件在时间轴上交错跳变。按文本归一 + IoU 合并即可消除。

    时间/来源维度(``frames_seen`` 与 ``merge_frame_gap`` 同时提供时启用):
    同文本同位的两行还须是「同一视觉实例」才合并——两组 frames_seen 的
    最小交叉帧距 ≤ merge_frame_gap;超出则视为不同时刻的合法重复(副歌
    歌词重现、状态栏文字周期性出现),双份保留并经 ``log`` 留痕,避免
    静默丢文字。阈值依据:取关键帧池的时间网格步(调用方经
    ``_keyframe_pool_frame_gap`` 传入)。清晰度前缀池内两两间隔 ≥
    min_gap_sec,是 OCR 证据的最细采样网格(时间覆盖追加帧每桶一帧、远稀疏
    于该网格,不计入网格步,见 ``_keyframe_pool_frame_gap``)——两组读法的
    最近帧距超过一个
    网格步,说明两帧之间至少隔着一个未读到该行的池帧,而持续在场的同一
    行会被融合阶段(IoU ≥ 0.3 的槽对齐)并进同一槽、根本不会产生两行,
    故判为不同实例;帧距不超过一个网格步则无法排除单次持续在场,仍合并
    (近重复槽通常共享帧来源、交叉帧距为 0,原有跳变修复不回退)。
    **阈值语义的已知松紧**:``merge_frame_gap`` 是**全池网格步**(候选池
    排序后相邻帧号的最大间隔),而 ``frames_seen`` 只回配**选中批**的帧;
    选中批通常比全池稀疏,故实际生效的阈值比「选中批内实测帧距」偏松,
    属**有意的保守合并方向**——偏松只会多合并同实例的近重复槽(修掉
    \\an4/\\an6 交错跳变),偏紧才会把同一实例拆成两行输出。改成按选中批
    计算会让批内相邻帧距(如 370/381/389,距离 19 > 网格步 11)不再合并、
    回退已修复的跳变问题,故不采纳。
    任一行来源缺失(空集)时按旧规则合并:宁可保守保住跳变修复,不扩大
    误删面。旧签名(不传 frames_seen/merge_frame_gap)行为与历史版本一致。
    返回 (去重后的行, 删除的行数)。

    """
    provenance: Optional[List[Set[int]]] = None
    gap_limit = int(merge_frame_gap) if merge_frame_gap is not None else 0
    if (frames_seen is not None and merge_frame_gap is not None
            and len(frames_seen) == len(rows)):
        provenance = [{int(f) for f in seen} for seen in frames_seen]

    def _emit(message: str) -> None:
        if log is not None:
            log(message)

    kept: List[Tuple[str, Tuple[float, float, float, float]]] = []
    kept_seen: List[Set[int]] = []  # 与 kept 平行;合并时并集两行来源
    removed = 0
    for idx, (text, box) in enumerate(rows):
        merge_at: Optional[int] = None  # 首个可合并(同实例/来源缺失)的旧行
        keep_anyway: Optional[Tuple[int, int]] = None  # (旧行, 最小交叉帧距)
        for ki, (kt, kb) in enumerate(kept):
            if (_norm_text(kt) != _norm_text(text)
                    or _iou_box(kb, box) < iou_thresh):
                continue
            if provenance is None:
                merge_at = ki
                break
            seen_old, seen_new = kept_seen[ki], provenance[idx]
            if not seen_old or not seen_new:
                merge_at = ki  # 来源缺失:旧规则兜底(见 docstring)
                break
            gap = min(abs(a - b) for a in seen_old for b in seen_new)
            if gap <= gap_limit:
                merge_at = ki
                break
            if keep_anyway is None:
                keep_anyway = (ki, gap)
        if merge_at is not None:
            removed += 1
            if provenance is not None:
                kept_seen[merge_at] |= provenance[idx]
            continue
        if keep_anyway is not None:
            ki, gap = keep_anyway
            _emit(
                "dedupe: kept time-separated duplicate "
                f"{text!r}: frames {sorted(kept_seen[ki])} vs "
                f"{sorted(provenance[idx])} (min gap {gap} > {gap_limit})")
        kept.append((text, box))
        if provenance is not None:
            kept_seen.append(set(provenance[idx]))
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


class _LazyOcrFn:
    """延迟构造引擎的 OCR 可调用对象:首次调用才加载模型,可由持有者释放。

    ``run_pipeline`` 要在首趟与各重锚定趟之间**复用同一个** OCR 引擎(每趟各
    建一个引擎会把模型重复加载 N 次),但引擎构造时序必须留在「首次 OCR 调用」:
    配置非法 / 视频打不开等前置错误今天不会加载任何模型(见
    tests/test_motion_ass_cli.py 的非法配置用例,``ocr_fn=None`` 且视频不存在),
    把 ``_default_ocr_fn`` 提到 ``run_pipeline`` 入口会改变这一时序与失败路径。
    故这里只在**被调用**时构造,并把引擎的生命周期交回调用方。
    """

    __slots__ = ("_engine_id", "_fn", "_engine")

    def __init__(self, engine_id: Optional[str]) -> None:
        self._engine_id = engine_id
        self._fn: Optional[OcrFn] = None
        self._engine: Any = None

    def __call__(self, image: Any) -> Dict[str, Any]:
        if self._fn is None:
            self._fn, self._engine = _default_ocr_fn(self._engine_id)
        return self._fn(image)

    def release(self) -> None:
        """释放引擎(清理失败不影响主流程,与 ``build_motion_events`` 同规矩)。"""
        engine, self._engine = self._engine, None
        if engine is not None:
            try:
                engine.cleanup()
            except Exception:  # 引擎清理失败不影响主流程
                pass


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

def _batch_texts(batch_results: List[Tuple[int, Dict[str, Any]]]) -> List[str]:
    """批次内所有 ``rec_texts`` 条目(str 化,升序按帧序拼接)。

    类型防御:引擎差异/异常输出可能给 str、None 或不存在的键——``rec_texts``
    是 str 时视作单条文本,非 ``list``/``tuple`` 视作无文本,条目非 str 的
    直接跳过(判据本身绝不因脏数据抛错)。空列表 = 「这批一个文本条目都
    没有」,非空 = 「这批读到的文本条目全是内容(可能是噪声)」——调用方
    据此区分「无文字」与「只有噪声」两类留痕。
    """
    out: List[str] = []
    for _frame, ocr_data in batch_results:
        texts = ocr_data.get("rec_texts")
        if isinstance(texts, str):
            texts = [texts]
        if not isinstance(texts, (list, tuple)):
            continue
        out.extend(str(t) for t in texts if isinstance(t, str))
    return out


def _batch_has_text(batch_results: List[Tuple[int, Dict[str, Any]]]) -> bool:
    """批次是否含**可用**文字行(与 junk filter 等价的行级判据)。

    判据 = ``rec_texts`` 中至少有一条 ``not is_noise_text(text)``
    (:func:`core.text_utils.is_noise_text`,与行融合后的 ``junk_line_filter``
    同一判据)。等价性:选定批进入融合后,``junk_line_filter`` 会把噪声行
    全部剔除;批内没有一个非噪声条目 ⇒ 过滤后行集必为空 ⇒ 该批对下游
    毫无贡献(只会让融合行断言抛错),因此「过滤噪声后仍有非空行」与
    「junk filter 之后仍有行」是同一件事,在更早的位置判定可以跳过整批、
    把机会留给下一批。

    旧判据只看 ``rec_texts`` 列表是否非空,而噪声行(``""``、``"000"``、
    ``"<"`` 等)同样让列表非空——空白引导段/状态栏误读的批次会被当作
    「有文字」选中并 ``break``,随后 junk filter 把行全剔空,在融合行
    断言处抛 :class:`RuntimeError` 而**不再尝试下一批**(12.mp4 的空白
    引导段即此场景)。
    """
    from core.text_utils import is_noise_text

    return any(not is_noise_text(text) for text in _batch_texts(batch_results))


def _ok_chains(ok_tracks: Sequence[Any]) -> List[List[Any]]:
    """把 ok 轨迹按帧号连续性切成「链」(相邻帧号差 1 属同一条链)。

    轨迹管线只在 ok 帧内有位姿:一条链 = 一段连续可跟踪的窗口,链间缺口
    (lost 帧)处位姿断开、文字轨迹分叉。返回按帧号升序的链列表(每条链内
    帧保持原顺序);输入为空时返回空列表。纯诊断计算,不改变 ``ok_tracks``
    本身(内部排序只作用于副本)。
    """
    chains: List[List[Any]] = []
    prev_frame: Optional[int] = None
    for track in sorted(ok_tracks, key=lambda t: int(t.frame_num)):
        frame = int(track.frame_num)
        if prev_frame is None or frame != prev_frame + 1:
            chains.append([])
        chains[-1].append(track)
        prev_frame = frame
    return chains


def _lost_reason_histogram(tracks: Sequence[Any]) -> Dict[str, int]:
    """丢锁原因直方图:{reason: 帧数}。

    ``lost_reason`` 是 ``core.scene_plane_tracker.TrackedQuad`` 的新字段
    (ok 帧恒为 ``None``,lost 帧取质量门限名)。字段可能尚未落地(旧版
    tracker 没有该属性),故一律 ``getattr`` 兜底:缺失或 ``None`` 的原因
    不计入直方图——全部缺失时返回 ``{}``(新旧 tracker 都能工作)。
    零出现的原因此处天然不出现。顺序 = 帧数降序(并列按首次出现),
    便于日志里一眼看出主导原因。
    """
    counts: Dict[str, int] = {}
    first_seen: Dict[str, int] = {}
    for index, track in enumerate(tracks):
        if getattr(track, "status", None) == "ok":
            continue
        reason = getattr(track, "lost_reason", None)
        if reason is None:
            continue
        key = str(reason)
        if key not in counts:
            counts[key] = 0
            first_seen[key] = index
        counts[key] += 1
    return {key: counts[key]
            for key in sorted(counts, key=lambda k: (-counts[k], first_seen[k]))}


def _lost_reason_histogram_text(lost_reasons: Dict[str, int]) -> str:
    """直方图日志文本(``few_matches=1234, high_reproj=8``);空直方图 → none。"""
    if not lost_reasons:
        return "none"
    return ", ".join(
        f"{reason}={count}" for reason, count in lost_reasons.items())


def _ok_chain_window_text(chains: Sequence[Sequence[Any]]) -> str:
    """逐链的 ok 窗口日志文本(``t=0.00–0.25s (3 ok frames)``,``; `` 分隔)。"""
    return "; ".join(
        f"t={chain[0].time_sec:.2f}–{chain[-1].time_sec:.2f}s "
        f"({len(chain)} ok frames)" for chain in chains)


# ---------------------------------------------------------------------------
# 按链重锚定:纯规划 + 事件合并(见 REANCHOR_* 常量注释)
# ---------------------------------------------------------------------------

def should_reanchor(ok_ratio: float) -> bool:
    """首趟 ok 占比是否低到值得按链重锚定(恰好等于门限**不**触发)。

    门限 :data:`REANCHOR_MAX_OK_RATIO`:高于它的素材(11.mp4 1.0、DMG 0.707、
    12.mp4 0.924)一行扫描、一趟补跑都不做,输出与改动前逐字节一致。
    """
    return float(ok_ratio) < REANCHOR_MAX_OK_RATIO


def plan_reanchor_windows(
    ok_frames: Collection[int],
    content_windows: Sequence[Tuple[int, int]],
    *,
    covered_ratio: float = REANCHOR_WINDOW_COVERED_RATIO,
    max_chunks: int = REANCHOR_MAX_PASSES,
    max_chunk_frames: int = REANCHOR_CHUNK_FRAMES,
    min_chunk_frames: int = REANCHOR_MIN_CHUNK_FRAMES,
) -> List[Dict[str, Any]]:
    """内容窗口的**替换决策**(纯函数):覆盖率不足的窗口 → 覆盖整窗的补跑段。

    以内容窗口为单位(而不是「窗口内没 ok 的那几帧」):首趟只在窗口的一部分
    上跟住时,窗口其余部分会沿用上一行的错字(4K 的 414-556 窗口实测 86/143 =
    60.1% 覆盖,尾部 19.64-23.19s 挂着 明日もこうして / 君のそばにいて,而屏幕上
    是 同じ時を過ごしていたいな)——挂错字比留窟窿更糟,所以整窗重跑。

    1. 逐窗口算 ``coverage = 窗口内首趟 ok 帧数 / 窗口帧数``;``>= covered_ratio``
       的窗口完全不碰(4K 的 230-396 窗口 100% 覆盖);
    2. 其余窗口按 ``max_chunk_frames`` 从**窗口首帧**起切段(末段可短),每段是
       一趟独立跟踪的 ``[start_frame, end_frame]``;短于 ``min_chunk_frames`` 的
       段丢掉(撑不起一趟跟踪的锚帧与关键帧池);
    3. 全局段数上限 ``max_chunks``:按窗口序(最早窗口在前)、窗口内按帧序取最早
       的若干段;被上限截掉的窗口 ``chunks`` 为空(调用方据此判「未替换」),
       部分被截的窗口只带留下的前几段。

    返回按窗口序排列的 ``[{"start_frame", "end_frame", "coverage", "chunks"}]``
    (含需要替换但一段都没留下的窗口,``chunks == []``);``chunks`` 内区间均落在
    该窗口内、互不重叠,可直接当 ``build_motion_events`` 的区间。
    """
    ok = {int(frame) for frame in ok_frames}
    cap = max(1, int(max_chunk_frames))
    min_len = max(1, int(min_chunk_frames))
    budget = max(0, int(max_chunks))
    windows: List[Dict[str, Any]] = []
    for window in content_windows:
        first, last = int(window[0]), int(window[1])
        length = last - first + 1
        if length <= 0:
            continue
        covered = sum(1 for frame in range(first, last + 1) if frame in ok)
        coverage = covered / float(length)
        if coverage >= float(covered_ratio):
            continue  # 覆盖达标:整窗不碰
        chunks: List[Tuple[int, int]] = []
        cursor = first
        while cursor <= last:
            end = min(last, cursor + cap - 1)
            if end - cursor + 1 >= min_len:
                chunks.append((cursor, end))
            cursor = end + 1
        windows.append({
            "start_frame": first,
            "end_frame": last,
            "coverage": coverage,
            "chunks": chunks,
        })
    # 全局段数上限:按窗口序/帧序保留最早的若干段;被截到的窗口只留前缀。
    for window in windows:
        chunks = window["chunks"]
        if len(chunks) > budget:
            window["chunks"] = chunks[:budget]
            budget = 0
        else:
            budget -= len(chunks)
    return windows


def plan_reanchor_chunks(
    ok_ratio: float,
    ok_frames: Collection[int],
    content_windows: Sequence[Tuple[int, int]],
    *,
    max_chunks: int = REANCHOR_MAX_PASSES,
    max_chunk_frames: int = REANCHOR_CHUNK_FRAMES,
    min_chunk_frames: int = REANCHOR_MIN_CHUNK_FRAMES,
    covered_ratio: float = REANCHOR_WINDOW_COVERED_RATIO,
) -> List[Tuple[int, int]]:
    """:func:`plan_reanchor_windows` 的扁平投影:补跑段的帧区间(时间升序)。

    含触发判定:``ok_ratio >= REANCHOR_MAX_OK_RATIO`` → ``[]``(覆盖率够,连扫描
    都不该跑,见 :func:`should_reanchor`)。段数上限与切段规则见
    :func:`plan_reanchor_windows`(两者同源,只是这里丢掉窗口归属)。
    """
    if not should_reanchor(ok_ratio):
        return []
    windows = plan_reanchor_windows(
        ok_frames, content_windows, covered_ratio=covered_ratio,
        max_chunks=max_chunks, max_chunk_frames=max_chunk_frames,
        min_chunk_frames=min_chunk_frames)
    return [chunk for window in windows for chunk in window["chunks"]]


def _event_overlaps_span(event: Dict[str, str],
                         span: Tuple[float, float]) -> bool:
    """事件时间跨度与 ``(start_sec, end_sec)`` 是否有正长度交集。

    端点相接(事件恰好在窗口起点结束 / 恰在窗口终点开始)不算重叠——替换段的事件
    正落在窗口端点上,按「相接即重叠」会把刚生成的事件自己判成越界。
    """
    start = _parse_ass_time(event["start_time"])
    end = _parse_ass_time(event["end_time"])
    return start < span[1] and end > span[0]


def drop_events_over_window(
    events: Sequence[Dict[str, str]],
    span: Tuple[float, float],
) -> Tuple[List[Dict[str, str]], int]:
    """删掉时间跨度与 ``span`` 重叠的事件,返回 ``(留下的事件, 删除数)``。"""
    kept = [ev for ev in events if not _event_overlaps_span(ev, span)]
    return kept, len(events) - len(kept)


def surviving_window_overlaps(
    records: Sequence[Tuple[Optional[int], Sequence[Dict[str, str]]]],
    window_index: int,
    span: Tuple[float, float],
) -> List[Dict[str, str]]:
    """替换后仍与窗口跨度重叠、且不属于该窗口替换趟的事件(应为空)。

    ``records`` 是逐趟的 ``(窗口下标 | None, 事件列表)``(None = 首趟);
    本窗口自己的替换趟(index == ``window_index``)天然落在窗口跨度内,不算违规。
    非空即说明删除逻辑没把上一趟的过期事件清干净——只告警,不静默。
    """
    offenders: List[Dict[str, str]] = []
    for index, events in records:
        if index == window_index:
            continue
        offenders.extend(ev for ev in events if _event_overlaps_span(ev, span))
    return offenders


def _window_time_span(
    start_frame: int,
    end_frame: int,
    times: Dict[int, float],
) -> Optional[Tuple[float, float]]:
    """窗口的时间跨度 ``(start_sec, end_sec)``:取首趟轨迹里首/末帧的 ``time_sec``。

    帧→秒不另造映射(容器 POS_MSEC 优先、回退 frame/fps 的口径已在轨迹里),
    越界帧(窗口落在首趟轨迹范围之外)取最近可用帧的时间戳;``times`` 为空
    返回 None(调用方保留原事件)。
    """
    if not times:
        return None
    known = sorted(times)

    def _time_of(frame: int) -> float:
        if frame in times:
            return times[frame]
        nearest = min(known, key=lambda f: (abs(f - frame), f))
        return times[nearest]

    return (_time_of(int(start_frame)), _time_of(int(end_frame)))


def merge_pass_events(
    pass_events: Sequence[Sequence[Dict[str, str]]],
) -> List[Dict[str, str]]:
    """多趟(首趟 + 各重锚定趟)事件合并:拼接后按解析 start 时间稳定排序。

    需要替换的窗口内的事件在调用前已按窗口时间跨度删除
    (:func:`drop_events_over_window`),故只排序、不去重。``sorted`` 稳定:start
    完全相同的并列事件保持「首趟在前、其后按趟序」的原顺序(写盘时
    :func:`write_ass` 用同一 key 再排一次,顺序不变)。
    """
    merged: List[Dict[str, str]] = []
    for events in pass_events:
        merged.extend(events)
    return sorted(merged, key=lambda ev: _parse_ass_time(ev["start_time"]))


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
    quad_window_origin / ocr_engine,以及两个纯报告字段(新增,不影响事件
    几何与任何决策):``lost_reasons``(丢锁原因 → 帧数,零出现的原因不出现,
    旧 tracker 无 ``lost_reason`` 字段时为 ``{}``)、``chains``
    (``{"count": 链数, "windows": [{"start_sec", "end_sec", "ok_frames"}]}``,
    链 = 帧号连续的 ok 帧段)。
    ``tracks`` 是**本趟**的单链轨迹;按链重锚定由 :func:`run_pipeline` 编排,
    补跑趟是各自独立的轨迹(自带锚帧与坐标系的单链,``h_total = I`` 于该锚帧),
    **不**并入 ``tracks``——多趟轨迹混进一条列表会让下游
    ``build_line_tracks`` 的单 ``ref_frame`` 约定失效(所有 ok 帧必须同属一个
    参考平面),要某段的轨迹就按区间单独再跑一趟。
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
    # 轨迹诊断(纯报告,不参与任何下游决策):ok 占比、ok 链(连续可跟踪
    # 窗口;链间缺口 = lost 帧)与丢锁原因直方图。丢失原因此前不可观测——
    # ROI 静默回退静态策略时无法区分是「质量门限卡在哪一条」还是「根本没再
    # 捕获」,只能靠猜;这里把两条信息一次打全。
    ok_ratio = len(ok_tracks) / float(len(tracks)) if tracks else 0.0
    ok_chains = _ok_chains(ok_tracks)
    lost_reasons = _lost_reason_histogram(tracks)
    log(f"      ok ratio {ok_ratio * 100:.1f}%, {len(ok_chains)} ok chain(s): "
        f"{_ok_chain_window_text(ok_chains) or 'none'}")
    log(f"      lost reasons: {_lost_reason_histogram_text(lost_reasons)}")
    # 覆盖率过低时把后果写清楚:轨迹管线只在 ok 帧窗口内有位姿(链在缺口处
    # 断开),窗口外的文字必然没有事件;若该窗口内恰好没有文字,关键帧池必然
    # 取不到文字行,整个 ROI 回退静态策略。此告警由 12.mp4 轨迹 ROI 的失败
    # 促成(修复前 79/1871 ok 且全落在无文字的空白引导段;根因是锚定帧选择,
    # 已修,现为 1729/1871),当前仍在触发的是 4K 歌词条这类「内容逐行出现/
    # 消失」的形态(行间空白处丢锁,接不上后续行)——告警带上主导丢锁原因与
    # 链数,让退化方向自解释,不必再去猜关键帧选择。
    # 触发条件不变:帧数达下限且 ok 占比低于门限。
    if (tracks and len(tracks) >= _TRACK_SPAN_MIN_FRAMES
            and ok_ratio < _TRACK_LOW_COVERAGE):
        log(f"warning: plane tracked on only {ok_ratio * 100:.1f}% of frames "
            f"(ok window t={ok_tracks[0].time_sec:.2f}–"
            f"{ok_tracks[-1].time_sec:.2f}s, {len(ok_chains)} chain(s), "
            f"dominant loss reason {next(iter(lost_reasons), 'unknown')}); "
            "text outside this window cannot be covered by the trajectory "
            "pipeline, and the ROI falls back to the static policy if this "
            "window has no text")

    # 2. 清晰关键帧(展开图 Laplacian 方差;top-K,分数降序,首个为锚定帧)。
    #    plane_size 由 init quad 边长推导,与 keyframe_selector 缺省逻辑一致
    #    (显式传入,避免依赖 ok_tracks[0].quad 的隐式推导)。
    #    实际取 max(keyframe_count, _KEYFRAME_POOL) 的候选池,按清晰度降序
    #    分批 OCR:最清晰的帧可能恰好落在文字出现之前(聊天界面文字逐条
    #    浮现、信纸逐行显影的空白引导段),该批识别不出文字行时换下一批,
    #    直到取到文字或候选池耗尽。「识别不出文字行」= 批内没有任何非噪声
    #    条目(判据与行融合后的 junk filter 等价,见 _batch_has_text):
    #    纯噪声批(状态栏/导航栏误读)同样换下一批并留痕。
    #    ensure_coverage=True 在清晰度前缀之后追加「时间覆盖代表帧」(ok 帧
    #    时间跨度等分成 k 个桶,每个尚无代表帧的桶取桶内最清晰帧;池总长上限
    #    2*k):纯清晰度选取没有任何覆盖保证,池可能整段落在清晰度占优的
    #    空白引导段里,后段有文字却没有候选帧。追加帧只扩池、不动前缀,
    #    因此既有视频(首批即有文字)的接受批与输出逐字节不变;代价是池耗尽
    #    时最坏多 OCR 追加帧数帧(一旦某批命中文字即停止)。
    plane_size = _plane_size_from_quad(quad)
    coverage_diag: Dict[str, Any] = {}
    keyframe_pool = select_keyframes(
        video_path, tracks, k=max(max(1, int(keyframe_count)), _KEYFRAME_POOL),
        min_gap_sec=max(0.0, float(min_gap_sec)), plane_size=plane_size,
        ensure_coverage=True, diagnostics=coverage_diag)
    if not keyframe_pool:  # 防御:有 ok 帧则必非空
        raise RuntimeError("no keyframes selected from tracking result")
    log(f"[2/5] keyframe pool (sharpest first): {keyframe_pool}")
    for line in _describe_keyframe_pool(keyframe_pool, coverage_diag):
        log(f"      {line}")

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
        # 也不再重复解码(池有上界:清晰度 top-K + 覆盖帧,默认 ≤2*k 帧,
        # 1080p 每帧约 6MB)。池级解码失败(容器帧数
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
            # 接受判据 = 「过滤噪声后仍有非空行」(与 junk filter 等价,见
            # _batch_has_text):纯噪声批(空白引导段/状态栏误读)视同无文字,
            # 留痕后换下一批,而不是选中后被 junk filter 剔空并报错退出。
            if _batch_has_text(batch_results):
                sample_results = batch_results
                if i:
                    log(f"      sharpest keyframes had no text; using batch "
                        f"{batch}")
                break
            texts = _batch_texts(batch_results)
            log(f"      keyframe batch {batch} skipped: "
                + (f"noise-only ({texts})" if texts else "no text")
                + "; trying next batch")
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
    # 去重带时间/来源维度:frames_seen 回配每行被哪些关键帧读到,合并阈值
    # 取关键帧池的时间网格步——**网格步 = 池内相邻帧号的最大间隔,与实测
    # 到的批间距无关**(选中批通常比全池稀疏,故实际阈值偏松,属有意的
    # 保守合并方向:偏松只多合并同实例的近重复槽),同一持续实例(帧来源
    # 相邻/交错)仍合并(\an4/\an6 跳变修复不回退),不同时刻的同文本合法
    # 重复双份保留并留痕。语义详见 _keyframe_pool_frame_gap / dedupe_rows。
    # 网格步只取**清晰度前缀**(coverage_diag["prefix"]):覆盖帧每桶一帧、
    # 远稀疏于 min_gap 网格,计入会为「原池一帧文字都没有」才用得上的追加帧
    # 改变既有视频(11.mp4/DMG 接受批全在前缀里)的合并阈值与产物。
    sharpness_pool = coverage_diag.get("prefix") or keyframe_pool
    rows, n_dup = dedupe_rows(
        rows,
        frames_seen=_rows_frames_seen(rows, sample_results),
        merge_frame_gap=_keyframe_pool_frame_gap(sharpness_pool),
        log=lambda message: log(f"      {message}"))
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
    # 视频分辨率提前取一次(校正与合成共用):pose-verify 的像素阈值
    # (static_tol/search_radius/retry_radius)要按 video_height/1080 归一,
    # 合成器要用它做标点补偿上限的归一与 PlayRes;单次读取复用,不重复
    # 开容器(打不开仍按既有语义抛 RuntimeError)。
    width, height = _video_size(video_path)
    if cfg.verify_screen_pose:
        from core.pose_verify import VerifyConfig, verify_line_tracks

        vcfg = VerifyConfig(
            sample_max=cfg.verify_sample_max,
            search_radius_px=cfg.verify_search_radius_px,
            min_score=cfg.verify_min_score,
            drop_score=cfg.verify_drop_score,
            static_tol_px=cfg.verify_static_tol_px,
            min_static_samples=cfg.verify_min_static_samples,
            retry_radius_px=cfg.verify_retry_radius_px,
            long_span_frames=cfg.verify_long_span_frames,
            span_confirm_max=cfg.verify_span_confirm_max,
            block_max_frames=cfg.verify_block_max_frames,
            roi_clamp=cfg.verify_roi_clamp,
        )
        try:
            line_tracks, reports = verify_line_tracks(
                video_path, tracks, line_tracks, vcfg, log=log,
                video_height=height, roi_quad=quad)
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
        # --- 以下为纯报告字段(新增,不影响任何事件几何/.ass 输出/决策)---
        # 丢锁原因直方图(零出现的原因此处不出现;旧 tracker 无该字段时为 {});
        "lost_reasons": lost_reasons,
        # ok 链:连续可跟踪窗口数 + 每链窗口(秒;含 ok 帧数);
        "chains": {
            "count": len(ok_chains),
            "windows": [
                {"start_sec": float(chain[0].time_sec),
                 "end_sec": float(chain[-1].time_sec),
                 "ok_frames": len(chain)}
                for chain in ok_chains
            ],
        },
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
    keyframes / hard_lines / policy + 重锚定报告,见下)。

    首趟 ok 占比低于 :data:`REANCHOR_MAX_OK_RATIO` 时按链重锚定(成因与门限
    取值见 ``REANCHOR_*`` 常量注释):``scan_content_windows`` 按缺省判据
    (``_INIT_ROI_MIN_FEATURES``,逐行歌词的区间)→ 覆盖率低于
    :data:`REANCHOR_WINDOW_COVERED_RATIO` 的窗口**整窗重跑**(只补「窗口内没 ok
    的那几帧」会在窗口尾部留下上一行的错字),按 :data:`REANCHOR_CHUNK_FRAMES`
    切段、至多 :data:`REANCHOR_MAX_PASSES` 段 → 每段用**同一** config / OCR
    可调用对象 / log 再跑一趟 :func:`build_motion_events`。每趟是独立单链轨迹
    (自带锚帧,``h_total = I`` 于该锚帧),窗口的替换段与更早各趟在**该窗口时间
    跨度内**的事件互斥:重锚定先把跨度内更早各趟的事件删掉,再并入替换趟事件
    (``replaced_windows[*].dropped_events``),故合并只需按 start 排序、无需去重。

    - ``passes`` = 逐趟报告 ``[{start_frame, end_frame, ok_frames, events}]``
      (首趟在前,帧区间为实际跑过的范围);``reanchored`` = 是否真的补跑并
      合并了至少一趟;``merged_lines`` = 各趟 ``lines`` 之和(合并后行数,
      新增键);``replaced_windows`` = 被替换的窗口
      ``[{start_frame, end_frame, coverage, dropped_events}]``(窗口时间跨度取
      首趟轨迹首/末帧的 ``time_sec``)。
    - ``tracks`` 仍是**首趟**轨迹:每趟自带坐标系,把多趟轨迹塞进一条列表会
      让下游 ``build_line_tracks`` 的单 ``ref_frame`` 约定失效(所有 ok 帧必须
      同属一个参考平面),故补跑趟的轨迹不并入;需要某段的轨迹时按 ``passes``
      的区间单独重跑。
    - ``ok_frames`` 是各趟 ok 帧的**并集**(替换让同一帧被两趟覆盖时只算一次;
      各趟自身数字见 ``passes``,其和可能大于并集),``events`` 是替换后的合并
      总量;``total_frames`` 仍是首趟区间帧数,``ok_frames / total_frames`` 即
      合并覆盖率。``lines`` 语义不变(首趟行数,合并行数另见 ``merged_lines``)。
    """
    def log(message: str) -> None:
        if not quiet:
            print(message, file=sys.stderr)

    from core.scene_plane_tracker import scan_content_windows

    ocr_holder: Optional[_LazyOcrFn] = None
    if ocr_fn is None:
        # 首趟与各重锚定趟共用一个 OCR 可调用对象(引擎只加载一次);引擎仍在
        # 首次 OCR 调用时才构造,前置失败路径的时序与改动前一致。
        ocr_holder = _LazyOcrFn(ocr_engine)
        ocr_fn = ocr_holder
    try:
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

        first_ok = {int(t.frame_num) for t in summary["tracks"]
                    if getattr(t, "status", None) == "ok"}
        # 逐趟记录:(窗口下标 | None, 事件列表)。None = 首趟,整数 = 该窗口的替换趟。
        # 替换时要按窗口把「更早各趟」的事件删掉,故必须留着来源标记。
        records: List[Tuple[Optional[int], List[Dict[str, str]]]] = [(None, events)]
        pass_lines: List[int] = [int(summary["lines"])]
        passes: List[Dict[str, Any]] = [{
            "start_frame": int(start_frame),
            "end_frame": int(start_frame) + int(summary["total_frames"]) - 1,
            "ok_frames": int(summary["ok_frames"]),
            "events": len(events),
        }]
        pass_ok_frames: List[set] = [first_ok]
        total_frames = int(summary["total_frames"])
        ok_ratio = (int(summary["ok_frames"]) / float(total_frames)
                    if total_frames else 0.0)
        replaced_windows: List[Dict[str, Any]] = []
        # 2. 按链重锚定(仅在首趟覆盖率过低时;扫描也只在触发后才跑)。
        if should_reanchor(ok_ratio):
            content_windows: List[Tuple[int, int]] = []
            scan_t0 = time.monotonic()
            try:
                # 窗口判据用扫描器缺省的 _INIT_ROI_MIN_FEATURES(192,不覆盖):
                # 这是**逐行歌词**的判据——一行淡出时 719-720 帧的内部计数降到
                # 111/105,192 恰好在两行之间切开(579-718 / 721-800,一行一个
                # 窗口);阈值降到 48 会把两行并成一个窗口,而 120 帧上限切出来的
                # 段正跨在行变更上,那段的关键帧 OCR 只读得到变更前那行
                # (见 REANCHOR_* 注释的实测)。
                content_windows = scan_content_windows(
                    video_path, quad, start_frame, end_frame)
            except (OSError, ValueError, RuntimeError) as exc:
                # 扫描失败只是「没找到可补的内容」:不留痕会被误读成「没有内容段」,
                # 但也绝不因此丢掉首趟已产出的字幕。
                log(f"warning: content scan failed ({exc}); skipping re-anchoring")
            scan_sec = time.monotonic() - scan_t0
            planned = plan_reanchor_windows(first_ok, content_windows)
            chunks = [chunk for window in planned for chunk in window["chunks"]]
            replaced: List[Tuple[int, Dict[str, Any]]] = [
                (index, window) for index, window in enumerate(planned)
                if window["chunks"]]
            end_desc = str(end_frame) if end_frame is not None else "end"
            log(f"coverage {ok_ratio * 100:.1f}% < "
                f"{REANCHOR_MAX_OK_RATIO * 100:.0f}%: re-anchoring: replacing "
                f"{len(replaced)} of {len(content_windows)} content window(s) "
                f"{[[w['start_frame'], w['end_frame']] for _i, w in replaced]} "
                f"with {len(chunks)} pass(es) {chunks} "
                f"([{start_frame}, {end_desc}], scan {scan_sec:.1f}s)")
            starved = [window for window in planned if not window["chunks"]]
            if starved:
                # 预算不够或窗口太短:这些窗口保留原来的（可能过期的）事件，
                # 不静默删掉已有字幕。
                log(f"warning: {len(starved)} window(s) not replaced "
                    f"(pass budget / window shorter than "
                    f"{REANCHOR_MIN_CHUNK_FRAMES} frames): "
                    f"{[[w['start_frame'], w['end_frame']] for w in starved]}")
            partial = [window for window in planned if window["chunks"]
                       and window["chunks"][-1][1] < window["end_frame"]]
            if partial:
                # 段数上限把窗口的后半截切掉了:替换趟只覆盖前缀,窗口尾部会留空
                # (比挂错字好,但不是完整替换),留痕。
                log(f"warning: pass budget exhausted: {len(partial)} window(s) "
                    f"only partially replaced (tail stays blank): "
                    f"{[[w['start_frame'], w['end_frame']] for w in partial]}")
            chunk_window: Dict[Tuple[int, int], int] = {
                chunk: index for index, window in replaced
                for chunk in window["chunks"]}
            for chunk_start, chunk_end in chunks:
                try:
                    chunk_events, chunk_summary = build_motion_events(
                        video_path, quad,
                        start_frame=chunk_start, end_frame=chunk_end,
                        config_data=config_data, ocr_engine=ocr_engine,
                        keyframe_count=keyframe_count, min_gap_sec=min_gap_sec,
                        vlm_min_confidence=vlm_min_confidence,
                        auto_brightness=auto_brightness,
                        brightness_per_line=brightness_per_line,
                        occlusion_clip=occlusion_clip,
                        scene_text_policy=scene_text_policy,
                        ocr_fn=ocr_fn, log=log)
                except (OSError, ValueError, RuntimeError, KeyError) as exc:
                    # 重锚定只是补覆盖:某段失败(无 ok 帧、无文字行)不拖垮整条
                    # 链,留痕后继续下一段。
                    log(f"warning: re-anchor pass on frames "
                        f"[{chunk_start}, {chunk_end}] failed ({exc}); skipping")
                    continue
                chunk_ok = {int(t.frame_num) for t in chunk_summary["tracks"]
                            if getattr(t, "status", None) == "ok"}
                # 不变量逐条显式校验并留痕(不抛错:重锚定只是补覆盖,校验失败
                # 也不该拖垮整条链):① 段区间落在它所替换的窗口内(规划保证);
                # ② 本趟 ok 帧落在本趟区间内(越界说明轨迹/坐标系已串趟)。
                window = planned[chunk_window[(chunk_start, chunk_end)]]
                if not (window["start_frame"] <= chunk_start
                        and chunk_end <= window["end_frame"]):
                    log(f"warning: re-anchor pass on frames "
                        f"[{chunk_start}, {chunk_end}] falls outside window "
                        f"[{window['start_frame']}, {window['end_frame']}]")
                covered = set(range(int(chunk_start), int(chunk_end) + 1))
                outside = sorted(chunk_ok - covered)
                if outside:
                    log(f"warning: re-anchor pass on frames "
                        f"[{chunk_start}, {chunk_end}] reports ok frame(s) "
                        f"outside its own range (e.g. {outside[:5]}); "
                        "keeping them out of the merged ok total")
                    chunk_ok &= covered
                records.append((chunk_window[(chunk_start, chunk_end)],
                                chunk_events))
                pass_lines.append(int(chunk_summary["lines"]))
                pass_ok_frames.append(chunk_ok)
                passes.append({
                    "start_frame": int(chunk_start),
                    "end_frame": int(chunk_end),
                    "ok_frames": len(chunk_ok),
                    "events": len(chunk_events),
                })
                log(f"      re-anchor pass on frames "
                    f"[{chunk_start}, {chunk_end}]: {len(chunk_ok)}/"
                    f"{int(chunk_summary['total_frames'])} frame(s) ok, "
                    f"{len(chunk_events)} event(s), "
                    f"{int(chunk_summary['lines'])} line(s)")

            # 3. 替换而不是并存:需要替换的窗口,其时间跨度内更早各趟的事件全部删掉
            #    (窗口尾部挂着的上一行错字比留窟窿更糟),再并入该窗口的替换趟事件。
            #    时间跨度取**首趟轨迹**里窗口首/末帧的 time_sec(不另造帧→秒映射)。
            times = {int(t.frame_num): float(t.time_sec) for t in summary["tracks"]}
            spans: List[Tuple[int, Dict[str, Any], Tuple[float, float]]] = []
            for index, window in replaced:
                span = _window_time_span(
                    window["start_frame"], window["end_frame"], times)
                if span is None:  # 防御:首趟轨迹为空(不可能有事件要删)
                    log(f"warning: no pass-1 timestamp for window "
                        f"[{window['start_frame']}, {window['end_frame']}]; "
                        "keeping its earlier events")
                    continue
                spans.append((index, window, span))
            replaced_windows = []  # 只有真正做了替换(有窗口时间跨度)的窗口才报告
            for index, window, span in spans:
                dropped = 0
                for record_index, record_events in records:
                    if record_index == index:  # 本窗口自己的替换趟
                        continue
                    kept, removed = drop_events_over_window(record_events, span)
                    dropped += removed
                    if removed:
                        record_events[:] = kept
                replaced_windows.append({
                    "start_frame": window["start_frame"],
                    "end_frame": window["end_frame"],
                    "coverage": window["coverage"],
                    "dropped_events": dropped,
                })
                log(f"      replaced window [{window['start_frame']}, "
                    f"{window['end_frame']}] (t={span[0]:.2f}-{span[1]:.2f} s, "
                    f"coverage {window['coverage'] * 100:.0f}%): "
                    f"dropped {dropped} event(s)")
            # 替换后校验:除本窗口替换趟外,不应再有事件落在窗口跨度内(应为 0,
            # 非 0 说明上面的删除漏了——告警留痕,不静默)。
            for index, window, span in spans:
                offenders = surviving_window_overlaps(records, index, span)
                if offenders:
                    log(f"warning: replaced window [{window['start_frame']}, "
                        f"{window['end_frame']}] still has {len(offenders)} "
                        f"event(s) from other passes overlapping its span "
                        f"(e.g. {offenders[:2]})")
            events = merge_pass_events([record_events
                                        for _index, record_events in records])

        merged_lines = sum(pass_lines)
        # ok 帧取各趟的**并集**:替换让同一帧被两趟覆盖时只算一次(各趟自身数字
        # 见 passes,其和会大于并集)。
        merged_ok = len(set().union(*pass_ok_frames)) if pass_ok_frames else 0
        title = os.path.splitext(os.path.basename(str(video_path)))[0]
        n_written = write_ass(
            out_path, events, summary["width"], summary["height"], title)
        log(f"[4/5] synthesized {len(events)} event(s) for "
            f"{merged_lines} line(s)")
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
            # 合并口径:各趟 ok 帧的**并集**(替换让同一帧被两趟覆盖时只算一次),
            # total_frames 仍是首趟区间帧数 → 两者相除即合并覆盖率;首趟自身的
            # 数字在 passes[0] 里,各趟之和可能大于本值。
            "ok_frames": merged_ok,
            "total_frames": summary["total_frames"],
            "keyframes": summary["keyframes"],
            "hard_lines": summary["hard_lines"],
            "policy": summary["policy"],
            # --- 按链重锚定报告(新增键;不触发时 passes 只有首趟、reanchored
            #     False、replaced_windows 为空、merged_lines == lines,输出与
            #     改动前逐字节一致)---
            "passes": passes,
            "reanchored": len(passes) > 1,
            "merged_lines": merged_lines,
            "replaced_windows": replaced_windows,
        }
    finally:
        if ocr_holder is not None:
            # 复用给补跑趟的引擎在所有趟跑完后释放(与改动前「关键帧 OCR 结束
            # 即释放」相比只是延后,不改变任何输出)。
            ocr_holder.release()


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
