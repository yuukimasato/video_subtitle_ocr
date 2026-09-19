# core/scene_plane_tracker.py
"""场景文字平面跟踪：关键帧框选一次，逐帧输出四边形轨迹与单应性。

对应《手机邮件场景文字分析》阶段二「动态平面 ROI」的第一里程碑：

- 用户在关键帧框选文字平面的四个角点（quad），跟踪器用 ORB 特征匹配 +
  RANSAC 单应估计在后续帧中跟随该平面（手机屏幕/信件/招牌）。
- 每帧输出：四边形（原画面坐标）、累计单应（关键帧画面 → 当前帧，含逆）、
  内点率、重投影误差、匹配数与状态。
- 质量门限（匹配数/内点率/重投影误差/相邻帧跳变/面积突变/凸性）不达标的帧
  记为 ``lost``，并保留最近一个好帧的特征用于后续重捕获——失败不外推、
  不产出无证据的轨迹点（手部大面积遮挡时轨迹在遮挡段断开，文字重新可见后
  自动接回）。
- 轨迹可存为 JSON（含正/逆单应），供透视展开 OCR 与后续 ASS 定位使用；
  :func:`unwarp_plane` 把当前帧按四边形展开成正视矩形。

依赖仅 OpenCV（项目现有依赖，Apache-2.0）。遮挡蒙版、ASS 轨迹标签输出与
GUI 四角修正属于阶段二后续项，不在本模块。
"""

from __future__ import annotations

import logging
import math
import itertools
from dataclasses import dataclass, asdict
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

import cv2
import numpy as np

logger = logging.getLogger(__name__)

ProgressCB = Callable[[int, int], None]

# 平面四边形：4 个 [x, y] 顶点（用户框选顺序即为平面方向，不做重排）。
Quad = List[List[float]]

# ── 默认质量门限（可经 track_plane* 的同名参数覆盖）──────────────────────
DEFAULT_MIN_MATCHES = 12          # 单应估计所需的最少匹配对
_MAX_INIT_PROBE_FRAMES = 900      # 初始锚定向后探测的帧数上限(空白引导段罕见超过)
_INIT_PROBE_LOG_EVERY = 150       # 探测进度日志间隔(帧):长探测不再无留痕
_INIT_STRONG_FEATURES = 48        # 「特征丰富」判定下限:文字/UI 丰富的帧远超此数
DEFAULT_MIN_INLIER_RATIO = 0.35   # RANSAC 内点占匹配对比例下限
DEFAULT_MAX_REPROJ_ERROR = 4.0    # 内点重投影 RMSE 上限（像素）
DEFAULT_MAX_AREA_JUMP = 0.5       # 相邻好帧四边形面积比允许突变幅度
DEFAULT_MAX_JUMP_RATIO = 0.8      # 单帧顶点跳变上限（相对平均边长）
JUMP_FLOOR_PX = 24.0              # 跳变下限，防止小平面被卡死
SEARCH_PAD_RATIO = 0.35           # 匹配源点搜索区外扩（相对 quad bbox 对角线）
MIN_INIT_AREA_PX = 64.0           # 初始四边形最小面积


@dataclass
class TrackedQuad:
    """单帧跟踪结果。``status="lost"`` 时 quad/单应为 None。"""

    frame_num: int
    time_sec: float
    status: str                      # "ok" | "lost"
    quad: Optional[Quad] = None
    homography: Optional[List[List[float]]] = None        # 关键帧 → 当前帧
    homography_inv: Optional[List[List[float]]] = None    # 当前帧 → 关键帧
    inlier_ratio: float = 0.0
    reproj_error: float = 0.0
    matches: int = 0


def _quad_area(quad: np.ndarray) -> float:
    return float(abs(cv2.contourArea(quad.reshape(-1, 1, 2).astype(np.float32))))


def _quad_is_convex(quad: np.ndarray) -> bool:
    """四个顶点按给定顺序构成凸四边形（允许数值噪声，拒绝自交/共线）。"""
    pts = quad.reshape(-1, 2).astype(float)
    n = len(pts)
    signs = []
    for i in range(n):
        a, b, c = pts[i], pts[(i + 1) % n], pts[(i + 2) % n]
        cross = (b[0] - a[0]) * (c[1] - b[1]) - (b[1] - a[1]) * (c[0] - b[0])
        if abs(cross) < 1e-6:
            return False
        signs.append(cross > 0)
    return all(s == signs[0] for s in signs)


