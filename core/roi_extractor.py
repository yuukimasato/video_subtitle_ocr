import os
import cv2
import numpy as np
import logging
from typing import List, Dict, Generator, Tuple, Union, DefaultDict
from collections import defaultdict
from PySide6.QtCore import QCoreApplication

logger = logging.getLogger(__name__)

_has_cuda_gpu = False
try:
    if cv2.cuda.getCudaEnabledDeviceCount() > 0:
        _has_cuda_gpu = True
        logger.info(QCoreApplication.translate("roi_extractor", "CUDA-enabled GPU detected and available for OpenCV."))
    else:
        logger.info(QCoreApplication.translate("roi_extractor", "No CUDA-enabled GPU detected or OpenCV not compiled with CUDA support."))
except AttributeError:
    logger.info(QCoreApplication.translate("roi_extractor", "OpenCV CUDA module not found. Likely OpenCV was not compiled with CUDA support."))
except Exception as e:
    logger.warning(QCoreApplication.translate("roi_extractor", f"Error checking for CUDA GPU: {e}"))

def get_roi_frame_number(roi_entry: Dict, fps: float, time_key: str, frame_key: str) -> int:
    if frame_key in roi_entry and roi_entry[frame_key] is not None:
        return int(roi_entry[frame_key])
    time_val = roi_entry.get(time_key, 0)
    if isinstance(time_val, str): 
        try:
            parts = time_val.replace(',', '.').split(':')
            if len(parts) == 3:
                h, m, s_ms = parts
                s_parts = s_ms.split('.')
                s = int(s_parts[0])
                ms = int(s_parts[1]) if len(s_parts) > 1 else 0
                total_seconds = float(h) * 3600 + float(m) * 60 + float(s) + ms / 1000.0
                return int(total_seconds * fps)
        except:
            pass 
    return int(float(time_val) * fps)

def calculate_total_roi_frames(
    roi_data: List[Dict],
    total_frames: int,
    fps: float
) -> int:
    if not roi_data:
        return 0

    frame_to_rois_map: DefaultDict[int, List[int]] = defaultdict(list)
    for idx, roi_entry in enumerate(roi_data):
        start_frame = get_roi_frame_number(roi_entry, fps, 'start_time', 'start_frame')
        end_frame = get_roi_frame_number(roi_entry, fps, 'end_time', 'end_frame')
        
        start_frame = max(0, min(start_frame, total_frames - 1))
        end_frame = max(0, min(end_frame, total_frames - 1))
        
        for frame_num in range(start_frame, end_frame + 1):
            frame_to_rois_map[frame_num].append(idx)

    total_roi_frames = sum(len(rois) for rois in frame_to_rois_map.values())
    logger.info(
        QCoreApplication.translate(
            "roi_extractor",
            "Pre-calculated total of {} ROI frames to process."
        ).format(total_roi_frames)
    )
    return total_roi_frames


