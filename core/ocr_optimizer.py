# core/ocr_optimizer.py
import os
import logging
import cv2
import numpy as np
from typing import List, Dict, Optional, Tuple, Any, Callable, Set
from skimage.metrics import structural_similarity as ssim
from collections import Counter, defaultdict, OrderedDict
from PySide6.QtCore import QCoreApplication

from core import ocr_engine_manager
from core import ocr_processor

logger = logging.getLogger(__name__)

# 位置对齐判定同一视觉行的最小 IoU（行框轴对齐包围盒）。相似帧间行位置
# 基本稳定，上下堆叠行的框垂直不重叠，该阈值只拒绝完全错位的框。
MIN_LINE_MATCH_IOU = 0.30

# 观测框（axis-aligned bounding box）：(x1, y1, x2, y2)。
_LineBox = Tuple[float, float, float, float]


def _poly_to_aabb(poly: Any) -> Optional[_LineBox]:
    """把 4 点多边形（嵌套列表或 numpy 数组）转成轴对齐包围盒。"""
    try:
        arr = np.asarray(poly, dtype=float)
    except (TypeError, ValueError):
        return None
    if arr.ndim != 2 or arr.shape[0] < 3 or arr.shape[1] != 2:
        return None
    return (
        float(arr[:, 0].min()), float(arr[:, 1].min()),
        float(arr[:, 0].max()), float(arr[:, 1].max()),
    )


def _line_aabbs(ocr_data: Dict[str, Any]) -> Optional[List[_LineBox]]:
    """逐行轴对齐框，用于跨采样帧的位置对齐。

    返回 None 表示该结果没有可用几何（调用方应回退到旧行索引对齐）；
    返回空列表表示结果为空但几何本身可用（0 行 → 0 框）。
    """
    texts = ocr_data.get('rec_texts') or []
    polys = ocr_data.get('dt_polys')
    if not isinstance(polys, (list, tuple)) or (texts and len(polys) != len(texts)):
        return None
    if not polys:
        return []
    boxes: List[_LineBox] = []
    for poly in polys:
        box = _poly_to_aabb(poly)
        if box is None:
            return None
        boxes.append(box)
    return boxes


def _box_iou(a: _LineBox, b: _LineBox) -> float:
    ix1, iy1 = max(a[0], b[0]), max(a[1], b[1])
    ix2, iy2 = min(a[2], b[2]), min(a[3], b[3])
    inter = max(0.0, ix2 - ix1) * max(0.0, iy2 - iy1)
    area_a = max(0.0, a[2] - a[0]) * max(0.0, a[3] - a[1])
    area_b = max(0.0, b[2] - b[0]) * max(0.0, b[3] - b[1])
    union = area_a + area_b - inter
    return inter / union if union > 0 else 0.0


def _align_lines_to_anchor(
    anchor_aabbs: List[_LineBox],
    sample_aabbs: List[_LineBox],
    scores: Optional[List] = None,
) -> Tuple[List[Optional[int]], List[int]]:
    """把一个采样帧的行贪心匹配到锚定帧的行槽上（按置信度降序、IoU 最大）。

    返回 (mapping, unmatched)：mapping[锚行槽] = 采样帧行号或 None；
    unmatched 是没匹配上任何锚行槽的采样帧行号（锚定帧漏读的行）。
    """
    order = list(range(len(sample_aabbs)))
    if scores is not None and len(scores) == len(sample_aabbs):
        order.sort(key=lambda i: -float(scores[i]))
    mapping: List[Optional[int]] = [None] * len(anchor_aabbs)
    used: Set[int] = set()
    for oi in order:
        best_ai: Optional[int] = None
        best_iou = MIN_LINE_MATCH_IOU
        for ai, box in enumerate(anchor_aabbs):
            if mapping[ai] is not None:
                continue
            iou = _box_iou(box, sample_aabbs[oi])
            if iou >= best_iou:
                best_ai, best_iou = ai, iou
        if best_ai is not None:
            mapping[best_ai] = oi
            used.add(oi)
    unmatched = [oi for oi in order if oi not in used]
    return mapping, unmatched


def _pick_list_item(ocr_data: Dict[str, Any], key: str, idx: int) -> Any:
    items = ocr_data.get(key)
    if isinstance(items, (list, tuple)) and idx < len(items):
        return items[idx]
    return None

# Optional dependency for fuzzy text matching; fall back to exact-match only.
try:
    import Levenshtein
except ImportError:
    Levenshtein = None
    logger.debug("python-Levenshtein not available; frame text similarity falls back to exact match only.")

