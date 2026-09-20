# core/motion_ass.py
"""移动文字轨迹 → ASS 轨迹字幕合成器(纯函数,不依赖 PySide6)。

对应《移动文字轨迹 → ASS 轨迹字幕 设计》§4.1 / §5:

- :func:`build_line_tracks` —— 参考关键帧平面坐标的行框,经帧间单应链乘
  ``H(ref→t) = H(init→t)·inv(H(init→ref))`` 逐 ok 帧映射为行四边形,
  得到每行逐帧的 ``LinePose``(中心 / 角度 / 缩放);lost 帧无条目。
- :func:`smooth_line_track` —— 滑动平均去抖(窗口只数有 pose 的帧,且不
  跨越长 lost 遮挡段),只去高频抖动、不改缓慢运动趋势。
- :func:`simplify_and_segment` —— 中心序列 Douglas-Peucker 简化得分段点,
  并入角度/缩放显著变化帧,强制最小段长,输出连续无缝的帧号区间。
- :func:`synthesize_events` —— 标签阶梯:连续静止段 → 一条 ``\\pos`` 事件
  (无 ``\\move``/``\\t``;静止段组按组级复核收敛——组内任意帧的锚点距
  组参考锚点 ≤ settle,复核不过另起一组,见 :class:`_StaticGroupCheck`);
  单段直线 → 一条 ``\\move`` 事件;
  多段 → 每段一条 ``\\move`` 事件(边界共享同一格式化时间字符串,
  段内角度/缩放超阈值叠加 ``\\t``);段数爆炸 → 帧级 ``\\pos`` 兜底;
  lost 间隔切段(可选 ``lost_hold_sec`` 保持)。行锚点按
  :mod:`core.text_alignment` 检测的原文对齐选 ``\\an4/\\an5/\\an6``:
  居中块锚行框中心(旧行为),左/右对齐块锚在逐帧跟踪的行框左/右缘中点,
  保持原排版的公共边距。

平滑(阶梯 step 1)由调用方在合成前调用 :func:`smooth_line_track` 完成;
:func:`synthesize_events` 只消费给定的 pose,不修改输入行轨迹。

时间一律用 ``TrackedQuad.time_sec`` 浮点,仅在格式化时转 centisecond
(:func:`format_ass_time` 与主流水线 ``H:MM:SS.CC`` 截断语义一致)。

增量特性「屏幕亮度自适应」:文字平面(手机屏幕)渐变变暗/变亮时,字幕用
``\\t`` 驱动 ``\\1c``(颜色变暗)+ ``\\alpha``(变透明)忠实跟随——
:func:`simplify_luma_curve` 简化 ``core.screen_luma`` 测得的亮度比值折线,
:func:`brightness_tag_chain` 把折线换算成单条事件的局部标签串。
两者均为纯函数,不依赖视频 I/O。
"""

from __future__ import annotations

import math
from bisect import bisect_left, bisect_right
from dataclasses import dataclass
from typing import Dict, List, Optional, Sequence, Tuple

import numpy as np

from core.scene_plane_tracker import TrackedQuad
from core.text_alignment import (
    ALIGN_CENTER,
    ALIGN_LEFT,
    ALIGN_RIGHT,
    detect_line_alignments,
)

__all__ = [
    "MotionAssConfig",
    "LinePose",
    "LineTrack",
    "format_ass_time",
    "homography_between",
    "build_line_tracks",
    "smooth_line_track",
    "simplify_and_segment",
    "synthesize_events",
    "simplify_luma_curve",
    "brightness_tag_chain",
    "punct_comp_offset_px",
]


# ---------------------------------------------------------------------------
# 配置与数据结构
# ---------------------------------------------------------------------------

