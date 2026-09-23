# core/scene_brightness.py
r"""B3:有色遮罩与屏幕亮度共用同一条绝对时间亮度曲线。

两种蒙版的职责划分(设计 §3 V7):

- ``\p1`` 有色遮罩是**可见图层**:颜色必须与所在屏幕的明暗同步
  (``base_color × 有效亮度比``),保持不透明——靠降低 alpha 露出原字
  是错误行为;
- ``\iclip`` 是不可见裁剪边界,没有亮度语义(由 B4 的前景轮廓负责)。

:func:`apply_scene_brightness` 把运动脚本里既有的逐事件亮度标签循环
(:func:`core.motion_ass.brightness_tag_chain` 调用)抽出共用:事件复制后
按自身 start/end 从**绝对采样曲线**插值生成 ``\1c/\alpha`` 标签块;携带
``base_color`` 的遮罩事件按通道缩放且不写 ``\alpha``(保持不透明);
``base_color`` 始终保留为元数据,不在处理时 pop 丢失。

:func:`sample_visible_background_luma` 只测量**背景**的可见灰度(排除
手部前景);有效像素不足 64 返回 ``None``——未知不得用黑色或手部颜色
代替。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, List, Optional, Sequence, Tuple

from core.motion_ass import brightness_tag_chain

if TYPE_CHECKING:  # 仅类型标注:运行时在函数内延迟导入 numpy/cv2
    import numpy as np

__all__ = [
    "apply_scene_brightness",
    "sample_visible_background_luma",
    "sample_static_mask_luma_curve",
]

_MIN_VALID_PIXELS = 64


def _parse_cs(value: str) -> float:
    h, m, rest = str(value).split(":")
    return int(h) * 3600 + int(m) * 60 + float(rest)


def apply_scene_brightness(
    events: List[dict],
    curve: Sequence[Tuple[float, float]],
    *,
    use_alpha: bool = True,
    use_color: bool = True,
    per_line: Optional[Sequence[Sequence[Tuple[float, float]]]] = None,
) -> List[dict]:
    """按绝对亮度曲线为每条事件生成 ``\1c/\alpha`` 标签块(复制输入)。

    - 事件时间(start/end)决定曲线插值窗口;切分后的事件按各段真实起止
      时刻求亮度,不重置曲线;
    - ``base_color``(有色遮罩):按通道缩放、``use_alpha`` 强制关闭
      (遮罩保持不透明,绝不靠 alpha 露出原字幕),并保留为元数据;
    - 无 ``base_color`` 的文字事件保持既有行为(灰度 ``\1c`` + ``\alpha``);
    - ``per_line``:可选逐行曲线(下标 = 事件 ``line_idx``),缺失/空时
      回退全局曲线。
    """
    out: List[dict] = []
    for ev in events:
        ev = dict(ev)
        base_color = ev.get("base_color")
        ev_curve = curve
        if per_line is not None:
            li = ev.get("line_idx")
            if isinstance(li, int) and 0 <= li < len(per_line) and per_line[li]:
                ev_curve = per_line[li]
        if ev_curve:
            chain = brightness_tag_chain(
                list(ev_curve),
                _parse_cs(ev["start_time"]),
                _parse_cs(ev["end_time"]),
                use_color=use_color,
                use_alpha=use_alpha and base_color is None,
                base_color=base_color,
            )
            if chain:
                ev["tags"] = f"{ev['tags']}{{{chain}}}"
        out.append(ev)
    return out


def sample_visible_background_luma(
    frame_bgr: "np.ndarray",
    background_mask: "np.ndarray",
    occluder_mask: Optional["np.ndarray"] = None,
) -> Optional[float]:
    """背景 mask 且非手部前景像素的灰度中位数;有效像素 < 64 返回 None。

    未知(采样不足)不得用黑色或手部颜色代替——由调用方记录 unknown 并
    沿既有规则处理。
    """
    import cv2
    import numpy as np

    if frame_bgr is None or background_mask is None:
        return None
    h, w = frame_bgr.shape[:2]
    if background_mask.shape != (h, w):
        return None
    valid = np.asarray(background_mask) > 0
    if occluder_mask is not None:
        if occluder_mask.shape != (h, w):
            return None
        valid &= ~(np.asarray(occluder_mask) > 0)
    if int(valid.sum()) < _MIN_VALID_PIXELS:
        return None
    gray = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2GRAY)
    return float(np.median(gray[valid]))


def sample_static_mask_luma_curve(
    read_frame,
    rect: Tuple[int, int, int, int],
    start_sec: float,
    end_sec: float,
    *,
    fps: float,
    baseline_percentile: float = 90.0,
    stride: int = 3,
    stroke_delta: int = 40,
    densify_diff: float = 0.1,
) -> List[Tuple[float, float]]:
    """静态遮罩的背景亮度曲线(逐帧采样有效背景,排除文字笔画)。

    ``read_frame(frame_num) -> BGR ndarray | None`` 由调用方提供(生成器的
    共享读取器)。默认每 ``stride`` 帧与首尾边界采样;相邻比值差 >
    ``densify_diff`` 时补中间帧(一轮,仍受原有效帧范围限制)。笔画剔除与
    :func:`core.scene_text_policy.sample_background_stats` 同锚(区域中位
    灰 − ``stroke_delta`` 判墨)。基线 = 各样本的 ``baseline_percentile``
    分位,ratio = luma / baseline 截到 (0, 1]。无可靠样本返回 []。
    """
    import cv2
    import numpy as np

    if fps <= 0 or end_sec <= start_sec:
        return []
    x1, y1, x2, y2 = (int(round(v)) for v in rect)
    f0 = max(0, int(round(start_sec * fps)))
    f1 = int(round(end_sec * fps))
    if f1 < f0:
        return []
    frames = sorted({f for f in range(f0, f1 + 1, max(1, stride))} | {f0, f1})

    def _sample(f: int) -> Optional[float]:
        img = read_frame(f)
        if img is None:
            return None
        crop = img[max(0, y1):y2, max(0, x1):x2]
        if crop.size == 0:
            return None
        gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
        med = float(np.median(gray))
        background = gray >= (med - float(stroke_delta))
        return sample_visible_background_luma(crop, background)

    samples: List[Tuple[float, Optional[float]]] = [
        (f / fps, _sample(f)) for f in frames]
    # 强明暗变化补中间帧(一轮;补采受原范围限制)。
    densified: List[Tuple[float, Optional[float]]] = []
    for i, (t, r) in enumerate(samples):
        densified.append((t, r))
        if i + 1 >= len(samples):
            continue
        t2, r2 = samples[i + 1]
        if r is None or r2 is None:
            continue
        base = max(r, 1e-6)
        if abs((r2 / base) - 1.0) > densify_diff:
            mid_f = int(round((t + t2) / 2.0 * fps))
            if float(f0) / fps < t2 and mid_f not in frames:
                densified.append((mid_f / fps, _sample(mid_f)))
    densified.sort(key=lambda tr: tr[0])
    # 短缺口桥接:None 用最近可靠样本,最多跨 3 帧;更长的缺口保持未知
    # (不生成亮度推断)。
    bridged: List[Tuple[float, float]] = []
    for i, (t, r) in enumerate(densified):
        if r is None:
            for dj in range(1, 4):
                for j in (i - dj, i + dj):
                    if 0 <= j < len(densified) and densified[j][1] is not None:
                        r = densified[j][1]
                        break
                if r is not None:
                    break
        if r is not None:
            bridged.append((t, r))
    if not bridged:
        return []
    baseline = float(np.percentile([r for _t, r in bridged],
                                   float(baseline_percentile)))
    if baseline <= 0.0:
        return []
    curve: List[Tuple[float, float]] = []
    for t, r in bridged:
        ratio = min(1.0, max(r / baseline, np.nextafter(0.0, 1.0)))
        curve.append((t, ratio))
    return curve
