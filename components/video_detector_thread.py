# components/video_detector_thread.py
"""
Background thread for asynchronous video type auto-detection.

Runs the VideoTypeDetector in a QThread so it doesn't block the UI.
Emits the DetectionResult when complete.
"""

from __future__ import annotations

import logging
import os

from typing import List, Dict

from PySide6.QtCore import QThread, Signal

logger = logging.getLogger(__name__)


class VideoDetectorThread(QThread):
    """QThread that runs VideoTypeDetector.detect() in the background.

    Usage:
        thread = VideoDetectorThread(video_path=..., roi_data=..., ...)
        thread.detection_done.connect(on_done)
        thread.start()
    """

    detection_done = Signal(object)  # DetectionResult
    detection_error = Signal(str)

    def __init__(
        self,
        video_path: str,
        roi_data: List[Dict],
        fps: float,
        total_frames: int,
        video_width: int,
        video_height: int,
        use_llm: bool = False,
        parent=None,
    ):
        super().__init__(parent)
        self._video_path = video_path
        self._roi_data = roi_data
        self._fps = fps
        self._total_frames = total_frames
        self._video_width = video_width
        self._video_height = video_height
        self._use_llm = use_llm

    def run(self) -> None:
        """Run detection in background thread."""
        if self._fps <= 0 or self._total_frames <= 0:
            # Degenerate metadata (broken container / fps probe failure) would
            # otherwise produce absurd durations that poison every rule.
            self.detection_error.emit(
                f"invalid video metadata (fps={self._fps}, frames={self._total_frames})"
            )
            return
        try:
            from core.video_type_detector import VideoTypeDetector, VideoMetadata

            try:
                file_size_mb = os.path.getsize(self._video_path) / (1024.0 * 1024.0)
            except OSError:
                file_size_mb = 0.0

            meta = VideoMetadata(
                file_path=self._video_path,
                file_name=os.path.basename(self._video_path),
                duration_sec=self._total_frames / self._fps,
                width=self._video_width,
                height=self._video_height,
                fps=self._fps,
                total_frames=self._total_frames,
                aspect_ratio=self._video_width / max(self._video_height, 1),
                has_audio=True,  # Best-effort; audio check requires ffprobe
                audio_stream_count=1,
                codec_name="",
                file_size_mb=file_size_mb,
                file_name_keywords=[],
            )

            detector = VideoTypeDetector(use_llm=self._use_llm)
            result = detector.detect(
                video_path=self._video_path,
                metadata=meta,
                roi_data=self._roi_data,
            )
            self.detection_done.emit(result)

        except Exception as e:
            logger.exception("Video type auto-detection failed")
            self.detection_error.emit(str(e))
