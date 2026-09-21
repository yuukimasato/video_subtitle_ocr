# components/font_update_review_dialog.py
"""字体更新建议复核对话框（T3.4 §5.4 缺口回写的人工确认层）。

展示 ``font_intel.closure`` 产出的「更新建议包」条目（unknown 字体 /
低置信映射 / 链上更优建议），由用户逐条勾选决定去留：**勾选 = 采纳**
（写覆盖层），不勾 = 否决（留痕丢弃）。采纳必须显式勾选、默认全不选
——``method=llm_inferred`` 的推断记录永不免审直接生效（运行时
``compliance.decide`` 另有防御：未确认记录绝不参与 replace_auto）。

与 ``font_map_review_dialog`` 相同的分层约定：对话框只收集决策、不做
任何 DB 写入，``accept`` 后经 ``get_result()`` 取回结构化决策 dict
（``{"adopted": [...], "rejected": [...]}``），由调用方经
``font_intel.etl.db_loader.apply_font_update_decisions`` 写入覆盖层
（``method=human`` 确认语义）。本模块不依赖 font_intel。

i18n：所有文案以**字符串字面量**直调 ``QCoreApplication.translate``
（context=类名）——本版 pyside6-lupdate 的 Python 解析器不识别别名与
变量实参（T2.5/T1.5 实测同因同解）。
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


# 建议类型 → 展示文案。必须在 _type_text() 里以**字符串字面量**直调
# QCoreApplication.translate（理由同模块 docstring）。
def _type_text(suggestion_type: str) -> str:
    key = suggestion_type or ""
    if key == "new_font_record":
        return QCoreApplication.translate(
            "FontUpdateReviewDialog", "新增字体记录")
    if key == "mapping_correction":
        return QCoreApplication.translate(
            "FontUpdateReviewDialog", "映射修正")
    if key == "new_mapping":
        return QCoreApplication.translate(
            "FontUpdateReviewDialog", "新增映射")
    return suggestion_type or "-"


def _method_text(method: str) -> str:
    key = method or ""
    if key == "llm_inferred":
        return QCoreApplication.translate(
            "FontUpdateReviewDialog", "模型推断（未经人工确认）")
    if key == "glyph_rerank":
        return QCoreApplication.translate(
            "FontUpdateReviewDialog", "字形重排证据")
    return method or "-"


class _ItemRow(QWidget):
    """单条建议条目：勾选行（摘要）+ 说明标签（建议值/理由/依据/方法）。"""

    def __init__(self, item: Dict[str, Any], parent=None):
        super().__init__(parent)
        self.item = item
        self.actionable = item.get("suggestion") is not None

        self.checkbox = QCheckBox(self._summary_text(), self)
        # 采纳必须显式勾选：默认不勾（llm_inferred 推断产出免审即生效
        # 是方案 §5.4 明令禁止的行为）。
        self.checkbox.setChecked(False)
        self.checkbox.setEnabled(self.actionable)

        self.detail_label = QLabel(self._detail_text(), self)
        self.detail_label.setWordWrap(True)
        self.detail_label.setTextInteractionFlags(
            Qt.TextSelectableByMouse)  # 依据来源可复制

        layout = QVBoxLayout(self)
        layout.setContentsMargins(2, 4, 2, 4)
        layout.setSpacing(2)
        layout.addWidget(self.checkbox)
        layout.addWidget(self.detail_label)

    def _summary_text(self) -> str:
        item = self.item
        suggestion = item.get("suggestion")
        font_name = str(item.get("font_name") or "")
        if suggestion and suggestion.get("kind") == "mapping":
            return QCoreApplication.translate(
                "FontUpdateReviewDialog", "{0}：{1} → {2}").format(
                    _type_text(str(item.get("type") or "")),
                    font_name, str(suggestion.get("cn_name") or ""))
        if suggestion:  # font 记录
            return QCoreApplication.translate(
                "FontUpdateReviewDialog", "{0}：{1}").format(
                    _type_text(str(item.get("type") or "")), font_name)
        return QCoreApplication.translate(
            "FontUpdateReviewDialog", "{0}：{1}（不可采纳）").format(
                _type_text(str(item.get("type") or "")), font_name)

    def _detail_text(self) -> str:
        item = self.item
        lines: List[str] = []
        suggestion = item.get("suggestion")
        if suggestion and suggestion.get("kind") == "mapping":
            lines.append(QCoreApplication.translate(
                "FontUpdateReviewDialog", "建议：{0} → {1}").format(
                    suggestion.get("jp_name", ""),
                    suggestion.get("cn_name", "")))
        elif suggestion:  # font
            lines.append(QCoreApplication.translate(
                "FontUpdateReviewDialog", "建议：新增记录 {0}（许可类别"
                " {1}）").format(
                    suggestion.get("canonical_name", ""),
                    suggestion.get("license_category") or "unknown"))
        conf = item.get("confidence")
        conf_text = "—" if conf is None else f"{float(conf):.2f}"
        lines.append(QCoreApplication.translate(
            "FontUpdateReviewDialog", "类型：{0} ｜ 置信度：{1} ｜ 依据："
            "{2} ｜ 方法：{3}").format(
                _type_text(str(item.get("type") or "-")), conf_text,
                str(item.get("evidence") or "-") or "-",
                _method_text(str(item.get("method") or "-"))))
        lines.append(QCoreApplication.translate(
            "FontUpdateReviewDialog", "理由：{0}").format(
                str(item.get("reason") or "-")))
        if not self.actionable:
            lines.append(QCoreApplication.translate(
                "FontUpdateReviewDialog", "（该条目无建议值，仅展示）"))
        return "\n".join(lines)


class FontUpdateReviewDialog(QDialog):
    """更新建议包逐条采纳/否决；accept 后经 ``get_result()`` 取回决策。"""

    def __init__(self, items, parent=None):
        super().__init__(parent)
        self.setWindowTitle(
            QCoreApplication.translate("FontUpdateReviewDialog", "字体库更新建议"))
        self.resize(760, 600)
        self.setModal(True)

        self._items: List[Dict[str, Any]] = [dict(i) for i in (items or [])]
        self._rows: List[_ItemRow] = []

        content = QWidget()
        content_layout = QVBoxLayout(content)
        content_layout.setSpacing(6)

        if self._items:
            hint = QLabel(
                QCoreApplication.translate(
                    "FontUpdateReviewDialog",
                    "勾选 = 采纳并写入用户覆盖层（人工确认语义）；不勾 = 否决。"
                    "未经确认的模型推断记录不参与自动替换。"),
                content)
            hint.setWordWrap(True)
            content_layout.addWidget(hint)
            for item in self._items:
                row = _ItemRow(item, content)
                content_layout.addWidget(row)
                self._rows.append(row)
        else:
            content_layout.addWidget(QLabel(
                QCoreApplication.translate(
                    "FontUpdateReviewDialog", "无待复核的更新建议。"), content))
        content_layout.addStretch(1)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(content)

        buttons = QDialogButtonBox(
            QDialogButtonBox.Ok | QDialogButtonBox.Cancel, parent=self
        )
        buttons.button(QDialogButtonBox.Ok).setText(
            QCoreApplication.translate("FontUpdateReviewDialog", "应用"))
        buttons.button(QDialogButtonBox.Cancel).setText(
            QCoreApplication.translate("FontUpdateReviewDialog", "取消"))
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)

        outer = QVBoxLayout(self)
        outer.addWidget(scroll, 1)
        outer.addWidget(buttons, 0)

    def get_result(self) -> Dict[str, Any]:
        """收集勾选结果：勾选的可采纳条目为采纳、未勾为否决；不可采纳
        条目不进任何列表。返回 ``{"adopted": [...], "rejected": [...]}``，
        由调用方经 ``db_loader.apply_font_update_decisions`` 写入覆盖层。"""
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
