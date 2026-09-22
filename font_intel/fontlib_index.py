# font_intel/fontlib_index.py
"""本机字体库扫描索引（T1.2）：目录扫描 + fontTools 元数据提取 + 参考字形缓存。

扫描 Linux 常见字体目录（系统级 + 用户级）中的 .ttf/.otf/.ttc/.otc，用
fontTools 读 name/OS-2 表产出字体情报记录（字段对齐 ``fonts_db`` 的字体
记录），经 ``FontsDB.import_seed`` 分批导入 seed 层（复用其幂等语义）；
Pillow 渲染常用字参考字形，``FontGlyphCache`` 按 LRU 缓存（仅内存，
不落盘）。

可选依赖模式
-------------
fontTools 与 Pillow 是**可选依赖**（见 ``requirements-fontintel.txt``）：
未安装时 ``is_available()`` 返回 False，对外入口一律优雅降级（返回空
结果 + warning），绝不抛 ImportError——font_intel 其余部分不受影响。

硬约束
------
- 索引只提取**元数据**（含本机 file_path 仅供本机匹配用），字体文件
  本体不入库、不复制、不分发。
- 许可判定**默认从严**：license 嗅探只认 OFL/Apache/MIT/GPL；有 license
  文本但认不出、或完全无许可信息，一律 ``unknown``，绝不猜
  ``free_commercial``。
- official_url 必经 ``font_intel.compliance.sanitize_url`` 消毒，命中
  盗版/破解标记的链接直接丢弃。
- 语言覆盖检测属后续任务，本任务 ``languages`` 固定为 ``[]``。
"""

from __future__ import annotations

import logging
import os
import re
from collections import OrderedDict
from typing import TYPE_CHECKING, Optional

from font_intel.compliance import sanitize_url
from font_intel.fonts_db import FontsDB

if TYPE_CHECKING:
    from PIL.Image import Image

logger = logging.getLogger(__name__)

# ── 可选依赖（import 失败不阻断本模块加载） ──────────────────────

try:
    from fontTools.ttLib import TTFont
    _FONTTOOLS_AVAILABLE = True
except ImportError:  # pragma: no cover - 取决于环境
    TTFont = None
    _FONTTOOLS_AVAILABLE = False

try:
    from PIL import Image, ImageDraw, ImageFont
    _PIL_AVAILABLE = True
except ImportError:  # pragma: no cover - 取决于环境
    Image = None
    ImageDraw = None
    ImageFont = None
    _PIL_AVAILABLE = False


def is_available() -> bool:
    """fontTools 与 Pillow 均可用时返回 True（本模块功能的启用开关）。"""
    return _FONTTOOLS_AVAILABLE and _PIL_AVAILABLE


# ── 常量 ─────────────────────────────────────────────────────────

# 大小写不敏感后缀匹配；.ttc/.otc 容器取首个 face。
_FONT_EXTENSIONS = (".ttf", ".otf", ".ttc", ".otc")

# seed 层单批导入条数（fonts + aliases 合计）。
IMPORT_BATCH_SIZE = 200

# nameID：16/17 是 Typographic family/subfamily，优先于 1/2。
_NAME_ID_TYPO_FAMILY = 16
_NAME_ID_TYPO_SUBFAMILY = 17
_NAME_ID_FAMILY = 1
_NAME_ID_SUBFAMILY = 2
_NAME_ID_FULL_NAME = 4
_NAME_ID_POSTSCRIPT = 6
_NAME_ID_MANUFACTURER = 8
_NAME_ID_DESIGNER = 9
_NAME_ID_LICENSE_TEXT = 13
_NAME_ID_LICENSE_URL = 14

# license 文本嗅探（从严：只认以下四种，其余一律 unknown）。
_OFL_RE = re.compile(r"\bOFL\b", re.IGNORECASE)
_MIT_RE = re.compile(r"\bMIT\b")
_GPL_RE = re.compile(r"\bGPL\b")

# 单字体别名上限（含本地化名；防病态 name 表撑爆 aliases 表）。
_MAX_ALIASES = 24

SOURCE = "local_index"


# ── 目录扫描 ─────────────────────────────────────────────────────

def default_font_dirs() -> list[str]:
    """Linux 常见字体目录（系统级 + 用户级），只返回实际存在的目录。"""
    home = os.path.expanduser("~")
    candidates = (
        "/usr/share/fonts",
        "/usr/local/share/fonts",
        os.path.join(home, ".local", "share", "fonts"),
        os.path.join(home, ".fonts"),
    )
    return [d for d in candidates if os.path.isdir(d)]


