# 2.5.0 一键使用控制面板实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [x]`) syntax for tracking.

**Goal:** 在不删除高级能力、不改变 OCR 默认结果的前提下，把 GUI 默认操作收敛为“加载视频 → 自动检测 ROI → 一键识别并导出”。

**Architecture:** 保留 `ControlPanelWidget.get_pipeline_options()` 和现有信号接口，优先通过控件分组、可见性和默认值实现 quick mode。高级设置继续存在但默认折叠；UI 文案从实现术语改为用户任务术语。核心流水线不改行为，只调整传参和展示层。

**Tech Stack:** Python 3.12、PySide6、pytest、现有 Qt signal/slot 结构。

**Spec:** `optimization_plan_2.5.0.md` 第十节“精简控制按钮和设置，支持一键使用”。

## Global Constraints

- 基线分支是当前 `main`，版本 2.5.0；不得直接修改或重写基线提交。
- 现有 `get_pipeline_options()` 返回字段和已有 signal 名称必须保持兼容。
- 不删除 OCR 引擎、LLM、颜色门控、分片并行、调试和 ROI 编辑能力，只改变默认可见性和入口。
- 默认 OCR 结果策略保持：自动引擎、自动模型、内存模式开启、来源过滤关闭、分片并行自动。
- 每个任务先写失败测试，再实现，再运行针对性测试，最后提交独立 commit。
- 全量回归命令固定为 `.venv/bin/pytest -q`；基线当前为 `287 passed, 1 skipped`。
- 未经验证不得修改已有测试断言来适配实现。

## 文件边界

- Modify: `components/control_panel.py`：控件分组、quick mode、条件显示、文案和选项映射。
- Modify: `main_window/window.py`、`main_window/pipeline_control.py`：只在需要时接入 quick mode 状态和主按钮文案，保持现有信号流程。
- Create: `tests/test_control_panel_quick_mode.py`：UI 默认状态、选项映射和可见性测试。
- Modify: `i18n/app_zh_CN.ts`、`i18n/app_ja_JP.ts`：新增或修改文案后同步翻译源；不要手工修改 `.qm` 二进制文件。

### Task 1: 建立基线和 UI 行为测试

**Files:**
- Create: `tests/test_control_panel_quick_mode.py`
- No production changes.

**Interfaces:**
- Consumes: `ControlPanelWidget`, `get_pipeline_options()`。
- Produces: 后续任务必须满足的默认状态和兼容性断言。

- [x] **Step 1: 保存基线信息**

```bash
git status --short
git log -1 --oneline
.venv/bin/pytest -q
```

Expected: 工作区只有已知未跟踪的优化方案文件；测试为 `287 passed, 1 skipped` 或记录环境差异。

- [x] **Step 2: 编写失败测试**

覆盖以下行为：实例化后主按钮存在且可用；高级组默认折叠；LLM 组默认折叠；默认选项为自动引擎、自动模型、来源过滤关闭、自动 ROI 开启；`get_pipeline_options()` 仍返回原字段。

- [x] **Step 3: 运行针对性测试确认失败**

```bash
.venv/bin/pytest -q tests/test_control_panel_quick_mode.py
```

Expected: 新增的 quick mode 断言失败，现有测试不因本任务改变。

- [x] **Step 4: Commit**

```bash
git add tests/test_control_panel_quick_mode.py
git commit -m "test: define one-click control panel baseline"
```

### Task 2: 增加 quick mode 和默认首屏

**Files:**
- Modify: `components/control_panel.py`
- Test: `tests/test_control_panel_quick_mode.py`

**Interfaces:**
- Consumes: 现有 `ControlPanelWidget` 构造流程。
- Produces: `set_quick_mode(enabled: bool) -> None`；默认 `quick_mode=True`。

- [x] **Step 1: 实现 `set_quick_mode`**

将样式模板、语言和主操作保留在默认视图；将 OCR 引擎详情、来源分类细项、绘制模式细项和高级选项放入可折叠或条件显示容器。保留原控件对象和 signal 连接，不复制第二套选项状态。

- [x] **Step 2: 保持默认值不变**

确保默认值仍为：`ocr_engine=auto`、`model_tier=auto`、`in_memory=True`、`source_filter=False`、`auto_roi_on_load=True`、`chunk_workers=0`。

- [x] **Step 3: 运行测试**

```bash
.venv/bin/pytest -q tests/test_control_panel_quick_mode.py
```

Expected: Task 1 的 UI 默认状态测试通过。

- [x] **Step 4: Commit**

```bash
git add components/control_panel.py tests/test_control_panel_quick_mode.py
git commit -m "feat: add compact quick mode to control panel"
```

### Task 3: 收敛按钮和用户文案

**Files:**
- Modify: `components/control_panel.py`
- Modify: `i18n/app_zh_CN.ts`, `i18n/app_ja_JP.ts`
- Test: `tests/test_control_panel_quick_mode.py`

**Interfaces:**
- Consumes: 现有 `run_pipeline_requested`、`browse_template_requested`、`auto_detection_requested` 信号。
- Produces: 主按钮文案“开始识别并导出”；高级操作仍触发原有信号。

- [x] **Step 1: 将高频操作保留为主按钮和两个次按钮**

主操作使用“开始识别并导出”；次操作使用“调整字幕区域”和“重新检测”。“检测可用引擎”“刷新模型列表”“预览检测效果”移入高级区或在条件满足时显示。

- [x] **Step 2: 合并来源过滤选项**

增加一个用户向的组合框，选项为“只保留字幕”“字幕和画面文字”“保留全部文字”，内部映射到原有 `keep_overlay/keep_scene/keep_unknown` 字段，保证下游接口不变。