def _mean_edge_length(quad: np.ndarray) -> float:
    pts = quad.reshape(-1, 2).astype(float)
    edges = [
        math.hypot(*(pts[(i + 1) % 4] - pts[i])) for i in range(4)
    ]
    return sum(edges) / len(edges)


def _validate_init_quad(quad: Sequence[Sequence[float]]) -> np.ndarray:
    arr = np.asarray(quad, dtype=np.float64)
    if arr.shape != (4, 2):
        raise ValueError(f"init quad must be 4x2 points, got shape {arr.shape}")
    if not np.all(np.isfinite(arr)):
        raise ValueError("init quad has non-finite coordinates")
    if _quad_area(arr) < MIN_INIT_AREA_PX:
        raise ValueError(f"init quad too small (area < {MIN_INIT_AREA_PX:.0f}px^2)")
    if not _quad_is_convex(arr):
        raise ValueError("init quad is not a convex quadrilateral in the given order")
    return arr


def _bbox_expand(quad: np.ndarray, pad_px: float) -> Tuple[float, float, float, float]:
    x1, y1 = quad.reshape(-1, 2).min(axis=0)
    x2, y2 = quad.reshape(-1, 2).max(axis=0)
    return (float(x1) - pad_px, float(y1) - pad_px, float(x2) + pad_px, float(y2) + pad_px)


def _detect_features(
    gray: np.ndarray, feature_count: int,
    mask: Optional[np.ndarray] = None,
) -> Tuple[np.ndarray, np.ndarray]:
    orb = cv2.ORB_create(nfeatures=int(feature_count), scaleFactor=1.2, nlevels=8)
    keypoints, descriptors = orb.detectAndCompute(gray, mask)
    if descriptors is None or len(keypoints) == 0:
        return np.empty((0, 2), np.float64), np.empty((0, 32), np.uint8)
    pts = np.array([kp.pt for kp in keypoints], dtype=np.float64)
    return pts, descriptors


def _match_points(
    ref_pts: np.ndarray,
    ref_desc: np.ndarray,
    cur_pts: np.ndarray,
    cur_desc: np.ndarray,
    region: Tuple[float, float, float, float],
    lowe_ratio: float,
) -> Tuple[np.ndarray, np.ndarray]:
    """Lowe 比值检验匹配，只保留源点落在 region（外扩 bbox）内的配对。"""
    if len(ref_pts) == 0 or len(cur_pts) == 0:
        return np.empty((0, 2)), np.empty((0, 2))
    matcher = cv2.BFMatcher(cv2.NORM_HAMMING)
    pairs = matcher.knnMatch(ref_desc, cur_desc, k=2)
    x1, y1, x2, y2 = region
    src, dst = [], []
    for pair in pairs:
        if len(pair) < 2:
            continue
        m, n = pair[0], pair[1]
        if m.distance >= lowe_ratio * n.distance:
            continue
        p = ref_pts[m.queryIdx]
        if not (x1 <= p[0] <= x2 and y1 <= p[1] <= y2):
            continue
        src.append(p)
        dst.append(cur_pts[m.trainIdx])
    if not src:
        return np.empty((0, 2)), np.empty((0, 2))
    return np.asarray(src, np.float64), np.asarray(dst, np.float64)


def _reproj_rmse(src: np.ndarray, dst: np.ndarray, h_mat: np.ndarray) -> float:
    if len(src) == 0:
        return float("inf")
    proj = cv2.perspectiveTransform(src.reshape(-1, 1, 2).astype(np.float32), h_mat)
    diff = proj.reshape(-1, 2).astype(np.float64) - dst
    err = np.sqrt((diff ** 2).sum(axis=1))
    return float(np.sqrt((err ** 2).mean()))


def _mat_to_list(h_mat: Optional[np.ndarray]) -> Optional[List[List[float]]]:
    if h_mat is None:
        return None
    return [[float(v) for v in row] for row in h_mat]


