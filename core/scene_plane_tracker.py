# core/scene_plane_tracker.py
"""场景文字平面跟踪：关键帧框选一次，逐帧输出四边形轨迹与单应性。

对应《手机邮件场景文字分析》阶段二「动态平面 ROI」的第一里程碑：

- 用户在关键帧框选文字平面的四个角点（quad），跟踪器用 ORB 特征匹配 +
  RANSAC 单应估计在后续帧中跟随该平面（手机屏幕/信件/招牌）。
- 每帧输出：四边形（原画面坐标）、累计单应（关键帧画面 → 当前帧，含逆）、
  内点率、重投影误差、匹配数与状态。
- 质量门限（匹配数/内点率/重投影误差/相邻帧跳变/面积突变/凸性）不达标的帧
  记为 ``lost``（具体卡在哪一条见 ``TrackedQuad.lost_reason``，ok 帧为 None，
  锚定帧之前的引导段记 ``"pre_anchor"``），并保留最近一个好帧的特征用于后续
  重捕获——失败不外推、
  不产出无证据的轨迹点（手部大面积遮挡时轨迹在遮挡段断开，文字重新可见后
  自动接回）。
- 轨迹可存为 JSON（含正/逆单应），供透视展开 OCR 与后续 ASS 定位使用；
  :func:`unwarp_plane` 把当前帧按四边形展开成正视矩形。

依赖仅 OpenCV（项目现有依赖，Apache-2.0）。遮挡蒙版、ASS 轨迹标签输出与
GUI 四角修正属于阶段二后续项，不在本模块。

两条入口共用同一套跟踪逻辑（见 ``_FrameSource``）：:func:`track_plane_frames`
接收**内存帧列表**（帧本就整段驻留，行为与输出逐字节不变）；
:func:`track_plane` 走**顺序解码流式**——任意时刻只驻留常数帧（当前帧 +
reference 状态 + 候选帧的灰度/特征），峰值内存不随帧范围增长。

:func:`scan_content_windows` 是同一解码纪律下的**只读扫描器**：顺序解码一遍，
逐帧报出「quad 内部有内容」（复用 :func:`_count_features_in_roi` 的计数）
的连续区间。它不跟踪、不写轨迹，供调用方（``scripts/motion_ass`` 的按链
重锚定）判断哪些帧值得另起一趟跟踪——单趟跟踪在内容逐行出现/消失处丢锁
后接不上后续行，而丢锁段之外的帧本身仍有文字。同样只 seek 一次（``start > 0``
时定位到起始帧）、此后只 ``read()``，不跳帧。
"""

from __future__ import annotations

import logging
import math
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
# 锚定帧「quad 内部特征丰富」判定下限(4 × _INIT_STRONG_FEATURES)。实测分离度
# 导出:坏锚定帧的 quad 内部特征数 0-151(空白引导段白屏上的 JPEG 噪声 /
# 歌词行间隙),好锚定帧 1393-2000(聊天 UI / 字幕文字,特征数饱和);192 落在
# 该间隙内且偏保守。仅用作**优先**锚定条件:探测上限内没有帧满足它时,回退到
# 旧规则(首个 region0 特征数 ≥ strong_need 的帧),行为与加入本判据前一致。
_INIT_ROI_MIN_FEATURES = 192
ROI_PAD_PX = 24.0                 # 锚定判据「quad 内部」的外扩像素(与既有下限同值)
DEFAULT_MIN_INLIER_RATIO = 0.35   # RANSAC 内点占匹配对比例下限
DEFAULT_MAX_REPROJ_ERROR = 4.0    # 内点重投影 RMSE 上限（像素）
DEFAULT_MAX_AREA_JUMP = 0.5       # 相邻好帧四边形面积比允许突变幅度
DEFAULT_MAX_JUMP_RATIO = 0.8      # 单帧顶点跳变上限（相对平均边长）
JUMP_FLOOR_PX = 24.0              # 跳变下限，防止小平面被卡死
SEARCH_PAD_RATIO = 0.35           # 匹配源点搜索区外扩（相对 quad bbox 对角线）
MIN_INIT_AREA_PX = 64.0           # 初始四边形最小面积


