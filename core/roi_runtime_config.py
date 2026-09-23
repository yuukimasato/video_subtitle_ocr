# core/roi_runtime_config.py
"""ROI 运行时配置的一次性解析：CLI 与 GUI 共用同一有效结果。

真实样本（V4）暴露的配置断层：CLI 显式请求的模型档位/语言没有进入运动
轨迹路径；ROI 文件顶层语言被丢弃；显式 ``--scene-text-policy overlap``
与「未传参数」无法区分，导致永远无法覆盖 ROI 文件里保存的 mask；保存的
pose 标签只有 GUI 传给生成器。本模块把「读 ROI 文件 → 解析每路径有效的
引擎选项/显示策略/姿态标签」收敛成纯函数，主入口在抽帧、静态引擎创建与
``collect_motion_roi_specs`` 之前调用一次，静态/运动共用。

约定：
- 未传的 CLI 参数用 ``None`` 区分；对外无任何配置时保持旧默认（ch / overlap）。
- 引擎选项字典必须深拷贝，逐 ROI 解析互不污染。
- 语言优先级：ROI ``ocr_lang`` > 显式全局语言 > ROI 文件顶层语言 > ``ch``。
- 显示策略优先级：显式 CLI 策略 > ROI 保存值 > ``overlap``。
"""

from __future__ import annotations

import json
import logging
from copy import deepcopy
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

DEFAULT_OCR_LANG = "ch"
DEFAULT_SCENE_TEXT_POLICY = "overlap"


@dataclass
class RoiFileConfig:
    """GUI 保存的 ROI 文件完整内容。

    ``rois`` 是逐条 ROI dict（与 GUI 内部表示一致）；``ocr_lang`` 是旧版本
    ``load_roi_file`` 丢弃掉的文件顶层识别语言（裸数组文件为 None）。
    """

    rois: List[Dict[str, Any]] = field(default_factory=list)
    ocr_lang: Optional[str] = None


def _clean_lang(value: Any) -> Optional[str]:
    """语言字段 → 去空白字符串；空/非法值归一为 None。"""
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def read_roi_config(path: str) -> RoiFileConfig:
    """读取 GUI 保存的 ROI json，返回完整配置（含顶层语言）。

    接受 ``{"rois": [...]}``（GUI 保存格式，可携带顶层 ``ocr_lang``）或裸
    ROI 数组。校验规则与旧 ``load_roi_file`` 完全一致：每条目必须是携带
    ``type``/``points`` 的对象，且至少一条；违反抛 ``ValueError``，文件不
    可读抛 ``OSError``。不改变原文件内容。
    """
    with open(path, "r", encoding="utf-8") as f:
        payload = json.load(f)
    if isinstance(payload, dict):
        entries = payload.get("rois")
        file_lang = _clean_lang(payload.get("ocr_lang"))
    elif isinstance(payload, list):
        entries = payload
        file_lang = None
    else:
        raise ValueError(
            f"ROI file {path!r}: expected {{\"rois\": [...]}} or a bare list")
    if not isinstance(entries, list):
        raise ValueError(
            f"ROI file {path!r}: expected {{\"rois\": [...]}} or a bare list")
    rois: List[Dict[str, Any]] = []
    for i, entry in enumerate(entries):
        if not isinstance(entry, dict):
            raise ValueError(f"ROI file {path!r}: entry {i} is not an object")
        if not entry.get("type") or "points" not in entry:
            raise ValueError(
                f"ROI file {path!r}: entry {i} missing 'type'/'points'")
        rois.append(entry)
    if not rois:
        raise ValueError(f"ROI file {path!r}: no ROI entries")
    return RoiFileConfig(rois=rois, ocr_lang=file_lang)


def effective_ocr_options(
    global_options: Optional[Dict[str, Any]],
    roi: Optional[Dict[str, Any]],
    *,
    file_lang: Optional[str] = None,
    cli_lang: Optional[str] = None,
) -> Dict[str, Any]:
    """合成一个 ROI 的一次有效引擎选项（深拷贝，不污染调用方）。

    ``global_options`` 是全局引擎选项（lang/model_tier 等）；本函数只按
    优先级重写 ``lang``，其余键（如 ``model_tier``）原样保留——运动路径
    因此拿到与静态路径相同的模型档位。优先级：
    ROI ``ocr_lang`` > ``cli_lang``（显式全局 --lang / GUI 全局语言）>
    ``file_lang``（ROI 文件顶层语言）> :data:`DEFAULT_OCR_LANG`。
    """
    options = deepcopy(global_options or {})
    options["lang"] = (
        _clean_lang((roi or {}).get("ocr_lang"))
        or _clean_lang(cli_lang)
        or _clean_lang(file_lang)
        or DEFAULT_OCR_LANG
    )
    return options


def resolve_roi_policies(
    rois: Optional[List[Dict[str, Any]]],
    explicit_policy: Optional[str],
) -> List[Dict[str, Any]]:
    """解析每个 ROI 的场景文字显示策略（深拷贝，不就地修改）。

    ``explicit_policy`` 非 None（用户显式传参，含显式 ``overlap``）时覆盖
    全部 ROI；为 None（未传参）时保留 ROI 保存值，缺省补
    :data:`DEFAULT_SCENE_TEXT_POLICY`。合法性由调用方的 argparse choices /
    :func:`core.pipeline_worker.collect_roi_scene_text_options` 校验。
    """
    resolved = deepcopy(list(rois or []))
    for roi in resolved:
        roi["scene_text_policy"] = (
            explicit_policy if explicit_policy is not None
            else roi.get("scene_text_policy") or DEFAULT_SCENE_TEXT_POLICY
        )
    return resolved


def collect_roi_pose_tags(
    rois: Optional[List[Dict[str, Any]]],
) -> Dict[str, Dict[str, Any]]:
    """收集 ``roi_N`` → 保存的 pose 标签（与 GUI 主流水线同一判断）。

    仅收集「写入画面位置标签」勾选且 ``pose`` 是 dict 的 ROI；其余不伪造
    合成姿态。CLI 由此把 GUI 已有的位置/旋转元数据同样传给生成器。
    """
    tags: Dict[str, Dict[str, Any]] = {}
    for idx, roi in enumerate(rois or []):
        if not isinstance(roi, dict):
            continue
        pose = roi.get("pose")
        if roi.get("write_pose_tags") and isinstance(pose, dict):
            tags[f"roi_{idx}"] = pose
    return tags


def collect_roi_auto_brightness(
    rois: Optional[List[Dict[str, Any]]],
    global_enabled: bool = False,
) -> Dict[str, bool]:
    """收集 ``roi_N`` → 是否自动亮度(逐 ROI 开关 OR 全局开关)。

    CLI 的 ``--auto-brightness`` 与 GUI 的逐 ROI「自动亮度」勾选统一在此
    合成;生成器据此决定是否为该 ROI 的静态遮罩/文字采样屏幕亮度曲线。
    """
    out: Dict[str, bool] = {}
    for idx, roi in enumerate(rois or []):
        if not isinstance(roi, dict):
            continue
        out[f"roi_{idx}"] = bool(roi.get("motion_auto_brightness", False)) \
            or bool(global_enabled)
    return out
