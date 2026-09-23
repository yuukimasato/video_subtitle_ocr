# core/occluder_contours.py
r"""B4:屏幕坐标前景轮廓分割与时间局部 ``\iclip``。

与 B1/B2 的平面遮挡多边形不同,前景(手)独立于文字平面运动:轮廓在**当前
视频屏幕坐标**上细化,不随平面单应拖动;输出统一携带
:class:`ContourResult` 状态——``valid``(有轮廓)、``clear``(有检测证据且
无前景)、``unknown``(未采样/失败;缺失帧按 unknown 处理,仅允许复用不超
过一帧的上一有效轮廓)。

流水线:
1. :func:`build_seeds` —— 亮度归一差分产生候选;结合连通区域连贯性
   (细碎字形变化不成强前景种子);纯全局调暗不产生前景;domain(待保护
   文字所在物体及适当外扩)之外为可靠背景种子;
2. :func:`refine_occluder_contours` —— GrabCut(GC_INIT_WITH_MASK)在
   局部边界带精化;只保留与强前景种子相连的区域(不用凸包填死指缝);
   ``RETR_CCOMP`` 保留洞结构(指缝),``approxPolyDP`` 误差上限为
   1080p 的 1.5px(按分辨率缩放),顶点上限 512;
3. :func:`contours_to_iclip` —— 轮廓 → 一条多子路径 ``\iclip`` 绘图串
   (洞用反向绕序闭合子路径;坐标已在 ASS PlayRes,只变换这一次);
4. :func:`apply_screen_occlusion` —— 按事件自身几何/绝对时间切片消费
   ``screen_occlusions``(键 = 视频帧号):``valid`` 写 iclip、``clear``
   不裁、``unknown`` 仅复用不超过一帧的上一有效轮廓,之后不应用裁剪。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import List, Mapping, Optional, Sequence, Tuple

import numpy as np

logger = logging.getLogger(__name__)

__all__ = [
    "ContourRing",
    "ContourResult",
    "build_seeds",
    "refine_occluder_contours",
    "contours_to_iclip",
    "apply_screen_occlusion",
]

_MIN_FG_PIXELS = 64          # 前景种子最少像素(不足 = unknown)
_MAX_COVERAGE = 0.55         # 变化覆盖率超过 = 全局外观变化,非前景
_DIFF_TOL = 26.0             # 亮度归一差分阈值
_MORPH_KERNEL = 5
_MIN_COMPONENT_AREA = 400.0  # 连通前景候选最小面积(px²,滤细碎字形变化)
_VERTEX_LIMIT = 512          # 单轮廓顶点上限(超出分多路径保存并记录)
_BASE_EPSILON_PX = 1.5       # approxPolyDP 误差上限(1080p 基准)


@dataclass
class ContourRing:
    """一条闭合轮廓环;``points`` 为 N×2 有限屏幕坐标。"""

    points: np.ndarray
    is_hole: bool = False
    parent: Optional[int] = None


@dataclass
class ContourResult:
    """单帧屏幕轮廓结果。``status`` ∈ valid/clear/unknown。"""

    rings: List[ContourRing] = field(default_factory=list)
    status: str = "unknown"
    reason: str = ""

    @property
    def valid(self) -> bool:
        return self.status == "valid" and bool(self.rings)


def _brightness_normalized_diff(
    frame_gray: "np.ndarray", reference_gray: "np.ndarray"
) -> "np.ndarray":
    """亮度归一差分:整体明暗等比缩放抵消,局部遮挡保留。"""
    import cv2

    frame_med = float(np.median(frame_gray))
    ref_med = float(np.median(reference_gray))
    if frame_med > 1.0 and ref_med > 1.0 and abs(frame_med - ref_med) > 1e-6:
        scaled = np.clip(
            frame_gray.astype(np.float64) * (ref_med / frame_med),
            0.0, 255.0)
        return cv2.absdiff(scaled.astype(np.uint8), reference_gray)
    return cv2.absdiff(frame_gray, reference_gray)


def build_seeds(
    frame_gray: "np.ndarray",
    reference_gray: "np.ndarray",
    domain: "np.ndarray",
    *,
    diff_tol: float = _DIFF_TOL,
    min_component_area: float = _MIN_COMPONENT_AREA,
    max_coverage: float = _MAX_COVERAGE,
    morph_kernel: int = _MORPH_KERNEL,
) -> Tuple["np.ndarray", "np.ndarray", str, str]:
    """亮度归一差分 → (前景种子, 背景种子, status, reason)。

    前景候选 = domain 内变化像素的连通区域(面积 ≥ ``min_component_area``,
    细碎字形变化不成强前景种子);候选膨胀后的余量即可靠背景。全局调暗
    (覆盖率超门限)返回 status="clear"(有证据且无前景);前景候选为空
    同样 clear。
    """
    import cv2

    h, w = frame_gray.shape[:2]
    diff = _brightness_normalized_diff(frame_gray, reference_gray)
    changed = (diff > float(diff_tol)).astype(np.uint8)
    k = int(morph_kernel)
    if k >= 3:
        kernel = np.ones((k, k), np.uint8)
        changed = cv2.morphologyEx(changed, cv2.MORPH_CLOSE, kernel)
        changed = cv2.morphologyEx(changed, cv2.MORPH_OPEN, kernel)
    if float(changed.mean()) > float(max_coverage):
        empty = np.zeros((h, w), np.uint8)
        return empty, empty, "clear", (
            f"global appearance change ({changed.mean():.0%} of pixels); "
            "not foreground evidence")
    # domain 外一概视为可靠背景;domain 内远离候选的区域随后补充。
    fg_seed = np.zeros((h, w), np.uint8)
    domain_mask = np.asarray(domain) > 0
    changed_in = changed.copy()
    changed_in[~domain_mask] = 0
    n_components, labels, stats, _centroids = cv2.connectedComponentsWithStats(
        changed_in, connectivity=8)
    kept = np.zeros((h, w), np.uint8)
    for i in range(1, n_components):
        if float(stats[i, cv2.CC_STAT_AREA]) >= float(min_component_area):
            kept[labels == i] = 1
    if not kept.any():
        empty = np.zeros((h, w), np.uint8)
        return empty, empty, "clear", "no connected foreground candidate"
    # 强前景种子 = 候选腐蚀后的可信核。
    fg_seed = cv2.erode(kept, np.ones((max(3, k), max(3, k)), np.uint8))
    if not fg_seed.any():
        fg_seed = kept
    # 背景种子 = domain 外 + domain 内远离候选的可靠区域。
    fg_zone = cv2.dilate(kept, np.ones((2 * k + 1, 2 * k + 1), np.uint8))
    bg_seed = np.zeros((h, w), np.uint8)
    bg_seed[~domain_mask] = 1
    bg_seed[(domain_mask) & (fg_zone == 0)] = 1
    return fg_seed, bg_seed, "valid", "foreground candidate(s) found"


def refine_occluder_contours(
    frame_bgr: "np.ndarray",
    foreground_seed: "np.ndarray",
    background_seed: "np.ndarray",
    domain: "np.ndarray",
    *,
    previous_mask: Optional["np.ndarray"] = None,
    epsilon_px: Optional[float] = None,
    resolution_height: float = 1080.0,
) -> ContourResult:
    """GrabCut 精化 + 洞结构保留 → 屏幕(视频)坐标轮廓。

    - 冲突种子(fg∩bg)、前景种子不足 64 像素、没有可靠背景 → unknown;
    - 只保留与强前景种子相连的区域(不凸包填死指缝);
    - ``RETR_CCOMP`` 保留洞(外环 is_hole=False,内环 is_hole=True 并携带
      parent 外环索引);
    - ``approxPolyDP`` 误差上限 = ``_BASE_EPSILON_PX``×(resolution_height
      /1080);单轮廓顶点超 512 时按更粗误差重试并在 reason 记录复杂度,
      不增加误差吃掉指缝之外再静默截断。
    ``previous_mask`` 仅作 GrabCut 先验提示(可选),不参与判定。
    """
    import cv2

    h, w = frame_bgr.shape[:2]
    fg = np.asarray(foreground_seed)
    bg = np.asarray(background_seed)
    dom = np.asarray(domain) > 0
    if fg.shape != (h, w) or bg.shape != (h, w):
        return ContourResult([], "unknown", "seed shape mismatch")
    conflict = int(((fg > 0) & (bg > 0)).sum())
    if conflict:
        return ContourResult(
            [], "unknown", f"conflicting seeds ({conflict} px)")
    fg_count = int((fg > 0).sum())
    if fg_count < _MIN_FG_PIXELS:
        return ContourResult(
            [], "unknown",
            f"foreground seed too small ({fg_count} < {_MIN_FG_PIXELS} px)")
    if not (bg > 0).any() or not dom.any():
        return ContourResult([], "unknown", "no reliable background seeds")

    labels = np.full((h, w), cv2.GC_PR_BGD, np.uint8)
    labels[dom == 0] = cv2.GC_BGD
    labels[bg > 0] = cv2.GC_BGD
    labels[fg > 0] = cv2.GC_FGD
    try:
        cv2.grabCut(frame_bgr, labels, None, np.zeros((1, 65)),
                    np.zeros((1, 65)), 3, cv2.GC_INIT_WITH_MASK)
    except cv2.error as exc:
        return ContourResult([], "unknown", f"grabCut failed: {exc}")
    binary = np.isin(labels, [cv2.GC_FGD, cv2.GC_PR_FGD]).astype(np.uint8)
    # 只保留与强前景种子相连的区域。
    n_comp, comp_labels = cv2.connectedComponents(binary, connectivity=8)
    keep_labels = set(int(v) for v in np.unique(comp_labels[fg > 0])) - {0}
    if not keep_labels:
        return ContourResult(
            [], "clear", "grabCut found no foreground near seeds")
    final = np.isin(comp_labels, list(keep_labels)).astype(np.uint8)
    contours, hierarchy = cv2.findContours(
        final, cv2.RETR_CCOMP, cv2.CHAIN_APPROX_SIMPLE)
    if not contours:
        return ContourResult([], "clear", "no contours after segmentation")
    eps = (float(epsilon_px) if epsilon_px is not None
           else _BASE_EPSILON_PX * float(resolution_height) / 1080.0)
    hierarchy = hierarchy[0] if hierarchy is not None else None
    rings: List[ContourRing] = []
    simplified: List[Tuple[int, int]] = []  # (轮廓下标, 顶点数)
    for idx, contour in enumerate(contours):
        pts = _approximate(contour, eps)
        is_hole = bool(hierarchy is not None and hierarchy[idx][3] >= 0)
        parent = (int(hierarchy[idx][3])
                  if is_hole and hierarchy is not None else None)
        rings.append(ContourRing(
            points=pts.reshape(-1, 2).astype(np.float64),
            is_hole=is_hole, parent=parent))
        simplified.append((idx, len(pts)))
    over = [(i, n) for i, n in simplified if n >= _VERTEX_LIMIT]
    reason = "grabCut segmentation ok"
    if over:
        reason += (f"; {_VERTEX_LIMIT}-vertex limit reached on "
                   f"{len(over)} contour(s) (kept as-is, complexity noted)")
    return ContourResult(rings, "valid", reason)


def _approximate(contour: "np.ndarray", eps: float) -> "np.ndarray":
    """approxPolyDP 简化;顶点超上限时逐步放大误差(记录型退化)。"""
    import cv2

    approx = cv2.approxPolyDP(contour, float(eps), True)
    factor = 1.0
    while len(approx) >= _VERTEX_LIMIT and factor < 8.0:
        factor *= 2.0
        approx = cv2.approxPolyDP(contour, float(eps) * factor, True)
    return approx


def contours_to_iclip(rings: Sequence[ContourRing]) -> str:
    r"""轮廓环 → 一条含多个闭合子路径的 ``\iclip`` 绘图串(不含大括号)。

    外环正绕序、洞反向闭合(libass nonzero 填充下洞从蒙版中挖去);无环
    返回空串;非有限坐标拒绝(:class:`ValueError`)。
    """
    live = [r for r in rings if r is not None and len(r.points) >= 3]
    if not live:
        return ""

    def fmt(v: float) -> str:
        return f"{float(v):.1f}"

    parts: List[str] = []
    for ring in live:
        pts = np.asarray(ring.points, dtype=np.float64)
        if not np.all(np.isfinite(pts)):
            raise ValueError("contour ring has non-finite coordinates")
        ordered = pts if not ring.is_hole else pts[::-1]
        cmds = [f"m {fmt(ordered[0][0])} {fmt(ordered[0][1])}"]
        rest = " ".join(f"{fmt(p[0])} {fmt(p[1])}" for p in ordered[1:])
        cmds.append(f"l {rest}")
        parts.append(" ".join(cmds))
    return f"\\iclip({' '.join(parts)})"


def apply_screen_occlusion(
    events: List[dict],
    screen_occlusions: Mapping[int, ContourResult],
    *,
    fps: float,
    event_geometry: Mapping[str, object],
    default_status: str = "unknown",
) -> List[dict]:
    r"""策略后按事件几何/绝对时间切片,把屏幕轮廓写成时间局部 ``\iclip``。

    ``screen_occlusions`` 键 = 视频帧号,缺失帧按 unknown 处理;
    ``event_geometry`` 按事件稳定 ID 保存重建来源(文字 LineTrack 或
    mask 块四角轨迹),值提供 ``frames() -> [(frame_num, (x1,y1,x2,y2))]``
    (屏幕坐标的逐帧行/块框)。事件几何与轮廓相交才算命中;``clear`` 不
    裁剪;``unknown`` 仅允许复用不超过一帧的上一有效轮廓并记录诊断,之后
    静态回退(不应用裁剪)。
    """
    if not events or not screen_occlusions:
        return list(events)

    def _cs(value: str) -> float:
        h, m, rest = str(value).split(":")
        return int(h) * 3600 + int(m) * 60 + float(rest)

    out: List[dict] = []
    for ev in events:
        # 事件稳定 ID:优先 roi,其次 name(几何重建来源的登记键)。
        geometry = None
        if event_geometry:
            for key in (str(ev.get("roi")), str(ev.get("name")),
                        str(id(ev))):
                geometry = event_geometry.get(key)
                if geometry is not None:
                    break
        frame_boxes = list(geometry.frames()) if geometry is not None else []
        if not frame_boxes:
            out.append(ev)
            continue
        st, en = round(_cs(ev["start_time"]) * 100), round(_cs(ev["end_time"]) * 100)
        points: List[Tuple[float, Optional[str]]] = [(st, None)]
        last_valid: Optional[str] = None
        last_valid_frame: Optional[int] = None
        for frame, box in frame_boxes:
            t = round(frame / float(fps) * 100) if fps > 0 else 0
            if t < st or t > en:
                continue
            result = screen_occlusions.get(frame)
            if result is None and default_status == "clear":
                # 缺失帧按 clear(有证据窗口外的帧无前景证据)。
                clip = None
                points.append((t, clip))
                continue
            clip = None
            if result is not None and result.valid:
                ring_pts = [r for r in result.rings
                            if _ring_intersects_box(r.points, box)]
                if ring_pts:
                    try:
                        clip = contours_to_iclip(ring_pts)
                    except ValueError:
                        clip = None
                last_valid = clip
                last_valid_frame = frame
            elif result is not None and result.status == "unknown":
                # unknown(未采样/失败/缺失):仅复用不超过一帧的上一有效
                # 轮廓并留痕,之后不应用裁剪(不无限期延用过期轮廓)。
                if (last_valid is not None
                        and last_valid_frame is not None
                        and frame - last_valid_frame <= 1):
                    clip = last_valid
                    logger.info(
                        "screen occlusion: frame %s unknown; reusing "
                        "previous valid contour (1 frame)", frame)
                else:
                    last_valid = None
            else:  # clear:有证据确认无前景
                last_valid = None
                last_valid_frame = None
            points.append((t, clip))
        if not any(clip for _t, clip in points):
            out.append(ev)
            continue
        # 相邻同 clip 归并;clip 段后接 None 时,clip 只延伸其末帧 + 1 帧
        # (其余为 unknown → 不裁);整条时间轴完整覆盖到 en,不留空洞。
        runs: List[Tuple[Optional[str], float, Optional[float]]] = []
        for t, clip in points:
            if runs and runs[-1][0] == clip:
                runs[-1] = (clip, runs[-1][1], t)
            else:
                runs.append((clip, t, t))
        runs.append((None, en, en))
        for i, (clip, t0, _t1) in enumerate(runs):
            if i + 1 >= len(runs):
                break
            nxt_t = runs[i + 1][1]
            if clip is not None:
                # clip 只延伸到其末帧 + 1 帧与下一段起点中的较小者
                # (厘秒域;段长不足 1cs 时延伸到 1cs,绝不产出零时长)。
                frame_dt_cs = max(1, round(100.0 / max(fps, 1e-6)))
                seg_end = min(nxt_t, _t1 + frame_dt_cs)
                seg_end = max(seg_end, t0 + 1)
                seg_end = min(seg_end, nxt_t)
                if seg_end <= t0:
                    seg_end = t0 + 1
            else:
                seg_end = nxt_t
            if seg_end <= t0:
                continue
            new_ev = dict(ev)
            new_ev["start_time"] = _fmt_ass(t0 / 100.0)
            new_ev["end_time"] = _fmt_ass(seg_end / 100.0)
            if clip:
                new_ev["tags"] = f"{new_ev['tags']}{{{clip}}}"
            out.append(new_ev)
    return out


def _ring_intersects_box(points: "np.ndarray",
                         box: Tuple[float, float, float, float]) -> bool:
    """轮廓外接框与行/块框(屏幕坐标)快速相交判定。"""
    x1, y1, x2, y2 = (float(v) for v in box[:4])
    px1 = float(np.min(points[:, 0]))
    py1 = float(np.min(points[:, 1]))
    px2 = float(np.max(points[:, 0]))
    py2 = float(np.max(points[:, 1]))
    return not (px2 < x1 or px1 > x2 or py2 < y1 or py1 > y2)


def _fmt_ass(t: float) -> str:
    cs = int(round(t * 100.0))
    cent = cs % 100
    total = cs // 100
    sec = total % 60
    mint = (total // 60) % 60
    hour = total // 3600
    return f"{hour}:{mint:02d}:{sec:02d}.{cent:02d}"
