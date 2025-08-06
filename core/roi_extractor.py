# core/roi_extractor.py
import os
import cv2
import numpy as np
import logging
from typing import List, Dict, Generator, Tuple, Union, DefaultDict
from collections import defaultdict

logger = logging.getLogger(__name__)

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
    logger.info(f"预计算得到总共需要处理 {total_roi_frames} 个ROI帧。")
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
        logger.warning("无法开始提取：未提供视频路径或ROI数据。")
        return

    if save_to_disk and not work_dir:
        logger.error("在 save_to_disk 模式下，需要提供有效的工作目录 `work_dir`。")
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
        logger.error(f"无法打开视频文件进行截取: {video_path}")
        return
    
    roi_dirs = {}
    if save_to_disk:
        for idx in range(len(roi_data)):
            roi_identifier = f"roi_{idx}"
            output_dir = os.path.join(work_dir, "1_roi_images", f"{video_name}_{roi_identifier}")
            os.makedirs(output_dir, exist_ok=True)
            roi_dirs[idx] = output_dir
    
    try:
        for frame_num in unique_frame_nums:
            cap.set(cv2.CAP_PROP_POS_FRAMES, frame_num)
            ret, frame = cap.read()
            if not ret:
                logger.warning(f"无法读取视频第 {frame_num} 帧。")
                continue
            
            for roi_idx in frame_to_rois_map[frame_num]:
                roi_entry = roi_data[roi_idx]
                roi_identifier = f"roi_{roi_idx}"
                roi_type = roi_entry.get('type', 'rect')
                points = roi_entry['points']
                
                roi_frame = None
                if roi_type == 'rect':
                    if isinstance(points, list) and len(points) == 4:
                        x, y, w, h = [int(p) for p in points]
                        h_img, w_img = frame.shape[:2]
                        y1, y2 = max(0, y), min(h_img, y + h)
                        x1, x2 = max(0, x), min(w_img, x + w)
                        if y2 > y1 and x2 > x1:
                            roi_frame = frame[y1:y2, x1:x2]
                    else:
                        logger.error(f"矩形ROI的points格式不正确: {points}")
                        continue
                elif roi_type == 'poly':
                    try:
                        mask = np.zeros(frame.shape[:2], dtype=np.uint8)
                        poly_points = np.array(points, dtype=np.int32)
                        if poly_points.ndim != 2 or poly_points.shape[1] != 2 or poly_points.shape[0] < 2:
                            logger.error(f"多边形ROI的points格式不正确: {points}")
                            continue
                        
                        poly_points[:, 0] = np.clip(poly_points[:, 0], 0, frame.shape[1] - 1)
                        poly_points[:, 1] = np.clip(poly_points[:, 1], 0, frame.shape[0] - 1)

                        cv2.fillPoly(mask, [poly_points], 255)
                        masked_frame = cv2.bitwise_and(frame, frame, mask=mask)
                        
                        x, y, w, h = cv2.boundingRect(poly_points)
                        h_img, w_img = frame.shape[:2]
                        y1, y2 = max(0, y), min(h_img, y + h)
                        x1, x2 = max(0, x), min(w_img, x + w)
                        if y2 > y1 and x2 > x1:
                            roi_frame = masked_frame[y1:y2, x1:x2]
                    except Exception as e:
                        logger.error(f"处理多边形ROI时出错: {e}")
                        continue
                else:
                    logger.error(f"未知的ROI类型: {roi_type}")
                    continue

                if roi_frame is not None and roi_frame.size > 0:
                    if save_to_disk:
                        filename = f"frame_{frame_num:06d}.jpg"
                        frame_path = os.path.join(roi_dirs[roi_idx], filename)
                        cv2.imwrite(frame_path, roi_frame)
                        yield roi_entry, frame_path, frame_num, roi_identifier
                    else:
                        yield roi_entry, roi_frame.copy(), frame_num, roi_identifier
                else:
                    logger.warning(f"ROI提取为空，帧 {frame_num}，ROI {roi_identifier}")
    
    finally:
        cap.release()
