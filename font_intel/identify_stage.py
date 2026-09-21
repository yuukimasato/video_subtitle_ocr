# font_intel/identify_stage.py
"""``font_identify`` 主进程后置阶段（T3.3）：OCR 结果行 → 字体候选侧表。

阶段归属（架构红线 1/2，docs/development_plan_font_translation_compliance.md §3）
--------------------------------------------------------------------------------
字体识别是**本地计算**，作为主进程后置阶段执行：阶段 3（坐标还原）之后、
构造 ASS 优化器之前（``core/pipeline_worker.run`` 接线）。阶段 1–3 运行于
chunk worker 子进程、禁止服务端调用——本阶段只在主进程运行，且权重下载
（唯一的网络活动）失败可恢复、绝不阻塞。**字块图像不跨阶段携带**：阶段 4
只有文本与框坐标，字块经 ``roi_extractor.extract_single_roi_crop_with_time``
随机访问取帧重裁（边界精修同款模式），按字幕组采样、按 (roi, 稳定文本)
去重（每组只随机访问 1 帧）。

输入 / 输出
-----------
输入 restored_results 元素即 ``coordinate_restorer.restore_coordinates``
产出：``(ocr_data, frame_num, roi_id, frame_time_sec)``，其中 ``ocr_data``
带平行列表 ``rec_texts / rec_scores / rec_boxes``（框为**全视频坐标**
x1,y1,x2,y2——还原后坐标）。

输出为**侧表**（``FontIdentifyResult.identifications``）：键
``(roi_id, frame_num, 行序号)`` → 识别条目（同 (roi, 稳定文本) 组内所有
行共享同一条目）。**绝不修改 restored_results 的原有元组结构**——既有
消费方（subtitle_generator 等）零感知；字体名落 ASS 是 T3.4 的事，本阶段
只产出候选 + 许可类别。

识别链
------
1. 候选生成：``font_intel.recognizer.base.get_recognizer(cfg)``（当前
   yuzu，torch 可选；不可用时链可用项为空 → 阶段以 ``no_recognizer``
   跳过，不开视频）；
2. 本机解析：候选名经 ``fontlib_index``（T1.2 索引）解析到本机字体文件；
3. 字形重排：``glyph_rerank.rerank``（T3.2）对已解析候选按参考字形相似
   度重排——重排分即最终排序分；未解析候选保持模型序殿后（``glyph_ranked
   = False``，``score`` 退回模型分）；
4. 许可查询：候选名查 ``fonts_db``（T1.1）得 ``license_category``；
   库未收录 / 库未开为 ``None``。

降级语义（硬约束）
------------------
整段阶段任何异常降级为"无识别结果"（``FontIdentifyResult``，status=
``error``），绝不向主流水线抛异常、绝不中断出片；单组取帧/识别失败只
跳过该组。**配置关闭（默认）时零开销**：不打开视频、不 import torch
相关模块（yuzu 由 ``get_recognizer`` 延迟导入；本模块自身零 torch 依赖）。
"""

from __future__ import annotations

import logging
import os
import re
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

import cv2
import numpy as np

# 随机访问取帧（边界精修同款）；模块级引用便于测试 monkeypatch。
from core.roi_extractor import extract_single_roi_crop_with_time

logger = logging.getLogger(__name__)

# ── 常量 ─────────────────────────────────────────────────────────

# 行级 OCR 分数门槛：对齐 subtitle_generator.MIN_SCORE_THRESHOLD——只对
# 能活到出片阶段的行做字体识别。
MIN_LINE_SCORE = 0.6

# 默认识别组数上限（成本护栏；超出按首现顺序截断）。
DEFAULT_MAX_GROUPS = 128

# 阶段状态（FontIdentifyResult.status）。
STATUS_DISABLED = "disabled"        # 配置关闭（默认零开销直通）
STATUS_EMPTY = "empty"              # 无可识别行
STATUS_NO_RECOGNIZER = "no_recognizer"  # 链中无可用识别器（如 torch 缺失）
STATUS_OK = "ok"                    # 至少一组识别完成
STATUS_DEGRADED = "degraded"        # 有组但全部失败（取帧/ROI 缺失等）
STATUS_CANCELLED = "cancelled"      # 用户取消（部分结果保留）
STATUS_ERROR = "error"              # 阶段级意外异常（降级为无识别结果）

