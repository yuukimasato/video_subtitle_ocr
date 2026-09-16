# 需求文档:移动文字轨迹字幕与亮度自适应(视频字幕 OCR 工具)

- 整理日期:2026-09-16;**状态更新 2026-09-17**(正常流程 GUI/CLI 接入已
  落地,见 §3 状态列与 §6/§7 增补;**阶段三四项待做已全部落地**,见 §3
  FR-1/FR-9 及亮度/字形增补行、§6.4、§7、§8)
- 来源会话:移动文字轨迹头脑风暴 → 设计 → 实施 → 验收 → 亮度自适应增补 →
  主流水线集成 → 阶段三收尾(自动检测/\iclip/逐行亮度/字形补偿)
- 本文档为该特性的**需求入口与状态总览**;细节见关联文档:
  - 设计 spec:`docs/superpowers/specs/2026-09-15-motion-trajectory-ass-design.md`
  - 实施计划:`docs/superpowers/plans/2026-09-15-motion-trajectory-ass-plan.md`
  - 验收记录:`docs/superpowers/evidence/2026-09-16-motion-trajectory-ass-acceptance.md`

## 1. 背景与问题

工具现有流水线(ROI → 跳帧采样 OCR → 分组 → ASS)对**画面中移动的文字**
(典型:角色手持手机看邮件,测试样本为
`test/[DMG] 冴えない彼女の育てかた♭ 第07話19.mp4`,1920×1080 @23.976fps,
246 帧)输出质量差,实测 263 条事件中存在:

1. **跳格**:位置容差驱动的分组/合并(`MERGE_POS_TOLERANCE=20px`、
   `SCENE_POS_TOLERANCE_PX=24px`)把移动文字切成大量 0.2~0.4s 的静态
   `\pos` 短段,段间跳位;
2. **重复**:同文本同时间段多条事件;
3. **碎片垃圾**:运动模糊帧被强行 OCR,产出「藤とさ」「SEMY」类碎片读法
   与零时长事件。

用户原始提案(需求起点):检查生成字幕是否有文字移动;如有则**逐帧识别
而非跳帧识别**;获取每一帧的位置轨迹;之后在 `\move`、逐帧 `\pos`、
`\t()` 动画之间选择表达。

## 2. 需求澄清中的技术结论(约束后续设计)

- `\t()` **不能驱动** `\pos/\move`(两者非可动画标签);`\t` 只能动画
  `\frz/\frx/\fry`(旋转)、`\fscx/\fscy`(缩放)、`\1c/\alpha`(颜色/
  透明度)等。位移只有 `\move`(两点线性)或离散 `\pos` 两条路;
- **逐帧"识别"应拆成逐帧"跟踪几何" + 关键帧"识别文本"**:对模糊帧逐帧
  OCR 既贵又是垃圾读法的根源;文本由清晰关键帧投票获得,位置由逐帧单应
  跟踪提供;
- `\move` 仅匀速直线,非线性轨迹按误差预算做**分段 `\move`**。

## 3. 功能需求与状态