@dataclass
class TrackedQuad:
    """单帧跟踪结果。``status="lost"`` 时 quad/单应为 None。

    ``lost_reason`` 记录该帧丢失的原因：主循环里是质量门限没过的那一条（见
    ``_track_plane_source`` 的分支顺序），锚定帧之前的引导段是 ``"pre_anchor"``
    （跟踪尚未开始，无门限失败可归因）；``ok`` 帧为 None。仅用于可观测性，
    不参与任何判据；``save_trajectory`` 在值为 None 时省略该键，帧 0 锚定的
    轨迹（无引导段、全 ok）JSON 与加入该字段前逐字节一致。
    """

    frame_num: int
    time_sec: float
    status: str                      # "ok" | "lost"
    quad: Optional[Quad] = None
    homography: Optional[List[List[float]]] = None        # 关键帧 → 当前帧
    homography_inv: Optional[List[List[float]]] = None    # 当前帧 → 关键帧
    inlier_ratio: float = 0.0
    reproj_error: float = 0.0
    matches: int = 0
    lost_reason: Optional[str] = None


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


def _count_features_in_roi(gray: np.ndarray, quad: np.ndarray,
                           feature_count: int) -> int:
    """quad 内部（bbox 外扩 ``ROI_PAD_PX``，裁到画面内）的 ORB 特征**计数**。

    锚定判据专用：与 ``_features_near_quad`` 的 region0（外扩
    ``max(24, 0.35*diag)``，quad 小时面积约为其 3 倍）不同，这里量的是
    *选中的平面本身*有没有纹理——空白白屏上的 JPEG 噪声在 region0 内能凑够
    ``strong_need``，在 quad 内部却近于 0。

    刻意是**另一次**聚焦检测、且只返回计数：锚定帧存下来的
    ``ref.pts/ref.desc`` 必须仍由 ``_features_near_quad`` 产出，保持不变；
    本函数不保留点/描述子，用完即弃。
    """
    mask = np.zeros(gray.shape[:2], np.uint8)
    x1f, y1f, x2f, y2f = _bbox_expand(quad, ROI_PAD_PX)
    x1 = max(0, int(math.floor(x1f)))
    y1 = max(0, int(math.floor(y1f)))
    x2 = min(gray.shape[1], int(math.ceil(x2f)))
    y2 = min(gray.shape[0], int(math.ceil(y2f)))
    if x2 <= x1 or y2 <= y1:
        return 0
    mask[y1:y2, x1:x2] = 255
    pts, _desc = _detect_features(gray, feature_count, mask)
    return int(pts.shape[0])


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


class _FrameSource:
    """``track_plane*`` 内部的帧来源：按**非降索引**取帧 + 取该帧时间戳。

    核心跟踪逻辑只经本接口取帧，于是「整段驻留」只属于入参本就是内存列表的
    调用方：:class:`_SequenceFrameSource` 直接索引原列表；
    :class:`_StreamFrameSource` 顺序解码视频，任意时刻只驻留当前帧。
    """

    __slots__ = ()

    def total(self) -> Optional[int]:
        """期望帧数；None 表示只有读到耗尽才知道（容器帧数不可信且未给终点）。"""
        raise NotImplementedError

    def frame_at(self, index: int) -> Optional[np.ndarray]:
        """第 ``index`` 帧（相对起始帧）；None 表示来源已耗尽/解码失败。"""
        raise NotImplementedError

    def time_at(self, index: int) -> float:
        """第 ``index`` 帧的时间戳（秒），语义与各入口旧实现一致。"""
        raise NotImplementedError

    def reposition(self, index: int) -> None:
        """把下一次 ``frame_at`` 的起点定位到 ``index``（只顺序推进，不 seek）。"""
        raise NotImplementedError


class _SequenceFrameSource(_FrameSource):
    """内存帧列表来源（:func:`track_plane_frames` 入口，行为与旧实现一致）。"""

    __slots__ = ("_frames", "_times", "_fps", "_start_frame_num")

    def __init__(self, frames: Sequence[np.ndarray],
                 times: Optional[Sequence[float]], fps: float,
                 start_frame_num: int):
        self._frames = frames
        self._times = times
        self._fps = fps
        self._start_frame_num = start_frame_num

    def total(self) -> int:
        return len(self._frames)

    def frame_at(self, index: int) -> np.ndarray:
        return self._frames[index]

    def time_at(self, index: int) -> float:
        # 与旧实现同式：显式 times 优先，否则按 start_frame_num + 索引与 fps 推算。
        if self._times:
            return self._times[index]
        return float(self._start_frame_num + index) / (self._fps or 30.0)

    def reposition(self, index: int) -> None:
        return None


