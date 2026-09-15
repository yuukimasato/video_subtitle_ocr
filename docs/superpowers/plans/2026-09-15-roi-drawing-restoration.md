# ROI 绘制入口恢复实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在默认简洁界面恢复矩形和多边形 ROI 的可见绘制入口，同时保留已有编辑、保存和识别行为。

**Architecture:** 仅调整控制面板的可见性策略；画布绘制、ROI 数据结构和流水线均复用当前实现。通过 UI 可见性/信号回归测试保护入口，通过现有全量测试保护基线。

**Tech Stack:** Python 3、PySide6、pytest、git。

**Spec:** `docs/superpowers/specs/2026-09-15-roi-drawing-restoration.md`

## Global Constraints

- 不回退或重写 `components/video_display.py` 的现有矩形/多边形实现。
- 不修改 ROI 数据格式：矩形使用 `[x, y, w, h]`，多边形使用 `[[x, y], ...]`。
- 不删除 `get_pipeline_options()` 的任何字段。
- 修改前必须记录基线测试结果；修改后必须运行定向测试和全量测试。
- 任何子智能体都在共享工作区工作，不得覆盖其他智能体的未提交修改。

---

### Task 1: 记录并保护基线

**Files:**
- Test: `tests/test_control_panel_quick_mode.py`
- Test: `tests/test_roi_canvas_editing.py`

- [x] **Step 1: 检查工作区和分支**

```bash
git status --short --branch
git branch --show-current
```

Expected: 当前分支为 `main`；不要重置或清理已有未提交文件。

- [x] **Step 2: 运行当前基线测试**

```bash
.venv/bin/pytest -q
```

Expected baseline: `326 passed, 1 skipped`（允许出现环境相关 warning，但不得有失败）。若结果不同，记录实际结果并停止修改，先报告基线变化。

- [x] **Step 3: 提交或保存基线记录**

将命令、提交号和实际结果写入执行记录；不得修改生产代码。

### Task 2: 编写 quick mode 绘制入口回归测试

**Files:**
- Modify: `tests/test_control_panel_quick_mode.py`

**Interfaces:**
- Consumes: `ControlPanelWidget.set_quick_mode(bool)`、`draw_mode_group`、`rect_mode_radio`、`poly_mode_radio`、`edit_mode_radio`。
- Produces: 可验证 quick mode 绘制入口可见性的测试。

- [x] **Step 1: 添加失败测试**

在现有 `test_quick_mode_toggle_keeps_controls_and_values` 后加入：

```python
def test_quick_mode_keeps_roi_drawing_modes_visible(panel):
    assert panel.quick_mode is True
    assert panel.draw_mode_group.isVisibleTo(panel)
    assert panel.rect_mode_radio.isVisibleTo(panel)
    assert panel.poly_mode_radio.isVisibleTo(panel)
    assert panel.edit_mode_radio.isVisibleTo(panel)
```

- [x] **Step 2: 运行定向测试确认失败**

```bash
.venv/bin/pytest -q tests/test_control_panel_quick_mode.py::test_quick_mode_keeps_roi_drawing_modes_visible
```

Expected: FAIL，因为当前 `set_quick_mode(True)` 隐藏 `draw_mode_group`。

### Task 3: 恢复 quick mode 绘制模式可见性

**Files:**
- Modify: `components/control_panel.py:set_quick_mode`

**Interfaces:**
- Consumes: 现有 `draw_mode_group` 控件和 `full` 布尔值。
- Produces: quick mode 下始终可见的绘制模式入口；full mode 行为保持兼容。

- [x] **Step 1: 修改可见性策略**

将 `set_quick_mode()` 中：

```python
self.draw_mode_group.setVisible(full)
```

改为：

```python
# ROI 绘制是核心工作流入口，简洁模式也必须可见。
self.draw_mode_group.setVisible(True)
```

不要改动 `source_filter_group`、`engine_group`、高级组或模式入口按钮的逻辑。

- [x] **Step 2: 运行定向测试**

```bash
.venv/bin/pytest -q tests/test_control_panel_quick_mode.py tests/test_roi_canvas_editing.py
```

Expected: 新增测试和既有测试全部通过。若原测试断言 quick mode 隐藏 `draw_mode_group`，将该断言改为只验证来源过滤组仍隐藏，并保留 full mode 可见性断言。

- [x] **Step 3: 验证模式信号仍工作**

```bash
.venv/bin/pytest -q tests/test_control_panel_quick_mode.py -k 'secondary_actions or quick_mode or mode_switch'
```

Expected: `调整字幕区域` 仍发出 `edit`，单选项切换仍发出 `rect`/`poly`/`edit`。

### Task 4: 全量回归与人工检查

**Files:**
- No production files beyond Task 3.

- [x] **Step 1: 运行全量测试**

```bash
.venv/bin/pytest -q
```

Expected: 基线 `326 passed, 1 skipped` 或因新增测试增加后的对应通过数量；不得出现失败。

- [x] **Step 2: 做最小 GUI 烟测**

```bash
QT_QPA_PLATFORM=offscreen .venv/bin/python - <<'PY'
from PySide6.QtWidgets import QApplication
from components.control_panel import ControlPanelWidget
app = QApplication.instance() or QApplication([])
panel = ControlPanelWidget()
assert panel.draw_mode_group.isVisibleTo(panel)
assert panel.rect_mode_radio.isVisibleTo(panel)
assert panel.poly_mode_radio.isVisibleTo(panel)
assert panel.edit_mode_radio.isVisibleTo(panel)
panel.shutdown_background_threads(timeout_ms=100)
print('ROI drawing entry smoke test passed')
PY
```

Expected: 输出 `ROI drawing entry smoke test passed`。

- [x] **Step 3: 检查差异**

```bash
git diff --check
git diff -- components/control_panel.py tests/test_control_panel_quick_mode.py
```

Expected: 只有绘制模式可见性和对应测试变化，无数据格式或流水线改动。

- [x] **Step 4: 提交**

```bash
git add components/control_panel.py tests/test_control_panel_quick_mode.py
git commit -m "fix: restore ROI drawing modes in quick panel"
```

提交前确认没有把 `optimization_plan_main.md`、其他用户文件或构建产物加入提交。