| # | 需求 | 决策/口径 | 状态 |
| --- | --- | --- | --- |
| FR-1 | 移动文字检测触发 | 采样 OCR 行心位移超阈值触发密集模式;**两步走**:阶段一/二为手动指定(GUI 画 ROI + 勾选,或 CLI quad),自动检测留后续 | ✅ 已实现(`core/motion_detector.py`:ROI 范围内按 `--motion-auto-stride` 秒采样 OCR,同文本行心串链、最大位移 > 移动门限(默认 24px)→ 产出 quad+时间范围走既有轨迹管线,同动多行并块;CLI `--motion-auto[/-stride/-threshold]`,GUI 控制面板「自动检测移动文字」复选框 → `PipelineWorker(motion_auto_detect=)`,接管成功抑制该 ROI 静态碎片) |
| FR-2 | 逐帧位置轨迹 | 复用 `scene_plane_tracker`(ORB+RANSAC 单应、质量门限、lost 重捕获);输入四边形+时间范围 | ✅ 已实现(接入正常流程,见 FR-8) |
| FR-3 | 文本识别与融合 | 清晰关键帧(Laplacian 方差 top-K,默认 3)上 OCR,统一坐标展开,按行框位置对齐投票 | ✅ 已实现 |
| FR-4 | ASS 标签阶梯 | 单段 `\move` → 分段 `\move`(DP 容差 2px@1080p,最小段 3 帧,段边界时间无缝)→ 旋转/缩放叠 `\t(\frz/\fscx/\fscy)` → 混乱抖动帧级 `\pos` 兜底 | ✅ 已实现 |
| FR-5 | 遮挡/出画处理 | lost 切段不外推,恢复后以真实轨迹重新开始;如实呈现(文字消失) | ✅ 已实现 |
| FR-6 | 渲染误差校验 | libass 烧录抽样帧,字幕中心与期望中心偏差统计(预算:中位 ≤4px、p95 ≤8px),超预算可减半容差复验一次 | ✅ 已实现 |
| FR-7 | 屏幕亮度自适应标签 | 文字平面渐变变暗/变亮时,`\t` 驱动 `\1c`(颜色变暗)**与** `\alpha`(变透明)**两者结合**;**完全忠实跟随,无保底下限**(用户明确选择;最暗时字幕与原字一起接近隐形) | ✅ 已实现(GUI 复选框/CLI `--auto-brightness`);**增补:屏幕局部调暗的背景适配** = `brightness_per_line`(逐行独立测亮度曲线,`core.screen_luma.measure_line_luma_curves`,事件按 line_idx 取所属行;CLI `--brightness-per-line`),整平面曲线作为缺省/回退 |
| FR-8 | GUI/CLI 正常流程接入 | GUI:「写入画面位置标签」勾选 + 平面形状 ROI(多边形/矩形,手绘闭合点自动去重、>4 点取最小外接矩形)走轨迹管线;「亮度自适应」复选框随 pose 联动、`motion_auto_brightness` 持久化。CLI:`--motion-quad[/-file]`、`--auto-brightness`、`--roi-file`(GUI 保存的 ROI json,与 GUI 勾选等价);轨迹事件 `Name=motion` 原样并入,同 ROI 静态事件抑制,失败回退静态路径 | ✅ 已落地(提交 `fb19f82`、`f3bdb68`);其中「自动检测触发」归 FR-1 |
| FR-9 | `\iclip` 手部遮挡蒙版 | 阶段三 | ✅ 已实现(`core/occlusion_mask.py`:展开图 vs 锚定帧灰度差 → 遮挡多边形,与行框相交者以 `\iclip`(+`\t` 等结构动画/保守静态并集)裁剪;`MotionAssConfig.occlusion_*`,CLI `--occlusion-clip`,GUI「遮挡蒙版」复选框随 pose 联动、`motion_occlusion_clip` 持久化) |
| 增补 | 行尾全角标点字形补偿 | 行尾「。，、」等墨迹偏左标点使渲染墨水整体左偏 ≤7px(2026-09-16 验收记录 dx 限制) | ✅ 已实现(`punct_comp_offset_px`:x 右移 min(上限, 0.25×行高),上限 `punct_comp_max_px=7` 随 PlayRes 高度缩放;`\move/\pos` 阶梯与 mask 遮罩块同步平移;默认开,可关) |

## 4. 非功能需求

- 主流水线行为零改动;新模块不依赖 PySide6;轨迹合成器为纯函数可单测;
- 时间一律用浮点秒,仅格式化时取 centisecond;相邻段边界共享同一时间字符串;
- 不新增服务端调用;若引入服务端调用须遵循有界退避(429 限最大重试次数、
  单次/总等待上限,耗尽后优雅降级,见工作区 AGENTS.md);
- 每个任务独立可验证、独立提交;修改前记录测试基线,完成后全量回归。

## 5. 关键决策记录(对话中用户拍板)

1. **方案选型 = A(跟踪+关键帧 OCR+标签阶梯)**:候选 B(逐帧 OCR 直出
   `\pos` 流,成本高、事件爆炸)、C(首末帧线性 `\move`,只修匀速)均否;