# 组条目状态（entry["status"]）。
ENTRY_OK = "ok"
ENTRY_NO_CANDIDATES = "no_candidates"

# 稳定文本归一：去全部空白 + 去尾部抖动标点（与 data_grouping 的帧分组
# 同一动机——OCR 对句尾省略号/空白的不同读法不应拆出两套识别）。
_WHITESPACE_RE = re.compile(r"\s+")
_FLICKER_PUNCT_TAIL_RE = re.compile(r"[·・.。…﹒∼~\-–—\s]+$")


def _stable_text(text: Any) -> str:
    """(roi, 文本) 分组键的稳定化文本。"""
    return _FLICKER_PUNCT_TAIL_RE.sub("", _WHITESPACE_RE.sub("", str(text or "")))


# ── 配置与结果 ───────────────────────────────────────────────────


@dataclass
class FontIdentifyConfig:
    """font_identify 后置阶段配置（``PipelineWorker.font_identify_config``）。

    ``enabled=False``（默认）时整段零开销：不打开视频、不 import torch
    相关模块。字段有 GUI/CLI 接入（T3.4/T3.5）前全部给出保守默认值。
    """

    #: 总开关（默认关闭——方案要求功能整体可勾选关闭）。
    enabled: bool = False
    #: 每组输出的候选数（Top-N）。
    top_n: int = 5
    #: 置信度阈值：最终分低于它的候选标记 ``low_confidence=True``（保留
    #: 展示、不参与后续自动替换——T3.4 合规闸门的 replace_auto 门槛）。
    #: 0 = 不标记。
    confidence_threshold: float = 0.0
    #: 识别组数上限（成本护栏；一组 = 一个 (roi, 稳定文本)）。
    max_groups: int = DEFAULT_MAX_GROUPS

    # -- 本机字体库（名称→路径解析，供字形重排） --------------------
    #: 额外扫描目录（用户指定字体库）。
    extra_font_dirs: List[str] = field(default_factory=list)
    #: 是否扫描系统字体目录（生产默认 True；测试指向临时目录时关闭）。
    scan_system_fonts: bool = True

    # -- fonts.db 许可查询 ------------------------------------------
    #: 数据库路径；None 用默认 XDG 路径（文件尚不存在时跳过查询，
    #: 避免为识别阶段凭空建库）。
    db_path: Optional[str] = None

    # -- yuzu 识别器（duck-typing 消费，见 recognizer/base.get_recognizer）
    #: yuzu 权重路径；空 = 默认缓存路径（VSO_FONT_YUZU_WEIGHTS 亦可）。
    yuzu_weights_path: str = ""
    #: yuzu 标签表路径；空 = 与权重同目录的 labels.json。
    yuzu_labels_path: str = ""
    #: 允许延迟下载权重（有界重试 + 失败可恢复，不阻塞主流水线）。
    allow_download: bool = True


@dataclass
class FontIdentifyResult:
    """font_identify 阶段产出（侧表，不改 restored_results 结构）。

    ``identifications``：键 ``(roi_id, frame_num, 行序号)`` → 条目 dict：
    ``{text, roi_id, frame_num, box, frame_time_sec, status,
    identified_fonts}``；同 (roi, 稳定文本) 组内全部行共享同一条目对象。
    ``identified_fonts`` 为按最终分降序的候选 dict 列表（≤top_n）：
    ``{name, score, model_score, glyph_score, glyph_ranked, source,
    font_path, license_category, low_confidence}``。
    """

    enabled: bool = False
    status: str = STATUS_DISABLED
    groups_total: int = 0
    groups_sampled: int = 0
    identifications: Dict[Tuple[str, int, int], Dict] = field(default_factory=dict)


# ── 组采集 ───────────────────────────────────────────────────────