@dataclass
class MotionAssConfig:
    """合成器参数(默认值见设计 §5.1)。"""

    move_tol_px: float = 2.0        # DP 简化/单段判定容差
    min_seg_frames: int = 3         # 最小段长(帧)
    smooth_window: int = 5          # 滑动平均窗口(帧,只数有 pose 的帧)
    smooth_max_gap: int = 2         # 平滑可跨越的最大帧号间隔(更长 lost 段分段平滑)
    rot_thresh_deg: float = 1.0     # 分段点/±t 叠加的角度阈值
    scale_thresh: float = 0.02      # 缩放变化阈值
    dense_pos_fallback: bool = True
    dense_stride: int = 2           # 兜底 \pos 每 N 好帧一条
    max_segments_per_sec: float = 6.0
    lost_hold_sec: float = 0.0      # lost 保持时长(0=切段)

    # —— 融合行噪声过滤(手机状态栏/导航栏图标误读:<、>、000、单字象形)——
    # 判据与静态路径共用(core.text_utils.is_noise_text),行融合后、建轨迹前剔除。
    junk_line_filter: bool = True

    # —— 屏幕空间实测校正(core.pose_verify;默认开)——
    # 跟踪平面被背景运动污染(文字实际固定在屏幕坐标)时,模板匹配实测
    # 把行轨迹锚回真实屏幕位置;完全静止的行吸附为常量位姿,合成器输出
    # 单条 \pos 事件(无 \move/\t)。
    verify_screen_pose: bool = True
    verify_sample_max: int = 16          # 每行最多采样帧数(含首末)
    verify_search_radius_px: float = 40.0  # 匹配搜索半径(预测中心 ± 半径)
    verify_min_score: float = 0.45       # 参与校正的最小匹配置信度
    verify_drop_score: float = 0.30      # 判定文字不可见的置信度
    verify_static_tol_px: float = 2.5    # 屏幕静止判定容差(px)
    verify_min_static_samples: int = 3   # 静止判定所需的最少有效采样数
    # —— 以下透传 VerifyConfig 的失配跨度证据分级 / 分块解码参数(语义见
    #    core.pose_verify.VerifyConfig 对应字段注释)——
    verify_retry_radius_px: float = 80.0   # 失配采样的扩半径重试搜索半径
    verify_long_span_frames: int = 48      # 失配跨度「超长」判定(帧)
    verify_span_confirm_max: int = 8       # 超长跨度删除前跨内补采确认帧数上限
    verify_block_max_frames: int = 64      # 分块解码单块帧并集上限(≤0 不分块)
    # ROI 包含门控(「校正不得把行移出所属 ROI」,语义见 VerifyConfig.roi_clamp
    # 与 core.pose_verify.verify_line_tracks):实测中心限制在用户手绘 ROI
    # (最小边 10%、至少 4px 外扩)内;4K 固定歌词条实测中,大搜索半径在
    # 条带外锁到背景纹理、把正确位姿改到偏移 115–215px 处并误删可见段
    # ——门控后伪峰被掩膜,ROI 内真值峰照常采纳。False 关闭。
    verify_roi_clamp: bool = True

    # —— 步进/静止体制分段(core.step_segmentation;默认开)——
    # 实测中心(模板匹配)收敛的行按「hold 段」拆分输出逐段 \pos:全程
    # 一段=静止(单条 \pos);段间位置跳变=步进(一拍二/三/四,逐 hold
    # \pos 硬切,内部边界可加 \fad 渐隐);实测呈持续中间速度的行(真正
    # 滚动/横移)不拆分、回退既有轨迹路径(\move)——模糊时偏向 \pos。
    # 需要 verify_screen_pose 开(消费其实测采样);关闭后行为与 2.7.1
    # 一致。逐 hold \pos 的边界由定向加密(换位窗口两阶段扫描)定位。
    hold_pos_segmentation: bool = True
    # hold 段内实测中心相对段中位的最大偏离(px;1080p 基准,按
    # video_height/1080 等比缩放,与 verify_static_tol_px 同口径)。超过
    # 即该段不算 hold:段间中间速度采样意味着连续运动,整行回退。
    hold_tol_px: float = 2.5
    # 相邻实测中心位移超过该值判「换位」开新 hold;0=自动
    # max(3×hold_tol_px, 0.30×行高, 8px×分辨率缩放)。介于 hold 容差与
    # 换位阈值之间的位移(慢漂移/亚字高滑动)不成 hold 也不成换位——但
    # 稀疏采样下模板匹配噪声同样呈现为小幅漂移:超容差段先经**漂移复核**
    # (段内均匀补采重测,见 drift_recheck_samples),复核后仍漂移才整行
    # 回退既有路径。
    step_jump_px: float = 0.0
    # 漂移复核的段内补采帧数:段内实测偏差超 hold_tol_px 的候选段,在其
    # 采样跨度内均匀补采这么多个帧重测(按段中位中心模板匹配,分数达
    # verify_min_score 才采纳);复核后收敛 → 仍是 hold(补采并入该段,
    # 中位中心随之更新);仍漂移 → 整行回退。0=关闭复核(超容差即回退)。
    drift_recheck_samples: int = 6
    # 内部 hold 的最少实测样本数(首/末 hold 贴着可见期端头,允许 1;
    # 内部单样本 hold 须两侧边界都被定向加密确认,否则整行回退)。
    min_hold_samples: int = 2
    # 内部换位边界的 \fad 渐隐时长(ms;0=纯硬切)。前一 hold 事件尾部
    # 渐出、后一 hold 事件头部渐入,消除换位瞬间的位置跳变突兀感;\fad
    # 对 \t(\alpha) 亮度链是乘法叠加,二者共存。仅时间上连续(中间无
    # 不可见跨度)的相邻 hold 事件加,可见期端头不加。
    step_fade_ms: int = 100
    # 定向加密:换位定位的两阶段扫描。粗扫以 densify_coarse_stride 为帧距
    # 括出换位所在区间,再在命中点 ± densify_halfwin_frames 内逐帧精扫
    # (±0.5s@24fps),边界精度 ±1 帧;单次换位解码帧数以 densify_max_frames
    # 封顶(超出时自动放大粗扫帧距)。加密只发生在换位窗口内,成本有界。
    densify_coarse_stride: int = 4
    densify_halfwin_frames: int = 12
    densify_max_frames: int = 96

    # —— 静止链塌缩:链内锚点/角度/缩放全程低于阈值时输出单条 \pos 事件 ——
    collapse_static_chains: bool = True
    # 段内总位移视为静止的感知阈值。以 1080p 为基准的绝对像素,合成时按
    # video_height/1080 等比缩放(2160p→24px、540p→6px;1080p 或
    # video_height 缺省时恰为原值,行为不变)——绝对像素阈值对分辨率敏感:
    # 4K 下相对画面过小(塌缩吸死可感知的小运动),480p 下可能超过字高
    # (塌缩形同虚设);角度/缩放速率阈值(collapse_max_*_rate)是相对量,
    # 不随分辨率缩放。
    collapse_settle_tol_px: float = 12.0
    # 位移低于感知阈值的段还须角度/缩放变化足够慢才塌缩(区分「单应污染
    # 的慢漂移」与「绕行中心的真实缩放/旋转动画」);单位:°/s 与 1/s。
    collapse_max_rot_rate: float = 3.0
    collapse_max_scale_rate: float = 0.10

    # —— 屏幕亮度自适应(增量特性;--auto-brightness 开启,--config-json 可覆盖)——
    brightness_tol: float = 8.0                   # 亮度曲线 DP 简化容差(0-255 亮度级)
    brightness_baseline_percentile: float = 90.0  # 亮度基线分位(各帧中位值的分位)
    brightness_use_color: bool = True             # \1c 颜色跟随
    brightness_use_alpha: bool = True             # \alpha 透明度跟随
    brightness_per_line: bool = False             # 逐行测亮度(屏幕局部调暗的背景适配)

    # —— 行尾全角标点字形补偿(。、等墨迹偏左的标点,渲染墨水整体左偏 ≤7px)——
    punct_comp_enabled: bool = True
    punct_comp_max_px: float = 7.0                # 补偿上限(1080p 基准,随 PlayRes 高度缩放)

    # —— \iclip 手部遮挡蒙版(FR-9;core.occlusion_mask 参数,--occlusion-clip 开启)——
    occlusion_clip: bool = False                  # 部分遮挡跨度内的事件追加 \iclip
    occlusion_diff_tol: float = 26.0              # 展开图灰度差阈值(变化像素判定)
    occlusion_min_area_px: float = 400.0          # 变化区最小面积(平面 px)
    occlusion_min_line_overlap: float = 0.06      # 与行框相交面积占比下限
    occlusion_max_coverage: float = 0.55          # 变化覆盖率上限(超过=调暗/切镜,不判遮挡)
    occlusion_sample_max_frames: int = 7          # 每条事件最多取的遮挡采样帧数
    occlusion_edge_margin_px: int = 8             # 展开图四边忽略带(单应边界采样不稳定)
    occlusion_edge_sliver_max_px: float = 24.0    # 贴边条带判定的最大厚度(px)
    occlusion_edge_sliver_min_frac: float = 0.5   # 条带须覆盖对应边长的比例


@dataclass
class LinePose:
    """单行文字在某帧的几何:中心点、行方向角(度)、长边缩放比。"""

    center: Tuple[float, float]
    angle_deg: float
    scale: float


@dataclass
class LineTrack:
    """单行文字的逐帧轨迹;``poses`` 的 key 为 frame_num,lost 帧无条目。"""

    text: str
    ref_box: tuple                  # 参考帧平面坐标 (x1, y1, x2, y2)
    height: float                   # 行参考高度(y2 - y1),输出 \fs 用
    poses: Dict[int, LinePose]


# ---------------------------------------------------------------------------
# 时间格式化(与主流水线 H:MM:SS.CC 一致)
# ---------------------------------------------------------------------------

def format_ass_time(sec: float) -> str:
    """秒 → ASS ``H:MM:SS.CC``(centisecond 截断,与 generator 时间轴一致)。"""
    if sec < 0:
        sec = 0.0
    # 1e-6 只抵消浮点表示噪声:POS_MSEC/1000 的秒值经常落在真值一个 ULP
    # 之下(如 1.16*100 == 115.99999...),直接截断会把整帧时间戳提前 1cs
    # (与 subtitle_generator/timeline.py 的同源修复保持一致)。
    cs_total = int(float(sec) * 100 + 1e-6)
    h, rem = divmod(cs_total, 360000)
    m, rem = divmod(rem, 6000)
    s, cs = divmod(rem, 100)
    return f"{h}:{m:02d}:{s:02d}.{cs:02d}"


# ---------------------------------------------------------------------------
# 行尾全角标点字形补偿
# ---------------------------------------------------------------------------

# 行尾全角标点(墨迹位于字身框左半、右半留白的标点):渲染排版宽度计入
# 空白半格,libass \an5 按排版盒居中会使墨水整体左偏(2026-09-16 DMG 验收
# 实测:带标点行 dx -6~-7px、无标点行 ≈ -0.5px)。补偿 = 右移半个空白格
# ≈ 0.25×行高,上限随 PlayRes 高度缩放(默认 7px@1080p)。
_TRAILING_BLANK_RIGHT_PUNCT = frozenset("。，、．,・")


