# core/vlm_refine.py
"""VLM 难帧兜底：把 OCR 投票不一致/低置信度的字幕帧交给视觉大模型裁决。

Backlog 项「难帧 VLM 兜底」的实现。配置全部来自环境变量：

- ``VLM_REFINE_BASE_URL``：OpenAI 兼容 API base URL（如 vLLM 部署的
  ``http://host:8000/v1``）。
- ``VLM_REFINE_API_KEY``：API Key。
- ``VLM_REFINE_MODEL``：模型名，默认 ``"HunyuanOCR"``（与 vLLM 部署的
  HunyuanOCR 及任何 OpenAI 兼容视觉端点兼容）。

所有请求经 ``core.llm_client.call_llm`` 发出，自动获得有界退避
（429 Too Many Requests 最多重试 10 次 / 总等待 300s，耗尽后优雅降级）。
本模块的任何失败都只返回 ``None``，由调用方保留原 OCR 结果。
"""

from __future__ import annotations

import base64
import json
import logging
import os
import re
from typing import Any, List, Optional

import cv2
import numpy as np

from core import llm_client

logger = logging.getLogger(__name__)

try:
    import json_repair
except ImportError:  # pragma: no cover - json_repair is a declared dependency
    json_repair = None

DEFAULT_VLM_MODEL = "HunyuanOCR"

_SYSTEM_PROMPT = (
    "你是视频字幕校对助手。用户会给出同一字幕画面的若干帧图片，"
    "以及每行字幕的多个 OCR 候选文本。"
    "请对照图片从候选中选出正确字幕文本；若候选全部错误，则按图片内容修正。"
    "必须保持行数与顺序不变，只返回一个 JSON 字符串数组，不要输出任何其他内容。"
)

_JPEG_IMWRITE_PARAMS = [int(cv2.IMWRITE_JPEG_QUALITY), 90]


def is_configured() -> bool:
    """是否已配置 VLM 兜底端点（两个环境变量均非空）。"""
    return bool(os.getenv("VLM_REFINE_BASE_URL", "").strip()) and bool(
        os.getenv("VLM_REFINE_API_KEY", "").strip()
    )


def _encode_image_jpeg(image: np.ndarray) -> Optional[str]:
    """BGR 图转 JPEG base64 data URL；编码失败返回 None。"""
    if image is None or not isinstance(image, np.ndarray) or image.size == 0:
        return None
    ok, buf = cv2.imencode(".jpg", image, _JPEG_IMWRITE_PARAMS)
    if not ok:
        return None
    return "data:image/jpeg;base64," + base64.b64encode(buf.tobytes()).decode("ascii")


def _strip_code_fence(raw: str) -> str:
    s = raw.strip()
    if s.startswith("```"):
        s = re.sub(r"^```(?:json)?\s*", "", s, flags=re.IGNORECASE)
        s = re.sub(r"\s*```\s*$", "", s)
    return s.strip()


def _parse_text_array(raw: str) -> Optional[List[str]]:
    """把模型输出解析为非空字符串数组；任何不符合预期的情况返回 None。"""
    s = _strip_code_fence(str(raw))
    data: Any = None
    try:
        data = json.loads(s)
    except (ValueError, TypeError):
        if json_repair is None:
            return None
        try:
            data = json_repair.loads(s)
        except Exception:
            return None
    if not isinstance(data, list):
        return None
    out: List[str] = []
    for item in data:
        if not isinstance(item, str):
            return None
        text = item.strip()
        if not text:  # 合理性检查：每行必须是非空字符串
            return None
        out.append(text)
    return out


def refine_subtitle_text(
    images: List[np.ndarray], candidates: List[List[str]]
) -> Optional[List[str]]:
    """用视觉大模型校正难帧的 OCR 文本。

    Args:
        images: 同一字幕序列的首/中/尾帧 BGR 图。
        candidates: 每行字幕的多个 OCR 候选文本（外层长度 = 行数）。

    Returns:
        与 ``candidates`` 行数等长的最终文本列表；任何失败返回 ``None``
        （调用方保留原 OCR 结果）。
    """
    try:
        return _refine_impl(images, candidates)
    except Exception:
        logger.warning("VLM subtitle refinement failed; keeping original OCR result.", exc_info=True)
        return None


def _refine_impl(
    images: List[np.ndarray], candidates: List[List[str]]
) -> Optional[List[str]]:
    if not images or not candidates:
        return None

    lines_desc = "\n".join(
        "{}. {}".format(i + 1, json.dumps(cands or [], ensure_ascii=False))
        for i, cands in enumerate(candidates)
    )
    user_text = (
        "画面共有 {} 行字幕，每行的 OCR 候选文本如下：\n{}\n"
        "请给出每行的最终字幕文本，只返回 JSON 字符串数组。".format(len(candidates), lines_desc)
    )

    content: List[dict] = [{"type": "text", "text": user_text}]
    for image in images:
        data_url = _encode_image_jpeg(image)
        if data_url is None:
            return None
        content.append({"type": "image_url", "image_url": {"url": data_url}})

    messages = [
        {"role": "system", "content": _SYSTEM_PROMPT},
        {"role": "user", "content": content},
    ]

    model = os.getenv("VLM_REFINE_MODEL", "").strip() or DEFAULT_VLM_MODEL
    response = llm_client.call_llm(messages=messages, model=model, temperature=0)
    raw = str(response.choices[0].message.content)

    parsed = _parse_text_array(raw)
    if parsed is None or len(parsed) != len(candidates):
        return None
    return parsed
