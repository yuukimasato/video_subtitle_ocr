# font_intel/fonts_db.py
"""本地字体数据库（SQLite 单文件，仅标准库 ``sqlite3``，无网络调用）。

存储字体情报：字体元数据、中日英别名、日→中字体名映射（含负映射）、
许可合规规则与用户确认记录，供后续翻译建议/合规决策引擎查询。

默认库路径与 ``utils/secret_store.py`` 同一约定：
``$XDG_DATA_HOME/video_subtitle_ocr/fonts.db``（未设置时
``~/.local/share/video_subtitle_ocr/fonts.db``）。测试可传显式 path
（含 ``":memory:"``）。

schema 版本化迁移
-----------------
``MIGRATIONS`` 是有序 ``(version, sql)`` 列表；打开库时按需逐版本应用
（只执行 version 大于库内当前版本的迁移），已最新则不做任何写入。
库内版本高于代码支持版本时拒绝打开（禁止隐式降级）。

双层（seed/overlay）语义
------------------------
- ``seed``：ETL 导入的种子情报，运行时只读——写入口仅 ``import_seed``
  （幂等，按唯一键替换旧值）；其余 API 一律不写 seed。
- ``overlay``：用户/运行时的覆盖层，同名/同键记录优先于 seed；
  ``reset_overlay()`` 整体清除。种子层不可变，"整体重置种子" 由
  ``reset_overlay()`` + 重新 ``import_seed()`` 组合实现。

唯一键取舍：``fonts`` 上是 ``UNIQUE(canonical_name, layer)`` 而非全局
UNIQUE——同名字体允许 seed 与 overlay 各存一行，查询时 overlay 优先、
删除 overlay 后回落 seed；同理 ``jp_cn_font_map`` 的幂等键含 layer，
由写入 API 以"先删同键旧行再插入"保证（负映射 cn_name 为 NULL，
SQLite 的 UNIQUE 对 NULL 视为互异，故不能只靠表约束）。
"""

from __future__ import annotations

import json
import logging
import os
import sqlite3
from datetime import datetime, timezone
from typing import Any, Optional

logger = logging.getLogger(__name__)

LAYER_SEED = "seed"
LAYER_OVERLAY = "overlay"

_LICENSE_CATEGORIES = ("open_source", "free_commercial", "commercial_paid", "unknown")
_USAGE_SCENES = ("personal", "publish", "commercial")
_RULE_ACTIONS = ("allow", "prompt", "replace_auto", "report_only")
_MAP_KINDS = ("mapping", "negative_mapping")

# 运行时允许 import_seed 写入的表；user_overrides 是用户确认记录（运行时
# 数据），不属于种子情报，因此不可通过种子导入伪造。
_SEED_TABLES = ("fonts", "aliases", "jp_cn_font_map", "license_rules")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


def _alternates_to_text(value) -> Optional[str]:
    """开源替代链字段规范化为 JSON 字符串数组文本（去空去重，首现顺序保留）。

    接受 ``None``（返回 None）、``list``/``tuple``（元素转 str）或已是
    JSON 数组的文本；其余类型拒绝（ValueError）。空列表规范化为 None
    （NULL），查询侧按"无替代链"处理。
    """
    if value is None:
        return None
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except (ValueError, TypeError) as exc:
            raise ValueError(
                f"alternates must be a list or a JSON array text, got {value!r}"
            ) from exc
    elif isinstance(value, (list, tuple)):
        parsed = value
    else:
        raise ValueError(
            f"alternates must be a list or a JSON array text, got {value!r}"
        )
    items: list[str] = []
    seen: set[str] = set()
    for item in parsed:
        text = str(item).strip()
        if text and text.lower() not in seen:
            seen.add(text.lower())
            items.append(text)
    return json.dumps(items, ensure_ascii=False) if items else None


# ── schema v1 ───────────────────────────────────────────────────