class _RefState:
    """最近一个好帧的跟踪状态（用于下一帧匹配与丢失后重捕获）。"""

    __slots__ = ("gray", "pts", "desc", "quad", "h_total", "frame_num")

    def __init__(self, gray, pts, desc, quad, h_total, frame_num):
        self.gray = gray
        self.pts = pts
        self.desc = desc
        self.quad = quad
        self.h_total = h_total
        self.frame_num = frame_num


def track_plane_frames(
    frames: Sequence[np.ndarray],
    init_quad: Sequence[Sequence[float]],
    start_frame_num: int = 0,
    times: Optional[Sequence[float]] = None,
    *,
    fps: float = 30.0,
    progress_cb: Optional[ProgressCB] = None,
    min_matches: int = DEFAULT_MIN_MATCHES,
    min_inlier_ratio: float = DEFAULT_MIN_INLIER_RATIO,
    max_reproj_error: float = DEFAULT_MAX_REPROJ_ERROR,
    max_area_jump: float = DEFAULT_MAX_AREA_JUMP,
    max_jump_ratio: float = DEFAULT_MAX_JUMP_RATIO,
    feature_count: int = 2000,
    lowe_ratio: float = 0.75,
    ransac_thresh: float = 3.0,
) -> List[TrackedQuad]:
    """在给定的 BGR 帧序列上跟踪 ``init_quad`` 标记的文字平面。

    Args:
        frames: BGR 帧列表（ndarray），第一帧为关键帧。
        init_quad: 关键帧上的 4x2 四边形顶点（顺序保持，须为凸四边形）。
        start_frame_num: 第一帧对应的视频帧号（时间轴用）。
        times: 每帧时间戳（秒）；缺省用 ``start_frame_num/fps`` 推算。
        fps: ``times`` 缺省时的时间戳推算帧率。
        progress_cb: ``progress_cb(done, total)`` 逐帧回调。
        min_matches / min_inlier_ratio / max_reproj_error / max_area_jump /
            max_jump_ratio: 质量门限，见模块 docstring。
        feature_count / lowe_ratio / ransac_thresh: ORB 与匹配参数。

    Returns:
        与输入等长的 :class:`TrackedQuad` 列表。质量门限不达标的帧
        ``status="lost"``，四边形与单应为 None，轨迹不外推。
    """
    if not frames:
        return []
    quad0 = _validate_init_quad(init_quad)
    # RANSAC 内部使用 cv2 全局 RNG；固定种子保证同一输入轨迹可复现。
    cv2.setRNGSeed(0)

    diag = float(np.hypot(*((quad0.reshape(-1, 2).max(axis=0) - quad0.reshape(-1, 2).min(axis=0)))))
    region0 = _bbox_expand(quad0, max(24.0, SEARCH_PAD_RATIO * diag))

    def _features_near_quad(gray: np.ndarray):
        # 探测/初始锚定帧只在 region0 内检测特征:quad 占画面小时,全帧
        # 检测的特征预算大量落在区域外,区域内特征数系统性偏低还白费算力。
        # 匹配语义不变——_match_points 本就按 region 过滤 ref 源点,初始段
        # ref.quad 未动时 region 即 region0;锚定后的第一个好帧起 ref 即
        # 换成全帧检测的特征,后续跟踪不受影响。
        mask = np.zeros(gray.shape[:2], np.uint8)
        x1 = max(0, int(math.floor(region0[0])))
        y1 = max(0, int(math.floor(region0[1])))
        x2 = min(gray.shape[1], int(math.ceil(region0[2])))
        y2 = min(gray.shape[0], int(math.ceil(region0[3])))
        if x2 > x1 and y2 > y1:
            mask[y1:y2, x1:x2] = 255
        pts, desc = _detect_features(gray, feature_count, mask)
        near = (pts[:, 0] >= region0[0]) & (pts[:, 0] <= region0[2]) \
            & (pts[:, 1] >= region0[1]) & (pts[:, 1] <= region0[3])
        return pts, desc, near

    # 首帧可能恰好无纹理:聊天界面文字逐条浮现(起始是空白屏)、信纸逐行
    # 显影、淡入首帧等,quad 区域内特征数不足并不代表该平面不可跟踪。
    # 向后逐帧探测,优先锚定在第一个「特征丰富」的帧上(阈值
    # _init_strong_features):仅 UI 框架可见时特征刚过下限,锚在那里跟踪
    # 很快会丢,而文字出现后的帧特征数倍增,锚点自然落进文字区段。全片
    # 都不「丰富」时退回第一个够 min_matches 的帧;仍没有才按原样报错。
    # 探测上限 _MAX_INIT_PROBE_FRAMES 帧。之前的帧记 lost(该段无文字可
    # 回贴,不产出事件)。
    total = len(frames)
    last_reported = 0

    def _report(done: int) -> None:
        # 进度只进不退:探测期已按帧回调,锚定回退(first_weak)后主循环
        # 会重扫已探测的帧,回调值不得小于已报值。
        nonlocal last_reported
        if progress_cb is not None and done > last_reported:
            last_reported = done
            progress_cb(done, total)

    gray0 = cv2.cvtColor(frames[0], cv2.COLOR_BGR2GRAY)
    pts0, desc0, near0 = _features_near_quad(gray0)
    _report(1)

    init_index = 0
    strong_need = max(4 * min_matches, _INIT_STRONG_FEATURES)
    first_weak_index: Optional[int] = None
    # 候选锚定帧特征缓存:只存「刚够 min_matches」与「特征丰富」的候选帧
    # (通常 ≤2 个),供重锚定软一致性校验复用,不做重复检测。灰度不入缓存
    # ——frames 本就整段驻留内存,按需 cvtColor 比缓存整个探测池省一个
    # 数量级内存(900 帧 1080p 灰度约 1.7GB,不可接受)。
    cand_feats: Dict[int, Tuple[np.ndarray, np.ndarray, np.ndarray]] = {
        0: (pts0, desc0, near0)}
    if int(near0.sum()) >= strong_need:
        pass  # 首帧已特征丰富,无需探测
    else:
        if int(near0.sum()) >= min_matches:
            first_weak_index = 0
        probe_cap = min(len(frames), _MAX_INIT_PROBE_FRAMES)
        for probe in range(1, probe_cap):
            gray_p = cv2.cvtColor(frames[probe], cv2.COLOR_BGR2GRAY)
            pts_p, desc_p, near_p = _features_near_quad(gray_p)
            count = int(near_p.sum())
            _report(probe + 1)
            if count >= strong_need:
                init_index = probe
                cand_feats[probe] = (pts_p, desc_p, near_p)
                logger.info(
                    "init frame %d has too few features near the quad; "
                    "re-anchored plane tracking on frame %d",
                    start_frame_num, start_frame_num + init_index)
                break
            if count >= min_matches:
                cand_feats[probe] = (pts_p, desc_p, near_p)
                if first_weak_index is None:
                    first_weak_index = probe
            if probe % _INIT_PROBE_LOG_EVERY == 0:
                logger.info(
                    "init re-anchor probe: %d/%d frames scanned, still "
                    "below %d features near the quad",
                    probe, probe_cap, strong_need)
        else:
            # 没有「特征丰富」的帧:退回第一个刚够 min_matches 的帧。
            if first_weak_index is not None:
                init_index = first_weak_index
                logger.info(
                    "no feature-rich frame found; plane tracking anchored on "
                    "frame %d with %d features",
                    start_frame_num + init_index,
                    int(cand_feats[init_index][2].sum()))

    def _lead_in_plane_moved(cand_pts: np.ndarray, cand_desc: np.ndarray) -> bool:
        """frame0 ↔ 候选锚定帧在 region0 内匹配,单应明显偏离单位阵即平面在动。

        匹配不足/单应不可估 → 无证据不判动(软校验:证据不足不拒绝锚定)。
        判动阈值:平移超过 quad 对角线 ~25%,或旋转 >~8°、缩放/剪切各向
        异性 >~15%——引导段内平面自身在动时,候选帧上按 quad0 原坐标锚定
        会整体错位。
        """
        src, dst = _match_points(pts0, desc0, cand_pts, cand_desc,
                                 region0, lowe_ratio)
        if len(src) < min_matches:
            return False
        h_mat, inlier = cv2.findHomography(
            src.reshape(-1, 1, 2).astype(np.float32),
            dst.reshape(-1, 1, 2).astype(np.float32),
            cv2.RANSAC, ransac_thresh)
        # 内点门槛取 min_matches 的一半(下限 4):引导段两帧隔了若干帧,
        # Lowe 配对本就稀疏,按满额 min_matches 要求会普遍校验失败、形同虚设。
        if h_mat is None or inlier is None \
                or int(inlier.sum()) < max(4, min_matches // 2):
            return False
        h_n = h_mat.astype(np.float64) / float(h_mat[2, 2])
        shift = float(np.hypot(h_n[0, 2], h_n[1, 2]))
        aff = h_n[:2, :2]
        ang = abs(math.degrees(math.atan2(float(aff[1, 0]), float(aff[0, 0]))))
        scale = math.sqrt(abs(float(np.linalg.det(aff))))
        sv = np.linalg.svd(aff, compute_uv=False)
        aniso = float(sv[0]) / max(1e-9, float(sv[1]))
        return bool(shift > 0.25 * diag or ang > 8.0
                    or abs(scale - 1.0) > 0.15 or aniso > 1.15)

    # 重锚定软一致性校验:最强候选帧可能落在引导段内平面已移动之后(镜头
    # 推移/内容换页),按 quad0 原坐标锚定会错位。frame0 ↔ 候选帧匹配校验,
    # 判「动」的候选依次退回下一个(first_weak 更靠近 frame0,平面更可能
    # 仍在原位);全部不可信时仍锚最强帧并 warning 留痕——只降级不抛错,
    # 现状能锚定的场景一律不受影响。
    if init_index != 0:
        candidates = [init_index]
        if first_weak_index is not None and first_weak_index != init_index:
            candidates.append(first_weak_index)
        chosen: Optional[int] = None
        for cand in candidates:
            cand_pts, cand_desc = cand_feats[cand][:2]
            if not _lead_in_plane_moved(cand_pts, cand_desc):
                chosen = cand
                break
            logger.info(
                "lead-in consistency check: plane moved between frame %d "
                "and candidate frame %d; falling back",
                start_frame_num, start_frame_num + cand)
        if chosen is None:
            chosen = candidates[0]
            logger.warning(
                "lead-in consistency check failed on all %d candidates; "
                "anchoring on the strongest frame %d anyway",
                len(candidates), start_frame_num + chosen)
        if chosen != init_index:
            init_index = chosen
            logger.info(
                "plane tracking anchored on frame %d after the lead-in "
                "consistency check", start_frame_num + init_index)

    anchor_gray = cv2.cvtColor(frames[init_index], cv2.COLOR_BGR2GRAY)
    anchor_pts, anchor_desc, anchor_near = cand_feats[init_index]
    if int(anchor_near.sum()) < min_matches:
        raise ValueError(
            f"init quad has too few detectable features ({int(anchor_near.sum())} < {min_matches}); "
            "pick a textured plane region"
        )
    ref = _RefState(anchor_gray, anchor_pts, anchor_desc, quad0.copy(),
                    np.eye(3, dtype=np.float64), start_frame_num + init_index)

    tracks: List[TrackedQuad] = []
    for j in range(init_index):
        frame_num = start_frame_num + j
        tracks.append(TrackedQuad(
            frame_num=frame_num,
            time_sec=(times[j] if times else float(frame_num) / (fps or 30.0)),
            status="lost",
        ))
    tracks.append(
        TrackedQuad(
            frame_num=start_frame_num + init_index,
            time_sec=(times[init_index] if times else float(start_frame_num + init_index) / (fps or 30.0)),
            status="ok",
            quad=[[float(v) for v in pt] for pt in quad0],
            homography=_mat_to_list(ref.h_total),
            homography_inv=_mat_to_list(np.linalg.inv(ref.h_total)),
            inlier_ratio=1.0,
            reproj_error=0.0,
            matches=int(anchor_near.sum()),
        )
    )

    _report(init_index + 1)
    for idx in range(init_index + 1, total):
        frame_num = start_frame_num + idx
        time_sec = times[idx] if times else float(frame_num) / (fps or 30.0)
        gray = cv2.cvtColor(frames[idx], cv2.COLOR_BGR2GRAY)
        cur_pts, cur_desc = _detect_features(gray, feature_count)

        ref_diag = float(np.hypot(*(
            ref.quad.reshape(-1, 2).max(axis=0) - ref.quad.reshape(-1, 2).min(axis=0)
        )))
        region = _bbox_expand(ref.quad, max(24.0, SEARCH_PAD_RATIO * ref_diag))
        src, dst = _match_points(ref.pts, ref.desc, cur_pts, cur_desc, region, lowe_ratio)

        ok = False
        inlier_ratio = 0.0
        reproj = float("inf")
        if len(src) >= min_matches:
            h_mat, inlier_mask = cv2.findHomography(
                src.reshape(-1, 1, 2).astype(np.float32),
                dst.reshape(-1, 1, 2).astype(np.float32),
                cv2.RANSAC, ransac_thresh,
            )
            if h_mat is not None and inlier_mask is not None:
                inliers = int(inlier_mask.sum())
                inlier_ratio = inliers / float(len(src))
                if inlier_ratio >= min_inlier_ratio:
                    reproj = _reproj_rmse(src[inlier_mask[:, 0] > 0], dst[inlier_mask[:, 0] > 0], h_mat)
                candidate = cv2.perspectiveTransform(
                    ref.quad.reshape(-1, 1, 2).astype(np.float32), h_mat
                ).reshape(-1, 2).astype(np.float64)

                jumps = np.hypot(
                    candidate[:, 0] - ref.quad[:, 0], candidate[:, 1] - ref.quad[:, 1]
                )
                max_jump = max(JUMP_FLOOR_PX, max_jump_ratio * _mean_edge_length(ref.quad))
                area_ratio = _quad_area(candidate) / max(1e-6, _quad_area(ref.quad))
                ok = bool(
                    np.all(np.isfinite(candidate))
                    and _quad_is_convex(candidate)
                    and reproj <= max_reproj_error
                    and (1.0 - max_area_jump) <= area_ratio <= (1.0 + max_area_jump)
                    and bool((jumps <= max_jump).all())
                )

        if ok:
            h_step = h_mat.astype(np.float64)
            h_total = h_step @ ref.h_total
            ref = _RefState(gray, cur_pts, cur_desc, candidate, h_total, frame_num)
            tracks.append(TrackedQuad(
                frame_num=frame_num,
                time_sec=time_sec,
                status="ok",
                quad=[[float(v) for v in pt] for pt in candidate],
                homography=_mat_to_list(h_total),
                homography_inv=_mat_to_list(np.linalg.inv(h_total)),
                inlier_ratio=float(inlier_ratio),
                reproj_error=float(reproj),
                matches=int(len(src)),
            ))
        else:
            # 保留 ref 状态供后续帧重捕获；本帧不产出轨迹点。
            tracks.append(TrackedQuad(
                frame_num=frame_num,
                time_sec=time_sec,
                status="lost",
                inlier_ratio=float(inlier_ratio),
                reproj_error=float(reproj) if math.isfinite(reproj) else -1.0,
                matches=int(len(src)),
            ))

        _report(idx + 1)

    return tracks


def track_plane(
    video_path: str,
    init_quad: Sequence[Sequence[float]],
    start_frame: int = 0,
    end_frame: Optional[int] = None,
    *,
    fps: float = 0.0,
    progress_cb: Optional[ProgressCB] = None,
    **tracking_params: Any,
) -> List[TrackedQuad]:
    """:func:`track_plane_frames` 的视频入口：顺序解码 [start_frame, end_frame]。

    ``end_frame`` 为闭区间；None 表示读到视频末尾（自动截断到最后一帧）。
    时间戳优先取容器 POS_MSEC，取不到时回退 frame/fps（与 roi_extractor 一致）。
    """
    cap = cv2.VideoCapture(str(video_path))
    if not cap.isOpened():
        raise RuntimeError(f"Cannot open video: {video_path}")
    try:
        total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        if fps <= 0:
            fps = float(cap.get(cv2.CAP_PROP_FPS) or 0.0) or 30.0
        start = max(0, int(start_frame))
        # 容器帧数不可靠（部分 MKV/WebM 报 0 或负值）：此时不把 end 钳到 -1，
        # 显式 end_frame 仍受尊重，否则顺序解码读到 EOF（下方循环 break）。
        end: Optional[int]
        if total > 0:
            end = min(int(end_frame) if end_frame is not None else (total - 1), total - 1)
            if end < start:
                raise ValueError(f"empty frame range: [{start}, {end}]")
        else:
            end = int(end_frame) if end_frame is not None else None
            if end is not None and end < start:
                raise ValueError(f"empty frame range: [{start}, {end}]")

        cap.set(cv2.CAP_PROP_POS_FRAMES, start)
        frames: List[np.ndarray] = []
        times: List[float] = []
        frame_nums = range(start, end + 1) if end is not None else itertools.count(start)
        for frame_num in frame_nums:
            ok, frame = cap.read()
            if not ok or frame is None:
                logger.warning("Plane tracker: cannot decode frame %d; stop at %d.", frame_num, frame_num - 1)
                break
            frames.append(frame)
            try:
                ms = float(cap.get(cv2.CAP_PROP_POS_MSEC))
            except Exception:
                ms = 0.0
            times.append(ms / 1000.0 if ms > 0 else frame_num / fps)
        return track_plane_frames(
            frames, init_quad,
            start_frame_num=start, times=times, fps=fps,
            progress_cb=progress_cb, **tracking_params,
        )
    finally:
        cap.release()


# ---------------------------------------------------------------------------
# 轨迹 JSON 与透视展开
# ---------------------------------------------------------------------------

def save_trajectory(
    path: str,
    tracks: List[TrackedQuad],
    *,
    video_path: str = "",
    init_quad: Optional[Sequence[Sequence[float]]] = None,
    meta: Optional[Dict[str, Any]] = None,
) -> None:
    """轨迹写入 JSON：逐帧四边形 + 正/逆单应 + 质量指标。"""
    import json

    payload = {
        "video": video_path,
        "init_quad": [[float(v) for v in pt] for pt in init_quad] if init_quad is not None else None,
        "meta": dict(meta or {}),
        "frames": [asdict(t) for t in tracks],
    }
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)


