# core/text_alignment.py
"""识别行框 → 原文对齐方式检测(左/中/右)+ 段落块合并。

识别文字以替换字体回贴时,若一律用 ``\\an5`` 锚在行框中心,原文左对齐/
右对齐的多行块会因各行检测宽度/渲染宽度的差异失去公共边距(短行浮到
中间,整块看起来「全部居中」,与原画面排版不符)。本模块从 OCR 行框几何
反推原文对齐方式,供各 ASS 产出路径选用对应锚点:

- 左对齐块 → ``\\an4``(中左),锚在各行框左缘;
- 居中块   → ``\\an5``(中心),锚在行框中心(旧行为);
- 右对齐块 → ``\\an6``(中右),锚在各行框右缘。

:func:`detect_line_alignment` 比较行框左缘/中心/右缘三组序列的极差,取
最小者;并列按 中 > 左 > 右 偏好(等宽行块的三个极差同时为 0 → 归中,
与旧行为一致;单行块同理归中)。不用阈值:OCR 行框抖动(几 px)远小于
真实对齐差异(短行中心可偏移数百 px),最小极差本身即判定。

:func:`detect_line_alignments` 面向**混合布局**(聊天界面左列接收气泡 +
右列发送气泡、邮件正文行距 ≥ 行高等):逐行在全组做边缘贴合投票,每行
独立取票数最高的对齐边,不要求垂直相邻。诊断:传仅关键字参数
``diagnostics``(dict)可带出逐行判定结果(行 idx → left/center/right +
三边票数 + 去趋势剪切斜率),供真实视频排查「为什么判成 left」,不影响
返回值。:func:`merge_line_blocks` 自 core.scene_text_policy 迁入(该模块
继续 re-export,API 不变),仍用于遮罩并块(整段一次盖住)。

健壮性:坐标含 NaN/inf、行数不足或框退化(平均行高 ≤ 0)时不抛异常,
非有限行归中且不参与投票(不污染均值/容差),其余行按有限坐标照常判定。

纯函数:不读视频、不依赖 numpy/cv2/PySide6。
"""

from __future__ import annotations

import math
from typing import Dict, List, Optional, Sequence, Tuple

__all__ = [
    "ALIGN_LEFT",
    "ALIGN_CENTER",
    "ALIGN_RIGHT",
    "detect_line_alignment",
    "detect_line_alignments",
    "merge_line_blocks",
]

ALIGN_LEFT = "left"
ALIGN_CENTER = "center"
ALIGN_RIGHT = "right"

# 极差并列时的偏好序:居中优先(等宽/单行块保持旧行为),其次左、右。
_PREFERENCE = (ALIGN_CENTER, ALIGN_LEFT, ALIGN_RIGHT)


def _linear_residuals(values: List[float], ys: List[float],
                      slope: float = 0.0) -> List[float]:
    """按给定斜率去趋势:返回 ``value − (a + slope·y)`` 残差(a 最小二乘)。

    轨迹管线的平面坐标来自手选 quad 的单应展开——quad 差一两度,画面里
    竖直的 UI 列在平面坐标里就会随 y 线性漂移(实测 4° 偏差漂 36px,
    远超贴合容差),投票前必须把这道趋势拟掉。斜率由
    :func:`_estimate_shear_slope` 全局估计(仿射剪切对所有列相同);
    ``slope=0`` 即不去趋势。ragged(真不对齐)列去趋势后依旧参差,
    不产生虚假贴合。
    """
    n = len(values)
    my = sum(ys) / n
    mv = sum(values) / n
    a = mv - slope * my
    return [v - (a + slope * y) for v, y in zip(values, ys)]