_V1_SQL = """
CREATE TABLE schema_version (
    version     INTEGER PRIMARY KEY,
    applied_at  TEXT NOT NULL
);

CREATE TABLE fonts (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    canonical_name   TEXT NOT NULL,
    vendor           TEXT,
    category         TEXT,
    languages        TEXT,
    license_category TEXT NOT NULL DEFAULT 'unknown'
        CHECK (license_category IN ('open_source','free_commercial','commercial_paid','unknown')),
    license_name     TEXT,
    official_url     TEXT,
    source           TEXT,
    layer            TEXT NOT NULL DEFAULT 'seed' CHECK (layer IN ('seed','overlay')),
    updated_at       TEXT,
    UNIQUE (canonical_name, layer)
);

CREATE TABLE aliases (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    font_id INTEGER NOT NULL REFERENCES fonts(id) ON DELETE CASCADE,
    alias   TEXT NOT NULL,
    layer   TEXT NOT NULL DEFAULT 'seed' CHECK (layer IN ('seed','overlay'))
);
CREATE UNIQUE INDEX idx_aliases_font_alias ON aliases(font_id, alias, layer);
CREATE INDEX idx_aliases_alias ON aliases(alias);

CREATE TABLE jp_cn_font_map (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    jp_name     TEXT NOT NULL,
    cn_name     TEXT,
    kind        TEXT NOT NULL DEFAULT 'mapping' CHECK (kind IN ('mapping','negative_mapping')),
    method      TEXT,
    confidence  REAL,
    source_file TEXT,
    line_no     INTEGER,
    layer       TEXT NOT NULL DEFAULT 'seed' CHECK (layer IN ('seed','overlay'))
);
CREATE INDEX idx_jp_cn_map_jp ON jp_cn_font_map(jp_name);
CREATE INDEX idx_jp_cn_map_cn ON jp_cn_font_map(cn_name);

CREATE TABLE license_rules (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    license_category TEXT NOT NULL,
    usage_scene      TEXT NOT NULL CHECK (usage_scene IN ('personal','publish','commercial')),
    action           TEXT NOT NULL CHECK (action IN ('allow','prompt','replace_auto','report_only')),
    layer            TEXT NOT NULL DEFAULT 'seed' CHECK (layer IN ('seed','overlay')),
    UNIQUE (license_category, usage_scene, layer)
);

CREATE TABLE user_overrides (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    target_table TEXT NOT NULL,
    target_key   TEXT NOT NULL,
    patch_json   TEXT NOT NULL,
    approved     INTEGER NOT NULL DEFAULT 0,
    created_at   TEXT
);
"""

MIGRATIONS: list[tuple[int, str]] = [
    (1, _V1_SQL),
    # v2：jp_cn_font_map 增加 source 溯源列（数据来源与许可标注，如
    # "Seekladoom/Japanese-Chinese-Fonts-adaptation (MIT)"）——ETL S6
    # 入库要求逐条映射带来源标注；ALTER TABLE 只加可空列，旧数据不受影响。
    (2, "ALTER TABLE jp_cn_font_map ADD COLUMN source TEXT;"),
    # v3：fonts 增加 alternates 列（开源替代链，JSON 字符串数组文本）——
    # matching 三级链第二级"开源替代"的查库字段；ALTER TABLE 只加可空列，
    # 旧数据不受影响。
    (3, "ALTER TABLE fonts ADD COLUMN alternates TEXT;"),
]


def default_db_path() -> str:
    """默认库路径（与 utils/secret_store.py 的 XDG 约定一致）。"""
    data_home = os.environ.get("XDG_DATA_HOME", "").strip() or os.path.join(
        os.path.expanduser("~"), ".local", "share"
    )
    return os.path.join(data_home, "video_subtitle_ocr", "fonts.db")


# ── main class ──────────────────────────────────────────────────

