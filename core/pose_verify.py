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
4. 失配跨度分级判定(防幽灵文字,同时避免可见文字被误删出时间空洞):
   连续硬失配采样(分数 < ``drop_score``)构成候选不可见跨度,删除前按
   证据强度分级确认——失配采样先以更大半径(``retry_radius_px``)重试,
   区分「漂移累积超出搜索半径」与「文字真的没了」;分数落在
   ``[drop_score, min_score)`` 的边界采样只标记、不参与删除;跨度帧长超过
   ``long_span_frames`` 时还需在跨度内部补采确认(内部帧仍全部失配才删,
   见到文字则整段保留)。判定成立的跨度删除位姿(等效 lost,轨迹切链),
   每一步决策留痕于 ``LineVerifyReport.spans`` 供日志与离线分析;
5. 全部有效采样收敛在 ``static_tol_px`` 内的行判定为屏幕静止:整条轨迹
   位姿吸附为中位实测中心(角度/缩放取参考帧值),供合成器输出单条
   ``\\pos`` 事件(无 ``\\move``/``\\t``)。

模块只依赖 OpenCV/numpy 与 :class:`core.motion_ass.LineTrack`,不依赖
PySide6;视频解码自包含且**非单遍**,分两遍扫描(结果与整段一次性解码
逐位一致,分遍只是「解码批次 + 阶段划分」,不改变任何一行的校验结果):

1. **第 1 遍**(``_chunk_plans`` 分块 → 阶段 A):行 plan 按时间局部性分块,
   块按时间顺序处理,共用一个**顺序读取游标** ``_FrameCursor``(帧号单调
   前进、绝不 ``cap.set`` 跳帧;非单调请求确定性回退为「重开捕获从头读」)。
   逐块解码 → 阶段 A(``_match_line_samples``)→ 记下超长失配跨度的
   ``confirm_picks`` → 立即释放该块帧。单块驻留帧数受有效块大小约束,
   有效块大小 = ``VerifyConfig.block_max_frames`` 按分辨率归一
   (``_effective_block_frames``,字节预算恒定);游标把**后续批次还要用**
   的帧留在有界预取区(``_FrameCursor``),相邻块的采样时间窗重叠时不必
   回退重读,顺序读取总量 ≈ 一遍视频长度而不是「块数 × 视频长度」。
2. **第 2 遍**(确认帧 → 阶段 B):把所有块的确认帧并集按时间切成有界小块
   顺序解码,行一旦「帧到齐」就立刻做分级判定(阶段 B)并释放其确认帧;
   阶段 B 只查确认帧(采样帧的分数/偏移早已存进 ``_LineWork``),确认帧
   缺失时保持既有「未测 → 保留整段」语义(``long_span_unconfirmed``)。