def _collect_groups(restored_results) -> List[Dict[str, Any]]:
    """按 (roi_id, 稳定文本) 归组 OCR 行；首现顺序保持确定。

    组成员记录 ``(frame_num, 行序号, 视频坐标框)``；分数低于
    ``MIN_LINE_SCORE``、框非法的行不参与（与生成器消费口径一致）。
    """
    groups: "OrderedDict[Tuple[str, str], Dict[str, Any]]" = OrderedDict()
    for item in restored_results or []:
        try:
            data, frame_num, roi_id = item[0], item[1], item[2]
        except (TypeError, ValueError, IndexError):
            continue
        if not isinstance(data, dict):
            continue
        try:
            frame_num = int(frame_num)
        except (TypeError, ValueError):
            continue
        texts = data.get("rec_texts") or []
        scores = data.get("rec_scores") or []
        boxes = data.get("rec_boxes") or []
        if not (len(texts) == len(scores) == len(boxes)):
            logger.warning(
                "font_identify: 帧 %s（ROI %s）OCR 平行列表长度不一致，整帧跳过",
                frame_num, roi_id,
            )
            continue
        for idx, raw_text in enumerate(texts):
            text = str(raw_text).strip()
            if not text:
                continue
            try:
                if float(scores[idx]) < MIN_LINE_SCORE:
                    continue
            except (TypeError, ValueError):
                continue
            try:
                x1, y1, x2, y2 = (int(round(float(v))) for v in boxes[idx])
            except (TypeError, ValueError):
                continue
            if x2 <= x1 or y2 <= y1:
                continue
            key = (str(roi_id), _stable_text(text))
            group = groups.get(key)
            if group is None:
                group = {"roi_id": str(roi_id), "text": text, "members": []}
                groups[key] = group
            group["members"].append((frame_num, idx, (x1, y1, x2, y2)))
    return list(groups.values())


# ── 裁剪几何 ─────────────────────────────────────────────────────


def _resolve_roi_entry(
    roi_id: str,
    roi_data: Optional[List[Dict]],
    video_width: int,
    video_height: int,
) -> Optional[Dict[str, Any]]:
    """roi_id → ROI 条目；合并画布模式（roi_merged）合成全帧矩形。"""
    rid = str(roi_id or "")
    if rid == "roi_merged":
        if video_width > 0 and video_height > 0:
            return {"type": "rect",
                    "points": [0, 0, int(video_width), int(video_height)],
                    "full_frame": True}
        return None
    try:
        idx = int(rid.rsplit("_", 1)[1])
    except (IndexError, ValueError):
        return None
    if not isinstance(roi_data, list) or not (0 <= idx < len(roi_data)):
        return None
    entry = roi_data[idx]
    return entry if isinstance(entry, dict) else None


def _roi_crop_origin(roi_entry: Dict[str, Any]) -> Optional[Tuple[int, int]]:
    """ROI 裁剪原点（还原坐标 → 裁剪局部坐标的偏移）。

    语义与 ``coordinate_restorer._get_roi_offset`` 一致：rect 取
    clamp 后左上角；poly 取顶点 clip 后外接矩形左上角；full_frame 为
    (0, 0)。无法解析返回 None。
    """
    if roi_entry.get("full_frame"):
        return 0, 0
    rtype = roi_entry.get("type", "rect")
    points = roi_entry.get("points")
    if rtype == "rect":
        if isinstance(points, (list, tuple)) and len(points) == 4:
            return max(0, int(points[0])), max(0, int(points[1]))
        return None
    if rtype == "poly":
        try:
            pts = np.array(points, dtype=np.int32)
            if pts.ndim != 2 or pts.shape[1] != 2 or pts.shape[0] < 2:
                return None
            pts[:, 0] = np.clip(pts[:, 0], 0, None)
            pts[:, 1] = np.clip(pts[:, 1], 0, None)
            x, y, _w, _h = cv2.boundingRect(pts)
            return int(x), int(y)
        except (TypeError, ValueError, cv2.error):
            return None
    return None