class FontsDB:
    """本地字体数据库句柄。

    打开即完成版本迁移（含新建库）；同一文件可多次开闭，迁移只发生一次。
    """

    def __init__(self, path: Optional[str] = None) -> None:
        """
        Args:
            path: 数据库文件路径；``None`` 用 ``default_db_path()``；
                ``":memory:"`` 建内存库（测试用）。
        """
        if path is None:
            path = default_db_path()
        self._path = path
        if path != ":memory:":
            # 显式路径允许指向尚不存在的目录（如首启时的 XDG 数据目录）。
            os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        self._conn = sqlite3.connect(path)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA foreign_keys=ON")
        self._apply_migrations()

    # -- lifecycle -------------------------------------------------

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> "FontsDB":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    def _apply_migrations(self) -> None:
        conn = self._conn
        # 版本表的正式 DDL 在 v1 迁移内；这里只探测当前版本（新库视为 0，
        # 由 v1 统一建表，避免与迁移脚本重复建表冲突）。
        has_version_table = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='schema_version'"
        ).fetchone()
        current = 0
        if has_version_table is not None:
            row = conn.execute("SELECT MAX(version) FROM schema_version").fetchone()
            current = int(row[0]) if row and row[0] is not None else 0
        latest = MIGRATIONS[-1][0] if MIGRATIONS else 0
        if current > latest:
            # 库来自更新版本的程序：拒绝打开比静默降级/损坏 schema 更安全。
            raise RuntimeError(
                f"fonts.db schema version {current} is newer than supported "
                f"version {latest}: {self._path}"
            )
        prev = 0
        for version, sql in MIGRATIONS:
            if version <= prev:
                raise RuntimeError(
                    f"MIGRATIONS versions must be strictly increasing "
                    f"(got {version} after {prev})"
                )
            prev = version
            if version <= current:
                continue
            # 每个迁移与其版本号写入同一事务：executescript 不做隐式事务
            # 控制，因此显式 BEGIN/COMMIT，失败即整体回滚。
            script = (
                "BEGIN;\n"
                + sql.strip().rstrip(";")
                + ";\nINSERT INTO schema_version (version, applied_at) "
                + f"VALUES ({int(version)}, '{_now_iso()}');\nCOMMIT;"
            )
            try:
                conn.executescript(script)
            except Exception:
                conn.rollback()
                logger.exception("fonts.db migration to v%s failed", version)
                raise
            logger.info("fonts.db migrated to schema v%s: %s", version, self._path)

    def get_schema_version(self) -> int:
        """当前库 schema 版本；空库（未应用任何迁移）返回 0。"""
        row = self._conn.execute("SELECT MAX(version) FROM schema_version").fetchone()
        return int(row[0]) if row and row[0] is not None else 0

    # -- seed import -------------------------------------------------

    def import_seed(self, records: list[dict]) -> None:
        """导入种子情报（只写 layer='seed'）。

        幂等：同唯一键的 seed 旧行先删后插（替换旧值），重复导入不产生
        重复行。单批原子：任一记录非法则整批不落库。

        记录格式（按 ``table`` 字段区分）：
        - ``fonts``: canonical_name 必填，其余字段可选；
        - ``aliases``: font（对应字体的 canonical_name）+ alias 必填；
        - ``jp_cn_font_map``: jp_name 必填；kind 默认 'mapping'（此时
          cn_name 必填）；负映射 cn_name 可空；
        - ``license_rules``: license_category / usage_scene / action 必填。
        """
        validated = [self._validate_seed_record(rec) for rec in records]
        with self._conn:
            for rec in validated:
                self._upsert_seed_row(rec)

    def _validate_seed_record(self, rec: Any) -> dict:
        if not isinstance(rec, dict) or rec.get("table") not in _SEED_TABLES:
            raise ValueError(f"import_seed: unknown record table: {rec!r}")
        layer = rec.get("layer", LAYER_SEED)
        if layer != LAYER_SEED:
            # import_seed 是唯一的 seed 写入口；覆盖层数据走 overlay API。
            raise ValueError(
                f"import_seed only accepts layer='seed' records, got {layer!r}"
            )
        table = rec["table"]
        out: dict = {"table": table, "layer": LAYER_SEED}

        def _s(key: str) -> Optional[str]:
            val = rec.get(key)
            return str(val).strip() if val is not None else None

        if table == "fonts":
            out["canonical_name"] = _s("canonical_name")
            if not out["canonical_name"]:
                raise ValueError("fonts seed record requires canonical_name")
            lc = _s("license_category") or "unknown"
            if lc not in _LICENSE_CATEGORIES:
                raise ValueError(f"invalid license_category: {lc!r}")
            out["license_category"] = lc
            for key in ("vendor", "category", "languages", "license_name",
                        "official_url", "source"):
                out[key] = _s(key)
            # 开源替代链（schema v3）：list/JSON 数组文本 → 规范化 JSON 文本。
            out["alternates"] = _alternates_to_text(rec.get("alternates"))
        elif table == "aliases":
            out["font"] = _s("font")
            out["alias"] = _s("alias")
            if not out["font"] or not out["alias"]:
                raise ValueError("aliases seed record requires font and alias")
            out["source"] = _s("source")
        elif table == "jp_cn_font_map":
            out["jp_name"] = _s("jp_name")
            if not out["jp_name"]:
                raise ValueError("jp_cn_font_map seed record requires jp_name")
            kind = _s("kind") or "mapping"
            if kind not in _MAP_KINDS:
                raise ValueError(f"invalid kind: {kind!r}")
            out["kind"] = kind
            out["cn_name"] = _s("cn_name")
            if kind == "mapping" and not out["cn_name"]:
                raise ValueError("positive mapping requires cn_name")
            conf = rec.get("confidence")
            out["confidence"] = float(conf) if conf is not None else None
            line_no = rec.get("line_no")
            out["line_no"] = int(line_no) if line_no is not None else None
            for key in ("method", "source_file", "source"):
                out[key] = _s(key)
        else:  # license_rules
            for key, allowed in (("license_category", _LICENSE_CATEGORIES),
                                 ("usage_scene", _USAGE_SCENES),
                                 ("action", _RULE_ACTIONS)):
                val = _s(key)
                if val not in allowed:
                    raise ValueError(f"license_rules invalid {key}: {val!r}")
                out[key] = val
        return out

    def _upsert_seed_row(self, rec: dict) -> None:
        conn = self._conn
        table = rec["table"]
        if table == "fonts":
            # 替换 seed 旧值时连带旧别名一起删，别名随后整批重建。
            conn.execute(
                "DELETE FROM aliases WHERE font_id IN "
                "(SELECT id FROM fonts WHERE canonical_name=? AND layer='seed')",
                (rec["canonical_name"],),
            )
            conn.execute(
                "DELETE FROM fonts WHERE canonical_name=? AND layer='seed'",
                (rec["canonical_name"],),
            )
            conn.execute(
                "INSERT INTO fonts (canonical_name, vendor, category, languages,"
                " license_category, license_name, official_url, source,"
                " alternates, layer, updated_at) VALUES (?,?,?,?,?,?,?,?,?,'seed',?)",
                (rec["canonical_name"], rec["vendor"], rec["category"],
                 rec["languages"], rec["license_category"], rec["license_name"],
                 rec["official_url"], rec["source"], rec.get("alternates"),
                 _now_iso()),
            )
        elif table == "aliases":
            row = conn.execute(
                "SELECT id FROM fonts WHERE canonical_name=? AND layer='seed'",
                (rec["font"],),
            ).fetchone()
            if row is None:
                raise ValueError(
                    f"aliases seed record references unknown seed font: {rec['font']!r}"
                )
            conn.execute(
                "DELETE FROM aliases WHERE font_id=? AND alias=? AND layer='seed'",
                (row[0], rec["alias"]),
            )
            conn.execute(
                "INSERT INTO aliases (font_id, alias, layer) VALUES (?,?,'seed')",
                (row[0], rec["alias"]),
            )
        elif table == "jp_cn_font_map":
            conn.execute(
                "DELETE FROM jp_cn_font_map WHERE jp_name=? AND kind=?"
                " AND COALESCE(cn_name,'')=COALESCE(?,'') AND layer='seed'",
                (rec["jp_name"], rec["kind"], rec["cn_name"]),
            )
            conn.execute(
                "INSERT INTO jp_cn_font_map (jp_name, cn_name, kind, method,"
                " confidence, source_file, line_no, source, layer)"
                " VALUES (?,?,?,?,?,?,?,?,'seed')",
                (rec["jp_name"], rec["cn_name"], rec["kind"], rec["method"],
                 rec["confidence"], rec["source_file"], rec["line_no"],
                 rec["source"]),
            )
        else:  # license_rules
            conn.execute(
                "DELETE FROM license_rules WHERE license_category=?"
                " AND usage_scene=? AND layer='seed'",
                (rec["license_category"], rec["usage_scene"]),
            )
            conn.execute(
                "INSERT INTO license_rules (license_category, usage_scene,"
                " action, layer) VALUES (?,?,?,'seed')",
                (rec["license_category"], rec["usage_scene"], rec["action"]),
            )

    # -- overlay writers ---------------------------------------------

    def upsert_overlay_font(
        self,
        canonical_name: str,
        *,
        vendor: Optional[str] = None,
        category: Optional[str] = None,
        languages: Optional[str] = None,
        license_category: str = "unknown",
        license_name: Optional[str] = None,
        official_url: Optional[str] = None,
        source: Optional[str] = None,
        aliases: Optional[list] = None,
        alternates: Optional[list] = None,
        layer: str = LAYER_OVERLAY,
    ) -> None:
        """写入/替换覆盖层字体记录（含覆盖层别名与开源替代链）。

        种子层运行时只读：``layer`` 参数仅接受 'overlay'，传入 'seed'
        直接拒绝，保证 upsert 只落覆盖层。``alternates`` 接受字体名列表
        （存为 JSON 数组文本）；默认 None 时该列落 NULL，查询按字段级
        回落种子值（与其它可空字段一致）。
        """
        if layer != LAYER_OVERLAY:
            raise ValueError(
                f"overlay writers only accept layer='overlay', got {layer!r}"
            )
        name = (canonical_name or "").strip()
        if not name:
            raise ValueError("canonical_name is required")
        if license_category not in _LICENSE_CATEGORIES:
            raise ValueError(f"invalid license_category: {license_category!r}")
        alias_list = [str(a).strip() for a in (aliases or []) if str(a).strip()]
        alternates_text = _alternates_to_text(alternates)
        conn = self._conn
        with conn:
            conn.execute(
                "DELETE FROM aliases WHERE font_id IN "
                "(SELECT id FROM fonts WHERE canonical_name=? AND layer='overlay')",
                (name,),
            )
            conn.execute(
                "DELETE FROM fonts WHERE canonical_name=? AND layer='overlay'",
                (name,),
            )
            cur = conn.execute(
                "INSERT INTO fonts (canonical_name, vendor, category, languages,"
                " license_category, license_name, official_url, source,"
                " alternates, layer, updated_at) VALUES (?,?,?,?,?,?,?,?,?,'overlay',?)",
                (name, vendor, category, languages, license_category,
                 license_name, official_url, source, alternates_text,
                 _now_iso()),
            )
            font_id = int(cur.lastrowid)
            for alias in alias_list:
                conn.execute(
                    "INSERT INTO aliases (font_id, alias, layer)"
                    " VALUES (?,?,'overlay')",
                    (font_id, alias),
                )

    def add_overlay_mapping(
        self,
        jp_name: str,
        cn_name: Optional[str] = None,
        *,
        kind: str = "mapping",
        method: Optional[str] = None,
        confidence: Optional[float] = None,
        source_file: Optional[str] = None,
        line_no: Optional[int] = None,
        source: Optional[str] = None,
        layer: str = LAYER_OVERLAY,
    ) -> None:
        """写入/替换覆盖层日→中映射（正/负映射；同键替换，不产生重复行）。

        ``source`` 为数据来源与许可标注（如 ETL S5 人工确认记录沿用的
        "Seekladoom/Japanese-Chinese-Fonts-adaptation (MIT)"），仅作元数据。
        """
        if layer != LAYER_OVERLAY:
            raise ValueError(
                f"overlay writers only accept layer='overlay', got {layer!r}"
            )
        if kind not in _MAP_KINDS:
            raise ValueError(f"invalid kind: {kind!r}")
        jp = (jp_name or "").strip()
        cn = (cn_name or "").strip() or None
        if not jp:
            raise ValueError("jp_name is required")
        if kind == "mapping" and not cn:
            raise ValueError("positive mapping requires cn_name")
        conn = self._conn
        with conn:
            conn.execute(
                "DELETE FROM jp_cn_font_map WHERE jp_name=? AND kind=?"
                " AND COALESCE(cn_name,'')=COALESCE(?,'') AND layer='overlay'",
                (jp, kind, cn),
            )
            conn.execute(
                "INSERT INTO jp_cn_font_map (jp_name, cn_name, kind, method,"
                " confidence, source_file, line_no, source, layer)"
                " VALUES (?,?,?,?,?,?,?,?,'overlay')",
                (jp, cn, kind, method, confidence, source_file, line_no,
                 source),
            )

    def reset_overlay(self) -> None:
        """清除全部覆盖层数据（种子层不动；user_overrides 是用户确认记录，
        不属于覆盖层情报，故一并保留）。"""
        conn = self._conn
        with conn:
            # 先删别名再删字体，配合外键级联双保险。
            conn.execute("DELETE FROM aliases WHERE layer='overlay'")
            for table in ("fonts", "jp_cn_font_map", "license_rules"):
                conn.execute(f"DELETE FROM {table} WHERE layer='overlay'")
        logger.info("fonts.db overlay layer reset: %s", self._path)

    # -- lookups -------------------------------------------------------

    def lookup_font(self, name: str) -> Optional[dict]:
        """按规范名或别名查字体；命中 overlay 时优先返回（NULL 字段回落
        同名 seed 值），否则回落 seed；都未命中返回 None。"""
        key = (name or "").strip()
        if not key:
            return None
        overlay_row = self._find_font_row(key, LAYER_OVERLAY)
        seed_row = self._find_font_row(key, LAYER_SEED)
        top = overlay_row if overlay_row is not None else seed_row
        if top is None:
            return None
        overlay = dict(overlay_row) if overlay_row is not None else None
        seed = dict(seed_row) if seed_row is not None else None
        merged = dict(top)
        if (overlay is not None and seed is not None
                and overlay["canonical_name"] == seed["canonical_name"]):
            # 覆盖层未覆盖的字段回落种子层（id 之外逐字段合并）。
            for field, seed_val in seed.items():
                if field == "id":
                    continue
                if merged.get(field) is None and seed_val is not None:
                    merged[field] = seed_val
        font_id = int(merged.pop("id"))
        merged["font_id"] = font_id
        merged["aliases"] = [
            r[0] for r in self._conn.execute(
                "SELECT alias FROM aliases WHERE font_id=? ORDER BY id",
                (font_id,),
            )
        ]
        return merged

    def _find_font_row(self, name: str, layer: str) -> Optional[sqlite3.Row]:
        row = self._conn.execute(
            "SELECT DISTINCT f.* FROM fonts f "
            "LEFT JOIN aliases a ON a.font_id = f.id "
            "WHERE f.layer = ? AND (f.canonical_name = ? OR a.alias = ?) "
            "ORDER BY f.id LIMIT 1",
            (layer, name, name),
        ).fetchone()
        return row

    def lookup_jp_cn(
        self, jp_name: str, *, include_negative: bool = False
    ) -> list[dict]:
        """日文名 → 中文名候选列表。

        默认只返回正映射（kind='mapping'）；``include_negative=True``
        时负映射也一并返回（以 kind 字段区分）。同键
        (jp_name, kind, cn_name) 的 overlay 行优先：被其覆盖的 seed 行
        不再返回（行本身仍保留在库中）。
        """
        key = (jp_name or "").strip()
        if not key:
            return []
        sql = "SELECT * FROM jp_cn_font_map WHERE jp_name=?"
        if not include_negative:
            sql += " AND kind='mapping'"
        sql += " ORDER BY id"
        rows = [dict(r) for r in self._conn.execute(sql, (key,))]
        overlay_keys = {
            (r["kind"], r["cn_name"] or "")
            for r in rows if r["layer"] == LAYER_OVERLAY
        }
        return [
            r for r in rows
            if r["layer"] != LAYER_SEED
            or (r["kind"], r["cn_name"] or "") not in overlay_keys
        ]

    def lookup_jp_cn_reverse(self, cn_name: str) -> list[dict]:
        """中文名反向查询：映射到该中文名的 jp_name 行列表（matching 的
        中→日链路用）。

        只返回正映射（负映射 ``cn_name`` 为 NULL，天然不会按名命中）。
        同键 (jp_name, kind, cn_name) 的 overlay 行优先：被其覆盖的 seed
        行不再返回（行本身仍保留在库中）——语义与 ``lookup_jp_cn`` 镜像。
        """
        key = (cn_name or "").strip()
        if not key:
            return []
        rows = [dict(r) for r in self._conn.execute(
            "SELECT * FROM jp_cn_font_map WHERE cn_name=? ORDER BY id", (key,))]
        overlay_keys = {
            (r["kind"], r["jp_name"] or "")
            for r in rows if r["layer"] == LAYER_OVERLAY
        }
        return [
            r for r in rows
            if r["layer"] != LAYER_SEED
            or (r["kind"], r["jp_name"] or "") not in overlay_keys
        ]

    def list_fonts(self) -> list[dict]:
        """列出全部字体（只读）：同名 seed/overlay 行合并为一行，overlay
        优先、NULL 字段回落 seed——合并语义与 ``lookup_font`` 一致；结果按
        canonical_name 排序保证确定。供 matching 同风格类别兜底等批量检索
        使用（库量级为数百~数千行，全量取回后内存过滤即可，无需额外索引）。
        """
        rows = [dict(r) for r in self._conn.execute(
            "SELECT * FROM fonts ORDER BY canonical_name,"
            " CASE layer WHEN 'overlay' THEN 0 ELSE 1 END, id")]
        merged_by_name: dict[str, dict] = {}
        order: list[str] = []
        for row in rows:
            name = row["canonical_name"]
            if name not in merged_by_name:
                merged_by_name[name] = row
                order.append(name)
                continue
            top = merged_by_name[name]
            for field, val in row.items():
                if field != "id" and top.get(field) is None and val is not None:
                    top[field] = val
        out: list[dict] = []
        for name in order:
            merged = dict(merged_by_name[name])
            font_id = int(merged.pop("id"))
            merged["font_id"] = font_id
            merged["aliases"] = [
                r[0] for r in self._conn.execute(
                    "SELECT alias FROM aliases WHERE font_id=? ORDER BY id",
                    (font_id,),
                )
            ]
            out.append(merged)
        return out

    def lookup_license_rule(
        self, license_category: str, usage_scene: str
    ) -> Optional[dict]:
        """查合规规则（决策引擎后续任务消费）；overlay 优先于 seed。"""
        row = self._conn.execute(
            "SELECT * FROM license_rules WHERE license_category=? AND usage_scene=? "
            "ORDER BY CASE layer WHEN 'overlay' THEN 0 ELSE 1 END, id LIMIT 1",
            (license_category, usage_scene),
        ).fetchone()
        return dict(row) if row is not None else None
