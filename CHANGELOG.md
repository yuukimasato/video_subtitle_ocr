# 更新日志（Changelog）

本项目遵循 [语义化版本](https://semver.org/lang/zh-CN/) 风格的 `主.次.修订` 版本号。
各版本发布前的完整测试记录见 [docs/testing.md](docs/testing.md)，
打包与发布流程见 [docs/packaging.md](docs/packaging.md) 的“版本发布检查清单”。

## 未发布

### 新增

- **移动文字轨迹管线接入项目 CLI 与主流水线 GUI（阶段二：pose 绑定）**：
  2.6.3 的轨迹字幕此前只能手动跑 `scripts/motion_ass.py`，现与 GUI
  「写入画面位置标签」复选框绑定——**四点多边形 ROI + 勾选 pose** 即走
  轨迹管线（`collect_motion_roi_specs` 收集 → `PipelineWorker` 内调
  `scripts/motion_ass.build_motion_events`：逐帧平面跟踪 → 清晰关键帧
  OCR → 位置投票融合 → `\move`/`\t` 合成），轨迹事件以 `Name=motion`
  在噪声过滤/时间合并/LLM 润色之后原样并入输出（逐字保留，不参与重写），
  同一 ROI 的静态事件被抑制避免双份文本；跟踪/OCR 失败自动回退该 ROI 的
  静态 pose 路径（告警留痕，不中断任务链），手绘多边形顶点顺序（顺/逆
  时针）自动纠正。GUI 新增「亮度自适应（轨迹字幕跟随屏幕明暗）」复选框
  （随 pose 联动启用、`motion_auto_brightness` 键随 ROI json 持久化，
  即 `--auto-brightness` 的 GUI 入口）；项目 CLI 新增
  `--motion-quad "x1,y1 … x4,y4[@start-end]"`（可重复，秒区间与 `--roi`
  同语法）、`--motion-quad-file`、`--auto-brightness`，轨迹事件并入同一
  输出 .ass。生成器新增 `motion_events`/`motion_roi_ids` 参数与 Dialogue
  Name 列输出（缺省空串，既有输出逐字节不变）；轨迹事件引用 NoteBox
  样式时头部按需追加样式行。三语 i18n 补齐。单元测试 589 → **616**
  （新增 `tests/test_motion_integration.py` 27 项）。
- **轨迹 ROI 形状归一化 + `--roi-file`（GUI 工作流等价闭环）**：
  `collect_motion_roi_specs` 不再要求多边形恰 4 个顶点——GUI 手绘四边形
  会把「回到起点」的闭合点击也存进 points（5 点），此前轨迹模式永远不
  会被触发；现自动去重闭合点（末点≈首点，容差 max(4px, 对角线 1%)），
  仍多于 4 点取最小外接矩形四角，矩形 ROI（points=[x,y,w,h]）展开为四
  角，即「任意平面形状 + 勾选 pose」都走轨迹管线（<4 点/未勾选仍走静
  态）。项目 CLI 新增 `--roi-file`：直接载入 GUI 保存的 ROI json
  （{"rois":[…]} 或裸列表），per-ROI 标志（write_pose_tags /
  motion_auto_brightness / scene_text_policy）原样生效——CLI 运行与 GUI
  勾选完全等价；该路径产出的轨迹事件同样登记 `motion_roi_ids` 抑制对应
  ROI 的静态事件（失败回退静态）。验收：用真实保存的手机屏幕 ROI（勾选
  pose+亮度）经 CLI 跑出 28 条轨迹事件（亮度标记 14/28、零静态残留），
  与基线 motion-bright.ass 逐事件同构、位置偏差 ≤0.5px（\fs 数值差异来
  自四边形尺寸不同的平面坐标比例，经单应映射后渲染物理尺寸一致）。单元
  测试 616 → **624**。
- **移动文字轨迹「阶段三」四项收尾全部落地**（需求文档 FR 表待做清零）：
  1. **FR-1 自动检测触发**：`core/motion_detector.py` 在 ROI 外接矩形内按
     `--motion-auto-stride`（默认 0.5s）采样 OCR，同文本行心按时间串链
     （跳变超 max(48px, 2.5×行高) 断链），链内最大位移超移动门限（默认
     24px，与主流水线同源）即产出「quad + 时间范围」（minAreaRect 外扩
     0.5×行高；同动多行并块供跟踪取更多特征），自动走既有轨迹管线并抑制
     该 ROI 静态碎片。项目 CLI 新增 `--motion-auto[/-stride/-threshold]`；
     GUI 控制面板新增「自动检测移动文字（轨迹字幕）」复选框 →
     `PipelineWorker(motion_auto_detect=)`（无手动 pose ROI 时生效）。
  2. **FR-9 `\iclip` 手部遮挡蒙版**：`core/occlusion_mask.py` 把采样 ok 帧
     展开到平面坐标、与锚定关键帧差分得遮挡多边形（亮度归一 + 覆盖率
     门限防把「屏幕调暗/切镜」误判为遮挡），与行框相交达标者以
     `\iclip` 追加到事件——段端点多边形等结构时 `\t` 动画插值（与
     `\move` 线性假设一致），个数变化时退化为保守静态并集（宁多勿漏）；
     lost 大遮挡仍由切段兜底。CLI `--occlusion-clip`；GUI「遮挡蒙版」
     复选框随 pose 联动、`motion_occlusion_clip` 随 ROI json 持久化。
     DMG 实测：遮挡检测运行、输出与关闭时逐字节一致（真实遮挡区间由
     lost 兜底，蒙版保守 no-op）；防误报 3 项单测（整体调暗/调暗+局部
     遮挡/整屏噪声）。
  3. **屏幕局部调暗背景适配**：`core.screen_luma.measure_line_luma_curves`
     逐行独立测亮度曲线（行框四角经 H(ref→t) 映射、投影多边形内中位值，
     基线/截断语义与整平面一致），事件按 `line_idx` 取所属行曲线
     （`brightness_per_line`，默认关；独立 CLI `--brightness-per-line`；
     whitespace/external 重建事件与缺失行回退整平面曲线）。
  4. **行尾全角标点字形补偿**：`punct_comp_offset_px` 对行尾「。，、」等
     墨迹偏左标点右移 min(上限, 0.25×行高)（上限 `punct_comp_max_px=7`
     随 PlayRes 高度缩放，默认开、可经 config 关闭），`\move/\pos` 阶梯
     与 mask 遮罩块同步平移；DMG 正常流程实测 7 行行尾标点精确 +7.0px
     （短行 +6.7 未触顶）、非标点行零偏移，2026-09-16 记录的 dx −5.3~
     −7.2px 渲染偏移限制按度量补偿消除。
  另:`cli.py` ROI 时间字段解析兼容 GUI 的 HH:MM:SS.mmm 字符串与数值秒；
  三语 i18n 补齐（遮挡蒙版/自动检测文案，.qm 实测加载）。单元测试
  624 → **661+**。

- **场景文字策略背景分析与空白带判定加固**：背景取色新增 robust 模式
  （双向剔墨 + MAD/IQR 稳健离散度 + 置信度，默认仍按旧 std≤18 规则、
  逐位兼容）；空白带墨迹二值化支持局部百分位/Otsu 阈值（默认 global 不
  变），候选带按面积、宽高、背景均匀性、可排版长度评分得 confidence，
  低于阈值走既定回退链；逐块背景统计（std/MAD/IQR/样本数/置信度）与
  候选带评分进入诊断信息。静态路径逐 ROI 策略上下文化，多 ROI 复用单一
  `FrameReader`（VideoCapture 打开与 seek 不再随 ROI 数线性增长，DMG
  实测 2 ROI 仍 1 次 open/7 次 seek）；分析图可选时间邻域多帧中位合成
  （`analysis_frame_mode="median"`，默认单帧不变）；未知策略名在
  CLI/GUI 入口明确报错。遮罩新增透视安全边界：四角矩形近似误差
  （最大角点偏移/对角线）超 `mask_max_perspective_error`（默认 0.1，
  轴对齐/相似变换为浮点噪声量级不受影响）或 quad 退化时自动降级
  external 并留痕；实验开关 `mask_polygon_clip`（默认关）可生成逐帧
  四角 `\p1` 多边形遮罩。单元测试 666 → **739**（零回归，跳过/警告
  数不变）；DMG 真机四策略验收与单/多 ROI 读取对比见
  `docs/superpowers/evidence/2026-09-17-scene-text-policy-hardening/`。

### 修复

- **轨迹字幕配套选项（亮度自适应/遮挡蒙版/场景文字显示）勾选即生效**：
  此前只有「写入画面位置标签」复选框有即时写回（勾选后直接开始识别不再
  静默丢失），亮度自适应/遮挡蒙版复选框与场景策略下拉仍要靠 添加/更新
  ROI 按钮才落盘——用户勾选后直接识别，GUI 跑出的轨迹字幕不带变暗跟随
  与遮挡蒙版效果（ROI autosave 里两标志为 false），与
  `--auto-brightness`/`--occlusion-clip` 测试产物不一致。现三个控件与
  pose 同契约：`motion_brightness_toggled`/`motion_occlusion_toggled`/
  `scene_policy_changed` 信号 → 即时写回当前选中 ROI
  （`motion_auto_brightness`/`motion_occlusion_clip`/`scene_text_policy`）。
  用真实手绘 5 点闭合多边形 ROI（autosave 原样）+ 两标志开启经 CLI 验证：
  暗屏链事件携带 `\1c/\alpha \t` 变暗跟随（与 motion-bright 基线同构）。
- **「写入画面位置标签」勾选即生效、零倾角也写标签、旋转标签不再泄漏为
  字面文本**（三处关联修复，回应"勾选后输出与不勾选完全一样"的反馈）：
  1. 复选框状态此前只在 添加新 ROI / 更新选中 ROI 时写入 ROI 条目，勾选
     后直接开始识别会静默丢失，重新选中又被回填重置——现在勾选变化即时
     写回当前选中 ROI（`pose_tags_toggled` 信号 → `on_pose_tags_toggled`，
     开启时按几何重算 `pose`）。
  2. 场景行启用 pose 后始终写入完整旋转块 `\frz …\frx …\fry …`（含 0 值）：
     此前零倾角（正放矩形 ROI）追加的是空串，勾选前后输出逐字节相同，
     无法确认选项生效；且与 Bottom/Top 行的整体标签块（恒含 0 值）不一致。
  3. 回归修复：场景行旋转此前用 `tags + rotation` 拼接，落在既有
     `{…}` override 块**之外**——ASS 语法下 `}` 之后的 `\frz(8.0)` 会被
     当作字幕字面文本整串渲染在画面上；现并入块内
     `{\an5\pos(x,y)\frz..\frx..\fry..}`。
- **安装版 motion 轨迹管线不可用（RapidOCR 未随包分发）**：deb 的 venv
  依赖来自 requirements.txt，此前 rapidocr/onnxruntime 仅作为注释里的
  可选项，安装后运行 `scripts/motion_ass.py --ocr-engine rapid` 直接
  `ImportError: RapidOCR is not installed`、无任何输出。现把
  `rapidocr>=3.9,<4.0` + `onnxruntime>=1.20` 纳入 requirements.txt 随包
  安装；`motion_ass._default_ocr_fn` 对依赖缺失的指定引擎先告警并回退
  注册表默认可用引擎（不再让整条任务链退出），完全无可用引擎才报错。
- **LLM 服务端失败不再击穿任务链（工作区规则落实）**：`core.llm_client.call_llm`
  现把 openai SDK 级失败（连接失败/超时，以及 429 有界退避耗尽后的原始
  `RateLimitError`，装饰器加 `reraise=True`）归一化为 `LlmApiError`
  （`RuntimeError` 子类）——`subtitle_llm_polish` 各入口（润色/碎片合并/
  策略复核/来源分类）既有的 `except RuntimeError` 优雅降级分支此前接不住
  `tenacity.RetryError`/`openai.APIError`，一次限速耗尽即让整条生成链崩溃。
  另为 `fetch_openai_compatible_model_ids`（GET /v1/models）补上有界 429
  退避（≤3 次重试、单次等待 ≤5s、总等待 ≤30s，耗尽抛可恢复
  `RuntimeError`）。
- **颜色门限校准的色相环形均值**：`color_presence_gate.calibrate_hsv_from_crop`
  对 OpenCV 色相（0-179 环形量）改用倍角法环形均值；红色/品红等横跨
  0/179 边界的颜色旧实现算术均值会落到无关色相（如红样本均值 ≈绿区），
  inRange 校准完全失配、字幕帧被成批丢弃。跨界区间拆为
  `orange_lower/upper` + 可选 `orange_lower2/upper2` 两段取并集，
  `build_gate_spec`/`roi_extractor` 同步透传（老 gate spec 无新键不受影响）。
- **语言检测误判未知脚本为 JP**：`styling._detect_language` 用
  `defaultdict` 查 `counts['JP']` 会物化 `JP:0` 键，使 `if not counts`
  永不成立——希腊/阿拉伯/泰文等全字符在已知脚本范围外的行被误判为
  `JP` 并套用日文字体（缺字形整行豆腐块）；改用 `counts.get('JP', 0)`。
- **OCR 采样融合平票按"无多数"送审**：`fuse_samples_by_position` 与
  `_fuse_samples_strict_index` 的难帧判定由 `votes*2 < observations`
  （允许平票通过）收紧为 `<=`，与注释"观测内部无多数"一致——两帧读出
  不同文本时不再静默按置信度平局裁决，而是交 VLM 复核。
- **批量 OCR 错位防护**：`_run_batch_ocr_on_samples` 在返回前校验覆盖了
  全部采样帧、`normalize_batch_result` 输出条数与块一致；缺帧时回退逐帧
  路径，不再返回与输入错位（锚定帧语义被破坏）的结果列表。
  `imread` 失败不再把 `None` 写入图像缓存（瞬时 IO 错误可在 LRU 淘汰前
  恢复重试）。
- **取消任务不再误报成功**：阶段 4（LLM 润色）中取消时 worker 仍会写出
  半成品 ASS 并正常返回、误发 `pipeline_finished`；现与阶段 1-3 一致
  静默返回。取消路径的进度对话框销毁与 DeepSeek 面板复位在
  `_on_pipeline_worker_thread_done` 兜底（此前隐藏对话框跨运行累积、
  LLM 面板残留过期内容）。
- **分块并行只在安全模式启用**：`PipelineWorker.run` 的分块条件补上
  `not save_intermediate_json and in_memory_ocr`——重叠窗口的各 worker
  会向共享 `work_dir` 写同名逐帧文件（`frame_%06d.jpg`/中间 JSON），
  磁盘模式与调试模式下存在写竞争（与注释声称的行为对齐）。
- **分块协调者存活误判**：worker 发完 `records/done` 即退出，消息尚在
  队列 backlog 时 `is_alive()` 已为 False，会被误判失败烧掉一次重试整窗
  重 OCR；`_check_liveness` 现先非阻塞排空队列再复核。
  `_terminate_all` 在 join 前先置 `cancel_event`，避免意外异常路径上
  每个存活 worker 空等 10s。
- **换视频时自动 ROI 扫描不再丢失**：`load_video` 先取消旧扫描（异步
  收尾）再触发新扫描，旧实现 `isRunning()` 恒为真导致新视频的自动扫描
  被静默跳过；现置挂起标记，旧线程收尾后自动补扫（保持与旧扫描串行，
  不引入全局引擎并发争用）。
- **时间轴浮点截断噪声**：`timeline._format_time_seconds` 的
  `int(sec*100)` 在毫秒量化时间戳上约 5% 概率提前 1cs（如 1.16s →
  `.15`）；加 1e-6 epsilon 抵消 ULP 噪声，截断语义不变。
- **`SubtitleAligner` 插入段错位与行首占位**：连续插入（run ≥ 2）时旧
  flag 逻辑把真实目标行顶掉（等长但内容错位丢行）；占位行改为直接落到
  自己一侧并保证返回等长列表，行首占位填充空串。另加 ndiff 提示行
  防御分支。
- **平面跟踪健壮性**：容器帧数不可靠（部分 MKV/WebM 报 0）时
  `track_plane` 不再把 end 钳到 -1 抛 "empty frame range"，顺序解码读到
  EOF；`unwarp_canonical` 对退化单应（h22≈0/非有限）显式抛
  `ValueError` 而非产出 NaN 展开图。
- **场景文字策略空行守卫**：`apply_policy` 无识别行时原样返回事件并留痕
  （此前 whitespace/mask 路径会在空 rows 上取最值崩溃）。
- **场景文字策略加固轮次修复（重叠/遮罩/外置/空白四策略，默认 overlap
  行为不变——动态与静态 overlap ASS 与基线逐字节一致）**：遮罩轨迹
  改以显式 `ref_frame`（OCR 锚定帧）重建（缺省推断并进诊断，消除
  `tracks[0]` 与锚定帧不一致的整体偏移）；事件归属优先 `line_idx`、
  文本匹配仅作兼容回退（重复台词不再错误合并时间跨度）；自定义 style
  贯通动态/静态 mask 与空白路径（不再硬编码 Scene）；多行块遮罩按行
  补偿求 union（块级最大补偿致某行悬出的问题消除）；轨迹平滑窗口限制
  在连续 OK 段内（`smooth_max_gap`，长 lost 间隙不再跨段连接两侧姿态）；
  空轨迹与 NaN/反向/零面积行框入口校验后按回退链优雅降级（不再
  IndexError/ValueError）；`merge_rois` 混合策略显式警告并记录最终
  策略，不再静默取第一项。

## 2.6.3（2026-09-16）

### 新增

- **移动文字轨迹字幕（阶段一，手动触发全链路）**：新增 `scripts/motion_ass.py`
  端到端入口——手动框选平面四边形（手机屏幕/信件/招牌）+ 时间范围后，
  复用 `scene_plane_tracker` 逐帧单应跟踪，`core/keyframe_selector.py` 按
  展开图清晰度（Laplacian 方差）选 top-K 关键帧，OCR 在统一坐标展开图上
  运行并经模块级 `fuse_samples_by_position`（自 `ocr_optimizer` 行为保持
  抽取）按位置对齐投票，最终 `core/motion_ass.py` 纯函数合成器把逐帧
  中心/角度/缩放轨迹按误差预算（默认 2px @1080p）选标签：匀速直线单段
  `\move`、非线性分段 `\move`（段边界时间无缝相接）、旋转/缩放叠
  `\t(\frz/\fscx/\fscy)`、混乱抖动帧级 `\pos` 兜底、lost 遮挡切段不外推。
  配套 `scripts/verify_motion_ass.py` 渲染偏差校验（libass 烧录质心/包围盒
  偏差统计，超预算可自动减半容差复验）。在「冴えない彼女の育てかた♭
  第07話」手机邮件场景验收：263 条碎片静态 `\pos` 事件 → 26 条轨迹事件
  （13 行 × 2 链），运动轴贴合偏差 ≤1px，模糊碎片/重复行/零时长事件清零；
  验收记录见
  `docs/superpowers/evidence/2026-09-16-motion-trajectory-ass-acceptance.md`。
  单元测试 350 → **441**（新增 `tests/test_motion_ass.py` 31 项、
  `tests/test_motion_ass_cli.py` 11 项、`tests/test_keyframe_selector.py`
  8 项、`tests/test_verify_motion_ass.py` 38 项、融合抽取 3 项）。
- **轨迹字幕屏幕亮度自适应**：`scripts/motion_ass.py --auto-brightness`
  ——逐 ok 帧测量文字平面区域亮度（`core/screen_luma.py`，90 分位基线，
  中位值口径），DP 简化后把变暗/变亮折线换算成每条事件的局部
  `\t(ms1,ms2,\1c&H..&\alpha&H..&)` 链（毫秒端点相接，与 `\move` 位移共存），
  字幕颜色与透明度忠实跟随屏幕明暗；`brightness_use_color`/
  `brightness_use_alpha`/`brightness_tol`/`brightness_baseline_percentile`
  可经 `--config-json` 调整，开关关闭时输出与既有完全一致。DMG 手机场景
  验证：暗屏 247→30→233 全程跟随，最暗态字幕与原字同样淡出（忠实模式），
  烧录实测亮/中/暗三态墨水亮度符合标签期望。单元测试 441 → **466**。
- **场景文字显示策略**：`scripts/motion_ass.py --scene-text-policy
  overlap|mask|external|whitespace`（默认 overlap，行为不变）——解决识别
  字幕与画面原文字"双重曝光"重影：**mask** 段落块合并后以 `\p1` 矢量遮罩
  （layer 0）盖住原文字、识别文本置于 layer 1，遮罩取色剔除墨水像素、
  `\bord0` 无缝贴合、宽度按渲染后字幕居中外扩，随轨迹 `\move`+`\t` 移动
  并与亮度自适应联动；**external** 多块合并为单条 `NoteBox` 事件
  （BorderStyle=3 自适应底框、底边居中、CJK 折行禁则、过高自动缩字号）；
  **whitespace** 在统一坐标展开图检测空白带放置文本，轨迹/亮度标签零成本
  复用；所选模式不可用时按 whitespace→mask→external 自动回退（背景杂色
  std>18 判定等），全程告警留痕。DMG 手机场景验收：mask 模式重影彻底
  消除，whitespace 因本例文本量大于空白带按设计回退 mask，external 布局
  正确。新增 `core/scene_text_policy.py`，单元测试 466 → **525**。
- **场景文字显示策略接入完整版 GUI 与主流水线**：完整版 ROI 定义面板新增
  「场景文字显示」下拉（叠加/遮罩原文字/外置展示框/空白区放置），每个 ROI
  独立设置并随 ROI json（`scene_text_policy` 键）持久化；主流水线静态
  `\pos` 路径复用同一策略引擎（遮罩取色在 ROI 亮帧上剔除墨水像素、空白带
  检测、回退链），策略事件豁免噪声过滤与时间合并并支持 Layer 列；CLI 增加
  `--scene-text-policy`。移动文字保护：同一文本行框中心跨组位移超过位置
  容差时自动降级 external（静态遮罩会与原字错位，逐帧跟随请用轨迹管线）。
  过程修复：策略分析图改为跨全部场景组范围取最亮候选帧（避免取色落在暗屏
  段）；策略文本事件显式 `\fs=行高`（字幕大小贴合原字）。三语 i18n 补齐。
  DMG 主流水线验收：移动段自动降级、静止段遮罩无缝、外置框布局正确。
  单元测试 525 → **583**。

## 2.6.1（2026-09-15）

### 新增

- **场景文字平面跟踪（阶段二首里程碑）**：新增 `core/scene_plane_tracker.py`
  ——关键帧框选文字平面（手机屏幕/信件/招牌）四角后，ORB 特征匹配 + RANSAC
  单应估计逐帧跟随，输出逐帧四边形、累计单应（含逆变换）、内点率与重投影
  误差；内点率/重投影/面积突变/顶点跳变/凸性等质量门限不达标的帧记为
  lost（不外推），遮挡结束自动重捕获接回；`unwarp_plane`/`unwarp_canonical`
  透视展开到统一坐标供 OCR 使用，轨迹可导出/导入 JSON。新增独立入口
  `scripts/track_plane.py`（视频 + 四角 → 轨迹 JSON + 展开预览）。单元测试
  342 → **350**（新增 `tests/test_scene_plane_tracker.py` 8 项：平移/缩放
  旋转跟随、遮挡丢失与重捕获、噪声帧不产出轨迹、JSON 往返、退化四边形
  拒绝、视频包装层）。依赖仅项目现有 OpenCV（Apache-2.0）。

### 优化

- **场景文字逐行融合（手机/信件/招牌等画面文字）**：OCR 采样投票由"严格按
  行索引对齐"升级为"按行框位置对齐"（IoU 贪心匹配，`core/ocr_optimizer.py`）——
  锚定帧漏读或整帧空读时，该行由其余采样帧对应位置的观测补齐（缺失观测不
  投票、不计入该行分母，遮挡行不做负向投票）；行数不一致不再整段放弃投票，
  歧义行收敛到行级送 VLM 复核；融合行按行框 y 中心排序输出。无行几何的引擎
  结果保持旧的行索引投票与回退行为。代表行投票（`data_grouping`）同步改为
  按 y 重叠对齐行槽，行数不足众数的帧也能为其实际读到的行槽提供证据。全帧
  扫描（`fullframe_scanner`）的场景文字候选按"归一化文本一致 + 空间相邻/重叠"
  合并，手机/文档略微移动或跨网格被拆开时不再产生重复候选 ROI。单元测试
  331 → **342**（新增 `tests/test_scene_text_fusion.py` 11 项）。

### 修复

- **简洁模式恢复 ROI 绘制模式**：一键控制面板上线后「绘制模式」组在简洁视图
  被错误隐藏，而 ROI 绘制是核心工作流入口；现简洁/完整模式均可见。
- **扫描字幕带倾斜误判**：`--scan`/自动检测的倾斜带判定改为**逐文本行投票**——
  对每行 quad 求自身长边偏角，倾斜行占比 ≥60% 且方向一致（±8°）才输出旋转
  多边形 ROI，并仅用倾斜行求外接矩形。旧实现用全部点的最小外接矩形朝向判定，
  底部对白字幕逐句位置/宽度漂移时点云整体呈对角分布，水平字幕带被误判为
  倾斜带并输出对角多边形，OCR 截断字幕左右两侧文本（真实 1080p 样本复现：
  47 条完整对白 → 28 条且多数残缺）。单元测试 327 → **331**（新增逐行投票
  回归用例 4 项：阶梯点云、倾斜占比不足、方向不一致、倾斜多数忽略水平离群）。

## 2.6.0（2026-09-15）

### 新增

- **一键控制面板（简洁视图）**：控制面板默认收敛为「加载视频 → 自动检测 ROI →
  开始识别并导出」，首屏仅保留样式模板、识别语言、文字保留三选一与主操作；
  「调整字幕区域」「重新检测」为次操作。高级能力（引擎详情、来源过滤、绘制
  模式、颜色门控、LLM、进程分片等）全部保留，经「完整设置」入口展开，「简洁
  界面」可随时返回；`get_pipeline_options()` 字段与全部信号保持兼容。
- **界面语言切换**：「文件操作」组新增语言选择器（自动（跟随系统）／简体中文/
  繁體中文/English/日本語），切换后经确认自动重启应用生效；启动语言持久化于
  QSettings（`ui/language`，默认跟随系统 locale）。
- **新增英语与繁体中文界面**：全部 265 条界面文案翻译（英文按自然表述、繁体
  按台湾用语习惯），并新增 `i18n/apply_translations.py` + 译文数据文件，
  `lupdate → apply_translations → lrelease` 三步可再生全部翻译。
- **扫描/识别执行状态统一**：扫描与 OCR 运行期间主按钮禁用并显示当前阶段，
  完成、失败、取消（含线程收尾路径）统一恢复；「检测可用引擎」「刷新模型列表」
  「预览检测效果」按条件显示。

### 修复

- **深度扫描复核对话框翻译条目丢失**：`scan_review_dialog.py` 的 `_tr()` 包装
  因 lupdate 静态解析无法提取（变量实参），该对话框文案此前从未进入翻译目录。
  现改为字面量 `QCoreApplication.translate()` 调用，四种语言均可翻译。
- 修复控制面板折叠组内条件化启用状态在执行状态恢复后未重新套用的问题。

## 2.5.0（2026-09-12）

### 新增

- **并行边界精修**：精修阶段（GUI 管线约六成耗时）任务化后由最多 4 个线程并行
  执行，每线程独立引擎与视频捕获，ground window 合并为一次批量推理；线程数按
  核数/内存自适应，任何失败自动回退原串行路径。24 分钟测试视频精修
  290.3s → 202.6s（1.43×），管线全程 470.9s → 349.3s（1.35×），输出与串行
  在文本/时间戳级一致（管线级等价门禁通过）。分片模式的 worker 内保持串行
  精修，避免引擎内存放大。
- **进程分片并行（长视频，实验性）**：管线阶段 1–3（ROI 帧提取、智能 OCR、坐标
  还原）可按时间切成多个带重叠区的窗口，由多个独立进程并行执行；主进程完成
  接缝合并与 ASS 生成。分片数按 CPU 核数 / 可用内存 / 视频时长自动决定（GPU
  环境与短于 10 分钟的视频自动走单进程原路径）。CLI 新增 `--workers`（0=自动、
  1=强制单进程、N=并行数上限），GUI 控制面板新增「进程分片（长视频提速）」
  选择器。窗口重叠 15 秒 + 帧级核心区归属规则保证接缝合并确定性；失败窗口
  自动重试一次，再失败回退主进程内串行——任何 worker 故障都不会中断整体任务。
  **实测提速依工作负载而定**（详见 docs/testing.md v2.5.0 记录）：逐帧高密度
  OCR 的合成基准上 32 核约 1.1–1.4×，8 核持平；静态帧更多的真实素材预期更好。
  分片/单进程输出经等价性门禁验证完全一致（含接缝敌意夹具 14/14 事件）。

### 修复

- **场景预设过滤丢弃正常对白**：生成阶段的文本来源分类器从未提取视觉特征
  （`extract_visual_features` 未接入管线），但视觉层仍用未测量默认值打分——
  `edge_density=0` 被当成"画面模糊→场景文字"扣 0.2 分，把底部对白的加权融合分
  压到 OVERLAY 判定线（>0.65）之下，`--scan --preset anime` 等组合
  （`keep_unknown=False`）在真实动画样本上 48 条对白只剩 1 条（唯一幸存者靠
  句尾问号拿 +0.15 语义分）。现在视觉特征未经测量时该层不参与加权（权重在
  已测层间重归一化），真实测量过的视觉特征照常生效。CLI 与 GUI 同路径受益。
- **`--preset` 无效 id 不再等全片 OCR 跑完才报错**：参数解析阶段即校验
  preset id（此前在阶段 4 才检查，用户要先等完整 OCR 通过才能发现拼写错误，
  退出码 2 不变）。
- **并行精修的独立引擎在空/`auto` 引擎 id 下必现 `NameError`**：
  `build_standalone_engine` 调用了不存在的模块级 `get_default()`，空 id 一进
  并行精修就抛 `NameError`（`auto` 则因注册表查无此 id 报错），被管线兜底
  吞掉后**静默回退串行**——空引擎 id 下并行精修从未真正生效。现在空/`auto`
  id 解析为注册表默认引擎（与单例路径语义一致），显式 id 不受影响。
- **`--engine auto` 时 `--model-tier`/语言被静默忽略**：`OcrOptimizer` 对空/
  `auto` 引擎 id 从不调用 `set_engine`，`lang`/`model_tier` 选项到不了引擎
  初始化——自动引擎的 CLI 运行一直被钉在版本默认模型（medium），与
  `--model-tier tiny/small` 无关。现在空 id 在尚无已初始化引擎时仍会下发
  选项（注册表默认引擎不变）；已在别处显式初始化的引擎不受影响。
- **等价性门禁**：新增分片 vs 单进程的事件级等价测试（真实 spawn 子进程 +
  真实 OCR 引擎跑基准视频，生成的 ASS 事件必须完全一致），作为合入红线。

### 内部

- 基准测试视频生成器（`benchmarks/make_test_video.py`）的字体路径改为动态
  解析（`~/用户字体目录 → 系统统一字体目录 → fontconfig`），不再硬编码
  家目录；`build_deb.sh` 等本地脚本不受影响。
- `PipelineWorker` 阶段 1–3 抽为 `core/pipeline_stages.py` 模块级阶段函数
  （携带可序列化 `PipelineContext`，重构行为零变化），供 GUI 线程与分片
  worker 进程复用；阶段 4（ASS 生成 + LLM 润色）仍只在主进程执行，LLM
  调用保持单点与有界退避。

### 测试

- 单元测试 214 → **287 个**（分片规划、接缝合并、worker 消息协议、协调者
  容错与回退、CLI 参数、未测量视觉层门禁、独立引擎 id 解析）。
- 分片/单进程等价性测试（真实引擎）通过；性能验收数据见
  docs/testing.md 的 v2.5.0 记录。
- 发布前 CLI 链路复测（真实 1080p 动画样本 + 人工校对字幕对照）：
  详见 docs/testing.md。

## 2.4.2（2026-09-12）

### 改进

- **CLI 默认底部条带随分辨率缩放**：无 `--roi`/`--scan` 时的默认条带由固定
  底部 160px 改为帧高的 20%（至少 160px）。固定 160px 在 1080p 及更高分辨率的
  视频里容不下双行字幕，首行会被条带上边缘截掉而整行丢失（如「你現在就一副/
  打死也不肯離開這裡的樣子」只剩下一行）；720p 及以下行为不变。
- **帧级边界精修默认开启**：首/末帧逐帧复核（淡入淡出边界精修）改为默认对
  所有 ROI 生效——ROI 未写 `fade_in_refine_enabled` 字段时视为开启，仅显式置
  `False` 才跳过。此前 CLI 手动 ROI、自动建议 ROI、旧版配置文件里的 ROI 均因
  缺省该字段而跳过精修，时间轴只能精确到 OCR 采样；现在统一按帧复核首尾：
  边界处逐帧重识别（探测用 2x 放大重试），前一帧文字一致则把开始时间前移一帧、
  末尾同理后移，文本不同即停（不会跨入相邻字幕）。GUI 复选框、ROI 列表徽标、
  自动建议 ROI 字段同步该默认值。

### 修复

- **CLI 版本号未随 2.4.1 同步**：`cli.py` 的 `__version__` 停留在 2.4.0，
  `video-subtitle-ocr-cli --version` 与 deb 包版本（2.4.1，见 DEBIAN/control）
  不一致；按打包检查清单第 2 条补齐。
- **切换帧字幕叠印**：事件起止时间写 ASS 时由厘秒四舍五入改为截断。事件边界
  来自帧时间戳（末帧时间+帧距），四舍五入可能把边界推过帧边界（如 57.099 写成
  57.10），播放器在切换后的第一帧上同时画出旧事件与下一条事件——把生成字幕
  叠在硬字幕视频上校对时，表现为新旧两行字幕叠印一帧。截断后事件边界精确对齐
  帧切换，不再越界。
- **同 ROI 相邻事件端点齐平**：帧级时间推导的毫秒级误差可能让前一条结束时间
  略微越过下一条开始时间，生成阶段统一截平，时间轴与人工字幕一致首尾相接。
- **同一条字幕的省略号抖动不再碎裂/丢帧**：淡入淡出边缘 OCR 会把「好熱」
  读成「好熱」/「好熱…」/「好熱·.」等只有尾标点不同的读法，导致帧分组碎裂、
  淡入首帧整组被丢弃（字幕头被截 1-2 帧）。帧分组与事件合并比较文本前先归一化
  尾部省略号；时长过短的孤立读法组并入帧号相邻、文本一致的稳定组，剩下不相似
  的短组才按最短帧数丢弃。
- **一行对白被拆成多框**：OCR 常把含空格/间隙的一行对白拆成左右两个同高框，
  旧逻辑按 y 排序拼 `\N` 会渲染成假两行且左右顺序不稳定。载入阶段将对白带内
  垂直重叠 ≥55% 的相邻框按 x 顺序并回一行（净空约 1/8 字高以上补空格）；
  场景区画面文字不受影响。
- **双行字幕丢行**：事件文本此前取自分组首帧，首帧读丢一行时整段时间轴都
  缺一行（如「你現在就一副/打死也不肯離開這裡的樣子」只剩下一行）。改为按组内
  全部帧投票选出代表行：行数取众数、逐行槽多数投票、并列取更完整读法。
- **短句误合并**：「真的假的？」结束后 0.7s 出现的新台词「真的」因文本为子串
  关系且间隔 ≤4s 被错误吞并。子串包含合并现要求两事件几乎相接（≤0.6s），
  高 Levenshtein 相似不受影响。
- **底部带幻影噪声行**：字幕带内单字（非标点）或 ≤2 位纯 ASCII 的噪声读法
  （「C」「W」「TA」「YE」等）在载入阶段剔除，不再以独立事件或 `\N` 第二行
  混入成片。

### 测试

- 单元测试 189 → **213 个**（边界截断、短组吸收、同行框合并、子串合并护栏、
  代表行投票等）。
- 回归基准：行准确率 100%（5/5），头尾误差 max 0.033s（≤1 帧 @30fps），与
  2.4.0 持平；真实动画样本与人工逐帧校对稿对比，边界偏差全部收敛到 ≤0.03s。

## 2.4.0（2026-09-11）

### 新增

- **ROI 画布直接编辑**：绘制模式新增「编辑（拖动调整）」——在视频画面上直接拖动整体移动
  ROI；矩形拖 8 个控制点（4 角 + 4 边中点）缩放；多边形拖顶点改形。自动检测不准的 ROI
  可直接手工微调，无需删除重画。拖动数学抽为纯函数并有单元测试覆盖。
- **倾斜字幕带识别为多边形**：自动检测利用 OCR 返回的文本行四边形，检测到字幕带明显倾斜
  （长边偏角 > 4°）时输出 4 点旋转矩形 ROI（避免外扩的轴对齐矩形引入大量背景）；
  轴对齐字幕带仍输出矩形。
- **修复**：删除 ROI（尤其列表最后一项）后画面上残留已删图形的问题。

### 改进

- **国际化**：补齐新增界面文案的日语翻译（编辑模式、扫描复核、过滤策略切换等），
  `app_ja_JP.qm` 有效翻译条目 119 → 131；`pyside6-lupdate` 现以
  `-tr-function-alias translate+=_tr` 运行以识别 `_tr()` 包装（见 packaging.md 发布清单）。
- **文档**：修正 deb 包描述中界面本地化语言的过度声明（实际为 zh/ja，跟随系统 locale，
  其余语言回退中文源文案）；明确界面语言切换机制与 `LANGUAGE`/`LC_ALL` 临时切换方法。

### 测试

- 单元测试 170 → **189 个**（新增画布编辑命中/拖动、倾斜 ROI 建议、全片扫描等用例）。
- 模型链路专项检查：未缓存模型（cyrillic / PP-OCRv6 tiny / small）按需下载正常；
  语言路由（中/英/日 → v6，韩/俄/阿 → v5 多语言）、档位切换（tiny/small/auto）、
  引擎切换（paddle ↔ rapid）后推理均正常；`preload_models.py` 运行正常。
- 回归基准：行准确率 100%（5/5，含淡入淡出用例），头尾误差 max 0.033 s（≤1 帧 @30fps），
  与 2.3.0 持平。

## 2.3.0（2026-09-10）

- **纯结构重构，零行为变化**：`main_window.py`（1481 行单类）拆分为 `main_window/` Mixin 包；
  `core/subtitle_generator.py`（约 1367 行）拆分为 `core/subtitle_generator/` 包；
  对外符号经 `__init__.py` re-export，接口不变。
- 边界精修（淡入淡出帧级对齐）默认启用：基准 OCR 调用 123 → 133、填充帧 450 → 475，
  换取淡入淡出字幕头尾误差 ≤1 帧。
- 单元测试 101 → 170 个。

## 2.2.0（2026-09-08）

- 修复 CLI `--engine auto` 未注册导致的 RuntimeError，自动引擎选择可用。
- 回归基准体系完善：新增 `scripts/benchmark_regression.py`（headless 跑流水线，
  输出准确率/耗时/内存报告并与基线对比）与 `benchmarks/make_test_video.py`
  （生成带已知字幕的测试视频 + ground-truth）。
- deb 安装测试（rootless 全新 venv + 全量 pip 联网安装路径）全流程通过。

## 2.1.0（2026-08）

- 引入 RapidOCR（ONNX Runtime）备用引擎与引擎管理器（自动选择 / 手动切换）。
- CLI headless 流水线（`cli.py`）：与 GUI 相同的四段流水线可在无显示器服务器运行，
  支持 `--roi`、`--scan`、`--preset`、`--engine` 等参数。
- 全片扫描与深度扫描：自动定位字幕带 / 疑似水印 / 场景文字，深度扫描复核对话框支持
  逐项勾选导入；水印文本在生成阶段按文本/位置匹配剔除。
- 按 ROI 的文字过滤策略（手动 ROI 全部保留 / 自动字幕带受场景过滤控制）。
- LLM 增强（可选）：难帧 VLM 兜底、字幕润色、碎片合并，OpenAI 兼容接口，
  有界指数退避（429 限流重试有次数/时长上限，耗尽后优雅降级）。

## 1.x（2025-08 – 2025-12）

- 初始版本：PySide6 GUI、矩形/多边形 ROI 定义、ROI 帧提取、SSIM 跳帧 + 多点采样投票的
  智能优化、坐标还原、带样式分配与 `\pos`/`\frz` 定位的 ASS 字幕生成。
- GPU 支持（CUDA 编译 OpenCV 的 ROI 裁剪加速）、国际化框架（zh/ja）、
  抓取区域截取性能优化。
