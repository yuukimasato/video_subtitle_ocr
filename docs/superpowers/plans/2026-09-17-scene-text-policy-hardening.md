# 场景文字显示策略加固实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: 按任务逐项执行；每项先写失败测试，再实现，再运行定向测试，最后提交小步 commit。执行者不得跳过基线记录。

**Goal:** 在保持默认 `overlap` 和既有对白字幕不变的前提下，加固四种场景文字显示策略，并完成动态/静态路径的可复现视觉验收。

**Architecture:** 继续以 `core/scene_text_policy.py` 为纯函数核心；动态轨迹和静态生成器提供统一 `PolicyContext`，策略返回带诊断信息的 `PolicyResult`。所有回退显式记录原因，事件保留 layer/policy 元数据。

**Tech Stack:** Python 3.12、pytest、NumPy、OpenCV、ASS/libass、ffmpeg、现有 PySide6 GUI。

**Spec:** `docs/superpowers/specs/2026-09-17-scene-text-policy-audit-and-hardening.md`

## Global Constraints

- 默认 `scene_text_policy=overlap`。
- 不删除或重写既有测试；新增测试必须先失败再实现。
- 不改变对白字幕样式、既有 ROI JSON 兼容性和 ASS 默认事件字段。
- 每个任务结束运行定向测试并创建小步 commit；最终运行全量测试和真实视频验收。
- 任何视觉差异必须保存输入配置、ASS、渲染帧和回退日志。

### Task 0: 建立不可破坏的基线

**Files:**
- Create: `docs/superpowers/evidence/2026-09-17-scene-text-policy-hardening-baseline/manifest.json`
- Create: `docs/superpowers/evidence/2026-09-17-scene-text-policy-hardening-baseline/pytest-output.txt`
- Create: `docs/superpowers/evidence/2026-09-17-scene-text-policy-hardening-baseline/worktree-status.txt`

- [ ] 运行 `git status --short`, `git rev-parse HEAD`, `python --version`, `.venv/bin/pytest -q`。
- [ ] 保存完整输出、测试数量、跳过项、警告数量、HEAD 和工作树状态；明确当前工作树是否干净。
- [ ] 生成关键源文件 SHA-256 清单，避免把脏工作树当成基线。
- [ ] 只提交证据文件，不修改实现。

### Task 1: 为策略 API 增加上下文和诊断契约

**Files:**
- Modify: `core/scene_text_policy.py`
- Test: `tests/test_scene_text_policy.py`

- [ ] 先新增失败测试：`ref_frame` 不等于 `tracks[0]` 时遮罩轨迹以显式参考帧构建；`tracks=[]` 返回明确降级而非 `IndexError`；结果包含 requested/applied mode、notes、diagnostics。
- [ ] 实现最小兼容接口：保留现有函数签名的兼容包装，新增可选 `ref_frame`、`style` 和诊断字段。
- [ ] 对输入行框做有限数、正宽高和边界裁剪校验；无有效行时保持 overlap 原事件。
- [ ] 运行 `pytest tests/test_scene_text_policy.py -q`。
- [ ] 提交 `fix: harden scene text policy context validation`。

### Task 2: 修复动态轨迹归属和样式传递

**Files:**
- Modify: `core/scene_text_policy.py`, `core/motion_ass.py`
- Test: `tests/test_scene_text_policy.py`, `tests/test_motion_ass.py`

- [ ] 先新增失败测试：重复 body 的两行不会错误合并；自定义 style 能出现在 mask/whitespace 事件；不同标点补偿的多行块按行 union 覆盖；长 lost 间隙不会跨段平滑。
- [ ] 使用 `line_idx`/row ID 建立事件归属，body 仅兼容回退；统一把 style 传入 `_mask_events_for_block` 和静态生成器。
- [ ] 将平滑限制在连续 OK run 或时间窗口内。
- [ ] 运行定向测试并检查默认 overlap 事件逐字段不变。
- [ ] 提交 `fix: preserve scene policy event identity and style`。

### Task 3: 加固背景取色与空白带判定

