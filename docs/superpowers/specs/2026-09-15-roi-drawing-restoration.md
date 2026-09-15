# ROI 绘制入口恢复设计

## 目标

恢复 2.5.0 主线 `fe42f75` 中用户可见的 ROI 绘制入口，使当前 GUI 在默认简洁界面也能创建矩形 ROI 和多边形 ROI，并继续支持已有 ROI 的编辑。

## 代码事实

当前 `components/video_display.py` 仍实现：

- `rect` 模式：鼠标拖动绘制矩形。
- `poly` 模式：逐点点击，完成至少三个顶点后闭合多边形。
- `edit` 模式：矩形整体移动/八点缩放，多边形整体移动/顶点调整。

当前 `main_window/roi_editing.py` 仍实现：

- 从画布读取矩形 `[x, y, w, h]`。
- 从画布读取多边形 `[[x1, y1], ...]`。
- 创建 ROI 条目、写入起止帧、预处理、来源策略和姿态信息。
- 更新、删除、保存和加载 ROI。

回归原因在 `components/control_panel.py:set_quick_mode()`：默认 quick mode 将 `draw_mode_group` 设置为不可见。`调整字幕区域` 按钮只能切换到 `edit`，用户无法直接选择矩形或多边形绘制。

## 设计决策

采用最小兼容修复：

1. quick mode 保持现有简洁设置隐藏策略。
2. `draw_mode_group` 在 quick mode 中保持可见，显示矩形、多边形、编辑三个选项。
3. 不改 ROI 数据格式、不改识别流水线、不改画布坐标映射。
4. “调整字幕区域”继续切换到 `edit` 模式。
5. 保持 full mode 行为不变。

## 验收标准

- 新建 `ControlPanelWidget` 后，绘制模式组可见。
- quick mode 下矩形、多边形和编辑单选项可见且可触发 `draw_mode_changed`。
- full mode 下来源过滤等高级组仍按现有逻辑显示。
- 现有 ROI 画布几何测试全部通过。
- 全量测试通过，且不改变 `get_pipeline_options()` 字段集合。
