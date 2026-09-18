# tests/test_log_viewer.py
"""日志查看器渲染回归:任意文本（引号/尖括号/实体/换行）必须逐字显示。

背景：append_log 曾对消息做 html.escape 后直接 QTextEdit.append——不含
尖括号的消息被 Qt 的 mightBeRichText 判为纯文本插入，转义实体原样露出
（含 dict repr 的日志行每个单引号都显示成 &#x27;）。修复后强制以富文本
插入，实体正确渲染回原字符。
"""

from __future__ import annotations

import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from PySide6.QtWidgets import QApplication  # noqa: E402

_app = QApplication.instance() or QApplication(sys.argv[:1])

from components.log_viewer import LogViewerWidget  # noqa: E402


def _make_viewer():
    viewer = LogViewerWidget()
    # 隔离用例：清掉默认 document 结构
    viewer.log_display.clear()
    return viewer


def test_quotes_render_verbatim_not_as_entities():
    """含 dict repr 的日志行（用户截图场景）：单/双引号不得显示为
    &#x27; / &quot;。"""
    viewer = _make_viewer()
    msg = "OCR engine switched to: paddle (options={'lang': 'japan', 'model_tier': 'auto'})"
    viewer.append_log(msg)
    rendered = viewer.log_display.toPlainText()
    assert rendered.strip() == msg
    assert "&#x27;" not in rendered
    assert "&quot;" not in rendered


def test_angle_brackets_and_ampersand_render_verbatim():
    """路径/OCR 文本里的 <...> 与 & 不得被当作标签吞掉或显示成 &lt;。"""
    viewer = _make_viewer()
    msg = "跳帧: 抓取区域(ROI) 'roi_0' <workspace> & 剔除噪声 '<' '>' &amp; 保留"
    viewer.append_log(msg)
    rendered = viewer.log_display.toPlainText()
    assert rendered.strip() == msg


def test_multiline_message_keeps_line_structure():
    viewer = _make_viewer()
    msg = "Traceback line 1\nRuntimeError: <roi> failed\n  line 3 with 'quote'"
    viewer.append_log(msg)
    rendered = viewer.log_display.toPlainText()
    assert rendered.strip().splitlines() == msg.splitlines()
    assert "&#x27;" not in rendered and "&lt;roi&gt;" not in rendered


def test_multiple_appends_stay_separate_lines():
    viewer = _make_viewer()
    viewer.append_log("first 'a'")
    viewer.append_log("second <b>")
    lines = viewer.log_display.toPlainText().strip().splitlines()
    assert lines == ["first 'a'", "second <b>"]
