# font_intel/recognizer/__init__.py
"""font_intel.recognizer：字体识别子包。

- ``base``（T3.1）：识别器接口 ``FontRecognizer`` / 候选 ``FontCandidate``
  / 工厂 ``get_recognizer`` / 链归并 ``collect_candidates``；
- ``yuzu``（T3.1）：YuzuMarker.FontDetection 候选生成器——**不在包
  ``__init__`` 导入**（torch 全可选，经 ``base.get_recognizer`` 延迟
  导入，未装 torch 时主流水线零影响）；
- ``glyph_rerank``（T3.2）：字形重排裁决器。注意它是"候选重排裁决器"
  而非字体识别器——分数仅供排序与置信度参考，不承诺全自动准确。
"""

from font_intel.recognizer.base import (
    DEFAULT_TOP_N,
    FontCandidate,
    FontRecognizer,
    collect_candidates,
    get_recognizer,
)
from font_intel.recognizer.glyph_rerank import (
    CANVAS_DEFAULT,
    FUSION_WEIGHTS,
    HOG_WEIGHT,
    NORM_SIZE,
    PHASH_WEIGHT,
    SSIM_WEIGHT,
    RankedCandidate,
    ReferenceRenderCache,
    compare_images,
    is_comparison_available,
    render_reference,
    rerank,
)

__all__ = [
    "CANVAS_DEFAULT",
    "DEFAULT_TOP_N",
    "FUSION_WEIGHTS",
    "HOG_WEIGHT",
    "NORM_SIZE",
    "PHASH_WEIGHT",
    "SSIM_WEIGHT",
    "FontCandidate",
    "FontRecognizer",
    "RankedCandidate",
    "ReferenceRenderCache",
    "collect_candidates",
    "compare_images",
    "get_recognizer",
    "is_comparison_available",
    "render_reference",
    "rerank",
]