def _crop_line_block(
    roi_crop: Any, box: Tuple[int, int, int, int], origin: Tuple[int, int]
) -> Optional[np.ndarray]:
    """从 ROI 裁剪中再裁出该行字块（视频坐标 → 局部坐标，带安全 padding）。"""
    if roi_crop is None or not hasattr(roi_crop, "shape"):
        return None
    h_img, w_img = roi_crop.shape[:2]
    ox, oy = origin
    x1, y1, x2, y2 = box
    rx1, ry1, rx2, ry2 = x1 - ox, y1 - oy, x2 - ox, y2 - oy
    pad = max(2, int(0.15 * max(1, ry2 - ry1)))
    rx1, ry1, rx2, ry2 = rx1 - pad, ry1 - pad, rx2 + pad, ry2 + pad
    rx1, ry1 = max(0, rx1), max(0, ry1)
    rx2, ry2 = min(w_img, rx2), min(h_img, ry2)
    if rx2 - rx1 < 2 or ry2 - ry1 < 2:
        return None
    block = roi_crop[ry1:ry2, rx1:rx2]
    return block if block.size else None


# ── 本机解析与许可查询 ───────────────────────────────────────────


def _build_font_path_map(config: FontIdentifyConfig) -> Dict[str, str]:
    """字体名（含别名，大小写不敏感）→ 本机字体文件路径。

    经 ``fontlib_index``（T1.2）扫描；依赖缺失 / 扫描失败返回空表
    （候选保留模型序，字形重排降级），绝不抛异常。
    """
    try:
        from font_intel import fontlib_index
    except Exception:  # pragma: no cover - 包内导入失败极罕见
        return {}
    if not fontlib_index.is_available():
        logger.warning(
            "fontTools/Pillow 未安装，本机字体解析不可用（候选保持模型序）")
        return {}
    extra = [str(d) for d in (getattr(config, "extra_font_dirs", None) or [])]
    records: List[Dict] = []
    try:
        if getattr(config, "scan_system_fonts", True):
            records = fontlib_index.scan_fonts(extra)
        else:
            exts = (".ttf", ".otf", ".ttc", ".otc")
            files: List[str] = []
            for d in extra:
                if not os.path.isdir(d):
                    continue
                for root, _dirs, names in os.walk(d):
                    files.extend(
                        os.path.join(root, n) for n in sorted(names)
                        if n.lower().endswith(exts))
            records = [
                rec for rec in
                (fontlib_index.extract_font_record(p) for p in sorted(files))
                if rec
            ]
    except Exception as exc:
        logger.warning("本机字体索引失败（名称→路径解析降级为空）: %s", exc)
        return {}
    mapping: Dict[str, str] = {}
    for rec in records:
        path = rec.get("file_path")
        if not path:
            continue
        names = [rec.get("canonical_name")] + list(rec.get("aliases") or [])
        for name in names:
            if name:
                mapping.setdefault(str(name).casefold(), path)
    return mapping


def _open_license_db(config: FontIdentifyConfig):
    """懒开 fonts.db；默认库文件尚不存在时跳过（不凭空建库）。失败返回 None。"""
    try:
        from font_intel import fonts_db

        path = getattr(config, "db_path", None)
        if path:
            return fonts_db.FontsDB(str(path))
        default = fonts_db.default_db_path()
        if os.path.isfile(default):
            return fonts_db.FontsDB(default)
    except Exception as exc:
        logger.warning("fonts.db 打开失败（许可查询降级为 None）: %s", exc)
    return None


# ── 阶段主体 ─────────────────────────────────────────────────────


