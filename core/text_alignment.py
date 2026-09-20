# core/text_alignment.py
"""识别行框 → 原文对齐方式检测(左/中/右)+ 段落块合并。

识别文字以替换字体回贴时,若一律用 ``\\an5`` 锚在行框中心,原文左对齐/
右对齐的多行块会因各行检测宽度/渲染宽度的差异失去公共边距(短行浮到
中间,整块看起来「全部居中」,与原画面排版不符)。本模块从 OCR 行框几何
反推原文对齐方式,供各 ASS 产出路径选用对应锚点:

- 左对齐块 → ``\\an4``(中左),锚在各行框左缘;
- 居中块   → ``\\an5``(中心),锚在行框中心(旧行为);
- 右对齐块 → ``\\an6``(中右),锚在各行框右缘。

:func:`detect_line_alignments` 面向**混合布局**(聊天界面左列接收气泡 +
右列发送气泡、邮件正文行距 ≥ 行高等):逐行在全组做边缘贴合投票,每行
独立取票数最高且边际达标的对齐边(票数无严格多数即低置信,显式回退
居中并留痕,见 ``min_vote_margin``),不要求垂直相邻。诊断:传仅关键字参数
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
    "detect_line_alignments",
    "merge_line_blocks",
]

ALIGN_LEFT = "left"
ALIGN_CENTER = "center"
ALIGN_RIGHT = "right"

# 极差/票数并列时的偏好序:居中优先(等宽/单行块保持旧行为),其次左、右。
_PREFERENCE = (ALIGN_CENTER, ALIGN_LEFT, ALIGN_RIGHT)

# 诊断里「未估计」的数值占位(NaN):键必须存在(消费方直接取值不 KeyError),
# 值明示未估计,与真实数值(含 0.0)区分——不编造 0 充数。
_DIAG_UNSET = float("nan")


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


def detect_line_alignments(
    boxes: Sequence[Sequence[float]],
    *,
    edge_tol_ratio: float = 0.35,
    detrend_shear: bool = False,
    min_vote_margin: int = 1,
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

    低置信边际检查(``min_vote_margin``,仅关键字,默认 1):票数含自身行
    (+1 对三边等量,不影响边际),每行的采纳证据强度记为边际——票数
    严格多数时为「胜出边 − 次高边」的票数差;三边票数并列时按既有规则
    取簇内残差极差更小的边缘,若极差**严格**更小(真对齐列去趋势后残差
    几乎重合,伪列只是被容差桥接——去趋势剪切列的既有判真机制)记 1,
    极差也并列(等宽行块)记 0。边际 ≥ ``min_vote_margin`` 才采纳;不足即
    组内对该行「贴哪条边」没有共识,显式回退 ``center``(\\an5,锚自身行框
    中心,视觉位置不变)并在诊断留痕,不再靠偏好序把无证据的并列硬判成
    left/right。默认 1 的标定依据:下限——2 行全左小组每行 left 2 票 vs
    次高 1 票、聊天屏每侧仅 2 行同理,真对齐的边际恰为 1(回归基准明示
    此类应判 left/right),阈值 ≥2 会把这些真对齐行也回退 center,小组/
    小屏的对齐检测即失效;上限——边际 0(票数并列且极差也打不开)是
    证据最弱的判定,恰由 1 拦下。传 0 关闭检查(完全旧行为,并列由极差/
    偏好打破);传 ≥2 收紧为「须更强票数多数」,边际 1 的真对齐小组与
    极差打破的并列也一并回退。

    ``detrend_shear=True`` 供行框坐标自带**线性剪切**的路径使用:轨迹管线
    的 quad 展开平面坐标(手选 quad 差一两度,竖直 UI 列随 y 漂移数十
    px)与静态 mask 路径的视频坐标行框(斜放平面上的竖直列在画面里本
    就随 y 线性倾斜)都属此类,先由 :func:`_estimate_shear_slope` 估计全局
    剪切斜率、去除后再投票。正面版式(完全对齐的边)成对斜率全为 0,
    估计器按 0 处理,开关等价于无操作;主流水线 SCENE 分支按组行数启用
    (≥5 行才开——斜放平面上的长邮件正文竖直列随 y 倾斜,不去趋势整组
    误归中;小组样本不足以可靠估计斜率,保持不去趋势)。

    ``diagnostics``(仅关键字,可选)传入 dict 时就地写入逐行判定结果,
    便于真实视频排查「为什么判成 left / 为什么回退 center」而不影响
    返回值:``{"shear_slope": float, "shear_slope_reason": 斜率原因,
    "avg_h": 平均行高, "tol": 边缘贴合容差, "n_valid": 有效行数,
    "excluded_rows": 非有限/畸形被排除的行 idx,
    "min_vote_margin": 边际阈值,
    "low_margin_rows": 因边际不足回退 center 的行 idx 列表,
    "rows": [{"row": 输入行 idx, "align": 边,
    "votes": {"left"/"center"/"right": 票数}}, ...]}``;
    边际回退的行在 ``rows`` 里额外带 ``"low_margin": True`` 与
    ``"rejected_align"``(被否决的票数胜出边)两个键——未回退的行不带,
    既有行 schema 不变;非有限行 votes 全 0、align 为 center。

    上述固定键在**所有**返回路径都写入(早退路径同样带键,消费方直接
    取 ``diag["tol"]`` 不会 KeyError),值诚实区分「未估计」与「未采纳」:
    有效行 < 2 时不做任何行级估计,``avg_h``/``tol`` 全为 ``NaN``(键
    存在、值即「未估计」,不是编造的 0);``avg_h ≤ 0`` 的退化几何里
    ``avg_h`` 是真实算出的均值(如 0.0),``tol`` 按 0 行高无意义仍为
    ``NaN``。``shear_slope_reason`` 在两条早退路径分别为 ``no_rows``
    (有效行 < 2,含全部坐标非有限)与 ``degenerate``(平均行高 ≤ 0),
    去趋势关闭时为 ``detrend_off``,开启时为估计器的原因(``adopted
    (best=…/total=…)`` / ``no_pairs`` / ``insufficient_dominance(…)``
    等),从不写 None 或空串。
    """
    n = len(boxes)
    aligns = [ALIGN_CENTER] * n
    votes: List[Optional[Dict[str, int]]] = [None] * n
    # 边际检查留痕:输入行 idx → 被否决的票数胜出边(回退 center 的行)。
    rejected: Dict[int, str] = {}
    low_margin_rows: List[int] = []

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

    def _fill_diag(slope: float, avg_h: float, tol: float,
                   slope_reason: str) -> None:
        """落盘全部固定诊断键(所有返回路径共用,键集恒定、值不编造)。

        ``avg_h``/``tol`` 未估计时传 ``_DIAG_UNSET``(NaN):键存在可供
        消费方直接取值,值本身明示「未估计」,与真实数值(哪怕 0.0)区分。
        """
        if diagnostics is None:
            return
        diagnostics["shear_slope"] = round(float(slope), 4)
        diagnostics["shear_slope_reason"] = slope_reason
        diagnostics["avg_h"] = round(float(avg_h), 4)
        diagnostics["tol"] = round(float(tol), 4)
        diagnostics["excluded_rows"] = list(excluded)
        diagnostics["n_valid"] = len(parsed)
        diagnostics["min_vote_margin"] = int(min_vote_margin)
        diagnostics["low_margin_rows"] = list(low_margin_rows)
        rows: List[Dict[str, object]] = []
        for i in range(n):
            row: Dict[str, object] = {
                "row": i, "align": aligns[i],
                "votes": votes[i] or {"left": 0, "center": 0, "right": 0}}
            if i in rejected:
                # 低置信回退留痕(未回退的行不带这两个键,schema 只增不改)
                row["low_margin"] = True
                row["rejected_align"] = rejected[i]
            rows.append(row)
        diagnostics["rows"] = rows

    if len(parsed) < 2:
        # 行数不足(含坐标全非有限):不做行级估计,均值/容差无从谈起——
        # 键仍带出,值为 NaN(未估计),原因记 no_rows。
        _fill_diag(0.0, _DIAG_UNSET, _DIAG_UNSET, "no_rows")
        return aligns
    avg_h = sum(b[3] - b[1] for b in parsed) / len(parsed)
    if avg_h <= 0.0:
        # 退化几何:均值行高是真实算出值(如 0.0),容差按 0 行高无意义
        # → 未估计(NaN),原因记 degenerate。
        _fill_diag(0.0, avg_h, _DIAG_UNSET, "degenerate")
        return aligns
    tol = float(edge_tol_ratio) * avg_h
    if detrend_shear:
        slope = _estimate_shear_slope(parsed, avg_h, diagnostics)
        # 估计器已把原因写入 diagnostics(含成对对数,如
        # ``adopted(best=28/total=56)``);取回是为了让 _fill_diag 成为
        # 固定键的唯一写入点(诊断关闭时该值不落键,仅作占位)。
        slope_reason = (str(diagnostics.get("shear_slope_reason", ""))
                        if diagnostics is not None else "")
    else:
        slope = 0.0
        slope_reason = "detrend_off"
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
        voted = min(tied,
                    key=lambda a: (spread[a][k], _PREFERENCE.index(a)))
        counts = {a: int(cluster[a][k]) for a in (ALIGN_LEFT, ALIGN_CENTER,
                                                  ALIGN_RIGHT)}
        votes[i] = counts
        # 采纳证据强度(边际):票数严格多数 → 票数差;票数并列但簇内残差
        # 极差严格更小 → 记 1(既有第二证据:真对齐列残差重合,伪列靠容
        # 差桥接);极差也并列 → 记 0(偏好序归中等无证据结果)。
        if len(tied) > 1:
            strict = (spread[voted][k]
                      < min(spread[a][k] for a in tied if a != voted))
            margin = 1 if strict else 0
        else:
            margin = counts[voted] - max(counts[a] for a in counts
                                         if a != voted)
        if voted != ALIGN_CENTER and margin < min_vote_margin:
            # 低置信(无达标证据):不硬判 left/right,显式回退 center 并
            # 留痕(min_vote_margin=0 时本分支不可达,旧行为)。
            aligns[i] = ALIGN_CENTER
            rejected[i] = voted
            low_margin_rows.append(i)
        else:
            aligns[i] = voted
    _fill_diag(slope, avg_h, tol, slope_reason)
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