- [x] **Step 3: 更新翻译源并运行文案测试**

测试必须断言默认界面不显示 `进程分片`、`颜色门控` 等实现术语，但高级模式仍能找到对应控件。

- [x] **Step 4: Commit**

```bash
git add components/control_panel.py i18n/app_zh_CN.ts i18n/app_ja_JP.ts tests/test_control_panel_quick_mode.py
git commit -m "feat: simplify control panel actions and wording"
```

### Task 4: 接入“恢复完整设置”和执行状态

**Files:**
- Modify: `components/control_panel.py`
- Modify: `main_window/window.py`, `main_window/pipeline_control.py`
- Test: `tests/test_control_panel_quick_mode.py`, existing pipeline control tests

**Interfaces:**
- Consumes: `set_quick_mode(bool)`、现有扫描启动和完成信号。
- Produces: 用户可从 quick mode 进入完整设置；运行期间主按钮禁用并显示当前阶段，完成后恢复。

- [x] **Step 1: 增加“完整设置”入口**

点击后调用 `set_quick_mode(False)`，不重建控件、不清空用户设置。增加“简洁界面”入口可返回 quick mode。

- [x] **Step 2: 接入执行状态**

扫描开始时禁用主按钮和会改变输入的控件；扫描完成、失败或取消时统一恢复。不得新增第二个线程或第二条 pipeline 路径。

- [x] **Step 3: 运行回归测试**

```bash
.venv/bin/pytest -q tests/test_control_panel_quick_mode.py tests/test_pipeline_progress_dialog_dismiss_race.py
```

- [x] **Step 4: Commit**

```bash
git add components/control_panel.py main_window/window.py main_window/pipeline_control.py tests/test_control_panel_quick_mode.py
git commit -m "feat: expose full settings and unified execution state"
```

### Task 5: 真实 GUI 冒烟和全量回归

**Files:**
- Modify only if verification exposes a real regression.
- Test: existing suite plus `tests/test_control_panel_quick_mode.py`.

**Interfaces:**
- Consumes: Tasks 1-4 的完整 UI。
- Produces: 可审查的测试记录和性能/交互验收结果。

- [x] **Step 1: 运行全量测试**

```bash
.venv/bin/pytest -q
```

Expected: 所有原有测试通过；新增测试通过；若数量变化，记录具体原因。

- [x] **Step 2: 运行 GUI 冒烟检查**

验证：加载视频后自动检测 ROI；默认首屏可直接点击主按钮；可以进入完整设置；启用 LLM 后才显示 provider/API/model；启用颜色门控后才显示预览按钮；取消和失败后控件恢复。

- [x] **Step 3: 检查接口兼容性**

```bash
git diff HEAD~4 -- components/control_panel.py main_window/window.py main_window/pipeline_control.py
```

确认没有删除原有 `get_pipeline_options()` 字段、信号、线程清理逻辑和高级设置功能。

- [x] **Step 4: Commit verification record**

```bash
git add docs/superpowers/plans/2026-09-14-one-click-control-panel.md
git commit -m "test: verify one-click control panel rollout"
```

## 验收标准

- 新用户无需理解 OCR 引擎、worker、颜色门控或 LLM provider 即可完成一次识别。
- 默认操作路径不超过：选择视频、确认 ROI、点击主按钮。
- 高级设置仍完整可用，且 `get_pipeline_options()` 输出兼容现有 pipeline。
- LLM、颜色门控和模型刷新按钮按条件显示。
- 扫描完成、失败和取消后 UI 状态一致恢复。
- 全量回归测试通过，且无基线测试被删除或放宽。

## 执行方式

建议使用子智能体逐任务执行：每个任务由一个新 agent 完成，完成后由主 agent 检查 diff、运行该任务测试，再派发下一任务。任何测试失败先修复或回退当前任务，不进入下一阶段。

## 执行记录（2026-09-14）

- Task 1–4 按计划逐任务实施，各自独立提交：`06fe390` / `75f43e8` / `a3ae4a3` / `128f1cc`。
- 基线说明：本次会话首次全量运行在未设置 `QT_QPA_PLATFORM=offscreen` 时被 GUI 测试挂起（环境问题，与本次改动无关）。设置离屏平台后全量测试约 50 秒完成。
- Task 5 全量回归：`304 passed, 1 skipped`（约 50.6s）。相对计划基线 `287 passed, 1 skipped` 增加 17 个用例，全部来自新增的 `tests/test_control_panel_quick_mode.py`；原有 288 个用例无一删除或放宽（`git diff HEAD~4 -- tests/` 仅新增该文件）。
- Task 5 GUI 冒烟（offscreen + `benchmarks/test_video_subtitle.mp4`）：20/20 通过，覆盖加载视频后自动检测 ROI、默认首屏直接可点主按钮、完整设置/简洁界面互切、LLM 凭据区与刷新按钮条件显示、颜色门控预览按钮条件显示、运行锁定与取消/完成后恢复、`get_pipeline_options()` 字段完整且默认引擎为 auto。
- 接口兼容性：信号声明、`get_pipeline_options()` 返回字段、线程清理逻辑（`shutdown_background_threads`/`closeEvent` 等）在 diff 中零删改；高级设置（引擎详情、来源过滤、绘制模式、颜色门控、LLM、进程分片）全部保留，仅默认可见性收敛。
- i18n：`pyside6-lupdate` + `pyside6-lrelease` 同步 zh_CN/ja_JP 翻译源并重建 `.qm`；新增文案的 ja 译文已填（qm 有效翻译 zh 87 条 / ja 139 条）。