"""

from __future__ import annotations

import logging
import threading
from contextlib import contextmanager
from dataclasses import dataclass, field, replace
from typing import Callable, Dict, Iterator, List, Optional, Sequence, Set, Tuple

import numpy as np

from core.motion_ass import LinePose, LineTrack
from core.scene_plane_tracker import TrackedQuad

__all__ = [
    "VerifyConfig",
    "LineVerifyReport",
    "SpanDecision",
    "verify_line_tracks",
]

logger = logging.getLogger(__name__)

# 有效块帧数下限:按分辨率归一后仍保证单块能装下少量行采样,防止低分辨率的
# 病态小块把解码批次数放大到无意义的量级(单行采样就有 sample_max 帧)。
MIN_BLOCK_FRAMES = 8
# 游标预取窗口上限 = 有效块帧数 × 本系数(与单块驻留同量级的字节预算),
# 并与 MIN_PREFETCH_FRAMES 取大:相邻块的采样时间窗常常互相重叠(后一块的
# 最早采样早于前一块的最晚采样),没有任何预取时游标每个块都要回退重读一遍
# 前缀,解码总量退化为「块数 × 帧号」。预取把「后续批次仍需要、且落在本次
# 顺序扫描范围内」的帧顺手留下,重叠部分只读一次。取 2× 是为覆盖「下一块
# 整块 + 再下一块落在本窗口内的采样」。
_PREFETCH_BLOCK_FACTOR = 2
# 预取窗口下限(帧):需要预取的帧数只取决于「块时间窗彼此重叠多少帧」,
# 是分辨率无关的帧数量级;而单块字节预算按分辨率归一后 4K 的有效块帧数只有
# 1080p 的 1/4——若不设下限,「4K + 显式小块」会因窗口过小而频繁回退重读,
# 耗时反而比默认配置更差(实测:4K/block=16 的有效块 8 → 窗口 16 帧 →
# 4.85 遍视频读取;窗口 32 帧 → 1 遍)。窗口只是**上限**,只有后续批次真的
# 需要才会驻留。
MIN_PREFETCH_FRAMES = 32


@dataclass
class VerifyConfig:
    """实测校正参数(对应 ``MotionAssConfig`` 的 ``verify_*`` 字段)。"""

    sample_max: int = 16            # 每行最多采样的帧数(含首末)
    search_radius_px: float = 40.0  # 匹配搜索半径(预测中心 ± 半径)
    min_score: float = 0.45         # 参与校正的最小匹配置信度
    drop_score: float = 0.30        # 判定文字不可见的置信度
    static_tol_px: float = 2.5      # 屏幕静止判定的最大位移
    min_static_samples: int = 3     # 静止判定所需的最少有效采样数
    # 失配采样(< drop_score)的扩半径重试搜索半径,默认 ≈2×search_radius_px:
    # 区分「漂移累积超出搜索半径」(更大窗口能找回,采样照常纳入实测序列)
    # 与「文字真的没了」(重试仍失配,删除证据升级)。≤ search_radius_px
    # 时禁用重试。
    retry_radius_px: float = 80.0
    # 失配跨度帧长上限:相邻失配采样隔开的跨度超过该帧数(约 2s@24fps)视为
    # 「超长」——两个稀疏采样失配不该直接删掉数秒位姿,删除前需在跨度内部
    # 补采确认(见 span_confirm_max)。
    long_span_frames: int = 48
    # 超长失配跨度删除确认时,在跨度内部均匀补采的帧数**上限**(cap):
    # 实际确认帧数 = min(cap, max(1, 跨度帧长 // long_span_frames)),随跨度
    # 线性增长(2 倍 long_span_frames 的跨度补 2 帧、10 倍补 10 帧,2000 帧
    # 的长跨度不再只有 3 个确认点),并以本值封顶。额外解码代价的上界:
    # 每个待删跨度 ≤ min(cap, 跨度帧长 // long_span_frames) 帧,且只补
    # 块内尚未解码的缺帧(见 verify_line_tracks 的补解码阶段)。
    span_confirm_max: int = 8
    # 分块解码的单块帧并集上限(纯内存界;≤0 = 不分块,一次解码全部行采样)。
    block_max_frames: int = 64
    # ROI 包含门控:调用方传入 ``roi_quad``(用户手绘 ROI,屏幕坐标)时,
    # 若全局最优峰落在 ROI(按 ROI 最小边 10%、至少 4px 外扩)之外、且
    # ROI 内存在可采纳峰(分数 ≥ min_score),则改取 ROI 内最优峰——大半
    # 径重试在 ROI 外锁到相似纹理(4K 固定歌词条实测:模板锁到条带外
    # 背景,偏移 115–215px)不再压过真值。**只在 ROI 内有可采纳峰时改写**:
    # ROI 内无峰(文字滚出 ROI 但仍在画面上可见,DMG 邮件屏滚出行)时回退
    # 全局峰,与旧行为逐字节一致——钳制绝不制造失配、绝不删除可见位姿。
    # False 关闭钳制(不传 roi_quad 同效)。
    roi_clamp: bool = True


@dataclass
class SpanDecision:
    """单个失配跨度的判定留痕(供日志与离线分析)。

    ``frames`` 为跨度内的行位姿帧号:dropped=True 时即被删除的帧,
    False 时为涉险但保留的帧。``scores`` 含边界失配采样的最终分数与
    跨内确认帧的探测分数(键为帧号)。
    """

    start: int           # 跨度首采样帧号(含)
    end: int             # 跨度末采样帧号(含)
    frames: List[int]    # 跨度内的行位姿帧号
    dropped: bool        # 是否执行删除
    reason: str          # 证据分级,见下方 _REASON_* 注释
    scores: Dict[int, float] = field(default_factory=dict)


# SpanDecision.reason 取值(失配跨度的证据分级,强度递增):
# - double_mismatch:相邻两个采样都硬失配、跨度 ≤ long_span_frames,
#   与既有「相邻两失配采样配对即删」语义一致,直接删;
# - long_span_confirmed:超长跨度,**确认帧测到且全部硬失配**,证据
#   升级后才删(真正的滚出画面/长遮挡);
# - long_span_kept_visible:超长跨度,确认帧见到 ≥drop_score 的匹配
#   (含边界分)——删除会制造可见时间空洞,整段保留并留痕;
# - long_span_unconfirmed:超长跨度,**确认帧一个都没测到**(跨度内没有
#   可用补采帧,或整批补解码失败/目标帧都读不出来)——证据不足以判定
#   「文字真的没了」,按「未测」处理整段保留,绝不按「已确认硬失配」
#   删除(宁可留一段幽灵,不误删可见文字)。


@dataclass
class LineVerifyReport:
    """单行校正诊断(供日志/测试断言)。"""

    static: bool = False
    corrected: bool = False
    n_samples: int = 0
    n_good: int = 0
    # 静止判定中有效采样相对中位实测中心的最大偏离(px)。哨兵 ``-1.0`` =
    # **未评估**(没走到静止判定分支:补丁不可用、有效采样不足、首末采样
    # 缺一或存在被删跨度);真实偏离恒 ≥ 0,离线分析据哨兵区分「零偏差」
    # 与「未评估」。
    max_dev_px: float = -1.0
    dropped_frames: int = 0
    scores: Dict[int, float] = field(default_factory=dict)
    # 触发扩半径重试的采样帧号 → 重试分数(采纳后的最终分数仍在 scores,
    # 取常规/重试两次匹配的较大者)。
    retry_scores: Dict[int, float] = field(default_factory=dict)
    # 最终分数落在 [drop_score, min_score) 的采样帧号(保留+标记:文字
    # 大概率还在、只是匹配质量差,不参与失配删除)。
    borderline: List[int] = field(default_factory=list)
    # 失配跨度判定留痕(含被拒删的保留跨度),供调用方日志与离线分析。
    spans: List[SpanDecision] = field(default_factory=list)
    # 因 ROI 钳制改取 ROI 内次优峰的匹配次数(全局最优峰落在 ROI 外、
    # 其分数被钳制丢弃的采样次数;含阶段 B 确认帧)。
    n_clamped: int = 0
    # ROI 内无可采纳峰而回退全局峰、且全局峰落在 ROI 外的采样次数(含
    # 阶段 B 确认帧)。两种形态共用该计数:文字滚出 ROI 但仍可见(DMG
    # 邮件屏,回退是**正确**行为)与文字消失后重试锁到 ROI 外相似纹理
    # (「消失段」残留形态,当前无空间约束、靠失配分级兜底)。仅观测、
    # 不改变任何采纳结果;后者若被真实素材暴露,「消失段校正不得把行移
    # 出 ROI」的行为约束以该计数为定位依据单独立项。
    n_roi_fallback: int = 0


class _FrameCursor:
    """跨批次共享的**顺序**解码游标(单调前进,绝不 seek)。

    - ``read(wanted)`` 返回该批需要的帧(缺席 = 读不到,由调用方按缺帧降级):
      已在预取区里的直接取用,其余从当前读位置**顺序**读到该批帧尾——
      与既有 ``_decode_frames`` 一样「顺序解码、不随机 seek」(刻意避免容器
      seek 的时间轴/关键帧偏差),只是捕获不再每批重开、前缀不再重读;
    - 非单调请求(需要的帧在游标位置之前、且不在预取区)按**确定性回退**
      处理:释放捕获、重开并从头顺序读到该批帧尾。既不静默丢帧也不抛异常,
      读不到的帧照旧缺席;
    - 预取区:``plan`` 登记各批次的帧号集合,得到「帧号 → 最后消费批次序号」
      需求图;顺序扫描时顺手留下**后续批次还要用**的帧(上限
      ``prefetch_limit`` 帧,超出时按「需求最远者先淘汰」),使相邻批次的
      时间窗重叠时不必回退重读;批次序号 = 本游标服务的第几次 ``read``。

    坐标系与语义:帧图像一律由同一捕获按帧号 0,1,2,… 顺序解出,没有任何
    跳帧/seek,因此「从预取区取」与「重读一遍」得到的是逐字节相同的帧。
    """

    def __init__(self, video_path: str, prefetch_limit: int = 0) -> None:
        self.video_path = str(video_path)
        self.prefetch_limit = max(0, int(prefetch_limit))
        self._cap = None                      # cv2.VideoCapture(懒开)
        self._pos = 0                         # 下一个待读帧号([0, _pos) 已读过)
        self._keep: Dict[int, np.ndarray] = {}   # 预取区:后续批次还要用的帧
        self._demand: Dict[int, int] = {}     # 帧号 → 最后需要它的批次序号
        self._calls = 0                       # 已服务批次数(当前批次序号)

    # ------------------------------------------------------------------
    # 生命周期
    # ------------------------------------------------------------------
    def plan(self, batches: Sequence[Sequence[int]]) -> None:
        """登记各批次的帧号集合,建立预取需求图(批次序号 = 调用顺序)。"""
        demand: Dict[int, int] = {}
        for i, batch in enumerate(batches):
            for f in batch:
                demand[int(f)] = i
        self._demand = demand

    def close(self) -> None:
        if self._cap is not None:
            self._cap.release()
            self._cap = None
        self._keep.clear()

    def __enter__(self) -> "_FrameCursor":
        return self

    def __exit__(self, *_exc) -> None:
        self.close()

    # ------------------------------------------------------------------
    # 读取
    # ------------------------------------------------------------------
    def read(self, wanted: Sequence[int]) -> Dict[int, np.ndarray]:
        """解码该批需要的帧(升序去重;读不到的帧直接缺席)。"""
        want = sorted({int(f) for f in wanted})
        ordinal = self._calls
        self._calls += 1
        if not want:
            return {}
        got: Dict[int, np.ndarray] = {f: self._keep[f] for f in want
                                      if f in self._keep}
        rest = [f for f in want if f not in got]
        if rest:
            if rest[0] < self._pos:
                self._reopen()            # 非单调:确定性回退(从头读)
            if self._cap is None:
                self._cap = self._open()
                self._pos = 0
            self._pos = self._sweep(rest, want[-1], ordinal, got)
        self._evict(ordinal)
        return got

    def _open(self):
        import cv2

        cap = cv2.VideoCapture(self.video_path)
        if not cap.isOpened():
            raise RuntimeError(f"Cannot open video: {self.video_path}")
        return cap

    def _reopen(self) -> None:
        """非单调回退:丢弃捕获与读位置,下次从头顺序读(预取区帧仍有效)。"""
        if self._cap is not None:
            self._cap.release()
            self._cap = None
        self._pos = 0

    def _sweep(self, need: Sequence[int], hi: int, ordinal: int,
               got: Dict[int, np.ndarray]) -> int:
        """从游标位置顺序读到 ``hi``(含),不 seek;返回新的游标位置。"""
        pending: Set[int] = set(need)
        idx = self._pos
        cap = self._cap
        while idx <= hi:
            ok, frame = cap.read()
            if not ok or frame is None:
                break                     # EOF:剩余帧缺席(调用方缺帧降级)
            if idx in pending:
                got[idx] = frame
                pending.discard(idx)
            self._prefetch(idx, frame, ordinal)
            idx += 1
        return idx

    def _prefetch(self, idx: int, frame: np.ndarray, ordinal: int) -> None:
        """后续批次还要用的帧留在预取区(有界,超出丢需求最远者)。"""
        if self.prefetch_limit <= 0:
            return
        if self._demand.get(idx, -1) <= ordinal:
            return
        self._keep[idx] = frame
        while len(self._keep) > self.prefetch_limit:
            far = max(self._keep, key=lambda f: self._demand.get(f, -1))
            del self._keep[far]

    def _evict(self, ordinal: int) -> None:
        """释放已无后续消费者的预取帧(当前批次已用完)。"""
        if not self._keep:
            return
        for f in [f for f in self._keep
                  if self._demand.get(f, -1) <= ordinal]:
            del self._keep[f]


_ACTIVE_CURSOR = threading.local()


@contextmanager
def _shared_cursor(cursor: _FrameCursor) -> Iterator[_FrameCursor]:
    """本次调用内把游标挂到模块上下文,供 ``_decode_frames`` 批次复用时取用。

    线程局部:并发调用各自持有自己的游标,不会串读同一个捕获。
    """
    prev = getattr(_ACTIVE_CURSOR, "cursor", None)
    _ACTIVE_CURSOR.cursor = cursor
    try:
        yield cursor
    finally:
        _ACTIVE_CURSOR.cursor = prev


def _decode_frames(video_path: str, wanted: Sequence[int]) -> Dict[int, np.ndarray]:
    """顺序单遍解码指定帧号集合;读不到的帧直接缺席,由调用方按缺帧降级。

    调用上下文里有同路径的共享游标(``_shared_cursor``)时复用它——游标只
    前进、不回退,并把后续批次还要用的帧留在有界预取区;没有共享游标
    (测试/工具的独立调用)时临时开一个游标,每次从头顺序读到该批帧尾,
    与「每批独立解码」的既有语义逐字节一致。视频打不开抛
    :class:`RuntimeError`。
    """
    cur = getattr(_ACTIVE_CURSOR, "cursor", None)
    if cur is not None and cur.video_path == str(video_path):
        return cur.read(wanted)
    with _FrameCursor(str(video_path)) as one:
        return one.read(wanted)



def _sample_frames(frames: Sequence[int], k: int) -> List[int]:
    """帧号序列的均匀采样(恒含首末),升序返回。

    ``k ≤ 1`` 只取一个点(跨中点:单点确认取跨中最有代表性;旧实现按
    ``i*(n-1)/(k-1)`` 计算,``k=1`` 直接 ZeroDivisionError——确认帧数
    按跨度增长后下限恰为 1,故一并修掉)。``k`` 超过帧数时返回全部帧。
    """
    n = len(frames)
    k = max(1, int(k))
    if n <= k:
        return list(frames)
    if k == 1:
        idx = {(n - 1) // 2}
    else:
        # 均匀取 k 个下标:i*(n-1)/(k-1),恒含 0 与 n-1
        idx = {round(i * (n - 1) / (k - 1)) for i in range(k)}
    return [frames[i] for i in sorted(idx)]


def _effective_block_frames(block_max_frames: int,
                            video_height: Optional[float]) -> int:
    """按分辨率归一的有效块帧数(字节预算恒定,与像素阈值归一同口径)。

    单块驻留帧的字节数 ≈ 帧数 × 宽 × 高 × 3,分辨率翻倍即 4 倍,因此与
    ``verify_*`` 的像素阈值一样按 ``(1080 / video_height)²`` 折算帧数::

        有效块帧数 = min(block_max_frames,
                        max(MIN_BLOCK_FRAMES,
                            round(block_max_frames × (1080 / video_height)²)))

    - ``block_max_frames ≤ 0`` 的「不分块」语义不做归一,原样返回;
    - ``video_height`` 缺省 ``None``、非正、或恰为 1080 时系数为 1.0,
      原样返回(逐字节不变);
    - 归一结果以配置值为上界(绝不超过 ``block_max_frames``),``max`` 到
      ``MIN_BLOCK_FRAMES`` 只在下限仍不超过配置值时生效:配置本身比
      ``MIN_BLOCK_FRAMES`` 还小时以配置为准。
    """
    limit = int(block_max_frames)
    if limit <= 0 or video_height is None:
        return limit
    height = float(video_height)
    if not height > 0:
        return limit
    factor = (1080.0 / height) ** 2
    if factor == 1.0:
        return limit
    return min(limit, max(MIN_BLOCK_FRAMES, int(round(limit * factor))))


def _chunk_frames(frames: Sequence[int], limit: int) -> List[List[int]]:
    """把升序帧号序列切成 ≤ ``limit`` 帧的连续小块(``limit ≤ 0`` 不切)。

    只限制单批顺序解码的驻留帧数(内存界),不改变任何帧的解码方式。
    """
    lo = int(limit) if limit is not None else 0
    if lo <= 0 or len(frames) <= lo:
        return [list(frames)] if frames else []
    return [list(frames[i:i + lo]) for i in range(0, len(frames), lo)]


def _block_wanted(block: Sequence[Tuple[int, LineTrack, List[int], int]]) -> Set[int]:
    """块内所有行的采样帧 + 参考帧并集(该块一次解码要覆盖的帧号)。"""
    wanted: Set[int] = set()
    for _li, _lt, samples, ref_ok in block:
        wanted.update(samples)
        wanted.add(ref_ok)
    return wanted


def _chunk_plans(
    plans: Sequence[Tuple[int, LineTrack, List[int], int]],
    block_max_frames: int,
) -> List[List[Tuple[int, LineTrack, List[int], int]]]:
    """按时间局部性把行 plan 分块,限制单块解码帧并集(纯内存优化)。

    - plans 按首采样帧升序贪心聚合:下一行的采样范围与当前块范围重叠
      (首采样 ≤ 块内最大采样帧)则并入,否则另起一块——同期出现的行
      采样帧大概率落在同一时段,一块内一次顺序解码即可覆盖;
    - 并入后块内帧并集超过 ``block_max_frames`` 也另起块:可见期整体
      重叠的多行否则会聚成一块,失去内存上界。行自身采样 ≤ sample_max,
      单行天然有界;被拆开的行允许其帧在相邻块重复解码,只多花解码时间。

    判定不变性:每行的匹配与判定只消费该行自己的采样帧和该行已测采样
    序列(窗口中心 = 预测 + 该行此前实测偏移的插值外推,外推与解码顺序
    无关,见 ``_match_line_samples``),与哪些行同块、先处理哪块无关——
    分块只改变解码批次,不改变任何一行的校验结果。
    """
    if block_max_frames is None or int(block_max_frames) <= 0:
        return [list(plans)]
    limit = int(block_max_frames)
    blocks: List[List[Tuple[int, LineTrack, List[int], int]]] = []
    cur: List[Tuple[int, LineTrack, List[int], int]] = []
    cur_frames: set = set()
    cur_hi: Optional[int] = None
    for plan in sorted(plans, key=lambda p: p[2][0]):
        pf = set(plan[2])
        if (cur and (cur_hi < plan[2][0]
                     or len(cur_frames | pf) > limit)):
            blocks.append(cur)
            cur, cur_frames, cur_hi = [], set(), None
        cur.append(plan)
        cur_frames |= pf
        cur_hi = plan[2][-1] if cur_hi is None else max(cur_hi, plan[2][-1])
    if cur:
        blocks.append(cur)
    return blocks


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


def _normalize_roi_quad(roi_quad) -> np.ndarray:
    """ROI 四边形归一:(4,2) float64、凸包、正鞋面积顶点顺序。

    用户手绘顶点顺序/方向不保证;凸包保证半平面判据的凸性前提。点数不
    足 4、含非有限坐标或退化到无面积时抛 :class:`ValueError`(调用方按
    「禁用钳制」降级,不中断校验)。
    """
    pts = np.asarray(roi_quad, dtype=np.float64).reshape(-1, 2)
    if pts.shape != (4, 2) or not np.all(np.isfinite(pts)):
        raise ValueError(f"invalid roi quad: {roi_quad!r}")
    import cv2

    hull = cv2.convexHull(pts.astype(np.float32).reshape(-1, 1, 2))
    quad = hull.reshape(-1, 2).astype(np.float64)
    if quad.shape[0] < 3:
        raise ValueError(f"degenerate roi quad: {roi_quad!r}")
    x, y = quad[:, 0], quad[:, 1]
    area = 0.5 * float(np.dot(x, np.roll(y, -1)) - np.dot(y, np.roll(x, -1)))
    if abs(area) < 1e-6:
        raise ValueError(f"degenerate roi quad: {roi_quad!r}")
    if area < 0:
        quad = quad[::-1].copy()
    return quad


def _quad_inside_mask(xs, ys, quad: np.ndarray,
                      margin_px: float) -> np.ndarray:
    """候选中心网格是否落在凸四边形(外扩 ``margin_px``)内。

    ``quad`` 须为 :func:`_normalize_roi_quad` 的正鞋面积顺序(该顺序下
    内部点对每条有向边的叉积 ≤ 0);带符号距离 ≤ +margin_px 即视为在内
    (允许向外扩 ``margin_px``)。``xs``/``ys`` 一维坐标网格,返回
    ``(|ys|, |xs|)`` 布尔阵。
    """
    grid_x = np.asarray(xs, dtype=np.float64)[None, :]
    grid_y = np.asarray(ys, dtype=np.float64)[:, None]
    inside = np.ones((grid_y.shape[0], grid_x.shape[1]), dtype=bool)
    n = len(quad)
    for i in range(n):
        p = quad[i]
        q = quad[(i + 1) % n]
        ex, ey = float(q[0] - p[0]), float(q[1] - p[1])
        length = float(np.hypot(ex, ey))
        if length < 1e-9:
            continue
        cross = (grid_x - p[0]) * ey - (grid_y - p[1]) * ex
        inside &= (cross / length) <= float(margin_px)
    return inside


def _match_center(frame: np.ndarray, patch: np.ndarray,
                  center: Tuple[float, float], radius: float,
                  patch_size: Tuple[int, int],
                  restrict: Optional[Tuple[np.ndarray, float]] = None,
                  stats: Optional[Dict[str, int]] = None,
                  adopt_score: float = -1.0) -> Tuple[float, float, float]:
    """在 frame 的预测位置邻域内模板匹配,返回 (实测中心 x, y, 置信度)。

    匹配失败(窗口完全越界)返回 (-1, -1, -1)。峰值经 3×3 二次曲面
    细化到亚像素。

    ``restrict`` = (凸四边形顶点, 外扩 px) 时启用 ROI 钳制——**只在 ROI
    内存在可采纳峰(分数 ≥ ``adopt_score``,调用方传 min_score)时**改取
    ROI 内最优峰(全局最优峰在 ROI 外的次数计入 ``stats['clamped']``);
    ROI 内无可采纳峰时**回退全局峰**(与不钳制的旧行为逐字节一致):
    滚出 ROI 但仍在画面上可见的文字(DMG 邮件屏滚出行)照常跟随,绝不
    因钳制被判失配而出时间空洞。回退次数计入 ``stats['fallback']``,
    其中全局峰落在 ROI 外的次数(「滚出仍可见」与「消失后假锁 ROI 外
    纹理」两种形态的观测计数)计入 ``stats['fallback_outside']``。
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
    # 窗口原点(含镜像填充的坐标回换):res 单元 (rx,ry) 的实测中心 =
    # (wx+rx+(pw-1)/2, wy+ry+(ph-1)/2)。
    wx = cx0 - px0
    wy = cy0 - py0

    def _refine(r: np.ndarray, peak: Tuple[int, int]) -> Tuple[float, float]:
        """3×3 二次曲面亚像素细化(边界峰不外推),返回细化后的 (rx,ry)。"""
        rxi, ryi = float(peak[0]), float(peak[1])
        if 0 < peak[0] < r.shape[1] - 1 and 0 < peak[1] < r.shape[0] - 1:
            dl, dc, dr = float(r[peak[1], peak[0] - 1]), float(r[peak[1], peak[0]]), float(r[peak[1], peak[0] + 1])
            denom = dl - 2 * dc + dr
            if abs(denom) > 1e-9:
                rxi += max(-0.5, min(0.5, 0.5 * (dl - dr) / denom))
            dt, dm, db = float(r[peak[1] - 1, peak[0]]), float(r[peak[1], peak[0]]), float(r[peak[1] + 1, peak[0]])
            denom = dt - 2 * dm + db
            if abs(denom) > 1e-9:
                ryi += max(-0.5, min(0.5, 0.5 * (dt - db) / denom))
        return rxi, ryi

    if restrict is None:
        _, score, _, peak = cv2.minMaxLoc(res)
        rx, ry = _refine(res, peak)
        mx = wx + rx + (pw - 1) / 2.0
        my = wy + ry + (ph - 1) / 2.0
        return (float(mx), float(my), float(score))

    # —— ROI 钳制:先算全局峰(回退基准,与旧行为逐字节一致),再算 ROI
    #    内最优峰;ROI 内分数达到 adopt_score 才改写,否则回退全局峰。
    quad, margin_px = restrict
    _, g_score, _, g_peak = cv2.minMaxLoc(res)
    g_rx, g_ry = _refine(res, g_peak)
    g_mx = wx + g_rx + (pw - 1) / 2.0
    g_my = wy + g_ry + (ph - 1) / 2.0

    mxs = wx + np.arange(res.shape[1]) + (pw - 1) / 2.0
    mys = wy + np.arange(res.shape[0]) + (ph - 1) / 2.0
    res_roi = np.where(_quad_inside_mask(mxs, mys, quad, margin_px),
                       res, np.float32(-1.0))
    _, r_score, _, r_peak = cv2.minMaxLoc(res_roi)
    if r_score < adopt_score:
        if stats is not None:
            stats["fallback"] = stats.get("fallback", 0) + 1
            if not bool(_quad_inside_mask(
                    np.array([g_mx]), np.array([g_my]), quad,
                    margin_px)[0, 0]):
                stats["fallback_outside"] = stats.get(
                    "fallback_outside", 0) + 1
        return (float(g_mx), float(g_my), float(g_score))
    if stats is not None and g_score > r_score + 1e-6:
        stats["clamped"] = stats.get("clamped", 0) + 1
    # 亚像素细化用**真实分数面** res(掩膜只在选峰时生效):掩膜边界峰的
    # 邻居在 res_roi 里是 -1,拿它进抛物线会把细化拉向 ROI 内侧——钳制
    # 在「全局峰本就在 ROI 内」时必须逐位等于不钳制(峰值索引相同,对
    # res 细化即逐位相同),该不变量被 test_roi_clamp_noop_when_inroi 固化。
    r_rx, r_ry = _refine(res, r_peak)
    r_mx = wx + r_rx + (pw - 1) / 2.0
    r_my = wy + r_ry + (ph - 1) / 2.0
    return (float(r_mx), float(r_my), float(r_score))


