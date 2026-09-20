# 功能变化汇总：v2.6.10（37d3341）→ 当前代码（未发布）

- 汇总日期：2026-09-20
- 范围：`37d3341`（v2.6.10，`cli.py __version__ = "2.6.10"`）→ 当前工作区
  （含 3 个本地提交与尚未提交的工作区改动）。
- 核对方式：逐提交 diff + 工作区 diff 与 `CHANGELOG.md` / 提交信息交叉比对；
  关键符号抽查（`SpanDecision`、`lost_reason`、`_INIT_ROI_MIN_FEATURES`、
  `frames_seen`、`min_vote_margin`、`_padded_mask_box`、
  `merge_restoration_tags`、`ensure_coverage` 等）；全量测试套件实跑验证。

## 版本轨迹

| 节点 | 提交 | 版本 | 内容 |
| --- | --- | --- | --- |
| 起点 | `37d3341` | 2.6.10 | README 反映 v2.6.x 功能集 |
| ① | `59cf7a4` | 2.6.11 | 按原文对齐回贴（`\an4/5/6`）+ 真实视频联测加固 |
| ② | `50bce58` | 2.7.0 | 屏幕空间位姿实测校正 + 静止塌缩 + 逐行还原标签 |
| ③ | `d233576` | 2.7.0 | 版本同步、CHANGELOG、测试记录（发布杂务） |
| 终点 | 工作区（未提交） | 未发布 | 4K 实测驱动的修复/内存/锚定/可观测性轮次 |

提交范围合计（已提交部分）：23 个文件，+3214/−167；工作区另有 21 个文件
修改（+7229/−590）与 3 个未跟踪文件（`tests/test_motion_ass_collapse.py`、
两份 plane-reacquisition 计划/设计文档）。

> 注意：`CHANGELOG.md` 的 2.7.0 一节是「自 2.6.3 以来的合并叙述」，
> 其中一部分（如意/西/葡语言修复、`mask_only` 策略、429 有界退避、
> GUI 轨迹接入等）实际落在 `37d3341` **之前**的 2.6.4–2.6.10 deb 版本中，
> 不在本汇总范围内；本汇总只覆盖上表 ①②③ + 工作区。

## 一、功能新增

### 1. 按画面原文对齐方式回贴（v2.6.11，`core/text_alignment.py` 新模块）
- 逐行左/中/右边缘贴合投票（差 ≤ 0.35×平均行高），由 OCR 行框几何反推
  原文对齐：左对齐 → `\an4`、右对齐 → `\an6`、居中/孤行 → `\an5`；
  支持同屏左右对齐并存（聊天混排）与松行距（邮件正文），不要求垂直相邻。
- 剪切去趋势：quad 展开平面的手选偏差会使竖直 UI 列随 y 线性漂移，
  投票前按成对斜率直方图估计全局剪切并去趋势（仅平面坐标路径）。
- 四条产出路径全部接入：主流水线 SCENE 分支、静态 mask/mask_only、
  静态 whitespace（固定 `\an4` 于带左缘）、轨迹 `\move`/密集 `\pos`
  （锚在逐帧跟踪行框左/右缘中点，随 `\move\frz` 旋转一致）。
- 行尾全角标点补偿按锚点沿行方向分解（倾斜文本行不再欠/过补偿）。
- `merge_line_blocks` 自 scene_text_policy 迁入本模块并 re-export。

### 2. 屏幕空间位姿实测校正 + 静止塌缩（v2.7.0，`core/pose_verify.py` 新模块，默认启用）
- 归一化互相关模板匹配逐行实测文字在原始帧中的真实位置，把单应轨迹
  锚回实测偏移；修复「文字固定、背景横移」场景下字幕随背景漂移/缩放。
- 全程可见且收敛的行判定为屏幕静止，整条吸附常量位姿；仅部分期间
  可见的行删除失配跨度（等效 lost 切链），不再渲染幽灵文字。
- 自适应补丁（参考帧无文字时改取首个有纹理采样帧）+ 零纹理守卫。
- **静止塌缩**（`core/motion_ass.py`）：位移 ≤`collapse_settle_tol_px`
  且角/缩放速率低于感知阈值的连续段合并为单条 `\pos`，文字不动不再
  输出 `\move`/`\t`；真实运动段保持逐帧跟随。
- **融合行同文本近重复去重**（`scripts/motion_ass.py dedupe_rows`，
  IoU ≥ 0.5）：消除同一行双轨迹的 `\an4/\an6` 锚点交错跳变。
