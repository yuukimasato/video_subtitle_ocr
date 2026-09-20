# 字号标定与还原精度优化开发计划（font_size_calibration）

> 文档版本：v1.0（2026-09-21）
> 适用范围：场景文字 / 移动文字轨迹路径的 `\fs` 确定，及衍生逻辑（描边估计、行尾标点补偿、遮罩外扩、折行字号适配）
> 关联文档：`docs/development_plan.md`、`docs/requirements-motion-trajectory-ass.md`

---

## 1. 背景：现行字号方案

项目按字幕类型分两套字号逻辑：

| 路径 | 字号来源 | 位置 |
|---|---|---|
| 对白（BOTTOM/TOP） | 视频高度固定比例（Default 0.06×、Top 0.05×），或用户模板 | `core/subtitle_generator/styling.py:264-271` |
| 场景文字策略（mask/whitespace 回贴原行） | OCR 行框像素高直接作 `\fs` | `core/scene_text_policy.py:1745` |
| 移动文字轨迹 | 跟踪行参考高度 `lt.height` 直接作 `\fs` | `core/motion_ass.py:854` |
| external / whitespace 折行 | `fit_font_size`：`min(base, 带高//行数, 带宽//最长行)`，夹到 `[24, base]` | `core/scene_text_policy.py:610-624` |
| Scene 样式基准字号 | `高度×0.04`，作为折行 base 传入 | `core/subtitle_generator/generator.py:457` |

该方案成立的前提：`PlayResX/Y` = 视频实际分辨率（`styling.py:257-258`），`\fs` 单位与画面像素 1:1，OCR 框高可直写 `\fs`。

## 2. 问题定义

- **P1（核心）em 与墨迹高混用**：`\fs` 是字体 em 高度，OCR 行框高是"墨迹字面高度"（≈ em × k_ink，k_ink < 1）。现行 `fs = 行高` 使替换文字系统性偏小约 (1−k_ink)，思源黑体 CN 的 k_ink 预计 ≈ 0.9。
- **P2 多行行距偏差**：`fit_font_size` 的 `带高//行数` 隐含"每行渲染高 = fs"；libass 实际行距 = (ascender+descender)/upem × fs（思源黑体 CN ≈ 1.4~1.5em），折行块实际渲染高度系统性超出带高估计。
- **P3 宽度启发式粗糙**：`_rendered_width`（`scene_text_policy.py:876-883`）按"全角 1.0×行高、半角 0.5×行高"估渲染宽度，纯 ASCII 行实际渲染宽可达框宽两倍（代码注释已承认），导致遮罩过宽或欠覆盖。
- **非问题（明确不改）**：对白固定比例是有意设计（检测框逐帧抖动，按框定字号会跳变）；对白不需要中值滤波。

## 3. 目标（可验收）

| # | 目标 | 验收标准 |
|---|---|---|
| G1 | 场景/轨迹行 `\fs` 按 k_ink 修正 | 用标定字体渲染验证：替换文字墨迹高与 OCR 行框高偏差 ≤ 5% |
| G2 | 折行带高适配计入行距比 k_line | 多行折行块实测渲染高度与带高偏差 ≤ 8%（修正前系统性超出 ~1.4×） |
| G3 | 渲染宽度估计改用真实字体度量 | 对 ASCII 样本行，`_rendered_width` 估计值与真实渲染宽偏差 ≤ 15%（修正前最差 ~2×） |
| G4 | 衍生逻辑语义一致 | 描边 `\bord`、标点补偿统一改用修正后 fs（em 语义）；遮罩 pad 继续用原始行高（物理墨迹语义）——有单测断言两条语义不混淆 |
| G5 | 无字体文件/标定失败时优雅回退 | 回退系数 1.0（即现状行为），不抛异常、不中断任务链 |

## 4. 非目标（本轮不做）

- 不做逐行渲染反馈闭环（起 libass 试渲、二分搜 fs）——成本过高，作为个别行仍不达标时的后续手段（Backlog）
- 不换 OCR 引擎、不引入 OCR 字符级框（PaddleOCR/RapidOCR 无字号输出；`return_word_box` 字符级框对 CJK 收益小，进 Backlog）
- 不改对白字号逻辑与模板路径
- 不做字体嵌入（`[Fonts]` 段）
- 本方案全程本地计算（字体度量/标定均离线），无服务端调用

## 5. 方案选型记录

| 备选 | 结论 | 理由 |
|---|---|---|
| A. 字体度量标定（渲染实测墨迹高/fs） | **采纳（Phase 1）** | 修改面小、一次标定长期复用 |
| B. 渲染反馈匹配（逐行试渲二分） | 否决（本轮） | 单行成本高；其八成收益可由 D 以 1% 成本拿到 |
| C. OCR 引擎字号输出 | 否决 | PaddleOCR/RapidOCR 无此字段，换引擎不划算；列举的第三方字段来源未证实 |
| D. 真实字体度量替换宽度启发式（fontTools 读 cmap/hmtx） | **采纳（Phase 2）** | 纯 Python、无 GUI 依赖（scene_text_policy 须保持不依赖 PySide6，chunk worker 可用） |

## 6. 技术设计

### 6.1 标定常量模块（新增 `core/font_metrics.py`）

- 常量表按**样式字体名**键控（本轮只需思源黑体 CN 一项；JP/KO/RU 样式路径不逐行写 `\fs`，无需标定）：
  - `k_ink`：墨迹高 / em。标定法：以 `\fs=1000` 渲染代表性字集（如「国漢字永Aa1」），取字面 bbox 高的中位数 / 1000。fontTools 读 glyf/CFF 字形 bbox × 1000/upem 亦可作离线近似，最终以渲染实测为准。
  - `k_line`：libass 行高 / em = (hhea.ascent − hhea.descent) / upem。fontTools 读 hhea；思源黑体 CN 预期 ≈ (1160+320)/1000 = 1.48。
