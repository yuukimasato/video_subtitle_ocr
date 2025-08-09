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
    ocr_result_generator: Generator[Tuple[Dict, Union[str, Dict[str, Any]], int, str], None, None],
    work_dir: str
) -> Generator[Tuple[Dict, int, str], None, None]:
    if not work_dir:
        logger.error(QCoreApplication.translate("coordinate_restorer", "A valid working directory `work_dir` must be provided."))
        return

    for roi_entry, ocr_data_or_path, frame_num, roi_identifier in ocr_result_generator:
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

            output_dir = os.path.join(work_dir, "3_restored_json", roi_identifier)
            os.makedirs(output_dir, exist_ok=True)
            
            output_file_name = f"frame_{frame_num:06d}_{roi_identifier}_restored.json"
            output_file_path = os.path.join(output_dir, output_file_name)
            with open(output_file_path, 'w', encoding='utf-8') as f:
                json.dump(transformed_data, f, ensure_ascii=False, indent=2)
            
            yield transformed_data, frame_num, roi_identifier

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
    roi_type = roi_entry.get('type', 'rect')
    points = roi_entry['points']
    
    if roi_type == 'rect':
        if isinstance(points, list) and len(points) == 4:
            return int(points[0]), int(points[1])
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
    new_data = json.loads(json.dumps(data)) 

    for key in ['dt_polys', 'rec_polys']:
        if key in new_data and isinstance(new_data[key], list):
            for poly in new_data[key]:
                if isinstance(poly, list):
                    for point in poly:
                        if isinstance(point, list) and len(point) == 2:
                            point[0] = float(point[0]) + offset_x
                            point[1] = float(point[1]) + offset_y

    if 'rec_boxes' in new_data and isinstance(new_data['rec_boxes'], list):
        for box in new_data['rec_boxes']:
            if isinstance(box, list) and len(box) == 4:
                box[0] = float(box[0]) + offset_x
                box[1] = float(box[1]) + offset_y
                box[2] = float(box[2]) + offset_x
                box[3] = float(box[3]) + offset_y
            
    return new_data