def _iter_font_files(dirs: list[str], recursive: bool) -> list[str]:
    """收集目录下的字体文件路径（排序保证结果确定；坏目录直接忽略）。"""
    files: list[str] = []
    for d in dirs:
        if not os.path.isdir(d):
            continue
        if recursive:
            for root, _dirnames, filenames in os.walk(d):
                for fn in filenames:
                    if fn.lower().endswith(_FONT_EXTENSIONS):
                        files.append(os.path.join(root, fn))
        else:
            for fn in os.listdir(d):
                path = os.path.join(d, fn)
                if os.path.isfile(path) and fn.lower().endswith(_FONT_EXTENSIONS):
                    files.append(path)
    return sorted(files)


def scan_fonts(extra_dirs: list[str] = [], recursive: bool = True) -> list[dict]:
    """扫描 默认目录 + extra_dirs，返回成功的索引记录列表。

    单文件失败（无读权限/损坏）warning 后继续，不中断扫描；依赖缺失
    时优雅降级返回 []。
    """
    if not is_available():
        logger.warning(
            "fontTools/Pillow 未安装，本机字体扫描不可用（优雅降级返回空列表）；"
            "可选安装: pip install -r requirements-fontintel.txt"
        )
        return []
    dirs = default_font_dirs() + [str(d) for d in (extra_dirs or [])]
    records: list[dict] = []
    for path in _iter_font_files(dirs, recursive):
        if not os.access(path, os.R_OK):
            logger.warning("字体文件无读权限，已跳过: %s", path)
            continue
        rec = extract_font_record(path)
        if rec is not None:
            records.append(rec)
    return records


# ── 元数据提取 ───────────────────────────────────────────────────

def _open_ttfont(path: str):
    # TTC/OTC 容器取首个 face（fontNumber=0）；对普通 TTF/OTF 无副作用。
    return TTFont(str(path), fontNumber=0, lazy=True)


def _name_str(name_table, name_id: int) -> Optional[str]:
    try:
        value = name_table.getDebugName(name_id)
    except Exception:
        return None
    if value is None:
        return None
    value = str(value).strip()
    return value or None


def _localized_family_names(name_table, name_ids, exclude: set) -> list[str]:
    """跨平台/语言收集 family 名（别名用）。

    ``getDebugName`` 只返回首个命中记录（通常英文），而 ASS 样式、中日
    映射表引用的是**本地化名**（思源黑体 CN / 源ノ角ゴシック JP，Windows
    平台 langID 0x0804/0x0411 等）。这里遍历 name 表全部记录，取 family
    相关 nameID 的各语言写法，供 lookup 经 aliases 命中。
    """
    names: list[str] = []
    try:
        records = list(name_table.names)
    except Exception:
        return names
    for rec in records:
        try:
            if getattr(rec, "nameID", None) not in name_ids:
                continue
            value = rec.toUnicode().strip()
        except Exception:
            continue
        if not value or value in exclude:
            continue
        if value in names:
            continue
        names.append(value)
    return names


def sniff_license(license_text: Optional[str]) -> tuple[str, str]:
    """从 license 文本嗅探 (license_name, license_category)。

    从严约束：只认 OFL/Apache/MIT/GPL（均记 open_source）；有文本但
    认不出、或无文本，一律 unknown——绝不猜 free_commercial。
    """
    if not license_text or not str(license_text).strip():
        return "", "unknown"
    lowered = str(license_text).lower()
    if "sil open font license" in lowered or _OFL_RE.search(str(license_text)):
        return "OFL-1.1", "open_source"
    if "apache" in lowered:
        return "Apache-2.0", "open_source"
    if _MIT_RE.search(str(license_text)):
        return "MIT", "open_source"
    if "gnu gpl" in lowered or _GPL_RE.search(str(license_text)):
        return "GPL", "open_source"
    return "", "unknown"