class _StreamFrameSource(_FrameSource):
    """视频顺序解码来源：峰值内存与帧范围无关（只驻留当前帧）。

    - 只 ``read``：不随机 seek、不用 ``grab`` 跳帧。``start > 0`` 时打开后
      定位一次（与旧实现相同），``start == 0`` 时连这一次都不需要。
    - :meth:`reposition` 需要回到已解过的帧（初始化探测的 ``first_weak`` 回退：
      锚定帧落在探测终点之前）时，重开解码器从头顺序解码到目标帧——仍不 seek，
      重放长度 ≤ 探测上限 ``_MAX_INIT_PROBE_FRAMES``。
    - 时间戳与 ``read`` 同步取（``POS_MSEC`` 优先，取不到按 ``frame_num/fps``
      推算），与旧实现逐帧一致。
    """

    __slots__ = ("_path", "_start", "_fps", "_expected_total", "_cap", "_next",
                 "_lead_times", "_last_index", "_last_time", "_exhausted",
                 "_warned")

    def __init__(self, cap: cv2.VideoCapture, video_path: str, start: int,
                 fps: float, expected_total: Optional[int]):
        self._path = str(video_path)
        self._start = int(start)
        self._fps = fps
        self._expected_total = expected_total
        self._cap: Optional[cv2.VideoCapture] = cap
        self._next = 0
        # 引导段（索引 < 探测上限）的时间戳：探测回退后重建前导 lost 帧要用。
        # 每帧 8B（900 帧 ~7KB），与「常驻帧数」无关。
        self._lead_times: Dict[int, float] = {}
        self._last_index = -1
        self._last_time = 0.0
        self._exhausted = False
        self._warned = False
        self._position(cap)  # start > 0 时定位到请求的起始帧（旧实现同此）

    # ── 生命周期 ─────────────────────────────────────────────────────
    def close(self) -> None:
        cap, self._cap = self._cap, None
        if cap is not None:
            cap.release()

    def _position(self, cap: cv2.VideoCapture) -> None:
        """解码会话起始定位一次；``start == 0`` 时连这一次都不需要。"""
        if self._start > 0:
            # 唯一一次 set（旧实现同此）：此后只顺序 read，不随机 seek。
            cap.set(cv2.CAP_PROP_POS_FRAMES, self._start)

    def _open(self) -> cv2.VideoCapture:
        cap = cv2.VideoCapture(self._path)
        if not cap.isOpened():
            raise RuntimeError(f"Cannot open video: {self._path}")
        self._position(cap)
        return cap

    # ── _FrameSource ─────────────────────────────────────────────────
    def total(self) -> Optional[int]:
        return self._expected_total

    def frame_at(self, index: int) -> Optional[np.ndarray]:
        if index < self._next:
            raise RuntimeError(
                f"frame source is forward-only: frame {index} requested after "
                f"frame {self._next - 1}; call reposition() first")
        frame: Optional[np.ndarray] = None
        while self._next <= index:
            frame = self._read_next()
            if frame is None:
                return None
        return frame

    def time_at(self, index: int) -> float:
        if index == self._last_index:
            return self._last_time
        try:
            return self._lead_times[index]
        except KeyError:
            # 核心只按顺序取帧：时间戳要么刚随本帧读出，要么在引导段时间表里。
            raise RuntimeError(
                f"no timestamp for frame {index}: frame source is "
                "forward-only") from None

    def reposition(self, index: int) -> None:
        if index < self._next:
            # 回到已解过的帧：重开解码器从头顺序解码（不 seek），丢弃中间帧。
            # 新解码会话重置「本次已读到末帧」，但已发过的 warning 不再重复。
            self.close()
            self._cap = self._open()
            self._next = 0
            self._exhausted = False
        while self._next < index:
            if self._read_next() is None:
                return

    # ── 逐帧解码 ─────────────────────────────────────────────────────
    def _read_next(self) -> Optional[np.ndarray]:
        """顺序读下一帧；None = 解码失败/已到 EOF（warning 每次耗尽只报一次）。"""
        if self._cap is None or self._exhausted:
            return None
        ok, frame = self._cap.read()
        index = self._next
        frame_num = self._start + index
        if not ok or frame is None:
            self._exhausted = True
            if not self._warned:
                self._warned = True
                logger.warning(
                    "Plane tracker: cannot decode frame %d; stop at %d.",
                    frame_num, frame_num - 1)
            return None
        try:
            ms = float(self._cap.get(cv2.CAP_PROP_POS_MSEC))
        except Exception:
            ms = 0.0
        self._last_index = index
        self._last_time = (ms / 1000.0 if ms > 0
                           else frame_num / (self._fps or 30.0))
        self._next = index + 1
        if index < _MAX_INIT_PROBE_FRAMES:
            self._lead_times[index] = self._last_time
        return frame