class OcrOptimizer:

    def __init__(self,
                 work_dir: str,
                 visualize: bool,
                 in_memory_mode: bool,
                 save_ocr_json: bool = True,
                 image_similarity_threshold: float = 0.98,
                 search_step: int = 15,
                 motion_sentinel_enabled: bool = True,
                 motion_change_ratio_threshold: float = 0.01,
                 motion_pixel_delta: int = 10,
                 ocr_engine_id: str = "",
                 engine_options: Optional[Dict[str, Any]] = None,
                 similarity_target_width: int = 320,
                 text_similarity_threshold: float = 0.9,
                 feature_cache_max_entries: int = 2048,
                 image_cache_max_entries: int = 256,
                 vlm_refine_enabled: Optional[bool] = None,
                 vlm_refine_min_confidence: float = 0.85):

        self.work_dir = work_dir
        self.visualize = visualize
        self.in_memory_mode = in_memory_mode
        self.save_ocr_json = save_ocr_json
        self.image_similarity_threshold = image_similarity_threshold
        self.search_step = search_step
        self.ocr_engine_id = ocr_engine_id  # If set, switch engine before first OCR call
        self.engine_options = dict(engine_options or {})
        # Target width for grayscale images used by SSIM/motion checks; <= 0 disables downsampling.
        self.similarity_target_width = int(similarity_target_width)
        # Normalized Levenshtein similarity threshold for tolerating single-character OCR jitter.
        self.text_similarity_threshold = float(text_similarity_threshold)
        # LRU caps for in-memory caches; <= 0 means unbounded.
        self.feature_cache_max_entries = int(feature_cache_max_entries)
        self.image_cache_max_entries = int(image_cache_max_entries)

        # Motion sentinel: skip OCR when ROI hardly changes between adjacent frames.
        # This is most effective during static subtitle display (lasting seconds).
        # NOTE: the ratio is measured on the downscaled grayscale image. Short
        # subtitles cover only a few percent of a typical wide ROI band —
        # measured on the benchmark video: 7-char line ≈ 4.0%, 10-char ≈ 6.6%,
        # static background ≈ 0.0%. The default 0.01 keeps text appearances
        # well above the threshold while still skipping static frames.
        self.motion_sentinel_enabled = bool(motion_sentinel_enabled)
        self.motion_change_ratio_threshold = float(motion_change_ratio_threshold)
        self.motion_pixel_delta = int(motion_pixel_delta)

        # VLM 难帧兜底 (core/vlm_refine.py): when a voted sequence is "hard"
        # (a winning line has no majority or low average confidence), the
        # sampled frames + per-line candidate texts are sent to a vision LLM.
        # None = auto-enable only when vlm_refine.is_configured(); explicit
        # True/False force the behaviour regardless of configuration.
        self.vlm_refine_enabled = vlm_refine_enabled
        self.vlm_refine_min_confidence = float(vlm_refine_min_confidence)
        self._last_gray_by_roi: Dict[str, np.ndarray] = {}
        self._last_ocr_by_roi: Dict[str, Dict[str, Any]] = {}
        
        self._image_cache: "OrderedDict[Any, np.ndarray]" = OrderedDict()
        self._feature_cache: "OrderedDict[Any, np.ndarray]" = OrderedDict()
        # (roi_id, frame_num) -> OCR result tuple. Dedupes re-OCR of the same
        # frame between the binary search and sequence sampling. Boundary
        # refinement's upscaled probes bypass it (they are one-shot).
        self._ocr_result_cache: "OrderedDict[Any, Tuple]" = OrderedDict()

        # Lightweight stats for performance observability.
        self.ocr_calls: int = 0
        self.frames_filled: int = 0

    def _cache_put(self, cache: "OrderedDict", key: Any, value: Any, max_entries: int) -> None:
        """Insert into an OrderedDict-backed LRU cache, evicting the oldest entries beyond the cap."""
        cache[key] = value
        cache.move_to_end(key)
        limit = int(max_entries)
        if limit > 0:
            while len(cache) > limit:
                cache.popitem(last=False)

    def _get_image(self, frame_data: Tuple) -> Optional[np.ndarray]:
        img_input = frame_data[1]
        if isinstance(img_input, np.ndarray):
            return img_input
        
        if isinstance(img_input, str):
            if img_input in self._image_cache:
                self._image_cache.move_to_end(img_input)
                return self._image_cache[img_input]
            if os.path.exists(img_input):
                img = cv2.imread(img_input)
                if not self.in_memory_mode:
                    self._cache_put(self._image_cache, img_input, img, self.image_cache_max_entries)
                return img
        
        logger.warning(
            QCoreApplication.translate(
                "ocr_optimizer",
                "Could not get image data for frame {}. Input type: {}"
            ).format(frame_data[2], type(img_input))
        )
        return None

    def _get_grayscale_image(self, frame_data: Tuple) -> Optional[np.ndarray]:
        frame_id = (frame_data[3], frame_data[2]) 
        if frame_id in self._feature_cache:
            self._feature_cache.move_to_end(frame_id)
            return self._feature_cache[frame_id]

        img = self._get_image(frame_data)
        if img is None:
            return None

        gray_img = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        # Downsample for SSIM/motion checks to cut compute cost. Note: the motion
        # sentinel's change_ratio threshold now applies to the ratio measured on
        # this downsampled image, not on the full-resolution one.
        if 0 < self.similarity_target_width < gray_img.shape[1]:
            scale = float(self.similarity_target_width) / float(gray_img.shape[1])
            target_height = max(1, int(round(gray_img.shape[0] * scale)))
            gray_img = cv2.resize(
                gray_img,
                (self.similarity_target_width, target_height),
                interpolation=cv2.INTER_AREA
            )
        self._cache_put(self._feature_cache, frame_id, gray_img, self.feature_cache_max_entries)
        
        return gray_img

    def _empty_ocr_data(self) -> Dict[str, Any]:
        return {
            'dt_polys': [],
            'rec_polys': [],
            'rec_texts': [],
            'rec_scores': [],
            'rec_boxes': []
        }

    def _motion_sentinel_skip(self, frame_data: Tuple) -> Tuple[bool, Optional[np.ndarray]]:
        """
        Returns (should_skip_ocr, gray_img_for_state).
        If skipping, caller should reuse last OCR result (or empty) for this ROI.
        """
        if not self.motion_sentinel_enabled:
            return False, None

        roi_id = str(frame_data[3])
        prev_gray = self._last_gray_by_roi.get(roi_id)
        curr_gray = self._get_grayscale_image(frame_data)
        if curr_gray is None:
            return False, None
        if prev_gray is None:
            return False, curr_gray
        if prev_gray.shape != curr_gray.shape:
            # ROI size changed; treat as changed and reset background reference.
            return False, curr_gray

        try:
            diff = cv2.absdiff(prev_gray, curr_gray)
            delta = int(max(1, self.motion_pixel_delta))
            changed = int(np.count_nonzero(diff > delta))
            ratio = float(changed) / float(diff.size) if diff.size else 1.0
        except Exception:
            return False, curr_gray

        thr = float(max(0.0, self.motion_change_ratio_threshold))
        return (ratio < thr), curr_gray

    def _ensure_engine_selected(self) -> None:
        # Apply the engine selection/options on the first OCR call. For
        # ""/"auto" the already-selected (or registry-default) engine is
        # kept — but when no engine has been initialized yet, the options
        # (lang, model_tier, ...) must still reach initialize(): previously
        # the empty id skipped set_engine entirely, silently pinning auto
        # runs to the version-default (medium) models regardless of
        # --model-tier. An engine deliberately initialized elsewhere (e.g.
        # preload or a registered test double) always wins.
        if getattr(self, '_engine_switched', False):
            return
        if self.ocr_engine_id in ("", "auto"):
            from core import ocr_engine_manager as _mgr
            if not _mgr.is_engine_initialized():
                _mgr.set_engine(_mgr.get_current_engine_id() or "",
                                self.engine_options)
            self._engine_switched = True
            return
        from core.ocr_engine_manager import set_engine
        set_engine(self.ocr_engine_id, self.engine_options)
        self._engine_switched = True

    def _run_single_ocr(self, frame_data: Tuple, use_cache: bool = True) -> Tuple:
        self._ensure_engine_selected()

        cache_key = (frame_data[3], frame_data[2])
        if use_cache:
            cached = self._ocr_result_cache.get(cache_key)
            if cached is not None:
                self._ocr_result_cache.move_to_end(cache_key)
                return cached

        self.ocr_calls += 1
        ocr_generator = ocr_engine_manager.run_batch_ocr(
            iter([frame_data]),
            self.work_dir,
            visualize=self.visualize,
            save_json=self.save_ocr_json
        )
        try:
            result = next(ocr_generator)
        except StopIteration:
            # Keep extended tuple shape if present.
            frame_time_sec = float(frame_data[4]) if len(frame_data) >= 5 and frame_data[4] is not None else 0.0
            result = (frame_data[0], self._empty_ocr_data(), frame_data[2], frame_data[3], frame_time_sec)
        if use_cache:
            self._cache_put(self._ocr_result_cache, cache_key, result, self.image_cache_max_entries)
        return result

    def _are_images_visually_similar(self, frame_data1: Tuple, frame_data2: Tuple) -> bool:
        gray1 = self._get_grayscale_image(frame_data1)
        gray2 = self._get_grayscale_image(frame_data2)

        if gray1 is None or gray2 is None or gray1.shape != gray2.shape:
            return False
        
        similarity = ssim(gray1, gray2)
        return similarity >= self.image_similarity_threshold

    def _are_frames_similar(self, result1: Dict, result2: Dict) -> bool:
        text1 = "".join(result1.get('rec_texts', [])).strip()
        text2 = "".join(result2.get('rec_texts', [])).strip()

        # Exact-match fast path.
        if text1 and text1 == text2:
            return True

        # Keep original semantics: any empty text means "not similar".
        if not text1 or not text2:
            return False

        # Tolerate single-character OCR jitter via normalized Levenshtein similarity.
        if Levenshtein is None:
            return False

        max_len = max(len(text1), len(text2))
        similarity = 1.0 - Levenshtein.distance(text1, text2) / max_len
        return similarity >= self.text_similarity_threshold

    def _get_sample_results(self, sample_frames: List[Tuple]) -> List[Tuple]:
        """Run OCR on sampled frames, preferring one predict_batch call when possible.

        The batch path is only used when debug JSON dumping is disabled
        (save_ocr_json=False) and more than one frame needs OCR; any failure
        falls back to the per-frame path to stay robust.
        """
        if len(sample_frames) > 1 and not self.save_ocr_json:
            batch_results = self._run_batch_ocr_on_samples(sample_frames)
            if batch_results is not None:
                return batch_results
        return [self._run_single_ocr(frame_data) for frame_data in sample_frames]

    def _predict_batch_chunk_size(self) -> int:
        """Max frames per native predict_batch call, sized for the device.

        GPU batches are the throughput lever (design target 8–16); on CPU
        batching mostly saves per-call overhead, so a smaller chunk keeps
        peak memory low. Result is cached per optimizer instance — the
        device does not change mid-run.
        """
        if getattr(self, "_batch_chunk_size", None):
            return self._batch_chunk_size
        try:
            device = ocr_processor.get_device_mode()
        except Exception:
            device = "cpu"
        size = 12 if device == "gpu" else 6
        self._batch_chunk_size = size
        return size

    def _run_batch_ocr_on_samples(self, sample_frames: List[Tuple]) -> Optional[List[Tuple]]:
        """Best-effort batch OCR on sampled frames.

        Returns a list of result tuples (same shape as _run_single_ocr) aligned
        with the input order, or None so callers fall back to per-frame OCR.
        Frames whose result is already in the OCR cache are reused instead of
        being predicted again. Pending frames are predicted in device-sized
        chunks (``_predict_batch_chunk_size``) so GPU batches stay inside
        memory budgets.
        """
        try:
            self._ensure_engine_selected()
            engine = ocr_engine_manager.get_engine()

            results_by_pos: Dict[int, Tuple] = {}
            pending = []  # (position, frame_data, image) needing prediction
            for pos, frame_data in enumerate(sample_frames):
                cache_key = (frame_data[3], frame_data[2])
                cached = self._ocr_result_cache.get(cache_key)
                if cached is not None:
                    self._ocr_result_cache.move_to_end(cache_key)
                    results_by_pos[pos] = cached
                    continue
                img = self._get_image(frame_data)
                if img is not None:
                    pending.append((pos, frame_data, img))
            if not pending and not results_by_pos:
                return None

            batch_normalizer = getattr(engine, "normalize_batch_result", None)

            def predict_chunk(chunk: List[Tuple]) -> None:
                """One native predict_batch call for ``chunk`` of pending items."""
                raw_list = engine.predict_batch([img for _, _, img in chunk])
                if not isinstance(raw_list, list) or len(raw_list) != len(chunk):
                    raise ValueError(
                        "predict_batch returned {} results for {} images".format(
                            len(raw_list) if isinstance(raw_list, list) else type(raw_list).__name__,
                            len(chunk)
                        )
                    )
                # Prefer the engine's own batch normalizer (shape-specific, e.g.
                # RapidOCR's flat triples vs PaddleOCR's per-image dicts); fall
                # back to the PaddleOCR-style [item] wrap for minimal fake engines.
                normalized_items = (
                    list(batch_normalizer(raw_list))
                    if callable(batch_normalizer)
                    else [engine.normalize_result([raw_item]) for raw_item in raw_list]
                )
                for (pos, frame_data, _), ocr_data in zip(chunk, normalized_items):
                    # Same tuple shape and time handling as _run_single_ocr.
                    frame_time_sec = float(frame_data[4]) if len(frame_data) >= 5 and frame_data[4] is not None else 0.0
                    result = (
                        frame_data[0],
                        ocr_data,
                        frame_data[2],
                        frame_data[3],
                        frame_time_sec
                    )
                    results_by_pos[pos] = result
                    self._cache_put(
                        self._ocr_result_cache,
                        (frame_data[3], frame_data[2]),
                        result,
                        self.image_cache_max_entries,
                    )

            if pending:
                chunk_size = self._predict_batch_chunk_size()
                for start in range(0, len(pending), chunk_size):
                    predict_chunk(pending[start:start + chunk_size])
            if not results_by_pos:
                return None
            if pending:
                # One real batch invocation counts as a single OCR call
                # (cache-only returns don't touch the engine).
                self.ocr_calls += 1
            return [results_by_pos[p] for p in sorted(results_by_pos)]
        except Exception:
            logger.warning(
                QCoreApplication.translate(
                    "ocr_optimizer",
                    "Batch OCR prediction failed; falling back to per-frame OCR."
                ),
                exc_info=True
            )
            return None

    def _get_best_ocr_result_from_sequence(self, frame_sequence: List[Tuple]) -> Tuple:
        if not frame_sequence:
            return None

        sample_indices = {0}
        if len(frame_sequence) > 2:
            sample_indices.add(len(frame_sequence) // 2)
        if len(frame_sequence) > 1:
            sample_indices.add(len(frame_sequence) - 1)

        ordered_samples = [frame_sequence[i] for i in sorted(list(sample_indices))]
        sample_results = self._get_sample_results(ordered_samples)

        if len(sample_results) == 1:
            return sample_results[0]

        base_result_tuple = sample_results[0]
        anchor_aabbs = _line_aabbs(base_result_tuple[1])

        if anchor_aabbs is None:
            best_ocr_data, hard_lines, per_slot_candidates = (
                self._fuse_samples_strict_index(sample_results)
            )
        else:
            best_ocr_data, hard_lines, per_slot_candidates = (
                self._fuse_samples_by_position(sample_results, anchor_aabbs)
            )

        if best_ocr_data is None:
            return base_result_tuple

        # VLM 难帧兜底：仅替换 rec_texts，失败时保持投票结果不变。
        self._maybe_vlm_refine(
            best_ocr_data, ordered_samples, sample_results, hard_lines,
            per_slot_candidates=per_slot_candidates,
        )

        # Preserve time on the sampled frame (not used for fill; fill uses per-frame time).
        base_time_sec = float(base_result_tuple[4]) if len(base_result_tuple) >= 5 and base_result_tuple[4] is not None else 0.0
        return (base_result_tuple[0], best_ocr_data, base_result_tuple[2], base_result_tuple[3], base_time_sec)

    def _fuse_samples_strict_index(
        self, sample_results: List[Tuple]
    ) -> Tuple[Optional[Dict[str, Any]], List[int], Optional[List[List[str]]]]:
        """无行几何时的旧版按行索引投票。

        返回 (None, [], None) 表示采样帧无法安全投票，调用方直接使用锚定帧
        自身的结果（旧行为）。
        """
        base_result_tuple = sample_results[0]
        num_lines = len(base_result_tuple[1].get('rec_texts', []))

        if num_lines == 0:
            return None, [], None

        # 没有几何可对齐时，行数不一致意味着索引不再指向同一视觉行——
        # 回退到锚定帧自身结果，避免拼接出错行文本。
        if any(len(r[1].get('rec_texts', [])) != num_lines for r in sample_results):
            logger.debug(
                QCoreApplication.translate(
                    "ocr_optimizer",
                    "Sampled frames disagree on line count; keeping base frame OCR result."
                )
            )
            return None, [], None

        best_ocr_data = {
            'dt_polys': [None] * num_lines,
            'rec_polys': [None] * num_lines,
            'rec_texts': [''] * num_lines,
            'rec_scores': [0.0] * num_lines,
            'rec_boxes': [None] * num_lines
        }

        hard_lines: List[int] = []
        per_slot_candidates: List[List[str]] = []

        for line_idx in range(num_lines):
            text_votes = Counter()
            score_sum = defaultdict(float)
            result_map = defaultdict(list)

            for res_tuple in sample_results:
                ocr_data = res_tuple[1]
                if line_idx < len(ocr_data.get('rec_texts', [])):
                    text = ocr_data['rec_texts'][line_idx]
                    score = ocr_data['rec_scores'][line_idx]
                    text_votes[text] += 1
                    score_sum[text] += score
                    result_map[text].append(ocr_data)

            if not text_votes:
                continue

            best_text = max(text_votes, key=lambda t: (text_votes[t], score_sum[t] / text_votes[t]))

            best_result_source = result_map[best_text][0]

            best_ocr_data['rec_texts'][line_idx] = best_text
            best_ocr_data['rec_scores'][line_idx] = best_result_source['rec_scores'][line_idx]
            best_ocr_data['dt_polys'][line_idx] = best_result_source['dt_polys'][line_idx]
            best_ocr_data['rec_polys'][line_idx] = best_result_source['rec_polys'][line_idx]
            best_ocr_data['rec_boxes'][line_idx] = best_result_source['rec_boxes'][line_idx]
            per_slot_candidates.append(list(result_map.keys()))

            # 难帧判定：获胜文本票数未过半，或平均置信度低于阈值。
            votes = text_votes[best_text]
            avg_confidence = score_sum[best_text] / votes if votes else 0.0
            if votes < len(sample_results) / 2 or avg_confidence < self.vlm_refine_min_confidence:
                hard_lines.append(line_idx)

        return best_ocr_data, hard_lines, per_slot_candidates

    def _fuse_samples_by_position(
        self,
        sample_results: List[Tuple],
        anchor_aabbs: List[_LineBox],
    ) -> Tuple[Dict[str, Any], List[int], List[List[str]]]:
        """基于行框位置对齐的逐行融合。

        与旧行索引投票的区别（对应场景文字/遮挡/首帧漏读的根因）：
        - 某个采样帧漏读一行只让该行在该帧成为"缺失观测"，不再使整段
          放弃投票；缺失观测不投票、也不计入该行的分母（遮挡行不做负向
          投票）。
        - 锚定帧（首采样）漏读的行由其余采样帧中同位置的行补齐为额外
          行槽——首帧为空/漏字可以被后续清晰帧救回。
        - 行数不一致不再直接放弃，而是逐行对齐后继续投票，歧义行交给
          VLM 复核。
        """
        base_result_tuple = sample_results[0]
        anchor = base_result_tuple[1]
        anchor_texts = anchor.get('rec_texts', []) or []
        anchor_scores = anchor.get('rec_scores', []) or []
        num_anchor = len(anchor_texts)

        # 每个行槽的独立观测：(text, score, 来源 ocr_data, 行号)。
        slots: List[List[Tuple[str, float, Dict[str, Any], int]]] = [[] for _ in range(num_anchor)]
        # 锚定帧漏读、由其余帧补出的额外行槽。
        extra_slots: List[List[Tuple[str, float, Dict[str, Any], int]]] = []
        extra_aabbs: List[_LineBox] = []

        for pos, res_tuple in enumerate(sample_results):
            ocr_data = res_tuple[1]
            texts = ocr_data.get('rec_texts', []) or []
            scores = ocr_data.get('rec_scores', []) or []

            def _obs(li: int) -> Tuple[str, float, Dict[str, Any], int]:
                score = float(scores[li]) if li < len(scores) else 0.0
                return (texts[li], score, ocr_data, li)

            if pos == 0:
                # 锚定帧按行号自映射。
                for li in range(len(texts)):
                    slots[li].append(_obs(li))
                continue

            sample_aabbs = _line_aabbs(ocr_data)
            if sample_aabbs is None or len(sample_aabbs) != len(texts):
                # 该采样帧没有可用几何：行数与锚定帧一致时仍可按索引对齐，
                # 否则丢弃其观测（宁缺勿错接）。
                if len(texts) == num_anchor:
                    for li in range(len(texts)):
                        slots[li].append(_obs(li))
                continue

            mapping, unmatched = _align_lines_to_anchor(anchor_aabbs, sample_aabbs, scores)
            for ai, oi in enumerate(mapping):
                if oi is not None:
                    slots[ai].append(_obs(oi))
            for oi in unmatched:
                obs = _obs(oi)
                target: Optional[int] = None
                best_iou = MIN_LINE_MATCH_IOU
                for ei, box in enumerate(extra_aabbs):
                    iou = _box_iou(box, sample_aabbs[oi])
                    if iou >= best_iou:
                        target, best_iou = ei, iou
                if target is None:
                    extra_slots.append([obs])
                    extra_aabbs.append(sample_aabbs[oi])
                else:
                    extra_slots[target].append(obs)

        best_ocr_data = {
            'dt_polys': [],
            'rec_polys': [],
            'rec_texts': [],
            'rec_scores': [],
            'rec_boxes': []
        }
        hard_lines: List[int] = []
        per_slot_candidates: List[List[str]] = []

        # 行槽统一按行框 y 中心排序输出：被救回的额外行槽（锚定帧漏读的
        # 行）按其真实位置插回读取顺序，而不是追加在尾部。
        ordered_slots = (
            [(obs, box) for obs, box in zip(slots, anchor_aabbs)]
            + [(obs, box) for obs, box in zip(extra_slots, extra_aabbs)]
        )
        ordered_slots.sort(key=lambda pair: (pair[1][1] + pair[1][3]) / 2.0)

        for obs_list, _ in ordered_slots:
            if not obs_list:
                # 锚定帧行槽必然有自映射观测；防御空槽。
                continue
            text_votes = Counter(o[0] for o in obs_list)
            score_sum = defaultdict(float)
            for text, score, _, _ in obs_list:
                score_sum[text] += score
            best_text = max(text_votes, key=lambda t: (text_votes[t], score_sum[t] / text_votes[t]))

            win_score, win_data, win_idx = next(
                (score, data, li) for text, score, data, li in obs_list if text == best_text
            )
            best_ocr_data['rec_texts'].append(best_text)
            best_ocr_data['rec_scores'].append(win_score)
            best_ocr_data['dt_polys'].append(_pick_list_item(win_data, 'dt_polys', win_idx))
            best_ocr_data['rec_polys'].append(_pick_list_item(win_data, 'rec_polys', win_idx))
            best_ocr_data['rec_boxes'].append(_pick_list_item(win_data, 'rec_boxes', win_idx))

            seen = set()
            candidates: List[str] = []
            for text, _, _, _ in obs_list:
                if text and text not in seen:
                    seen.add(text)
                    candidates.append(text)
            per_slot_candidates.append(candidates)

            # 难帧判定（分母只数真实观测）：
            # - 观测内部无多数（票数不足观测数一半）；或
            # - 只有一个采样帧读到该行（缺第二证据交叉验证；旧行为下行数
            #   不一致时整段跳过 VLM，这里收敛到行级送审）；或
            # - 平均置信度低于阈值。
            votes = text_votes[best_text]
            observations = len(obs_list)
            avg_confidence = score_sum[best_text] / votes if votes else 0.0
            if (
                (observations == 1 and len(sample_results) > 1)
                or votes * 2 < observations
                or avg_confidence < self.vlm_refine_min_confidence
            ):
                hard_lines.append(len(best_ocr_data['rec_texts']) - 1)

        return best_ocr_data, hard_lines, per_slot_candidates

    def _maybe_vlm_refine(
        self,
        best_ocr_data: Dict[str, Any],
        sample_frames: List[Tuple],
        sample_results: List[Tuple],
        hard_lines: List[int],
        per_slot_candidates: Optional[List[List[str]]] = None,
    ) -> None:
        """难帧 VLM 兜底（core/vlm_refine.py）。

        只替换 ``rec_texts``；分数与框保持投票结果的值。整条路径绝不抛异常：
        任何失败只记一次 warning 并保持原结果。ocr_calls 统计不受影响。

        ``sample_frames`` 与 ``sample_results`` 按序一一对应：前者是原始帧
        元组（用于取图像），后者是 OCR 结果元组（用于取各行候选文本）。
        ``per_slot_candidates`` 是融合阶段按位置对齐好的逐行候选；为 None 时
        按行索引从各采样帧提取（旧行为，行数不一致时会错位）。
        """
        if not hard_lines:
            return
        try:
            from core import vlm_refine as vlm_refine_module

            enabled = self.vlm_refine_enabled
            if enabled is None:
                enabled = vlm_refine_module.is_configured()
            if not enabled:
                return

            num_lines = len(best_ocr_data.get('rec_texts', []))
            if num_lines == 0:
                return

            images = []
            for frame_data in sample_frames:
                img = self._get_image(frame_data)
                if img is not None:
                    images.append(img)
            if not images:
                return

            if per_slot_candidates is not None and len(per_slot_candidates) == num_lines:
                candidates = [list(c) for c in per_slot_candidates]
            else:
                # 每行的候选文本 = 各采样帧中该行的文本（去重保序）。
                candidates = []
                for line_idx in range(num_lines):
                    seen = set()
                    line_candidates: List[str] = []
                    for res_tuple in sample_results:
                        ocr_data = res_tuple[1]
                        if not isinstance(ocr_data, dict) or line_idx >= len(ocr_data.get('rec_texts', [])):
                            continue
                        text = str(ocr_data['rec_texts'][line_idx])
                        if text and text not in seen:
                            seen.add(text)
                            line_candidates.append(text)
                    candidates.append(line_candidates)

            refined = vlm_refine_module.refine_subtitle_text(images, candidates)
            if refined is None:
                return
            if len(refined) != num_lines:
                logger.warning(
                    QCoreApplication.translate(
                        "ocr_optimizer",
                        "VLM refine returned {0} lines for {1} expected; keeping original result."
                    ).format(len(refined), num_lines)
                )
                return
            best_ocr_data['rec_texts'] = [str(text) for text in refined]
        except Exception:
            logger.warning(
                QCoreApplication.translate(
                    "ocr_optimizer",
                    "VLM refine failed; keeping original voting result."
                ),
                exc_info=True
            )

    def process_roi_group(self, 
                          roi_frames: List[Tuple], 
                          is_cancelled_func: Callable[[], bool],
                          progress_callback: Optional[Callable[[int], None]] = None
                         ) -> List[Tuple]:
        if not roi_frames:
            return []

        processed_results = []
        i = 0
        group_processed_count = 0

        while i < len(roi_frames):
            if is_cancelled_func():
                logger.info(QCoreApplication.translate("ocr_optimizer", "OCR optimizer detected cancellation signal, terminating early."))
                return []
            current_frame_data = roi_frames[i]

            # Motion sentinel: if ROI hardly changes, skip OCR and reuse last result.
            roi_id = str(current_frame_data[3])
            should_skip, curr_gray = self._motion_sentinel_skip(current_frame_data)
            if curr_gray is not None:
                self._last_gray_by_roi[roi_id] = curr_gray
            if should_skip:
                frame_time_sec = float(current_frame_data[4]) if len(current_frame_data) >= 5 and current_frame_data[4] is not None else 0.0
                # Shallow copy: downstream per-frame annotations must not leak
                # into the shared reuse state.
                reused = dict(self._last_ocr_by_roi.get(roi_id) or self._empty_ocr_data())
                filled_result = (
                    current_frame_data[0],
                    reused,
                    current_frame_data[2],
                    current_frame_data[3],
                    frame_time_sec
                )
                processed_results.append(filled_result)
                i += 1
                group_processed_count += 1
                self.frames_filled += 1
                if progress_callback:
                    progress_callback(group_processed_count)
                continue

            initial_result_tuple = self._run_single_ocr(current_frame_data)
            initial_ocr_result = initial_result_tuple[1]
            current_text = "".join(initial_ocr_result.get('rec_texts', [])).strip()
            # Update reuse state after we actually ran OCR.
            self._last_ocr_by_roi[roi_id] = initial_ocr_result if isinstance(initial_ocr_result, dict) else self._empty_ocr_data()

            if not current_text:
                # A real OCR call that happened to recognize nothing — not a
                # filled frame; don't count it in frames_filled.
                processed_results.append(initial_result_tuple)
                i += 1
                group_processed_count += 1
                if progress_callback:
                    progress_callback(group_processed_count)
                continue

            start_index = i
            last_similar_index = start_index

            probe_index = start_index + 1
            while probe_index < len(roi_frames):
                if not self._are_images_visually_similar(current_frame_data, roi_frames[probe_index]):
                    break
                probe_index += self.search_step
            
            low = start_index + 1
            high = min(probe_index, len(roi_frames) - 1)
            
            while low <= high:
                mid = (low + high) // 2
                mid_frame_data = roi_frames[mid]
                
                if not self._are_images_visually_similar(current_frame_data, mid_frame_data):
                    high = mid - 1
                    continue

                mid_result_tuple = self._run_single_ocr(mid_frame_data)
                if self._are_frames_similar(initial_ocr_result, mid_result_tuple[1]):
                    last_similar_index = mid
                    low = mid + 1
                else:
                    high = mid - 1

            similar_sequence = roi_frames[start_index : last_similar_index + 1]

            if last_similar_index == start_index:
                # Single-frame sequence: the anchor was already OCR'd above;
                # reuse that result instead of sampling it a second time.
                best_result_tuple = initial_result_tuple
            else:
                best_result_tuple = self._get_best_ocr_result_from_sequence(similar_sequence)
            best_ocr_result = best_result_tuple[1]
            self._last_ocr_by_roi[roi_id] = best_ocr_result if isinstance(best_ocr_result, dict) else self._empty_ocr_data()

            for j in range(start_index, last_similar_index + 1):
                frame_data_to_fill = roi_frames[j]
                frame_time_sec = float(frame_data_to_fill[4]) if len(frame_data_to_fill) >= 5 and frame_data_to_fill[4] is not None else 0.0
                # Each filled frame gets its own (shallow) copy so downstream
                # per-frame mutations can't pollute the whole batch.
                ocr_data_for_frame = dict(best_ocr_result) if isinstance(best_ocr_result, dict) else best_ocr_result
                filled_result = (
                    frame_data_to_fill[0],
                    ocr_data_for_frame,
                    frame_data_to_fill[2],
                    frame_data_to_fill[3],
                    frame_time_sec
                )
                processed_results.append(filled_result)
                self.frames_filled += 1
            
            if last_similar_index > start_index:
                logger.info(
                    QCoreApplication.translate(
                        "ocr_optimizer",
                        "Smart frame skipping: ROI '{}' from frame {} to {} has similar content, skipping {} OCR operations."
                    ).format(current_frame_data[3], current_frame_data[2], roi_frames[last_similar_index][2], last_similar_index - start_index)
                )
            
            num_processed_in_batch = last_similar_index - start_index + 1
            i = last_similar_index + 1
            group_processed_count += num_processed_in_batch
            if progress_callback:
                progress_callback(group_processed_count)
            
        return processed_results

    def cleanup(self):
        self._image_cache.clear()
        self._feature_cache.clear()
        self._ocr_result_cache.clear()
        self._last_gray_by_roi.clear()
        self._last_ocr_by_roi.clear()
        logger.debug(QCoreApplication.translate("ocr_optimizer", "Cleaning up cache."))