def extract_roi_frames(
    video_path: str,
    roi_data: List[Dict],
    total_frames: int,
    fps: float,
    work_dir: str,
    save_to_disk: bool = True
) -> Generator[Tuple[Dict, Union[str, np.ndarray], int, str], None, None]:
    if not video_path or not roi_data:
        logger.warning(QCoreApplication.translate("roi_extractor", "Extraction cannot start: video path or ROI data not provided."))
        return

    if save_to_disk and not work_dir:
        logger.error(QCoreApplication.translate("roi_extractor", "In save_to_disk mode, a valid working directory `work_dir` must be provided."))
        return

    video_name = os.path.splitext(os.path.basename(video_path))[0]

    frame_to_rois_map: DefaultDict[int, List[int]] = defaultdict(list)
    for idx, roi_entry in enumerate(roi_data):
        start_frame = get_roi_frame_number(roi_entry, fps, 'start_time', 'start_frame')
        end_frame = get_roi_frame_number(roi_entry, fps, 'end_time', 'end_frame')
        
        start_frame = max(0, min(start_frame, total_frames - 1))
        end_frame = max(0, min(end_frame, total_frames - 1))
        
        for frame_num in range(start_frame, end_frame + 1):
            frame_to_rois_map[frame_num].append(idx)
    
    unique_frame_nums = sorted(frame_to_rois_map.keys())
    
    cap = cv2.VideoCapture(video_path)
    if not cap.isOpened():
        logger.error(
            QCoreApplication.translate(
                "roi_extractor",
                "Could not open video file for extraction: {}"
            ).format(video_path)
        )
        return
    
    roi_dirs = {}
    if save_to_disk:
        for idx in range(len(roi_data)):
            roi_identifier = f"roi_{idx}"
            output_dir = os.path.join(work_dir, "1_roi_images", f"{video_name}_{roi_identifier}")
            os.makedirs(output_dir, exist_ok=True)
            roi_dirs[idx] = output_dir
    
    use_gpu_for_processing = _has_cuda_gpu
    if use_gpu_for_processing:
        logger.info(QCoreApplication.translate("roi_extractor", "Attempting to use GPU for ROI extraction."))
    else:
        logger.info(QCoreApplication.translate("roi_extractor", "Using CPU for ROI extraction (GPU not available or OpenCV not compiled with CUDA)."))

    try:
        for frame_num in unique_frame_nums:
            cap.set(cv2.CAP_PROP_POS_FRAMES, frame_num)
            ret, frame = cap.read()
            if not ret:
                logger.warning(
                    QCoreApplication.translate(
                        "roi_extractor",
                        "Could not read video frame {}."
                    ).format(frame_num)
                )
                continue
            
            gpu_frame = None
            if use_gpu_for_processing:
                try:
                    gpu_frame = cv2.cuda_GpuMat()
                    gpu_frame.upload(frame)
                except Exception as e:
                    logger.warning(
                        QCoreApplication.translate(
                            "roi_extractor",
                            "Failed to upload frame to GPU for frame {}. Falling back to CPU for this frame. Error: {}"
                        ).format(frame_num, e)
                    )
                    gpu_frame = None
            current_processing_frame = gpu_frame if gpu_frame is not None else frame
            
            if gpu_frame is not None:
                h_img, w_img = gpu_frame.rows, gpu_frame.cols
            else:
                h_img, w_img = frame.shape[:2]

            for roi_idx in frame_to_rois_map[frame_num]:
                roi_entry = roi_data[roi_idx]
                roi_identifier = f"roi_{roi_idx}"
                roi_type = roi_entry.get('type', 'rect')
                points = roi_entry['points']
                
                roi_frame_result = None
                
                if roi_type == 'rect':
                    if isinstance(points, list) and len(points) == 4:
                        x, y, w, h = [int(p) for p in points]
                        
                        y1, y2 = max(0, y), min(h_img, y + h)
                        x1, x2 = max(0, x), min(w_img, x + w)
                        
                        if y2 > y1 and x2 > x1:
                            if gpu_frame is not None:
                                gpu_roi_frame = current_processing_frame[y1:y2, x1:x2]
                                roi_frame_result = gpu_roi_frame.download()
                            else:
                                roi_frame_result = current_processing_frame[y1:y2, x1:x2]
                    else:
                        logger.error(
                            QCoreApplication.translate(
                                "roi_extractor",
                                "Incorrect points format for rectangular ROI: {}"
                            ).format(points)
                        )
                        continue
                elif roi_type == 'poly':
                    try:
                        poly_points = np.array(points, dtype=np.int32)
                        if poly_points.ndim != 2 or poly_points.shape[1] != 2 or poly_points.shape[0] < 2:
                            logger.error(
                                QCoreApplication.translate(
                                    "roi_extractor",
                                    "Incorrect points format for polygonal ROI: {}"
                                ).format(points)
                            )
                            continue
                        
                        poly_points[:, 0] = np.clip(poly_points[:, 0], 0, w_img - 1)
                        poly_points[:, 1] = np.clip(poly_points[:, 1], 0, h_img - 1)

                        if gpu_frame is not None:
                            cpu_mask = np.zeros((h_img, w_img), dtype=np.uint8)
                            cv2.fillPoly(cpu_mask, [poly_points], 255)
                            
                            gpu_mask = cv2.cuda_GpuMat()
                            gpu_mask.upload(cpu_mask)

                            gpu_masked_frame = cv2.cuda_GpuMat()
                            cv2.cuda.bitwise_and(current_processing_frame, current_processing_frame, gpu_masked_frame, mask=gpu_mask)
                            
                            x, y, w, h = cv2.boundingRect(poly_points)
                            
                            y1, y2 = max(0, y), min(h_img, y + h)
                            x1, x2 = max(0, x), min(w_img, x + w)
                            if y2 > y1 and x2 > x1:
                                gpu_roi_frame = gpu_masked_frame[y1:y2, x1:x2]
                                roi_frame_result = gpu_roi_frame.download()
                        else:
                            mask = np.zeros((h_img, w_img), dtype=np.uint8)
                            cv2.fillPoly(mask, [poly_points], 255)
                            masked_frame = cv2.bitwise_and(current_processing_frame, current_processing_frame, mask=mask)
                            
                            x, y, w, h = cv2.boundingRect(poly_points)
                            
                            y1, y2 = max(0, y), min(h_img, y + h)
                            x1, x2 = max(0, x), min(w_img, x + w)
                            if y2 > y1 and x2 > x1:
                                roi_frame_result = masked_frame[y1:y2, x1:x2]
                    except Exception as e:
                        logger.error(
                            QCoreApplication.translate(
                                "roi_extractor",
                                "Error processing polygonal ROI: {}"
                            ).format(e)
                        )
                        continue
                else:
                    logger.error(
                        QCoreApplication.translate(
                            "roi_extractor",
                            "Unknown ROI type: {}"
                        ).format(roi_type)
                    )
                    continue

                if roi_frame_result is not None and roi_frame_result.size > 0:
                    if save_to_disk:
                        filename = f"frame_{frame_num:06d}.jpg"
                        frame_path = os.path.join(roi_dirs[roi_idx], filename)
                        cv2.imwrite(frame_path, roi_frame_result)
                        yield roi_entry, frame_path, frame_num, roi_identifier
                    else:
                        yield roi_entry, roi_frame_result.copy(), frame_num, roi_identifier
                else:
                    logger.warning(
                        QCoreApplication.translate(
                            "roi_extractor",
                            "ROI extraction resulted in empty image, frame {}, ROI {}"
                        ).format(frame_num, roi_identifier)
                    )

    finally:
        cap.release()
