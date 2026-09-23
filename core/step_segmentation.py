# core/step_segmentation.py
r"""步进/静止体制分段:实测中心 → 逐 hold ``\pos``(换位硬切 + 边界渐隐)。

轨迹管线原先只有两种归宿:全程静止吸附(首末采样可见 + 全部采样收敛在
``static_tol_px`` 内)或跟随校正后轨迹走 DP 分段(``\\move`` 为主)。聊天
界面等「文字显示一段时间 → 瞬间换位 → 再显示一段时间」的内容两头不靠:
文字只在一段期间可见(首末采样必失配,吸附永不触发),而实测又证明显示
期间文字纹丝不动——换位是瞬时的,不该用 ``\\move`` 滑过去。

本模块消费 :class:`core.pose_verify.LineVerifyReport.measured`(逐采样帧
的实测中心,分数 ≥ ``min_score``),把每行的可见时间轴划分为 **hold 段**:

1. 相邻实测中心位移超过换位阈值(``step_jump_px``,0=自动)判「换位」,
   开新 hold;否则并入当前 hold;
2. 每个 hold 验证内部收敛:全部实测中心相对段中位的偏离 ≤
   ``hold_tol_px``。不满足时先做**漂移复核**(段内均匀补采
   ``drift_recheck_samples`` 个帧重测——稀疏采样下模板匹配噪声同样呈现
   为小幅漂移):复核后收敛 → 仍是 hold;仍漂移 → 段内有持续中间速度
   (真正滚动/横移),**整行回退**既有轨迹路径(模糊时偏向 ``\\pos``:
   证据不足不改变输出方式);
3. hold 边界默认取相邻采样帧的中点;有视频时用**定向加密**精化:粗扫
   (``densify_coarse_stride`` 帧距)括出换位区间,再在命中点
   ± ``densify_halfwin_frames`` 内逐帧模板匹配定位首帧(精度 ±1 帧);
   单次换位解码帧数以 ``densify_max_frames`` 封顶,超预算自动放大粗扫
   帧距。采样间横着 ok 帧断口(文字经历过不可见期)的边界不加密——
   断口首帧即边界,视同已确认;
4. 验证通过的行拆成 per-hold 恒位姿 :class:`core.motion_ass.LineTrack`
   (中心 = 段内实测中位、角度/缩放 = 段首帧位姿),交回既有合成器输出
   单条 ``\\pos``/段;静止是 K=1 的特例。内部单样本 hold(过渡中途恰好
   被采到一次)须两侧边界都已确认才放行,否则整行回退。

分段成功后,:func:`attach_step_fades` 给时间上连续的相邻 hold 事件注入
``\\fad`` 渐隐(前一事件尾部渐出、后一事件头部渐入),消除换位瞬间的
位置跳变突兀感;``\\fad`` 对 ``\\t(\\alpha)`` 亮度链是乘法叠加,共存不
冲突。中间隔不可见跨度(时间不连续)的相邻事件不加——文字本来就不在。

fail-open:单行任何一步失败(实测缺失、验证不过、加密异常)都回退该行
原轨迹,不影响其他行,更不中断任务链。
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Sequence, Tuple

import numpy as np

from core.motion_ass import LinePose, LineTrack, MotionAssConfig

logger = logging.getLogger(__name__)

__all__ = [
    "HoldSegment",
    "HoldSplitResult",
    "segment_line_holds",
    "attach_step_fades",
]

# 定向加密的侧别判定:B 位置匹配置信度 ≥ 采纳分(与 verify 同一口径)且
# 显著高于 A 位置才判 B——该行文字在两个位置长得一样,分数接近说明还在
# 过渡(或都没对上),不能判 B。
_SIDE_MARGIN = 0.05
# 侧别匹配的搜索半径(px):hold 中心已由段内实测中位钉死,只需吸收
# 匹配噪声,不需要 verify 那样的 40px 大窗。
_SIDE_RADIUS_PX = 8.0

# 边界请求(阶段一产物,阶段二就地精化,阶段三消费):
#   frame      边界帧号(排他:该帧起文字已在 B 侧;初值=采样帧中点)
#   confirmed  边界是否已确认(可见性断点/相邻采样/加密命中)
#   lo/hi      换位区间两侧的锚定采样帧(仅未确认边界携带)
#   a/b        两侧 hold 的实测中位中心(供补丁提取与侧别匹配)
_Bound = Dict[str, object]

FrameReader = Callable[[Sequence[int]], Dict[int, "np.ndarray"]]


@dataclass
class HoldSegment:
    """单行的一个 hold 段(合成时输出一条 ``\\pos`` 事件)。"""

    start_frame: int                 # 首 ok 帧(含)
    end_frame: int                   # 末 ok 帧(含)
    center: Tuple[float, float]      # 段内实测中位中心(屏幕坐标)
    n_samples: int                   # 段内实测采样数
    bound_confirmed: Tuple[bool, bool]  # (左边界, 右边界)是否已确认


@dataclass
class HoldSplitResult:
    """整批行的分段产物(供合成与事件后处理)。"""

    # 供合成的轨迹列表(fallback/未处理行保持原轨迹对象)。
    tracks: List[LineTrack] = field(default_factory=list)
    # 合成轨迹下标 → 原始行下标(未拆分行是恒等映射的一位)。
    orig_index_of: List[int] = field(default_factory=list)
    # 原始行下标 → hold 段列表(仅分段成功的行;fallback 行不出现)。
    holds_by_line: Dict[int, List[HoldSegment]] = field(default_factory=dict)


def segment_line_holds(
    line_tracks: Sequence[LineTrack],
    reports: Dict[int, object],
    cfg: MotionAssConfig,
    *,
    video_path: Optional[str] = None,
    frame_reader: Optional[FrameReader] = None,
    log: Optional[Callable[[str], None]] = None,
    video_height: Optional[float] = None,
) -> HoldSplitResult:
    """逐行尝试 hold 分段,返回供合成的轨迹列表与行映射。

    ``frame_reader``(帧号集合 → 帧图,缺帧缺席)缺省时从 ``video_path``
    构造(与 pose_verify 同一条顺序解码游标,分批有界);测试可直接注入
    合成帧源。视频两侧参数都缺时边界退回采样帧中点(内部单样本 hold 的
    确认条件随之收紧)。任一行失败只回退该行,绝不抛出中断任务链。
    """
    if log is None:
        def log(message: str) -> None:
            logger.info("step-seg: %s", message)

    if not cfg.hold_pos_segmentation:
        return HoldSplitResult(
            tracks=list(line_tracks),
            orig_index_of=list(range(len(line_tracks))))

    if frame_reader is not None:
        return _run(line_tracks, reports, cfg, frame_reader, log,
                    video_height)
    if video_path:
        from core.pose_verify import (
            _FrameCursor,
            _decode_frames,
            _shared_cursor,
        )

        with _FrameCursor(str(video_path)) as cursor, \
                _shared_cursor(cursor):
            def reader(wanted: Sequence[int], _vp: str = str(video_path)):
                return _decode_frames(_vp, wanted)

            return _run(line_tracks, reports, cfg, reader, log, video_height)
    return _run(line_tracks, reports, cfg, None, log, video_height)


def attach_step_fades(
    events: List[Dict],
    holds_by_line: Dict[int, List[HoldSegment]],
    cfg: MotionAssConfig,
    *,
    times_by_frame: Optional[Dict[int, float]] = None,
) -> int:
    """给相邻 hold 事件的连续边界注入 ``\\fad`` 渐隐,返回注入的边界数。

    ``events`` 的 ``line_idx`` 须已重映射回原始行号。仅处理 hold 数 ≥ 2
    的行。``times_by_frame``(帧号 → time_sec,B2 起生产路径必传)时以**原
    hold 的绝对边界**定位注入点:遮挡离散切片会把一个 hold 拆成多个片段
    事件,只有真正衔接 hold 边界的两个片段获得渐隐,hold 内部的切片边界
    不重新从 0 渐显。缺省(旧调用/直接单测,无切片)按相邻事件结点注入,
    语义与旧版一致。``step_fade_ms ≤ 0`` 时不注入(纯硬切)。
    """
    fade_ms = int(getattr(cfg, "step_fade_ms", 0) or 0)
    if fade_ms <= 0 or not holds_by_line:
        return 0
    by_line: Dict[int, List[Dict]] = {}
    for ev in events:
        li = ev.get("line_idx")
        if isinstance(li, int) and len(holds_by_line.get(li, ())) >= 2:
            by_line.setdefault(li, []).append(ev)

    def _cs(value: str) -> float:
        h, m, rest = str(value).split(":")
        return int(h) * 3600 + int(m) * 60 + float(rest)

    def _boundary_time(hold: HoldSegment) -> Optional[float]:
        if not times_by_frame:
            return None
        f = int(hold.start_frame)
        if f in times_by_frame:
            return float(times_by_frame[f])
        later = [t for fr, t in times_by_frame.items() if fr >= f]
        return min(later) if later else None

    n = 0
    for li in sorted(by_line):
        evs = sorted(by_line[li], key=lambda e: _cs(e["start_time"]))
        if times_by_frame:
            holds = holds_by_line[li]
            for hold in holds[1:]:
                bt = _boundary_time(hold)
                if bt is None:
                    continue
                prev = [e for e in evs
                        if abs(_cs(e["end_time"]) - bt) <= 0.02]
                nxt = [e for e in evs
                       if abs(_cs(e["start_time"]) - bt) <= 0.02]
                if not prev or not nxt:
                    continue
                a = max(prev, key=lambda e: _cs(e["end_time"]))
                b = min(nxt, key=lambda e: _cs(e["start_time"]))
                if a is b or abs(_cs(a["end_time"]) - _cs(b["start_time"])) > 0.02:
                    continue
                a["tags"] += "{\\fad(0,%d)}" % fade_ms
                b["tags"] += "{\\fad(%d,0)}" % fade_ms
                n += 1
            continue
        for a, b in zip(evs, evs[1:]):
            if abs(_cs(a["end_time"]) - _cs(b["start_time"])) > 0.02:
                continue
            a["tags"] += "{\\fad(0,%d)}" % fade_ms
            b["tags"] += "{\\fad(%d,0)}" % fade_ms
            n += 1
    return n


# ---------------------------------------------------------------------------
# 主流程(三阶段:几何分段 → 定向加密 → 拆轨迹)
# ---------------------------------------------------------------------------

def _run(
    line_tracks: Sequence[LineTrack],
    reports: Dict[int, object],
    cfg: MotionAssConfig,
    reader: Optional[FrameReader],
    log: Callable[[str], None],
    video_height: Optional[float],
) -> HoldSplitResult:
    scale = (float(video_height) / 1080.0
             if video_height is not None and float(video_height) > 0 else 1.0)
    hold_tol = float(cfg.hold_tol_px) * scale
    jump_auto = max(3.0 * hold_tol, 8.0 * scale)

    # 每行进入分段流程的 (候选段列表, 边界请求列表, 段内偏差列表);
    # fallback 行不进入。
    plans: Dict[int, Tuple[list, List[_Bound], List[float]]] = {}

    # —— 阶段一:纯几何分段(不碰视频) ——
    for li, lt in enumerate(line_tracks):
        rep = reports.get(li) if reports else None
        measured = getattr(rep, "measured", None) if rep is not None else None
        frames = sorted(lt.poses)
        if not measured or len(frames) < 2:
            continue
        samples = sorted((int(f), (float(c[0]), float(c[1])))
                         for f, c in measured.items())
        runs = _chain_runs(samples, jump_auto, lt.height, hold_tol, cfg)
        devs = [_run_dev(run) for run in runs]
        ok_runs = _contiguous_runs(frames)
        bounds: List[_Bound] = []
        for k in range(len(runs) - 1):
            f_a = runs[k][-1][0]
            f_b = runs[k + 1][0][0]
            center_a = _median_center(runs[k])
            center_b = _median_center(runs[k + 1])
            if f_b <= f_a + 1:
                # 相邻采样帧:换位就发生在两帧之间,边界即 B 侧首采样帧。
                bounds.append({"frame": f_b, "confirmed": True,
                               "intra": True,
                               "a": center_a, "b": center_b})
                continue
            run_b = next((r for r in ok_runs if r[0] <= f_b <= r[1]), None)
            if run_b is not None and f_a >= run_b[0]:
                mid = (f_a + f_b + 1) // 2
                frame = min((f for f in frames if f >= mid), default=f_b)
                bounds.append({"frame": frame, "confirmed": False,
                               "intra": True,
                               "lo": f_a, "hi": f_b,
                               "a": center_a, "b": center_b})
            else:
                # 采样间有 ok 帧断口:文字经历过不可见期,断口首帧即边界。
                bounds.append({"frame": run_b[0] if run_b is not None
                               else f_b,
                               "confirmed": True, "intra": False,
                               "a": center_a, "b": center_b})
        plans[li] = (runs, bounds, devs)
        # 匀速直线判别(滚动签名):段内换位向量方向一致、幅度近似相等——
        # 匀速滚动的采样形态。真步进/聊天翻页的换位方向与幅度是杂乱的,
        # 不受影响。命中即整行回退既有 \move 路径。(跨可见断口的跳变是
        # 天然边界——逐场景出现的文本——不参与判别。)
        intra = [b for b in bounds if b["intra"]]
        if len(intra) >= 3:
            vecs = [np.array([b["b"][0] - b["a"][0],
                              b["b"][1] - b["a"][1]], dtype=np.float64)
                    for b in intra]
            mean = np.mean(vecs, axis=0)
            norm = float(np.linalg.norm(mean))
            if norm > 1e-6:
                tol = max(0.35 * norm, 6.0 * scale)
                if all(float(np.linalg.norm(v - mean)) <= tol
                       and float(np.dot(v, mean)) > 0.0 for v in vecs):
                    log(f"line {li} ({lt.text!r}) fallback: jumps follow "
                        f"a steady linear path (continuous scroll?)")
                    plans.pop(li)
                    continue

    # —— 阶段二:定向加密(全部行的未确认边界合批,顺序解码) ——
    if reader is not None and plans:
        pending = [(li, b) for li, (_r, bs, _d) in plans.items()
                   for b in bs if not b["confirmed"]]
        if pending:
            try:
                _refine_boundaries(line_tracks, pending, reader, cfg, log)
            except RuntimeError as exc:
                log(f"densify failed ({exc}); boundaries keep midpoints")

    # —— 阶段二 1/2:复核补采(① 段内偏差超容差的候选段:段内均匀补采
    #     重测——稀疏采样的匹配噪声常呈现为小幅漂移,复核后收敛仍是
    #     hold;② 样本数不足的短段:邻域补采凑足证据)。两类都以「补采
    #     帧按段中位中心模板匹配、分数达 verify_min_score 才采纳」为准。
    if reader is not None and plans:
        min_hold = max(1, int(cfg.min_hold_samples))
        recheck: List[Tuple[int, int, str]] = []
        for li, (runs, _bs, devs) in plans.items():
            n = len(runs)
            for k, (run, dev) in enumerate(zip(runs, devs)):
                if dev > hold_tol and len(run) > 1:
                    recheck.append((li, k, "drift"))
                elif len(run) < min_hold and 0 < k < n - 1:
                    # 首末段贴着视频首尾(淡入/淡出瞬态区),1 采样是既定
                    # 放行口径,不探测——探测只会把瞬态漂移采进来。
                    recheck.append((li, k, "short"))
        if recheck:
            try:
                _recheck_drift(line_tracks, plans, recheck, reader, cfg,
                               log, hold_tol)
            except RuntimeError as exc:
                log(f"drift recheck failed ({exc}); drifting runs "
                    "keep measured verdicts")

    # —— 阶段三:校验 + 拆轨迹(逐行;失败回退原轨迹) ——
    split_of: Dict[int, List[LineTrack]] = {}
    holds_of: Dict[int, List[HoldSegment]] = {}
    n_split = n_fallback = 0
    for li, lt in enumerate(line_tracks):
        if li not in plans:
            continue
        runs, bounds, devs = plans[li]
        n = len(runs)
        ok = True
        for k, run in enumerate(runs):
            if devs[k] > hold_tol:
                log(f"line {li} ({lt.text!r}) fallback: run drift "
                    f"{devs[k]:.1f}px > hold_tol {hold_tol:.1f}px "
                    f"(continuous motion?)")
                ok = False
                break
            if len(run) >= max(1, int(cfg.min_hold_samples)):
                continue
            left = True if k == 0 else bool(bounds[k - 1]["confirmed"])
            right = True if k == n - 1 else bool(bounds[k]["confirmed"])
            if k not in (0, n - 1) and not (left and right):
                log(f"line {li} ({lt.text!r}) fallback: interior "
                    f"1-sample hold with unconfirmed boundary")
                ok = False
                break
        if not ok:
            n_fallback += 1
            continue
        frames = sorted(lt.poses)
        holds = [_hold_of(run, frames, bounds, k, n)
                 for k, run in enumerate(runs)]
        split = _split_track(lt, holds)
        if split is None:
            n_fallback += 1
            continue
        n_split += 1
        holds_of[li] = holds
        split_of[li] = split
        log(f"line {li} ({lt.text!r}) -> {len(holds)} hold(s) "
            + " ".join(f"[{h.start_frame}-{h.end_frame}]"
                       f"@({h.center[0]:.0f},{h.center[1]:.0f})"
                       for h in holds))

    result = HoldSplitResult()
    for li, lt in enumerate(line_tracks):
        if li in split_of:
            for t in split_of[li]:
                result.tracks.append(t)
                result.orig_index_of.append(li)
        else:
            result.tracks.append(lt)
            result.orig_index_of.append(li)
    result.holds_by_line = holds_of
    if n_split or n_fallback:
        log(f"{n_split} line(s) hold-split, {n_fallback} fallback, "
            f"{len(line_tracks) - n_split - n_fallback} unchanged")
    return result


# ---------------------------------------------------------------------------
# 阶段一:分段几何
# ---------------------------------------------------------------------------

def _chain_runs(
    samples: List[Tuple[int, Tuple[float, float]]],
    jump_auto: float,
    line_h: float,
    hold_tol: float,
    cfg: MotionAssConfig,
) -> List[List[Tuple[int, Tuple[float, float]]]]:
    """按换位阈值把实测样本串成 hold 候选段(不做收敛判定,漂移交由
    漂移复核/阶段三统一处理)。"""
    jump = float(cfg.step_jump_px)
    if jump <= 0:
        jump = max(jump_auto, 0.30 * max(1.0, float(line_h)))
    runs: List[List[Tuple[int, Tuple[float, float]]]] = [[samples[0]]]
    for s in samples[1:]:
        prev = runs[-1][-1][1]
        if math.hypot(s[1][0] - prev[0], s[1][1] - prev[1]) > jump:
            runs.append([s])
        else:
            runs[-1].append(s)
    return runs


def _run_dev(
    run: Sequence[Tuple[int, Tuple[float, float]]],
) -> float:
    """段内实测中心相对段中位的最大偏离(px)。"""
    centers = np.array([c for _f, c in run], dtype=np.float64)
    med = np.median(centers, axis=0)
    return float(np.max(np.hypot(centers[:, 0] - med[0],
                                 centers[:, 1] - med[1])))


def _median_center(
    run: Sequence[Tuple[int, Tuple[float, float]]],
) -> Tuple[float, float]:
    centers = np.array([c for _f, c in run], dtype=np.float64)
    med = np.median(centers, axis=0)
    return (float(med[0]), float(med[1]))


def _contiguous_runs(frames: List[int]) -> List[Tuple[int, int]]:
    """ok 帧号里的连续区间列表(帧号差 > 1 断开)。"""
    runs: List[Tuple[int, int]] = []
    for f in frames:
        if runs and f == runs[-1][1] + 1:
            runs[-1] = (runs[-1][0], f)
        else:
            runs.append((f, f))
    return runs


def _hold_of(
    run: List[Tuple[int, Tuple[float, float]]],
    frames: List[int],
    bounds: List[_Bound],
    k: int,
    n: int,
) -> HoldSegment:
    """候选段 → :class:`HoldSegment`。

    帧范围 = [左边界, 右边界) 钳到 ok 帧——hold 段**划分整条 ok 时间轴**
    (首段从行首 ok 帧、末段到行尾 ok 帧),而不是从段内采样帧起算:换位
    之后、B 侧首个采样之前文字已经在 B 位置,按采样帧起算会让这段落出
    全部 hold、留下无字幕空洞。段的中心/样本数仍只由段内实测样本决定。
    """
    start = int(bounds[k - 1]["frame"]) if k > 0 else frames[0]
    raw_end = (int(bounds[k]["frame"]) - 1) if k < n - 1 else frames[-1]
    left = True if k == 0 else bool(bounds[k - 1]["confirmed"])
    right = True if k == n - 1 else bool(bounds[k]["confirmed"])
    end = max((f for f in frames if start <= f <= raw_end), default=start)
    return HoldSegment(
        start_frame=start,
        end_frame=end,
        center=_median_center(run),
        n_samples=len(run),
        bound_confirmed=(left, right))


def _split_track(
    lt: LineTrack, holds: List[HoldSegment],
) -> Optional[List[LineTrack]]:
    """一行 → per-hold 恒位姿轨迹(角度/缩放取段首帧位姿,中心取实测中位)。

    某 hold 在 ok 帧集合里为空(边界钳制后的病态区间)时返回 None——
    整行回退,不输出半拆分的产物。
    """
    out: List[LineTrack] = []
    for h in holds:
        frames = sorted(f for f in lt.poses
                        if h.start_frame <= f <= h.end_frame)
        if not frames:
            return None
        ref = lt.poses[frames[0]]
        out.append(LineTrack(
            text=lt.text, ref_box=lt.ref_box, height=lt.height,
            poses={f: LinePose(center=h.center, angle_deg=ref.angle_deg,
                               scale=ref.scale) for f in frames}))
    return out


# ---------------------------------------------------------------------------
# 阶段二 1/2:漂移复核(段内补采重测)
# ---------------------------------------------------------------------------

def _recheck_drift(
    line_tracks: Sequence[LineTrack],
    plans: Dict[int, Tuple[list, List[_Bound], List[float]]],
    recheck: List[Tuple[int, int, str]],
    reader: FrameReader,
    cfg: MotionAssConfig,
    log: Callable[[str], None],
    hold_tol: float,
) -> None:
    """段内补采复核,就地更新 runs/devs。

    - ``drift``(段内偏差超容差、多样本):在段采样跨度内均匀补采重测
      ——复核收敛则仍是 hold,仍漂移由阶段三回退整行;
    - ``short``(样本数不足 min_hold_samples):在采样帧 ±
      densify_halfwin_frames 邻域补采凑证据——逐场景出现的文本(时间戳
      等)每个可见期往往只被采到一次,邻域补采把「一瞥」扩成有厚度的
      hold。补采帧按段中位中心做小半径模板匹配,分数达
      ``verify_min_score`` 才采纳(遮挡/运动模糊帧不采纳);采纳后重算
      偏差,超容差照旧由阶段三回退。
    """
    half = max(1, int(cfg.densify_halfwin_frames))
    k_extra = max(0, int(cfg.drift_recheck_samples))
    if k_extra == 0:
        return
    from core.pose_verify import _match_center

    # 待解码帧并集:各段补采帧 + 段首采样帧(提模板)。
    wanted: Dict[Tuple[int, int, str], List[int]] = {}
    anchor_frames: Dict[Tuple[int, int, str], int] = {}
    for li, k, mode in recheck:
        runs, _bs, _devs = plans[li]
        run = runs[k]
        lo, hi = run[0][0], run[-1][0]
        have = {f for f, _c in run}
        if mode == "short":
            w_lo, w_hi = lo - half, hi + half
        else:
            w_lo, w_hi = lo, hi
        span = w_hi - w_lo
        extras: List[int] = []
        if span > 0:
            n = min(k_extra, span - 1)
            if n > 0:
                idx = {round(i * span / (n + 1)) for i in range(1, n + 1)}
                extras = sorted(w_lo + d for d in idx
                                if w_lo + d not in have and w_lo + d >= 0)
        wanted[(li, k, mode)] = extras
        anchor_frames[(li, k, mode)] = lo

    uniq = sorted({f for fs in wanted.values() for f in fs}
                  | set(anchor_frames.values()))
    imgs = _sweep(reader, uniq, max(4, int(cfg.densify_max_frames)))

    for li, k, mode in recheck:
        runs, _bs, devs = plans[li]
        run = runs[k]
        lt = line_tracks[li]
        aframe = anchor_frames[(li, k, mode)]
        patch = _patch(imgs, aframe, _median_center(run), lt)
        if patch is None:
            continue
        center = _median_center(run)
        adopted: List[Tuple[int, Tuple[float, float]]] = []
        for f in wanted[(li, k, mode)]:
            img = imgs.get(f)
            if img is None:
                continue
            mx, my, score = _match_center(
                img, patch, center, _SIDE_RADIUS_PX,
                (patch.shape[1], patch.shape[0]))
            if score >= cfg.verify_min_score:
                adopted.append((f, (float(mx), float(my))))
        if not adopted:
            continue
        merged = sorted(run + adopted)
        new_dev = _run_dev(merged)
        runs[k] = merged
        devs[k] = new_dev
        log(f"recheck[{mode}]: line {li} run {k} +{len(adopted)} sample(s), "
            f"dev -> {new_dev:.1f}px "
            f"({'hold' if new_dev <= hold_tol else 'still drifting'})")


# ---------------------------------------------------------------------------
# 阶段二:定向加密(换位边界的两阶段扫描)
# ---------------------------------------------------------------------------

def _refine_boundaries(
    line_tracks: Sequence[LineTrack],
    pending: List[Tuple[int, _Bound]],
    reader: FrameReader,
    cfg: MotionAssConfig,
    log: Callable[[str], None],
) -> None:
    """就地精化 ``pending`` 里的未确认边界(粗扫括区间 → 逐帧精扫)。

    三次有界顺序解码:两侧锚定帧(提模板)→ 粗扫帧(括区间)→ 精扫帧
    (定边界)。模板提取失败(缺帧/无纹理)的边界保持中点、维持未确认
    (由阶段三的单样本规则决定整行去留)。
    """
    half = max(1, int(cfg.densify_halfwin_frames))
    budget = max(4, int(cfg.densify_max_frames))

    # 0) 两侧锚定帧提模板(A 侧取 lo 帧、B 侧取 hi 帧——锚定采样帧上
    #    该位置必有文字);提取失败的边界直接放弃加密。
    imgs = _sweep(reader,
                  sorted({f for _li, b in pending
                          for f in (int(b["lo"]), int(b["hi"]))}),
                  budget)
    live: List[Tuple[int, int, _Bound]] = []   # (行号, 序号, 边界)
    for seq, (li, b) in enumerate(pending):
        pa = _patch(imgs, int(b["lo"]), b["a"],
                    line_tracks[li])
        pb = _patch(imgs, int(b["hi"]), b["b"],
                    line_tracks[li])
        if pa is not None and pb is not None:
            b["_pa"] = pa
            b["_pb"] = pb
            live.append((li, seq, b))

    # 1) 粗扫帧并集(超预算自动放大帧距,粗扫+精扫解码 ≤ budget/边界)。
    coarse: Dict[int, List[int]] = {}
    for _li, seq, b in live:
        lo, hi = int(b["lo"]) + 1, int(b["hi"]) - 1
        if hi < lo:
            # 阶段一已把相邻采样帧的边界就地确认,这里不会再出现。
            continue
        interval = hi - lo + 1
        stride = max(1, int(cfg.densify_coarse_stride))
        if interval + 2 * half > budget:
            stride = max(stride, -(-interval // max(1, budget - 2 * half)))
        coarse[seq] = list(range(lo, hi + 1, stride))
    if not coarse:
        return
    cimgs = _sweep(reader, sorted({f for fs in coarse.values()
                                   for f in fs}), budget)

    # 2) 粗扫判侧别:升序找首个 B 侧帧,括出精扫区间。
    for _li, _seq, b in live:
        fs = coarse.get(_seq)
        if not fs:
            continue
        hit = None
        for g in fs:
            img = cimgs.get(g)
            if img is not None and _side(img, b, cfg) == "B":
                hit = g
                break
        if hit is not None:
            b["bracket"] = hit

    # 3) 精扫:命中点 ± halfwin 内逐帧,首个 B 侧帧即边界。
    dense: Dict[int, List[int]] = {}
    for _li, seq, b in live:
        g = b.get("bracket")
        if g is None:
            continue
        lo = max(int(b["lo"]) + 1, int(g) - half)
        hi = min(int(b["hi"]) - 1, int(g) + half)
        if hi >= lo:
            dense[seq] = list(range(lo, hi + 1))
    if not dense:
        return
    dimgs = _sweep(reader, sorted({f for fs in dense.values()
                                   for f in fs}), budget)
    for li, _seq, b in live:
        fs = dense.get(_seq)
        if not fs:
            continue
        found = None
        for g in fs:
            img = dimgs.get(g)
            if img is not None and _side(img, b, cfg) == "B":
                found = g
                break
        # 精扫窗口内全是 A 侧(粗扫单帧偶判 B)→ 退回粗扫命中点:边界
        # 精度退化但方向正确。
        b["frame"] = found if found is not None else int(b["bracket"])
        b["confirmed"] = True
        # 边界钳到 ≥ 该帧的最近 ok 帧(合成事件时间取自 ok 帧)。
        frames = sorted(line_tracks[li].poses)
        clamped = min((f for f in frames if f >= int(b["frame"])),
                      default=int(b["hi"]))
        b["frame"] = clamped
        log(f"densify: line {li} boundary -> frame {clamped}")


def _sweep(
    reader: FrameReader, wanted: List[int], block: int,
) -> Dict[int, "np.ndarray"]:
    """升序分批解码(批大小 = block);共享游标下整体一次顺序扫过。"""
    out: Dict[int, "np.ndarray"] = {}
    uniq = sorted({int(f) for f in wanted})
    for i in range(0, len(uniq), max(1, block)):
        out.update(reader(uniq[i:i + block]) or {})
    return out


def _patch(
    imgs: Dict[int, "np.ndarray"], frame: int,
    center: Tuple[float, float], lt: LineTrack,
) -> Optional["np.ndarray"]:
    """锚定帧 + hold 中心 → 灰度补丁(缺帧/无纹理返回 None)。"""
    from core.pose_verify import _gray_patch, _is_textured

    img = imgs.get(int(frame))
    if img is None:
        return None
    w = float(lt.ref_box[2] - lt.ref_box[0])
    h = float(lt.ref_box[3] - lt.ref_box[1])
    patch = _gray_patch(img, center, w, h)
    if patch is None or not _is_textured(patch):
        return None
    return patch


def _side(img: "np.ndarray", b: _Bound, cfg: MotionAssConfig) -> str:
    """单帧侧别判定:B 位置置信度达标且显著高于 A 位置才判 B。"""
    from core.pose_verify import _match_center

    pa, pb = b["_pa"], b["_pb"]
    sb = _match_center(img, pb, b["b"], _SIDE_RADIUS_PX,
                       (pb.shape[1], pb.shape[0]))[2]
    if sb < cfg.verify_min_score:
        return "A"
    sa = _match_center(img, pa, b["a"], _SIDE_RADIUS_PX,
                       (pa.shape[1], pa.shape[0]))[2]
    return "B" if sb - sa >= _SIDE_MARGIN else "A"
