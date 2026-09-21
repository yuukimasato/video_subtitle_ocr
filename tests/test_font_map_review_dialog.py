# tests/test_font_map_review_dialog.py
"""Unit tests for font_intel.etl.review 包导出 + components.font_map_review_dialog (T1.5 S5).

覆盖：复核包/决策包的构建与 JSON 往返（schema 标记、阈值、坏 schema 拒绝）、
复核对话框的行构建（可采纳条目可勾选、pending/非映射类型禁用且默认不勾选）、
get_result 的采纳/否决结构化决策、空列表占位、以及"对话框不做 DB 写入"
的分层红线（模块源码不依赖 font_intel）。

GUI 测试与 test_log_viewer.py 同法：offscreen 平台，QApplication 必须先于
conftest 的 QCoreApplication 创建（QWidget 需要）。
"""

from __future__ import annotations

import json
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from PySide6.QtWidgets import (  # noqa: E402
    QApplication,
    QCheckBox,
    QDialogButtonBox,
    QLabel,
)

_app = QApplication.instance() or QApplication(sys.argv[:1])

import pytest  # noqa: E402

from components.font_map_review_dialog import FontMapReviewDialog  # noqa: E402
from font_intel.etl.review import (  # noqa: E402
    DECISIONS_SCHEMA,
    REVIEW_SCHEMA,
    build_review_items,
    build_review_package,
    decisions_package,
    read_decisions_package,
    read_review_package,
    write_decisions_package,
    write_review_package,
)


def _mapping_item(line_no=3, conf=0.85, jp="XFont", cn="YFont", **kw) -> dict:
    item = {
        "source_file": "map.txt",
        "line_no": line_no,
        "line": f"{jp} 追加：{cn}",
        "record_type": "mapping",
        "names": [jp],
        "target": cn,
        "confidence": conf,
        "method": "rule",
        "reason": "low_confidence",
        "note": "append",
        "suggestion": {"kind": "mapping", "jp_name": jp, "cn_name": cn},
    }
    item.update(kw)
    return item


def _pending_item(line_no=9, line="看不懂的一行", reason="unrecognized") -> dict:
    return {
        "source_file": "map.txt",
        "line_no": line_no,
        "line": line,
        "record_type": "pending",
        "names": [],
        "target": None,
        "confidence": None,
        "method": "rule",
        "reason": reason,
        "note": "",
        "suggestion": None,
    }


# ── 复核包构建与往返 ─────────────────────────────────────────────
class TestReviewPackage:
    def test_package_shape_and_roundtrip(self, tmp_path):
        items = [_mapping_item(), _pending_item()]
        pkg = build_review_package(items, threshold=0.9)
        assert pkg["schema"] == REVIEW_SCHEMA
        assert pkg["confidence_threshold"] == 0.9
        assert "Seekladoom" in pkg["source_tag"]
        assert pkg["generated_at"]
        assert pkg["items"] == items

        path = tmp_path / "review.json"
        write_review_package(pkg, path)
        loaded = read_review_package(path)
        assert loaded == pkg

    def test_read_rejects_wrong_schema(self, tmp_path):
        path = tmp_path / "bad.json"
        path.write_text(json.dumps({"schema": "other/1", "items": []}),
                        encoding="utf-8")
        with pytest.raises(ValueError, match="schema"):
            read_review_package(path)

    def test_read_rejects_non_json(self, tmp_path):
        path = tmp_path / "bad.json"
        path.write_text("不是 JSON", encoding="utf-8")
        with pytest.raises(ValueError):
            read_review_package(path)

    def test_missing_file_raises_oserror(self, tmp_path):
        with pytest.raises(OSError):
            read_review_package(tmp_path / "nope.json")


# ── 决策包构建与往返 ─────────────────────────────────────────────
class TestDecisionsPackage:
    def test_roundtrip(self, tmp_path):
        adopted = [_mapping_item()]
        rejected = [_pending_item()]
        pkg = decisions_package(adopted, rejected)
        assert pkg["schema"] == DECISIONS_SCHEMA
        assert pkg["adopted"] == adopted
        assert pkg["rejected"] == rejected

        path = tmp_path / "decisions.json"
        write_decisions_package(pkg, path)
        assert read_decisions_package(path) == pkg

    def test_read_rejects_wrong_schema(self, tmp_path):
        path = tmp_path / "bad.json"
        path.write_text(json.dumps({"schema": REVIEW_SCHEMA, "adopted": [],
                                    "rejected": []}), encoding="utf-8")
        with pytest.raises(ValueError, match="schema"):
            read_decisions_package(path)


