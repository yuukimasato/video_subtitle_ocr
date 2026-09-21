# font_intel/recognizer/__init__.py
"""font_intel.recognizer：字体识别子包。

现阶段（T3.2）只含字形重排裁决器 ``glyph_rerank``；识别器接口与
YuzuMarker 候选生成（T3.1 ``base.py``/``yuzu.py``）为后续任务，届时
在此包内扩展。注意 ``glyph_rerank`` 是"候选重排裁决器"而非字体识别
器——分数仅供排序与置信度参考，不承诺全自动准确。
"""

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
    "FUSION_WEIGHTS",
    "HOG_WEIGHT",
    "NORM_SIZE",
    "PHASH_WEIGHT",
    "SSIM_WEIGHT",
    "RankedCandidate",
    "ReferenceRenderCache",
    "compare_images",
    "is_comparison_available",
    "render_reference",
    "rerank",
]
