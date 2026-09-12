# core/ocr_optimizer.py
import os
import logging
import cv2
import numpy as np
from typing import List, Dict, Optional, Tuple, Any, Callable
from skimage.metrics import structural_similarity as ssim
from collections import Counter, defaultdict, OrderedDict
from PySide6.QtCore import QCoreApplication 

from core import ocr_engine_manager
from core import ocr_processor

logger = logging.getLogger(__name__)

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
        num_lines = len(base_result_tuple[1].get('rec_texts', []))

        if num_lines == 0:
            return base_result_tuple

        # Voting aligns samples strictly by line index. If any sample detected
        # a different number of lines (detection jitter / missed line), index
        # alignment no longer refers to the same visual lines — fall back to
        # the base frame's own result instead of producing spliced text.
        if any(len(r[1].get('rec_texts', [])) != num_lines for r in sample_results):
            logger.debug(
                QCoreApplication.translate(
                    "ocr_optimizer",
                    "Sampled frames disagree on line count; keeping base frame OCR result."
                )
            )
            return base_result_tuple

        best_ocr_data = {
            'dt_polys': [None] * num_lines,
            'rec_polys': [None] * num_lines,
            'rec_texts': [''] * num_lines,
            'rec_scores': [0.0] * num_lines,
            'rec_boxes': [None] * num_lines
        }

        hard_lines: List[int] = []

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

            # 难帧判定：获胜文本票数未过半，或平均置信度低于阈值。
            votes = text_votes[best_text]
            avg_confidence = score_sum[best_text] / votes if votes else 0.0
            if votes < len(sample_results) / 2 or avg_confidence < self.vlm_refine_min_confidence:
                hard_lines.append(line_idx)

        # VLM 难帧兜底：仅替换 rec_texts，失败时保持投票结果不变。
        self._maybe_vlm_refine(best_ocr_data, ordered_samples, sample_results, hard_lines)

        # Preserve time on the sampled frame (not used for fill; fill uses per-frame time).
        base_time_sec = float(base_result_tuple[4]) if len(base_result_tuple) >= 5 and base_result_tuple[4] is not None else 0.0
        return (base_result_tuple[0], best_ocr_data, base_result_tuple[2], base_result_tuple[3], base_time_sec)

    def _maybe_vlm_refine(
        self,
        best_ocr_data: Dict[str, Any],
        sample_frames: List[Tuple],
        sample_results: List[Tuple],
        hard_lines: List[int],
    ) -> None:
        """难帧 VLM 兜底（core/vlm_refine.py）。

        只替换 ``rec_texts``；分数与框保持投票结果的值。整条路径绝不抛异常：
        任何失败只记一次 warning 并保持原结果。ocr_calls 统计不受影响。

        ``sample_frames`` 与 ``sample_results`` 按序一一对应：前者是原始帧
        元组（用于取图像），后者是 OCR 结果元组（用于取各行候选文本）。
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

            # 每行的候选文本 = 各采样帧中该行的文本（去重保序）。
            candidates: List[List[str]] = []
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