2. **触发方式 = 两步走**:先手动 quad 打通全链路并用 DMG 视频验收,再加
   自动检测;
3. **实施方式 = 子代理并行**:4 个并行(融合抽取/关键帧选取/合成器/渲染
   校验)+ 后续 CLI 串接与亮度自适应各 1 个,主会话负责基线、调度、审查、
   真实运行与验收;
4. **亮度表达 = `\1c`+`\alpha` 结合**(候选:仅颜色/仅透明度);
5. **暗场口径 = 完全忠实跟随**,不加可读性保底(可通过
   `brightness_use_alpha=false` 变通为"只变暗不变透明"提高可读性)。

## 6. 验收标准与实测结果

| 指标 | 要求 | 实测(DMG 手机场景) |
| --- | --- | --- |
| 事件数 | 40±20 | 26(13 行 × 2 链) |
| 同文本重复链 / 模糊碎片 / 零时长事件 | 0 | 全部为 0 |
| lost 区间 | 切段不外推 | 85~156 帧无事件,与旧输出空白段互证 |
| 轨迹贴合 | 中位 ≤4px | 运动轴逐行包围盒偏差 ≤1.0px;恒定 x 向偏移 ≤7.2px(行尾全角标点字形度量,已记录为限制) |
| 亮度跟随 | 忠实 | 亮态墨水 248/255;最暗态与原帧差分 0(与原字一起隐形) |
| 回归 | 无 | 全量 350 → 441 → 466 passed, 1 skipped |

**正常流程增补验收(2026-09-17,GUI/CLI 等价闭环)**:

| 指标 | 要求 | 实测 |
| --- | --- | --- |
| GUI 等价 CLI(`--roi-file`,真实保存的手机 ROI + pose/亮度勾选) | 与基线 motion-bright.ass 同效果 | 28 条轨迹事件(14 行 × 2 链,亮度标记 14/28),**零静态残留** |
| 位置一致性 | 同效果 | 逐事件位置偏差 ≤0.5px;`\fs` 数值差异来自两次框选尺寸不同的平面坐标比例,经单应映射渲染物理尺寸一致 |
| 静止/回退 | 静态路径保持 | 未勾选 pose / 点数 <4 → 静态路径;轨迹失败回退该 ROI 静态事件 |
| 回归 | 无 | 全量 616 → **624 passed, 1 skipped** |

已知内容级差异(非管线行为):多识别 1 行「<」返回箭头图标、1 处 OCR 变体
(とりあえす/とりあえず),归 OCR 既有职责。

**阶段三收尾增补验收(2026-09-17,详见验收记录「阶段三收尾」节)**:

| 项 | 实测 |
| --- | --- |
| 行尾标点字形补偿 | 13 行中 7 行行尾「。、」x 端点精确 +7.0px(短行 +6.7=0.25×行高未触顶)、非标点行 dx=0;dx ≤7px 限制消除 |
| FR-9 `\iclip` | 检测运行(21 采样帧),输出与关闭时逐字节一致(DMG 遮挡在 lost 区间,蒙版保守 no-op);初版调暗段误判已修复(亮度归一 + 覆盖率门限 + 3 项防误报单测) |
| FR-1 自动检测 | 无 pose 勾选 + `--motion-auto`:检出 1 个块级移动区域(16 链并块,位移 292px,quad 恰覆盖手机屏幕),自动产出 32 条轨迹事件(174/246 ok,关键帧与手动重合),零静态残留 |
| 逐行亮度 | 单测级:局部调暗合成视频下逐行曲线各自跟随,整平面中位失真;接线/回退/退化覆盖 |
| 回归 | 全量 624 → **661+, 1 skipped**(零回归) |

## 7. 交付物

- 代码:`core/scene_plane_tracker.py`(前置)、`core/keyframe_selector.py`、
  `core/motion_ass.py`、`core/screen_luma.py`(含逐行
  `measure_line_luma_curves`)、`core/occlusion_mask.py`(FR-9)、
  `core/motion_detector.py`(FR-1)、`core/ocr_optimizer.py`
  (模块级融合函数抽取)、`scripts/motion_ass.py`(CLI,含
  `--auto-brightness`/`--brightness-per-line`/`--occlusion-clip`)、
  `scripts/verify_motion_ass.py`;
