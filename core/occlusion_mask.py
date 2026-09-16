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
    epsilon_px: float = 2.0           # approxPolyDP 简化容差
    morph_kernel: int = 5             # 闭+开核(奇数)
    sample_stride_frames: int = 3     # 每 N 个好帧检测一次遮挡
    sample_max_frames: int = 7        # 每条事件最多取的遮挡采样帧数
    resample_points: int = 16         # 每个多边形重采样点数(动画插值需等点)


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
    epsilon_px: float = 2.0,
    morph_kernel: int = 5,
) -> List[np.ndarray]:
    """单帧遮挡检测:展开图 vs 锚定帧灰度差 → 平面坐标多边形列表。

    展开窗口与 ``scripts.motion_ass._unwarp_quad_window`` 同一约定:窗口
    像素 (u, v) = 平面点 (qx1+u, qy1+v),故轮廓点加回 ``origin`` 即平面
    坐标。展开图无效(退化单应抛错由调用方处理)、窗口尺寸与锚定图不符
    或无显著变化区时返回空列表。
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
    changed = (cv2.absdiff(gray, anchor_gray) > float(diff_tol)).astype(np.uint8)
    k = int(morph_kernel)
    if k >= 3:
        kernel = np.ones((k, k), np.uint8)
        changed = cv2.morphologyEx(changed, cv2.MORPH_CLOSE, kernel)
        changed = cv2.morphologyEx(changed, cv2.MORPH_OPEN, kernel)
    contours, _ = cv2.findContours(
        changed, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    polys: List[np.ndarray] = []
    for contour in contours:
        if cv2.contourArea(contour) < float(min_area_px):
            continue
        approx = cv2.approxPolyDP(contour, float(epsilon_px), True)
        pts = approx.reshape(-1, 2).astype(np.float64) + np.array([qx1, qy1])
        if len(pts) >= 3:
            polys.append(pts)
    return polys


def collect_occlusions(
    video_path: str,
    tracks: Sequence,
    *,
    anchor_gray: np.ndarray,
    plane_size: Tuple[int, int],
    origin: Tuple[int, int],
    cfg: Optional[OcclusionConfig] = None,
    log: Optional[Callable[[str], None]] = None,
) -> Dict[int, List[np.ndarray]]:
    """逐采样 ok 帧检测遮挡,返回 ``{frame_num: [平面坐标多边形...]}``。

    顺序解码一遍(与 tracker/亮度测量同一时间轴);每
    ``sample_stride_frames`` 个 ok 帧采样一次、最后一个 ok 帧必测。任何
    帧都没有显著遮挡时返回空 dict。视频打不开抛 :class:`RuntimeError`。
    """
    import cv2

    cfg = cfg or OcclusionConfig()
    tmap = {t.frame_num: t for t in tracks}
    ok = sorted(
        f for f, t in tmap.items()
        if t.status == "ok" and t.homography_inv is not None)
    if not ok:
        return {}
    stride = max(1, int(cfg.sample_stride_frames))
    sample_frames = set(ok[::stride])
    sample_frames.add(ok[-1])

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
                        epsilon_px=cfg.epsilon_px,
                        morph_kernel=cfg.morph_kernel)
                except (ValueError, cv2.error):
                    polys = []  # 退化单应等:该帧不判遮挡
                if polys:
                    occlusions[frame_idx] = polys
            frame_idx += 1
    finally:
        cap.release()
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


def attach_occlusion_clips(
    events: List[Dict],
    *,
    tracks: Sequence,
    line_tracks: Sequence,
    occlusions: Dict[int, List[np.ndarray]],
    cfg: Optional[OcclusionConfig] = None,
) -> List[Dict]:
    """给部分遮挡跨度内的事件追加 ``\\iclip`` 蒙版(无遮挡事件原样返回)。

    ``occlusions`` 为 :func:`collect_occlusions` 的产出(可为空 → 原样返回)。
    仅处理带 ``line_idx`` 且能对应到 ``line_tracks`` 的事件(即合成文本行);
    遮挡多边形须与该行 ref_box 相交达 ``min_line_overlap`` 才计入。
    """
    if not events or not occlusions:
        return list(events)
    cfg = cfg or OcclusionConfig()
    tmap = {t.frame_num: t for t in tracks}
    ok_frames = sorted(
        f for f, t in tmap.items()
        if t.status == "ok" and t.time_sec is not None)
    if not ok_frames:
        return list(events)
    times = {f: float(tmap[f].time_sec) for f in ok_frames}

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
        span = [f for f in ok_frames if st - 1e-6 <= times[f] <= en + 1e-6]
        if not span:
            out.append(ev)
            continue
        chosen = _even_subset(span, cfg.sample_max_frames)
        hits: List[Tuple[int, List[np.ndarray]]] = []
        for f in chosen:
            overlapping = [
                poly for poly in occlusions.get(f, [])
                if _poly_box_overlap_ratio(poly, lt.ref_box) >= cfg.min_line_overlap]
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
    return out