def _probe_frame(img: np.ndarray, patch: np.ndarray,
                 center: Tuple[float, float], cfg: VerifyConfig,
                 restrict: Optional[Tuple[np.ndarray, float]] = None,
                 stats: Optional[Dict[str, int]] = None
                 ) -> Tuple[float, float, float, Optional[float]]:
    """单帧分级匹配:常规半径失配(< drop_score)时以更大半径重试一次。

    重试用于区分「漂移累积/搜索半径不够」与「文字真的没了」:前者会在
    更大窗口里给出高置信峰——重试分数 ≥ min_score 时该帧照常纳入实测
    序列,落在 [drop_score, min_score) 时保留但标记为边界;后者两次都
    失配,删除判定的证据强度升级。两次匹配的窗口中心相同(预测 + 该行
    此前实测偏移的外推),只是窗口变大。返回 (实测中心 x, y, 最终采纳
    分数, 重试分数或 None)。

    ``restrict``/``stats`` 透传给两次 :func:`_match_center`(ROI 钳制与
    诊断计数)。
    """
    size = (patch.shape[1], patch.shape[0])
    mx, my, score = _match_center(
        img, patch, center, cfg.search_radius_px, size,
        restrict=restrict, stats=stats, adopt_score=cfg.min_score)
    retry_score: Optional[float] = None
    if score < cfg.drop_score and cfg.retry_radius_px > cfg.search_radius_px:
        rx, ry, rscore = _match_center(
            img, patch, center, cfg.retry_radius_px, size,
            restrict=restrict, stats=stats, adopt_score=cfg.min_score)
        retry_score = rscore
        if rscore > score:
            mx, my, score = rx, ry, rscore
    return mx, my, score, retry_score