def punct_comp_offset_px(
    text: str,
    fs_px: float,
    *,
    enabled: bool = True,
    max_px: float = 7.0,
    video_height: Optional[float] = None,
) -> float:
    """行尾全角标点的字形度量补偿量(x 向右移,画面 px);无标点返回 0。

    ``fs_px`` 为行高(≈字号,``\\fs`` 同单位);补偿量 = ``min(上限, 0.25×行高)``,
    上限 = ``max_px``×(video_height/1080)(PlayRes 非空时按高度等比缩放,
    1080p 时恰为 ``max_px``)。
    """
    if not enabled:
        return 0.0
    stripped = str(text or "").strip()
    if not stripped or stripped[-1] not in _TRAILING_BLANK_RIGHT_PUNCT:
        return 0.0
    fs_px = float(fs_px or 0.0)
    if fs_px <= 0:
        return 0.0
    cap = float(max_px)
    if video_height is not None and float(video_height) > 0:
        cap *= float(video_height) / 1080.0
    return float(min(cap, 0.25 * fs_px))


# ---------------------------------------------------------------------------
# 帧间单应
# ---------------------------------------------------------------------------

def _frame_map(tracks: Sequence[TrackedQuad]) -> Dict[int, TrackedQuad]:
    return {t.frame_num: t for t in tracks}


def _homography_between(
    hmap: Dict[int, TrackedQuad], frame_a: int, frame_b: int,
) -> np.ndarray:
    for f in (frame_a, frame_b):
        tq = hmap.get(f)
        if tq is None or tq.status != "ok" or tq.homography is None:
            raise KeyError(f"frame {f} is lost or missing; no homography available")
    h_a = np.asarray(hmap[frame_a].homography, dtype=np.float64)
    h_b = np.asarray(hmap[frame_b].homography, dtype=np.float64)
    # H(a→b) = H(init→b) · inv(H(init→a))
    return h_b @ np.linalg.inv(h_a)


def homography_between(tracks: Sequence[TrackedQuad], frame_a: int, frame_b: int) -> np.ndarray:
    """帧 a 平面坐标 → 帧 b 画面的单应;端点 lost/不存在时抛 :class:`KeyError`。"""
    return _homography_between(_frame_map(tracks), frame_a, frame_b)


def _nearest_ok_frame(hmap: Dict[int, TrackedQuad], ref_frame: int) -> int:
    best: Tuple[int, int] | None = None  # (距离, 帧号),并列取更早帧
    for f, tq in hmap.items():
        if tq.status != "ok" or tq.homography is None:
            continue
        key = (abs(f - ref_frame), f)
        if best is None or key < best:
            best = key
    if best is None:
        raise KeyError("no ok frame in tracks; cannot anchor line reference")
    return best[0]


def _apply_homography(pts: np.ndarray, h_mat: np.ndarray) -> np.ndarray:
    hom = np.hstack([pts, np.ones((len(pts), 1))])
    proj = hom @ h_mat.T
    w = proj[:, 2:3]
    if not np.all(np.isfinite(proj)) or np.any(np.abs(w) < 1e-12):
        raise ValueError("degenerate homography while mapping line corners")
    return proj[:, :2] / w


# ---------------------------------------------------------------------------
# 行轨迹重建
# ---------------------------------------------------------------------------

def _pose_from_quad(mapped: np.ndarray, ref_long_edge: float, horizontal: bool) -> LinePose:
    center = mapped.mean(axis=0)
    edge = mapped[1] - mapped[0] if horizontal else mapped[2] - mapped[1]
    length = float(math.hypot(float(edge[0]), float(edge[1])))
    angle = math.degrees(math.atan2(float(edge[1]), float(edge[0])))
    scale = length / ref_long_edge if ref_long_edge > 1e-6 else 1.0
    return LinePose(
        center=(float(center[0]), float(center[1])),
        angle_deg=angle,
        scale=scale,
    )


def build_line_tracks(
    line_boxes: Sequence[Sequence[float]],
    texts: Sequence[str],
    tracks: Sequence[TrackedQuad],
    ref_frame: int,
) -> List[LineTrack]:
    """参考帧平面坐标的行框 → 逐 ok 帧的 :class:`LineTrack`。

    ``line_boxes`` / ``texts`` 为参考关键帧平面坐标系下的行框与文本,
    行顺序保持。``ref_frame`` 若为 lost,则把参考重锚定到最近的 ok 帧
    (并列取更早帧)后再参与逐帧映射;lost 帧不产出 pose。
    """
    if len(line_boxes) != len(texts):
        raise ValueError("line_boxes and texts must have the same length")
    hmap = _frame_map(tracks)
    ref = ref_frame
    ref_tq = hmap.get(ref_frame)
    if ref_tq is None or ref_tq.status != "ok" or ref_tq.homography is None:
        ref = _nearest_ok_frame(hmap, ref_frame)

    out: List[LineTrack] = []
    for box, text in zip(line_boxes, texts):
        x1, y1, x2, y2 = (float(v) for v in box)
        corners = np.array(
            [[x1, y1], [x2, y1], [x2, y2], [x1, y2]], dtype=np.float64)
        horizontal = abs(x2 - x1) >= abs(y2 - y1)
        long_edge = max(abs(x2 - x1), abs(y2 - y1))
        poses: Dict[int, LinePose] = {}
        for tq in tracks:
            if tq.status != "ok" or tq.homography is None:
                continue  # lost 帧无条目
            h_mat = _homography_between(hmap, ref, tq.frame_num)
            mapped = _apply_homography(corners, h_mat)
            poses[tq.frame_num] = _pose_from_quad(mapped, long_edge, horizontal)
        out.append(LineTrack(
            text=str(text),
            ref_box=(x1, y1, x2, y2),
            height=float(y2 - y1),
            poses=poses,
        ))
    return out


# ---------------------------------------------------------------------------
# 平滑
# ---------------------------------------------------------------------------