def load_trajectory(path: str) -> Dict[str, Any]:
    import json

    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def unwarp_plane(
    frame_bgr: np.ndarray,
    quad: Sequence[Sequence[float]],
    out_size: Tuple[int, int],
) -> np.ndarray:
    """把画面中 ``quad`` 标记的平面透视展开为正视矩形（w×h = out_size）。

    顶点顺序沿用 quad（左上起顺时针 → 输出正向；逆时针 → 输出镜像），
    与用户框选一致，不做自动重排。
    """
    src = np.asarray(quad, dtype=np.float32)
    w, h = int(out_size[0]), int(out_size[1])
    dst = np.array([[0, 0], [w - 1, 0], [w - 1, h - 1], [0, h - 1]], dtype=np.float32)
    h_mat = cv2.getPerspectiveTransform(src, dst)
    return cv2.warpPerspective(frame_bgr, h_mat, (w, h))


def unwarp_canonical(
    frame_bgr: np.ndarray,
    homography_inv: Sequence[Sequence[float]],
    out_size: Tuple[int, int],
) -> np.ndarray:
    """按轨迹里保存的逆单应（当前帧 → 关键帧平面）展开到统一坐标。"""
    w, h = int(out_size[0]), int(out_size[1])
    h_inv = np.asarray(homography_inv, dtype=np.float64)
    if h_inv.shape != (3, 3) or not np.isfinite(h_inv).all() or abs(float(h_inv[2, 2])) < 1e-12:
        # 退化单应（如手改的轨迹 JSON）：h22 归一化会除零产生 NaN，
        # 输出垃圾展开图，宁可显式报错。
        raise ValueError("degenerate homography_inv: cannot unwarp plane")
    h_inv = h_inv / float(h_inv[2, 2])
    return cv2.warpPerspective(frame_bgr, h_inv, (w, h))