- **正常流程接入**:`core/pipeline_worker.py`(`collect_motion_roi_specs`
  + `_run_motion_stage`:轨迹阶段/静态抑制/失败回退 + `_auto_detect_motion_specs`
  自动检测)、`cli.py`(`--motion-quad[/-file]`、`--auto-brightness`、
  `--roi-file`、`--motion-auto[/-stride/-threshold]`、
  `--brightness-per-line`、`--occlusion-clip`)、
  `core/subtitle_generator/generator.py`(`motion_events`/`motion_roi_ids`
  参数、Dialogue Name 列)、`main_window/roi_editing.py` +
  `components/roi_definition.py`(pose/亮度/遮挡蒙版复选框、即时写回、
  ROI 组装)、`components/control_panel.py`(「自动检测移动文字」开关)、
  三语 i18n;
- 样例产物(`test/` 工作区):`motion-quad.json`、`motion-trajectory.json`、
  `[DMG] 第07話19-motion.ass`、`[DMG] 第07話19-motion-bright.ass`(基线)、
  `[DMG] 第07話19-gui-flow.ass`(正常流程输出)、
  `[DMG] 第07話19-cli-motion.ass`、`motion-roi-accept.json`、
  `motion-roi-auto.json`、`[DMG] 第07話19-gui-flow-v2.ass`(阶段三验收:
  标点补偿基线)、`[DMG] 第07話19-occlusion.ass`(与 gui-flow-v2 逐字节
  一致)、`[DMG] 第07話19-motion-auto.ass`(自动检测输出);
- 提交:`5fda1de`(spec)→ `6b9371d`(plan)→ `2c0beaa`/`d5070a8`/
  `d44cff7`/`b8656bd`/`eb58617`(特性)→ `1922aa7`(验收)→
  `81520a1`(亮度)/`af6d9ba`(lint)/`99e6f06`(增补归档)→
  `fa2f8c5`(2.6.3 发布)→ `fb19f82`(主流水线 GUI/CLI 接入)→
  `f3bdb68`(ROI 形状归一化 + `--roi-file`)→ `58a7eaa`(标点补偿)→
  `5d6711a`(逐行亮度)→ `810baf9`(`\iclip` 遮挡蒙版)→
  `3a0145c`(自动检测)→ 阶段三 GUI/i18n 与文档归档。

## 8. 已知限制与后续方向

- ~~行尾全角标点使渲染包围盒含空白格,字形整体左偏 ≤7px~~ → 已实现字形
  度量补偿(见 §3 增补行):补偿后 dx 残余 ≈ 无标点行水平(±0.5px);
- ~~屏幕局部调暗的背景适配~~ → 已实现逐行亮度曲线(`brightness_per_line`);
- 亮度基线口径下,`brightness_use_color/use_alpha/tol/baseline_percentile`
  可配置,但无"保底可读性"下限(用户选择忠实);如需保底需新增
  floor 参数;
- 内容级误差(图标误读「血」「<」、「ます/まず」混淆)归 OCR/分类/VLM
  既有职责,本需求不扩权;
- `\iclip` 遮挡蒙版按段端点形状 + libass 线性插值近似(与 `\move` 线性
  运动假设一致);多边形个数在段内变化时退化为保守静态并集(宁多勿漏);
  锚定关键帧本身被遮挡的区间会被当作预期外观(此类场景请换锚定帧);
- 自动检测(FR-1)的采样 OCR 成本与检测范围×采样密度成正比,长视频建议
  以 ROI 圈紧检测范围;同文本 OCR 变体(とりあえす/とりあえず)会分散
  成多条链,位移门限仍可各自命中;
- 阶段二展望中「GUI 四角关键帧修正」「跨切镜身份隔离」仍属后续
  (lost 质量门限已兜底镜头切换)。
