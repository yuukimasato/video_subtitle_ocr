"""
OCR Processor — Deprecated compatibility module.

As of v2.0, this module is kept for backward compatibility only.
New code should use core.ocr_engine_manager instead, which provides
a unified interface across multiple OCR engines (PaddleOCR, RapidOCR, etc.).

All public functions in this module now delegate to ocr_engine_manager.
"""

import os
import logging
from typing import Iterator, Tuple, Dict, Any

from PySide6.QtCore import QCoreApplication

logger = logging.getLogger(__name__)


def get_device_mode() -> str:
    """
    Returns "cpu" or "gpu" based on environment and .gpu_mode file.
    This is used to decide safe parallelism in the pipeline.

    Priority order:
      1. MODE environment variable (set by launcher scripts)
      2. .gpu_mode file (supports both MODE=gpu and export MODE="gpu" formats)
      3. Runtime PaddlePaddle GPU availability check
      4. Default: "cpu"

    Deprecated: kept for backward compatibility.
    """
    import re

    # ── Strategy 1: Environment variable ──────────────────
    env_mode = os.environ.get("MODE", "").strip().lower()
    if env_mode in ("gpu", "cpu"):
        return env_mode

    # ── Strategy 2: Read .gpu_mode file ───────────────────
    gpu_mode_file = os.path.join(os.path.dirname(__file__), "..", ".gpu_mode")
    try:
        if os.path.exists(gpu_mode_file):
            with open(gpu_mode_file, "r") as f:
                for line in f:
                    line = line.strip()
                    if not line or line.startswith("#"):
                        continue
                    m = re.search(r'(?:export\s+)?MODE\s*=\s*["\']?(\w+)["\']?', line)
                    if m:
                        file_mode = m.group(1).strip().lower()
                        if file_mode in ("gpu", "cpu"):
                            return file_mode
    except Exception:
        pass

    # ── Strategy 3: Runtime GPU check via PaddlePaddle ────
    try:
        import paddle
        if paddle.is_compiled_with_cuda() and paddle.device.cuda.device_count() > 0:
            return "gpu"
        if hasattr(paddle, 'device'):
            if getattr(paddle.device, 'is_compiled_with_rocm', lambda: False)():
                return "gpu"
            if getattr(paddle.device, 'is_compiled_with_xpu', lambda: False)():
                return "gpu"
    except Exception:
        pass

    return "cpu"


def run_batch_ocr(
    frames_iter: Iterator[Tuple],
    work_dir: str,
    visualize: bool = False,
    save_json: bool = True
) -> Iterator[Tuple[Dict, Dict[str, Any], int, str, float]]:
    """Batch OCR entry point — delegates to engine manager.

    Deprecated: kept for backward compatibility.
    New code should call ocr_engine_manager.run_batch_ocr() directly.
    """
    from core.ocr_engine_manager import run_batch_ocr as _run
    yield from _run(frames_iter, work_dir, visualize=visualize, save_json=save_json)


def process_images(input_dir, output_dir, visualize=False):
    """Batch process images in a directory (CLI mode).

    Deprecated: kept for backward compatibility.
    """
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

    parser = argparse.ArgumentParser(description=QCoreApplication.translate("ocr_processor", "Batch OCR Image Processing"))
    parser.add_argument('--input', '-i', required=True,
                        help=QCoreApplication.translate("ocr_processor", "Input image directory path"))
    parser.add_argument('--output', '-o', required=True,
                        help=QCoreApplication.translate("ocr_processor", "Output results directory path"))
    parser.add_argument('--visualize', '-v', action='store_true',
                        help=QCoreApplication.translate("ocr_processor", "Enable visualization output"))
    
    args = parser.parse_args()
    process_images(args.input, args.output, visualize=args.visualize)