def _mismatch_runs(samples: Sequence[int], scores: Dict[int, float],
                   drop_score: float) -> List[List[int]]:
    """采样序列中「连续硬失配」的极大 run(分数缺失视同失配,与既有
    行为一致:解码不到的帧按失配处理)。

    分数 ≥ drop_score 的采样(含 [drop_score, min_score) 的边界分)切断
    run:边界分是「文字大概率还在、只是匹配质量差」的证据,按分级语义
    倾向保留,不参与删除配对。run(长度 ≥2)与旧「相邻两采样都失配才
    配对」等价:相邻对的并集 = run 首末采样隔开的跨度。
    """
    runs: List[List[int]] = []
    cur: List[int] = []
    for f in samples:
        if scores.get(f, -1.0) < drop_score:
            cur.append(f)
        elif cur:
            runs.append(cur)
            cur = []
    if cur:
        runs.append(cur)
    return runs


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


@dataclass
class _LineWork:
    """块内单行的两阶段中间态。

    阶段 A(``_match_line_samples``)填充 rep/patch/sample_data/
    confirm_picks,阶段 B(``_finalize_line``)消费。两阶段之间只依赖
    该行自身的数据与(补帧后的)帧图,不依赖其他行——这是「分块不改
    变校验结果」的关键。
    """

    li: int
    lt: LineTrack
    samples: List[int]
    ref_ok: int
    rep: LineVerifyReport
    patch: Optional[np.ndarray] = None
    # 有效采样 (帧号, 分数, (dx, dy)),升序;插值外推与静态判定的依据
    sample_data: List[Tuple[int, float, Tuple[float, float]]] = field(
        default_factory=list)
    # 超长失配跨度 (a, b) → 跨内确认帧(阶段 A 预选,阶段 B 前整块统一
    # 补解码,避免逐行多次开视频)
    confirm_picks: Dict[Tuple[int, int], List[int]] = field(
        default_factory=dict)