def extract_font_record(path: str) -> Optional[dict]:
    """提取单字体文件元数据记录；失败 warning 并返回 None（不中断扫描）。

    字段对齐 fonts_db 字体记录（另附本机 file_path 与 us_weight_class
    供本机匹配/字重推断用，均不入库）。
    """
    if not _FONTTOOLS_AVAILABLE:
        logger.warning("fontTools 未安装，跳过字体元数据提取: %s", path)
        return None
    try:
        font = _open_ttfont(path)
        try:
            name_table = font["name"]
            family = (
                _name_str(name_table, _NAME_ID_TYPO_FAMILY)
                or _name_str(name_table, _NAME_ID_FAMILY)
            )
            subfamily = (
                _name_str(name_table, _NAME_ID_TYPO_SUBFAMILY)
                or _name_str(name_table, _NAME_ID_SUBFAMILY)
            )
            full_name = _name_str(name_table, _NAME_ID_FULL_NAME)
            ps_name = _name_str(name_table, _NAME_ID_POSTSCRIPT)
            manufacturer = (
                _name_str(name_table, _NAME_ID_MANUFACTURER)
                or _name_str(name_table, _NAME_ID_DESIGNER)
            )
            license_text = _name_str(name_table, _NAME_ID_LICENSE_TEXT)
            license_url = _name_str(name_table, _NAME_ID_LICENSE_URL)
            # 本地化 family 名（各语言 nameID 16/1/4 写法）——必须在字体
            # 文件关闭前收集（lazy 表数据关后不可读）。
            localized_families = _localized_family_names(
                name_table, (_NAME_ID_TYPO_FAMILY, _NAME_ID_FAMILY,
                             _NAME_ID_FULL_NAME), set())
            weight: Optional[int] = None
            if "OS/2" in font:
                weight = int(font["OS/2"].usWeightClass)
        finally:
            font.close()
    except Exception as exc:
        logger.warning("字体元数据提取失败，已跳过: path=%s, error=%s", path, exc)
        return None

    if not family:
        logger.warning("字体缺少 family 名（nameID 1/16），已跳过: %s", path)
        return None

    license_name, license_category = sniff_license(license_text)
    aliases: list[str] = []
    seen = {family}
    for candidate in (
        f"{family} {subfamily}" if subfamily else None,
        full_name,
        ps_name,
    ):
        if candidate and candidate not in seen:
            seen.add(candidate)
            aliases.append(candidate)
    # 本地化名并入别名——运行时样式/中日映射表引用的是"思源黑体 CN"
    # 这类本地化名，只收英文名会导致运行时查不到。
    for localized in localized_families:
        if len(aliases) >= _MAX_ALIASES:
            break
        if localized in seen:
            continue
        seen.add(localized)
        aliases.append(localized)

    return {
        "table": "fonts",
        "canonical_name": family,
        "aliases": aliases,
        "vendor": manufacturer,
        "license_name": license_name,
        "license_category": license_category,
        # 硬红线：入库 URL 必经消毒，命中盗版/破解标记一律丢弃。
        "official_url": sanitize_url(license_url),
        # 语言覆盖检测属后续任务，本任务固定空列表。
        "languages": [],
        "source": SOURCE,
        "file_path": os.path.abspath(str(path)),
        "us_weight_class": weight,
    }


# ── 参考字形渲染与缓存 ───────────────────────────────────────────

def _cmap_codepoints(path: str) -> Optional[set]:
    """字体 cmap 覆盖的码点集合；读取失败返回 None（表示无法判定）。"""
    try:
        font = _open_ttfont(path)
        try:
            cmap = font.getBestCmap()
        finally:
            font.close()
        return set(cmap.keys()) if cmap else set()
    except Exception as exc:
        logger.warning("cmap 读取失败，跳过缺字形判定: path=%s, error=%s", path, exc)
        return None


def cmap_codepoints(path: str) -> Optional[set]:
    """``_cmap_codepoints`` 的公开封装（T3.2 字形重排缺字形判定用）。

    返回字体 cmap 覆盖的码点集合；读取失败返回 None（表示无法判定，
    调用方应放弃缺字形过滤而不是误判全覆盖）。
    """
    return _cmap_codepoints(path)