def _track_plane_source(
    source: _FrameSource,
    init_quad: Sequence[Sequence[float]],
    start_frame_num: int = 0,
    *,
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
    """:func:`track_plane_frames` / :func:`track_plane` 的共同实现。

    帧与时间戳全部经 ``source`` 按索引顺序取（见 :class:`_FrameSource`），
    因此同一份逻辑既能跑内存帧列表，也能跑顺序解码的视频流。
    """
    total_hint = source.total()
    if total_hint is not None and total_hint <= 0:
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
    #
    # 锚定判据在「region0 内特征丰富」之外再加一条「quad 内部特征丰富」
    # (_INIT_ROI_MIN_FEATURES):region0 是 quad bbox 外扩 max(24, 0.35*diag)
    # 的区域,quad 小时面积约为其 3 倍,空白白屏上的 JPEG 噪声就能把计数顶过
    # strong_need 而 quad 内部近于 0(实测 12.mp4 帧 34:region0 61 特征、
    # quad 内部 0;4K 帧 156:50 / 0)——锚在空白上的平面随后必然丢。故把
    # 「两条都满足」的帧作为优先候选 (b),「只满足旧 region0 条件」的帧作为
    # 候选 (a)(= 今天的锚定选择):命中 (b) 就锚 (b),探测上限内没有 (b)
    # 就回退 (a),今天的场景(如 11.mp4/[DMG] 帧 0 两条件都过)完全不变。
    total = total_hint if total_hint is not None else 0
    last_reported = 0

    def _report(done: int) -> None:
        # 进度只进不退:探测期已按帧回调,锚定回退(first_weak)后主循环
        # 会重扫已探测的帧,回调值不得小于已报值。
        nonlocal last_reported
        if progress_cb is not None and done > last_reported:
            last_reported = done
            progress_cb(done, total)

    first_frame = source.frame_at(0)
    if first_frame is None:
        # 来源第一帧就取不到(视频起始帧解码失败):与旧实现「frames 为空」等价。
        return []
    gray0 = cv2.cvtColor(first_frame, cv2.COLOR_BGR2GRAY)
    del first_frame  # 只驻留灰度与候选缓存:BGR 帧用完即弃
    pts0, desc0, near0 = _features_near_quad(gray0)
    _report(1)

    init_index = 0
    strong_need = max(4 * min_matches, _INIT_STRONG_FEATURES)
    first_weak_index: Optional[int] = None
    # 候选锚定帧状态 = (灰度, 点, 描述子, 区内掩码)：只保留**会被读到**的几帧
    # ——优先候选 (b)(region0 与 quad 内部都特征丰富)、候选 (a)(只过旧 region0
    # 条件,探测上限内没有 (b) 时的回退)、以及首个够 min_matches 的帧
    # (first_weak_index,重锚定软一致性校验未过时的回退目标;顺序上它可能早于
    # init_index,故不能只留锚定帧)。其余探测帧即用即弃:若把每个 count ≥
    # min_matches 的探测帧都留特征,最多累积 ~899 份(弱帧点数 < strong_need,
    # 各约 2KB;候选帧上限 feature_count 点、约 100KB),而实际只留上面那几份。
    # 灰度也只随候选帧留一份:锚定帧灰度要在探测结束后用于 ``_RefState``,
    # 内存列表路径下 frames 本就驻留(按需 cvtColor),流式路径下帧已丢弃、
    # 留这一份比重新解码省一趟,且不改语义(同一 cvtColor 输入)。命中 (b) 后
    # 候选 (a) 立即释放(见下),常驻份数与「候选种类数」相关,与探测帧数无关。
    _Candidate = Tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]
    weak_feats: Optional[_Candidate] = None
    strong_feats: Optional[_Candidate] = None
    legacy_feats: Optional[_Candidate] = None
    legacy_index: Optional[int] = None
    strong_found = False
    probe_needed = True
    if int(near0.sum()) >= strong_need:
        # 首帧在 region0 内已特征丰富:再确认 quad 内部也有纹理(候选 (b))。
        # 有则直接锚定首帧、连探测都不进(11.mp4/[DMG] 实测首帧 quad 内部
        # 2000 特征,正是这条路径),锚定帧特征集与加入本判据前逐字节一致。
        if _count_features_in_roi(gray0, quad0, feature_count) >= _INIT_ROI_MIN_FEATURES:
            probe_needed = False
        else:
            # region0 够而 quad 内部几乎空白:旧规则会在此锚定,现留作候选 (a)
            # 继续向后探测候选 (b)。
            legacy_index = 0
            legacy_feats = (gray0, pts0, desc0, near0)
    elif int(near0.sum()) >= min_matches:
        first_weak_index = 0
        weak_feats = (gray0, pts0, desc0, near0)
    if probe_needed:
        probe_cap = _MAX_INIT_PROBE_FRAMES
        if total_hint is not None:
            probe_cap = min(total_hint, _MAX_INIT_PROBE_FRAMES)
        for probe in range(1, probe_cap):
            frame_p = source.frame_at(probe)
            if frame_p is None:
                # 来源提前结束(容器帧数偏大/中途解码失败):探测到此为止,与旧
                # 实现在此处截断 frames(probe_cap 随之变小)等价。
                break
            gray_p = cv2.cvtColor(frame_p, cv2.COLOR_BGR2GRAY)
            del frame_p
            pts_p, desc_p, near_p = _features_near_quad(gray_p)
            count = int(near_p.sum())
            _report(probe + 1)
            if count >= strong_need:
                # 旧规则在此锚定;新规则把它记为候选 (a) 后继续看 quad 内部。
                if legacy_index is None:
                    legacy_index = probe
                    legacy_feats = (gray_p, pts_p, desc_p, near_p)
                # quad 内部判据只对已过旧规则的帧计算:符合本判据的帧本就稀少
                # (12.mp4/4K 实测各 ~10/~15 个),新增检测次数与探测帧数无关。
                if _count_features_in_roi(gray_p, quad0, feature_count) \
                        >= _INIT_ROI_MIN_FEATURES:
                    init_index = probe
                    strong_feats = (gray_p, pts_p, desc_p, near_p)
                    strong_found = True
                    legacy_feats = None  # 候选 (b) 已定,(a) 不再需要
                    logger.info(
                        "init frame %d has too few features inside the quad; "
                        "re-anchored plane tracking on frame %d",
                        start_frame_num, start_frame_num + init_index)
                    break
            elif count >= min_matches and first_weak_index is None:
                # 首个弱候选:留特征供软一致性校验回退,其后弱帧不再留。
                first_weak_index = probe
                weak_feats = (gray_p, pts_p, desc_p, near_p)
            if probe % _INIT_PROBE_LOG_EVERY == 0:
                logger.info(
                    "init re-anchor probe: %d/%d frames scanned, no frame "
                    "with %d+ features near the quad and %d+ inside it yet",
                    probe, probe_cap, strong_need, _INIT_ROI_MIN_FEATURES)
            del gray_p  # 非候选帧的灰度即用即弃(流式下每帧 4K 灰度 ~8MB)
        if not strong_found and legacy_index is not None:
            # 没有帧满足 quad 内部判据:退回候选 (a)——即旧规则的锚定选择,
            # 锚定帧与特征集与修复前一致。
            init_index = legacy_index
            strong_feats = legacy_feats
            logger.info(
                "no frame with enough features inside the quad; plane "
                "tracking anchored on frame %d with %d features (legacy rule)",
                start_frame_num + init_index,
                int(strong_feats[3].sum()))
        elif not strong_found and first_weak_index is not None:
            # 没有「特征丰富」的帧:退回第一个刚够 min_matches 的帧。
            init_index = first_weak_index
            logger.info(
                "no feature-rich frame found; plane tracking anchored on "
                "frame %d with %d features",
                start_frame_num + init_index,
                int(weak_feats[3].sum()))

    # 组装候选状态表:锚定帧(必留)+ 首个弱候选帧(与锚定帧不同时才需要
    # ——同帧时上一条即它自己)。init_index == 0 表示未重锚定,锚定帧就是
    # 首帧;否则锚定帧状态来自探测循环(候选 (b)、候选 (a) 回退或 weak 回退)。
    cand_feats: Dict[int, _Candidate] = {}
    if init_index == 0:
        cand_feats[0] = (gray0, pts0, desc0, near0)
    else:
        cand_feats[init_index] = (strong_feats if strong_feats is not None
                                  else weak_feats)
    if first_weak_index is not None and first_weak_index not in cand_feats:
        cand_feats[first_weak_index] = weak_feats

    def _lead_in_plane_moved(cand_pts: np.ndarray, cand_desc: np.ndarray,
                             cand_frame: int) -> bool:
        """frame0 ↔ 候选锚定帧在 region0 内匹配,单应明显偏离单位阵即平面在动。

        匹配不足/单应不可估 → 无证据不判动(软校验:证据不足不拒绝锚定)。
        单应退化(``h[2,2]`` 为 0/非有限)同属证据不足:RANSAC 内点 ≥4 不保证
        非退化,而按 ``h[2,2]`` 归一化会除零——非零平移项给 inf(方向保守),
        对角线项 0/0 给 NaN;NaN 既会让判据的每个比较恒 False,更会让
        ``np.linalg.svd`` 抛 LinAlgError 打断整条跟踪(实测 h[2,2]=0 与
        NaN 两种退化都抛)。故显式判退化,warning 留痕后返回 False(不判动、
        不拒绝该候选)。
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
        h22 = float(h_mat[2, 2])
        if not math.isfinite(h22) or abs(h22) < 1e-12:
            logger.warning(
                "lead-in consistency check: degenerate homography between "
                "frame %d and candidate frame %d (h[2,2]=%s); treating as "
                "insufficient evidence and keeping the candidate",
                start_frame_num, cand_frame, h22)
            return False
        h_n = h_mat.astype(np.float64) / h22
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
            cand_pts, cand_desc = cand_feats[cand][1:3]
            if not _lead_in_plane_moved(cand_pts, cand_desc,
                                        start_frame_num + cand):
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

    anchor_gray, anchor_pts, anchor_desc, anchor_near = cand_feats[init_index]
    if int(anchor_near.sum()) < min_matches:
        raise ValueError(
            f"init quad has too few detectable features ({int(anchor_near.sum())} < {min_matches}); "
            "pick a textured plane region"
        )
    ref = _RefState(anchor_gray, anchor_pts, anchor_desc, quad0.copy(),
                    np.eye(3, dtype=np.float64), start_frame_num + init_index)
    # 锚定帧已定:候选表(最多两份灰度+特征,锚定帧那份已进 ref)不再被读,
    # 释放之——流式下 4K 灰度 ~8MB/份,不属于「常数帧」预算。
    cand_feats.clear()
    weak_feats = strong_feats = legacy_feats = None

    tracks: List[TrackedQuad] = []
    for j in range(init_index):
        frame_num = start_frame_num + j
        tracks.append(TrackedQuad(
            frame_num=frame_num,
            time_sec=source.time_at(j),
            status="lost",
            # 引导段（锚定帧之前的帧：跟踪尚未开始，没有质量门限失败可归因）。
            # 与主循环的丢帧原因区分开，否则序列化后与 ok 帧同样缺键、下游
            # 直方图会把整段引导段报成「原因未知」。
            lost_reason="pre_anchor",
        ))
    tracks.append(
        TrackedQuad(
            frame_num=start_frame_num + init_index,
            time_sec=source.time_at(init_index),
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
    # 探测回退(first_weak 落在探测终点之前)后来源要回到 init_index+1 继续
    # 解码:内存列表下是 no-op,视频来源重开解码器顺序重放引导段(不 seek)。
    source.reposition(init_index + 1)
    idx = init_index + 1
    while total_hint is None or idx < total_hint:
        frame = source.frame_at(idx)
        if frame is None:
            # 来源耗尽(旧实现在同一位置截断 frames):轨迹到此为止。
            break
        frame_num = start_frame_num + idx
        time_sec = source.time_at(idx)
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        del frame  # 只驻留当前帧:BGR 帧用完即弃(流式下 4K ~25MB/帧)
        cur_pts, cur_desc = _detect_features(gray, feature_count)

        ref_diag = float(np.hypot(*(
            ref.quad.reshape(-1, 2).max(axis=0) - ref.quad.reshape(-1, 2).min(axis=0)
        )))
        region = _bbox_expand(ref.quad, max(24.0, SEARCH_PAD_RATIO * ref_diag))
        src, dst = _match_points(ref.pts, ref.desc, cur_pts, cur_desc, region, lowe_ratio)

        # 质量门限按**固定顺序**逐条判:分支顺序与判据本就有的求值顺序
        # （few_matches → 单应退化 → 内点率 → 有限性 → 凸性 → 重投影 →
        # 面积突变 → 顶点跳变）一致,故 ``ok`` 的取值与旧的一次性布尔表达式
        # 逐位相同;``lost_reason`` 只是把「哪一条没过」记下来。旧式
        # ``and`` 短路在 ``low_inlier`` 处的效果保持不变:内点率不足时不估
        # 重投影(``reproj`` 仍为 inf),该帧恒判 lost(inf > max_reproj_error)。
        ok = False
        lost_reason: Optional[str] = None
        inlier_ratio = 0.0
        reproj = float("inf")
        if len(src) < min_matches:
            lost_reason = "few_matches"
        else:
            h_mat, inlier_mask = cv2.findHomography(
                src.reshape(-1, 1, 2).astype(np.float32),
                dst.reshape(-1, 1, 2).astype(np.float32),
                cv2.RANSAC, ransac_thresh,
            )
            if h_mat is None or inlier_mask is None:
                lost_reason = "degenerate_h"
            else:
                inliers = int(inlier_mask.sum())
                inlier_ratio = inliers / float(len(src))
                # 判据写成 ``not (x >= 阈)`` 而不是 ``x < 阈``:两者对有限值等价,
                # 但对 NaN(NaN 内点率、或用户传 NaN 阈值)不同——旧的一次性
                # ``and`` 表达式在 NaN 下恒判 lost,``<`` 会让 NaN 漏过后续判据。
                if not (inlier_ratio >= min_inlier_ratio):
                    lost_reason = "low_inlier"
                else:
                    reproj = _reproj_rmse(src[inlier_mask[:, 0] > 0], dst[inlier_mask[:, 0] > 0], h_mat)
                candidate = cv2.perspectiveTransform(
                    ref.quad.reshape(-1, 1, 2).astype(np.float32), h_mat
                ).reshape(-1, 2).astype(np.float64)

                jumps = np.hypot(
                    candidate[:, 0] - ref.quad[:, 0], candidate[:, 1] - ref.quad[:, 1]
                )
                max_jump = max(JUMP_FLOOR_PX, max_jump_ratio * _mean_edge_length(ref.quad))
                area_ratio = _quad_area(candidate) / max(1e-6, _quad_area(ref.quad))
                if lost_reason is not None:
                    pass  # 内点率不足:重投影未估(inf),不必再看后四条判据
                elif not np.all(np.isfinite(candidate)):
                    lost_reason = "non_finite"
                elif not _quad_is_convex(candidate):
                    lost_reason = "non_convex"
                elif not (reproj <= max_reproj_error):
                    # 同 ``low_inlier``:``not (<=)`` 让 NaN 重投影判 lost,
                    # 与旧 ``and`` 表达式一致。
                    lost_reason = "high_reproj"
                elif not ((1.0 - max_area_jump) <= area_ratio <= (1.0 + max_area_jump)):
                    lost_reason = "area_jump"
                elif not bool((jumps <= max_jump).all()):
                    lost_reason = "vertex_jump"
                else:
                    ok = True

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
                lost_reason=lost_reason,
            ))

        _report(idx + 1)
        idx += 1

    return tracks


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
    source = _SequenceFrameSource(frames, times, fps, start_frame_num)
    return _track_plane_source(
        source, init_quad, start_frame_num, progress_cb=progress_cb,
        min_matches=min_matches, min_inlier_ratio=min_inlier_ratio,
        max_reproj_error=max_reproj_error, max_area_jump=max_area_jump,
        max_jump_ratio=max_jump_ratio, feature_count=feature_count,
        lowe_ratio=lowe_ratio, ransac_thresh=ransac_thresh,
    )


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

    与 :func:`track_plane_frames` 共用同一套跟踪逻辑，但帧由
    :class:`_StreamFrameSource` 顺序解码按需提供：任意时刻只驻留当前帧与
    候选帧的灰度/特征，峰值内存不随帧范围增长（旧实现把整段帧预载进
    ``List[np.ndarray]``，4K 801 帧约 20GB）。
    ``progress_cb`` 的 ``total`` 取请求区间帧数；容器帧数不可信且未给
    ``end_frame`` 时总数只有读到 EOF 才知道，此时传 0（未知）。
    """
    cap = cv2.VideoCapture(str(video_path))
    if not cap.isOpened():
        raise RuntimeError(f"Cannot open video: {video_path}")
    source: Optional[_StreamFrameSource] = None
    try:
        total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        if fps <= 0:
            fps = float(cap.get(cv2.CAP_PROP_FPS) or 0.0) or 30.0
        start = max(0, int(start_frame))
        # 容器帧数不可靠（部分 MKV/WebM 报 0 或负值）：此时不把 end 钳到 -1，
        # 显式 end_frame 仍受尊重，否则顺序解码读到 EOF（跟踪循环据此 break）。
        end: Optional[int]
        if total > 0:
            end = min(int(end_frame) if end_frame is not None else (total - 1), total - 1)
            if end < start:
                raise ValueError(f"empty frame range: [{start}, {end}]")
        else:
            end = int(end_frame) if end_frame is not None else None
            if end is not None and end < start:
                raise ValueError(f"empty frame range: [{start}, {end}]")

        expected_total = None if end is None else end - start + 1
        source = _StreamFrameSource(cap, str(video_path), start, fps,
                                    expected_total=expected_total)
        return _track_plane_source(
            source, init_quad, start_frame_num=start,
            progress_cb=progress_cb, **tracking_params,
        )
    finally:
        if source is not None:
            source.close()
        cap.release()