def _match_line_samples(
    li: int, lt: LineTrack, samples: List[int], ref_ok: int,
    frames_img: Dict[int, np.ndarray], cfg: VerifyConfig,
    restrict: Optional[Tuple[np.ndarray, float]] = None,
) -> _LineWork:
    """阶段 A:选参考补丁 + 逐采样匹配(带扩半径重试与边界标记)。

    窗口中心 = 预测位置 + 该行此前实测偏移的插值外推(顺序跟踪):单应
    声称静止而文字匀速漂移时,漂移会累积超过搜索半径,纯预测窗口在远端
    采样点再也够不到真值;带着已测偏移走,只要相邻采样间的增量不超过
    半径即可。外推只依赖该行自己按帧号升序的已测采样序列,与解码分块/
    处理批次无关——因此分块不改变任何一行的匹配结果。

    ``restrict`` = (ROI 凸四边形, 外扩 px) 时实测中心被限制在 ROI 内
    (见 :func:`_match_center`);钳制丢弃全局峰的次数记入 ``rep.n_clamped``。
    """
    rep = LineVerifyReport(n_samples=len(samples))
    work = _LineWork(li=li, lt=lt, samples=samples, ref_ok=ref_ok, rep=rep)
    box_w = float(lt.ref_box[2]) - float(lt.ref_box[0])
    box_h = float(lt.ref_box[3]) - float(lt.ref_box[1])

    # 自适应补丁:参考帧位姿恒等,但该行的文字未必在参考帧已出现
    # (聊天界面逐条浮现)——按时间顺序尝试各采样帧,取第一个补丁
    # 有纹理的帧作参考;其位姿中心即补丁基准位置(该帧偏移恒为 0,
    # 与「offset = 实测 − 预测」的定义自动一致)。
    for f in [ref_ok] + [s for s in samples if s != ref_ok]:
        img = frames_img.get(f)
        if img is None:
            continue
        candidate = _gray_patch(img, lt.poses[f].center, box_w, box_h)
        if candidate is not None and _is_textured(candidate):
            work.patch = candidate
            break
    if work.patch is None:
        return work

    sample_data = work.sample_data
    clamp_stats: Dict[str, int] = {}
    for f in samples:
        img = frames_img.get(f)
        if img is None:
            continue
        pred = lt.poses[f].center
        if sample_data:
            dx0, dy0 = _interp_offsets(sample_data, [f])[f]
            center = (pred[0] + dx0, pred[1] + dy0)
        else:
            center = pred
        mx, my, score, retry = _probe_frame(
            img, work.patch, center, cfg, restrict=restrict,
            stats=clamp_stats)
        if retry is not None:
            rep.retry_scores[f] = retry
        rep.scores[f] = score
        if score >= cfg.min_score:
            sample_data.append((f, score, (mx - pred[0], my - pred[1])))
        elif score >= cfg.drop_score:
            # 边界分:文字大概率还在(运动模糊/光照突变),保留并标记,
            # 不参与失配删除(见 _mismatch_runs)。
            rep.borderline.append(f)
    rep.n_good = len(sample_data)
    rep.n_clamped = clamp_stats.get("clamped", 0)
    rep.n_roi_fallback = clamp_stats.get("fallback_outside", 0)

    # 预选超长失配跨度的跨内确认帧(阶段 B 前整块统一补解码)。
    # 确认帧数随跨度恢复:min(cap, max(1, 跨度帧长 // long_span_frames))
    # ——旧实现恒为常数 cap(=3),2000 帧的长跨度也只有 3 个确认点,证据
    # 密度与跨度无关;按跨度线性增长后,每增加一个 long_span_frames 就多
    # 一个确认点,并以 span_confirm_max 封顶(额外解码代价 ≤ cap 帧/跨度,
    # 且只补缺帧)。
    all_ok = sorted(lt.poses)
    lsf = max(1, int(cfg.long_span_frames))
    for run in _mismatch_runs(samples, rep.scores, cfg.drop_score):
        a, b = run[0], run[-1]
        if b - a <= cfg.long_span_frames:
            continue
        interior = [f for f in all_ok if a < f < b]
        if interior:
            picks_n = min(max(1, int(cfg.span_confirm_max)),
                          max(1, (b - a) // lsf))
            work.confirm_picks[(a, b)] = _sample_frames(interior, picks_n)
    return work


def _finalize_line(
    work: _LineWork, frames_img: Dict[int, np.ndarray],
    cfg: VerifyConfig, log: Callable[[str], None],
    restrict: Optional[Tuple[np.ndarray, float]] = None,
) -> Tuple[Optional[LineTrack], LineVerifyReport]:
    """阶段 B:失配跨度分级判定(删除/保留留痕)→ 静止吸附或偏移插值。

    返回 (新轨迹或 None, 报告);None 表示该行保持原轨迹(补丁不可用
    或有效采样不足)。``restrict`` 透传给确认帧探测的 :func:`_probe_frame`
    (ROI 钳制与阶段 A 同一口径)。
    """
    li, lt, samples = work.li, work.lt, work.samples
    rep = work.rep
    if work.patch is None:
        # 补丁缺纹理(纯色块/细实线条)时归一化互相关无定义,会在
        # 均匀背景处产生系统性伪匹配——宁可不校正该行。
        log(f"pose-verify: line {li} ({lt.text!r}) skipped: "
            "reference patch unavailable or textureless on all samples")
        return None, rep

    all_ok_frames = sorted(lt.poses)
    pose_ref = lt.poses[work.ref_ok]

    # —— 不可见跨度分级判定:连续硬失配采样(≥2,单个失配不构成跨度,
    # 与旧配对语义一致)的首末隔开候选跨度,按证据强度分四级删除/保留:
    # 短跨度双失配直接删(旧语义,防幽灵文字);超长跨度需跨内确认帧
    # 仍全部硬失配才删(两个稀疏采样失配不足以支撑删掉数秒位姿);
    # 确认帧见到 ≥drop_score 的匹配(含边界分)则整段保留——删除会
    # 制造可见时间空洞;确认帧**一个都没测到**(读不出来/跨度内无补采帧)
    # 按「未测」处理同样保留——证据不足不等于「已确认硬失配」。中间隔着
    # 有效采样的一对失配不成 run:那是「消失又出现」的可见段。
    dropped: set = set()
    for run in _mismatch_runs(samples, rep.scores, cfg.drop_score):
        if len(run) < 2:
            continue
        a, b = run[0], run[-1]
        span_ok = [f for f in all_ok_frames if a <= f <= b]
        evidence = {f: rep.scores.get(f, -1.0) for f in run}
        long_span = b - a > cfg.long_span_frames
        picks = work.confirm_picks.get((a, b)) if long_span else None
        visible = False
        tested = 0
        if picks:
            # 确认帧的窗口中心同样带该行实测偏移外推;探到边界分也算
            # 可见(分级语义倾向保留)。缺帧(解码不到)不计入 tested,
            # 视同未测——与采样缺帧(视同失配)的处理刻意不同:采样帧
            # 的失配是「测到分数低」,确认帧缺失是「没测成」。
            priors = _interp_offsets(work.sample_data, picks)
            clamp_stats: Dict[str, int] = {}
            for f in picks:
                img = frames_img.get(f)
                if img is None:
                    continue
                tested += 1
                pred = lt.poses[f].center
                dx, dy = priors[f]
                _mx, _my, sc, _retry = _probe_frame(
                    img, work.patch, (pred[0] + dx, pred[1] + dy), cfg,
                    restrict=restrict, stats=clamp_stats)
                evidence[f] = sc
                if sc >= cfg.drop_score:
                    visible = True
                    break
            work.rep.n_clamped += clamp_stats.get("clamped", 0)
            work.rep.n_roi_fallback += clamp_stats.get("fallback_outside", 0)
        if long_span and not visible and not tested:
            # 未测(一个确认帧都没测到):不得按「已确认」删除,整段保留
            # 并给出独立 reason,让日志/离线分析能区分「确认后删除」
            # 「确认后保留」「无法确认而保留」三种。
            rep.spans.append(SpanDecision(
                start=a, end=b, frames=span_ok, dropped=False,
                reason="long_span_unconfirmed", scores=evidence))
            log(f"pose-verify: line {li} ({lt.text!r}) kept suspect span "
                f"{a}-{b}: no confirm frame could be probed (unconfirmed)")
            continue
        if long_span and visible:
            rep.spans.append(SpanDecision(
                start=a, end=b, frames=span_ok, dropped=False,
                reason="long_span_kept_visible", scores=evidence))
            log(f"pose-verify: line {li} ({lt.text!r}) kept suspect span "
                f"{a}-{b}: interior confirm frame matched")
            continue
        dropped.update(span_ok)
        rep.spans.append(SpanDecision(
            start=a, end=b, frames=span_ok, dropped=True,
            reason=("long_span_confirmed" if long_span
                    else "double_mismatch"),
            scores=evidence))

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
            and len(work.sample_data) >= cfg.min_static_samples):
        centers = np.array([
            (lt.poses[f].center[0] + d[0], lt.poses[f].center[1] + d[1])
            for f, _s, d in work.sample_data])
        med = np.median(centers, axis=0)
        dev = float(np.max(np.hypot(
            centers[:, 0] - med[0], centers[:, 1] - med[1])))
        rep.max_dev_px = dev
        if dev <= cfg.static_tol_px:
            static = True

    new_poses: Dict[int, LinePose] = {}
    if static:
        med_x = float(np.median([lt.poses[f].center[0] + d[0]
                                 for f, _s, d in work.sample_data]))
        med_y = float(np.median([lt.poses[f].center[1] + d[1]
                                 for f, _s, d in work.sample_data]))
        for f in keep_frames:
            new_poses[f] = LinePose(
                center=(med_x, med_y),
                angle_deg=pose_ref.angle_deg,
                scale=pose_ref.scale,
            )
        rep.static = True
        rep.corrected = True
        log(f"pose-verify: line {li} ({lt.text!r}) static at "
            f"({med_x:.1f},{med_y:.1f}), snapped {len(new_poses)} pose(s)"
            + (f", roi_fallback={rep.n_roi_fallback}"
               if rep.n_roi_fallback else ""))
    elif work.sample_data:
        offsets = _interp_offsets(work.sample_data, keep_frames)
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
            span_txt = ",".join(
                f"[{d.start}-{d.end}:{d.reason}"
                f"{'/dropped' if d.dropped else '/kept'}]"
                for d in rep.spans)
            log(f"pose-verify: line {li} ({lt.text!r}) corrected "
                f"({rep.n_good}/{rep.n_samples} samples), "
                f"dropped {rep.dropped_frames} invisible frame(s)"
                + (f" spans={span_txt}" if span_txt else "")
                + (f" roi_fallback={rep.n_roi_fallback}"
                   if rep.n_roi_fallback else ""))
    else:
        return None, rep

    return (LineTrack(
        text=lt.text, ref_box=lt.ref_box, height=lt.height,
        poses=new_poses), rep)


def verify_line_tracks(
    video_path: str,
    tracks: Sequence[TrackedQuad],
    line_tracks: Sequence[LineTrack],
    cfg: VerifyConfig,
    log: Optional[Callable[[str], None]] = None,
    video_height: Optional[float] = None,
    roi_quad: Optional[Sequence[Sequence[float]]] = None,
    roi_margin_px: Optional[float] = None,
) -> Tuple[List[LineTrack], Dict[int, LineVerifyReport]]:
    """逐行实测校正:返回 (新 LineTrack 列表, 逐行诊断)。

    输入轨迹不修改;单行失败(补丁取不到、有效采样不足)保持该行原轨迹。
    视频打不开抛 :class:`RuntimeError`,由调用方决定降级策略。

    ``roi_quad``(用户手绘 ROI 的四顶点,屏幕坐标)与 ``cfg.roi_clamp``
    (默认开)同给出时启用 **ROI 包含门控**:全局最优匹配峰落在 ROI(按
    ROI 最小边 10%、至少 4px 外扩;``roi_margin_px`` 显式覆盖)之外、且
    ROI 内存在可采纳峰(分数 ≥ ``min_score``)时,改取 ROI 内最优峰
    (计入 ``rep.n_clamped``)。**只在 ROI 内有可采纳峰时改写**:ROI 内
    无峰时回退全局峰(与不钳制的旧行为逐字节一致)——滚出 ROI 但仍可见
    的文字照常跟随,钳制绝不制造失配、绝不删除可见位姿。回退且全局峰在
    ROI 外的采样计入 ``rep.n_roi_fallback``(仅观测:它同时覆盖「滚出
    仍可见」的正确回退与「消失后假锁 ROI 外纹理」的残留形态,后者待真实
    素材暴露后再以该计数为依据单独立行为约束)。4K 固定歌词条
    实测中,分辨率归一后的大搜索半径(80/160px)相对 155px 高的条带过大,
    模板在条带外锁到背景纹理,把实测正确的位姿改到偏移 115–215px 处并
    误删可见段——门控后 ROI 内真值峰优先,伪峰被抑制。ROI 非法(点数/
    有限性/退化)时禁用钳制并 log 留痕,不中断校验。

    解码分两遍扫描(内存/解码代价优化,见模块文档):

    1. 行 plan 按时间局部性分块(``_chunk_plans``,单块帧并集 ≤ 有效块帧数
       ``_effective_block_frames``),块按时间顺序处理,共用一个顺序读取
       游标 ``_FrameCursor``:逐块解码 → 阶段 A → 记下超长失配跨度的
       ``confirm_picks`` → 立即释放该块帧(游标只保留后续批次还要用的帧);
    2. 确认帧并集按时间切成有界小块顺序解码;行一旦确认帧到齐立刻做
       分级判定与落地(阶段 B),随后释放该行确认帧。

    校验结果与「整段一次性解码 + 逐行匹配」完全一致:每行的匹配只消费该行
    自己的采样帧与已测采样序列,分块/分遍只改变解码批次与释放时机(纯
    内存与解码代价优化);阶段 B 只查确认帧(采样帧的分数/偏移早已存进
    ``_LineWork``),确认帧缺失时保持既有「未测 → 保留整段」语义。

    ``video_height``(画面高,px;可选)给出时把**绝对像素阈值**按
    ``video_height/1080`` 等比缩放(以 1080p 为基准):``static_tol_px``
    与 ``search_radius_px``/``retry_radius_px`` 都是绝对像素,对分辨率
    敏感——4K 下 static_tol 的相对容差只有 1080p 的一半(静止行更易被判
    运动、丢塌缩),低分辨率下搜索半径相对画面偏大(更容易匹配到邻域
    伪峰)。同一口径还用于**有效块帧数**归一(``_effective_block_frames``):
    单块驻留字节数 = 帧数 × 宽 × 高 × 3,分辨率翻倍即 4 倍,按
    ``(1080/video_height)²`` 折算帧数才能保持字节预算恒定。缩放范围仅这三个
    像素阈值与有效块帧数:采样帧数、分数门限(``min_score``/``drop_score``)、
    跨度帧数门限(``long_span_frames``/``span_confirm_max``)都是相对量或
    帧数,与像素尺寸无关;归一口径与
    :func:`core.motion_ass.synthesize_events` 的 ``video_height`` 一致
    (同为 ``video_height/1080`` 基准)。缺省 ``None`` 时不做任何缩放,
    行为与不带该参数时逐字节一致(传入 1080 时缩放系数恰为 1.0,同样不变)。
    """
    if log is None:
        log = lambda msg: logger.info(msg)  # noqa: E731

    if video_height is not None and float(video_height) > 0:
        px_scale = float(video_height) / 1080.0
        if px_scale != 1.0:
            cfg = replace(
                cfg,
                search_radius_px=float(cfg.search_radius_px) * px_scale,
                retry_radius_px=float(cfg.retry_radius_px) * px_scale,
                static_tol_px=float(cfg.static_tol_px) * px_scale,
            )
            log(f"pose-verify: pixel thresholds scaled x{px_scale:.3f} "
                f"for {float(video_height):.0f}px height "
                f"(static_tol={cfg.static_tol_px:.2f}px, "
                f"search_radius={cfg.search_radius_px:.2f}px, "
                f"retry_radius={cfg.retry_radius_px:.2f}px)")

    ok_frames = sorted(t.frame_num for t in tracks if t.status == "ok")
    if not ok_frames:
        return list(line_tracks), {}

    # —— ROI 包含门控(「校正不得把行移出所属 ROI」):归一失败则禁用。
    restrict: Optional[Tuple[np.ndarray, float]] = None
    if roi_quad is not None and cfg.roi_clamp:
        try:
            clamp_quad = _normalize_roi_quad(roi_quad)
        except ValueError as exc:
            log(f"pose-verify: ROI clamp disabled ({exc}); corrections "
                "are not ROI-bounded")
            clamp_quad = None
        if clamp_quad is not None:
            if roi_margin_px is not None:
                clamp_margin = float(roi_margin_px)
            else:
                bw = float(clamp_quad[:, 0].max() - clamp_quad[:, 0].min())
                bh = float(clamp_quad[:, 1].max() - clamp_quad[:, 1].min())
                clamp_margin = max(4.0, 0.10 * min(bw, bh))
            restrict = (clamp_quad, clamp_margin)
            log(f"pose-verify: ROI clamp active (margin="
                f"{clamp_margin:.1f}px); measured centers are restricted "
                "to the drawn ROI")

    plans: List[Tuple[int, LineTrack, List[int], int]] = []
    for li, lt in enumerate(line_tracks):
        frames = sorted(lt.poses)
        if len(frames) < 3:
            continue
        samples = _sample_frames(frames, max(3, int(cfg.sample_max)))
        ref_ok = frames[0]  # 参考帧位姿恒等(build_line_tracks 约定)
        plans.append((li, lt, samples, ref_ok))
    if not plans:
        return list(line_tracks), {}

    eff_block = _effective_block_frames(cfg.block_max_frames, video_height)
    blocks = _chunk_plans(plans, eff_block)
    batch_wanted = [_block_wanted(b) for b in blocks]

    # —— 第 1 遍:块按时间顺序解码(共享顺序游标)→ 阶段 A → 记下 confirm_picks
    #    → 立即释放该块帧(游标按需求图保留后续批次还要用的帧)。
    prefetch_limit = 0
    if eff_block > 0:
        prefetch_limit = max(MIN_PREFETCH_FRAMES,
                             _PREFETCH_BLOCK_FACTOR * eff_block)
    cursor = _FrameCursor(video_path, prefetch_limit=prefetch_limit)
    cursor.plan(batch_wanted)
    works: List[_LineWork] = []
    try:
        with _shared_cursor(cursor):
            for block, wanted in zip(blocks, batch_wanted):
                frames_img = _decode_frames(video_path, wanted)
                # 阶段 A:块内逐行补丁选择 + 采样匹配(失配带扩半径重试)。
                works.extend(
                    _match_line_samples(li, lt, samples, ref_ok, frames_img,
                                        cfg, restrict=restrict)
                    for li, lt, samples, ref_ok in block
                )
                del frames_img, block, wanted  # 块帧即刻释放(内存 = 单块)
    finally:
        cursor.close()

    # —— 第 2 遍:确认帧并集按时间分块(有界)顺序解码;行确认帧到齐即做
    #    阶段 B(分级判定 + 静止吸附/偏移插值)并释放该行确认帧。
    results: Dict[int, Tuple[Optional[LineTrack], LineVerifyReport]] = {}
    pending: List[Tuple[_LineWork, List[int]]] = [
        (w, sorted({f for picks in w.confirm_picks.values() for f in picks}))
        for w in works if w.confirm_picks
    ]
    if pending:
        confirm_union = sorted({f for _w, fs in pending for f in fs})
        frames_confirm: Dict[int, np.ndarray] = {}
        remaining = pending
        with _FrameCursor(video_path) as ccursor, _shared_cursor(ccursor):
            for chunk in _chunk_frames(confirm_union, eff_block):
                frames_confirm.update(_decode_frames(video_path, chunk))
                # 该行全部确认帧都落在已解码前缀里 → 可以落地(缺帧时
                # ``frames_confirm`` 里没有对应键,照旧按「未测」处理)。
                done = [i for i, (_w, fs) in enumerate(remaining)
                        if fs[-1] <= chunk[-1]]
                for i in done:
                    w = remaining[i][0]
                    results[w.li] = _finalize_line(w, frames_confirm, cfg,
                                                   log, restrict=restrict)
                done_set = set(done)
                remaining = [e for i, e in enumerate(remaining)
                             if i not in done_set]
                # 只保留还没落地的行仍需的确认帧(内存 = 在飞行确认帧)
                still = {f for _w, fs in remaining for f in fs}
                frames_confirm = {f: img for f, img in frames_confirm.items()
                                  if f in still}
    # 没有超长失配跨度的行(绝大多数)压根不需要确认帧:空字典落地,与旧
    # 路径「确认帧全缺席」不可区分——它们本来就没有 picks 可补测。
    for w in works:
        if w.li not in results:
            results[w.li] = _finalize_line(w, {}, cfg, log, restrict=restrict)

    reports: Dict[int, LineVerifyReport] = {}
    new_tracks: List[LineTrack] = []
    for li, lt in enumerate(line_tracks):
        res = results.get(li)
        if res is None:
            new_tracks.append(lt)  # 帧数不足未建 plan 的行保持原轨迹
            continue
        track, rep = res
        reports[li] = rep
        new_tracks.append(track if track is not None else lt)
    return new_tracks, reports