- 实测 11.mp4：582 条碎片事件 → 102 条（16 `\pos` + 86 真实运动 `\move`）。

### 3. 静态路径逐行还原标签（v2.7.0，`core/line_restoration.py` 新模块）
- `\frz` 取该行识别多边形长边方向角（不再继承 ROI 级手绘姿态；
  12.mp4 实测 ROI 整体 8.0° vs 逐行真值约 −2°）。
- 帧像素采样原文字颜色：多边形内中位色（背景）+ 墨迹簇四分位中位色
  （文字色）→ `\1c`/`\3c`；距离变换估笔画宽 → `\bord`（0.2–3.0）；
  墨迹占比/对比度不可信时跳过取色，宁缺勿错。
- `\frx`/`\fry` 不自动生成（平台 libass 伪 3D 无法可靠反演），
  透视补写交给 `mask_only` 手工排版流程。
- styling/generator 接线：Scene 行携带多边形进 pose 标签流程、组中间帧供取色。

### 4. 配置面扩展（均经 `MotionAssConfig` 透传，`--config-json` 可调）
- 新增 `verify_*`（`verify_drop_score`/`verify_search_radius_px`/
  `verify_retry_radius_px`/`verify_long_span_frames`/
  `verify_span_confirm_max`/`verify_block_max_frames`/
  `verify_min_static_samples` 等）与 `collapse_*` 字段。

## 二、健壮性与正确性修复（未发布轮为主）

- **失配跨度删除的证据密度分级**（pose_verify）：单点失配不再直接删
  数秒位姿——扩大半径重试找回、边界分保留标记、超长跨度内部补采确认
  （确认帧全硬失配才删）；判定全程写入 `LineVerifyReport.spans`
  （`SpanDecision`）与 `retry_scores`。
- **静止塌缩组级位移复核**（motion_ass）：相邻静止段并组时按组内每一帧
  锚点复核（此前只查单段首末），L 形轨迹整段被吸死成一条 `\pos`、
  终点错位 14.1px 的问题消除；不变式：塌缩组 `\pos` 与组内任意帧锚点
  偏差 ≤ settle（11.mp4 19/19、DMG 12/12 通过）。
- **塌缩/校验阈值分辨率归一化**：`collapse_settle_tol_px` 与
  `static_tol_px`/`search_radius_px`/`retry_radius_px` 按
  `video_height/1080` 等比缩放（4K/480p 行为合理化，1080p 不变）。
- **真实视频联测三处流水线阻塞**（v2.6.11）：OCR 运动哨兵改对比锚帧
  （打字机内容 12.mp4 从 1 次 → 302 次真实 OCR）；平面跟踪越过空白
  引导段向后探测锚定（上限 900 帧）；关键帧池分批 OCR + 批级失败容错
  （空白引导段不再整批报错回退）。
- **关键帧批接受判据**：纯噪声批不再被选中后中断换批逻辑
  （以「过滤噪声后仍有非空行」为判据）。
- **遮罩补丁与 `\an4/\an6` 锚点匹配**（scene_text_policy
  `_padded_mask_box`）：遮罩按渲染文本真实跨度外扩（左锚右端可差
  27.6px 的透字问题消除）；`\an5`/缺省输出逐字节不变。
- **策略 ROI 的 Scene 行补逐行还原标签**（generator）：mask/mask_only
  下的 Scene 行此前拿不到 `\frz`/取色；抽出
  `line_restoration.merge_restoration_tags` 供两条路径共用。
- **line_restoration 健壮性**：空/退化多边形、NaN/inf、点数 ≠ 4 均
  安全回退；删除孤立死代码。
- **对齐诊断与平面跟踪**：`detect_line_alignments` 全部返回路径补齐
  诊断键；删除孤立单数 API；探测候选特征只缓存真正会读的帧；
  `h[2,2]` 退化守卫（不再 `LinAlgError` 打断整条跟踪）。
- **pose_verify 其它**：确认帧无法解码按「未测」整段保留
  （`long_span_unconfirmed`）；确认帧数随跨度计算（上限 3→8）；
  `verify_min_static_samples` 补 `MotionAssConfig` 透传；
  `max_dev_px` 未评估改哨兵 −1.0。
- **关键帧候选池时间覆盖**（`keyframe_selector.ensure_coverage=True`）：
  k 个时间桶为尚无代表帧的桶补最清晰帧（追加池尾，前缀不变 →
  首批命中时产物逐字节不变）；清晰度挤在同一窗口导致轨迹管线整体
  失败的问题消除。

## 三、性能与资源（4K 实测驱动）