def scan_content_windows(
    video_path: str,
    quad: Sequence[Sequence[float]],
    start_frame: int = 0,
    end_frame: Optional[int] = None,
    *,
    feature_count: int = 2000,
    min_features: Optional[int] = None,
    min_window_frames: int = 3,
) -> List[Tuple[int, int]]:
    """顺序解码扫描「quad 内部有内容」的帧区间（绝对帧号，闭区间，升序）。

    解码纪律与 :func:`track_plane` 完全一致（模块 docstring 的不 seek / 不跳帧
    承诺同样适用）：顺序解码 ``[start_frame, end_frame]``，``start_frame > 0``
    时开容器后**定位一次**（``CAP_PROP_POS_FRAMES``，与
    :meth:`_StreamFrameSource._position` 同规矩），此后只 ``read()``——扫描
    中途不 seek、不跳帧。每帧只驻留当前帧的灰度，用完即弃：峰值内存与帧范围
    无关。

    逐帧判据 = :func:`_count_features_in_roi` 的 quad 内部 ORB 计数 ≥
    ``min_features``（缺省 :data:`_INIT_ROI_MIN_FEATURES`）：复用跟踪器
    **同一个**聚焦检测（quad bbox 外扩 :data:`ROI_PAD_PX`、裁到画面内），
    不另起一套检测器，「有内容」与锚定判据的「quad 内部特征丰富」同口径。

    用途（见 ``scripts/motion_ass.run_pipeline``）：一次跟踪在行间空白处丢锁
    后接不上后续行（4K 歌词条实测 ok 31.8%），据此把「内容窗口内但首趟未 ok」
    的帧切段另起轨迹。

    Args:
        video_path: 视频文件路径。
        quad: 4x2 四边形顶点（经 :func:`_validate_init_quad` 校验，与跟踪同一
            入口校验）。
        start_frame: 扫描起始帧（闭区间）。
        end_frame: 扫描结束帧（闭区间）；None 表示读到视频末尾。
        feature_count: ORB 特征检测上限（与跟踪器同一参数口径）。
        min_features: 「有内容」的 quad 内部特征数下限；None 取
            :data:`_INIT_ROI_MIN_FEATURES`。
        min_window_frames: 短于该帧数的连续段丢弃（单帧噪声不构成窗口）。

    Returns:
        ``[(first_frame, last_frame), ...]``：绝对帧号闭区间，升序、长度均
        ≥ ``min_window_frames``；没有任何达标帧时返回 ``[]``（不抛错，帧范围
        为空/无法解码同样只是空结果之外的部分截断）。
    """
    quad0 = _validate_init_quad(quad)
    need = int(_INIT_ROI_MIN_FEATURES if min_features is None
               else min_features)
    min_len = max(1, int(min_window_frames))
    cap = cv2.VideoCapture(str(video_path))
    if not cap.isOpened():
        raise RuntimeError(f"Cannot open video: {video_path}")
    windows: List[Tuple[int, int]] = []
    run_start: Optional[int] = None
    run_end: int = -1
    try:
        total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        start = max(0, int(start_frame))
        # 容器帧数不可信的处理与 track_plane 一致：报 0/负值时不把 end 钳到 -1，
        # 显式 end_frame 仍受尊重，否则顺序解码读到 EOF。
        end: Optional[int]
        if total > 0:
            end = min(int(end_frame) if end_frame is not None else (total - 1),
                      total - 1)
            if end < start:
                raise ValueError(f"empty frame range: [{start}, {end}]")
        else:
            end = int(end_frame) if end_frame is not None else None
            if end is not None and end < start:
                raise ValueError(f"empty frame range: [{start}, {end}]")
        if start > 0:
            # 唯一一次 set：此后只顺序 read（与 _StreamFrameSource._position 同）。
            cap.set(cv2.CAP_PROP_POS_FRAMES, start)
        frame_num = start
        while end is None or frame_num <= end:
            ok, frame = cap.read()
            if not ok or frame is None:
                break
            gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
            del frame  # 只驻留当前帧灰度（4K BGR ~25MB/帧，用完即弃）
            count = _count_features_in_roi(gray, quad0, feature_count)
            del gray
            if count >= need:
                if run_start is None:
                    run_start = frame_num
                run_end = frame_num
            elif run_start is not None:
                windows.append((run_start, run_end))
                run_start = None
            frame_num += 1
        if run_start is not None:
            windows.append((run_start, run_end))
    finally:
        cap.release()
    return [(first, last) for first, last in windows
            if last - first + 1 >= min_len]


# ---------------------------------------------------------------------------
# 轨迹 JSON 与透视展开
# ---------------------------------------------------------------------------

def _frame_dict(track: TrackedQuad) -> Dict[str, Any]:
    """逐帧字典：``lost_reason`` 为 None（ok 帧）时省略该键。

    省略而非写 null：帧 0 锚定（无引导段、全 ok）的轨迹 JSON 与加入该字段前
    逐字节一致。``load_trajectory`` 不需要改动（缺键即视为 None）。
    """
    data = asdict(track)
    if data["lost_reason"] is None:
        data.pop("lost_reason")
    return data


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
        "frames": [_frame_dict(t) for t in tracks],
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