def run_font_identify_stage(
    restored_results,
    video_path: str,
    fps: float,
    config: Optional[FontIdentifyConfig],
    *,
    roi_data: Optional[List[Dict]] = None,
    video_width: int = 0,
    video_height: int = 0,
    recognizers: Optional[List] = None,
    cancel_check=None,
) -> FontIdentifyResult:
    """font_identify 后置阶段入口：restored_results + 视频 → 字体候选侧表。

    配置关闭（``config`` 为 None 或 ``enabled=False``）时零开销直通。
    任何意外异常降级为 ``status="error"`` 的空结果，绝不抛异常。
    ``recognizers`` 供测试注入识别器替身；None 时经
    ``recognizer.base.get_recognizer(config)`` 构造默认链。
    """
    if config is None or not getattr(config, "enabled", False):
        return FontIdentifyResult(enabled=False, status=STATUS_DISABLED)
    try:
        return _run_stage(
            restored_results, video_path, fps, config,
            roi_data=roi_data, video_width=video_width,
            video_height=video_height, recognizers=recognizers,
            cancel_check=cancel_check,
        )
    except Exception as exc:
        logger.warning(
            "font_identify 阶段意外异常，降级为无识别结果（不中断出片）: %s",
            exc, exc_info=True,
        )
        return FontIdentifyResult(enabled=True, status=STATUS_ERROR)


