# 场景文字显示策略(mask / external / whitespace)设计

- 日期:2026-09-16
- 状态:已评审批准(先轨迹管线;三新模式全做;自动回退链)
- 前置:`docs/superpowers/specs/2026-09-15-motion-trajectory-ass-design.md`(轨迹字幕、
  亮度自适应均已落地)、`core/motion_ass.py`、`scripts/motion_ass.py`

## 1. 问题

场景文字(手机屏幕/信件/招牌)的识别字幕锚定在原文字位置(`\an5\pos` / 轨迹
`\move`),替换字体与原排版的字宽、字重不可能一致,原字从字幕笔画缝隙透出,
形成**双重曝光式重影**(DMG 验收烧录帧已证实)。这是"两层字叠加"的范式问题,
位置精度无法解决。

## 2. 目标 / 非目标

### 目标(本设计)

轨迹管线(`scripts/motion_ass.py`)新增 `--scene-text-policy
overlap|mask|external|whitespace`(默认 `overlap`,不改变任何既有输出),
外加所选模式不可用时的自动回退链。

### 非目标

- 主流水线(多边形 ROI 静态 `\pos` 路径)接入——第二步复用同一模式生成器;
- ASS 渲染器能力边界外的方案(模糊填充、背景重建/原字擦除——渲染器无法
  采样视频,纯色遮罩是 ASS 内的最优解);
- 自动检测触发、GUI 入口(既有阶段二项,不变)。

## 3. 四种模式

### 3.1 overlap(默认,现状)

识别文本直接叠加在原文字上方,适合对照阅读;零改动。

### 3.2 mask(`\p` 矢量遮罩)

低层遮罩盖住原文字 + 高层识别文本:

- **段落块合并**:垂直间距 < `block_vgap_ratio`(0.35)×平均行高且水平范围
  重叠的相邻行框合并为块(邮件正文 10 行 → 一个块),四周外扩
  `mask_pad_ratio`(0.12)×行高并裁到平面边界——整段一次盖住,行距缝隙不露字;
- **轨迹复用**:块角点经 `build_line_tracks` 同款单应映射得逐帧轨迹;遮罩事件
  `{\an7\p1\move(...)\t(\frz..\fscx..\fscy..)}m 0 0 l w 0 w h 0 h{\p0}`,
  平移/旋转/缩放与画面同步;遮罩用自己的 DP 分段(纯色块,无需与文本事件对齐);
- **取色**:统一坐标展开图上,块区域内**剔除墨水像素**(低于区域中位灰
  `ink_drop_delta`=40)后取中位色 → `\1c&HBBGGRR&`;
- **调暗联动**:`brightness_tag_chain` 扩展可选基色参数(给定 BGR 时按通道
  缩放,缺省保持灰色行为)——暗屏时遮罩与字幕一起变暗;遮罩不用 `\alpha`;
- **分层**:遮罩 layer 0,文本 layer 1(事件 dict 新增可选 `layer`,写入器
  默认 0,不影响既有输出)。

边界:纯色遮罩在纯色/近纯色 UI(屏幕、文档)上无痕;花纹/渐变背景上呈
"补丁"感——由回退链规避,非实现缺陷。

### 3.3 external(区域外 + 展示框)

- 识别文本挪出原区域,置于底边距居中(`\an2\pos(W/2, H-external_margin)`,
  `external_pos` 可选 top;`external_margin`=40);
- 展示框用专用样式 **`NoteBox`**(Scene 同字号,`BorderStyle=3`,
  `BackColour &H80000000&` 半透明黑)——框宽随文本自适应,零坐标计算,
  优于 `\p` 手画;
- 按块合并为单事件(块内各行 `\N` 连接,按屏宽−2×边距折行,CJK 规则:
  行首禁则字符 」。，、不下头),时间跨度取块内行的时间并集;
- **多块同时可见**(时间跨度重叠,如标题栏+发件人+正文):合并为**一条**
  外部事件,行序保持画面自上而下;总行数过高时按可用高度缩小字号
  (下限 24px),避免多条事件在底带互相叠罗汉。whitespace 模式同理:
  全部块文本合并为一个文本块放入空白带。

代价(明示):文字脱离物体、不随动——与常规翻译字幕惯例一致。

### 3.4 whitespace(原文字空白区)

- 统一坐标展开图二值化墨迹(低于背景估计 `ink_drop_delta` 判墨)并膨胀 →
  行占用剖面 → 在块之间的空隙/首块上方/末块下方找候选带:高度 ≥
  `ws_min_lines`(2)×行高、宽度 ≥ 60% 平面宽;
- 取能容纳折行后文本的最大候选带 → 文本按带宽折行、字号按
  min(带高÷行数, 带宽÷最长行) 适配,作为"合成行框"直接喂
  `build_line_tracks` → 轨迹、亮度标签零成本复用;
- 无合适带 → 触发回退。

## 4. 回退链

所选模式不可用时自动降级,顺序 **whitespace → mask → external**:

- whitespace:无候选带;
- mask:块内非墨水像素通道标准差 > `bg_max_std`(18)——背景杂色,纯色
  补丁观感突兀;
- external:总可落地(仅当屏宽放不下最长折行——实际不发生)。

`overlap` 不参与回退。降级在事件里以 `Name: motion` + 配置回写日志说明。

## 5. 配置

| 键 | 默认 | 说明 |
| --- | --- | --- |
| `policy_mask_pad_ratio` | 0.12 | 遮罩外扩(×行高) |
| `policy_block_vgap_ratio` | 0.35 | 并块的垂直间距阈值(×行高) |
| `policy_bg_max_std` | 18.0 | 遮罩降级的背景通道标准差上限 |
| `policy_external_pos` | "bottom" | external 位置 bottom/top |
| `policy_external_margin` | 40 | external 边距(px) |
| `policy_ws_min_lines` | 2 | 空白带最小高度(行) |
| `policy_ws_min_width_ratio` | 0.6 | 空白带最小宽度(×平面宽) |

## 6. 模块与验收

- 新增 `core/scene_text_policy.py`(纯函数:取色/并块/空白检测/折行/模式
  事件生成,不 import 视频I/O 与 PySide6);`motion_ass.py` 仅扩展
  `brightness_tag_chain` 基色参数;写入器支持 layer;
- 回归:默认 `overlap` 输出与现版本逐事件一致(全部既有 CLI 测试不动全过);
- DMG 三模式验收:mask 烧录帧原字被遮、译文清晰(对比 overlap 重影);
  whitespace 文本落于下半空白带并随轨迹移动;external 底带框内显示;
  截图入 evidence;全量 pytest 无回归。