**Files:**
- Modify: `core/scene_text_policy.py`
- Test: `tests/test_scene_text_policy.py`

- [ ] 先新增失败测试：暗底反白字、轻微压缩噪声、彩色纯色、低对比渐变、极少背景像素；断言 confidence、std/robust spread 和回退模式。
- [ ] 保持旧阈值默认兼容；增加局部百分位/Otsu 选项、MAD/IQR 统计和样本数诊断。
- [ ] 候选空白带按面积、宽高、背景均匀性和可排版长度评分；低 confidence 进入既定回退链。
- [ ] 运行 `pytest tests/test_scene_text_policy.py -q`。
- [ ] 提交 `feat: make scene policy background analysis robust`。

### Task 4: 统一静态路径的 ROI 策略语义和读取缓存

**Files:**
- Modify: `core/pipeline_worker.py`, `core/subtitle_generator/generator.py`
- Test: `tests/test_scene_text_policy_gui.py`, 新增 `tests/test_pipeline_scene_text_policy.py`

- [ ] 先新增失败测试：混合 ROI 策略产生逐 ROI 结果或明确错误；CLI 参数覆盖 ROI 配置的优先级固定；混合 Scene/Subtitle 行不丢失；多 ROI 只复用视频读取。
- [ ] 采用逐 ROI 策略上下文；不能拆分时发出可见警告并阻止静默取第一策略。
- [ ] 把分析帧读取抽象为可缓存 reader；按时间邻域生成分析图，保留单帧模式兼容开关。
- [ ] 运行静态专项测试和 GUI/CLI 测试。
- [ ] 提交 `fix: make static scene policy per roi and cache frames`。

### Task 5: 增加透视遮罩的安全边界

**Files:**
- Modify: `core/scene_text_policy.py`
- Test: `tests/test_scene_text_policy.py`, `tests/fixtures/`（如项目已有 fixture 目录）

- [ ] 先新增失败测试：梯形平面在矩形误差超过阈值时不生成虚假“完美”遮罩；退化裁剪框自动 external；轴对齐场景保持原 mask 输出。
- [ ] 短期实现误差检测和可配置降级；仅在实验开关打开时生成逐帧四角 `\p1` 多边形。
- [ ] 运行策略专项测试并检查 ASS 标签合法性。
- [ ] 提交 `fix: guard mask policy against perspective mismatch`。

### Task 6: 完成端到端 ASS 与视觉验收

**Files:**
- Create: `docs/superpowers/evidence/2026-09-17-scene-text-policy-hardening/`
- Modify: `docs/testing.md`（仅补充可复现命令和验收说明）

- [ ] 用 `test/[DMG] 冴えない彼女の育てかた♭ 第07話19.mp4` 和现有 ROI/quad 配置生成 overlap、mask、external、whitespace 四份 ASS。
- [ ] 使用项目现有 ffmpeg/libass 渲染命令，在相同帧号抽帧；保存 ASS、命令、stderr、截图、实际 applied policy 和 diagnostics。
- [ ] 像素/人工检查：mask 原字不透出；external 展示框位置稳定；whitespace 位于候选空白带；overlap 与基线一致；透视或杂色场景按预期降级。
- [ ] 比较单 ROI/多 ROI 读取耗时和读取次数。
- [ ] 提交 `test: add scene text policy render evidence`。

### Task 7: 最终回归与交付检查

**Files:**
- Modify: `CHANGELOG.md`（如项目惯例要求）
- Create: `docs/superpowers/evidence/2026-09-17-scene-text-policy-hardening/final-report.md`

- [ ] 运行 `.venv/bin/pytest -q`，与 Task 0 对比 passed/skipped/warnings。
- [ ] 运行 `ruff check`（按项目现有配置）和必要的 CLI smoke test。
- [ ] 检查默认 overlap 的 ASS diff；只允许预期的新增诊断/策略事件字段。
- [ ] 在 final-report.md 记录修改 commit、测试命令、视觉证据路径、已知边界和未纳入项。
- [ ] 提交 `docs: record scene text policy hardening results`。