- 查表键 = 替换字体 family；缺字体/缺表项时回退 `k_ink=1.0, k_line=1.0`（G5，行为回到现状）。
- 标定脚本落 `scripts/calibrate_font_metrics.py`（输入字体文件路径，输出建议常量与实测记录，供入库复核）。

### 6.2 写入点修正（Phase 1 主体）

| 写入点 | 现状 | 修改 |
|---|---|---|
| `core/motion_ass.py:854` | `fs_h = int(round(lt.height))` | `fs_h = int(round(lt.height / k_ink))` |
| `core/scene_text_policy.py:1745` | `\fs{round(line_h)}` | `\fs{round(line_h / k_ink)}` |

**语义红线（G4）**：修改后凡表示 **em 尺寸**的量传修正后 fs，凡表示**原始墨迹物理高度**的量保持行高：

- em 语义（传修正后 fs）：`sample_line_style` 的 `fs_px`（描边估计 `0.06×fs`，`core/line_restoration.py:197`）；`punct_comp_offset_px` 的 `fs_px`（`0.25×fs`，`core/motion_ass.py:242-267`）；`_rendered_width` 的字号入参（渲染宽 ∝ fs，Phase 2 后仍成立）。
- 物理墨迹语义（保持行高）：遮罩外扩 `pad = ratio × line_h`（`scene_text_policy.py:998`）；`LineTrack.height` 本身（轨迹几何真值）。
- `fs_px` 传参链同步：`generator.py:519-521`（策略行）与 `styling.py:112-114`（静态 Scene 行）传入修正后 fs，`merge_restoration_tags`/`sample_line_style` 签名不变。

### 6.3 折行行距修正（Phase 1 附带）

`fit_font_size` 高度项改为 `带高 // (行数 × k_line)`；宽度项 Phase 2 替换，Phase 1 保持 `带宽 // 最长行`。夹取区间 `[min_fs, base]` 不变（`base = 高度×0.04`，generator 传参不变）。whitespace 空白带放置同样吃 k_line 修正（目标是"装进带子"，只修行距，不涉及 k_ink）。

### 6.4 真实宽度度量（Phase 2）

- 新增字体 advance 查询（fontTools 读 cmap+hmtx，按 family 缓存）：`rendered_width(text, fs) = Σadvance_units × fs / upem`。
- 替换 `_rendered_width` 的 0.5/1.0 启发式与 `fit_font_size` 的 `带宽 // 最长行` 宽度项；字体缺失回退现启发式。
- ASCII 行（邮件/UI 类场景文字）收益最大；宽度估计同时服务遮罩外扩（避免欠覆盖露出原字）与折行。

## 7. 兼容性与测试影响

- **输出会变（有意的行为变更，非回归）**：场景/轨迹行 `\fs` 数值、折行块字号、遮罩宽度（Phase 2）。对白与模板路径逐字节不变。
- 需同步更新的测试（现有断言硬编码 `\fs` 值）：`tests/test_motion_ass.py`（`fs50` 系列断言，fixture 行高 50 → 修正后新值）、`tests/test_scene_text_policy.py`、`tests/test_text_alignment.py`（`\fs16`/`\fs20`）、`tests/test_motion_ass_cli.py`。`tests/test_line_restoration.py:202-204` 的 tags 透传断言不受影响。
- 常量须可注入（函数参数默认值取自 `font_metrics` 查表），测试可传 `k_ink=1.0` 固定旧行为做对照。
- 渲染器假设：libass/VSFilter，`ScaledBorderAndShadow: yes`（`styling.py:259`）——`\bord` 随 `\fs` 缩放，描边公式语义不变。

## 8. 实施步骤与验收

| 阶段 | 内容 | 验收 |
|---|---|---|
| P1a | `core/font_metrics.py` + `scripts/calibrate_font_metrics.py`，产出思源黑体 CN 的 k_ink/k_line 实测值入表 | 标定脚本对样例字体输出记录完整；缺字体回退路径有单测 |
| P1b | 两个写入点 + `fs_px` 传参链 + `fit_font_size` 行距项修正，测试更新 | G1/G2/G4 达标；全量测试绿 |
| P1c | `test_run/` 样本视频对比验证：修正前后遮罩盖住率、字形悬出率、还原行高偏差 | 出对比记录（前后 ASS + 截图），G1 指标实测达标 |
| P2 | fontTools advance 度量替换宽度启发式 | G3 达标；ASCII 样本行遮罩宽度对比记录 |

## 9. 风险与回滚

- **标定值偏差**：k_ink 取中位数、多字集采样；实测验证（P1c）兜底。风险残留时个别行可走 Backlog 的逐行闭环。
- **字体文件缺失**（用户未装思源黑体/打包环境差异）：查表回退 1.0，行为与现状一致，无异常路径。
- **回滚**：常量表置 `k_ink=1.0, k_line=1.0` 即整体回到现状输出；Phase 2 独立开关（回退启发式），两阶段可单独回滚。

## 10. 未决问题

- whitespace 空白带与 NoteBox 的 base 字号是否也吃 k_line（当前方案：空白带吃、NoteBox 不吃——NoteBox 是展示框非还原目标，保持现状）。
- ASCII 行是否引入 k_ink 的 cap-height/x-height 细分（依赖字符级框，Backlog）。
