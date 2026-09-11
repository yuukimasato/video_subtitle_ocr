# tests/test_pipeline_progress_dialog_dismiss_race.py
"""Regression: OCR 进度回调与进度对话框销毁的重入竞争。

机制（Qt 6 源码 qprogressdialog.cpp）：
QProgressDialog 在 isModal() 且已显示过（shownOnce）后，每次 setValue() 都会
调用 QCoreApplication::processEvents() 抽取事件队列。WindowModal 也算 modal。

因此当 worker 先 emit 进度、紧接着 emit pipeline_finished/error 时，主线程
的 _on_pipeline_progress 在 setValue() 内部就会把排在后面的 finished/error
投递出来；该处理器弹出模态 QMessageBox 并在用户点击后调用
_dismiss_progress_dialog() 把 self.progress_dialog 置为 None。控制流回到
setValue() 之后的下一条语句时，旧代码直接解引用了悬空引用：

    AttributeError: 'NoneType' object has no attribute 'setLabelText'

（_on_pipeline_progress 开头的判空在 setValue() 抽取事件之前执行，挡不住
抽队期间发生的置空。）
"""

import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import QApplication, QProgressDialog

# QApplication 必须在 conftest 的 QCoreApplication 之前创建（widget 需要）。
_app = QApplication.instance() or QApplication(sys.argv[:1])

from main_window.pipeline_control import PipelineControlMixin  # noqa: E402


class _PanelStub:
    """deepseek_progress_panel 的最小替身（进度路径只用 append_line）。"""

    def __init__(self):
        self.lines = []

    def append_line(self, line):
        self.lines.append(line)


class _Host(PipelineControlMixin):
    """只提供 _on_pipeline_progress / _dismiss_progress_dialog 触碰的属性。"""

    def __init__(self):
        self.progress_dialog = None
        self._pipeline_llm_active = False
        self.deepseek_progress_panel = _PanelStub()


def _make_progress_dialog() -> QProgressDialog:
    # 与 run_ocr_pipeline L102-109 完全一致的对话框配置。
    dlg = QProgressDialog("正在处理视频...", "取消", 0, 100)
    dlg.setMinimumDuration(0)
    dlg.setWindowModality(Qt.WindowModal)
    dlg.setAutoClose(True)
    dlg.show()
    return dlg


def test_progress_slot_survives_reentrant_dismissal():
    host = _Host()
    host.progress_dialog = _make_progress_dialog()

    # 预热：与真实运行相同，setValue(0) 后再 setValue(1) 会把对话框标记为
    # shownOnce，此后的每次 setValue() 都会抽取事件队列。
    host._on_pipeline_progress(0, "Step 1/4: Calculating...")
    host._on_pipeline_progress(1, "Step 1/4: ...")

    # 生产环境中，pipeline_finished 处理器（用户点完两个模态框后）会调用
    # _dismiss_progress_dialog()。这里把它排队，使其在下一次 setValue()
    # 的事件抽取中被投递 —— 精确复现用户截图的崩溃路径。
    QTimer.singleShot(0, host._dismiss_progress_dialog)

    # 旧代码在此抛出 AttributeError: 'NoneType' object has no attribute
    # 'setLabelText'；修复后应静默跳过对已销毁对话框的刷新。
    host._on_pipeline_progress(2, "Step 4/4: DeepSeek polishing subtitles (1/20 batches)...")

    assert host.progress_dialog is None