def _estimate_shear_slope(
    parsed: List[Tuple[float, float, float, float]],
    avg_h: float,
    diagnostics: Optional[Dict[str, object]] = None,
) -> float:
    """估计平面坐标的仿射剪切斜率(所有竖直 UI 列共享同一斜率)。

    对三种边缘的成对点(行距 ≥ 0.8×平均行高,避免同排噪声放大)计算
    斜率 ``(v_i − v_j) / (y_i − y_j)``,按 0.02 分 bin 投票;真列成对斜率
    会聚在真实剪切斜率附近并互相加强,散点则均匀摊开。取最大 bin 的
    平均斜率;|斜率| 上限 0.35(超过即 quad 本身不可信,不做去趋势),
    成对数 < 3(小数组,拟合不可靠)时返回 0——旧行为(不去趋势)。

    采纳前加占优度检验:无真实剪切的混排布局(聊天左右列等)里,跨列
    配对可把任意斜率顶成最大 bin——y 跨度大时按错误斜率去趋势会把真正
    对齐的边缘推移数十 px,整组投票翻掉。故最大 bin 除 ≥3 对外,还须
    占总参与对数 ≥1/4 才采纳,否则斜率按 0.0(不去趋势)处理;采纳原因
    (含对数信息)写入 ``diagnostics["shear_slope_reason"]`` 供排查。
    """
    def _reason(text: str) -> None:
        if diagnostics is not None:
            diagnostics["shear_slope_reason"] = text

    if len(parsed) < 3 or avg_h <= 0.0:
        _reason("too_few_rows")
        return 0.0
    ys = [(b[1] + b[3]) / 2.0 for b in parsed]
    height = max(ys) - min(ys)
    if height <= avg_h:
        _reason("no_vertical_span")
        return 0.0
    bins: Dict[int, List[float]] = {}
    y_lo, y_hi = 0.8 * avg_h, max(0.9 * height, 1.2 * avg_h)
    metrics = ([b[0] for b in parsed],
               [(b[0] + b[2]) / 2.0 for b in parsed],
               [b[2] for b in parsed])
    for col in metrics:
        for i in range(len(col)):
            for j in range(i + 1, len(col)):
                dy = ys[j] - ys[i]
                if y_lo <= abs(dy) <= y_hi:
                    s = (col[j] - col[i]) / dy
                    if abs(s) <= 0.35:
                        bins.setdefault(int(s / 0.02), []).append(s)
    if not bins:
        _reason("no_pairs")
        return 0.0
    best = max(bins.values(), key=len)
    total = sum(len(v) for v in bins.values())
    stats = f"(best={len(best)}/total={total})"
    if len(best) < 3:
        _reason("too_few_pairs" + stats)
        return 0.0
    if len(best) < max(3, 0.25 * total):
        _reason("insufficient_dominance" + stats)
        return 0.0
    _reason("adopted" + stats)
    return sum(best) / len(best)


def merge_line_blocks(
    boxes: Sequence[Sequence[float]],
    *,
    vgap_ratio: float = 0.35,
) -> List[Tuple[int, int]]:
    """段落块合并:返回块的 (起, 止) 行索引列表(闭区间,指向按 y 升序后的序列)。

    行按 y 升序内部排序后,相邻两行「垂直间距 < ``vgap_ratio`` × 两行平均
    行高 且 水平范围重叠」则并入同块(邮件正文 10 行 → 一个块)。
    """
    order = sorted(range(len(boxes)),
                   key=lambda i: (float(boxes[i][1]), float(boxes[i][0])))
    blocks: List[Tuple[int, int]] = []
    cur: List[int] = []
    for idx in order:
        if not cur:
            cur = [idx]
            continue
        prev = boxes[cur[-1]]
        box = boxes[idx]
        gap = float(box[1]) - float(prev[3])
        avg_h = ((float(prev[3]) - float(prev[1]))
                 + (float(box[3]) - float(box[1]))) / 2.0
        h_overlap = min(float(prev[2]), float(box[2])) - max(float(prev[0]), float(box[0])) > 0
        if gap < float(vgap_ratio) * avg_h and h_overlap:
            cur.append(idx)
        else:
            blocks.append((cur[0], cur[-1]))
            cur = [idx]
    if cur:
        blocks.append((cur[0], cur[-1]))
    return blocks


