# font_intel/recognizer/glyph_rerank.py
"""字形重排裁决器（T3.2）：按已知文本渲染候选字体参考字形，与视频字块
归一化比对（SSIM / 感知哈希 / HoG+余弦三路融合），对上游字体候选重排。

诚实定位（方案 §3.1/§3.2/§9 风险条目）
--------------------------------------
本模块**不是字体识别器**，而是"候选重排裁决器"。OCR 已给出"是什么字"
与文本框，因此无需字符分割——直接按已知文本渲染各候选字体的整串参考
字形，与视频字块归一化后比对，回答"这些候选里哪一个与视频字块最像"。
分数（融合分与各路分）仅供**排序与置信度参考**，不承诺全自动准确：
艺术字、特效字、描边、低分辨率压缩等干扰下精度会下降。运行时语义是
Top-N 候选 + 人工确认（UI 候选列表与并排样张预览）；若标定精度不达标，
整体降级为"仅展示候选 + 人工确认"，不自动落名。

对外 API
--------
- ``render_reference(text, font_path, canvas=None)``：按已知文本整串渲染
  参考字形（灰度、白字黑底）。缺字形 / 字体损坏 / 空文本等一律返回
  ``None``（warning 降级），绝不抛异常；结果按 (font_path, text, canvas)
  经 ``ReferenceRenderCache`` LRU 缓存（默认共享模块级实例，
  ``cache=None`` 可旁路）。
- ``compare_images(ref, crop)``：归一化（等比缩放到统一尺寸、Otsu 二值
  定位墨迹、极性对齐、可选质心对齐）后计算三路相似度并加权融合：
  SSIM（scikit-image）、pHash（cv2.dct 自实现，不引新库）、HoG+余弦
  （scikit-image hog）。输入结构非法（None/空数组/非图像）抛
  ``ValueError``；内容级问题（空白图）返回全零分 + warning。
- ``rerank(text, crop_image, candidates, font_index=None)``：对候选字体
  列表逐一渲染参考字形并比对，按融合分降序返回 ``RankedCandidate``
  （name/score/per_metric/font_path）。字块归一化结果在同一批候选间
  共享（只做一次）；输入非法（空图/空文本/空候选/依赖缺失）安全降级：
  保持原序、分数 0.0，绝不抛异常。

依赖
----
numpy 必需；cv2 与 scikit-image 缺失时比对/重排优雅降级（全零分 +
warning），渲染（Pillow，经 ``fontlib_index.is_available()`` 判定）不受
影响。不引入任何新依赖；torch 不在本模块范围（候选生成属 T3.1）。

候选格式
--------
``candidates`` 元素支持三种写法：``{"name": str, "font_path": str}``（name
可省略，回退文件名主干）、``(name, font_path)`` 二元组、纯路径 ``str``。
无法识别的元素按渲染失败处理（分数 0.0 沉底），不缩减返回列表长度。
"""

from __future__ import annotations

import logging
import os
from collections import OrderedDict
from dataclasses import asdict, dataclass, field
from typing import Any, Optional

import numpy as np

from font_intel import fontlib_index

logger = logging.getLogger(__name__)

# ── 可选依赖（cv2 / scikit-image；缺失时比对优雅降级） ────────────

try:
    import cv2 as _cv2

    _CV2_AVAILABLE = True
except ImportError:  # pragma: no cover - 取决于环境
    _cv2 = None
    _CV2_AVAILABLE = False

try:
    from skimage.feature import hog as _hog
    from skimage.filters import threshold_otsu as _threshold_otsu
    from skimage.metrics import structural_similarity as _structural_similarity

    _SKIMAGE_AVAILABLE = True
except ImportError:  # pragma: no cover - 取决于环境
    _hog = None
    _threshold_otsu = None
    _structural_similarity = None
    _SKIMAGE_AVAILABLE = False


def is_comparison_available() -> bool:
    """cv2 与 scikit-image 均可用时返回 True（比对/重排功能的启用开关）。"""
    return _CV2_AVAILABLE and _SKIMAGE_AVAILABLE


# ── 常量 ─────────────────────────────────────────────────────────