# ── 复核对话框（offscreen headless） ────────────────────────────
class TestFontMapReviewDialog:
    def test_rows_and_actionable_state(self):
        items = [
            _mapping_item(line_no=3),            # 可采纳
            _pending_item(line_no=9),            # 待人工：不可采纳
            _mapping_item(line_no=5, record_type="pitfall",
                          names=["甲", "乙"], target=None, suggestion=None),
        ]
        dialog = FontMapReviewDialog(items)
        checks = dialog.findChildren(QCheckBox)
        # 每条一个勾选行（对话框自身无其他 QCheckBox）
        assert len(checks) == 3
        # 默认全部不勾选：采纳必须显式勾选，杜绝 LLM 产出一键全收
        assert all(not c.isChecked() for c in checks)
        # 可采纳条目启用；pending 与非映射类型禁用（该类型不写库）
        enabled = [c.isEnabled() for c in checks]
        assert enabled == [True, False, False]

    def test_get_result_adopted_and_rejected(self):
        item_a = _mapping_item(line_no=3)
        item_b = _mapping_item(line_no=7, jp="PFont", cn="QFont")
        pending = _pending_item(line_no=9)
        dialog = FontMapReviewDialog([item_a, pending, item_b])
        checks = dialog.findChildren(QCheckBox)
        checks[0].setChecked(True)  # 采纳第一条；第二条不勾 = 否决

        result = dialog.get_result()
        assert result["adopted"] == [item_a]
        assert result["rejected"] == [item_b]
        # 不可采纳条目（pending）不进任何决策列表
        assert pending not in result["adopted"]
        assert pending not in result["rejected"]
        # 结构化决策可 JSON 序列化（供 CLI 导入）
        json.dumps(result, ensure_ascii=False)

    def test_empty_items_shows_placeholder(self):
        dialog = FontMapReviewDialog([])
        assert len(dialog.findChildren(QCheckBox)) == 0
        assert dialog.get_result() == {"adopted": [], "rejected": []}
        labels = dialog.findChildren(QLabel)
        assert any(labels)  # 占位说明存在

    def test_none_items_treated_as_empty(self):
        dialog = FontMapReviewDialog(None)
        assert dialog.get_result() == {"adopted": [], "rejected": []}

    def test_button_box_ok_cancel(self):
        dialog = FontMapReviewDialog([_mapping_item()])
        boxes = dialog.findChildren(QDialogButtonBox)
        assert len(boxes) == 1
        assert boxes[0].button(QDialogButtonBox.Ok) is not None
        assert boxes[0].button(QDialogButtonBox.Cancel) is not None

    def test_translated_texts_use_class_context(self):
        """文案经 QCoreApplication.translate（context=类名）：offscreen 无翻译
        时返回原文，这里锁定关键文案存在（i18n 键稳定）。"""
        dialog = FontMapReviewDialog([_mapping_item(line_no=3)])
        texts = [c.text() for c in dialog.findChildren(QCheckBox)]
        assert any("3" in t for t in texts)  # 行号进摘要
        assert "XFont" in texts[0] and "YFont" in texts[0]

    def test_dialog_module_never_touches_db(self):
        """分层红线：复核对话框只收集决策不做任何 DB 写入——模块不得
        import font_intel（写库由调用方经 db_loader 完成）或 sqlite3
        （无直接库访问）。用 AST 检查导入语句，docstring 提及模块名不算。"""
        import ast

        source_path = os.path.join(PROJECT_ROOT, "components",
                                   "font_map_review_dialog.py")
        with open(source_path, encoding="utf-8") as fh:
            tree = ast.parse(fh.read())
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                assert all(not a.name.startswith(("font_intel", "sqlite"))
                           for a in node.names)
            elif isinstance(node, ast.ImportFrom):
                module = node.module or ""
                assert not module.startswith(("font_intel", "sqlite"))