def render_reference_glyphs(
    font_path: str, chars: str, size: int = 64
) -> dict[str, "Image"]:
    """渲染参考字形（L 模式墨迹图，紧致裁剪）；缺字形/空白字形跳过。

    仅内存操作，不落盘；依赖缺失或字体加载失败时返回 {}（优雅降级）。
    """
    if not is_available():
        logger.warning("fontTools/Pillow 未安装，跳过参考字形渲染: %s", font_path)
        return {}
    try:
        face = ImageFont.truetype(str(font_path), size=int(size))
    except Exception as exc:
        logger.warning(
            "字体加载失败，跳过参考字形渲染: path=%s, error=%s", font_path, exc
        )
        return {}
    present = _cmap_codepoints(str(font_path))
    out: dict[str, "Image"] = {}
    for ch in str(chars):
        if present is not None and ord(ch) not in present:
            continue  # 缺字形：跳过（不渲染 .notdef 兜底方块）
        try:
            canvas = Image.new("L", (int(size) * 2, int(size) * 2), 0)
            ImageDraw.Draw(canvas).text(
                (int(size) // 2, int(size) // 2), ch, font=face, fill=255
            )
            bbox = canvas.getbbox()
        except Exception as exc:
            logger.warning(
                "字形渲染失败，跳过该字符: path=%s, char=%r, error=%s",
                font_path, ch, exc,
            )
            continue
        if bbox is None or bbox[2] - bbox[0] <= 0 or bbox[3] - bbox[1] <= 0:
            continue
        out[ch] = canvas.crop(bbox)
    return out


class FontGlyphCache:
    """参考字形内存 LRU 缓存（键为字体文件路径；上限按字体文件数计）。

    命中不重渲染；超出 capacity 时淘汰最久未使用的字体条目。仅内存，
    不落盘。
    """

    def __init__(self, capacity: int = 128) -> None:
        if int(capacity) <= 0:
            raise ValueError("capacity must be a positive integer")
        self._capacity = int(capacity)
        self._entries: "OrderedDict[str, dict[str, Image]]" = OrderedDict()

    def get(self, font_path: str, chars: str) -> dict[str, "Image"]:
        """返回该字体的 char→Image 映射；只补渲染缺失的字符。"""
        key = str(font_path)
        entry = self._entries.get(key)
        if entry is not None:
            self._entries.move_to_end(key)
        missing = "".join(
            ch for ch in str(chars) if entry is None or ch not in entry
        )
        if entry is None:
            entry = {}
            self._entries[key] = entry
        if missing:
            entry.update(render_reference_glyphs(font_path, missing))
        while len(self._entries) > self._capacity:
            self._entries.popitem(last=False)
        return entry


# ── 入库辅助 ─────────────────────────────────────────────────────

def _font_record_to_seed_records(rec: dict) -> list[dict]:
    """索引记录 → import_seed 记录单元（fonts + aliases 必须同批导入，
    保证别名对字体的引用在同一条 import_seed 事务内可见）。"""
    font_rec = {
        "table": "fonts",
        "canonical_name": rec["canonical_name"],
        "vendor": rec.get("vendor") or None,
        "license_name": rec.get("license_name") or None,
        "license_category": rec.get("license_category") or "unknown",
        "official_url": rec.get("official_url") or None,
        "source": rec.get("source") or SOURCE,
        # languages 列为 TEXT；本任务不做语言覆盖检测，空列表不落库。
        "languages": None,
    }
    alias_recs = [
        {
            "table": "aliases",
            "font": rec["canonical_name"],
            "alias": alias,
            "source": rec.get("source") or SOURCE,
        }
        for alias in rec.get("aliases", [])
    ]
    return [font_rec] + alias_recs


def build_index(
    db: FontsDB, extra_dirs: list[str] = [], batch_size: int = IMPORT_BATCH_SIZE
) -> dict:
    """扫描并分批导入 seed 层；返回 stats：scanned/imported/failed。

    复用 ``import_seed`` 的幂等语义（同键先删后插），二次 build 计数
    不翻倍；依赖缺失时优雅降级返回全零 stats。
    """
    stats = {"scanned": 0, "imported": 0, "failed": 0}
    if not is_available():
        logger.warning(
            "fontTools/Pillow 未安装，build_index 不可用（优雅降级返回全零 stats）"
        )
        return stats
    dirs = default_font_dirs() + [str(d) for d in (extra_dirs or [])]
    buffer: list[dict] = []
    for path in _iter_font_files(dirs, recursive=True):
        stats["scanned"] += 1
        try:
            rec = extract_font_record(path)
        except Exception as exc:
            logger.warning("字体索引失败，已跳过: path=%s, error=%s", path, exc)
            rec = None
        if rec is None:
            stats["failed"] += 1
            continue
        stats["imported"] += 1
        buffer.extend(_font_record_to_seed_records(rec))
        if len(buffer) >= int(batch_size):
            db.import_seed(buffer)
            buffer = []
    if buffer:
        db.import_seed(buffer)
    logger.info(
        "本机字体索引完成: scanned=%(scanned)s imported=%(imported)s "
        "failed=%(failed)s" % stats
    )
    return stats
