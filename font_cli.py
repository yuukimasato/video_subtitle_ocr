#!/usr/bin/env python3
"""vso-font — 字体智能独立 CLI（T3.5；与现有 ``cli.py`` 完全独立）。

现有 ``cli.py`` 是"单命令 + 位置参数 video"形态（架构红线 6，方案 §3.2），
直接塞子命令会破坏既有调用兼容——字体智能的命令行走本独立入口，经
``/usr/bin/vso-font`` bash 启动器进打包清单（与 ``video-subtitle-ocr-cli``
同模式，见 docs/packaging.md）。

用法
----
::

    vso-font identify IMAGE|VIDEO [--roi x,y,w,h|auto] [--text TEXT]
                     [--top-n N] [--font-db PATH] [--font-dir DIR]
                     [--samples N] [--json OUT] [--no-download]
    vso-font index [--dir DIR] [--db PATH]
    vso-font review import PACKAGE.json [--db PATH]

- ``identify``：图片直接识别；视频 + ``--roi`` 等间隔采样 ``--samples`` 帧
  裁块识别（``--roi`` 省略或 ``auto`` = 全帧）。识别链与主流水线
  ``font_identify`` 阶段同源：yuzu 候选生成（torch 可选）→ 字形重排裁决
  （需 ``--text`` 已知文本 + fontTools/Pillow）→ fonts.db 许可查询。
  无 torch 时降级为"仅字形重排（需 --text）"；两者皆不可用则报可恢复
  错误（退出码 3），提示安装可选依赖（requirements-fontintel.txt）。
- ``index``：扫描系统 + ``--dir``（重复给出）字体目录，经 fontTools 提取
  元数据建/更新 fonts.db 种子层（幂等，可重复执行）。
- ``review import``：导入 T1.5 复核包（``vso_font_map_review/1``）/ T3.4
  更新建议包（``vso_font_update_suggestions/1``）/ T1.5 决策包
  （``vso_font_map_review_decisions/1``），采纳语义与 GUI 复核对话框一致
  （带建议值的条目逐条入库，``method=human`` 人工确认语义）；命令本身即
  显式人工动作。

硬红线（与 GUI/compliance 一致）：输出只含字体名/分数/许可类别/来源与
库内官方链接，绝不提供、不链接任何破解渠道字体。

退出码：``0`` 成功；``1`` 一般错误（输入不存在/schema 不符/写库失败）；
``2`` 参数错误（argparse）；``3`` 缺可选依赖（可恢复——按提示安装或改用
降级路径）。
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys

PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

#: 独立入口版本号（--version 惰性读取 cli.__version__，失败时回退本值）。
__version__ = "1.0"

EXIT_OK = 0
EXIT_ERROR = 1
EXIT_MISSING_DEPS = 3

#: identify 结果 JSON schema（导出可追溯）。
IDENTIFY_SCHEMA = "vso_font_identify_result/1"

_VIDEO_EXTS = {
    ".mp4", ".avi", ".mkv", ".mov", ".webm", ".m4v", ".ts", ".flv", ".wmv",
}
_ROI_RE = re.compile(r"^\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*$")


def _err(msg: str) -> None:
    print(f"vso-font: {msg}", file=sys.stderr, flush=True)


def _parse_roi(spec: str | None) -> tuple[int, int, int, int] | None:
    """``"x,y,w,h"`` → (x, y, w, h)；``None``/``"auto"``/空 → None（全帧）。"""
    if spec is None or not str(spec).strip() or str(spec).strip().lower() == "auto":
        return None
    m = _ROI_RE.match(str(spec))
    if not m:
        raise ValueError(
            f"Invalid ROI spec: {spec!r} (expected x,y,w,h or 'auto')")
    x, y, w, h = (int(g) for g in m.group(1, 2, 3, 4))
    if w <= 0 or h <= 0:
        raise ValueError(f"ROI width/height must be positive: {spec!r}")
    return x, y, w, h


def _crop_frame(frame, roi):
    """按 (x, y, w, h) 裁帧（带边界钳制）；roi=None 原帧返回。"""
    if roi is None:
        return frame
    x, y, w, h = roi
    h_img, w_img = frame.shape[:2]
    x1, y1 = max(0, x), max(0, y)
    x2, y2 = min(w_img, x + w), min(h_img, y + h)
    if x2 - x1 < 2 or y2 - y1 < 2:
        raise ValueError(f"ROI {roi} outside frame bounds {w_img}x{h_img}")
    return frame[y1:y2, x1:x2]


def _is_video(path: str) -> bool:
    return os.path.splitext(path)[1].lower() in _VIDEO_EXTS


def _load_image_crops(path: str, roi, samples: int) -> list:
    """输入路径 → 裁剪后字块图像列表（图片 1 张；视频等间隔采样）。"""
    import cv2

    if not _is_video(path):
        img = cv2.imread(path, cv2.IMREAD_COLOR)
        if img is None:
            raise FileNotFoundError(f"cannot read image: {path}")
        return [_crop_frame(img, roi)]
    return _sample_video_crops(path, roi, samples)


def _sample_video_crops(path: str, roi, samples: int) -> list:
    """视频等间隔采样 ``samples`` 帧并裁 ROI（首中尾优先覆盖）。"""
    import cv2

    cap = cv2.VideoCapture(path)
    if not cap.isOpened():
        raise FileNotFoundError(f"cannot open video: {path}")
    crops: list = []
    try:
        total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        wanted = min(max(1, samples), total) if total > 0 else max(1, samples)
        if total > 0:
            if wanted == 1:
                indices = [total // 2]
            else:
                step = (total - 1) / (wanted - 1)
                indices = sorted({round(i * step) for i in range(wanted)})
        else:
            indices = None  # 帧数未知（流/损坏元数据）：顺序读前 N 帧。
        picked = 0
        while True:
            if indices is not None:
                if picked >= len(indices):
                    break
                cap.set(cv2.CAP_PROP_POS_FRAMES, indices[picked])
            ok, frame = cap.read()
            if not ok:
                break
            crops.append(_crop_frame(frame, roi))
            picked += 1
            if indices is None and picked >= wanted:
                break
    finally:
        cap.release()
    if not crops:
        raise FileNotFoundError(f"no decodable frame in video: {path}")
    return crops


def _scan_font_records(extra_dirs: list[str], restrict: bool) -> list[dict]:
    """本机字体记录扫描。

    ``restrict=True``（给了 ``--font-dir`` 的字形重排候选池）：只在给定
    目录内递归收集；``False``：系统目录 + 额外目录（模型候选的本机解析
    与默认建库口径一致）。
    """
    from font_intel import fontlib_index

    if not restrict:
        return [
            r for r in fontlib_index.scan_fonts(extra_dirs) if r
        ]
    records: list[dict] = []
    for d in extra_dirs:
        if not os.path.isdir(d):
            continue
        for root, _dirs, names in os.walk(d):
            for name in sorted(names):
                if name.lower().endswith((".ttf", ".otf", ".ttc", ".otc")):
                    rec = fontlib_index.extract_font_record(
                        os.path.join(root, name))
                    if rec:
                        records.append(rec)
    return records


def _name_path_pairs(records: list[dict]) -> list[tuple[str, str]]:
    """记录 → (名称, 路径) 对（规范名 + 别名，大小写去重保序）。

    供模型候选的名称→路径解析（别名也要能解析）；字形重排候选池另用
    :func:`_canonical_pairs`（每字体一条，避免别名变体挤占 Top-N 槽位）。
    """
    pairs: list[tuple[str, str]] = []
    seen: set = set()
    for rec in records:
        path = rec.get("file_path")
        if not path:
            continue
        names = [rec.get("canonical_name")] + list(rec.get("aliases") or [])
        for name in names:
            if not name:
                continue
            key = str(name).casefold()
            if key in seen:
                continue
            seen.add(key)
            pairs.append((str(name), str(path)))
    return pairs


def _canonical_pairs(records: list[dict]) -> list[tuple[str, str]]:
    """记录 → (规范名, 路径) 对：**每字体一条**（同一字体的别名不重复
    进字形重排候选池——Top-N 槽位应留给不同字体，而非同一字体的别名）。"""
    pairs: list[tuple[str, str]] = []
    seen: set = set()
    for rec in records:
        name = rec.get("canonical_name")
        path = rec.get("file_path")
        if not name or not path:
            continue
        key = str(path).casefold()
        if key in seen:
            continue
        seen.add(key)
        pairs.append((str(name), str(path)))
    return pairs


def _open_license_db(db_arg: str):
    """``--font-db`` → FontsDB；留空时默认库文件存在才开（不凭空建库）。"""
    from font_intel.fonts_db import FontsDB, default_db_path

    path = (db_arg or "").strip()
    if not path:
        path = default_db_path()
        if not os.path.isfile(path):
            return None
    return FontsDB(path)


def _license_fields(db, name: str) -> tuple[str | None, str]:
    """许可类别 + 官方链接（经 compliance.sanitize_url 白名单；库外为空）。"""
    if db is None:
        return None, ""
    try:
        info = db.lookup_font(name)
    except Exception:
        info = None
    if not info:
        return None, ""
    from font_intel.compliance import sanitize_url

    category = info.get("license_category") or "unknown"
    return category, sanitize_url(info.get("official_url")) or ""


def _identify(
    crops: list,
    *,
    text: str,
    top_n: int,
    extra_dirs: list[str],
    restrict_scan: bool,
    db,
    cfg,
) -> dict:
    """识别链主体（与 identify_stage 同源：模型候选 → 本机解析 → 字形重排）。

    返回结果 dict（不含输入元数据）；**不抛异常**——识别中的单块失败
    跳过，依赖缺失按可恢复错误语义返回（``_diagnose`` 由调用方先行判定）。
    """
    from font_intel.recognizer import base as recognizer_base
    from font_intel.recognizer import glyph_rerank

    recognizers = recognizer_base.get_recognizer(cfg)
    active = [
        r for r in recognizers
        if getattr(r, "available", False)
        and callable(getattr(r, "identify", None))
    ]
    glyph_ok = bool(
        text and fontlib_ok() and glyph_rerank.is_comparison_available())

    empty = {
        "schema": IDENTIFY_SCHEMA,
        "frames_sampled": len(crops),
        "model_available": bool(active),
        "glyph_rerank": glyph_ok,
        "candidates": [],
    }
    if not active and not glyph_ok:
        # 双链皆不可用：调用方按 _diagnose_identify 给可恢复错误，
        # 这里直接返回空结果（不扫描字体目录，零副作用）。
        return empty

    records = _scan_font_records(extra_dirs, restrict_scan)
    pairs = _name_path_pairs(records)
    path_map = {name.casefold(): path for name, path in pairs}
    # 字形重排候选池：每字体一条（规范名），别名不重复占位。
    glyph_pool = _canonical_pairs(records)

    best: dict[str, dict] = {}

    def _accum(entry: dict) -> None:
        key = entry["name"].casefold()
        prev = best.get(key)
        if prev is None or entry["score"] > prev["score"]:
            best[key] = entry

    for crop in crops:
        if active:
            cands = recognizer_base.collect_candidates(
                active, text, crop, top_n=top_n)
            for cand in cands:
                if not cand.font_path:
                    cand.font_path = path_map.get(cand.name.casefold())
            entries = [
                {
                    "name": c.name, "score": float(c.score),
                    "model_score": float(c.score), "glyph_score": None,
                    "glyph_ranked": False, "source": c.source,
                    "font_path": c.font_path,
                }
                for c in cands
            ]
            if glyph_ok:
                ranked = glyph_rerank.rerank(
                    text, crop,
                    [{"name": e["name"], "font_path": e["font_path"]}
                     for e in entries if e["font_path"]],
                )
                glyph_scores = {
                    r.name.casefold(): float(r.score)
                    for r in ranked if r.per_metric
                }
                for e in entries:
                    g = glyph_scores.get(e["name"].casefold())
                    if g is not None:
                        e["score"] = e["glyph_score"] = g
                        e["glyph_ranked"] = True
            for e in entries:
                _accum(e)
        elif glyph_ok:
            # 无 torch 降级：仅字形重排——对本机候选池直接打分排序。
            ranked = glyph_rerank.rerank(
                text, crop,
                [{"name": name, "font_path": path} for name, path in glyph_pool],
            )
            for r in ranked:
                path = path_map.get(r.name.casefold(), "")
                _accum({
                    "name": r.name, "score": float(r.score),
                    "model_score": None, "glyph_score": float(r.score),
                    "glyph_ranked": True, "source": "glyph_rerank",
                    "font_path": path,
                })

    candidates = sorted(best.values(), key=lambda e: -e["score"])[:top_n]
    for cand in candidates:
        category, official_url = _license_fields(db, cand["name"])
        cand["license_category"] = category
        cand["official_url"] = official_url
        cand.pop("font_path", None)  # 本机路径不进输出（库内字段为准）

    return {
        "schema": IDENTIFY_SCHEMA,
        "frames_sampled": len(crops),
        "model_available": bool(active),
        "glyph_rerank": glyph_ok,
        "candidates": candidates,
    }


def fontlib_ok() -> bool:
    try:
        from font_intel import fontlib_index
        return fontlib_index.is_available()
    except Exception:
        return False


def _diagnose_identify(text: str) -> str | None:
    """模型链与字形重排都不可用时的可恢复诊断（返回 None = 可继续）。

    由调用方在拿到 ``_identify`` 结果后依据 ``model_available`` /
    ``glyph_rerank`` 两个事实标志调用——不做事前猜测。
    """
    hints: list[str] = []
    from font_intel.recognizer.yuzu import _torch_available

    if not _torch_available():
        if not text:
            hints.append(
                "深度识别依赖 torch（可选依赖，本机未安装）。若已知字幕文本，"
                "可用 --text 走纯本地字形重排降级路径：vso-font identify "
                "IMAGE --text <字幕文本>")
        if not fontlib_ok():
            hints.append(
                "字形重排需要 fontTools/Pillow：pip install -r "
                "requirements-fontintel.txt")
    else:
        hints.append(
            "识别器链不可用（yuzu 权重缺失且下载被禁用？）。检查权重路径或"
            "去掉 --no-download；已知文本时可加 --text 启用字形重排")
    return "；".join(hints) if hints else None


# ── 子命令 ───────────────────────────────────────────────────────


def cmd_identify(args: argparse.Namespace) -> int:
    try:
        roi = _parse_roi(args.roi)
    except ValueError as exc:
        _err(str(exc))
        return EXIT_ERROR

    text = str(args.text or "").strip()
    samples = max(1, int(args.samples))
    try:
        crops = _load_image_crops(args.input, roi, samples)
    except (FileNotFoundError, ValueError) as exc:
        _err(str(exc))
        return EXIT_ERROR

    try:
        from font_intel.identify_stage import FontIdentifyConfig

        extra_dirs = [d for d in (args.font_dir or "").split(os.pathsep) if d]
        top_n = max(1, int(args.top_n))
        cfg = FontIdentifyConfig(
            enabled=True,
            top_n=top_n,
            extra_font_dirs=extra_dirs,
            db_path=(args.font_db or "").strip() or None,
            allow_download=not args.no_download,
        )
        db = _open_license_db(args.font_db)
        try:
            result = _identify(
                crops, text=text, top_n=top_n,
                extra_dirs=extra_dirs,
                restrict_scan=bool(extra_dirs),
                db=db, cfg=cfg,
            )
        finally:
            if db is not None:
                db.close()
    except Exception as exc:
        _err(f"identify failed: {exc}")
        return EXIT_ERROR

    # 双链皆不可用 → 可恢复错误：退出码非 0，提示安装可选依赖或改走
    # 降级路径（未产生任何写副作用）。
    if not result["model_available"] and not result["glyph_rerank"]:
        notice = _diagnose_identify(text)
        _err(f"{notice}（本错误可恢复：按提示安装可选依赖或补齐参数后重试；"
             f"本次未写入任何文件）")
        return EXIT_MISSING_DEPS

    result.update({
        "input": args.input,
        "input_type": "video" if _is_video(args.input) else "image",
        "roi": list(roi) if roi else None,
        "text": text or None,
    })

    out_path = (args.json or "").strip()
    if out_path:
        with open(out_path, "w", encoding="utf-8") as fh:
            json.dump(result, fh, ensure_ascii=False, indent=2)
            fh.write("\n")

    # stdout 人读输出：Top-N 候选（名称/分数/许可类别/来源；只出库内
    # 官方链接或空，绝不出现任何破解渠道——合规硬红线）。
    print(
        f"vso-font identify: {args.input} "
        f"({result['input_type']}, {result['frames_sampled']} frame(s) sampled)"
    )
    if not result["candidates"]:
        print("  (no candidates)")
    for i, cand in enumerate(result["candidates"], 1):
        license_text = cand["license_category"] or "unknown"
        line = (
            f" {i}. {cand['name']}  score={cand['score']:.3f}"
            f"  license={license_text}  source={cand['source']}"
        )
        if cand["official_url"]:
            line += f"  official={cand['official_url']}"
        print(line)
    if out_path:
        print(f"JSON written: {out_path}")
    return EXIT_OK


def cmd_index(args: argparse.Namespace) -> int:
    try:
        from font_intel import fontlib_index
        from font_intel.fonts_db import FontsDB

        if not fontlib_index.is_available():
            _err(
                "fontTools/Pillow 未安装（可选依赖）。安装：pip install -r "
                "requirements-fontintel.txt（该错误可恢复，本命令未产生副作用）"
            )
            return EXIT_MISSING_DEPS
        extra_dirs = [d for d in (args.dir or "").split(os.pathsep) if d]
        db = FontsDB((args.db or "").strip() or None)
        try:
            stats = fontlib_index.build_index(db, extra_dirs=extra_dirs)
        finally:
            db.close()
    except Exception as exc:
        _err(f"index failed: {exc}")
        return EXIT_ERROR
    print(json.dumps(stats, ensure_ascii=False))
    return EXIT_OK


_REVIEW_HANDLERS = {
    "vso_font_map_review/1": "_import_map_review",
    "vso_font_map_review_decisions/1": "_import_map_decisions",
    "vso_font_update_suggestions/1": "_import_update_suggestions",
}


def cmd_review(args: argparse.Namespace) -> int:
    if args.review_command != "import":
        _err(f"unknown review subcommand: {args.review_command!r}")
        return EXIT_ERROR
    try:
        with open(args.package, encoding="utf-8") as fh:
            payload = json.load(fh)
    except (OSError, json.JSONDecodeError) as exc:
        _err(f"cannot read package {args.package!r}: {exc}")
        return EXIT_ERROR
    schema = payload.get("schema") if isinstance(payload, dict) else None
    handler = _REVIEW_HANDLERS.get(schema)
    if handler is None:
        _err(
            f"unexpected schema in {args.package!r}: {schema!r} "
            f"(expected one of {sorted(_REVIEW_HANDLERS)})"
        )
        return EXIT_ERROR

    items = [i for i in (payload.get("items") or []) if isinstance(i, dict)]
    if schema == "vso_font_map_review_decisions/1":
        # 决策包自带 adopted/rejected（与 review.decisions_package 同构）。
        decisions = {
            "adopted": list(payload.get("adopted") or []),
            "rejected": list(payload.get("rejected") or []),
        }
    else:
        # 与 GUI 复核对话框一致：带建议值的条目可采纳；命令本身即显式
        # 人工确认动作，故默认采纳全部 actionable 条目，其余否决留痕。
        decisions = {
            "adopted": [i for i in items if i.get("suggestion")],
            "rejected": [i for i in items if not i.get("suggestion")],
        }

    try:
        from font_intel.etl import db_loader
        from font_intel.fonts_db import FontsDB

        db = FontsDB((args.db or "").strip() or None)
        try:
            if schema == "vso_font_update_suggestions/1":
                stats = db_loader.apply_font_update_decisions(db, decisions)
            else:
                stats = db_loader.apply_review_decisions(db, decisions)
        finally:
            db.close()
    except Exception as exc:
        _err(f"review import failed: {exc}")
        return EXIT_ERROR

    print(json.dumps({
        "package": args.package,
        "schema": schema,
        "adopted_items": stats.get("adopted_items", 0),
        "written": stats.get("written", 0),
        "rejected": len(stats.get("rejected") or []),
    }, ensure_ascii=False))
    return EXIT_OK


# ── argparse 骨架 ────────────────────────────────────────────────


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="vso-font",
        description=(
            "Font intelligence CLI: identify fonts in images/videos, index "
            "local font libraries, and import review/suggestion packages. "
            "Independent entry — the main `video-subtitle-ocr-cli` command "
            "is untouched."
        ),
    )
    parser.add_argument(
        "--version", action="version",
        version=_version_text(),
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_ident = sub.add_parser(
        "identify",
        help="identify fonts in an image, or in a video via ROI sampling",
    )
    p_ident.add_argument("input", metavar="IMAGE|VIDEO",
                         help="image file or video file")
    p_ident.add_argument(
        "--roi", default="", metavar="x,y,w,h|auto",
        help="crop region (pixels). 'auto' or omitted = full frame; for "
             "videos the crop is sampled per frame",
    )
    p_ident.add_argument(
        "--text", default="", metavar="TEXT",
        help="known subtitle text — enables the glyph-rerank path (works "
             "without torch; strongly recommended)",
    )
    p_ident.add_argument(
        "--top-n", type=int, default=5, metavar="N",
        help="number of candidates to output (default: 5)",
    )
    p_ident.add_argument(
        "--font-db", default="", metavar="PATH",
        help="fonts.db path for license lookups (default: XDG data dir; "
             "missing default db = license reported as unknown)",
    )
    p_ident.add_argument(
        "--font-dir", default="", metavar="DIR",
        help="extra font directory; when given, glyph-rerank candidates "
             "are restricted to it (path-sep-separated list allowed)",
    )
    p_ident.add_argument(
        "--samples", type=int, default=5, metavar="N",
        help="frames sampled evenly across a video input (default: 5)",
    )
    p_ident.add_argument(
        "--json", default="", metavar="OUT",
        help="also write the full machine-readable result JSON to OUT",
    )
    p_ident.add_argument(
        "--no-download", action="store_true",
        help="never download yuzu weights (offline mode; unavailable "
             "weights degrade to the glyph-rerank path)",
    )
    p_ident.set_defaults(func=cmd_identify)

    p_index = sub.add_parser(
        "index",
        help="build/update the local font library index in fonts.db",
    )
    p_index.add_argument(
        "--dir", default="", metavar="DIR",
        help="extra font directory to scan (path-sep-separated list "
             "allowed; system font directories are always scanned)",
    )
    p_index.add_argument(
        "--db", default="", metavar="PATH",
        help="fonts.db path (default: XDG data dir)",
    )
    p_index.set_defaults(func=cmd_index)

    p_review = sub.add_parser(
        "review",
        help="import font review / update-suggestion packages into fonts.db",
    )
    review_sub = p_review.add_subparsers(dest="review_command", required=True)
    p_import = review_sub.add_parser(
        "import",
        help="import a review package (vso_font_map_review/1), a decisions "
             "package (vso_font_map_review_decisions/1) or an update "
             "suggestions package (vso_font_update_suggestions/1)",
    )
    p_import.add_argument("package", metavar="PACKAGE.json",
                          help="package JSON produced by the ETL/pipeline")
    p_import.add_argument(
        "--db", default="", metavar="PATH",
        help="fonts.db path (default: XDG data dir)",
    )
    p_import.set_defaults(func=cmd_review)
    return parser


def _version_text() -> str:
    try:
        from cli import __version__ as main_version

        return f"vso-font {__version__} (video-subtitle-ocr {main_version})"
    except Exception:
        return f"vso-font {__version__}"


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    sys.exit(main())
