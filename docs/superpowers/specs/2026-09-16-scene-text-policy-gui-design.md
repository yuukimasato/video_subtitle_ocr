# 场景文字显示策略接入完整版 GUI 与主流水线 设计

- 日期:2026-09-16
- 状态:已评审批准(每个 ROI 独立设置)
- 前置:`docs/superpowers/specs/2026-09-16-scene-text-policy-design.md`(策略模式
  已在轨迹管线落地:`core/scene_text_policy.py`)

## 1. 目标

在完整版 GUI 的 ROI 定义面板增加「场景文字显示」下拉框(每 ROI 独立),
主流水线(静态 `\pos` 路径)支持 mask/external/whitespace 三种模式与既有
回退链。对白字幕(BOTTOM/TOP 样式)不受任何影响;默认 `overlap` 时输出
与现版本逐事件一致。

## 2. GUI(components/roi_definition.py + main_window/roi_editing.py)

- `RoiDefinitionWidget` 新增 `scene_text_policy_combo`(QComboBox):
  叠加(默认)/遮罩原文字/外置展示框/空白区放置,currentData 为
  overlap/mask/external/whitespace;tooltip 说明仅作用于场景文字事件、
  不可用时自动回退;
- ROI 条目组装处新增 `roi_entry["scene_text_policy"] = combo.currentData()
  or "overlap"`;选中已绘 ROI 时回填 combo(同 blur/pose 模式);
- ROI 编辑/写回路径保持该键不丢失;
- i18n:新字符串按项目机制补入 `i18n/translations_{en,zh_TW,ja_JP}.py`
  并执行 apply_translations(如环境无 lrelease,记录 .qm 待重编译)。

## 3. 管线接线(core/pipeline_worker.py)

- 收集 `roi_scene_text_policies[f"roi_{idx}"] = str(roi.get(
  "scene_text_policy") or "overlap")`(仅非 overlap 也可,收集全部亦可);
- ROI 几何:`roi_analysis_rects[f"roi_{idx}"] = points 外接矩形`
  (rect 由 [x,y,w,h],poly 由 min/max);
- `merge_rois` 合并模式:取第一个非 overlap 策略,混合时告警;
- 两者作为新构造参数传入 `OCRToASSOptimizer`。

## 4. 静态路径策略应用(core/subtitle_generator + scene_text_policy 扩展)

- `OCRToASSOptimizer` 新构造参数 `roi_scene_text_policies`;
- 应用点:每 ROI 分组并样式化后、`_filter_events` 之前。仅处理
  `location_type == "SCENE"` 的组;
- **分析图**:该 ROI 最大组的中间帧,按 ROI 外接矩形裁剪(每 ROI 仅取
  一帧,VideoCapture seek);行框(视频坐标)减外接框原点 → 平面坐标,
  与 `core/scene_text_policy.py` 现有纯函数对接;
- 新增 `apply_policy_static(...)`(纯函数,同 motion 版 `apply_policy` 的
  静态变体):mask=每块一条 `\an7\pos\p1` 静态矩形(layer 0,取色/杂色
  检查同 motion)+ 原行事件改 layer 1;external=按组合并单条 NoteBox
  事件;whitespace=空白带放置;回退链 whitespace→mask→external 不变;
  事件 dict 携带 `"policy": True` 标记与 `layer`;
- 时间:策略事件沿用所属组的 start/end(_estimate_end_time 同现有路径);
- **豁免**:`_filter_events` 与 `_merge_temporal_near_duplicate_events`
  跳过带 `policy` 标记的事件(与轨迹事件 `Name: motion` 豁免同哲学);
- ASS 头增加 NoteBox 样式(Scene 同字号、BorderStyle=3、
  BackColour &H80000000&、对齐 2);写入器支持事件可选 `layer`(缺省 0)。

## 5. 验收

- 单测:生成器合成数据三种模式(事件结构/layer/豁免)、overlap 回归
  (既有生成器测试不动全过)、GUI 组合框存在性与 roi_data 往返
  (pytest-qt 既有模式)、worker 收集逻辑;
- 真实验收:DMG 视频 `_roi.json` 加 `"scene_text_policy"` 键跑完整主流水线,
  mask 烧录帧重影消除、external 底部框、(whitespace 预期回退)对比截图;
- 全量 pytest 无回归。