# 参考字形渲染默认画布边长（正方形；显式传 canvas 时覆盖）。
CANVAS_DEFAULT = 160

# 归一化比对尺寸：所有图等比缩放到此正方形后计算三路相似度。
NORM_SIZE = 64

# pHash 参数：32×32 灰度做 DCT，取左上 8×8 低频块做中位数二值化（64 bit）。
_PHASH_DCT_SIZE = 32
_PHASH_GRID = 8
_PHASH_BITS = _PHASH_GRID * _PHASH_GRID

# 三路融合权重（模块级常量，可被 compare_images/rerank 的 weights 参数覆盖）。
SSIM_WEIGHT = 0.5
PHASH_WEIGHT = 0.3
HOG_WEIGHT = 0.2
FUSION_WEIGHTS: dict[str, float] = {
    "ssim": SSIM_WEIGHT,
    "phash": PHASH_WEIGHT,
    "hog": HOG_WEIGHT,
}
_METRIC_KEYS = ("ssim", "phash", "hog")


# ── 数据结构 ─────────────────────────────────────────────────────


@dataclass
class RankedCandidate:
    """重排后的候选字体条目。

    score 是三路融合分（约 [-1, 1]，实际有效区间约 [0, 1]）；
    per_metric 为 {"ssim", "phash", "hog"} 各路分数；参考字形渲染失败
    的候选 score=0.0 且 per_metric 为空 dict（区别于"计算过得 0 分"）。
    """

    name: str
    score: float
    per_metric: dict[str, float] = field(default_factory=dict)
    font_path: Optional[str] = None

    def as_dict(self) -> dict:
        return asdict(self)


def _zero_scores() -> dict[str, float]:
    return {"ssim": 0.0, "phash": 0.0, "hog": 0.0, "score": 0.0}


# ── 参考字形渲染与缓存 ───────────────────────────────────────────


def _parse_canvas(canvas: Any) -> Optional[tuple[int, int]]:
    """canvas 参数归一化为 (w, h) 正整数元组；非法返回 None。"""
    if canvas is None:
        return (CANVAS_DEFAULT, CANVAS_DEFAULT)
    try:
        if isinstance(canvas, (int, np.integer)):
            side = int(canvas)
            return (side, side) if side > 0 else None
        seq = tuple(int(v) for v in canvas)  # tuple/list 均可
    except (TypeError, ValueError):
        return None
    if len(seq) != 2 or seq[0] <= 0 or seq[1] <= 0:
        return None
    return (seq[0], seq[1])


class ReferenceRenderCache:
    """整串参考字形内存 LRU 缓存，键为 (font_path, text, (w, h))。

    命中返回 ``(True, value)``，未命中 ``(False, None)``；渲染失败
    （value=None）同样入缓存——同输入不会反复触底渲染。仅内存，不落盘。
    """

    def __init__(self, capacity: int = 256) -> None:
        if int(capacity) <= 0:
            raise ValueError("capacity must be a positive integer")
        self._capacity = int(capacity)
        self._entries: "OrderedDict[tuple, Optional[np.ndarray]]" = OrderedDict()

    @staticmethod
    def _key(font_path: Any, text: str, canvas: Any) -> tuple:
        return (str(font_path), str(text), _parse_canvas(canvas))

    def get(self, font_path: Any, text: str, canvas: Any) -> tuple[bool, Optional[np.ndarray]]:
        key = self._key(font_path, text, canvas)
        if key not in self._entries:
            return (False, None)
        self._entries.move_to_end(key)
        return (True, self._entries[key])

    def put(self, font_path: Any, text: str, canvas: Any, value: Optional[np.ndarray]) -> None:
        key = self._key(font_path, text, canvas)
        if key[2] is None:
            return
        self._entries[key] = value
        self._entries.move_to_end(key)
        while len(self._entries) > self._capacity:
            self._entries.popitem(last=False)


# 模块级共享缓存（rerank 默认使用；测试可注入独立实例隔离）。
DEFAULT_RENDER_CACHE = ReferenceRenderCache()