def _run_stage(
    restored_results,
    video_path: str,
    fps: float,
    config: FontIdentifyConfig,
    *,
    roi_data: Optional[List[Dict]],
    video_width: int,
    video_height: int,
    recognizers: Optional[List],
    cancel_check,
) -> FontIdentifyResult:
    from font_intel.recognizer import base as recognizer_base  # 零 torch 依赖

    groups = _collect_groups(restored_results)
    result = FontIdentifyResult(
        enabled=True, status=STATUS_EMPTY, groups_total=len(groups))
    if not groups:
        return result

    if recognizers is None:
        # 延迟构造默认链（内部再延迟 import yuzu——torch 全可选）。
        recognizers = recognizer_base.get_recognizer(config)
    active = [
        rec for rec in (recognizers or [])
        if getattr(rec, "available", False)
        and callable(getattr(rec, "identify", None))
    ]
    if not active:
        logger.info(
            "font_identify: 无可用识别器（torch/权重缺失或链为空），阶段跳过")
        result.status = STATUS_NO_RECOGNIZER
        return result

    try:
        top_n = max(1, int(getattr(config, "top_n", 5) or 5))
    except (TypeError, ValueError):
        top_n = 5
    try:
        limit = int(getattr(config, "max_groups", DEFAULT_MAX_GROUPS))
    except (TypeError, ValueError):
        limit = DEFAULT_MAX_GROUPS
    threshold = float(getattr(config, "confidence_threshold", 0.0) or 0.0)

    from font_intel.recognizer import glyph_rerank

    font_paths: Optional[Dict[str, str]] = None  # 懒建（首组有候选时）
    license_db = None                            # 懒开（首组有候选时）
    cap = None                                   # 共享 VideoCapture（懒开）
    sampled = 0
    cancelled = False

    try:
        for group_index, group in enumerate(groups):
            if limit > 0 and group_index >= limit:
                break
            if cancel_check is not None and cancel_check():
                cancelled = True
                break

            members = sorted(group["members"], key=lambda m: (m[0], m[1]))
            # 中位帧采样：躲开淡入淡出边界的抖动读法，代表性最稳。
            frame_num, line_idx, box = members[len(members) // 2]
            roi_entry = _resolve_roi_entry(
                group["roi_id"], roi_data, video_width, video_height)
            if roi_entry is None:
                logger.warning(
                    "font_identify: 找不到 ROI 条目 %s，该组跳过", group["roi_id"])
                continue
            origin = _roi_crop_origin(roi_entry)
            if origin is None:
                logger.warning(
                    "font_identify: ROI %s 形状非法，该组跳过", group["roi_id"])
                continue

            if cap is None:
                # 共享句柄（边界精修同款）：多组采样只开一次视频。
                cap = cv2.VideoCapture(video_path)
                if not cap.isOpened():
                    # 打不开时释放并回退逐次打开（extract 内部自开）——
                    # 若视频真不可读，各组会在 extract 处自然失败降级。
                    logger.warning(
                        "font_identify: 打开视频失败，回退逐帧打开: %s", video_path)
                    cap.release()
                    cap = None

            try:
                pair = extract_single_roi_crop_with_time(
                    video_path, roi_entry, frame_num, cap=cap,
                    fps=float(fps or 0.0))
            except Exception as exc:
                logger.warning(
                    "font_identify: 取帧异常（组跳过）: roi=%s frame=%s: %s",
                    group["roi_id"], frame_num, exc,
                )
                continue
            if not pair:
                continue
            roi_crop, frame_time = pair[0], pair[1]
            line_crop = _crop_line_block(roi_crop, box, origin)
            if line_crop is None:
                logger.warning(
                    "font_identify: 行字块裁剪失败（组跳过）: roi=%s frame=%s",
                    group["roi_id"], frame_num,
                )
                continue

            text = group["text"]
            candidates = recognizer_base.collect_candidates(
                active, text, line_crop, top_n=top_n)

            entry: Dict[str, Any] = {
                "text": text,
                "roi_id": group["roi_id"],
                "frame_num": frame_num,
                "box": box,
                "frame_time_sec": float(frame_time or 0.0),
                "status": ENTRY_NO_CANDIDATES,
                "identified_fonts": [],
            }

            if candidates:
                if font_paths is None:
                    font_paths = _build_font_path_map(config)
                # 本机解析：无路径候选尽力绑定本地字体文件（供重排渲染）。
                for cand in candidates:
                    if not cand.font_path:
                        cand.font_path = font_paths.get(cand.name.casefold())
                # 字形重排（T3.2）：已解析候选按参考字形相似度重排；
                # rerank 对空路径/渲染失败条目安全降级（保序、0 分）。
                ranked = glyph_rerank.rerank(
                    text, line_crop,
                    [{"name": c.name, "font_path": c.font_path}
                     for c in candidates if c.font_path],
                )
                glyph_scores: Dict[str, float] = {}
                for ranked_item in ranked:
                    if ranked_item.per_metric:
                        glyph_scores.setdefault(
                            ranked_item.name.casefold(), float(ranked_item.score))
                fonts: List[Dict[str, Any]] = []
                for cand in candidates:
                    glyph_score = glyph_scores.get(cand.name.casefold())
                    if glyph_score is not None:
                        score, glyph_score, ranked_flag = (
                            glyph_score, glyph_score, True)
                    else:
                        # 未解析/渲染失败：退回模型置信度（殿后由排序保证）。
                        score, glyph_score, ranked_flag = (
                            float(cand.score), None, False)
                    fonts.append({
                        "name": cand.name,
                        "score": score,
                        "model_score": float(cand.score),
                        "glyph_score": glyph_score,
                        "glyph_ranked": ranked_flag,
                        "source": cand.source,
                        "font_path": cand.font_path,
                    })
                fonts.sort(key=lambda f: -f["score"])
                top = fonts[:top_n]
                # 许可查询（T1.1 库）：截断后再查，省查询次数。
                if license_db is None:
                    license_db = _open_license_db(config)
                for item_font in top:
                    record = None
                    if license_db is not None:
                        try:
                            record = license_db.lookup_font(item_font["name"])
                        except Exception:
                            record = None
                    item_font["license_category"] = (
                        record.get("license_category")
                        if isinstance(record, dict) else None)
                    item_font["low_confidence"] = bool(
                        threshold > 0 and item_font["score"] < threshold)
                entry["status"] = ENTRY_OK
                entry["identified_fonts"] = top

            # 侧表挂载：组内所有行共享同一条目（不改 restored_results）。
            for m_frame, m_idx, _m_box in members:
                result.identifications[(group["roi_id"], m_frame, m_idx)] = entry
            sampled += 1
    finally:
        if cap is not None:
            try:
                cap.release()
            except Exception:  # pragma: no cover - 释放失败无害
                pass

    result.groups_sampled = sampled
    if cancelled:
        result.status = STATUS_CANCELLED
    else:
        result.status = STATUS_OK if sampled else STATUS_DEGRADED
    logger.info(
        "font_identify: %d/%d 组识别完成（状态 %s）",
        sampled, len(groups), result.status,
    )
    return result
