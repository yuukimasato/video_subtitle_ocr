# components/scan_worker_thread.py
"""后台线程运行 core.fullframe_scanner.perform_scan。

“加载视频后快速自动 ROI”与“深度扫描复核”共用本线程，仅采样数与快照
开关不同。perform_scan 懒加载：缺失模块只禁用扫描功能，不影响主窗口导入。
支持 cancel()：通过 progress_cb 抛出内部异常中止扫描（perform_scan 本身
没有取消参数）。
"""

from __future__ import annotations

import logging

from PySide6.QtCore import QThread, Signal

logger = logging.getLogger(__name__)


class _ScanCancelled(Exception):
    """内部异常：在 progress_cb 中抛出以中止已取消的扫描。"""


class ScanWorkerThread(QThread):
    scan_finished = Signal(object)  # ScanReport
    scan_error = Signal(str)
    scan_progress = Signal(int, int)  # (done, total) sampled frames

    def __init__(
        self,
        video_path: str,
        *,
        sample_count: int = 24,
        lang: str = "ch",
        model_tier: str = "auto",
        ocr_engine_id: str = "paddle",
        with_snapshots: bool = True,
        parent=None,
    ):
        super().__init__(parent)
        self._video_path = video_path
        self._sample_count = int(sample_count)
        self._lang = lang
        self._model_tier = model_tier
        self._ocr_engine_id = ocr_engine_id
        self._with_snapshots = bool(with_snapshots)
        self._cancelled = False

    def cancel(self) -> None:
        """请求取消；扫描在下一个进度回调点中止，结果被丢弃。"""
        self._cancelled = True

    def run(self) -> None:
        try:
            from core.fullframe_scanner import perform_scan
        except ImportError as e:
            self.scan_error.emit(str(e))
            return

        def _progress(done: int, total: int) -> None:
            if self._cancelled:
                raise _ScanCancelled
            self.scan_progress.emit(int(done), int(total))

        try:
            report = perform_scan(
                self._video_path,
                sample_count=self._sample_count,
                lang=self._lang,
                model_tier=self._model_tier,
                ocr_engine_id=self._ocr_engine_id,
                with_snapshots=self._with_snapshots,
                progress_cb=_progress,
            )
        except _ScanCancelled:
            logger.info("Scan cancelled; discarding partial results.")
            return
        except Exception as e:
            logger.exception("Background scan failed")
            self.scan_error.emit(str(e))
            return

        if self._cancelled:
            return
        self.scan_finished.emit(report)
