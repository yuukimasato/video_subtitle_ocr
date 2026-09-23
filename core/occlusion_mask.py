# core/occlusion_mask.py
"""手部遮挡检测与 ``\\iclip`` 蒙版(FR-9,阶段三)。

跟踪器质量门限兜住「大面积遮挡 → lost → 切段不外推」;本模块处理**部分
遮挡**:手挡住文字平面一角但 ORB 特征仍足够、跟踪保持 ok 的帧——此时
轨迹字幕会整行渲染到手上。做法:

1. :func:`detect_occlusion_polygons` —— 把当前帧按逆单应展开到统一平面
   坐标,与锚定关键帧展开图的灰度差超过 ``diff_tol`` 的像素即「变化区」
   (画面文字静止时,覆盖物 = 手/遮挡物);形态学闭+开清理后取外轮廓,
   简化为平面坐标多边形。
2. :func:`collect_occlusions` —— 顺序解码视频,对每个采样 ok 帧(每
   ``sample_stride_frames`` 个好帧一测,末帧必测)执行检测,返回
   ``{frame_num: [多边形...]}``;任何帧都无多边形时返回空 dict(无遮挡,
   输出零改动)。
3. :func:`attach_occlusion_clips` —— 对每条事件:取其行框(平面坐标)与
   事件跨度内采样帧的遮挡多边形,仅保留与行框相交面积 ≥
   ``min_line_overlap``×行框面积的多边形(手在平面别处不裁);把多边形经
   ``H(init→frame)`` 映射为画面(PlayRes)坐标后追加 ``\\iclip`` 标签块:

   - 跨度内只有一帧有遮挡 → 静态 ``\\iclip``;
   - 首末帧多边形**个数相同**(每个多边形已重采样为等点数)→
     ``\\iclip(首帧形状)`` + ``\\t(0,段长,\\iclip(末帧形状))``——libass
     对等结构 clip 绘图做坐标线性插值,与段内 ``\\move`` 的线性运动假设
     一致,段端点精确;
   - 个数不同(遮挡物出现/消失)→ 全部采样帧多边形的并集静态 ``\\iclip``
     (宁多勿漏:绝不把字渲染到手上,代价是遮挡前后多藏一点)。

锚定帧本身被遮挡的区间(手在关键帧就压着屏幕)会被当作「预期外观」而不
计入变化——此类场景请换锚定帧。时间复用 ``TrackedQuad.time_sec``;本模块
为纯函数(视频 I/O 只在 :func:`collect_occlusions`),不依赖 PySide6。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Dict, List, Optional, Sequence, Tuple

import numpy as np

__all__ = [
    "OcclusionConfig",
    "detect_occlusion_polygons",
    "collect_occlusions",
    "attach_occlusion_clips",
]


@dataclass
class OcclusionConfig:
    """遮挡蒙版参数(建自 ``MotionAssConfig`` 的 ``occlusion_*`` 字段)。"""

    diff_tol: float = 26.0            # 展开图灰度差 > 此值记为变化像素
    min_area_px: float = 400.0        # 变化区最小面积(平面 px),滤噪
    min_line_overlap: float = 0.06    # 与行框相交面积占比下限才计入裁剪
    max_coverage: float = 0.55        # 变化像素覆盖率超过此值视为全局变化
    epsilon_px: float = 2.0           # approxPolyDP 简化容差
    morph_kernel: int = 5             # 闭+开核(奇数)
    sample_stride_frames: int = 3     # 每 N 个好帧检测一次遮挡
    sample_max_frames: int = 7        # 每条事件最多取的遮挡采样帧数
    resample_points: int = 16         # 每个多边形重采样点数(动画插值需等点)
    edge_margin_px: int = 8           # 展开图四边忽略带(单应边界采样不稳定)
    edge_sliver_max_px: float = 24.0  # 贴边条带判定的最大厚度(px)
    edge_sliver_min_frac: float = 0.5  # 条带须覆盖对应边长的比例


# ---------------------------------------------------------------------------
# 检测(单帧)
# ---------------------------------------------------------------------------

def detect_occlusion_polygons(
    frame_bgr: np.ndarray,
    homography_inv: Sequence[Sequence[float]],
    anchor_gray: np.ndarray,
    plane_size: Tuple[int, int],
    origin: Tuple[int, int],
    *,
    diff_tol: float = 26.0,
    min_area_px: float = 400.0,
    max_coverage: float = 0.55,
    epsilon_px: float = 2.0,
    morph_kernel: int = 5,
    edge_margin_px: int = 8,
    edge_sliver_max_px: float = 24.0,
    edge_sliver_min_frac: float = 0.5,
) -> List[np.ndarray]:
    """单帧遮挡检测:展开图 vs 锚定帧灰度差 → 平面坐标多边形列表。

    展开窗口与 ``scripts.motion_ass._unwarp_quad_window`` 同一约定:窗口
    像素 (u, v) = 平面点 (qx1+u, qy1+v),故轮廓点加回 ``origin`` 即平面
    坐标。三处防误报:①当前展开图先按自身灰度中位值相对锚定图归一
    (屏幕整体变暗/变亮时逐像素等比缩放,差分≈0,而局部遮挡不受影响);
    ②形态学清理后变化像素覆盖率 > ``max_coverage`` 视为全局外观变化
    (调暗/切镜)而非遮挡,返回空列表;③四边 ``edge_margin_px`` 忽略带内
    的变化不计(展开窗口边界单应采样不稳定,典型产生贴边的整条窄带
    伪影),且贴合有效边界的细长条带(厚度 ≤ ``edge_sliver_max_px``、
    覆盖 ≥ ``edge_sliver_min_frac``×边长)按采样伪影剔除。展开图无效
    (退化单应抛错由调用方处理)、窗口尺寸与锚定图不符或无显著变化区
    时也返回空列表。
    """
    import cv2

    from core.scene_plane_tracker import unwarp_canonical

    qx1, qy1 = int(origin[0]), int(origin[1])
    pw, ph = int(plane_size[0]), int(plane_size[1])
    if pw < 2 or ph < 2:
        return []
    full = unwarp_canonical(frame_bgr, homography_inv, (qx1 + pw, qy1 + ph))
    window = full[qy1:qy1 + ph, qx1:qx1 + pw]
    gray = cv2.cvtColor(window, cv2.COLOR_BGR2GRAY)
    if gray.shape != anchor_gray.shape:
        return []
    # 亮度归一:整平面明暗变化(背光调节/调暗剧情)不应产生差分
    frame_med = float(np.median(gray))
    anchor_med = float(np.median(anchor_gray))
    if frame_med > 1.0 and anchor_med > 1.0 and abs(frame_med - anchor_med) > 1e-6:
        scaled = np.clip(
            gray.astype(np.float64) * (anchor_med / frame_med), 0.0, 255.0)
        diff = cv2.absdiff(scaled.astype(np.uint8), anchor_gray)
    else:
        diff = cv2.absdiff(gray, anchor_gray)
    changed = (diff > float(diff_tol)).astype(np.uint8)
    k = int(morph_kernel)
    if k >= 3:
        kernel = np.ones((k, k), np.uint8)
        changed = cv2.morphologyEx(changed, cv2.MORPH_CLOSE, kernel)
        changed = cv2.morphologyEx(changed, cv2.MORPH_OPEN, kernel)
    h, w = changed.shape
    m = int(edge_margin_px)
    if m > 0 and w > 2 * m and h > 2 * m:
        # 展开窗口边界由单应外采样/插值产生,与锚定帧的差分是稳定伪影
        # (整圈窄条),不计入遮挡。
        changed[:m, :] = 0
        changed[h - m:, :] = 0
        changed[:, :m] = 0
        changed[:, w - m:] = 0
    if float(changed.mean()) > float(max_coverage):
        return []  # 大半平面都变了:调暗/切镜等全局变化,不是局部遮挡
    contours, _ = cv2.findContours(
        changed, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    polys: List[np.ndarray] = []
    for contour in contours:
        if cv2.contourArea(contour) < float(min_area_px):
            continue
        bx, by, bw, bh = cv2.boundingRect(contour)
        if _is_edge_sliver(bx, by, bw, bh, w, h, m,
                           float(edge_sliver_max_px), float(edge_sliver_min_frac)):
            continue  # 贴边细长条带:展开采样伪影,不是遮挡物
        approx = cv2.approxPolyDP(contour, float(epsilon_px), True)
        pts = approx.reshape(-1, 2).astype(np.float64) + np.array([qx1, qy1])
        if len(pts) >= 3:
            polys.append(pts)
    return polys


def _is_edge_sliver(
    bx: int, by: int, bw: int, bh: int,
    w: int, h: int, margin: int,
    max_px: float, min_frac: float,
) -> bool:
    """贴边细长条带判定:平行于某条有效边界、厚度 ≤ max_px、
    长度 ≥ min_frac×该边全长。真实遮挡物(手/手指)在平面上是块状
    区域,跟踪误差产生的接缝伪影则是沿边界平行的窄条。"""
    x_in = bx <= margin
    x_out = bx + bw >= w - margin
    y_in = by <= margin
    y_out = by + bh >= h - margin
    if bh <= max_px and (y_in or y_out) and bw >= min_frac * w:
        return True
    if bw <= max_px and (x_in or x_out) and bh >= min_frac * h:
        return True
    return False


def collect_occlusions(
    video_path: str,
    tracks: Sequence,
    *,
    anchor_gray: np.ndarray,
    plane_size: Tuple[int, int],
    origin: Tuple[int, int],
    cfg: Optional[OcclusionConfig] = None,
    log: Optional[Callable[[str], None]] = None,
    samples: Optional[Dict[int, Optional[List[np.ndarray]]]] = None,
) -> Dict[int, List[np.ndarray]]:
    """逐采样 ok 帧检测遮挡,返回 ``{frame_num: [平面坐标多边形...]}``。

    顺序解码一遍(与 tracker/亮度测量同一时间轴);每
    ``sample_stride_frames`` 个 ok 帧采样一次、最后一个 ok 帧必测。任何
    帧都没有显著遮挡时返回空 dict。视频打不开抛 :class:`RuntimeError`。

    ``samples``(可选 side-channel,B1):调用方传空 dict,返回后包含**每个
    计划采样点**的完整检测证据——``[]`` = 成功检测且无遮挡(确认清晰,
    不得再当未知)、非空 = 命中多边形、``None`` = 解码/检测失败或无效
    跟踪(未知)。V3 的二次漏采根因正是「只保留命中帧、其余点信息丢失」
    ——消费方无法区分清晰与未采样。兼容返回值仍只含非空 polys(既有调用
    方的布尔判断与统计不变)。
    """
    import cv2

    cfg = cfg or OcclusionConfig()
    tmap = {t.frame_num: t for t in tracks}
    ok = sorted(
        f for f, t in tmap.items()
        if t.status == "ok" and t.homography_inv is not None)
    if not ok:
        if samples is not None:
            samples.clear()
        return {}
    stride = max(1, int(cfg.sample_stride_frames))
    sample_frames = set(ok[::stride])
    sample_frames.add(ok[-1])

    if samples is not None:
        samples.clear()
    occlusions: Dict[int, List[np.ndarray]] = {}
    cap = cv2.VideoCapture(str(video_path))
    if not cap.isOpened():
        raise RuntimeError(f"Cannot open video: {video_path}")
    try:
        frame_idx = 0
        while sample_frames:
            ok_flag, frame = cap.read()
            if not ok_flag or frame is None:
                break
            if frame_idx in sample_frames:
                sample_frames.discard(frame_idx)
                tq = tmap[frame_idx]
                try:
                    polys = detect_occlusion_polygons(
                        frame, tq.homography_inv, anchor_gray,
                        plane_size, origin,
                        diff_tol=cfg.diff_tol, min_area_px=cfg.min_area_px,
                        max_coverage=cfg.max_coverage,
                        epsilon_px=cfg.epsilon_px,
                        morph_kernel=cfg.morph_kernel,
                        edge_margin_px=cfg.edge_margin_px,
                        edge_sliver_max_px=cfg.edge_sliver_max_px,
                        edge_sliver_min_frac=cfg.edge_sliver_min_frac)
                except (ValueError, cv2.error):
                    polys = None  # 退化单应等:检测失败 = 未知,非清晰
                    if samples is not None:
                        samples[frame_idx] = None
                else:
                    if samples is not None:
                        samples[frame_idx] = list(polys)
                if polys:
                    occlusions[frame_idx] = polys
            frame_idx += 1
    finally:
        cap.release()
    if samples is not None:
        # 解码中断:剩余计划点从未检测 = 未知(None),不得伪装成清晰。
        for f in sample_frames:
            samples[f] = None
    if occlusions and log is not None:
        frames = sorted(occlusions)
        log(f"occlusion: detected on {len(frames)} sampled frame(s) "
            f"({frames[0]}..{frames[-1]})")
    return occlusions


# ---------------------------------------------------------------------------
# 多边形几何与标签合成
# ---------------------------------------------------------------------------

def _resample_polygon(pts: np.ndarray, n: int) -> Optional[np.ndarray]:
    """多边形沿周长均匀重采样为 n 点(n ≥ 3);退化返回 None。"""
    pts = np.asarray(pts, dtype=np.float64)
    if len(pts) < 3:
        return None
    seg = np.roll(pts, -1, axis=0) - pts
    lens = np.hypot(seg[:, 0], seg[:, 1])
    total = float(lens.sum())
    if total < 1e-6:
        return None
    targets = np.linspace(0.0, total, int(n), endpoint=False)
    cum = np.concatenate([[0.0], np.cumsum(lens)])
    idx = np.clip(np.searchsorted(cum, targets, side="right") - 1,
                  0, len(lens) - 1)
    t = (targets - cum[idx]) / np.maximum(lens[idx], 1e-12)
    return pts[idx] + seg[idx] * t[:, None]


def _to_screen(polys: Sequence[np.ndarray], homography: np.ndarray) -> List[np.ndarray]:
    """平面坐标多边形 → 画面(PlayRes)坐标(经 H(init→frame))。"""
    import cv2

    out = []
    for poly in polys:
        pts = np.asarray(poly, dtype=np.float64).reshape(-1, 2)
        proj = cv2.perspectiveTransform(
            pts.reshape(-1, 1, 2).astype(np.float32),
            homography.astype(np.float32),
        ).reshape(-1, 2).astype(np.float64)
        out.append(proj)
    return out


def _fmt(v: float) -> str:
    v = float(v)
    if v == 0.0:
        v = 0.0
    return f"{v:.1f}"


def _drawing(polys_screen: Sequence[np.ndarray]) -> str:
    """画面坐标多边形 → ASS 绘图命令(每多边形一个 m + l 子路径)。"""
    parts: List[str] = []
    for poly in polys_screen:
        cmds = [f"m {_fmt(poly[0][0])} {_fmt(poly[0][1])}"]
        rest = " ".join(f"{_fmt(p[0])} {_fmt(p[1])}" for p in poly[1:])
        cmds.append(f"l {rest}")
        parts.append(" ".join(cmds))
    return " ".join(parts)


def _poly_box_overlap_ratio(poly: np.ndarray, box: Sequence[float]) -> float:
    """多边形与行框(平面坐标)的相交面积 / 行框面积(光栅近似)。"""
    import cv2

    pts = np.asarray(poly, dtype=np.float64)
    x1, y1, x2, y2 = (float(v) for v in box[:4])
    bx1, by1 = int(np.floor(x1)), int(np.floor(y1))
    bx2, by2 = int(np.ceil(x2)), int(np.ceil(y2))
    w, h = bx2 - bx1, by2 - by1
    if w < 1 or h < 1:
        return 0.0
    mask = np.zeros((h, w), np.uint8)
    cv2.fillPoly(mask, [(pts - [bx1, by1]).astype(np.int32)], 255)
    return float((mask > 0).sum()) / float(w * h)


def _even_subset(frames: List[int], max_n: int) -> List[int]:
    """从(升序)帧列表均匀取至多 max_n 个,恒含首末。"""
    if len(frames) <= max_n:
        return list(frames)
    idx = np.linspace(0, len(frames) - 1, int(max_n))
    return [frames[int(round(i))] for i in idx]


def build_clip_tag(
    hits: List[Tuple[int, List[np.ndarray]]],
    tmap: Dict[int, object],
    cfg: OcclusionConfig,
    ev_start: float,
    ev_end: float,
) -> str:
    """(帧, 行框相交多边形) 序列 → ``\\iclip … \\t`` 标签串(不含大括号)。

    ``homography`` 取该帧 TrackedQuad 的 H(init→frame)(平面 → 画面坐标)。
    首末命中帧多边形个数相同(各自重采样为等点数)→ 动画插值;否则全部
    并集静态裁剪。命中为空返回 ""。
    """
    if not hits:
        return ""
    n_pts = max(3, int(cfg.resample_points))

    def prepared(frame: int, polys: List[np.ndarray]):
        hom = np.asarray(getattr(tmap[frame], "homography"), dtype=np.float64)
        resampled = []
        for poly in polys:
            r = _resample_polygon(poly, n_pts)
            if r is not None:
                resampled.append(r)
        if not resampled:
            return None
        return _to_screen(resampled, hom)

    entries = []
    for frame, polys in hits:
        prepared_polys = prepared(frame, polys)
        if prepared_polys:
            entries.append(prepared_polys)
    if not entries:
        return ""
    first, last = entries[0], entries[-1]
    if len(entries) > 1 and len(first) == len(last):
        ms = max(0, int(round((float(ev_end) - float(ev_start)) * 1000)))
        return (f"\\iclip({_drawing(first)})"
                f"\\t(0,{ms},\\iclip({_drawing(last)}))")
    union = [poly for entry in entries for poly in entry]
    return f"\\iclip({_drawing(union)})"


def _frame_clip_tag(
    frame: int,
    polys_plane: Sequence[np.ndarray],
    lt: object,
    homography: np.ndarray,
    cfg: OcclusionConfig,
) -> Optional[str]:
    r"""单帧静态 ``\iclip`` 标签(当前帧单应映射),无有效命中返回 None。

    多边形在平面坐标上做行框相交过滤、等点重采样,经**该帧**的
    H(init→frame) 映射到画面坐标后写静态矢量 clip——绝不在 ``\t`` 内写
    vector clip(libass 对其支持未经验证,V3)。
    """
    overlapping = [
        poly for poly in polys_plane
        if _poly_box_overlap_ratio(poly, lt.ref_box) >= cfg.min_line_overlap]
    if not overlapping:
        return None
    n_pts = max(3, int(cfg.resample_points))
    resampled = []
    for poly in overlapping:
        r = _resample_polygon(poly, n_pts)
        if r is not None:
            resampled.append(r)
    if not resampled:
        return None
    screen = _to_screen(resampled, homography)
    return f"\\iclip({_drawing(screen)})"


def attach_occlusion_clips(
    events: List[Dict],
    *,
    tracks: Sequence,
    line_tracks: Sequence,
    occlusions: Dict[int, List[np.ndarray]],
    cfg: Optional[OcclusionConfig] = None,
    samples: Optional[Dict[int, Optional[List[np.ndarray]]]] = None,
    motion_cfg: Optional[object] = None,
    alignments: Optional[Sequence[str]] = None,
    video_height: Optional[float] = None,
) -> List[Dict]:
    r"""给部分遮挡跨度内的事件追加时间局部化的 ``\iclip`` 蒙版。

    ``motion_cfg`` 为 None(旧调用)时保持旧的整段 ``\iclip(…\t…)`` 行为;
    生产路径(B2)传 ``MotionAssConfig``:对有相交命中的事件,按原事件覆盖
    的 ok 帧逐帧重建 ``\pos/\frz/\fscx/\fscy``(:func:`core.occlusion_timeline.
    pose_events_for_frames`),每帧从**最近采样**取平面多边形、经**当前帧**
    单应映射写静态 ``\iclip``;该帧无命中/样本未知则不带 clip。相邻帧仅当
    正文/标签/图层完全一致才压缩合并。没有任何相交命中的事件原样返回。

    ``samples``(B1 side-channel)提供每个计划采样点的完整证据([] = 确认
    清晰、None = 未知、非空 = 命中);缺省时从 ``occlusions`` 的键构造
    (只有命中帧已知)。``alignments`` 为逐行对齐结果(与 ``line_tracks``
    同序;缺省居中)。候选发现仍然只认 ``occlusions`` 的实际检测键——先
    无候选则整条事件零开销直通。
    """
    if not events:
        return list(events)
    if not occlusions:
        return list(events)
    cfg = cfg or OcclusionConfig()
    tmap = {t.frame_num: t for t in tracks}
    ok_frames = sorted(
        f for f, t in tmap.items()
        if t.status == "ok" and t.time_sec is not None)
    if not ok_frames:
        return list(events)
    times = {f: float(tmap[f].time_sec) for f in ok_frames}
    discrete = motion_cfg is not None
    align_list = list(alignments) if alignments is not None else []
    if discrete:
        from core.motion_ass import ALIGN_CENTER, format_ass_time
        from core.occlusion_timeline import (
            frame_end_sec,
            pose_events_for_frames,
            sample_for_frame,
        )

        max_dist = max(0, int(cfg.sample_stride_frames) // 2)
        # 生产者提供了证据就用之;samples 为空而 occlusions 非空(旧生产者/
        # 测试替身)时从检测键推导——只有命中帧已知,其余视为未知。
        sample_view: Dict[int, Optional[List[np.ndarray]]] = (
            samples if samples
            else {f: list(p) for f, p in occlusions.items()})

    def parse_cs(value: str) -> float:
        h, m, rest = str(value).split(":")
        return int(h) * 3600 + int(m) * 60 + float(rest)

    out: List[Dict] = []
    for ev in events:
        li = ev.get("line_idx")
        lt = line_tracks[li] if isinstance(li, int) and 0 <= li < len(line_tracks) else None
        if lt is None:
            out.append(ev)
            continue
        st = parse_cs(ev["start_time"])
        en = parse_cs(ev["end_time"])
        # 廉价预检:检测键与事件跨度不相交 → 零改动直通(逐帧解析都省了)。
        candidates = [f for f in sorted(occlusions)
                      if f in times and st - 1e-6 <= times[f] <= en + 1e-6]
        if not candidates:
            out.append(ev)
            continue
        if not discrete:
            hits: List[Tuple[int, List[np.ndarray]]] = []
            for f in candidates:
                overlapping = [
                    poly for poly in occlusions.get(f, [])
                    if _poly_box_overlap_ratio(poly, lt.ref_box)
                    >= cfg.min_line_overlap]
                if overlapping:
                    hits.append((f, overlapping))
            if not hits:
                out.append(ev)
                continue
            tag = build_clip_tag(hits, tmap, cfg, st, en)
            if not tag:
                out.append(ev)
                continue
            ev = dict(ev)
            ev["tags"] = f"{ev['tags']}{{{tag}}}"
            out.append(ev)
            continue
        # —— B2 离散切片:逐帧重建姿态 + 静态 iclip ——
        align = (align_list[li]
                 if 0 <= li < len(align_list) and align_list[li]
                 else ALIGN_CENTER)
        try:
            pose_events = pose_events_for_frames(
                lt, tracks, st, en, motion_cfg, align, li,
                video_height=video_height)
        except ValueError:
            # 量化坍缩等明确失败:走有证据的保守回退(旧整段并集 clip,
            # 宁多勿漏),不静默丢弃、不自动延长到后续文字。
            hits = []
            for f in candidates:
                overlapping = [
                    poly for poly in occlusions.get(f, [])
                    if _poly_box_overlap_ratio(poly, lt.ref_box)
                    >= cfg.min_line_overlap]
                if overlapping:
                    hits.append((f, overlapping))
            tag = build_clip_tag(hits, tmap, cfg, st, en) if hits else ""
            if not tag:
                out.append(ev)
            else:
                ev2 = dict(ev)
                ev2["tags"] = f"{ev2['tags']}{{{tag}}}"
                out.append(ev2)
            continue
        sliced: List[Dict] = []
        any_clip = False
        for pev in pose_events:
            frames = pev.get("_frames") or ()
            # 每帧解析 clip;相邻同 clip(含同为无 clip)归并同段。
            runs: List[Tuple[Optional[str], List[int]]] = []
            for f in frames:
                if f not in times:
                    clip = None
                else:
                    sample = sample_for_frame(f, sample_view, max_dist)
                    clip = None
                    if sample is not None:
                        polys = sample_view.get(sample) or []
                        clip = _frame_clip_tag(
                            f, polys, lt,
                            np.asarray(tmap[f].homography, dtype=np.float64),
                            cfg)
                if runs and runs[-1][0] == clip:
                    runs[-1][1].append(f)
                else:
                    runs.append((clip, [f]))
            for clip, frames_run in runs:
                t0 = times[frames_run[0]]
                t1 = frame_end_sec(frames_run[-1], times, ok_frames, en)
                if t1 <= t0:
                    continue
                new_ev = {
                    "start_time": format_ass_time(t0),
                    "end_time": format_ass_time(t1),
                    "style": pev["style"],
                    "name": pev["name"],
                    "tags": pev["tags"],
                    "body": pev["body"],
                    "line_idx": pev.get("line_idx", li),
                }
                for key in ("layer", "policy", "comment", "roi"):
                    if key in ev:
                        new_ev[key] = ev[key]
                if clip:
                    new_ev["tags"] = f"{new_ev['tags']}{{{clip}}}"
                    any_clip = True
                sliced.append(new_ev)
        if not any_clip:
            # 没有任何相交命中(或全部样本未知/清晰):事件原样返回。
            out.append(ev)
            continue
        out.extend(sliced)
    return out
