# core/coordinate_restorer.py
import os
import json
import logging
from typing import Generator, Tuple, Dict, Any, Optional, Union
import numpy as np
import cv2
from PySide6.QtCore import QCoreApplication

logger = logging.getLogger(__name__)

def restore_coordinates(
    ocr_result_generator: Generator[Tuple, None, None],
    work_dir: str,
    save_json: bool = True
) -> Generator[Tuple[Dict, int, str, float], None, None]:
    if not work_dir:
        logger.error(QCoreApplication.translate("coordinate_restorer", "A valid working directory `work_dir` must be provided."))
        return

    for item in ocr_result_generator:
        roi_entry, ocr_data_or_path, frame_num, roi_identifier = item[:4]
        frame_time_sec = float(item[4]) if len(item) >= 5 and item[4] is not None else 0.0
        try:
            if ocr_data_or_path is None:
                logger.warning(
                    QCoreApplication.translate(
                        "coordinate_restorer",
                        "Frame {} (ROI: {}) has no OCR results, coordinate restoration skipped."
                    ).format(frame_num, roi_identifier)
                )
                continue

            offset = _get_roi_offset(roi_entry)
            if offset is None:
                logger.warning(
                    QCoreApplication.translate(
                        "coordinate_restorer",
                        "Could not get offset for frame {} (ROI: {}), skipped."
                    ).format(frame_num, roi_identifier)
                )
                continue
            
            offset_x, offset_y = offset
            
            original_data = {}
            if isinstance(ocr_data_or_path, str):
                json_path = ocr_data_or_path
                if not os.path.exists(json_path):
                    logger.warning(
                        QCoreApplication.translate(
                            "coordinate_restorer",
                            "OCR result file does not exist: {}, skipped."
                        ).format(json_path)
                    )
                    continue
                with open(json_path, 'r', encoding='utf-8') as f:
                    original_data = json.load(f)
            elif isinstance(ocr_data_or_path, dict):
                original_data = ocr_data_or_path
            else:
                logger.warning(
                    QCoreApplication.translate(
                        "coordinate_restorer",
                        "Unknown OCR result data type (Frame {}, ROI {}). Type: {}, skipped."
                    ).format(frame_num, roi_identifier, type(ocr_data_or_path))
                )
                continue

            is_empty_ocr_result = not original_data.get('rec_texts')

            transformed_data = {}
            if not is_empty_ocr_result:
                transformed_data = _transform_json_coordinates(original_data, offset_x, offset_y)
            else:
                transformed_data = original_data
                logger.debug(
                    QCoreApplication.translate(
                        "coordinate_restorer",
                        "Frame {} (ROI: {}) OCR result is empty, skipping coordinate restoration."
                    ).format(frame_num, roi_identifier)
                )

            if save_json:
                output_dir = os.path.join(work_dir, "3_restored_json", roi_identifier)
                os.makedirs(output_dir, exist_ok=True)
                
                output_file_name = f"frame_{frame_num:06d}_{roi_identifier}_restored.json"
                output_file_path = os.path.join(output_dir, output_file_name)
                with open(output_file_path, 'w', encoding='utf-8') as f:
                    json.dump(transformed_data, f, ensure_ascii=False, indent=2)
            
            yield transformed_data, frame_num, roi_identifier, frame_time_sec

        except Exception as e:
            logger.error(
                QCoreApplication.translate(
                    "coordinate_restorer",
                    "Error restoring coordinates for frame {} (ROI: {}): {}"
                ).format(frame_num, roi_identifier, e),
                exc_info=True
            )
            continue

def _get_roi_offset(roi_entry: Dict) -> Optional[Tuple[int, int]]:
    # Full-frame composite output: coordinates already in original video space.
    if isinstance(roi_entry, dict) and roi_entry.get("full_frame"):
        return 0, 0
    roi_type = roi_entry.get('type', 'rect')
    points = roi_entry['points']
    
    if roi_type == 'rect':
        if isinstance(points, list) and len(points) == 4:
            # roi_extractor clamps the crop origin to the frame (max(0, x/y));
            # the offset added back during restoration must match that clamp,
            # or out-of-frame ROIs restore shifted coordinates.
            return max(0, int(points[0])), max(0, int(points[1]))
        else:
            logger.error(
                QCoreApplication.translate(
                    "coordinate_restorer",
                    "Incorrect points format for rectangular ROI: {}"
                ).format(points)
            )
            return None
    elif roi_type == 'poly':
        try:
            poly_points = np.array(points, dtype=np.int32)
            if poly_points.ndim != 2 or poly_points.shape[1] != 2 or poly_points.shape[0] < 2:
                logger.error(
                    QCoreApplication.translate(
                        "coordinate_restorer",
                        "Incorrect points format for polygonal ROI: {}"
                    ).format(points)
                )
                return None
            # Match roi_extractor: points are clipped to the frame before
            # the bounding rect is taken, so the origin is never negative.
            poly_points[:, 0] = np.clip(poly_points[:, 0], 0, None)
            poly_points[:, 1] = np.clip(poly_points[:, 1], 0, None)
            x, y, w, h = cv2.boundingRect(poly_points)
            return x, y
        except Exception as e:
            logger.error(
                QCoreApplication.translate(
                    "coordinate_restorer",
                    "Error parsing polygonal ROI offset: {}"
                ).format(e)
            )
            return None
    logger.error(
        QCoreApplication.translate(
            "coordinate_restorer",
            "Unknown ROI type: {}"
        ).format(roi_type)
    )
    return None

def _transform_json_coordinates(data: Dict[str, Any], offset_x: int, offset_y: int) -> Dict[str, Any]:
    # 仅对发生坐标偏移的键构造新容器（其余键浅拷贝共享），
    # 避免逐帧 json.loads(json.dumps(...)) 深拷贝的性能开销。
    new_data = data

    def _shift_polys(polys: Any) -> Any:
        if not isinstance(polys, list):
            return polys
        shifted = []
        for poly in polys:
            if isinstance(poly, list):
                shifted.append([
                    [float(p[0]) + offset_x, float(p[1]) + offset_y]
                    if isinstance(p, list) and len(p) == 2
                    else p
                    for p in poly
                ])
            else:
                shifted.append(poly)
        return shifted

    def _shift_boxes(boxes: Any) -> Any:
        if not isinstance(boxes, list):
            return boxes
        return [
            [float(b[0]) + offset_x, float(b[1]) + offset_y,
             float(b[2]) + offset_x, float(b[3]) + offset_y]
            if isinstance(b, list) and len(b) == 4
            else b
            for b in boxes
        ]

    for key in ('dt_polys', 'rec_polys'):
        if key in data:
            new_data = {**new_data, key: _shift_polys(data[key])}

    if 'rec_boxes' in data:
        new_data = {**new_data, 'rec_boxes': _shift_boxes(data['rec_boxes'])}

    return new_data

