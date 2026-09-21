# components/font_map_review_dialog.py
"""字体映射复核对话框（ETL S5 人工复核）。

展示 ``font_intel.etl.review`` 导出的低置信/待人工条目，由用户逐条
勾选决定去留：**勾选 = 采纳**（写覆盖层），不勾 = 否决（留痕丢弃）。
采纳必须显式勾选、默认全不选——LLM 产出永不免审直接生效。

与 ``scan_review_dialog`` 相同的分层约定：对话框只收集决策、不做任何
DB 写入，``accept`` 后经 ``get_result()`` 取回结构化决策 dict（与
``font_intel.etl.db_loader.apply_review_decisions`` 同构），由调用方
写入 fonts_db 覆盖层。

不可采纳条目（``suggestion`` 为空的待人工行与非映射类型）只展示、
勾选框禁用：当前 schema 下它们没有对应表，修正路径是在源表修订后
重跑 ETL，而不是在对话框里强行写库。
"""

from __future__ import annotations

from typing import Any, Dict, List

from PySide6.QtCore import QCoreApplication, Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QLabel,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

# 已知 reason 的展示文案（key → 翻译源文案；未知 reason 原样展示）。
_REASON_TEXTS = {
    "low_confidence": "低置信度，建议人工确认",
    "unrecognized": "规则未能解析该行",
    "hallucinated_name": "疑似幻觉字体名（归一化后不是原文行子串）",
    "unknown_font_name": "字体名未命中本地词表",
    "llm_failed": "LLM 调用失败（有界退避耗尽），本行未结构化",
    "llm_output_invalid": "LLM 输出不符合约定格式，本行未结构化",
    "no_api_key": "未配置 API Key，本行未结构化",
    "empty": "空行",
    "comment": "注释行",
}


def _tr(text: str) -> str:
    return QCoreApplication.translate("FontMapReviewDialog", text)


def _reason_text(reason: str) -> str:
    known = _REASON_TEXTS.get(reason or "")
    return _tr(known) if known is not None else (reason or "-")


class _ItemRow(QWidget):
    """单条复核条目：勾选行（摘要）+ 说明标签（原文/建议/溯源/理由）。"""

    def __init__(self, item: Dict[str, Any], parent=None):
        super().__init__(parent)
        self.item = item
        self.actionable = item.get("suggestion") is not None

        summary = self._summary_text()
        self.checkbox = QCheckBox(summary, self)
        # 采纳必须显式勾选：默认不勾（否则等于 LLM 低置信产出免审生效）。
        self.checkbox.setChecked(False)
        self.checkbox.setEnabled(self.actionable)

        details = self._detail_text()
        self.detail_label = QLabel(details, self)
        self.detail_label.setWordWrap(True)
        self.detail_label.setTextInteractionFlags(
            Qt.TextSelectableByMouse)  # 溯源文件名/行号可复制

        layout = QVBoxLayout(self)
        layout.setContentsMargins(2, 4, 2, 4)
        layout.setSpacing(2)
        layout.addWidget(self.checkbox)
        layout.addWidget(self.detail_label)

    def _summary_text(self) -> str:
        line_no = self.item.get("line_no", 0)
        suggestion = self.item.get("suggestion")
        if suggestion and suggestion.get("kind") == "mapping":
            return _tr("第 {0} 行：{1} → {2}").format(
                line_no, suggestion.get("jp_name", ""), suggestion.get("cn_name", ""))
        if suggestion:  # negative_mapping
            names = " ＆ ".join(str(n) for n in self.item.get("names") or [])
            return _tr("第 {0} 行：{1} →（负映射）").format(line_no, names)
        line = str(self.item.get("line") or "")[:40]
        return _tr("第 {0} 行：{1}").format(line_no, line)

    def _detail_text(self) -> str:
        item = self.item
        lines: List[str] = []
        line = str(item.get("line") or "")
        if line:
            lines.append(_tr("原文：{0}").format(line))
        suggestion = item.get("suggestion")
        if suggestion and suggestion.get("kind") == "mapping":
            lines.append(_tr("建议：{0} → {1}").format(
                suggestion.get("jp_name", ""), suggestion.get("cn_name", "")))
        elif suggestion:  # negative_mapping
            names = " ＆ ".join(str(n) for n in item.get("names") or [])
            lines.append(_tr("建议：{0} 无日文本家对应").format(names))
        conf = item.get("confidence")
        conf_text = "—" if conf is None else f"{float(conf):.2f}"
        lines.append(_tr("类型：{0} ｜ 置信度：{1} ｜ 来源：{2} ｜ 文件：{3}").format(
            str(item.get("record_type") or "-"), conf_text,
            str(item.get("method") or "-") or "-",
            str(item.get("source_file") or "-") or "-"))
        lines.append(_tr("理由：{0}").format(_reason_text(str(item.get("reason") or ""))))
        if not self.actionable:
            lines.append(_tr("（该条目不写库；如需修正请在源表修订后重跑导入）"))
        return "\n".join(lines)


class FontMapReviewDialog(QDialog):
    """低置信/待人工条目复核；accept 后经 ``get_result()`` 取回决策。"""

    def __init__(self, items, parent=None):
        super().__init__(parent)
        self.setWindowTitle(_tr("字体映射复核"))
        self.resize(760, 600)
        self.setModal(True)

        self._items: List[Dict[str, Any]] = [dict(i) for i in (items or [])]
        self._rows: List[_ItemRow] = []

        content = QWidget()
        content_layout = QVBoxLayout(content)
        content_layout.setSpacing(6)

        if self._items:
            for item in self._items:
                row = _ItemRow(item, content)
                content_layout.addWidget(row)
                self._rows.append(row)
        else:
            content_layout.addWidget(QLabel(_tr("无待复核记录。"), content))
        content_layout.addStretch(1)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(content)

        buttons = QDialogButtonBox(
            QDialogButtonBox.Ok | QDialogButtonBox.Cancel, parent=self
        )
        buttons.button(QDialogButtonBox.Ok).setText(_tr("应用"))
        buttons.button(QDialogButtonBox.Cancel).setText(_tr("取消"))
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)

        outer = QVBoxLayout(self)
        outer.addWidget(scroll, 1)
        outer.addWidget(buttons, 0)

    def get_result(self) -> Dict[str, Any]:
        """收集勾选结果：勾选的可采纳条目为采纳、未勾为否决；不可采纳
        条目不进任何列表。返回 ``{"adopted": [...], "rejected": [...]}``，
        由调用方经 ``db_loader.apply_review_decisions`` 写入覆盖层。"""
        adopted: List[Dict[str, Any]] = []
        rejected: List[Dict[str, Any]] = []
        for row in self._rows:
            if not row.actionable:
                continue
            if row.checkbox.isChecked():
                adopted.append(row.item)
            else:
                rejected.append(row.item)
        return {"adopted": adopted, "rejected": rejected}
