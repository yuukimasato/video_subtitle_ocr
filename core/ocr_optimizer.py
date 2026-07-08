# core/ocr_optimizer.py
import os
import logging
import cv2
import numpy as np
from typing import List, Dict, Optional, Tuple, Any, Callable
from skimage.metrics import structural_similarity as ssim
from collections import Counter, defaultdict
from PySide6.QtCore import QCoreApplication 

from core import ocr_engine_manager

logger = logging.getLogger(__name__)

class OcrOptimizer:

    def __init__(self,
                 work_dir: str,
                 visualize: bool,
                 in_memory_mode: bool,
                 save_ocr_json: bool = True,
                 image_similarity_threshold: float = 0.98,
                 search_step: int = 15,
                 motion_sentinel_enabled: bool = True,
                 motion_change_ratio_threshold: float = 0.05,
                 motion_pixel_delta: int = 10,
                 ocr_engine_id: str = ""):

        self.work_dir = work_dir
        self.visualize = visualize
        self.in_memory_mode = in_memory_mode
        self.save_ocr_json = save_ocr_json
        self.image_similarity_threshold = image_similarity_threshold
        self.search_step = search_step
        self.ocr_engine_id = ocr_engine_id  # If set, switch engine before first OCR call

        # Motion sentinel: skip OCR when ROI hardly changes between adjacent frames.
        # This is most effective during static subtitle display (lasting seconds).
        self.motion_sentinel_enabled = bool(motion_sentinel_enabled)
        self.motion_change_ratio_threshold = float(motion_change_ratio_threshold)
        self.motion_pixel_delta = int(motion_pixel_delta)
        self._last_gray_by_roi: Dict[str, np.ndarray] = {}
        self._last_ocr_by_roi: Dict[str, Dict[str, Any]] = {}
        
        self._image_cache: Dict[str, np.ndarray] = {}
        self._feature_cache: Dict[Tuple[str, int], np.ndarray] = {}

        # Lightweight stats for performance observability.
        self.ocr_calls: int = 0
        self.frames_filled: int = 0

    def _get_image(self, frame_data: Tuple) -> Optional[np.ndarray]:
        img_input = frame_data[1]
        if isinstance(img_input, np.ndarray):
            return img_input
        
        if isinstance(img_input, str):
            if img_input in self._image_cache:
                return self._image_cache[img_input]
            if os.path.exists(img_input):
                img = cv2.imread(img_input)
                if not self.in_memory_mode:
                    self._image_cache[img_input] = img
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
            return self._feature_cache[frame_id]

        img = self._get_image(frame_data)
        if img is None:
            return None

        gray_img = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        self._feature_cache[frame_id] = gray_img
        
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

    def _run_single_ocr(self, frame_data: Tuple) -> Tuple:
        # Switch engine on first OCR call if a specific engine is requested
        if self.ocr_engine_id and not getattr(self, '_engine_switched', False):
            from core.ocr_engine_manager import set_engine
            set_engine(self.ocr_engine_id)
            self._engine_switched = True

        self.ocr_calls += 1
        ocr_generator = ocr_engine_manager.run_batch_ocr(
            iter([frame_data]),
            self.work_dir,
            visualize=self.visualize,
            save_json=self.save_ocr_json
        )
        try:
            return next(ocr_generator)
        except StopIteration:
            # Keep extended tuple shape if present.
            frame_time_sec = float(frame_data[4]) if len(frame_data) >= 5 and frame_data[4] is not None else 0.0
            return (frame_data[0], {}, frame_data[2], frame_data[3], frame_time_sec)

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

        if text1 and text1 == text2:
            return True
        
        return False

    def _get_best_ocr_result_from_sequence(self, frame_sequence: List[Tuple]) -> Tuple:
        if not frame_sequence:
            return None

        sample_indices = {0}
        if len(frame_sequence) > 2:
            sample_indices.add(len(frame_sequence) // 2)
        if len(frame_sequence) > 1:
            sample_indices.add(len(frame_sequence) - 1)
        
        sample_results = [self._run_single_ocr(frame_sequence[i]) for i in sorted(list(sample_indices))]
        
        if len(sample_results) == 1:
            return sample_results[0]

        base_result_tuple = sample_results[0]
        num_lines = len(base_result_tuple[1].get('rec_texts', []))
        
        if num_lines == 0:
            return base_result_tuple

        best_ocr_data = {
            'dt_polys': [None] * num_lines,
            'rec_polys': [None] * num_lines,
            'rec_texts': [''] * num_lines,
            'rec_scores': [0.0] * num_lines,
            'rec_boxes': [None] * num_lines
        }

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

        # Preserve time on the sampled frame (not used for fill; fill uses per-frame time).
        base_time_sec = float(base_result_tuple[4]) if len(base_result_tuple) >= 5 and base_result_tuple[4] is not None else 0.0
        return (base_result_tuple[0], best_ocr_data, base_result_tuple[2], base_result_tuple[3], base_time_sec)

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
                reused = self._last_ocr_by_roi.get(roi_id) or self._empty_ocr_data()
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
                processed_results.append(initial_result_tuple)
                i += 1
                group_processed_count += 1
                self.frames_filled += 1
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
            
            best_result_tuple = self._get_best_ocr_result_from_sequence(similar_sequence)
            best_ocr_result = best_result_tuple[1]
            self._last_ocr_by_roi[roi_id] = best_ocr_result if isinstance(best_ocr_result, dict) else self._empty_ocr_data()

            for j in range(start_index, last_similar_index + 1):
                frame_data_to_fill = roi_frames[j]
                frame_time_sec = float(frame_data_to_fill[4]) if len(frame_data_to_fill) >= 5 and frame_data_to_fill[4] is not None else 0.0
                filled_result = (
                    frame_data_to_fill[0],
                    best_ocr_result,
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
        self._last_gray_by_roi.clear()
        self._last_ocr_by_roi.clear()
        logger.debug(QCoreApplication.translate("ocr_optimizer", "Cleaning up cache."))