def smooth_line_track(track: LineTrack, window: int = 5, max_gap: int = 2) -> None:
    """原地滑动平均 center/angle/scale;窗口(帧)只数有 pose 的帧。

    中心窗口 ``[f - window//2, f + window//2]`` 内的缺失(lost)帧自然跳过;
    角度先按序列展开(unwrap)再平均,避免 ±180° 边界跳变。窗口 <2 时不变。
    相邻 OK 帧号差 > ``max_gap`` 视为长遮挡段:平滑窗口不跨越(按连续 OK
    run 分段平滑),两侧姿态各自平均;短间隙(默认 ≤2 帧)保持旧行为可跨越
    (窗口半径内的缺失帧跳过后两侧仍落入同一窗口)。
    """
    if window is None or window < 2 or len(track.poses) < 2:
        return
    frames = sorted(track.poses)
    radius = max(1, int(window) // 2)
    max_gap = max(0, int(max_gap))
    angles = np.unwrap(np.deg2rad(
        [track.poses[f].angle_deg for f in frames]))
    xs = np.array([track.poses[f].center[0] for f in frames])
    ys = np.array([track.poses[f].center[1] for f in frames])
    scales = np.array([track.poses[f].scale for f in frames])

    # 连续 OK run(帧号差 > max_gap 处切段):每个窗口限制在自己的 run 内
    n = len(frames)
    run_lo = [0] * n
    run_hi = [0] * n
    rl = 0
    for i in range(n):
        if i and frames[i] - frames[i - 1] > max_gap:
            rl = i
        run_lo[i] = rl
    rr = n - 1
    for i in range(n - 1, -1, -1):
        if i < n - 1 and frames[i + 1] - frames[i] > max_gap:
            rr = i
        run_hi[i] = rr

    new_poses: Dict[int, LinePose] = {}
    for i, f in enumerate(frames):
        lo = bisect_left(frames, f - radius, run_lo[i], run_hi[i] + 1)
        hi = bisect_right(frames, f + radius, run_lo[i], run_hi[i] + 1)
        new_poses[f] = LinePose(
            center=(float(xs[lo:hi].mean()), float(ys[lo:hi].mean())),
            angle_deg=float(math.degrees(angles[lo:hi].mean())),
            scale=float(scales[lo:hi].mean()),
        )
    track.poses = new_poses


# ---------------------------------------------------------------------------
# 分段(Douglas-Peucker + 角度/缩放显著点 + 最小段长)
# ---------------------------------------------------------------------------

def _ang_diff(a: float, b: float) -> float:
    """两角度的最小绝对差(处理 ±180° 环绕),单位度。"""
    return abs((float(a) - float(b) + 180.0) % 360.0 - 180.0)


def _dp_breakpoints(pts: np.ndarray, tol: float) -> List[int]:
    """中心序列的 Douglas-Peucker 简化,返回保留点下标(含首末)。"""
    n = len(pts)
    if n < 3:
        return list(range(n))
    keep = np.zeros(n, dtype=bool)
    keep[0] = keep[n - 1] = True
    stack = [(0, n - 1)]
    while stack:
        i, j = stack.pop()
        if j - i < 2:
            continue
        a = pts[i]
        seg = pts[j] - a
        seg_len = math.hypot(float(seg[0]), float(seg[1]))
        rel = pts[i + 1:j] - a
        if seg_len < 1e-9:
            dist = np.hypot(rel[:, 0], rel[:, 1])
        else:
            dist = np.abs(seg[0] * rel[:, 1] - seg[1] * rel[:, 0]) / seg_len
        k = int(np.argmax(dist))
        if float(dist[k]) > tol:
            keep[i + 1 + k] = True
            stack.append((i, i + 1 + k))
            stack.append((i + 1 + k, j))
    return [idx for idx in range(n) if keep[idx]]


def _split_runs(frames: List[int]) -> List[List[int]]:
    """按帧号连续性切分(帧号跳变 >1 视为 lost 间隔)。"""
    runs: List[List[int]] = []
    cur: List[int] = []
    prev: int | None = None
    for f in frames:
        if prev is not None and f - prev > 1:
            runs.append(cur)
            cur = []
        cur.append(f)
        prev = f
    if cur:
        runs.append(cur)
    return runs


def _raw_segments(
    centers: np.ndarray,
    angles: Sequence[float],
    scales: Sequence[float],
    cfg: MotionAssConfig,
) -> List[Tuple[int, int]]:
    """DP 简化 + 角度/缩放显著点 → 原始分段(未强制最小段长)。"""
    n = len(centers)
    if n == 0:
        return []
    if n == 1:
        return [(0, 0)]
    bps = set(_dp_breakpoints(centers, cfg.move_tol_px))
    bps.add(0)
    bps.add(n - 1)
    for idx in range(1, n):
        if (_ang_diff(angles[idx], angles[idx - 1]) >= cfg.rot_thresh_deg
                or abs(scales[idx] - scales[idx - 1]) >= cfg.scale_thresh):
            bps.add(idx)  # 显著变化落在边界帧上,突变恰好在段间发生
    order = sorted(bps)
    return [(order[k], order[k + 1]) for k in range(len(order) - 1)]


def _enforce_min_len(
    segs: List[Tuple[int, int]], min_len: int,
) -> List[Tuple[int, int]]:
    """强制最小段长:最短段并入前邻段(无前邻则并入后邻),直至全部达标。"""
    segs = list(segs)
    min_len = max(1, int(min_len))
    while len(segs) > 1:
        lengths = [b - a + 1 for a, b in segs]
        si = min(range(len(segs)), key=lambda k: lengths[k])
        if lengths[si] >= min_len:
            break
        if si > 0:
            a0, _ = segs[si - 1]
            _, b1 = segs[si]
            segs[si - 1:si + 1] = [(a0, b1)]
        else:
            _, b1 = segs[1]
            segs[0:2] = [(segs[0][0], b1)]
    return segs


def _segment_indices(
    centers: np.ndarray,
    angles: Sequence[float],
    scales: Sequence[float],
    cfg: MotionAssConfig,
) -> Tuple[List[Tuple[int, int]], List[Tuple[int, int]]]:
    """单条链分段,返回 (原始分段, 强制最小段长后的分段)。

    段数爆炸判定(spec §5「混乱手持抖动会使段数爆炸」)基于原始 DP 分段;
    最小段长只用于整理 ``\\move`` 链的输出事件。
    """
    raw = _raw_segments(centers, angles, scales, cfg)
    return raw, _enforce_min_len(raw, cfg.min_seg_frames)


def simplify_and_segment(
    track: LineTrack, cfg: MotionAssConfig,
) -> List[Tuple[int, int]]:
    """行轨迹 → 帧号闭区间分段列表(连续无缝;lost 间隔处切开)。"""
    frames = sorted(track.poses)
    out: List[Tuple[int, int]] = []
    for run in _split_runs(frames):
        centers = np.array([track.poses[f].center for f in run], dtype=np.float64)
        angles = [track.poses[f].angle_deg for f in run]
        scales = [track.poses[f].scale for f in run]
        _, final_segs = _segment_indices(centers, angles, scales, cfg)
        for a, b in final_segs:
            out.append((run[a], run[b]))
    return out


# ---------------------------------------------------------------------------
# 合成(标签阶梯)
# ---------------------------------------------------------------------------

def _fmt1(v: float) -> str:
    v = float(v)
    if v == 0.0:
        v = 0.0  # 归一 -0.0
    return f"{v:.1f}"


def _fmt2(v: float) -> str:
    v = float(v)
    if v == 0.0:
        v = 0.0
    return f"{v:.2f}"


def _seg_ms(t0: float, t1: float) -> int:
    return max(0, int(round((float(t1) - float(t0)) * 1000)))


def _overlay_tags(
    angle0: float, angle1: float,
    scale0: float, scale1: float,
    t0: float, t1: float,
    cfg: MotionAssConfig,
) -> str:
    """段起止角度/缩放超阈值时生成「基值 + \\t」叠加标签段。"""
    rot = _ang_diff(angle0, angle1) >= cfg.rot_thresh_deg
    scl = abs(float(scale0) - float(scale1)) >= cfg.scale_thresh
    if not (rot or scl):
        return ""
    base = ""
    target = ""
    if rot:
        base += f"\\frz{_fmt2(angle0)}"
        target += f"\\frz{_fmt2(angle1)}"
    if scl:
        p0, p1 = float(scale0) * 100.0, float(scale1) * 100.0
        base += f"\\fscx{_fmt1(p0)}\\fscy{_fmt1(p0)}"
        target += f"\\fscx{_fmt1(p1)}\\fscy{_fmt1(p1)}"
    return f"{base}\\t(0,{_seg_ms(t0, t1)},{target})"


def _merge_chains_with_hold(
    runs: List[List[int]],
    tmap: Dict[int, TrackedQuad],
    hold_sec: float,
) -> List[List[int]]:
    """``lost_hold_sec`` > 0 时,间隔时长 ≤ hold 的相邻链合并为一条
    (事件跨越 lost 间隔,期间保持前段轨迹)。"""
    if hold_sec <= 0 or len(runs) < 2:
        return runs
    merged: List[List[int]] = [list(runs[0])]
    for run in runs[1:]:
        prev = merged[-1]
        gap = tmap[run[0]].time_sec - tmap[prev[-1]].time_sec
        if gap <= hold_sec + 1e-9:
            merged[-1] = prev + run
        else:
            merged.append(list(run))
    return merged


def _is_exploded(
    chain: List[int],
    segs: List[Tuple[int, int]],
    tmap: Dict[int, TrackedQuad],
    cfg: MotionAssConfig,
) -> bool:
    span = chain[segs[-1][1]] - chain[segs[0][0]] + 1
    if span / len(segs) < 2.0 * cfg.min_seg_frames:
        return True
    dur = tmap[chain[segs[-1][1]]].time_sec - tmap[chain[segs[0][0]]].time_sec
    per_sec = len(segs) / dur if dur > 1e-9 else float("inf")
    return per_sec > cfg.max_segments_per_sec


def _next_frame_time(
    tmap: Dict[int, TrackedQuad], frame: int, t0: float, dt: float,
) -> float:
    """链尾兜底:取轨迹里下一帧(任意状态)的时间,否则按中位帧距顺延。"""
    for f in range(frame + 1, frame + 32):
        tq = tmap.get(f)
        if tq is not None and tq.time_sec > t0:
            return tq.time_sec
    return t0 + dt


_AN_BY_ALIGN = {ALIGN_LEFT: 4, ALIGN_CENTER: 5, ALIGN_RIGHT: 6}


def _anchored_center(
    center: Tuple[float, float],
    angle_deg: float,
    scale: float,
    box_w: float,
    align: str,
    x_off: float,
) -> Tuple[float, float]:
    """事件锚点屏幕坐标(按行对齐方式,配 ``\\an4/\\an5/\\an6``)。

    居中 = 行框中心(旧行为);左/右 = 中心沿行方向(角度)回退/前进
    半个框宽——即逐帧跟踪的行框左/右缘中点,替换文本的公共边距钉在原
    排版边缘,渲染宽度差异向行尾方向消化。行尾标点补偿(x_off)与左/右
    锚点同沿行方向分解:``(x_off·cos(ang), x_off·sin(ang))``——补偿修正
    的是行尾标点墨迹在**文本行方向**上的偏移,文本随 ``\\frz`` 旋转后行
    方向 ≠ 屏幕 x 轴,按屏幕 x 平移在大倾角下会欠/过补偿(旧近似 ≤7px,
    斜边行距误差按角度放大)。居中分支保持既有屏幕 x 平移(补偿量 ≤7px,
    且不与行方向锚点耦合,居中块输出与旧版逐字节一致)。
    """
    if align == ALIGN_CENTER:
        return (center[0] + x_off, center[1])
    half = float(box_w) * float(scale) / 2.0
    ang = math.radians(float(angle_deg))
    sign = -1.0 if align == ALIGN_LEFT else 1.0
    along = sign * half + x_off  # 行方向上的有向偏移:半框宽 + 标点补偿
    return (center[0] + along * math.cos(ang),
            center[1] + along * math.sin(ang))


def _dense_events(
    chain: List[int],
    centers: np.ndarray,
    tmap: Dict[int, TrackedQuad],
    cfg: MotionAssConfig,
    style: str,
    fs_h: int,
    text: str,
    x_offset: float = 0.0,
    video_height: Optional[float] = None,
    align: str = ALIGN_CENTER,
    box_w: float = 0.0,
    angles: Optional[Sequence[float]] = None,
    scales: Optional[Sequence[float]] = None,
) -> List[Dict]:
    """帧级 ``\\pos`` 兜底:每隔 dense_stride 个好帧一条事件,
    时间取该帧 time_sec 到下一取样好帧 time_sec。"""
    n = len(chain)
    stride = max(1, int(cfg.dense_stride))
    times = [tmap[f].time_sec for f in chain]
    if n >= 2:
        dts = sorted(b - a for a, b in zip(times, times[1:]))
        dt = dts[len(dts) // 2]
    else:
        dt = 0.0
    if not x_offset:
        x_offset = punct_comp_offset_px(
            text, fs_h, enabled=cfg.punct_comp_enabled,
            max_px=cfg.punct_comp_max_px, video_height=video_height)
    an = _AN_BY_ALIGN.get(align, 5)
    if angles is None:
        angles = [0.0] * n
    if scales is None:
        scales = [1.0] * n
    events: List[Dict] = []
    for k in range(0, n, stride):
        f0 = chain[k]
        t0 = times[k]
        t1 = times[min(k + stride, n - 1)]
        if t1 <= t0:
            t1 = _next_frame_time(tmap, f0, t0, dt)
        if t1 <= t0:
            continue  # 零长段丢弃
        ax, ay = _anchored_center(centers[k], angles[k], scales[k], box_w,
                                  align, x_offset)
        tags = (f"{{\\an{an}\\fs{fs_h}"
                f"\\pos({_fmt1(ax)},{_fmt1(ay)})}}")
        events.append({
            "start_time": format_ass_time(t0),
            "end_time": format_ass_time(t1),
            "style": style,
            "name": "motion",
            "tags": tags,
            "body": text,
        })
    return events


@dataclass
class _StaticGroupCheck:
    """静止塌缩的**组级**复核状态(``_chain_events`` 阶梯 0 内部用)。

    段级 ``_seg_static`` 只保证「单段首末锚点距离 ≤ settle」:同向的相邻
    静止段串成一组后组内累计位移无上界——右移 10px 一段 + 下移 10px 一段
    (每段各 ≤ 12px)会让组终点距组参考锚点 14.1px,而组事件只输出一个
    ``\\pos``(错位反而超过 settle)。故候选静止段并入组前必须按**组**复核:

    1. 组内每一帧的锚点相对组参考锚点(组首段首帧)的距离 ≤ settle;
    2. 组内角度相对参考帧的最大偏离 / 整组跨度时长 ≤ rot_rate;
    3. 组内缩放极差 / 整组跨度时长 ≤ scale_rate。

    复核不过则另起一个静止组(参考锚点 = 该段首帧锚点),保持「文字不动
    就塌缩成单条 ``\\pos``」的语义——不退回 ``\\move``,否则大量微小抖动
    的静态文字会退化成海量 ``\\move`` 事件。

    本结构增量维护组内极值(每帧至多算一次,失败不改状态),整条链的
    复核代价 O(帧数)。
    """

    ref_ai: int          # 组参考帧(组首段首帧)在链内的下标:组 \pos 用其锚点
    hi: int              # 已复核到的链内帧下标(闭区间,含)
    max_move: float      # 组内锚点相对参考锚点的最大距离(px)
    max_ang: float       # 组内角度相对参考帧角度的最大偏离(度)
    scale_lo: float      # 组内缩放最小值
    scale_hi: float      # 组内缩放最大值

    @classmethod
    def start(cls, scales: Sequence[float], ref_ai: int) -> "_StaticGroupCheck":
        """以链内帧 ``ref_ai`` 为参考锚点开一个静止组。"""
        return cls(ref_ai=int(ref_ai), hi=int(ref_ai), max_move=0.0,
                   max_ang=0.0, scale_lo=float(scales[ref_ai]),
                   scale_hi=float(scales[ref_ai]))

    def try_extend(
        self,
        anchors: Sequence[Tuple[float, float]],
        angles: Sequence[float],
        scales: Sequence[float],
        chain: Sequence[int],
        tmap: Dict[int, TrackedQuad],
        bi: int,
        *,
        settle: float,
        rot_rate: float,
        scale_rate: float,
    ) -> bool:
        """试把链内帧 ``(hi, bi]`` 并入本组;通过则原地更新并返回 True。

        失败(锚点越界/角或缩放速率超限)时不改动任何状态,调用方另起
        新组。``hi`` 恒为同组前一段的末帧,段间连续无缝,故 ``(hi, bi]``
        恰为待并入段的帧(不含其首帧——该帧已在组内复核过)。
        """
        x0, y0 = anchors[self.ref_ai]
        a0 = angles[self.ref_ai]
        max_move = self.max_move
        max_ang = self.max_ang
        lo, hi_s = self.scale_lo, self.scale_hi
        for k in range(self.hi + 1, bi + 1):
            ax, ay = anchors[k]
            dist = math.hypot(float(ax) - float(x0), float(ay) - float(y0))
            if dist > settle:
                return False  # 组内任一帧越界即不并(不变式:≤ settle)
            if dist > max_move:
                max_move = dist
            ang = _ang_diff(angles[k], a0)
            if ang > max_ang:
                max_ang = ang
            sc = float(scales[k])
            if sc < lo:
                lo = sc
            if sc > hi_s:
                hi_s = sc
        dur = tmap[chain[bi]].time_sec - tmap[chain[self.ref_ai]].time_sec
        if dur > 1e-6:
            # 速率按**整组跨度**重算(参照帧 = 组参考帧),而不是复用段级
            # 结论:段时长连续叠加、缩放区间在段交界处共享端点,正时长段
            # 的段级速率合格确实能推出组级合格;真正依赖这道防线的是
            # 「段级速率被跳过」的段(_seg_static 在段时长 ≤ 1e-6 时直接
            # 判静止、不评估速率,见其 docstring 的早退分支)——组级复核
            # 是唯一能拦住这类段把整组带偏的地方。
            if max_ang / dur > rot_rate:
                return False
            if (hi_s - lo) / dur > scale_rate:
                return False
        self.hi = max(self.hi, int(bi))
        self.max_move = max_move
        self.max_ang = max_ang
        self.scale_lo, self.scale_hi = lo, hi_s
        return True


@dataclass
class _CollapseGroup:
    """``_chain_events`` 阶梯 0 的相邻段分组:连续静止段合成单条 ``\\pos`` 事件。"""

    static: bool
    sis: List[int]                               # 组成员段下标(segs 下标,升序)
    check: Optional[_StaticGroupCheck] = None    # 静止组的组级复核状态


def _chain_events(
    lt: LineTrack,
    chain: List[int],
    tmap: Dict[int, TrackedQuad],
    cfg: MotionAssConfig,
    style: str,
    video_height: Optional[float] = None,
    align: str = ALIGN_CENTER,
) -> List[Dict]:
    centers = np.array([lt.poses[f].center for f in chain], dtype=np.float64)
    angles = [lt.poses[f].angle_deg for f in chain]
    scales = [lt.poses[f].scale for f in chain]
    raw_segs, segs = _segment_indices(centers, angles, scales, cfg)
    if not segs:
        return []
    fs_h = int(round(lt.height))
    # 行尾全角标点字形补偿:渲染墨水整体左偏 ≤7px(见 punct_comp_offset_px),
    # x 端点统一右移补偿(mask 遮罩由 scene_text_policy 对同一偏移同步外扩)。
    x_off = punct_comp_offset_px(
        lt.text, fs_h, enabled=cfg.punct_comp_enabled,
        max_px=cfg.punct_comp_max_px, video_height=video_height)
    # 块对齐方式(左/中/右)→ 锚点 \an4/\an5/\an6:锚在逐帧跟踪的行框
    # 左/右缘中点,保持原排版的公共边距(居中块保持锚中心,旧行为)。
    an = _AN_BY_ALIGN.get(align, 5)
    box_w = float(lt.ref_box[2]) - float(lt.ref_box[0])
    anchors = [
        _anchored_center(centers[k], angles[k], scales[k], box_w, align, x_off)
        for k in range(len(chain))]

    # 阶梯 5:段数爆炸 → 帧级 \pos 兜底(按原始 DP 分段判定;单段永不触发)
    if cfg.dense_pos_fallback and len(raw_segs) >= 2 and _is_exploded(chain, raw_segs, tmap, cfg):
        return _dense_events(chain, centers, tmap, cfg, style, fs_h, lt.text,
                             x_offset=x_off, video_height=video_height,
                             align=align, box_w=box_w, angles=angles,
                             scales=scales)

    # 阶梯 0(段级):连续静止段合并为单条 \pos 事件(见下方 groups 循环)。

    events: List[Dict] = []
    multi = len(segs) > 1
    last = len(segs) - 1
    # 链尾(含单段)结束时间延伸到下一帧:ASS 的 End 是排他边界,取尾帧自身
    # 的 time_sec 会让最后一个 ok 帧整帧无字幕;与 dense 兜底/主流水线
    # generator 的「尾帧时间 + 1 帧距」语义对齐。
    chain_times = [tmap[f].time_sec for f in chain]
    if len(chain_times) >= 2:
        chain_dts = sorted(b - a for a, b in zip(chain_times, chain_times[1:]))
        chain_dt = chain_dts[len(chain_dts) // 2]
    else:
        chain_dt = 0.0

    # 静止塌缩的位移阈值 settle(段级与组级共用):感知阈值按分辨率归一
    # (video_height/1080,见 _seg_static docstring),再取 move_tol_px 地板
    # (地板不缩放,与 DP 分段同单位)。
    settle = float(cfg.collapse_settle_tol_px)
    if video_height is not None and float(video_height) > 0:
        settle *= float(video_height) / 1080.0
    settle = max(settle, float(cfg.move_tol_px))

    def _seg_static(ai: int, bi: int) -> bool:
        """**段级**判据:段内整体位移低于感知阈值、且角度/缩放变化足够慢。

        位移判据用段首末锚点距离,这只是「段内总位移」的下界近似:DP 只
        保证简化后段内**中心点**偏离弦 ≤ move_tol(锚点还受逐帧角度/缩放
        影响,见 :func:`_anchored_center`),强制最小段长的合并还会让个别
        段的内点偏离弦更远,而本函数从不逐帧检查。因此单段合格**不**蕴含
        组内合格——同向相邻静止段串成一组后组内累计位移无上界(每段各移
        10px 的两段组终点错位 14.1px)。组级安全性由
        :meth:`_StaticGroupCheck.try_extend` 逐帧复核(组内每一帧锚点
        相对组参考锚点 ≤ settle),本函数只做便宜的段级预筛。

        实测校正只回贴中心,单应的角度/缩放污染仍会让长时间静止的行留有
        数像素的慢漂移与假 ``\\t`` 变化——变化速率低于感知阈值的按静止
        处理(塌缩后这些假 ``\\t`` 一并消失);绕行中心的真实缩放/旋转
        动画(位移≈0 但角/秒、缩放/秒高)不受影响。

        位移阈值按分辨率归一(以 1080p 为基准):12px 是绝对像素,4K 下
        相对画面过小、低分辨率下可能接近字高,感知项按 video_height/1080
        等比缩放(video_height=1080 或缺省时恰为配置原值,与旧行为一致)。
        move_tol_px 地板项不随分辨率缩放:max 的语义是「塌缩阈值不得低于
        DP 分段容差」(段内位移 ≤ move_tol 的链不该因阈值配置过小被判
        运动),而 DP 分段仍按绝对 move_tol_px 进行,地板须与分段同单位;
        若一并缩放,低分辨率下 settle 会跌破 move_tol,这道防线失效。
        """
        ax, ay = anchors[ai]
        bx, by = anchors[bi]
        if math.hypot(bx - ax, by - ay) > settle:
            return False
        dur = tmap[chain[bi]].time_sec - tmap[chain[ai]].time_sec
        if dur <= 1e-6:
            return True
        ang_range = max(_ang_diff(a, angles[ai]) for a in angles[ai:bi + 1])
        if ang_range / dur > cfg.collapse_max_rot_rate:
            return False
        scale_range = max(scales[ai:bi + 1]) - min(scales[ai:bi + 1])
        return scale_range / dur <= cfg.collapse_max_scale_rate

    # 相邻段按「静止/运动」分组:连续静止段合并成一条 \pos 事件(文字
    # 不移动就不需要 \move/\t);运动段逐段输出 \move(± \t)。
    # 段级 _seg_static 只保证单段位移 ≤ settle,同向多段串成组的累计位移
    # 无上界(L 形:右移 10px + 下移 10px 两段各 ≤ settle,组终点错位
    # 14.1px > settle)——并入前一静止组前逐帧复核组内锚点跨度;复核不过
    # 则另起一个静止组(参考锚点 = 该段首帧锚点),不退回 \move。
    # 单段自成一组仍越界(段级判据的 DP 前提被最小段长合并破坏)时按运动
    # 段输出 \move:几何上该段确有不可忽略的位移,塌缩即错位。
    groups: List[_CollapseGroup] = []
    for si, (ai, bi) in enumerate(segs):
        static_seg = bool(cfg.collapse_static_chains and _seg_static(ai, bi))
        if static_seg and groups and groups[-1].static:
            group_check = groups[-1].check
            if group_check is not None and group_check.try_extend(
                    anchors, angles, scales, chain, tmap, bi,
                    settle=settle, rot_rate=cfg.collapse_max_rot_rate,
                    scale_rate=cfg.collapse_max_scale_rate):
                groups[-1].sis.append(si)
                continue
        candidate = _StaticGroupCheck.start(scales, ai)
        if static_seg and candidate.try_extend(
                anchors, angles, scales, chain, tmap, bi,
                settle=settle, rot_rate=cfg.collapse_max_rot_rate,
                scale_rate=cfg.collapse_max_scale_rate):
            groups.append(_CollapseGroup(static=True, sis=[si],
                                         check=candidate))
            continue
        groups.append(_CollapseGroup(static=False, sis=[si]))

    for gi, grp in enumerate(groups):
        static_group = grp.static
        sis = list(grp.sis)
        is_last_group = gi == len(groups) - 1
        first_ai = segs[sis[0]][0]
        last_bi = segs[sis[-1]][1]
        # 组边界 = 段边界:非链尾组的结束帧与下一组首段共享(排他边界);
        # 链尾组延伸一帧,末帧不落空。
        start_f = chain[first_ai]
        end_f = chain[last_bi] if is_last_group else chain[segs[sis[-1] + 1][0]]

        if static_group:
            t0 = tmap[start_f].time_sec
            t1 = tmap[end_f].time_sec
            if is_last_group:
                t1 = _next_frame_time(tmap, end_f, t1, chain_dt)
            if t1 <= t0:
                continue
            # 组 \pos 锚点 = 组级复核的参考锚点:静止组恒以组首段首帧起组,
            # 故 ref_ai == segs[sis[0]][0] == first_ai,复核已保证组内每一帧
            # 锚点距它 ≤ settle(塌缩不变式)。
            ax, ay = anchors[first_ai]
            events.append({
                "start_time": format_ass_time(t0),
                "end_time": format_ass_time(t1),
                "style": style,
                "name": "motion",
                "tags": f"{{\\an{an}\\fs{fs_h}\\pos({_fmt1(ax)},{_fmt1(ay)})}}",
                "body": lt.text,
            })
            continue

        for si in sis:
            ai, bi = segs[si]
            f0 = chain[ai]
            # 组内非末段与下一段共享边界帧;组末段结束于组边界。
            f1 = (chain[segs[si + 1][0]]
                  if si < sis[-1] else end_f)
            t0 = tmap[f0].time_sec
            t1 = tmap[f1].time_sec
            if is_last_group and si == last:
                t1 = _next_frame_time(tmap, f1, t1, chain_dt)
            if t1 <= t0:
                continue  # 零长段丢弃
            if multi or len(sis) > 1:
                move = (f"\\move({_fmt1(anchors[ai][0])},{_fmt1(anchors[ai][1])},"
                        f"{_fmt1(anchors[bi][0])},{_fmt1(anchors[bi][1])})")
            else:
                move = (f"\\move({_fmt1(anchors[ai][0])},{_fmt1(anchors[ai][1])},"
                        f"{_fmt1(anchors[bi][0])},{_fmt1(anchors[bi][1])},"
                        f"0,{_seg_ms(t0, t1)})")
            tags = f"{{\\an{an}\\fs{fs_h}{move}"
            tags += _overlay_tags(angles[ai], angles[bi], scales[ai], scales[bi],
                                  t0, t1, cfg)
            tags += "}"
            events.append({
                "start_time": format_ass_time(t0),
                "end_time": format_ass_time(t1),
                "style": style,
                "name": "motion",
                "tags": tags,
                "body": lt.text,
            })
    return events


def synthesize_events(
    line_tracks: Sequence[LineTrack],
    tracks: Sequence[TrackedQuad],
    cfg: MotionAssConfig,
    style: str = "Scene",
    *,
    video_height: Optional[float] = None,
    diagnostics: Optional[Dict[str, object]] = None,
    align_boxes: Optional[Sequence[tuple]] = None,
    line_align_index: Optional[Sequence[int]] = None,
) -> List[Dict]:
    """行轨迹 + 平面跟踪轨迹 → ASS 事件列表(标签阶梯)。

    每行独立处理:pose 按 lost 间隔切链(``lost_hold_sec`` > 0 时短间隔
    合并跨越);每条链先分段(单段直线/多段 ``\\move``),段数爆炸时整条
    链改帧级 ``\\pos`` 兜底。事件按行分组、行内按时间排序;不修改输入。
    ``video_height``(PlayResY)供分辨率相关阈值以 1080p 为基准等比缩放:
    行尾标点补偿上限(缺省按 ``punct_comp_max_px`` 原值封顶)与静止链
    塌缩位移阈值(缺省按 ``collapse_settle_tol_px`` 原值判定,行为与
    1080p 一致);角度/缩放变化速率阈值为相对量,不缩放。事件携带
    ``line_idx``(行下标,行序同输入),供逐行亮度适配等调用方回溯所属
    行;写入 .ass 时忽略。

    行锚点按原文对齐检测(:func:`core.text_alignment.detect_line_alignments`,
    全组逐行边缘贴合投票):居中块 ``\\an5`` 锚中心(旧行为),
    左/右对齐块 ``\\an4``/``\\an6`` 锚在逐帧跟踪的行框左/右缘中点——替换
    字体的公共边距钉在原排版边缘,不再整块「居中化」。``diagnostics``
    (仅关键字,可选)传入 dict 时带出逐行判定结果(行 idx → 对齐边 +
    三边票数 + 剪切斜率),供真实视频排查,不影响事件输出。

    步进/静止分段(:mod:`core.step_segmentation`)把一行拆成多条恒位姿
    轨迹输入时,对齐投票仍须在**原始行集合**上进行(拆分会复制行框,扰
    动全组投票与剪切去趋势):``align_boxes`` 提供投票输入行框(缺省用输
    入轨迹自身的 ref_box,行为不变),``line_align_index`` 给出输入轨迹
    下标 → 投票行下标的映射(缺省恒等)。``line_idx`` 仍是输入轨迹下标,
    由调用方负责重映射回原始行号。
    """
    tmap = _frame_map(tracks)
    events: List[Dict] = []
    # 行框是 quad 展开平面坐标:手选 quad 偏差会让竖直 UI 列随 y 漂移,
    # 投票前先估计全局剪切斜率去趋势(detrend_shear=True)。
    vote_boxes = ([tuple(b) for b in align_boxes] if align_boxes is not None
                  else [lt.ref_box for lt in line_tracks])
    aligns = detect_line_alignments(vote_boxes,
                                    detrend_shear=True,
                                    diagnostics=diagnostics)
    for line_idx, lt in enumerate(line_tracks):
        frames = sorted(lt.poses)
        if not frames:
            continue
        arow = (line_align_index[line_idx]
                if line_align_index is not None else line_idx)
        for chain in _merge_chains_with_hold(
                _split_runs(frames), tmap, cfg.lost_hold_sec):
            for ev in _chain_events(lt, chain, tmap, cfg, style,
                                    video_height=video_height,
                                    align=aligns[arow]):
                ev["line_idx"] = line_idx
                events.append(ev)
    return events


# ---------------------------------------------------------------------------
# 屏幕亮度自适应(增量特性;曲线来自 core.screen_luma,纯函数不碰视频 I/O)
# ---------------------------------------------------------------------------

def _interp_ratio(ts: Sequence[float], rs: Sequence[float], t: float) -> float:
    """折线在时刻 t 的插值比值;越界侧钳到端点(不外推)。"""
    if t <= ts[0]:
        return rs[0]
    if t >= ts[-1]:
        return rs[-1]
    idx = bisect_left(ts, t)
    t1, t2 = ts[idx - 1], ts[idx]
    r1, r2 = rs[idx - 1], rs[idx]
    if t2 <= t1 + 1e-12:
        return r1
    return r1 + (r2 - r1) * (t - t1) / (t2 - t1)


def simplify_luma_curve(
    curve: List[Tuple[float, float]],
    tol_luma: float = 8.0,
    baseline_luma: float = 247.0,
) -> List[Tuple[float, float]]:
    """对 ``(time, ratio)`` 亮度折线做 Douglas-Peucker 简化。

    容差由亮度级换算:``tol_ratio = tol_luma / baseline_luma``;偏差按
    「还原亮度后」的值偏差计(``luma = baseline * ratio``,等价于比值空间内
    对段的垂直偏差),与时间轴尺度无关。首末点恒保留,容差内的过渡抖动
    舍弃、拐点保留;输入按 time 升序,不修改输入。
    """
    out = [(float(t), float(r)) for t, r in curve]
    n = len(out)
    if n < 3:
        return out
    ts = [t for t, _r in out]
    rs = [r for _t, r in out]
    tol = abs(float(tol_luma)) / abs(float(baseline_luma)) if baseline_luma else 0.0
    keep = [False] * n
    keep[0] = keep[-1] = True
    stack = [(0, n - 1)]
    while stack:
        i, j = stack.pop()
        if j - i < 2:
            continue
        span = ts[j] - ts[i]
        k, dmax = -1, -1.0
        for m in range(i + 1, j):
            r_line = (rs[i] + (rs[j] - rs[i]) * (ts[m] - ts[i]) / span
                      if span > 1e-12 else rs[i])
            d = abs(rs[m] - r_line)
            if d > dmax:
                k, dmax = m, d
        if k >= 0 and dmax > tol:
            keep[k] = True
            stack.append((i, k))
            stack.append((k, j))
    return [out[idx] for idx in range(n) if keep[idx]]


def brightness_tag_chain(
    curve: List[Tuple[float, float]],
    ev_start: float,
    ev_end: float,
    *,
    use_color: bool = True,
    use_alpha: bool = True,
    base_color: Optional[Tuple[int, int, int]] = None,
) -> str:
    """把全局亮度关键帧折线换算成一条事件的局部标签串(不含最外层大括号)。

    - 事件起点处插值得到 r0:基值标签 ``\\1c&H..&``(缺省 ``base_color`` 时
      灰度 g=round(255·r0),三通道同值;给定 BGR ``base_color`` 时按通道
      缩放 ``round(c·r0)``)+ ``\\alpha&H..&``(a=round(255·(1-r0)),00=不透明);
    - 对每一段落在 ``(ev_start, ev_end)`` 内的相邻关键帧区间 [t1,t2] 各输出
      一个 ``\\t(ms1,ms2,\\1c..\\alpha..)``,ms 相对事件开始取整,相邻端点
      相接;区间与事件跨度求交集,交集 <1ms 丢弃;段目标值 = 段终点时间的
      插值比值;``use_color``/``use_alpha`` 为 False 时省略对应部分,两者都
      False 返回 "";
    - 曲线恒为 1.0(所有点比值==1.0),或事件跨度内无任何有效变化
      (r0==1.0 且所有段落入跨度部分的目标值也全为 1.0)→ 返回 ""
      (不加任何标签)。
    """
    if not curve or ev_end <= ev_start or not (use_color or use_alpha):
        return ""
    ts = [float(t) for t, _r in curve]
    rs = [float(r) for _t, r in curve]
    if all(r == 1.0 for r in rs):
        return ""

    def fmt_tags(ratio: float) -> str:
        ratio = min(1.0, max(0.0, ratio))
        out = ""
        if use_color:
            if base_color is None:
                g = int(round(255.0 * ratio))
                out += f"\\1c&H{g:02X}{g:02X}{g:02X}&"
            else:  # 给定基色:按通道缩放(遮罩与字幕一起调暗)
                channels = [int(round(float(c) * ratio)) for c in base_color]
                out += "\\1c&H{0:02X}{1:02X}{2:02X}&".format(*channels)
        if use_alpha:
            a = int(round(255.0 * (1.0 - ratio)))
            out += f"\\alpha&H{a:02X}&"
        return out

    ev_start = float(ev_start)
    ev_end = float(ev_end)
    r0 = _interp_ratio(ts, rs, ev_start)
    changed = r0 != 1.0
    parts = [fmt_tags(r0)]
    for (t1, _r1), (t2, _r2) in zip(curve, curve[1:]):
        lo = max(t1, ev_start)
        hi = min(t2, ev_end)
        if hi - lo < 0.001:  # 交集 <1ms 丢弃
            continue
        target = _interp_ratio(ts, rs, hi)
        if target != 1.0:
            changed = True
        ms1 = int(round((lo - ev_start) * 1000))
        ms2 = int(round((hi - ev_start) * 1000))
        parts.append(f"\\t({ms1},{ms2},{fmt_tags(target)})")
    if not changed:
        return ""  # 跨度内恒亮:基值与 \t 都是空操作
    return "".join(parts)