def detect_line_alignment(boxes: Sequence[Sequence[float]]) -> str:
    """判断同一块内各行文本的对齐方式,返回 ``"left" | "center" | "right"``。

    对左缘/中心/右缘三组坐标分别取极差(max − min),极差最小者即对齐
    边(对齐边在排版上是设计常量,极差应≈0;其余两边随行长变化)。并列
    按 中 > 左 > 右:等宽行块三者同时为 0,归中——与旧行为一致。少于
    2 行、行高非正或任一坐标非有限(NaN/inf,无法判定也不得抛异常)时
    返回 ``"center"``(单行无对齐可判,锚中心最稳)。
    本函数面向屏幕矩形坐标的静态路径(块通常 2-5 行,样本不足以估计
    剪切斜率);quad 展开平面的轨迹管线请用 :func:`detect_line_alignments`
    的 ``detrend_shear=True``。
    """
    parsed = []
    for b in boxes:
        try:
            x1, y1, x2, y2 = (float(v) for v in b)
        except (TypeError, ValueError):
            return ALIGN_CENTER
        if not (math.isfinite(x1) and math.isfinite(y1)
                and math.isfinite(x2) and math.isfinite(y2)):
            return ALIGN_CENTER
        parsed.append((x1, y1, x2, y2))
    if len(parsed) < 2:
        return ALIGN_CENTER
    if sum(b[3] - b[1] for b in parsed) <= 0.0:
        return ALIGN_CENTER
    lefts = [b[0] for b in parsed]
    centers = [(b[0] + b[2]) / 2.0 for b in parsed]
    rights = [b[2] for b in parsed]
    spreads = {
        ALIGN_LEFT: max(lefts) - min(lefts),
        ALIGN_CENTER: max(centers) - min(centers),
        ALIGN_RIGHT: max(rights) - min(rights),
    }
    return min(_PREFERENCE, key=lambda k: (spreads[k], _PREFERENCE.index(k)))