def _render_reference_uncached(
    text: str, font_path: Any, canvas: Any
) -> Optional[np.ndarray]:
    """整串渲染已知文本（不做字符分割）；任何失败路径返回 None + warning。

    canvas 为 _parse_canvas 产出的 (w, h)。缺字形判定经
    ``fontlib_index.cmap_codepoints``：任一字符无 cmap 覆盖即放弃
    （绝不渲染 .notdef 豆腐块污染比对分数）。
    """
    if not fontlib_index.is_available():
        logger.warning(
            "fontTools/Pillow 未安装，参考字形渲染不可用（优雅降级返回 None）"
        )
        return None
    if not isinstance(text, str) or not text.strip():
        logger.warning("参考字形渲染：文本为空或非字符串，返回 None")
        return None
    width, height = int(canvas[0]), int(canvas[1])
    if width <= 0 or height <= 0:
        logger.warning("参考字形渲染：canvas 尺寸非法: %r", (width, height))
        return None
    path = str(font_path)
    if not path.strip() or not os.path.isfile(path):
        logger.warning("参考字形渲染：字体文件不存在: %s", path)
        return None

    codes = fontlib_index.cmap_codepoints(path)
    if codes is not None:
        missing = [ch for ch in text if ord(ch) not in codes]
        if missing:
            logger.warning(
                "参考字形渲染：字体缺字形，放弃整串渲染: path=%s, missing=%r",
                path, missing[:8],
            )
            return None

    try:
        from PIL import Image, ImageDraw, ImageFont

        base_size = max(8, int(min(width, height) * 0.6))
        face = ImageFont.truetype(path, size=base_size)
        target = Image.new("L", (width, height), 0)
        drawer = ImageDraw.Draw(target)
        left, top, right, bottom = drawer.textbbox((0, 0), text, font=face)
        box_w, box_h = right - left, bottom - top
        # 超出画布 90% 时按比例缩一字号重排（单次重试，保证整串完整入画）。
        if box_w > 0 and box_h > 0 and (box_w > 0.9 * width or box_h > 0.9 * height):
            scale = min(0.9 * width / box_w, 0.9 * height / box_h)
            face = ImageFont.truetype(path, size=max(8, int(base_size * scale)))
            left, top, right, bottom = drawer.textbbox((0, 0), text, font=face)
            box_w, box_h = right - left, bottom - top
        if box_w <= 0 or box_h <= 0:
            logger.warning(
                "参考字形渲染：整串无可渲染墨迹: path=%s, text=%r", path, text
            )
            return None
        origin = (width // 2 - box_w // 2 - left, height // 2 - box_h // 2 - top)
        drawer.text(origin, text, font=face, fill=255)
        if target.getbbox() is None:
            logger.warning(
                "参考字形渲染：整串无可渲染墨迹: path=%s, text=%r", path, text
            )
            return None
        return np.asarray(target, dtype=np.uint8)
    except Exception as exc:
        logger.warning(
            "参考字形渲染失败（字体损坏或 Pillow 异常）: path=%s, error=%s", path, exc
        )
        return None


def render_reference(
    text: str,
    font_path: Any,
    canvas: Any = None,
    *,
    cache: Optional[ReferenceRenderCache] = DEFAULT_RENDER_CACHE,
) -> Optional[np.ndarray]:
    """按已知文本用指定字体整串渲染参考字形（灰度 uint8，白字黑底）。

    字符间不做分割；返回 H×W ndarray，失败（缺字形/字体损坏/字体文件
    不存在/空文本/非法 canvas）返回 None，绝不抛异常。结果按
    (font_path, text, canvas) 缓存；``cache=None`` 旁路缓存直连渲染。
    """
    if not isinstance(text, str):
        logger.warning("参考字形渲染：text 非字符串（%r），返回 None", type(text))
        return None
    if not isinstance(font_path, (str, os.PathLike)) or not str(font_path).strip():
        logger.warning("参考字形渲染：font_path 非法（%r），返回 None", font_path)
        return None
    size = _parse_canvas(canvas)
    if size is None:
        logger.warning("参考字形渲染：canvas 非法（%r），返回 None", canvas)
        return None
    if cache is None:
        return _render_reference_uncached(text, font_path, size)
    hit, value = cache.get(font_path, text, size)
    if hit:
        return value
    value = _render_reference_uncached(text, font_path, size)
    cache.put(font_path, text, size, value)
    return value


# ── 归一化 ───────────────────────────────────────────────────────


def _image_is_valid(image: Any) -> bool:
    if image is None:
        return False
    try:
        arr = np.asarray(image)
    except Exception:
        return False
    return arr.ndim in (2, 3) and min(arr.shape[:2]) >= 1


def _validate_image(image: Any, role: str) -> np.ndarray:
    if not _image_is_valid(image):
        raise ValueError(
            f"compare_images: {role} 必须是 2/3 维非空图像数组，得到 {type(image)}"
        )
    return np.asarray(image)


def _resize(img: np.ndarray, width: int, height: int) -> np.ndarray:
    if _CV2_AVAILABLE:
        return _cv2.resize(img, (width, height), interpolation=_cv2.INTER_AREA)
    # 无 cv2 时的最近邻降采样兜底（仅在依赖缺失降级路径外不应触达）。
    ys = np.clip(
        (np.arange(height) * img.shape[0] / max(height, 1)).astype(int),
        0, img.shape[0] - 1,
    )
    xs = np.clip(
        (np.arange(width) * img.shape[1] / max(width, 1)).astype(int),
        0, img.shape[1] - 1,
    )
    return img[np.ix_(ys, xs)]


def _shift_to_center(img: np.ndarray) -> np.ndarray:
    """整数平移使墨迹质心落在画布中心（可选的质心对齐步骤）。"""
    total = float(img.sum())
    if total <= 0:
        return img
    h, w = img.shape
    ys, xs = np.mgrid[0:h, 0:w]
    cy = float((ys * img).sum()) / total
    cx = float((xs * img).sum()) / total
    dy = int(round(cy - h / 2.0))
    dx = int(round(cx - w / 2.0))
    if dy == 0 and dx == 0:
        return img
    out = np.zeros_like(img)
    src_y, dst_y = max(dy, 0), max(-dy, 0)
    src_x, dst_x = max(dx, 0), max(-dx, 0)
    copy_h = min(h - src_y, h - dst_y)
    copy_w = min(w - src_x, w - dst_x)
    if copy_h > 0 and copy_w > 0:
        out[dst_y:dst_y + copy_h, dst_x:dst_x + copy_w] = img[
            src_y:src_y + copy_h, src_x:src_x + copy_w
        ]
    return out


def _normalize(
    image: Any, align_centroid: bool = False
) -> Optional[np.ndarray]:
    """归一化为 NORM_SIZE×NORM_SIZE 的 float32 [0,1] 墨迹图；失败返回 None。

    流程：灰度（RGB 无序安全取通道均值）→ 极性对齐（白底黑字的视频
    字块反转为白字黑底）→ Otsu 二值定位墨迹紧致框 → 等比补边成正方
    形 → 强度线性拉伸到 [0,1] → 面积插值缩放到统一尺寸 →（可选）质心
    对齐。空白图 / 常量图 / 阈值失效返回 None（调用方按全零分降级）。
    """
    try:
        arr = np.asarray(image)
        if arr.ndim not in (2, 3) or min(arr.shape[:2]) < 2:
            return None
        gray = arr.astype(np.float32)
        if gray.ndim == 3:
            gray = gray.mean(axis=2)
        if float(gray.mean()) > 127.5:
            gray = 255.0 - gray  # 极性对齐：一律白字黑底
        if _SKIMAGE_AVAILABLE:
            threshold = float(_threshold_otsu(gray))
        else:  # pragma: no cover - 依赖缺失降级路径
            threshold = float(gray.mean())
        mask = gray > threshold
        if not mask.any() or mask.all():
            return None
        rows = np.where(mask.any(axis=1))[0]
        cols = np.where(mask.any(axis=0))[0]
        gray = gray[rows[0]:rows[-1] + 1, cols[0]:cols[-1] + 1]
        h, w = gray.shape
        side = max(h, w)
        square = np.zeros((side, side), dtype=np.float32)
        y0, x0 = (side - h) // 2, (side - w) // 2
        square[y0:y0 + h, x0:x0 + w] = gray
        lo, hi = float(square.min()), float(square.max())
        if hi - lo < 1e-6:
            return None
        square = (square - lo) / (hi - lo)
        out = _resize(square, NORM_SIZE, NORM_SIZE).astype(np.float32)
        if align_centroid:
            out = _shift_to_center(out)
        return out
    except Exception as exc:  # 归一化永不向上抛
        logger.warning("字块归一化失败，按空白图处理: error=%s", exc)
        return None


# ── 三路相似度 ───────────────────────────────────────────────────


def _ssim_score(a: np.ndarray, b: np.ndarray) -> float:
    return float(_structural_similarity(a.astype(np.float64), b.astype(np.float64), data_range=1.0))


def _phash_bits(norm_img: np.ndarray) -> np.ndarray:
    """pHash 位图：32×32 DCT 左上 8×8 低频块对中位数二值化（自实现）。"""
    small = _resize(norm_img, _PHASH_DCT_SIZE, _PHASH_DCT_SIZE)
    if _CV2_AVAILABLE:
        dct = _cv2.dct(np.ascontiguousarray(small, dtype=np.float32))
    else:  # pragma: no cover - 依赖缺失降级路径
        dct = small.astype(np.float32)
    low = dct[:_PHASH_GRID, :_PHASH_GRID]
    return low > np.median(low)


def _phash_similarity(bits_a: np.ndarray, bits_b: np.ndarray) -> float:
    return float(1.0 - np.count_nonzero(bits_a != bits_b) / _PHASH_BITS)


def _hog_vector(norm_img: np.ndarray) -> np.ndarray:
    return np.asarray(
        _hog(
            norm_img,
            orientations=9,
            pixels_per_cell=(8, 8),
            cells_per_block=(2, 2),
            feature_vector=True,
        ),
        dtype=np.float64,
    )


def _cosine(a: np.ndarray, b: np.ndarray) -> float:
    norm_a = float(np.linalg.norm(a))
    norm_b = float(np.linalg.norm(b))
    if norm_a <= 0.0 or norm_b <= 0.0:
        return 0.0
    return float(np.dot(a, b) / (norm_a * norm_b))


def _resolve_weights(weights: Optional[dict]) -> dict[str, float]:
    resolved = dict(FUSION_WEIGHTS) if not weights else {
        key: float(weights.get(key, 0.0)) for key in _METRIC_KEYS
    }
    cleaned = {}
    for key, value in resolved.items():
        if value < 0.0:
            logger.warning("融合权重为负，按 0 处理: %s=%s", key, value)
            value = 0.0
        cleaned[key] = value
    return cleaned


def _fuse(per_metric: dict[str, float], weights: dict[str, float]) -> float:
    total = sum(weights.values())
    if total <= 0.0:
        return 0.0
    return float(sum(weights[key] * per_metric[key] for key in weights) / total)


def compare_images(
    ref: Any,
    crop: Any,
    *,
    weights: Optional[dict[str, float]] = None,
    align_centroid: bool = False,
) -> dict[str, float]:
    """归一化后计算参考字形与视频字块的三路相似度与融合分。

    返回 ``{"ssim", "phash", "hog", "score"}``。输入结构非法（None/空
    数组/非图像）抛 ``ValueError``；内容级失败（空白图/依赖缺失）返回
    全零分 + warning。融合权重默认 ``FUSION_WEIGHTS``，可被 ``weights``
    覆盖（按权和归一）。
    """
    ref_arr = _validate_image(ref, "ref")
    crop_arr = _validate_image(crop, "crop")
    if not is_comparison_available():
        logger.warning(
            "cv2/scikit-image 未安装，字形比对不可用（优雅降级返回全零分）"
        )
        return _zero_scores()
    resolved = _resolve_weights(weights)
    norm_ref = _normalize(ref_arr, align_centroid)
    norm_crop = _normalize(crop_arr, align_centroid)
    if norm_ref is None or norm_crop is None:
        logger.warning("字形比对：参考图或字块归一化失败（空白图），返回全零分")
        return _zero_scores()
    per_metric = {
        "ssim": _ssim_score(norm_ref, norm_crop),
        "phash": _phash_similarity(_phash_bits(norm_ref), _phash_bits(norm_crop)),
        "hog": _cosine(_hog_vector(norm_ref), _hog_vector(norm_crop)),
    }
    return {**per_metric, "score": _fuse(per_metric, resolved)}


# ── 重排裁决 ─────────────────────────────────────────────────────


def _name_from_path(path: str) -> str:
    return os.path.splitext(os.path.basename(path))[0] or path


def _coerce_candidate(item: Any, index: int) -> tuple[str, str]:
    """候选元素 → (name, font_path)；无法识别返回 ("candidate_i", "")，
    由后续渲染失败路径兜底（保持返回列表长度与输入一致）。"""
    if isinstance(item, str) and item.strip():
        return (_name_from_path(item.strip()), item.strip())
    if isinstance(item, dict):
        path = item.get("font_path")
        if isinstance(path, (str, os.PathLike)) and str(path).strip():
            name = item.get("name") or _name_from_path(str(path))
            return (str(name), str(path))
    if isinstance(item, (tuple, list)) and len(item) == 2:
        name, path = item
        if isinstance(path, (str, os.PathLike)) and str(path).strip():
            return (
                str(name) if name else _name_from_path(str(path)),
                str(path),
            )
    logger.warning("rerank：候选格式无法识别，按渲染失败处理: index=%s", index)
    return (f"candidate_{index}", "")


def rerank(
    text: str,
    crop_image: Any,
    candidates,
    font_index: Optional[ReferenceRenderCache] = None,
    *,
    weights: Optional[dict[str, float]] = None,
    canvas: Any = None,
    align_centroid: bool = False,
) -> list[RankedCandidate]:
    """对候选字体列表按"参考字形 vs 视频字块"融合分降序重排。

    同一批候选共享一次字块归一化；参考字形渲染经 ``font_index`` 缓存
    （None 时用模块级共享缓存）。安全降级：空候选返回 []；空文本/空图/
    非法输入/比对依赖缺失时保持原序返回、score=0.0，绝不抛异常。
    """
    items = [_coerce_candidate(c, i) for i, c in enumerate(candidates or [])]
    if not items:
        return []
    text_ok = isinstance(text, str) and bool(text.strip())
    if (
        not text_ok
        or not _image_is_valid(crop_image)
        or not is_comparison_available()
    ):
        logger.warning(
            "rerank：输入非法（空文本/空图/依赖缺失），安全降级保持原序: "
            "candidates=%d", len(items),
        )
        return [RankedCandidate(name, 0.0, {}, path or None) for name, path in items]

    cache = font_index if font_index is not None else DEFAULT_RENDER_CACHE
    norm_crop = _normalize(crop_image, align_centroid)
    if norm_crop is None:
        logger.warning("rerank：字块归一化失败（空白图），安全降级保持原序")
        return [RankedCandidate(name, 0.0, {}, path or None) for name, path in items]

    # 字块侧度量产物只算一次，整批候选共享。
    crop_phash = _phash_bits(norm_crop)
    crop_hog = _hog_vector(norm_crop)
    resolved = _resolve_weights(weights)

    ranked: list[RankedCandidate] = []
    for name, path in items:
        ref = render_reference(text, path, canvas=canvas, cache=cache)
        per_metric: dict[str, float] = {}
        if ref is not None:
            norm_ref = _normalize(ref, align_centroid)
            if norm_ref is not None:
                per_metric = {
                    "ssim": _ssim_score(norm_ref, norm_crop),
                    "phash": _phash_similarity(_phash_bits(norm_ref), crop_phash),
                    "hog": _cosine(_hog_vector(norm_ref), crop_hog),
                }
        score = _fuse(per_metric, resolved) if per_metric else 0.0
        ranked.append(RankedCandidate(name, float(score), per_metric, path or None))

    # 稳定排序：同分保持输入相对次序（sort 稳定 + 仅按 -score 作 key）。
    ranked.sort(key=lambda c: -c.score)
    return ranked
