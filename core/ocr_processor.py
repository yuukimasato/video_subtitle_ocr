import os
import json
import subprocess
from typing import Iterator, Union, Tuple, Dict, Any, List
from paddleocr import PaddleOCR
import numpy as np
import logging

logger = logging.getLogger(__name__)

_ocr_instance = None

def _get_paddle_ocr_instance():
    global _ocr_instance
    if _ocr_instance is None:
        logger.info("初始化 PaddleOCR 模型...")
        
        gpu_mode = "cpu"
        gpu_mode_file = os.path.join(os.path.dirname(__file__), "../.gpu_mode")
        if os.path.exists(gpu_mode_file):
            with open(gpu_mode_file, "r") as f:
                for line in f:
                    if line.startswith("export MODE="):
                        gpu_mode = line.strip().split("=")[1].strip('"')
                        break
        
        device = gpu_mode 
        logger.info(f"PaddleOCR将使用 {device.upper()}模式运行 ")

        _ocr_instance = PaddleOCR(
            use_doc_orientation_classify=False,
            use_doc_unwarping=False,
            use_textline_orientation=False,
            lang="ch",
            device=device
        )
        logger.info("PaddleOCR model initialized.")
    return _ocr_instance

def run_batch_ocr(frames_iter: Iterator[Tuple[Dict, Union[str, np.ndarray], int, str]], work_dir: str, visualize: bool = False) -> Iterator[Tuple[Dict, Dict[str, Any], int, str]]:
    ocr = _get_paddle_ocr_instance()
    
    ocr_output_dir = os.path.join(work_dir, "2_ocr_results")
    os.makedirs(ocr_output_dir, exist_ok=True)
    
    for frame_info in frames_iter:
        roi_entry_orig, img_input, frame_num, roi_identifier = frame_info
        
        is_path_input = isinstance(img_input, str)
        
        if is_path_input:
            logger.debug(f"Processing image from path: {img_input}")
            base_name = f"{os.path.splitext(os.path.basename(img_input))[0]}_{roi_identifier}"
        else:
            logger.debug(f"Processing image from memory (frame {frame_num}, ROI {roi_identifier})")
            base_name = f"frame_{frame_num:06d}_{roi_identifier}"

        raw_ocr_results: List[Any] = ocr.predict(img_input)

        json_path = os.path.join(ocr_output_dir, f"{base_name}.json")
        
        ocr_data_dict: Dict[str, Any] = {
            'dt_polys': [],
            'rec_polys': [],
            'rec_texts': [],
            'rec_scores': [],
            'rec_boxes': []
        }

        if raw_ocr_results:
            if isinstance(raw_ocr_results, list) and len(raw_ocr_results) == 1 and isinstance(raw_ocr_results[0], dict):
                single_dict_result = raw_ocr_results[0]
                texts = single_dict_result.get('rec_texts', [])
                scores = single_dict_result.get('rec_scores', [])
                polys = single_dict_result.get('rec_polys', [])
                boxes = single_dict_result.get('rec_boxes', [])

                min_len = min(len(texts), len(scores), len(polys), len(boxes))
                
                for i in range(min_len):
                    ocr_data_dict['dt_polys'].append(polys[i].tolist() if isinstance(polys[i], np.ndarray) else polys[i])
                    ocr_data_dict['rec_polys'].append(polys[i].tolist() if isinstance(polys[i], np.ndarray) else polys[i])
                    ocr_data_dict['rec_texts'].append(texts[i])
                    ocr_data_dict['rec_scores'].append(float(scores[i]))
                    ocr_data_dict['rec_boxes'].append(boxes[i].tolist() if isinstance(boxes[i], np.ndarray) else boxes[i])
                logger.debug(f"Processed document-level OCR result for frame {frame_num}, ROI {roi_identifier}.")

            else:
                for line_result in raw_ocr_results:
                    if len(line_result) == 2 and isinstance(line_result[0], list) and isinstance(line_result[1], tuple):
                        box_polygon = line_result[0]
                        text, score = line_result[1]

                        ocr_data_dict['dt_polys'].append(box_polygon)
                        ocr_data_dict['rec_polys'].append(box_polygon)
                        ocr_data_dict['rec_texts'].append(text)
                        ocr_data_dict['rec_scores'].append(float(score))

                        np_poly = np.array(box_polygon, dtype=np.int32)
                        x_min, y_min = np.min(np_poly[:, 0]), np.min(np_poly[:, 1])
                        x_max, y_max = np.max(np_poly[:, 0]), np.max(np_poly[:, 1])
                        ocr_data_dict['rec_boxes'].append([int(x_min), int(y_min), int(x_max), int(y_max)])
                    else:
                        logger.warning(f"Unexpected line result format for frame {frame_num}, ROI {roi_identifier}: {line_result}")

            with open(json_path, 'w', encoding='utf-8') as f:
                json.dump(ocr_data_dict, f, ensure_ascii=False, indent=2)
            
            logger.debug(f"Saved OCR results to: {json_path}")
            
            if visualize:
                logger.info(f"OCR Results for frame {frame_num}, ROI {roi_identifier}:")
                for i, (text, score) in enumerate(zip(ocr_data_dict['rec_texts'], ocr_data_dict['rec_scores'])):
                    logger.info(f"  {i+1}. Text: {text}, Score: {score:.2f}")
                
                img_output_dir = os.path.join(work_dir, "ocr_visualization")
                os.makedirs(img_output_dir, exist_ok=True)
                
                import tempfile
                if isinstance(img_input, str):
                    img_path = img_input
                else:
                    import cv2
                    temp_dir = tempfile.mkdtemp()
                    img_path = os.path.join(temp_dir, f"temp_{base_name}_{roi_identifier}.jpg")
                    cv2.imwrite(img_path, img_input)
                
                ocr = _get_paddle_ocr_instance()
                result = ocr.predict(img_path)
                
                for res in result:
                    res.save_to_img(img_output_dir)
                
                logger.info(f"Saved visualization to directory: {img_output_dir}")
        else:
            logger.debug(f"No OCR results found for frame {frame_num}, ROI {roi_identifier}.")
            with open(json_path, 'w', encoding='utf-8') as f:
                json.dump(ocr_data_dict, f, ensure_ascii=False, indent=2)
            logger.debug(f"Saved empty OCR results to: {json_path}")

        yield (
            roi_entry_orig,
            ocr_data_dict,
            frame_num,
            roi_identifier
        )

def process_images(input_dir, output_dir, visualize=False):
    os.makedirs(output_dir, exist_ok=True)
    image_files = sorted([
        f for f in os.listdir(input_dir)
        if f.lower().endswith(('.jpg', '.jpeg', '.png'))
    ])
    frames_iter = (({}, os.path.join(input_dir, f), i, f"file_{i}") for i, f in enumerate(image_files))
    for _ in run_batch_ocr(frames_iter, output_dir):
        pass

if __name__ == "__main__":
    import argparse
    
    logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

    parser = argparse.ArgumentParser(description='批量OCR处理图片')
    parser.add_argument('--input', '-i', required=True,
                        help='输入图片目录路径')
    parser.add_argument('--output', '-o', required=True,
                        help='输出结果目录路径')
    parser.add_argument('--visualize', '-v', action='store_true',
                        help='启用可视化输出')
    
    args = parser.parse_args()
    process_images(args.input, args.output, visualize=args.visualize)
