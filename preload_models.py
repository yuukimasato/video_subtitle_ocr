#!/usr/bin/env python3
"""
Pre-download PaddleOCR models during installation.

This script triggers the initial model download for the most commonly used
languages (Chinese, English) so the user doesn't have to wait on first OCR run.

Models are cached to ~/.paddleocr/ by PaddleOCR internally.
"""

import sys
import os
import logging
import time

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [preload] %(message)s",
)
logger = logging.getLogger("preload_models")


def preload_paddleocr(languages=("ch", "en"), device="cpu"):
    """Trigger PaddleOCR model download for specified languages.

    Creates a small synthetic image, runs OCR on it, and discards the result.
    This forces PaddleOCR to download and cache all required models.
    """
    import numpy as np
    import cv2

    # Create a tiny test image (white background, some black text-like pixels)
    img = np.ones((100, 300, 3), dtype=np.uint8) * 255
    # Add some black pixels to simulate text
    cv2.putText(img, "Test", (50, 60), cv2.FONT_HERSHEY_SIMPLEX, 1.0, (0, 0, 0), 2)

    for lang in languages:
        logger.info(f"Preloading PaddleOCR models for language: {lang}")
        try:
            from paddleocr import PaddleOCR
            ocr = PaddleOCR(
                use_doc_orientation_classify=False,
                use_doc_unwarping=False,
                use_textline_orientation=False,
                lang=lang,
                device=device,
            )
            # Run a dummy prediction to trigger model download
            _ = ocr.predict(img)
            logger.info(f"  ✓ Language '{lang}' models ready")
        except Exception as e:
            logger.warning(f"  ⚠ Language '{lang}' preload failed: {e}")
            logger.warning(f"    Models will be downloaded on first OCR use instead.")


def preload_rapidocr():
    """Preload RapidOCR ONNX models if rapidocr_onnxruntime is installed."""
    try:
        import rapidocr_onnxruntime  # noqa: F401
    except ImportError:
        return  # Not installed, skip

    logger.info("Preloading RapidOCR (ONNX) models...")
    try:
        import numpy as np
        import cv2
        from rapidocr_onnxruntime import RapidOCR

        img = np.ones((100, 300, 3), dtype=np.uint8) * 255
        ocr = RapidOCR()
        _ = ocr(img)
        logger.info("  ✓ RapidOCR models ready")
    except Exception as e:
        logger.warning(f"  ⚠ RapidOCR preload failed: {e}")


def main():
    logger.info("=" * 60)
    logger.info("Video Subtitle OCR — Model Preloader")
    logger.info("=" * 60)

    # Determine device from environment, .gpu_mode file, or runtime check
    device = "cpu"
    import re

    # Strategy 1: Env var
    env_mode = os.environ.get("MODE", "").strip().lower()
    if env_mode in ("gpu", "cpu"):
        device = env_mode

    # Strategy 2: .gpu_mode file (supports MODE=gpu and export MODE="gpu" formats)
    if device == "cpu":
        gpu_mode_file = os.path.join(os.path.dirname(__file__), ".gpu_mode")
        if os.path.exists(gpu_mode_file):
            try:
                with open(gpu_mode_file, "r") as f:
                    for line in f:
                        line = line.strip()
                        if not line or line.startswith("#"):
                            continue
                        m = re.search(r'(?:export\s+)?MODE\s*=\s*["\']?(\w+)["\']?', line)
                        if m:
                            file_mode = m.group(1).strip().lower()
                            if file_mode in ("gpu", "cpu"):
                                device = file_mode
                                break
            except Exception:
                pass

    # Strategy 3: Runtime PaddlePaddle GPU check
    if device == "cpu":
        try:
            import paddle
            if paddle.is_compiled_with_cuda() and paddle.device.cuda.device_count() > 0:
                device = "gpu"
        except Exception:
            pass

    logger.info(f"Device mode: {device}")

    # Preload PaddleOCR (primary engine)
    preload_paddleocr(languages=("ch", "en"), device=device)

    # Preload RapidOCR (secondary engine, ONNX-based)
    preload_rapidocr()

    logger.info("=" * 60)
    logger.info("Model preloading complete.")
    logger.info("Models cached in: ~/.paddleocr/")
    logger.info("=" * 60)


if __name__ == "__main__":
    main()
