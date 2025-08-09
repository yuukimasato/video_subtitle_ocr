# core/pipeline_worker.py
import os
import logging
import datetime
import shutil
from PySide6.QtCore import QThread, Signal, QCoreApplication

from typing import List, Dict, Optional
from collections import defaultdict

from core import roi_extractor, ocr_processor, coordinate_restorer, subtitle_generator
from core.ocr_optimizer import OcrOptimizer

logger = logging.getLogger(__name__)

class PipelineWorker(QThread):
    progress_updated = Signal(int, str)
    finished = Signal(str)
    error = Signal(str)

    def __init__(self, video_path: str, roi_data: List[Dict], total_frames: int, fps: float,
                 video_width: int, video_height: int, output_ass_path: str,
                 debug_mode: bool, template_path: Optional[str],
                 in_memory_ocr: bool = False, visualize: bool = False,
                 parent=None):
        super().__init__(parent)
        self.video_path = video_path
        self.roi_data = roi_data
        self.total_frames = total_frames
        self.fps = fps
        self.video_width = video_width
        self.video_height = video_height
        self.output_ass_path = output_ass_path
        self.debug_mode = debug_mode
        self.template_path = template_path
        self.in_memory_ocr = in_memory_ocr
        self.visualize = visualize
        self.is_cancelled = False
        self.work_dir: Optional[str] = None

    def run(self):
        try:
            video_name = os.path.splitext(os.path.basename(self.video_path))[0]
            timestamp = datetime.datetime.now().strftime("%Y%m%d%H%M%S")
            self.work_dir = os.path.join(os.path.dirname(self.output_ass_path), f"{video_name}_{timestamp}_ocr_temp")
            os.makedirs(self.work_dir, exist_ok=True)
            logger.info(
                QCoreApplication.translate(
                    "pipeline_worker",
                    "Intermediate files will be saved to: {}"
                ).format(self.work_dir)
            )
            
            log_mode_in_memory = QCoreApplication.translate("pipeline_worker", "in-memory data stream")
            log_mode_disk_file = QCoreApplication.translate("pipeline_worker", "disk file stream")
            log_mode = log_mode_in_memory if self.in_memory_ocr else log_mode_disk_file
            
            logger.info(
                QCoreApplication.translate(
                    "pipeline_worker",
                    "OCR pipeline will run in {} mode."
                ).format(log_mode)
            )

            self.progress_updated.emit(0, QCoreApplication.translate("pipeline_worker", "Step 1/4: Calculating number of ROI frames to process..."))
            if self.is_cancelled: return

            try:
                total_roi_frames = roi_extractor.calculate_total_roi_frames(
                    self.roi_data, self.total_frames, self.fps
                )

                if self.is_cancelled: return

                if not total_roi_frames:
                    raise RuntimeError(QCoreApplication.translate("pipeline_worker", "ROI extraction step did not produce any data. Please check ROI time and region settings."))

                self.progress_updated.emit(
                    1,
                    QCoreApplication.translate(
                        "pipeline_worker",
                        "Step 1/4: Calculation complete, total {} frames. Starting extraction..."
                    ).format(total_roi_frames)
                )

                frames_to_process = []
                roi_start_progress = 1
                roi_progress_range = 9

                frame_generator = roi_extractor.extract_roi_frames(
                    self.video_path, self.roi_data, self.total_frames, self.fps, self.work_dir,
                    save_to_disk=not self.in_memory_ocr
                )

                for i, frame_data in enumerate(frame_generator):
                    if self.is_cancelled: return
                    frames_to_process.append(frame_data)
                    progress = roi_start_progress + int(((i + 1) / total_roi_frames) * roi_progress_range)
                    self.progress_updated.emit(
                        progress,
                        QCoreApplication.translate(
                            "pipeline_worker",
                            "Step 1/4: Extracting ROI frames... ({}/{})"
                        ).format(i + 1, total_roi_frames)
                    )

                self.progress_updated.emit(
                    10,
                    QCoreApplication.translate(
                        "pipeline_worker",
                        "Step 1/4: ROI frame extraction complete. Total {} ROI frames."
                    ).format(len(frames_to_process))
                )

            except Exception as e:
                logger.error(
                    QCoreApplication.translate(
                        "pipeline_worker",
                        "Error during ROI extraction: {}"
                    ).format(e)
                )
                raise

            self.progress_updated.emit(
                10,
                QCoreApplication.translate(
                    "pipeline_worker",
                    "Step 2/4: Starting intelligent OCR recognition... (0/{})"
                ).format(total_roi_frames)
            )
            if self.is_cancelled: return

            roi_groups = defaultdict(list)
            for frame_data in frames_to_process:
                roi_identifier = frame_data[3]
                roi_groups[roi_identifier].append(frame_data)
            
            optimizer = OcrOptimizer(
                work_dir=self.work_dir,
                visualize=self.visualize,
                in_memory_mode=self.in_memory_ocr
            )

            ocr_results = []
            ocr_start_progress = 10
            ocr_progress_range = 70
            processed_count = 0

            for roi_id, group_frames in sorted(roi_groups.items()):
                if self.is_cancelled: break
                
                logger.info(
                    QCoreApplication.translate(
                        "pipeline_worker",
                        "Starting to process {}, containing {} frames..."
                    ).format(roi_id, len(group_frames))
                )
                group_frames.sort(key=lambda x: x[2])
                
                def progress_callback(group_processed_count: int):
                    current_total_processed = processed_count + group_processed_count
                    progress = ocr_start_progress + int((current_total_processed / total_roi_frames) * ocr_progress_range)
                    self.progress_updated.emit(
                        progress,
                        QCoreApplication.translate(
                            "pipeline_worker",
                            "Step 2/4: OCR recognition in progress... ({}/{})"
                        ).format(current_total_processed, total_roi_frames)
                    )

                optimized_group_results = optimizer.process_roi_group(
                    group_frames, 
                    is_cancelled_func=lambda: self.is_cancelled,
                    progress_callback=progress_callback
                )
                ocr_results.extend(optimized_group_results)
                
                processed_count += len(group_frames)

            optimizer.cleanup() 

            if self.is_cancelled: return

            if not ocr_results:
                raise RuntimeError(QCoreApplication.translate("pipeline_worker", "OCR recognition step did not produce any results."))
            self.progress_updated.emit(80, QCoreApplication.translate("pipeline_worker", "Step 2/4: OCR recognition complete."))

            self.progress_updated.emit(
                80,
                QCoreApplication.translate(
                    "pipeline_worker",
                    "Step 3/4: Starting coordinate restoration... (0/{})"
                ).format(len(ocr_results))
            )
            if self.is_cancelled: return

            restored_results = []
            restore_start_progress = 80
            restore_progress_range = 10
            
            restored_generator = coordinate_restorer.restore_coordinates(iter(ocr_results), self.work_dir)

            for i, restored_result in enumerate(restored_generator):
                if self.is_cancelled: return
                restored_results.append(restored_result)
                progress = restore_start_progress + int(((i + 1) / len(ocr_results)) * restore_progress_range)
                self.progress_updated.emit(
                    progress,
                    QCoreApplication.translate(
                        "pipeline_worker",
                        "Step 3/4: Restoring coordinates... ({}/{})"
                    ).format(i + 1, len(ocr_results))
                )

            if not restored_results:
                raise RuntimeError(QCoreApplication.translate("pipeline_worker", "Coordinate restoration step did not produce any results."))
            self.progress_updated.emit(90, QCoreApplication.translate("pipeline_worker", "Step 3/4: Coordinate restoration complete."))

            self.progress_updated.emit(90, QCoreApplication.translate("pipeline_worker", "Step 4/4: Starting ASS subtitle file generation..."))
            if self.is_cancelled: return

            converter = subtitle_generator.OCRToASSOptimizer(
                video_path=self.video_path, output_path=self.output_ass_path, fps=self.fps,
                width=self.video_width, height=self.video_height, template_path=self.template_path
            )
            converter.convert_from_memory(iter(restored_results))
            self.progress_updated.emit(100, QCoreApplication.translate("pipeline_worker", "Step 4/4: ASS subtitle generation complete."))

            self.finished.emit(self.output_ass_path)

        except Exception as e:
            logger.error(
                QCoreApplication.translate(
                    "pipeline_worker",
                    "Pipeline processing failed: {}"
                ).format(e),
                exc_info=True
            )
            self.error.emit(
                QCoreApplication.translate(
                    "pipeline_worker",
                    "An error occurred during processing: {}"
                ).format(e)
            )
        finally:
            if self.work_dir and not self.debug_mode:
                try:
                    shutil.rmtree(self.work_dir)
                    logger.info(
                        QCoreApplication.translate(
                            "pipeline_worker",
                            "Temporary working directory deleted: {}"
                        ).format(self.work_dir)
                    )
                except Exception as e:
                    logger.warning(
                        QCoreApplication.translate(
                            "pipeline_worker",
                            "Could not delete temporary working directory {}: {}"
                        ).format(self.work_dir, e)
                    )

    def cancel(self):
        self.is_cancelled = True
        logger.info(QCoreApplication.translate("pipeline_worker", "Task cancellation request sent."))
        
    def terminate(self):
        if self.isRunning():
            logger.warning(QCoreApplication.translate("pipeline_worker", "Forcibly terminating thread..."))
            super().terminate()

