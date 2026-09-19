# core/pose_verify.py
"""行轨迹的屏幕空间实测校正(template matching)。

移动文字轨迹管线假设「行框随跟踪平面单应逐帧映射」——当画面里的文字本身
固定在屏幕坐标(如叠加在移动背景上的固定 UI 面板)时,背景运动会经单应链
漏进行框轨迹,输出字幕跟着漂移/缩放,而真实文字并未移动。本模块用
归一化互相关模板匹配直接测量每行文字在原始帧里的真实位置:

1. 参考补丁取自行轨迹首个 ok 帧该行的实际画面纹理(参考帧位姿恒等:
   center=行框中心、angle=0、scale=1,见 ``build_line_tracks``);
2. 在时间上均匀采样的若干 ok 帧里,以单应预测位置为中心开搜索窗匹配,
   得到实测中心与匹配置信度;
3. 「实测偏移 = 实测中心 − 预测中心」按帧号线性插值成全轨迹偏移场并回贴,
   单应轨迹被锚回真实屏幕位置——背景平移/缩放污染即被消除;
4. 相邻两个采样帧都失配(分数 < ``drop_score``)时判定该跨度文字不可见
   (滚出画面/被遮挡),删除跨度内的位姿(等效 lost,轨迹切链);
5. 全部有效采样收敛在 ``static_tol_px`` 内的行判定为屏幕静止:整条轨迹
   位姿吸附为中位实测中心(角度/缩放取参考帧值),供合成器输出单条
   ``\\pos`` 事件(无 ``\\move``/``\\t``)。

模块只依赖 OpenCV/numpy 与 :class:`core.motion_ass.LineTrack`,不依赖
PySide6;视频解码自包含(顺序单遍,带帧缓存)。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Sequence, Tuple

import numpy as np

from core.motion_ass import LinePose, LineTrack
from core.scene_plane_tracker import TrackedQuad

__all__ = ["VerifyConfig", "verify_line_tracks"]

logger = logging.getLogger(__name__)


@dataclass
class VerifyConfig:
    """实测校正参数(对应 ``MotionAssConfig`` 的 ``verify_*`` 字段)。"""

    sample_max: int = 16            # 每行最多采样的帧数(含首末)
    search_radius_px: float = 40.0  # 匹配搜索半径(预测中心 ± 半径)
    min_score: float = 0.45         # 参与校正的最小匹配置信度
    drop_score: float = 0.30        # 判定文字不可见的置信度
    static_tol_px: float = 2.5      # 屏幕静止判定的最大位移
    min_static_samples: int = 3     # 静止判定所需的最少有效采样数


@dataclass
class LineVerifyReport:
    """单行校正诊断(供日志/测试断言)。"""

    static: bool = False
    corrected: bool = False
    n_samples: int = 0
    n_good: int = 0
    max_dev_px: float = 0.0
    dropped_frames: int = 0
    scores: Dict[int, float] = field(default_factory=dict)


def _decode_frames(video_path: str, wanted: Sequence[int]) -> Dict[int, np.ndarray]:
    """顺序单遍解码指定帧号集合(与 scripts/motion_ass._read_keyframe_frames
    同策略);读不到的帧直接缺席,由调用方按缺帧降级。"""
    import cv2

    want = sorted({int(f) for f in wanted})
    got: Dict[int, np.ndarray] = {}
    cap = cv2.VideoCapture(str(video_path))
    if not cap.isOpened():
        raise RuntimeError(f"Cannot open video: {video_path}")
    try:
        idx = 0
        pos = 0
        while pos < len(want):
            ok, frame = cap.read()
            if not ok or frame is None:
                break
            if idx == want[pos]:
                got[idx] = frame
                pos += 1
            idx += 1
    finally:
        cap.release()
    return got


def _sample_frames(frames: Sequence[int], k: int) -> List[int]:
    """帧号序列的均匀采样(恒含首末),升序返回。"""
    n = len(frames)
    if n <= k:
        return list(frames)
    # 均匀取 k 个下标:i*(n-1)/(k-1),恒含 0 与 n-1
    idx = {round(i * (n - 1) / (k - 1)) for i in range(k)}
    return [frames[i] for i in sorted(idx)]


def _gray_patch(frame: np.ndarray, center: Tuple[float, float],
                width: float, height: float) -> Optional[np.ndarray]:
    """以 center 为中心取 (w×h) 灰度补丁;越界裁剪,过小返回 None。"""
    import cv2

    h, w = frame.shape[:2]
    pw, ph = max(4, int(round(width))), max(4, int(round(height)))
    x0 = int(round(center[0] - pw / 2.0))
    y0 = int(round(center[1] - ph / 2.0))
    x1, y1 = x0 + pw, y0 + ph
    cx0, cy0, cx1, cy1 = max(0, x0), max(0, y0), min(w, x1), min(h, y1)
    if cx1 - cx0 < 4 or cy1 - cy0 < 4:
        return None
    patch = frame[cy0:cy1, cx0:cx1]
    if patch.shape[0] < 4 or patch.shape[1] < 4:
        return None
    gray = cv2.cvtColor(patch, cv2.COLOR_BGR2GRAY)
    return gray


def _is_textured(patch: np.ndarray) -> bool:
    """补丁是否含可定位纹理。

    纯色块(std < 6)拒绝:归一化互相关的相关系数在零方差补丁上无定义,
    会在均匀背景处给出伪满分(scripts/motion_ass CLI 合成用例里的纯黑
    "文字条"即此类)。文字笔画补丁(含抗锯齿/纹理)方差远高于此。
    """
    return float(patch.std()) >= 6.0


def _match_center(frame: np.ndarray, patch: np.ndarray,
                  center: Tuple[float, float], radius: float,
                  patch_size: Tuple[int, int]) -> Tuple[float, float, float]:
    """在 frame 的预测位置邻域内模板匹配,返回 (实测中心 x, y, 置信度)。

    匹配失败(窗口完全越界)返回 (-1, -1, -1)。峰值经 3×3 二次曲面
   细化到亚像素。
    """
    import cv2

    h, w = frame.shape[:2]
    ph, pw = patch.shape[:2]
    win_w = int(min(w, pw + 2 * radius))
    win_h = int(min(h, ph + 2 * radius))
    if win_w < pw + 2 or win_h < ph + 2:
        return (-1.0, -1.0, -1.0)
    x0 = int(round(center[0] - win_w / 2.0))
    y0 = int(round(center[1] - win_h / 2.0))
    # 越界部分镜像填充,保证窗口尺寸恒定、坐标系可换算
    px0, py0 = max(0, -x0), max(0, -y0)
    px1 = max(0, x0 + win_w - w)
    py1 = max(0, y0 + win_h - h)
    cx0, cy0 = max(0, x0), max(0, y0)
    cx1, cy1 = min(w, x0 + win_w), min(h, y0 + win_h)
    if cx1 - cx0 < pw + 2 or cy1 - cy0 < ph + 2:
        return (-1.0, -1.0, -1.0)
    win = frame[cy0:cy1, cx0:cx1]
    if px0 or py0 or px1 or py1:
        win = cv2.copyMakeBorder(
            win, py0, py1, px0, px1, cv2.BORDER_REPLICATE)
    win_gray = cv2.cvtColor(win, cv2.COLOR_BGR2GRAY)
    if win_gray.shape[0] <= ph or win_gray.shape[1] <= pw:
        return (-1.0, -1.0, -1.0)
    res = cv2.matchTemplate(win_gray, patch, cv2.TM_CCOEFF_NORMED)
    # 零方差区域(均匀补丁/均匀窗口)的相关系数无定义,OpenCV 可能输出
    # NaN 或在均匀背景处给出伪满分——归零处理,峰值细化也不外推 NaN。
    res = np.nan_to_num(res, nan=-1.0, posinf=-1.0, neginf=-1.0)
    _, score, _, peak = cv2.minMaxLoc(res)
    rx, ry = float(peak[0]), float(peak[1])
    # 3×3 二次曲面亚像素细化(边界峰不外推)
    if 0 < peak[0] < res.shape[1] - 1 and 0 < peak[1] < res.shape[0] - 1:
        dl, dc, dr = float(res[peak[1], peak[0] - 1]), float(res[peak[1], peak[0]]), float(res[peak[1], peak[0] + 1])
        denom = dl - 2 * dc + dr
        if abs(denom) > 1e-9:
            rx += max(-0.5, min(0.5, 0.5 * (dl - dr) / denom))
        dt, dm, db = float(res[peak[1] - 1, peak[0]]), float(res[peak[1], peak[0]]), float(res[peak[1] + 1, peak[0]])
        denom = dt - 2 * dm + db
        if abs(denom) > 1e-9:
            ry += max(-0.5, min(0.5, 0.5 * (dt - db) / denom))
    # 窗口原点(含镜像填充的坐标回换) + 峰值 + 补丁半宽 = 实测中心
    wx = cx0 - px0
    wy = cy0 - py0
    mx = wx + rx + (pw - 1) / 2.0
    my = wy + ry + (ph - 1) / 2.0
    return (float(mx), float(my), float(score))


def _interp_offsets(sample_data: List[Tuple[int, float, Tuple[float, float]]],
                    frames: Sequence[int]) -> Dict[int, Tuple[float, float]]:
    """按帧号对实测偏移做线性插值;越界侧钳到最近有效采样。

    ``sample_data`` = [(帧号, 分数, (dx, dy)), ...](只含有效采样,升序)。
    """
    out: Dict[int, Tuple[float, float]] = {}
    if not sample_data:
        return out
    xs = [f for f, _s, _d in sample_data]
    for f in frames:
        if f <= xs[0]:
            out[f] = sample_data[0][2]
            continue
        if f >= xs[-1]:
            out[f] = sample_data[-1][2]
            continue
        j = 1
        while xs[j] < f:
            j += 1
        f0, f1 = xs[j - 1], xs[j]
        d0, d1 = sample_data[j - 1][2], sample_data[j][2]
        t = (f - f0) / (f1 - f0) if f1 > f0 else 0.0
        out[f] = (d0[0] + (d1[0] - d0[0]) * t, d0[1] + (d1[1] - d0[1]) * t)
    return out


def verify_line_tracks(
    video_path: str,
    tracks: Sequence[TrackedQuad],
    line_tracks: Sequence[LineTrack],
    cfg: VerifyConfig,
    log: Optional[Callable[[str], None]] = None,
) -> Tuple[List[LineTrack], Dict[int, LineVerifyReport]]:
    """逐行实测校正:返回 (新 LineTrack 列表, 逐行诊断)。

    输入轨迹不修改;单行失败(补丁取不到、有效采样不足)保持该行原轨迹。
    视频打不开抛 :class:`RuntimeError`,由调用方决定降级策略。
    """
    if log is None:
        log = lambda msg: logger.info(msg)  # noqa: E731

    ok_frames = sorted(t.frame_num for t in tracks if t.status == "ok")
    if not ok_frames:
        return list(line_tracks), {}

    wanted: set = set()
    plans: List[Tuple[int, LineTrack, List[int], int]] = []
    for li, lt in enumerate(line_tracks):
        frames = sorted(lt.poses)
        if len(frames) < 3:
            continue
        samples = _sample_frames(frames, max(3, int(cfg.sample_max)))
        ref_ok = frames[0]  # 参考帧位姿恒等(build_line_tracks 约定)
        plans.append((li, lt, samples, ref_ok))
        wanted.update(samples)
        wanted.add(ref_ok)
    if not plans:
        return list(line_tracks), {}

    frames_img = _decode_frames(video_path, wanted)

    reports: Dict[int, LineVerifyReport] = {}
    new_tracks: List[LineTrack] = []
    for li, lt in enumerate(line_tracks):
        plan = next((p for p in plans if p[0] == li), None)
        if plan is None:
            new_tracks.append(lt)
            continue
        _li, _lt, samples, ref_ok = plan
        rep = LineVerifyReport(n_samples=len(samples))
        pose_ref = lt.poses[ref_ok]
        box_w = float(lt.ref_box[2]) - float(lt.ref_box[0])
        box_h = float(lt.ref_box[3]) - float(lt.ref_box[1])
        all_ok_frames = sorted(lt.poses)

        # 自适应补丁:参考帧位姿恒等,但该行的文字未必在参考帧已出现
        # (聊天界面逐条浮现)——按时间顺序尝试各采样帧,取第一个补丁
        # 有纹理的帧作参考;其位姿中心即补丁基准位置(该帧偏移恒为 0,
        # 与「offset = 实测 − 预测」的定义自动一致)。
        patch = None
        patch_frame = None
        patch_center = None
        for f in [ref_ok] + [s for s in samples if s != ref_ok]:
            img = frames_img.get(f)
            if img is None:
                continue
            candidate = _gray_patch(
                img, lt.poses[f].center, box_w, box_h)
            if candidate is not None and _is_textured(candidate):
                patch = candidate
                patch_frame = f
                patch_center = lt.poses[f].center
                break
        if patch is None:
            # 补丁缺纹理(纯色块/细实线条)时归一化互相关无定义,会在
            # 均匀背景处产生系统性伪匹配——宁可不校正该行。
            log(f"pose-verify: line {li} ({lt.text!r}) skipped: "
                "reference patch unavailable or textureless on all samples")
            new_tracks.append(lt)
            reports[li] = rep
            continue

        sample_data: List[Tuple[int, float, Tuple[float, float]]] = []
        for f in samples:
            img = frames_img.get(f)
            if img is None:
                continue
            pred = lt.poses[f].center
            # 窗口中心 = 预测位置 + 此前实测偏移的插值外推(顺序跟踪):
            # 单应声称静止而文字匀速漂移时,漂移会累积超过搜索半径,
            # 纯预测窗口在远端采样点再也够不到真值;带着已测偏移走,
            # 只要相邻采样间的增量不超过半径即可。
            if sample_data:
                priors = _interp_offsets(sample_data, [f])
                dx0, dy0 = priors[f]
                center = (pred[0] + dx0, pred[1] + dy0)
            else:
                center = pred
            mx, my, score = _match_center(
                img, patch, center, cfg.search_radius_px,
                (patch.shape[1], patch.shape[0]))
            rep.scores[f] = score
            if score >= cfg.min_score:
                sample_data.append((f, score, (mx - pred[0], my - pred[1])))
        rep.n_good = len(sample_data)

        # —— 不可见跨度:采样序列中紧邻的两个失配采样之间删除位姿(含两端)。
        # 中间隔着有效采样的一对失配不配对:那是「消失又出现」的可见段。
        dropped: set = set()
        for a, b in zip(samples, samples[1:]):
            if (rep.scores.get(a, -1.0) < cfg.drop_score
                    and rep.scores.get(b, -1.0) < cfg.drop_score):
                for f in all_ok_frames:
                    if a <= f <= b:
                        dropped.add(f)

        keep_frames = [f for f in all_ok_frames if f not in dropped]

        # —— 静止判定:可见性覆盖全程(首末采样有效、无不可见跨度)且
        # 有效采样收敛 → 整条吸附到中位实测中心。
        # 首末采样失配意味着文字只在一段期间可见(聊天滚动浮现/滚出),
        # 吸附成全程常量会在不可见时段渲染幽灵文字;中间个别采样因运动
        # 模糊失配不影响「全程静止」的事实。
        static = False
        full_span = (
            rep.scores.get(samples[0], -1.0) >= cfg.min_score
            and rep.scores.get(samples[-1], -1.0) >= cfg.min_score
        )
        if (full_span and not dropped
                and len(sample_data) >= cfg.min_static_samples):
            centers = np.array([
                (lt.poses[f].center[0] + d[0], lt.poses[f].center[1] + d[1])
                for f, _s, d in sample_data])
            med = np.median(centers, axis=0)
            dev = float(np.max(np.hypot(
                centers[:, 0] - med[0], centers[:, 1] - med[1])))
            rep.max_dev_px = dev
            if dev <= cfg.static_tol_px:
                static = True

        new_poses: Dict[int, LinePose] = {}
        if static:
            med_x = float(np.median([lt.poses[f].center[0] + d[0]
                                     for f, _s, d in sample_data]))
            med_y = float(np.median([lt.poses[f].center[1] + d[1]
                                     for f, _s, d in sample_data]))
            for f in keep_frames:
                new_poses[f] = LinePose(
                    center=(med_x, med_y),
                    angle_deg=pose_ref.angle_deg,
                    scale=pose_ref.scale,
                )
            rep.static = True
            rep.corrected = True
            log(f"pose-verify: line {li} ({lt.text!r}) static at "
                f"({med_x:.1f},{med_y:.1f}), snapped {len(new_poses)} pose(s)")
        elif sample_data:
            offsets = _interp_offsets(sample_data, keep_frames)
            for f in keep_frames:
                p = lt.poses[f]
                dx, dy = offsets.get(f, (0.0, 0.0))
                new_poses[f] = LinePose(
                    center=(p.center[0] + dx, p.center[1] + dy),
                    angle_deg=p.angle_deg,
                    scale=p.scale,
                )
            rep.corrected = any(d != (0.0, 0.0) for d in offsets.values())
            rep.dropped_frames = len(all_ok_frames) - len(keep_frames)
            if rep.corrected or rep.dropped_frames:
                log(f"pose-verify: line {li} ({lt.text!r}) corrected "
                    f"({rep.n_good}/{rep.n_samples} samples), "
                    f"dropped {rep.dropped_frames} invisible frame(s)")
        else:
            new_tracks.append(lt)
            reports[li] = rep
            continue

        new_tracks.append(LineTrack(
            text=lt.text, ref_box=lt.ref_box, height=lt.height,
            poses=new_poses))
        reports[li] = rep
    return new_tracks, reports