def detect_line_alignments(
    boxes: Sequence[Sequence[float]],
    *,
    edge_tol_ratio: float = 0.35,
    detrend_shear: bool = False,
    diagnostics: Optional[Dict[str, object]] = None,
) -> List[str]:
    """逐行对齐方式:全组边缘贴合投票,返回与 ``boxes`` 输入序对应的列表。

    真实排版里同侧文字共享公共边距(对齐边是设计常量),而聊天界面等
    混合布局(左列接收气泡 + 右列发送气泡)同一屏幕存在**两种对齐**,
    整组投一次票必然错一半;且邮件正文行距 ≥ 行高,按垂直相邻并块会把
    每行拆成孤块。故对每行分别在左缘/中心/右缘上统计「与组内其他行贴合
    (差 ≤ ``edge_tol_ratio`` × 平均行高;``detrend_shear=True`` 时先去除
    全局剪切斜率)」的票数,取票数最高的边缘为该行对齐方式;并列按
    中 > 左 > 右(等宽行块三边票数相同 → 归中,与旧行为一致)。孤行
    (三边票数都是 1,无上下文可判)归中——锚在自身中心,渲染宽度 ≈
    原框宽度,视觉位置不变。不要求垂直相邻。

    ``detrend_shear=True`` 供行框坐标自带**线性剪切**的路径使用:轨迹管线
    的 quad 展开平面坐标(手选 quad 差一两度,竖直 UI 列随 y 漂移数十
    px)与静态 mask 路径的视频坐标行框(斜放平面上的竖直列在画面里本
    就随 y 线性倾斜)都属此类,先由 :func:`_estimate_shear_slope` 估计全局
    剪切斜率、去除后再投票。正面版式(完全对齐的边)成对斜率全为 0,
    估计器按 0 处理,开关等价于无操作;主流水线 SCENE 分支按组行数启用
    (≥5 行才开——斜放平面上的长邮件正文竖直列随 y 倾斜,不去趋势整组
    误归中;小组样本不足以可靠估计斜率,保持不去趋势)。

    ``diagnostics``(仅关键字,可选)传入 dict 时就地写入逐行判定结果,
    便于真实视频排查「为什么判成 left」而不影响返回值:
    ``{"shear_slope": float, "rows": [{"row": 输入行 idx, "align": 边,
    "votes": {"left"/"center"/"right": 票数}}, ...],
    "shear_slope_reason": 斜率采纳原因(含对数),
    "avg_h": 平均行高, "tol": 边缘贴合容差,
    "excluded_rows": 非有限/畸形被排除的行 idx, "n_valid": 有效行数}``;
    非有限行 votes 全 0、align 为 center。
    """
    n = len(boxes)
    aligns = [ALIGN_CENTER] * n
    votes: List[Optional[Dict[str, int]]] = [None] * n

    # 逐行解析并过滤:坐标数不足/非数值/非有限(NaN/inf)的行归中且不
    # 参与投票——NaN 会把均值、容差与比较全部污染成「无人贴合」,inf 会
    # 让极差发散;剔除后其余行按有限坐标照常判定。被剔除的行 idx 记入
    # excluded_rows 留痕。
    parsed: List[Tuple[float, float, float, float]] = []
    valid: List[int] = []
    excluded: List[int] = []
    for i, b in enumerate(boxes):
        try:
            x1, y1, x2, y2 = (float(v) for v in b)
        except (TypeError, ValueError):
            excluded.append(i)
            continue
        if not (math.isfinite(x1) and math.isfinite(y1)
                and math.isfinite(x2) and math.isfinite(y2)):
            excluded.append(i)
            continue
        parsed.append((x1, y1, x2, y2))
        valid.append(i)

    def _fill_diag(slope: float, avg_h: Optional[float] = None,
                   tol: Optional[float] = None) -> None:
        if diagnostics is None:
            return
        diagnostics["shear_slope"] = round(float(slope), 4)
        diagnostics["excluded_rows"] = list(excluded)
        diagnostics["n_valid"] = len(parsed)
        if avg_h is not None:
            diagnostics["avg_h"] = round(float(avg_h), 4)
        if tol is not None:
            diagnostics["tol"] = round(float(tol), 4)
        diagnostics["rows"] = [
            {"row": i, "align": aligns[i],
             "votes": votes[i] or {"left": 0, "center": 0, "right": 0}}
            for i in range(n)]

    if len(parsed) < 2:
        _fill_diag(0.0)
        return aligns
    avg_h = sum(b[3] - b[1] for b in parsed) / len(parsed)
    if avg_h <= 0.0:
        _fill_diag(0.0, avg_h)
        return aligns
    tol = float(edge_tol_ratio) * avg_h
    if detrend_shear:
        slope = _estimate_shear_slope(parsed, avg_h, diagnostics)
    else:
        if diagnostics is not None:
            diagnostics["shear_slope_reason"] = "detrend_off"
        slope = 0.0
    ys = [(b[1] + b[3]) / 2.0 for b in parsed]
    edge_cols = {
        ALIGN_LEFT: _linear_residuals([b[0] for b in parsed], ys, slope),
        ALIGN_CENTER: _linear_residuals([(b[0] + b[2]) / 2.0 for b in parsed],
                                        ys, slope),
        ALIGN_RIGHT: _linear_residuals([b[2] for b in parsed], ys, slope),
    }
    cluster = {a: [sum(1 for v in col if abs(v - col[i]) <= tol)
                   for i in range(len(parsed))]
               for a, col in edge_cols.items()}
    # 票数并列(如剪切漂移列 vs 密集中列)时,取簇内残差极差更小的边缘——
    # 真对齐列去趋势后残差几乎重合,伪列只是被容差桥接;极差也相同(等宽
    # 行块三边全为 0)再按 中 > 左 > 右 归中。
    spread = {a: _cluster_spread(col, tol) for a, col in edge_cols.items()}
    for k, i in enumerate(valid):
        best = max(cluster[a][k] for a in _PREFERENCE)
        tied = [a for a in _PREFERENCE if cluster[a][k] == best]
        aligns[i] = min(tied,
                        key=lambda a: (spread[a][k], _PREFERENCE.index(a)))
        votes[i] = {a: int(cluster[a][k]) for a in (ALIGN_LEFT, ALIGN_CENTER,
                                                    ALIGN_RIGHT)}
    _fill_diag(slope, avg_h, tol)
    return aligns


def _cluster_spread(residuals: List[float], tol: float) -> List[float]:
    """残差排序后按「相邻间隙 > ``tol``」分簇,返回逐点所属簇的极差。"""
    order = sorted(range(len(residuals)), key=lambda k: residuals[k])
    spreads = [0.0] * len(residuals)
    start = 0
    for idx in range(1, len(order) + 1):
        if (idx == len(order)
                or residuals[order[idx]] - residuals[order[idx - 1]] > tol):
            seg = order[start:idx]
            span = (max(residuals[k] for k in seg)
                    - min(residuals[k] for k in seg)) if len(seg) > 1 else 0.0
            for k in seg:
                spreads[k] = span
            start = idx
    return spreads
