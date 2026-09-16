# 场景文字显示策略现状审查与补缺设计

- 日期：2026-09-17
- 范围：轨迹管线、主流水线静态路径、CLI、GUI、ASS 写出与真实烧录验收
- 目标：在不改变默认 `overlap` 行为的前提下，提高 `mask / external / whitespace` 的稳定性、可诊断性和跨路径一致性
- 本文只做审查和设计，不修改实现代码

## 1. 现状结论

四种策略已经落地，当前不是从零实现：

- `core/scene_text_policy.py` 已提供四种模式、段落并块、背景取色、空白带检测、CJK 折行、字号适配，以及 `whitespace → mask → external` 回退。
- `scripts/motion_ass.py` 已接入 `--scene-text-policy`、轨迹遮罩、Layer 0/1、`\move`、`\t` 和亮度联动。
- `core/subtitle_generator/generator.py` 已接入静态 `\pos` 路径；`pipeline_worker.py`、GUI ROI 编辑和 CLI 已保存/读取策略。
- ASS 头已有 `NoteBox`，写入器支持可选 `layer`；策略事件带有豁免标记，避免后续普通事件合并破坏遮罩层。
- 现有测试在当前工作树执行结果为 **666 passed, 1 skipped, 11 warnings**（Python 3.12.3，pytest 8.4.2，约 69 秒）。历史证据目录中的 331 测试属于更早且带脏工作树的基线，不能替代当前基线。

## 2. 已确认的风险与补缺

### P0：必须在实现前先补测试并修复

1. **轨迹参考帧不显式**：`mask/whitespace` 内部使用 `tracks[0].frame_num` 重建轨迹；OCR 锚定帧、首个 OK 帧和 `tracks[0]` 不一致时可能产生整体偏移。接口应显式接收 `ref_frame`，并使用 OCR 锚定帧。
2. **重复文本导致 external 错配**：按 `body` 匹配事件时，同文案多次出现会合并错误时间跨度。应优先使用 `line_idx`/行 ID，文本匹配只作兼容回退。
3. **空轨迹与坏框未形成稳定降级**：`rows` 非空而 `tracks=[]` 时可能 `IndexError`；NaN、反向、零面积 OCR 框可能污染轨迹。入口应验证并返回可诊断的降级结果。
4. **样式参数被硬编码**：动态和静态 mask 生成器部分路径固定 `Scene`，调用者传入自定义 style 无效。必须统一 style 传递并加回归测试。
5. **混合 ROI 策略语义不明确**：`merge_rois=True` 时不同 ROI 的策略可能被强行取第一项。应明确拒绝冲突、逐 ROI 处理，或发出强警告并记录最终策略；推荐逐 ROI 处理，无法拆分时显式失败而非静默覆盖。

### P1：建议在同一轮加固

6. **背景取色对渐变/彩色 UI 敏感**：最大通道标准差阈值 18 容易把压缩噪声判为杂色。保留现有阈值兼容性，同时增加鲁棒 MAD/IQR、采样像素数量和置信度；默认仍按旧规则，新增模式可配置启用。
7. **空白检测使用全局阈值**：暗底、反白字、渐变背景可能误判墨迹。建议支持局部百分位/Otsu，并用候选带面积、背景均匀性、可排版宽高计算 confidence。
8. **遮罩是轴对齐矩形**：旋转/缩放可跟随，但透视梯形无法完全覆盖。短期明确边界并在视觉验收中加入透视样例；后续可增加逐帧四角 `\p1` 多边形事件，不能用矩形近似时自动降级。
9. **多行标点补偿只取块级最大值**：不同文本行的左右补偿可能造成某行遮罩偏移或过宽。应按行补偿后求 union，保持每行覆盖。
10. **长 lost 间隙上的平滑**：平滑窗口可能跨越长遮挡连接两侧姿态。应按连续 OK run 或时间距离限制窗口。
11. **静态路径重复解码视频**：每 ROI 独立 `VideoCapture`/seek，多个 ROI 成本线性增加。应复用 reader 或按帧缓存。
12. **静态分析帧代表性不足**：只取单个最高亮度/中间帧，动态背景可能取色不代表实际显示区。建议时间邻域多帧中位合成，或按策略事件组取样。

## 3. 推荐架构

保留一个纯函数策略核心，动态和静态路径只负责提供上下文：

```text
PolicyContext {
  rows: [(row_id, text, box)]
  events: ASS event dicts
  plane_image(s): ndarray / optional multi-frame sample
  tracks: trajectory sequence / optional for static
  anchor_frame: int
  analysis_box: optional video rectangle
  style: text style name
  config: SceneTextPolicyConfig
}
        |
        v
apply_policy(context) -> PolicyResult
PolicyResult {
  events, requested_mode, applied_mode, notes, diagnostics
}
```

`PolicyResult` 应携带每块的背景 std、候选空白带、回退原因、参考帧和输入有效性统计，便于 GUI 日志和证据文件复核。`overlap` 仍直接返回原事件，保证字节级/字段级回归。

回退规则保持：

- `overlap` 永不回退；
- `whitespace` 无足够宽高或 confidence 不足 → `mask`；
- `mask` 背景置信度不足、透视误差超限或退化框 → `external`；
- `external` 仅在文本无法排版时报告不可用，不再静默生成越界事件。

## 4. 兼容性边界

- 默认配置、默认 `overlap`、既有对白字幕样式和已有 CLI 输出必须保持不变。
- 新字段只追加，不改变既有 ROI JSON 的读取；未知策略要在 CLI/GUI 入口明确报错。
- 纯色/近纯色平面适合 `mask`；纹理、渐变、复杂背景默认优先 `external`。
- 透视遮罩在矩形误差不可接受时必须降级或进入实验开关，不能声称 ASS 矩形能完成背景重建。

## 5. 验收标准

1. 全量 pytest 在改动前后均通过；跳过项和警告数量有记录。
2. 默认 `overlap` 的动态、静态事件结构与基线一致。
3. 新增单测覆盖参考帧、重复文本、空轨迹、非法框、自定义 style、混合 ROI、边界裁剪、暗底/渐变和彩色背景、长 lost 间隙。
4. 使用 DMG 测试视频分别生成四种策略 ASS，并用 ffmpeg/libass（或项目既有渲染工具）抽取相同帧：确认 mask 无重影、external 在展示框内、whitespace 落在空白带、overlap 保持原效果；记录回退原因和截图。
5. 性能验收至少比较单 ROI 与多 ROI 的视频读取次数/耗时，确认缓存没有改变结果。