- **pose_verify 两遍扫描 + 分块解码**：校验帧不再全量驻留、不再逐块
  从头解码；读取量降到 1.01 遍视频长度；4K 默认峰值内存
  3651 → 1318 MB、耗时 24.3 → 12.2 s；块大小按分辨率归一（字节预算恒定）。
- **平面跟踪流式帧来源**（scene_plane_tracker）：`track_plane` 不再把
  整个帧范围预载进内存列表（4K 801 帧 ≈ 20GB）；视频路径走顺序解码
  流式来源，4K 轨迹管线峰值 **19.24 → 2.16 GB（−88.8%）** 且不再随
  帧范围增长；11.mp4 4.00 → 0.24GB、DMG 1.64 → 0.22GB；轨迹 JSON 与
  `.ass` 产物逐字节不变。

## 四、跟踪质量（真实素材实测收益）

- **锚定判据要求 quad 内部有纹理**（`_INIT_ROI_MIN_FEATURES=192`）：
  此前只看外扩邻域特征数，白屏首帧可被邻域纹理误锚定后永久丢锁；
  12.mp4 ok **4.2% → 92.4%**（1 条 NoteBox 回退 → 192 条真实事件、
  26 行聊天文字），4K NCOP 7.0% → 31.8%；零回归（11.mp4/DMG 产物
  逐字节不变）。
- **低覆盖轨迹分块重锚定**（`scan_content_windows` + ≤4 趟）：
  覆盖率 < 0.6 的内容窗口按 ≤120 帧切块独立重建（各自锚定、坐标
  不变量逐段成立）；4K 固定歌词条 ok **31.8% → 66.4%**，四行歌词
  全部产出；未触发的素材零开销。

## 五、可观测性与诊断

- **轨迹丢锁原因可观测**：`TrackedQuad.lost_reason` 逐帧记录第一条
  未过的判据（`few_matches`/`non_convex`/`high_reproj`/`pre_anchor`
  等 8+1 种）；NaN 判据口径与旧表达式一致（不漏 NaN 进 JSON）；
  `[1/5]` 阶段打印 ok 占比/链窗口/丢锁直方图，summary 新增
  `lost_reasons`/`chains`（只增键，旧 JSON 兼容）。
- 对齐诊断：`min_vote_margin` 边际置信度（默认 1，并列且残差打不开
  显式归中，`low_margin_rows` 留痕）；`line_alignments`、
  `avg_h`/`tol`/`excluded_rows`/`n_valid`、`shear_slope_reason`
  贯通各路径。
- 跟踪覆盖率告警：ok 占比 <50% 时打印 ok 窗口与后果说明；
  `[2/5]` 后打印逐帧清晰度与池覆盖情况。

## 六、测试与文档

- 单元测试：2.7.0 发布时 874 passed → 当前 **1027 passed, 1 skipped**
  （本次实跑验证；CHANGELOG「未发布」最后一轮记录为 995+1，其后
  又补约 32 项未更新计数——小幅滞后，不影响结论）。
- 新增测试文件：`test_pose_verify.py`、`test_text_alignment.py`、
  `test_line_restoration.py`、`test_motion_ass_collapse.py`；
  `test_motion_ass_cli.py`、`test_scene_plane_tracker.py` 大幅扩充。
- 真实视频复测记录：`docs/testing.md` 新增 2026-09-19/20 三节
  （未发布修复轮、锚定判据修复 + 丢锁可观测、4K 分块重锚定）。
- README 功能清单同步（屏幕空间校正、逐行还原标签）。
- 新增（未跟踪）规划文档：`docs/superpowers/plans/2026-09-20-plane-reacquisition-plan.md`
  与 `specs/2026-09-20-plane-reacquisition-design.md`——分级重捕获
  Phase 1 被实测证伪（根因是锚定帧选择，已由上文第四节修复落地），
  文档保留为证伪记录；Phase 2「按链重锚定」（4K 歌词条所需）待做。

## 七、已知限制（未发布轮实测记录，未修）

- `verify_screen_pose` 的屏幕空间校正会破坏「固定位置歌词条」本已
  正确的位姿（4K 实测偏移 115–215px 并碎成 10 条事件）；关闭
  （`--config-json '{"verify_screen_pose": false}'`）即恢复正确单条
  `\pos`。11.mp4/DMG 的逐字节门槛依赖其当前行为，需单独立项修复
  （含「校正不得把行移出 ROI」边界约束）。
- 4K 剩余内存由 OCR 推理支配（单张 4K 图推理峰值约 3.03GB），
  需 OCR 侧降采样/分块推理，未做（影响识别质量）。
