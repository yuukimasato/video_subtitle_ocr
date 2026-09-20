# 更新日志（Changelog）

本项目遵循 [语义化版本](https://semver.org/lang/zh-CN/) 风格的 `主.次.修订` 版本号。
各版本发布前的完整测试记录见 [docs/testing.md](docs/testing.md)，
打包与发布流程见 [docs/packaging.md](docs/packaging.md) 的“版本发布检查清单”。

## 未发布

### 新增

- **失配跨度删除的证据密度分级（`core/pose_verify.py`）**：此前相邻两个
  失配采样（score < `verify_drop_score`）即删除跨度内全部位姿——每行最
  多 16 个稀疏采样的两点失配就可能删掉数秒位姿，运动模糊/短时遮挡造成的
  假「不可见」会让可见文字出现时间空洞。现分三级处理：① 失配采样先以扩
  大半径（`verify_retry_radius_px`，默认 2× 搜索半径、带实测偏移外推）重
  试一次，找回的采样照常纳入实测序列（顺带修复长程漂移下后续采样连锁失
  配）；② 分数落在 `[drop_score, min_score)` 的边界采样保留+标记（`rep.
  borderline`），不参与删除配对；③ 帧长超过 `verify_long_span_frames`
  （约 2s@24fps）的「超长」跨度删除前在跨度内部均匀补采确认（帧数随跨度
  `min(verify_span_confirm_max, max(1, span // verify_long_span_frames))`，
  默认上限 8），确认帧全部仍硬失配才删、任一见边界分即整段保留；确认帧
  一个都没能解码时按「未测」处理（整段保留，见「修复」节的
  `long_span_unconfirmed`）——真正消失的跨度仍必删。每次判定
  （含被拒删的保留跨度）写入 `LineVerifyReport.spans`
  （`SpanDecision`：帧号/是否删除/证据分级/涉及分数）与 `retry_scores`，
  日志带 `spans=[a-b:reason/dropped]` 摘要。配套 **分块解码控制内存**：
  校验帧不再全量驻留（此前所有行采样帧并集一次性解码，行多/4K 时可达
  GB 级），按首采样帧时间局部性把行分块（单块帧并集上限
  `verify_block_max_frames`，≤0 不分块），逐块解码-校验-释放；偏移外推
  只依赖各行自己的已测采样序列，校验结果与整批解码逐帧一致（混合场景已
  验证）。以上均经 `MotionAssConfig` 的 `verify_*` 字段透传，
  `--config-json` 可调。
- **对齐投票边际置信度检查（`core/text_alignment.py`）**：`detect_line_
  alignments` 新增仅关键字参数 `min_vote_margin`（默认 1）：票数并列且
  簇内残差极差也打不开（边际 0，此前靠 中>左>右 偏好可硬判 left/right）
  的行显式回退 center（`\an5`），diagnostics 记入 `low_margin_rows` 与
  逐行 `low_margin`/`rejected_align` 留痕；`min_vote_margin=0` 完全恢复
  旧行为，需更强置信的调用方可传 2。默认 1 经全部既有用例标定：2 行小
  组、聊天单侧 2 行等真对齐行的边际恰为 1，不受影响；38 个既有用例零翻转。
- **融合行去重补时间维（`scripts/motion_ass.py` `dedupe_rows`）**：此前
  仅「同文本 + IoU ≥ 0.5」全局合并，同位重复文本（副歌歌词、周期性重现
  的状态栏）会被静默误删。现按 OCR 证据回配每行的来源关键帧集
  （`frames_seen`：该帧存在同文本且 IoU ≥ 0.5 的读法即计入），合并条件
  追加「帧集最小交叉距离 ≤ 关键帧池网格步长」——同一视觉实例的近重复读
  法（帧来源相邻/共享）仍合并（\an4/\an6 跳变修复不回退），帧距超过一个
  网格步（中间隔着未读到该行的池帧）判为不同时刻实例，双份保留并 log
  留痕；来源缺失按旧规则保守合并。新参数全部可选，旧签名调用逐位兼容。
- **静止塌缩阈值分辨率归一化（`core/motion_ass.py`）**：
  `collapse_settle_tol_px`（12px）改为以 1080p 为基准按 `video_height/1080`
  等比缩放（2160p→24px、540p→6px；1080p 或缺省时行为不变）——绝对像素
  阈值对分辨率敏感：4K 下塌缩吸死可感知的小运动，480p 下可能超过字高。
  `max` 中的 `move_tol_px` 地板项不缩放（其语义是与 DP 分段容差同单位的
  下防线）；角度/缩放速率阈值本就是相对量，不受影响。新增
  `tests/test_motion_ass_collapse.py` 17 项（该逻辑此前无直接单测）：
  1080p 基线、2160p/540p/720p 阈值缩放翻转、速率门控分辨率无关、
  `video_height=1080/None/0` 输出逐字节一致。whitespace **轨迹**路径的
  `synthesize_events` 调用补传 `video_height`（此前缺省退回 1080p 基准；
  静态 whitespace 路径不调用该函数）。
- **轨迹丢锁原因可观测（`core/scene_plane_tracker.py` / `scripts/motion_ass.py`）**：
  `TrackedQuad` 末尾新增 `lost_reason: Optional[str] = None`，主循环把原先一次性
  的 `ok` 布尔表达式按**同一求值顺序**拆成逐条判据，丢帧记下第一条未过的判据
  （`few_matches` / `degenerate_h` / `low_inlier` / `non_finite` / `non_convex` /
  `high_reproj` / `area_jump` / `vertex_jump`），锚定帧之前的帧记 `pre_anchor`
  （跟踪尚未开始，无质量门限可归因）；ok 帧为 `None` 且 `save_trajectory` 走
  `_frame_dict` **省略该键**（全 ok 轨迹 JSON 逐字节不变，旧文件缺键按 `None` 读）。
  两条判据写成 `not (x >= 阈)` / `not (x <= 阈)` 而非 `<` / `>`：对有限值等价，
  但 NaN 时与旧 `and` 表达式同样判 lost（`NaN > 阈` 为假会让 NaN 漏过全部判据、
  被记成 ok 并把 NaN 写进 JSON）；新增用例直接构造 NaN 重投影固化该口径。
  `scripts/motion_ass.py` 的 `[1/5]` 阶段打印 ok 占比、ok 链数与各链窗口、丢锁
  原因直方图，覆盖率告警补「主因 + 链数」；`build_motion_events` 的 summary 新增
  `lost_reasons` 与 `chains`（只增键，既有键与事件/ASS 产物不变）。实测：
  DMG 丢帧主因 `non_convex`（72 帧）；12.mp4 为 `pre_anchor=123, few_matches=19`；
  4K 为 `few_matches=264, pre_anchor=230, low_inlier=40, non_convex=9, area_jump=3`。

### 优化

- **OCR 检测阶段工作分辨率封顶（`core/ocr_engine_paddle.py`，4K 内存）**：
  排查确认 PaddleOCR 3.7 经 paddlex 的 OCR 管线实际生效的检测缩放是
  `limit_side_len=64 / limit_type='min'`——对 ≥64px 的输入**不缩放**，
  DBNet 检测在全分辨率上跑：单张 4K 帧推理瞬时 ~2.2GB（引擎进程实测峰值
  3461MB，同内容 1080p 为 1672MB），是 4K 全幅 OCR 的内存支配项（此前
  记录于「已知限制」，当时结论「需对输入图降采样、会影响识别质量」——
  实际定位后无需降采样输入图：v6 检测模型的既定工作分辨率本就是长边
  960/'max'，只是该默认从未生效）。现引擎构造默认传
  `text_det_limit_side_len=2560 / text_det_limit_type='max'`：检测输入
  有界，单张 4K 推理峰值 **3461 → 2038MB（−41%）**；**识别裁剪仍取自
  原图**，识别分辨率不受影响（4K 歌词四帧实测文本逐字一致，仅个别帧
  置信度 0.993→0.974 量级波动）；输入长边 ≤ 封顶值时不缩放——全部
  1080p 流程与 4K 条带裁剪（1900px）路径行为不变。引擎选项
  `det_max_side_px`（默认 2560，0 = 不传参、保持 paddleocr 缺省行为）；
  旧版 paddleocr 拒绝该参数时 TypeError 降级链自动剥掉重试。RapidOCR
  引擎不涉及（构造不传参的既有约定）。单元测试 3 项（默认值、可调、
  降级剥离）。

### 修复

- **静止塌缩的组级位移复核（`core/motion_ass.py`）**：塌缩此前只检查**单个
  DP 段**的首末锚点距离（≤ `collapse_settle_tol_px`），相邻静止段并组时只
  比较布尔值、输出固定用组首段锚点——每段各移动 ≤12px 且同向时组内累计位
  移无上界，整段真实运动被吸死成一条 `\pos`。实测复现：L 形轨迹（右移 10px
  + 下移 10px，81 帧，DP 正确切成 2 段、每段 10px）输出**单条** `\pos` 覆盖
  3.37s，真值终点错位 14.1px（已超过 settle 本身；2.7.0 即存在，分辨率归一
  后 4K 下暴露面更大）。现改为**组级判据**：候选静止段并入前复核组内每一帧
  锚点相对组参考锚点的距离 ≤ settle，角度/缩放速率按整组跨度重算；不满足则
  另起新的静止组（仍是 `\pos`，不退化成 `\move`）。不变式：任何被塌缩的静态
  组，其 `\pos` 锚点与该组内任意帧跟踪锚点偏差 ≤ settle。真实视频实测：
  11.mp4 轨迹产物 19 条静止事件全部满足（最大偏差 10.08px），DMG 12 条
  （0.00px）。
- **关键帧批接受判据放行纯噪声批（`scripts/motion_ass.py`）**：此前用
  `any(rec_texts)`（列表非空）判定该批「有文字」，而 `is_noise_text` 对 `""`、
  `"000"`、`"<"` 均判噪声——只返回噪声的批次被选中并 `break`，随后 junk
  filter 把行剔空抛 `RuntimeError`，**不再尝试下一批**，恰是空白引导段/状态栏
  误读要修的场景。现改用与 junk filter 等价的「过滤噪声后仍有非空行」为判据，
  噪声批留痕后换下一批；`ocr_fn` 契约错误仍上抛、池耗尽才报错。
- **`core/pose_verify.py` 四处**：① 超长失配跨度的确认帧若一个都没能解码，
  此前按「已确认硬失配」删除（把「未测」当「测得失配」）——现整段保留并记
  reason `long_span_unconfirmed`，`SpanDecision.reason` 可区分「确认后删」
  「确认后留」「无法确认而留」；② 确认帧数不再固定 3 帧（见「新增」节的随
  跨度公式，默认上限 3→8）；③ `verify_min_static_samples` 此前只在
  `VerifyConfig` 里、`MotionAssConfig` 无对应字段（其余 `verify_*` 均可用
  `--config-json` 调）——补字段与透传；④ `static_tol_px`/`search_radius_px`/
  `retry_radius_px` 补分辨率归一（`verify_line_tracks` 新增可选
  `video_height`，按 `video_height/1080` 等比缩放，与塌缩 settle 口径一致；
  缺省不传时逐字节不变、1080p 系数为 1.0 亦不变）。另：
  `LineVerifyReport.max_dev_px` 未评估时由 `0.0` 改哨兵 `-1.0`（原先无法区分
  「零偏差」与「未评估」）；模块头「顺序单遍解码」的描述按实际（每块一遍
  顺序解码 + 确认帧第二遍补解码）更正。
- **遮罩补丁与左/右锚点不匹配（`core/scene_text_policy.py`）**：遮罩按行中心
  **对称**外扩，而 2.7.0 的逐行对齐把渲染文本钉在行框左缘（`\an4`）/右缘
  （`\an6`）——左锚时渲染文本右端超出遮罩右缘 `(need_w − box_w)/2 − pad`
  （CJK 典型行约 7.6px、纯 ASCII 行可达 27.6px，原文字会从字幕旁露出）。
  `_padded_mask_box` 新增按行的 `(an, anchor_x)` 外扩：`\an4`/`\an6` 覆盖渲染
  文本真实跨度，`\an5` 与缺省保持既有对称公式（输出逐字节不变）；静态路径的
  逐行判定提前，遮罩与文本 spec 共用**同一份**投票结果；轨迹路径从事件的
  `\an` 模式取锚点（`anchor_x` 取行框自身边缘，与 `rows` 同坐标系，避免用
  事件时刻的 `\pos` 把参考帧遮罩整体带偏）。原「对称外扩…无需改动」的注释
  结论已删除。真实视频实测：DMG 邮件屏 12 条遮罩全部覆盖对应渲染文本跨度。
- **策略 ROI 的 Scene 行缺逐行还原标签（`core/subtitle_generator/`）**：场景
  文字策略把 Scene 行从 `styled_lines` 移入策略上下文，随后
  `_apply_roi_pose_tags` 只处理 Bottom/Top 行——同一 ROI 勾选「写入画面位置
  标签」且策略为 mask/mask_only 时，Scene 行既无逐行 `\frz` 也无取色（该路径
  真实可达：11/12.mp4 的 pose ROI 因轨迹管线失败落到静态策略路径）。现将标签
  合并逻辑抽为 `core/line_restoration.merge_restoration_tags`（保留「必须并入
  既有 `}` 内部，否则 libass 把 `\frz` 当字面文本渲染」的约束），
  `_apply_roi_pose_tags` 改调它（行为逐字节不变），并在策略 `kind == "text"`
  事件上按行补 `\frz`/`\1c`/`\3c`/`\bord`（取帧或采样失败只缺颜色）。
  `note`/`scene_ws` 无逐行几何，不在范围。
- **`core/line_restoration.py` 健壮性与孤立代码**：空/退化多边形不再
  `IndexError`（返回 `None`）、`line_frz_deg` 对 NaN/inf 返回 0.0、点数 ≠ 4 走
  「最长边方向」回退（4 点配对逻辑与输出逐字节不变，真实 OCR 行均为 4 点）；
  删除 clip 之后永不为真的死代码 `pts.min() < 0`；内部工具 `math_hypot` 改私有
  名 `_hypot2`（此前公开命名却不在 `__all__`）。
- **对齐诊断与平面跟踪（`core/text_alignment.py`、`core/scene_plane_tracker.py`）**：
  ① `detect_line_alignments` 的 `avg_h`/`tol`/`n_valid`/`excluded_rows`/
  `shear_slope_reason` 补齐到所有返回路径（早退路径此前缺 2-3 个键、`reason`
  被打成 `None`，排查时分不清「没估计」与「没采纳」；未估计一律 NaN，不编造
  0）；② 删除零生产调用点的孤立 API `detect_line_alignment`（单数）及其测试；
  ③ 初始化探测的候选特征改为只缓存真正会被读到的帧（此前每个弱候选帧都缓存、
  上限 ~899 条，注释却称「通常 ≤2 个」；灰度仍不入缓存）；④
  `_lead_in_plane_moved` 对 `h[2,2]` 退化（0/nan）加守卫——此前会抛
  `LinAlgError` 打断整条跟踪，现按「证据不足」保留候选并留痕。
- **关键帧候选池的时间覆盖（`core/keyframe_selector.py`、`scripts/motion_ass.py`）**：
  候选池此前只按「清晰度降序 + `min_gap_sec` 0.33s 贪心」选取，不保证覆盖 ROI
  时间范围，与调用处 docstring 声称的「横跨整个跟踪」不符——清晰度集中在某段
  时池会挤在同一窗口，该窗口内没有文字就整条轨迹管线失败。现按 `k` 个时间桶
  为**尚无代表帧**的桶补入桶内最清晰帧，**追加在池尾**（前缀逐位不变，故首批
  命中时选中的批、锚定帧与产物逐字节不变），池长上界 `2*k`；由调用处显式开启
  （`ensure_coverage=True`），`select_keyframes` 默认行为不变。实测 11.mp4 追加
  `[28,95,109,211,221,279,536,584]`、DMG 追加 `[157,164,185]`，两条视频的接受批
  与 dedupe 网格步不变、**产物逐字节一致**（已复跑确认）。配套诊断：`[2/5]`
  后打印逐帧清晰度与覆盖情况，可一眼看出「按清晰度选」还是退化成时间网格。
  另新增**跟踪覆盖率告警**：ok 帧占比 <50% 时打印 ok 窗口时间范围并说明后果
  （窗口外文字无法被轨迹管线覆盖、窗口内无文字则整体回退静态策略），使这类
  失败自解释。
- **4K 素材实测驱动的两处内存优化（`core/pose_verify.py`、`core/scene_plane_tracker.py`）**：
  新增 4K 素材（`一周的朋友 NCOP(虹のかけら)_4k_high_quality_x264.mp4`，3840×2160、
  2210 帧）实测轨迹管线，暴露两处随分辨率放大到不可用的内存问题：
  ① **校验阶段的解码**：`_decode_frames` 每批都从第 0 帧顺序重读、且批内帧与确认帧堆在
  同一份驻留字典里——4K 默认（`verify_block_max_frames=64`）单块 1.59GB、峰值 **3.65GB**，
  而单纯缩小块大小会把耗时推到 2.7 倍（4 块 → 16 块 = 16 趟全片解码）。现改为**两遍扫描**：
  第 1 遍用共享的「只前进、不 seek」顺序游标（带**有界预取区**）逐块解码→阶段 A→释放块帧；
  第 2 遍把确认帧并集按时间分块解码后落地（阶段 B 实测只消费确认帧字典，不再需要采样帧
  图像）。读取量从「块数 × 视频长度」降到 **1.01 遍视频长度**（4K 实测 2241 帧 vs 模拟对照
  无预取 9672 帧）；4K 默认峰值 **3651 → 1318 MB、耗时 24.3 → 12.2 s**。同时块大小改为
  **按分辨率归一**（字节预算恒定，与 `verify_*` 阈值同口径）：
  `min(cap, max(8, round(cap × (1080/h)²)))`——1080p/缺省逐字节不变、2160p→16、1440p→36、
  `cap ≤ 0` 不归一；分块不变性在 4K 上复核为**0 处字段差异**（逐行 static/dropped/spans/
  borderline/位姿全等）。
  ② **平面跟踪预载整个帧范围**：`track_plane` 原先把区间内每帧 `cap.read()` 进
  `frames: List[np.ndarray]` 再交给 `track_plane_frames`——4K 下 801 帧 ≈ 20GB，实测
  轨迹管线峰值 **19.24GB**。现抽出「帧来源」抽象：视频路径走**顺序解码的流式来源**
  （只驻留常数帧；`first_weak` 回退时重开解码器顺序**重放**，仍不 seek、不跳帧），
  内存列表路径原样保留，质量门限/锚定/回退逻辑两个入口共用（一致性是结构性的）。
  4K 轨迹管线峰值 **19.24GB → 2.16GB（−88.8%）**且**不再随帧范围增长**（101/401/801 帧
  → 670/690/690 MB，主要是解码器常数缓冲，追踪自身只加约 60MB）；11.mp4 4.00 → 0.24GB、
  DMG 1.64 → 0.22GB。三份轨迹 JSON（含 `h_total` 逐帧单应）与两份 `.ass` 产物**逐字节不变**。
- **平面跟踪锚定在「quad 内部无纹理」的帧上（`core/scene_plane_tracker.py`）**：
  初始锚定判据此前只看 `region0`（quad 外扩 `max(24, 0.35·diag)`，面积约为 quad 的
  3 倍）内的特征数是否 ≥ `strong_need`（默认 48），且**遇到第一个达标帧就 break**
  ——邻域纹理足以把一个「选中的平面本身完全空白」的帧判成特征丰富帧。实测
  12.mp4（0–1870 帧，`12_roi_motion.json`）：锚定帧 34（t=1.42s）region0 内 61 个
  特征而 **quad 内部 0 个**（全是白屏 JPEG 噪声）；帧 113 硬切（匹配数 903→0 一帧
  内）后永久丢锁，**79/1871 帧 ok（4.2%）**，关键帧池取不到文字行 → 该 ROI 回退
  静态策略。该 ROI 的真实内容（聊天 UI，特征数饱和 2000）从帧 130 起才出现，
  **同素材从 130 起重新锚定实测 98.9% ok**——瓶颈是锚定帧选择，不是缺重捕获。
  现新增 `_INIT_ROI_MIN_FEATURES`（192，由实测分离度导出：坏锚定帧 quad 内
  0–151、好锚定帧 1393–2000）与 `_count_features_in_roi`（只对已过旧判据的帧做
  一次聚焦检测、只返回计数，锚定帧存下的 `ref.pts/desc` 仍由原路径产出）：优先
  选「region0 与 quad 内部都特征丰富」的帧，探测上限内没有这样的帧时**回退旧
  规则**（锚定帧与特征集与修复前完全一致）。实测收益：12.mp4 ok **4.2% → 92.4%**
  （1729/1871，单链 t=5.13–77.20s；`overlap` 策略产出 **192 条事件**，165 `\move`
  + 27 `\pos`，取代原「1 条 NoteBox」回退，文本由乱码变为 26 行真实聊天文字，
  OCR 行如 `まだ帰ってない？`/`終電になっちゃった`）；4K NCOP（0–800）**7.0% →
  31.8%**（255/801、2 条链、退出码 2→0、5 条歌词事件）。**零回归**：11.mp4 与
  DMG 首帧 quad 内部即 2000 特征 → 仍锚定帧 0，轨迹 JSON 逐字节相同、四份
  `.ass`（`scripts/motion_ass.py` 与 `cli.py` 两条路径各两份）逐字节相同；
  4K 剩余缺口见「已知限制」（歌词逐行重锚定）。
- **低覆盖轨迹的分块重锚定（`core/scene_plane_tracker.py` / `scripts/motion_ass.py`）**：
  4K NCOP 的歌词条是**固定屏幕位置**的逐行歌词（内容窗口帧 207–211 / 230–396 /
  414–556 / 579–800，合计 537/801 = 67% 为「ROI 内有字形」），行间有空白，相邻
  歌词字符不同、与上一行描述子无法匹配，故单链在行间丢锁后接不上后续行：实测
  首遍 255/801 = 31.8%，缺 414–469 与 579–800 两段（后者还含**两行**不同歌词）。
  新增 `scan_content_windows`（顺序解码、`start>0` 时只定位一次、不中途 seek，复用
  `_count_features_in_roi` 的 ROI 内部特征计数）扫描内容窗口；对**覆盖率不足**的
  窗口（`REANCHOR_WINDOW_COVERED_RATIO` 0.8 以下）按 ≤`REANCHOR_CHUNK_FRAMES`(120)
  切块，**每块独立跑一趟** `build_motion_events`——各自锚定、`h_total=I`、仍是单链，
  故 `build_line_tracks` 的单 `ref_frame` 约定与坐标不变量**逐段成立**；被替换窗口
  内其它趟次的事件按时间**丢弃**（避免 pass 1 的旧行集在新内容上重渲染出错误文字），
  块间不重叠且与首遍已覆盖帧不重叠 ⇒ 合并无需去重。触发条件 `ok 占比 < 0.6`
  （`REANCHOR_MAX_OK_RATIO`），上限 `REANCHOR_MAX_PASSES`(4)；OCR 回调跨趟复用
  （模型不重复加载）。实测 4K：ok **31.8% → 66.4%**（532/801），四行歌词全部产出
  （`明日もこうして`/`君のそばにいて`/`同じ時を過ごしていたいな`/
  `ゆっくりページをめくるように`/`また増やしてゆこう`/`君との印し`），每行一个静止
  `\pos`（固定条带的正确形态）。**零回归**：11.mp4(1.0)/DMG(0.707)/12.mp4(0.924) 的
  ok 占比均 ≥ 0.6 ⇒ 不触发也不扫描，四份 `.ass` 与 11.mp4 轨迹 JSON 仍逐字节相同。
  代价只落在触发时：一次全片 ROI 扫描（4K 实测 18.4 s）与额外趟次的解码/OCR
  （4K 整体 40 s → 约 120–170 s）。
- **屏幕空间校正的 ROI 包含门控（`core/pose_verify.py`，修 4K 固定歌词条
  实测缺陷）**：`verify_screen_pose`（默认开）的模板匹配此前不受任何空间
  约束——4K NCOP 固定歌词条（155px 高条带 ROI）实测中，分辨率归一后的大
  搜索半径（80/160px）相对条带过大，模板在条带外锁到背景纹理，把跟踪侧
  本已正确的位姿（行心 (1914,2072)，逐帧误差 <1px）改到 y≈1858–1957
  （偏移 115–215px，超出条带），并把可见段判为不可见删除，一行歌词碎成
  10 条错位事件；此前唯一规避手段是关闭整项校正。现 `verify_line_tracks`
  新增 `roi_quad` 入参，调用方（`scripts/motion_ass.py`）把用户手绘 ROI
  四边形传入：全局最优匹配峰落在 ROI（按 ROI 最小边 10%、至少 4px 外扩）
  之外、**且 ROI 内存在可采纳峰（分数 ≥ min_score）**时，改取 ROI 内最优
  峰（`LineVerifyReport.n_clamped` 计数留痕）。钳制语义是严格附加的
  「只在 ROI 内有可采纳峰时改写」：ROI 内无峰时回退全局峰、与旧行为**逐
  字节一致**——文字滚出手绘 ROI 但仍在画面上可见的场景（DMG 邮件屏滚出
  的导航栏标题，实测抽帧确认可见）照常跟随，钳制绝不制造失配、绝不删除
  可见位姿（首版「界外即失配」的实现正是被 DMG 真实素材验收否决——会
  让滚出段遮罩消失、原字裸露——后改掉的）。ROI 非法（点数/非有限/退化）
  时禁用钳制并留痕，不中断校验。`MotionAssConfig` 新增 `verify_roi_clamp`
  （默认开，`--config-json` 可关）。合成视频单测 7 项（伪峰抑制、无 ROI/
  关钳制旧行为、余量内外、非法 ROI、半平面掩膜与四边形归一）。真实素材
  验收：11.mp4 与 DMG 的 `.ass` 产物**逐字节不变**；4K 歌词条默认配置
  （verify 开）从 10 条错位事件修复为与 `verify_screen_pose: false` 逐
  事件同构的 8 条（同文本同时间跨度，位置差 ≤1px，`ゆっくりページを
  めくるように` 吸附 (1913.0,2073.5)，落在字形带内）。「已知限制」中
  相应两条目移除。
- **ROI 钳制的回退观测与细化口径收尾（同日第二轮）**：门控的「ROI 内无
  可采纳峰 → 回退全局峰」分支此前完全不可观测——「文字消失后重试假锁
  ROI 外相似纹理」的残留形态（见「已知限制」）一旦出现无从定位。现
  `_match_center` 分级计数：回退总数计入 `stats['fallback']`，其中
  **回退且全局峰落在 ROI 外**的采样计入 `LineVerifyReport.n_roi_fallback`
  （阶段 A/B 累加；corrected/static 日志非零时附 `roi_fallback=N`）。
  该计数同时覆盖「滚出 ROI 仍可见」的正确回退（DMG 形态）与消失段假锁，
  **零采纳行为变化**——行为约束（「消失段校正不得移出 ROI」）待真实素材
  暴露后以此为定位依据单独立项（稀疏采样间隔内滚出文字可远移，按帧做
  连续性/距离门控会误伤 DMG 形态，不能简单加）。另修钳制分支的亚像素
  细化：改用真实分数面 `res` 而非掩膜后 `res_roi` 细化（掩膜边界峰的
  -1 邻居进抛物线会把细化拉向 ROI 内侧，最多半像素）——「全局峰可采纳
  且在 ROI 内时，钳制逐位等于不钳制」的不变量由此严格成立，新增
  `test_roi_clamp_noop_when_global_peak_inroi` 固化；既有用例补
  `n_roi_fallback` 断言（钳制路径为 0、滚出回退路径等于采样数）。

### 已知限制（本轮实测记录，未修）

- **4K 下剩余内存主要由 OCR 推理占用**：检测阶段工作分辨率封顶（见「优化」
  节）后，单张 4K 帧的检测输入有界（引擎进程峰值 3461 → 2038MB）；剩余为
  PaddleOCR 模型加载约 0.56GB 与识别（rec）推理缓冲、框架常量，不随帧数
  增长；若要进一步压，需在 OCR 侧做分块推理（会影响识别质量，未做）。
- **`verify_screen_pose` 对「文字消失后」的失配跨度仍无空间约束**：ROI
  包含门控只在 ROI 内存在可采纳峰时改写匹配；文字真正消失后 ROI 内无峰，
  校正回退全局峰——大半径重试仍可能锁到 ROI 外相似纹理（与旧行为一致，
  该跨度由失配分级判定兜底删除/保留）。该形态现已**可观测**：回退且全局
  峰在 ROI 外的采样计入 `LineVerifyReport.n_roi_fallback`（corrected/
  static 日志附 `roi_fallback=N`），真实素材暴露时可直接定位（见「修复」
  节同日第二轮）。行为约束（「消失段校正不得把行移出 ROI」）仍需单独立
  项：稀疏采样间隔内滚出文字可远移，按帧做连续性/距离门控会误伤 DMG
  滚出形态。4K 固定歌词条实测中该形态未再出现（可见段全部正确采纳）。

### 测试

- 单元测试 875 项收集 → **905 passed, 1 skipped**（新增：失配跨度分级
  5 项、边际置信度 4 项、时间维去重 5 项、塌缩归一化 17 项，既有用例零
  翻转）。
- 修复轮：**975 项收集 / 974 passed, 1 skipped**（+69 项：塌缩组级判据 9、
  批接受判据与 `verify_*` 透传 5、pose-verify 证据分级/确认帧数/阈值归一/哨兵
  8、遮罩锚点外扩 9、策略逐行还原 4、line_restoration 健壮性与标签合并 9、
  对齐诊断与平面跟踪净 +1、关键帧池时间覆盖 10、跟踪覆盖率告警 2、
  4K 两遍扫描与块大小归一 5、平面跟踪流式来源 7；`ruff check --select F`
  全仓全绿）。
- 平面跟踪锚定判据 + 丢锁原因可观测轮：**996 项收集 / 995 passed, 1 skipped**
  （+21 项：ROI 内部锚定判据 15、丢锁原因与 `pre_anchor` 序列化 5、NaN 重投影
  口径 1；`ruff check --select F` 全仓全绿）。既有用例零翻转；`test_run/out/`
  四份 `.ass`（11.mp4/DMG 各两条管线路径）与 `bl_traj_11.json` **逐字节不变**。
- 真实视频复测（1080p；`test_run/` 口径与产物）：
  12.mp4 主流水线（overlap，1871 帧 ROI）1600 事件，`\an4/\an5/\an6` =
  551/570/479，与修复前**各项统计一致**且连跑两遍**逐字节相同**；
  11.mp4 轨迹管线（overlap，quad 取 `11_roi_overlap.json` minAreaRect，650 帧）
  121 事件（静止 `\pos` 19 + `\move` 102），静止锚点不变式 19/19 通过
  （最大偏差 10.08px ≤ settle 12px；同口径关闭塌缩为 126 条 `\move`）；
  DMG 轨迹管线（mask）36 事件，12 条遮罩对同文本渲染跨度 12/12 覆盖通过，
  烧帧目检原字被补丁覆盖、重绘文字落在原位、左缘对齐；
  12.mp4/11.mp4 的 write_pose_tags ROI 仍因关键帧无文字而回退静态策略
  （与修复前一致，各 1 条 NoteBox）。
- ROI 包含门控 + det 分辨率封顶轮：**1038 项收集 / 1037 passed, 1
  skipped**（+42 项：ROI 钳制 7、det 封顶 3，另有上一轮记录（995）之后
  未入账的既有新增 32 项一并计入；`ruff check . --select F` 全仓全绿）。
  真实素材验收：11.mp4 / DMG `.ass` 产物逐字节不变；4K 歌词条默认配置
  （verify 开）8 条事件与 `verify_screen_pose: false` 逐事件同构、位置
  差 ≤1px（修复前为 10 条错位事件）；引擎级单张 4K 推理峰值
  3461 → 2038MB。详见 `docs/testing.md` 2026-09-20 修复轮一节。
- ROI 钳制回退观测 + 细化口径轮：**1039 项收集 / 1038 passed, 1
  skipped**（+1 项：钳制无操作不变量；既有钳制用例补 `n_roi_fallback`
  断言，既有用例零翻转；`ruff check . --select F` 全仓全绿）。零采纳
  行为变化，详见 `docs/testing.md` 2026-09-20 第三轮。

## 2.7.0（2026-09-19）

### 新增

- **轨迹字幕屏幕空间实测校正（`core/pose_verify.py`，默认启用）**：修复
  「文字固定在屏幕坐标、背景横移」场景（如叠加在运动背景上的固定聊天
  面板）下，背景运动经单应链漏进行框轨迹、输出字幕跟着漂移/缩放的问题。
  归一化互相关模板匹配逐行实测文字在原始帧中的真实位置，把单应轨迹锚
  回实测偏移（采样点线性插值）；全程可见且收敛的行判定为屏幕静止，整条
  吸附为常量位姿；只在一段期间可见的行（聊天逐条浮现/滚出）删除失配
  跨度内的位姿（等效 lost，轨迹切链），不再渲染幽灵文字。自适应补丁：
  参考帧处文字尚未出现时按时间顺序改取首个有纹理的采样帧；零方差补丁
  （纯色块）整行跳过，防伪匹配。配套 **静止段塌缩**：合成器把链内总位
  移 ≤ `collapse_settle_tol_px`（12px）且角度/缩放变化速率低于感知阈值
  （`collapse_max_rot_rate` 3°/s、`collapse_max_scale_rate` 0.10/s）的连
  续段合并为单条 `\pos` 事件——文字不动就不需要 `\move`/`\t`（片头入
  场动画等真实运动段保持 `\move` 逐帧跟随）。融合行新增同文本近重复去
  重（`dedupe_rows`，IoU ≥ 0.5），消除同一视觉行生成两条轨迹、锚点
  `\an4`/`\an6` 交错跳变的问题。实测 11.mp4：582 条碎片事件 → 102 条
  （16 条静止 `\pos` + 86 条真实运动 `\move`），渲染目检字幕精确贴合
  气泡。`MotionAssConfig` 新增 `verify_*`/`collapse_*` 字段，`--config-json` 可调。
- **静态路径逐行还原标签（`core/line_restoration.py`）**：「写入画面位置
  标签」开启时，场景行的 `\frz` 不再继承 ROI 级手绘姿态，而是取该行识
  别多边形的长边方向角（屏幕坐标下的真实倾角；12.mp4 实测 ROI 整体
  8.0° vs 逐行真值约 −2°，方向约定与 `_compute_roi_pose` 一致：右下沉
  为负、右上升为正，近竖排返回 0）。同时从该行所在帧采样文字颜色与描
  边：多边形内部中位色作背景、远离背景的墨迹簇四分位中位色作文字色，
  输出 `\1c`/`\3c`；墨迹掩码距离变换估计原字笔画宽，扣除替换字体固有
  笔画后给出 `\bord`（夹在 0.2–3.0）。墨迹占比/对比度不在可信区间时跳
  过取色，宁缺勿错。`\frx`/`\fry` 不自动生成：本平台 libass 的伪 3D
  按字形独立错切、整行呈扇形展开，无法从几何可靠反演，透视补写交给
  `mask_only` 手工排版流程。
- **识别语言新增「中文繁體」（`chinese_cht`）**：识别语言下拉框在简体之后
  新增繁体中文。paddleocr 3.7 的 `_PPOCRV6_LANGS` 本就包含 `chinese_cht`
  ——与简体/英/日同一套 PP-OCRv6 模型，**无需额外下载模型**，选路加入
  `V6_LANGS` 快车道（`resolve_model_selection("chinese_cht")` →
  `{"ocr_version": "PP-OCRv6"}`，档位 tiny/small/medium 同样可用）；
  引擎元数据 `supports_languages` 同步补齐；提示语四语（en/ja/zh_CN/
  zh_TW）更新。合成繁体字幕图实测：`今天天氣真不錯，我們去看電影` 识别
  得分 0.995（复用已缓存 v6 模型，零下载）。RapidOCR 引擎不区分语言
  （内置 PP-OCRv6 ONNX 模型），行为不变。
- **「仅遮罩」场景文字策略（`mask_only`，typesetting 遮罩蒙版）**：面向
  字幕组排版工作流——片源画面带烧录文字（场记板/招牌/手机屏幕等）时，
  工具生成 `\p1` 纯色遮罩盖住原文字，**layer 1 留空**供在遮罩上自行
  排版覆写（重铺译文或 `\p` 矢量字）。与既有 `mask` 模式的区别：`mask`
  会把识别文本以 Dialogue 重渲染在 layer 1（同语言清晰重渲染），
  `mask_only` 把识别文本改写为 ASS **`Comment:` 行**——播放器不渲染、
  Aegisub 等编辑器网格仍可见原文与时间，作排版/翻译参考。遮罩生成完全
  复用 mask（背景取色/渲染宽度外扩/透视安全边界/轨迹 `\move`+`\t` 跟随/
  亮度 `base_color` 联动），可用性不足时按 `mask_only → external` 自动
  回退并留痕；静态路径的「文本移动 > 容差 → 降级 external」守卫同扩。
  接入：GUI 每 ROI「场景文字显示」下拉框新增「仅遮罩（供排版覆写）」
  （随 ROI json 持久化）、项目 CLI `--scene-text-policy mask_only`、
  轨迹 CLI `scripts/motion_ass.py` 同名参数；三语 i18n 补齐。
  单元测试 **764 passed, 1 skipped**（含新增
  `tests/test_scene_text_policy_mask_only.py` 11 项：动态/静态 spec、
  回退链、Comment 行写入、主流水线端到端）。
  DMG 手机场景实测（验收记录见
  `docs/superpowers/evidence/2026-09-18-mask-only-acceptance/`）：动态管线
  12 行 → 12 条遮罩 Dialogue（6 块 × 2 链，layer 0，采样色 `\p1` 随
  `\move` 跟踪）+ 12 条 Comment 参考行，零渲染正文残留；libass 烧录
  目检原字被无缝覆盖、链尾帧无透字。
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

- **deb 安装下载加速（镜像自动测速 + 代理/离线配置）**：postinst 此前固定
  从官方 PyPI 下载数百 MB wheel，弱网环境实测仅 ~38KB/s。现安装时并发
  探测官方 PyPI 与清华 TUNA/阿里云/腾讯云/华为云镜像（每家实测下载
  512KB 的含 TTFB 耗时，探测单请求超时 4–6s、整体预算 ≤12s，有界不卡
  安装），最快者先行，失败的安装尝试自动轮换下一候选（重试仍为有界
  退避：5 次、5/10/20/40/60s、总等待上限 135s 不变）。新增可选配置
  `/etc/video-subtitle-ocr/install.conf`：`PIP_INDEX_URL` 钉定镜像（免
  探测）、`http(s)_proxy` 等 VPN/HTTP 代理透传（检测到代理即经代理走
  官方 PyPI）、`PIP_FIND_LINKS` 本地 wheel 离线安装、
  `PIP_EXTRA_INDEX_URL`/`PIP_EXTRA_ARGS`/`PIP_TIMEOUT` 细调；环境变量
  （`sudo -E`）同样生效。失败指引与 README/packaging.md 补充配置示例；
  内网 http 源自动补 `--trusted-host`；pip 自升级改走所选镜像并加
  `timeout 90s` 上限。实测：探测本机选出 TUNA（512KB 0.61s vs PyPI
  1.74s）。

### 优化

- **分块并行解码起点优化（晚窗口不再从头解码）**：分块并行此前每个
  worker 都从第 0 帧顺序解码到自己的窗口；现 `_seek_decode_start` 把顺序
  抽取器 seek 到计划书的 `grab_start_frame`（前置预卷吸收
  `CAP_PROP_POS_FRAMES` 定位误差），seek 点之前触发的区间事件预应用，
  后端不支持 seek 时回退整段顺序解码；`PipelineContext.decode_start_frame`
  打通抽取阶段。配套两项：窗口裁剪后幸存 ROI 携带 `_global_roi_index`
  全局编号，阶段 4 的 per-ROI 元数据（pose 标签/过滤/场景文字策略）不再
  因前部 ROI 被裁掉而错位；流式模式下抽取完成不再把进度回发到 10%。
  新增 `tests/test_roi_extractor_seek.py`（seek 与全解码等价性、区间预
  应用、目标钳制、阶段接线）与 `test_chunk_worker` 全局编号用例。

### 修复

- **三视频复测驱动的功能完善（性能 / 健壮性 / 对齐判定 / 诊断 / 回退产物）**：
  - **关键帧池一次性解码 + 批级失败容错**（`scripts/motion_ass.py`）：
    分批 OCR 此前每批各做一次顺序解码、选定批后又整批重读（最坏 5 遍）；
    现进循环前一次解码整个候选池（≤ max(keyframe_count,12) 帧），批级
    解码失败（容器帧数虚标、尾帧解码出错）log 留痕后换下一批而不整体
    失败，`ocr_fn` 契约错误仍上抛，池耗尽无文字才报错。
  - **锚帧与最终复用结果同点更新**（`core/ocr_optimizer.py`）：initial
    OCR 后提前设锚、序列内 best 选定后又复用另一帧的结果，两者脱钩会
    让哨兵触发时机漂移；现统一在最终结果确定处设锚（空结果也是真实
    OCR），锚帧与 `_last_ocr_by_roi` 原子更新，多 ROI 状态互不污染。
  - **剪切斜率采纳加占优度校验**（`core/text_alignment.py`）：左/中/右
    三种边缘的成对斜率混 bin 投票此前最大 bin 有 ≥3 对即采纳，4–8 行
    混合布局中 3 对跨列噪声同落一 bin 即胜出、去趋势把真对齐边缘推离
    容差而整组翻错（11.mp4 倾斜聊天平面右气泡被判 `\an4` 等）；现要求
    best bin 对数 ≥ max(3, 0.25×总参与对数)，不足则斜率按 0 处理，
    diagnostics 带出 `shear_slope_reason`。复测 11.mp4：混排左右气泡
    逐行归位（旧 332/107/143 → 新 333/103/146，翻转项与原画面气泡侧
    别逐一吻合）。
  - **SCENE 分支按行数启用去趋势**（`core/subtitle_generator/styling.py`）：
    组行数 ≥5 时 `detrend_shear=True`（斜放平面的竖直 UI 列在视频坐标
    里随 y 倾斜），<5 行保持旧行为。
  - **平面跟踪初始化探测优化**（`core/scene_plane_tracker.py`）：探测/
    锚定特征按 region0 外接矩形 mask 检测（quad 小于画面时区域内特征
    密度上升、不再误判不足）；探测期间 progress_cb 逐帧回调 + 每 ~150
    帧 log 一次（进度单调）；重锚定前做 frame0↔候选帧软一致性校验
    （匹配充分且单应明显偏离单位阵 → 引导段平面在动 → 依次退回弱帧
    候选；全部不可信仍锚最强帧并 warning，不抛错），探测池只缓存特征
    不缓存灰度。
  - **静态 whitespace / external 回退产物按整行文本去重**
    （`core/scene_text_policy.py` `_wrap_rows_dedup`）：静态路径的行集
    跨整个 ROI 时间段，常驻文字（状态栏/标题栏）随每个 OCR 组重复进入，
    11.mp4 聊天状态栏「べにっぽ」25 秒重复 55 次撑爆单条 NoteBox；现
    按首次出现顺序去重，motion 版按组分段路径不受影响。
  - **诊断补全**：`detect_line_alignments` diagnostics 新增
    `avg_h`/`tol`/`excluded_rows`/`n_valid`；静态 whitespace 路径补写
    `line_alignments`（源行判定留痕，单块放置固定 `\an4` 不受其影响）。
  - 新增回归：关键帧换批/批级失败/锚帧刷新/多 ROI 隔离、剪切斜率占优
    度（噪声拒纳 + 强剪切采纳）、≥5 行斜放 SCENE 组、引导段平面移动
    退回弱帧、静态 whitespace/external 去重与诊断键；单元测试
    841 → **856 passed, 1 skipped**。三视频复测：12.mp4 主流水线产物与
    优化前逐字节一致（零回归），DMG 邮件画面遮罩/对齐目检通过（滚动段
    跟踪正常），11.mp4 混排气泡对齐判定改善。

- **真实视频联测暴露的三处流水线阻塞（12.mp4 全流程 0 事件 → 可用）**：
  - **OCR 运动哨兵的空结果污染与渐进变化失感**：`_motion_sentinel_skip`
    此前对比**上一帧**——打字机式渐进浮现（聊天气泡逐条出现）每步差异
    都低于阈值、永不触发真实 OCR，空白首帧的空结果被一路复用到片尾
    （12.mp4 78 秒只 OCR 1 帧、产物 0 事件）。现对比**锚帧**（上次真正
    OCR 的帧）：变化逐帧累积、越限即触发真实 OCR；跳过帧不更新锚帧，
    静止 ROI 复用语义不变（相邻全等帧仍整段跳过）。真实联测 12.mp4：
    1 次 → 302 次真实 OCR，全部文字行被拾取。
  - **平面跟踪初始化锚死在无纹理首帧**：`track_plane_frames` 此前只在
    第 0 帧找特征，不足即抛错（12.mp4 首帧空白聊天屏 → 0 特征 → 轨迹
    管线整体失败）。现向后逐帧探测（上限 900 帧）：优先锚定第一个
    「特征丰富」（≥ max(4×min_matches, 48)）的帧——仅 UI 框架可见时
    特征刚过下限、锚在那里很快丢跟，文字出现后的帧特征数倍增，锚点
    自然落进文字区段；全片无「丰富」帧则退回第一个够 `min_matches`
    的帧；之前的帧记 lost（该段无文字可回贴，不产出事件）。
  - **运动关键帧按清晰度选取可能全部落在文字出现之前**：关键帧 OCR
    只取最清晰 K 帧，空白引导段的最清晰帧先于文字出现，整批无文字行
    即报错回退（12.mp4 候选全在 0–4.6s）。现取
    `max(keyframe_count, 12)` 候选池按清晰度降序**分批** OCR，该批无
    文字行换下一批，直到取到文字或池耗尽；首批有文字时行为与输出与
    旧版逐字节一致。
  新增回归：`tests/test_ocr_optimizer.py` 锚帧累积再检测、
  `tests/test_scene_plane_tracker.py` 空白引导段重锚定。
- **识别文字不再强制居中显示,按画面原文对齐方式回贴(`\an4`/`\an5`/`\an6`)**:
  此前所有识别行一律 `\an5` 锚在行框中心——原画面**左对齐**的多行文字
  (邮件正文、信纸、聊天记录等)因各行检测/渲染宽度差异失去公共边距,
  短行浮到画面中间,整块看起来「全部居中」,与原排版不符(DMG 邮件画面
  与月色真美聊天画面验收发现)。现由 OCR 行框几何反推原文对齐:**逐行**
  在全组做边缘贴合投票——分别统计左缘/中心/右缘上「与组内其他行贴合
  (差 ≤ 0.35×平均行高)」的票数,取最高者为该行对齐边。左对齐行 →
  `\an4` 锚行框左缘,右对齐行 → `\an6` 锚右缘,居中行 → `\an5` 锚中心
  (旧行为);不要求垂直相邻(邮件松行距可判),同屏**左右两种对齐并存**
  的聊天界面逐行各归其位;孤行(三边都无人贴合,如落单气泡)归中、锚
  自身原位;等宽行块三边票数相同 → 归中,单行归中,这些场景输出逐字节
  不变。三条产出路径接入:主流水线 SCENE 分支(`\pos`)、场景文字策略
  mask/mask_only 的文本事件(与遮罩并块无关,整组投票)、轨迹管线
  `\move`/帧级 `\pos` 兜底(锚在逐帧跟踪的行框左/右缘中点,随 `\move`
  与 `\frz\fscx\fscy` 一致跟随,倾斜手机屏幕实测旋转几何精确);whitespace
  静态路径的单条放置改 `\an4` 左对齐于空白带左缘(与 motion 版合成行框
  「带内左对齐」的既有约定对齐,此前 `\an5` 与之矛盾)。检测/并块实现
  抽为无依赖纯函数模块 `core/text_alignment.py`(`merge_line_blocks`
  自 scene_text_policy 迁入并 re-export,API 不变)。**真实视频回归**
  (月色真美 11/12.mp4、DMG 邮件画面):静态 `\pos` 与轨迹管线烧录目检,
  邮件正文 10 行全部 `\an4` 钉在公共左缘、聊天左右气泡各归其位、倾斜
  平面随 `\move\frz` 旋转一致;quad 展开平面的手选偏差会让竖直 UI 列
  随 y 线性漂移(实测 4° 偏差漂 36px),轨迹管线投票前按成对斜率直方图
  估计全局剪切并去趋势(`detect_line_alignments(detrend_shear=True)`,
  仅平面坐标路径启用;票数并列时取簇内残差极差更小的边缘)。新增
  `tests/test_text_alignment.py` 22 项(判定/投票、聊天混排、松行距、
  剪切去趋势、SCENE 分支/静态 mask/轨迹管线锚点、旋转轨迹锚点几何);
  单元测试 806 → **828 passed, 1 skipped**。同条目后续优化四项:
  ①静态 mask 路径同样启用剪切去趋势(其行框是视频坐标轴对齐裁剪,非
  quad 展开,但斜放平面上的竖直 UI 列在视频坐标里本就随 y 线性倾斜;
  正面版式成对斜率全 0,开关等价于无操作);②轨迹管线的行尾标点补偿
  (≤7px)改与 `\an4/\an6` 锚点同沿行方向分解(`x_off·cos/sin(ang)`,
  旧实现按屏幕 x 平移,倾斜文本行会欠/过补偿;居中分支保持屏幕 x 平移,
  居中块输出逐字节不变);③边界情形加固:`detect_line_alignments` /
  `detect_line_alignment` 对 n=0/1、零高度/退化框、坐标含 NaN/inf、
  畸形框不再抛异常——非有限/畸形行归中且不参与投票(不污染均值与容
  差),其余行按有限坐标照常判定;④诊断可见性:`detect_line_alignments`
  新增仅关键字参数 `diagnostics`(dict 就地写入行 idx → 对齐边 + 三边
  票数 + 剪切斜率,不影响返回值),静态策略并入
  `PolicyResult.diagnostics["line_alignments"]`、`synthesize_events` 同名
  参数带出、SCENE 分支 DEBUG 日志与轨迹脚本 stderr 留痕,便于真实视频
  排查「为什么判成 left」。单元测试 828 → **839 passed, 1 skipped**(联测修复后再 → 841)。
- **意大利语/西班牙语/葡萄牙语识别语言自始不可用（未知语言名）**：下拉框
  三个语言值用的旧式名 `italian`/`spanish`/`portuguese` 不在 paddleocr 3.7
  的任何语言族里（拉丁语族只认 ISO 代码 `it`/`es`/`pt`，仅 `french`/
  `german` 保留别名），选择后引擎初始化必报
  「No models are available for lang=…」。现下拉框值规范为 `it`/`es`/`pt`
  （旧值本就 100% 失败、无可用配置依赖），并依据 paddleocr 的
  `_PPOCRV6_LANGS`（{ch, chinese_cht, en, japan} ∪ 拉丁语族）把全部拉丁
  语系语言（french/german/it/es/pt）加入 `V6_LANGS` v6 快车道——复用已
  缓存的 PP-OCRv6 模型（免下载 PP-OCRv5_server_det + latin rec，初始化
  秒级），模型档位 tiny/small/medium 亦随之可用；korean/russian/arabic
  无 v6 模型，维持 PP-OCRv5 多语言回落。新增选路回归（拉丁语系 → v6、
  旧式名不得回流）与下拉框语言值断言；12 种语言合成字幕图全量实测
  **12/12 通过、相似度全部 1.00**（含新修复的意/西/葡与繁体）。单元测试
  805 → **806 passed, 1 skipped**。
- **日志面板不再显示 HTML 实体乱码（`&#x27;`）**：日志查看器对每条消息
  `html.escape` 后直接 `QTextEdit.append`——不含尖括号的消息会被 Qt 的
  `mightBeRichText` 判为纯文本插入，转义实体原样露出：含 dict repr 的
  日志行（「OCR engine switched to: paddle (options={&#x27;lang&#x27;…})」、
  「智能跳帧: 抓取区域(ROI) &#x27;roi_0&#x27;…」）每个引号都显示成
  `&#x27;`，含 `<` 的消息同样会露出 `&lt;`。现以
  `<div style="white-space: pre-wrap">` 强制按富文本插入：转义实体正确
  渲染回原字符，HTML 注入防护不变，多行消息（traceback）与行首缩进在
  HTML 解析下原样保留，原文中的字面 `&amp;` 不会被二次反转。
  新增 `tests/test_log_viewer.py` 4 项（引号逐字、尖括号/& 逐字、多行
  缩进保留、多条不串行）；单元测试 799 → **803 passed, 1 skipped**。
- **无选中行时逐 ROI 开关勾选静默丢失（亮度自适应不生效的真正根因）**：
  「亮度自适应/遮挡蒙版/位置标签/场景策略」的即时写回都以「ROI 列表
  当前有选中行」为前提，无选中时静默 `return`；而加载视频 → 自动加载
  ROI autosave 后列表恰恰没有选中行，面板停在空白复位态——用户勾选
  pose + 亮度自适应后直接识别，写回全部丢弃，面板显示已勾选而内存
  `roi_data` 仍是旧值，GUI 轨迹字幕不带变暗跟随标签（autosave json 里
  `motion_auto_brightness: false` 即内存真相，勾选从未落进内存；json
  本身只在「开始识别」时从内存单向导出，不存在旧文件覆盖）。三层修复：
  1. **载入后自动选中并回填**：视频加载/手动加载 ROI 配置后列表无选中
     时选中第一行（按住跳帧），详情面板如实回填已保存值——勾选必然有
     落点（`_select_first_roi_without_seek`）。
  2. **丢弃留痕**：无选中且已有 ROI 条目时勾选，日志给出可见告警
     （`roi_data` 为空的「先配置待新建 ROI」正常流程不打扰）。
  3. **启动前兜底同步**：`run_ocr_pipeline` 在自动备份前以面板为准写回
     选中条目的四个逐 ROI 开关（`_sync_selected_roi_panel_flags`，
     pose 随几何重算），保证面板显示、autosave json、识别管线三者一致。
  新增 `tests/test_roi_pose_tags.py` 回归 4 项（无选中告警、自动选中、
  启动前同步、同步不动时间几何）；用用户真实 autosave（5 点闭合多边形、
  `motion_auto_brightness: false`）headless 复现原流程验证修复。单元测试
  795 → **799 passed, 1 skipped**。
- **LLM 429 有界退避尊重 Retry-After，总等待精确有界**：`_call_llm_api`
  的退避等待现按剩余总预算截断（tenacity 的 `stop_after_delay` 允许最后
  一次 sleep 超出总预算，现单次 ≤60s、总等待精确 ≤300s）；429 响应携带
  Retry-After（delay-seconds 或 HTTP-date；openai SDK/httpx 与 urllib
  HTTPError 两种头形态）时尊重其指引（同样受单次/总上限钳制），每次退避
  等待前打 warning 日志解释管线长停顿。`/v1/models` 探测同样处理，响应体
  读取超时（裸 `TimeoutError`）归一化为可恢复 `RuntimeError`。
- **第三轮代码审查修复（单帧链/策略事件/模板样式/区域偏移/句柄泄漏）**：
  1. **单帧轨迹链不再整条丢弃**：`motion_ass._chain_events` 此前在链尾
     +1 帧延伸之前判零长，单帧链（只在一帧跟踪成功——闪烁跟踪/遮挡恢复
     的典型形态）被 `f1 <= f0` 守卫整条丢弃，该帧字幕完全消失；现先延伸
     再判零长（`f1 < f0` 在严格递增分段下不可能出现）。遮罩事件
     （`scene_text_policy._mask_events_for_block`）同款修复。
  2. **wrap_cjk 行首禁则不产空行**：极窄带宽（max_chars=1）时禁则字符
     下挪把空串写进行表；修复后宁超限不空行。
  3. **策略事件不进 LLM 碎片合并/润色**：LLM 碎片合并重建 dict 会丢掉
     policy/layer 标记（遮罩与文本的图层关系随之丢失）；润色阶段只送
     普通字幕文本，遮罩/mask_only 排版参考行（空 body）不再交给模型
     「补全」。
  4. **模板路径补齐 NoteBox 样式**：自定义模板的场景文字策略回退链或
     轨迹事件产出 NoteBox 事件而模板头缺该样式时，libass 回退 Default
     丢失不透明底框；现按需在样式段补齐（模板已含 NoteBox 不重复）。
  5. **motion_detector region 越缘偏移**：region 越过画面左/上边缘时，
     裁剪起点钳到 0 而 OCR 框偏移量用未钳值，检出区域整体平移；现偏移
     与裁剪用同一钳后值。
  6. **精修线程句柄泄漏**：OCR 引擎构造失败时释放已打开的 VideoCapture。
  7. **CLI 两处**：临时 `QCoreApplication` 被引用计数立即回收
     （`instance()` 复回 None）改为保留引用；`--roi-file` 的轨迹规格透传
     start_frame/end_frame/scene_text_policy，与 GUI 主流水线路径等价。
  新增 `tests/test_review_fixes_3.py` 覆盖上述 1–4、5（负 region 位移
  不变性）与 3（不可达端口保证合并若发生必失败）；单测
  766 → **795 passed, 1 skipped**。
- **矩形遮罩链尾 +1 帧延伸（mask/mask_only 遮罩与文本时间对齐）**：
  `core/scene_text_policy.py` 的 `_mask_events_for_block` 此前链尾结束时间
  取尾帧自身 `time_sec`，而文本事件已经过 motion_ass 的「链尾 +1 帧距」
  修复——链尾最后一帧文本仍在而遮罩已消失，原字透出约 1 帧时长。现套用
  同款修复（末段 `t1 = _next_frame_time(...)`，与多边形
  `mask_polygon_clip`/external NoteBox/whitespace 路径既有语义一致），
  遮罩与文本事件结束时间逐条相同；回归基线字面量同步（遮罩 0.36→0.40，
  文本/external 不变）。DMG 实测：链 1/链 2 遮罩与文本均止于
  `0:00:03.54`/`0:00:10.26`，链 1 尾帧（f84，t=3.504）遮罩仍覆盖。
  另修两处 `ruff --select F`：`core/screen_luma.py` 补 `Dict` 导入
  （F821）、`tests/test_review_fixes_2.py` 删未用导入（F401）。
  验收记录：`docs/superpowers/evidence/2026-09-18-mask-only-acceptance/`。
- **轨迹字幕三项收尾修复**（DMG 手机场景实测 + 单测 765 项零回归）：
  1. **融合行噪声过滤**：轨迹管线此前把手机状态栏/导航栏图标的误读
     （`<`、`>`、`000`、单字象形误读「血」「言」）原样合成为 Scene 事件
     （DMG 实测底缘 y≈1050 处产出垃圾行）。现将静态路径的噪声判据抽为
     `core.text_utils.is_noise_text`（`_is_noise_body` 完全委托，行为不变），
     `build_motion_events` 行融合后按其剔除（`MotionAssConfig.junk_line_filter`，
     默认开、`--config-json` 可关，剔除经 log 留痕）。DMG 实测：4 行噪声
     全部剔除，事件 32→24 且全部为真实文本。
  2. **遮挡检测边缘假阳性**：展开窗口四边由单应边界采样/插值产生稳定的
     贴边窄条伪影（DMG 实测顶部 5px 整幅条 + 左缘 ≤28px×72% 高条带，共
     20 个采样帧误报），此前仅靠行框重叠门槛无害过滤——文字贴近平面
     边缘时会被误加 `\iclip` 藏字。现 `detect_occlusion_polygons` 在
     形态学清理后清零四边 `edge_margin_px`（默认 8）忽略带，并按
     「贴有效边界 + 厚度 ≤ `edge_sliver_max_px`（24）+ 覆盖 ≥
     `edge_sliver_min_frac`（0.5）×边长」剔除贴边细长条带；真实贴边
     遮挡物（厚度超上限的块状区域）不受影响。DMG 实测误报帧 20→9
     （余下为平面内部的滚动内容差异，不与行框相交达标，最终 `\iclip`
     仍为 0——该片段无真实遮挡）。
  3. **ROI 列表轨迹标记**：列表条目此前只显示 [颜色掩膜]/[模糊]/
     [淡入淡出微调]/[自动过滤]，勾选「写入画面位置标签/亮度自适应/
     遮挡蒙版」后从列表完全看不出设置已写入（易误判「勾了没生效」）。
     现随 ROI 状态显示 [位置标签]/[亮度自适应]/[遮挡蒙版]，三语 i18n
     补齐。新增 `tests/test_roi_list_tags.py`（3 项）、
     `tests/test_text_utils_noise.py`（9 项）、遮挡边缘伪影单测 3 项与
     垃圾行过滤端到端 2 项；单元测试 745 → **765**。

- **全库代码审查修复七项**（编译 + 745 项测试零回归）：
  1. **chunk 并行协调器死循环**：窗口第二次失败由心跳/存活检测
     （`_check_liveness`，worker 被 OOM kill 等静默死亡场景）检出时，
     顺序兜底只置 `done` 不发消息，该窗口 index 永远留在 `pending`
     ——主循环空转、进度条永久卡死（唯一出口是用户取消）；现在
     liveness 扫描对已 done 窗口同步出队，运行能正常收敛返回。
  2. **ASS 数值标签误带圆括号（静默失效）**：`\frz(8.0)`、`\fs(30)`
     不是合法 ASS 语法（数值标签参数直接跟数值，仅 `\pos/\move/\t/
     \clip` 用括号），libass 解析失败即忽略——pose 旋转、场景字静态
     mask 的字号设置全部无效。修复 `styling.py` 两处与
     `scene_text_policy.py` 一处，与 `core/motion_ass.py` 的正确写法
     对齐；`\iclip` 遮挡跨度随链尾结束时间 +1 帧同步修正测试。
  3. **`motion_ass.format_ass_time` 浮点截断缺 epsilon**：
     `POS_MSEC/1000` 秒值常落在真值一个 ULP 之下（`1.16*100 ==
     115.999…`），直接截断把约 5% 的帧时间戳提前 1cs；补 `+1e-6`
     与主流水线 `timeline.py` 的同源修复对齐。
  4. **轨迹 `\move` 链尾少 1 帧**：末段（含单段）结束时间取尾帧自身
     时刻，而 ASS 的 End 是排他边界——最后一个被跟踪 ok 帧整帧无
     字幕；现延伸到下一帧时刻（与 dense 兜底、主流水线 generator 的
     「尾帧 + 1 帧」语义一致）。
  5. **SCENE 主导 ROI 误丢边缘组**：`roi_filters` 中「主导是场景字时
     不去过滤 BOTTOM/TOP」的注释与实现相反，旧 `continue` 把组中心
     落在画面顶/底位置带的组静默丢弃——恰好误杀靠近边缘的真实场景
     字（招牌/手机屏等）；按注释与 generator 常量注释的既定意图删除
     该分支，位置分类对场景字不可靠，杂项仅靠高度比过滤兜底。
  6. **OCR 引擎 `_predict_lock` 串行化并行精修**：两个引擎的锁此前是
     **类属性**，`build_standalone_engine` 为每个精修线程构建的独立
     实例全部争抢同一把锁，OCR 推理退化为单线程，与
     `refine_executor`/`ocr_engine_manager` 文档声明的「独立实例不在
     共享锁上串行」直接矛盾；锁改为实例级（每实例一个 predictor，
     符合 Paddle/ORT 官方并发建议）。
  7. **关窗时模型列表拉取线程未收尾（qFatal 风险）**：`_FetchOpenAIModelsThread`
     超时未结束既不 cancel 也不 terminate，窗口销毁运行中的 QThread
     触发 Qt qFatal；被新拉取顶替的旧线程更是完全脱离跟踪（连退出
     确认框都不弹）。现在全部 fetch 线程登记在 `_fetch_threads` 列表，
     `shutdown_background_threads` 超时后按 closeEvent 同策略强杀并
     恢复按钮状态。另修 `test_occlusion_mask` docstring 的
     `\i` 转义 SyntaxWarning。单元测试 739 → **745**（新增
     `tests/test_roi_filters.py` 3 项 + 死循环/时间戳/实例锁回归各 1）。
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
