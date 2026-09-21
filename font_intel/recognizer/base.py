# font_intel/recognizer/base.py
"""字体识别器接口与工厂（T3.1）：crop + 已知文本 → Top-N 字体候选。

定位（方案 §3.1/§3.2，docs/feature_plan_font_translation_compliance.md）
------------------------------------------------------------------------
字体识别走"多开源项目组合"路线：深度模型（YuzuMarker.FontDetection，
见 ``yuzu.py``）做**候选生成**，``glyph_rerank`` 字形重排做裁决。本模块
只定义统一接口与识别器链的构造/归并逻辑，不含任何具体识别算法。

对外 API
--------
- ``FontCandidate(name, score, source, font_path=None)``：候选数据类。
  ``score ∈ [0, 1]``，语义由 ``source`` 决定（yuzu = 模型 softmax 置信度；
  字形重排分数见 ``glyph_rerank.RankedCandidate``，不经本类表达）。
- ``FontRecognizer``：识别器抽象接口。实现契约：
  ① ``identify(text, crop_image, top_n)`` **绝不抛异常**——模型未加载、
  推理失败、依赖缺失一律返回 ``[]``（错误状态记录在实现自身，如
  ``state``/``last_error``，供日志与诊断）；② ``available`` 属性表达
  "依赖齐备且权重就位或可获取"；③ 构造函数零重活（不加载模型、不联网、
  不 import torch）——重活全部延迟到 identify。
- ``get_recognizer(cfg)``：按配置构造识别器链（当前 yuzu，可扩展）。
  **绝不抛异常**：单个识别器构造失败仅告警并跳过；torch/权重缺失时
  返回的链中相应对象 ``available=False``（链非空但可用项为空），调用方
  以 ``available`` 过滤。cfg 按 duck-typing 读取（``yuzu_weights_path``/
  ``yuzu_labels_path``/``allow_download``），缺省取各识别器默认值。
- ``collect_candidates(recognizers, text, crop_image, top_n)``：跑链并
  归并候选——按字体名去重（大小写不敏感，保留最高分，同分优先带
  ``font_path`` 者），按分数降序，截断 Top-N。链内单个识别器异常仅
  告警并继续，绝不向上抛。

torch 完全可选
--------------
本模块顶层零 torch 依赖；yuzu 识别器在工厂内**延迟导入**（import 放在
函数体内），未安装 torch 时工厂照常返回链对象，主流水线零影响。
"""

from __future__ import annotations

import abc
import logging
from collections import OrderedDict
from dataclasses import asdict, dataclass
from typing import Any, Iterable, List, Optional

logger = logging.getLogger(__name__)

# 默认 Top-N（识别器接口与链归并共用）。
DEFAULT_TOP_N = 5


# ── 数据结构 ─────────────────────────────────────────────────────


@dataclass
class FontCandidate:
    """识别器产出的单一字体候选。

    ``font_path`` 为候选对应的本机字体文件路径（识别器能解析时携带；
    缺失时由消费方经本机字体库索引解析，供字形重排渲染参考字形）。
    """

    name: str
    score: float
    source: str
    font_path: Optional[str] = None

    def as_dict(self) -> dict:
        return asdict(self)


# ── 识别器接口 ───────────────────────────────────────────────────


class FontRecognizer(abc.ABC):
    """字体识别器接口：crop + 已知文本 → Top-N 候选列表。

    注意 ``text`` 是统一接口的一部分（"OCR 已给出是什么字"是本项目
    识别链的前提假设）；具体识别器可自行决定是否消费——yuzu 模型只吃
    图像，text 仅做非空校验后忽略。
    """

    #: 候选来源标识（写入 FontCandidate.source）。
    source: str = "base"

    @property
    @abc.abstractmethod
    def available(self) -> bool:
        """当前是否可用（依赖齐备且权重就位/可获取）。"""

    @abc.abstractmethod
    def identify(
        self, text: str, crop_image: Any, top_n: int = DEFAULT_TOP_N
    ) -> List[FontCandidate]:
        """识别字块图像中的字体，返回按置信度降序的 Top-N 候选。

        实现契约：绝不抛异常；任何失败路径返回 []。
        """


# ── 工厂 ─────────────────────────────────────────────────────────


def get_recognizer(cfg: Any = None) -> List[FontRecognizer]:
    """按配置构造识别器链（当前：yuzu → 后续可扩展 DeepFont 等）。

    **绝不抛异常**：单个识别器构造失败仅告警并跳过。torch/权重缺失时
    返回的链中对象 ``available=False``——链非空但可用项为空，功能整体
    可关（方案 T3.1 验收标准）。构造零重活：不加载模型、不联网、
    不 import torch（yuzu 模块内部同样延迟导入 torch）。

    Args:
        cfg: ``font_intel.identify_stage.FontIdentifyConfig`` 或 None；
            按 duck-typing 读取 ``yuzu_weights_path`` / ``yuzu_labels_path``
            / ``allow_download`` 字段，缺省取各识别器默认值。
    """
    chain: List[FontRecognizer] = []
    try:
        # 延迟导入：torch 全可选，未安装时本工厂照常工作（链对象
        # available=False），主流水线其余部分零影响。
        from font_intel.recognizer.yuzu import YuzuFontRecognizer

        chain.append(YuzuFontRecognizer(
            weights_path=getattr(cfg, "yuzu_weights_path", None),
            labels_path=getattr(cfg, "yuzu_labels_path", None),
            allow_download=bool(getattr(cfg, "allow_download", True)),
        ))
    except Exception as exc:  # 识别器构造失败绝不阻断调用方
        logger.warning("yuzu 识别器构造失败（跳过，不中断）: %s", exc)
    # 后续识别器在此追加（同样 try/except 隔离）。
    return chain


# ── 链归并 ───────────────────────────────────────────────────────


def collect_candidates(
    recognizers: Iterable[FontRecognizer],
    text: str,
    crop_image: Any,
    top_n: int = DEFAULT_TOP_N,
) -> List[FontCandidate]:
    """跑识别器链并归并候选。

    - 去重：按字体名（大小写不敏感）合并，保留最高分；同分时优先
      携带 ``font_path`` 的条目（可直接参与字形重排）。
    - 排序：按分数降序（稳定），截断 Top-N。
    - 链内隔离：单个识别器异常仅告警并继续，绝不向上抛；非
      ``FontCandidate``/空名条目直接丢弃。
    """
    merged: "OrderedDict[str, FontCandidate]" = OrderedDict()
    for rec in recognizers or []:
        try:
            cands = rec.identify(text, crop_image, top_n=top_n)
        except Exception as exc:
            logger.warning(
                "识别器 %s 推理失败（链内隔离，跳过）: %s",
                getattr(rec, "source", "?"), exc,
            )
            continue
        for cand in cands or []:
            if not isinstance(cand, FontCandidate):
                continue
            name = str(cand.name).strip()
            if not name:
                continue
            key = name.casefold()
            prev = merged.get(key)
            score = float(cand.score)
            better = (
                prev is None
                or score > prev.score
                or (score == prev.score and cand.font_path
                    and not prev.font_path)
            )
            if better:
                merged[key] = FontCandidate(
                    name, score,
                    str(cand.source or getattr(rec, "source", "unknown")),
                    cand.font_path or (prev.font_path if prev else None),
                )
    out = sorted(merged.values(), key=lambda c: -c.score)
    try:
        limit = int(top_n)
    except (TypeError, ValueError):
        limit = DEFAULT_TOP_N
    return out[:limit] if limit > 0 else out
