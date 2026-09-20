# 平面跟踪丢锁重捕获实施计划

> **⚠️ 状态：Phase 1 已被实测证伪，本计划不再按原样执行（2026-09-20）。**
> Task 1 的实测（计划自己的默认参数）显示分级重捕获在 12.mp4 上把 ok 从 13/700
> 只变成 14/700，且其论证前提（锚帧含内容无关结构）在素材上为假；根因实为
> **锚定帧选择**，已按实测改做锚定判据修复 + 丢锁原因可观测性并落地（见
> `CHANGELOG.md`「修复」节与 `docs/testing.md` 2026-09-20 节，12.mp4 ok
> 4.2% → 92.4%）。**实测数据与逐条修订见文末「实测修订」**；仍未做的是 4K
> 歌词条所需的 **Phase 2 按链重锚定**（本计划 §Phase 2 的量化触发条件已成立）。

> **For agentic workers:** REQUIRED SUB-SKILL: 按任务逐项执行；每项先写失败测试，再实现，再跑定向测试与真实素材验收，最后小步 commit。**不得跳过 Task 0 的基线与 Task 1 的实测分类**——本计划的参数取值必须由实测数据决定，不允许凭直觉调阈值。

**Goal:** 让轨迹管线在平面跟踪丢锁后能**重新捕获并继续产出字幕**，同时**绝不破坏**「所有 ok 帧共享同一参考平面坐标系」这一前提（破坏它会静默产出错位字幕，比断链更糟）。

**Architecture:** 保持 `core/scene_plane_tracker.py` 为跟踪核心、「`track_plane_frames` 处理内存帧序列 / `track_plane` 流式解码」的双入口结构。重捕获作为主循环内的**分级尝试**引入，命中时把 `h_total` **直接对回锚帧平面**（`h_total = h_anchor`，不累加），因此下游 `core/motion_ass.build_line_tracks` / `pose_verify` / `synthesize_events` **无需改动坐标约定**；丢失段保持缺口，`_split_runs` 自然切链，事件按链产出。只有在 Phase 1 实测无法达成验收时，才进入 Phase 2（per-chain 坐标系，需改下游）。

**Tech Stack:** Python 3.12、NumPy、OpenCV（ORB、`findHomography`/RANSAC）、pytest、ffmpeg/libass、现有 4K/1080p 真实素材。

**Spec:** [`docs/superpowers/specs/2026-09-20-plane-reacquisition-design.md`](../specs/2026-09-20-plane-reacquisition-design.md)（实施规格：代码级现状、算法与门限表、数据结构与序列化兼容、测试矩阵、参数定值流程）

**Evidence（本计划的实测依据）:** `CHANGELOG.md` 的「已知限制」两条 + `docs/testing.md` 的「未发布修复轮」表（12.mp4 与 4K 的 ok 覆盖率、回退链、内存口径）。

## Global Constraints

- **坐标不变量（最重要）**：任何被标为 `ok` 的帧，其 `homography` 必须能把「初始锚帧平面坐标」映到该帧画面；禁止写入参考系不同的单应。新增校验必须能自动检出违反。
- **不 seek、不跳帧**：沿用顺序解码策略（重捕获不得引入 `cap.set` 随机定位或 `cap.grab` 跳读）。
- **零回归**：11.mp4（651/651 ok）与 DMG（174/246 ok）的轨迹 JSON 与 `.ass` 产物必须**逐字节不变**；`pytest tests/ -q` 全绿；`ruff check --select F` 全绿。
- **默认值保守**：新增参数默认值必须使「从不丢锁的视频」输出逐字节不变（重捕获只在连续 lost 之后介入）。
- **内存不退化**：4K 轨迹管线峰值不得超过当前 2.2 GB（重捕获不得引入整段帧驻留）。
- 任何视觉/数值差异必须保存输入配置、轨迹 JSON、ASS、渲染帧与日志。

## 背景与实测证据

轨迹 ROI 一旦丢锁就**永久**丢锁，导致该 ROI 整体回退静态策略。两次独立复现：

| 素材 | ROI | ok 帧 | ok 窗口 | 后果 |
| --- | --- | --- | --- | --- |
| 12.mp4（1920×1080，1871 帧） | `12_roi_motion.json` | 79/1871（4.2%） | t=1.42–4.67 s（画面空白） | 关键帧池无文字 → `RuntimeError` → 回退静态策略（mask→external，1 条 NoteBox） |
| 4K NCOP（3840×2160，801 帧） | `1000,1990 2900,1990 2900,2145 1000,2145` | 56/801（7.0%） | t=6.51–8.80 s | 同上（exit 2） |

关键观察：两次的 ok 窗口**都在文字/内容出现之前**（12.mp4 t=4.5 s 帧 ROI 为空白屏，t≈6 s 聊天内容出现后再未捕获）；而同一区域走静态主流水线可识别出 1600 条事件（12.mp4，t>10 s 占 1579 条）与 4 条歌词（4K 0–33 s）——**文字确实存在，瓶颈在跟踪**。

复现命令（内存与覆盖率都用 `/usr/bin/time -v` 读 `Maximum resident set size`）：

```bash
# 12.mp4
.venv/bin/python scripts/motion_ass.py --video test/12.mp4 \
  --quad-file test_run/12_quad.json --start-frame 0 --end-frame 1870 \
  --scene-text-policy mask --out /tmp/12_motion.ass
# 4K
.venv/bin/python scripts/motion_ass.py --video "test/一周的朋友 NCOP(虹のかけら)_4k_high_quality_x264.mp4" \
  --quad "1000,1990 2900,1990 2900,2145 1000,2145" \
  --start-frame 0 --end-frame 800 --scene-text-policy overlap --out /tmp/4k_overlap.ass
```

## 根因（代码级）

1. **主循环没有重捕获分支。** `track_plane_frames` 只在成功时更新 `ref`；失败帧仅保留 `ref` 继续下一帧（`core/scene_plane_tracker.py` 主循环；注释「保留 ref 状态供后续帧重捕获」只覆盖「内容没变、门限偶尔过严」这一种情况）。初始化有向后探测（`_MAX_INIT_PROBE_FRAMES = 900`、`_INIT_STRONG_FEATURES = 48` 的「特征丰富帧」优先），主循环没有对应机制。
2. **匹配源点是上一好帧的特征描述子。** 空白屏帧上检测到的特征多为屏幕内容纹理/JPEG 噪声；内容一变（文字出现/滚动），这些描述子整体失配，而 `min_matches = 12`、`lowe_ratio = 0.75`、搜索区仅 `_bbox_expand(ref.quad, max(24, 0.35·diag))`——既没有别的参照物，也没有放宽余地。
3. **坐标前提是隐式的。** `_homography_between(a,b) = H_b · inv(H_a)`（`core/motion_ass.py`），`build_line_tracks` 用**同一个** `ref` 把所有 ok 帧的行框映射出来 ⇒ 所有 ok 帧必须同属初始帧平面。因此「另起参考帧往后跟」这类朴素重锚定**不能**直接接进现有链路：映出来的位置会静默错位。
4. **丢锁原因不可观测。** `TrackedQuad` 只存 `status/inlier_ratio/reproj_error/matches`，没有「为什么判 lost」的字段（匹配对不足 / 内点率不足 / 重投影超限 / 面积跳变 / 顶点跳变 / 单应退化），也没有链标识。没有这个就无法判断「放宽哪个门限」是安全的。

## 非目标

- 不承诺「内容大变后必然跟住」。Phase 1 只做**能以锚帧为参照重捕获**的情形；内容彻底改变（新画面、切镜）属于 Phase 2/3。
- 不改现有阈值默认值语义、不改 `track_plane` / `track_plane_frames` 公开签名、不动 OCR 侧内存（4K 剩余内存支配项另案，见 `CHANGELOG.md` 已知限制）。
- 不为「丢锁」引入人工交互或 GUI 改造（只加日志/摘要字段）。

## 设计

### Phase 1（必要，低风险）：以锚帧为全局参照的分级重捕获

在 `track_plane_frames` 主循环内加入：

1. **连续 lost 计数**：`lost_streak += 1`（成功清零）。达到 `reacquire_after_lost`（默认 12 帧）后才开始尝试重捕获，且此后**每 `reacquire_interval` 帧**（默认 6）尝试一次——避免每帧都做放宽匹配拖慢常态。
2. **分级尝试**（按顺序，命中即止；全程顺序解码）：
   - **a. 对锚帧**（`anchor_pts` / `anchor_desc`，锚定时留存）：当前帧重新检测特征（`feature_count` 提高到 `reacquire_feature_count`，默认 4000），搜索区放宽到 `reacquire_search_pad_ratio`（默认 1.5×diag，必要时整帧），`lowe_ratio` 放宽到 `reacquire_lowe_ratio`（默认 0.85），`min_matches` 降到 `reacquire_min_matches`（默认 8），RANSAC 阈值放宽到 `reacquire_ransac_thresh`（默认 5.0）。命中则 `h_total = h_anchor`（**直测锚帧**，不累加）。
   - **b. 对最后一个好帧**（`ref`）：同上放宽，用 `h_total = h_step @ ref.h_total`。这一级覆盖「平面没变、只是门限偶尔过严」的情形。
   - 命中后仍要过**几何闸门**：单应有限、四边形凸、面积比在 `max_area_jump` 内（相对**最后已知 quad**，不是相对锚帧）、顶点跳变不超过 `max_jump_ratio · 边长`（相对**最后已知位置**，允许大位移——重捕获本来就会跳，故这一级的跳变上限用独立参数 `reacquire_max_jump_ratio`，默认放宽到 3.0，并保留绝对上限防离谱误配）。
3. **失败留痕**（见「必要配套」第 1 条）：每帧记录 `lost_reason`；重捕获尝试记录「第几级尝试过、各门限的实测值」。
4. **配置化**：全部参数进入 `tracking_params`，默认值使「从不丢锁的视频」输出不变（11.mp4/DMG 全 ok ⇒ 重捕获路径不被触发 ⇒ 逐字节一致）。`MotionAssConfig` 增加同名 `track_*` 字段供 `--config-json` 调。

**为什么这样能修 12.mp4/4K 的形态**：新内容出现在**同一物理平面**上（手机屏/显示器），而锚帧里含有**内容无关的稳定结构**（屏框、边框、机身轮廓）。放宽门限 + 提高特征数后，锚帧的结构特征与当前帧的对应特征仍能配上；一旦配上，`h_total` 直测锚帧，坐标天然正确。

**若 Phase 1 实测达不到验收**（锚帧是空白屏、结构特征不足），再评估 Phase 2——**用 Task 1 的实测数据决策，不提前实现**。

### Phase 2（条件触发，中等改造）：per-chain 坐标系

- `TrackedQuad` 增加 `plane_id`（链标识）；无法对回初始平面时**另起平面**（新参考帧 + 自洽 `h_total`）。
- 下游需按链分别做 OCR 锚定/融合/行轨迹（`scripts/motion_ass.py` 的关键帧池与 `fuse_samples_by_position`、`build_line_tracks` 的 `ref`、`pose_verify` 的输入），`synthesize_events` 已能按 `_split_runs` 分链出事件。
- 触发条件（量化）：Phase 1 落地后 12.mp4 ok 占比仍 < 30%，且 Task 1 实测显示「失败帧与锚帧的共同特征数中位数 < `reacquire_min_matches`」。

### Phase 3（可选）：结构性特征优先

对手机/显示器类平面，把 `_detect_features` 的 mask 加权到 quad 边缘带（屏框/边框），或为屏幕类 ROI 提供「结构优先」开关，提高重捕获成功率。数据驱动决定是否做。

## 必要配套内容（不属于「可选」）

1. **丢锁原因可观测性（Phase 1 的前置条件）**
   - `TrackedQuad` 增加 `lost_reason: Optional[str]`（枚举：`few_matches` / `low_inlier` / `high_reproj` / `area_jump` / `vertex_jump` / `degenerate_h` / `no_ref`），`ok` 帧为 `None` 或 `"reacquired"` 标记（记录本次是通过哪一级重捕获恢复的）。
   - 同步 `save_trajectory` 与 `load_trajectory`（`core/scene_plane_tracker.py`，两者都已存在；轨迹 JSON **只新增键、不删旧键**，加载侧缺键按 `None` 兼容旧文件）、`[1/5]` 摘要（各原因计数 + 重捕获次数 + 链数）。当前 `docs/development_plan.md` 并未描述轨迹 JSON schema，需**新增**一段 schema 说明（列出字段、含义、兼容约定）。
   - **没有这一项，任务 2 的门限取值只能靠猜**；也保留「下次再退化时能一眼看出退化在哪」的能力。
2. **阈值口径与分辨率一致**
   - 跟踪器阈值仍是绝对量：`max_reproj_error=4.0`、`JUMP_FLOOR_PX=24`、`min_matches=12`、`lowe_ratio=0.75`、`SEARCH_PAD_RATIO=0.35`。而 `collapse_settle_tol_px`、`verify_*` 阈值已按 `video_height/1080` 归一——**同一套代码在 4K 与 1080p 上语义不同**。
   - 处置二选一并在文档写明：① 对像素量（`max_reproj_error`、`JUMP_FLOOR_PX`、搜索外扩绝对下限）按 `video_height/1080` 归一；② 明确文档化「跟踪器不归一」的理由与其后果。**倾向 ①**（与既有归一化口径一致），但必须以「11.mp4/DMG 逐字节不变」为前置验收（1080p 系数为 1.0）。
3. **覆盖率与链报告**
   - `build_motion_events` 的 summary 增加：链数、各链 ok 窗口、覆盖率、重捕获次数、各 lost 原因占比；GUI 侧（`core/pipeline_worker.py` 的告警）据此在回退前给出「该 ROI 仅 X% 可用」的明确提示。
   - 现有告警只在 <50% 时打一行文本（`scripts/motion_ass.py`），扩展为结构化摘要。
4. **回归素材与慢测**
   - 12.mp4 与 4K NCOP 固化为「丢锁素材」，记录**预期 ok 覆盖率与 ok 窗口**作为回归指标；完整跑很慢（4K 801 帧 ≈ 50 s，12.mp4 1871 帧更久）⇒ 用环境变量门控的慢测（如 `MOTION_SLOW_TESTS=1`）+ 一份期望值清单，避免以后又静默退化。
   - 快测用合成视频覆盖重捕获路径（见验收标准）。
5. **与 `lost_hold_sec` 的交互**
   - `lost_hold_sec > 0` 会把 lost 缺口桥接起来复用前段位姿（`_merge_chains_with_hold`）。重捕获之后跨越缺口的桥接会产生「假 `\move`」。处置：重捕获发生的缺口**强制不桥接**，并在摘要里标注；同时更新该参数的注释说明。
6. **文档更新**
   - `CHANGELOG.md`：Phase 1 落地后把「平面跟踪没有丢锁重捕机制」从「已知限制」移到「修复」，并写清实测覆盖率改善；`docs/testing.md` 增加一行 12.mp4/4K 的覆盖率与烧帧目检记录。

## 验收标准（量化）

| 项目 | 门槛 |
| --- | --- |
| 12.mp4 轨迹 ROI | ok 帧占比 **≥60%**（现状 4.2%），且 t>10 s 的窗口内产出 `\move`/`\pos` 事件（不再回退为单条 NoteBox） |
| 4K NCOP 0–800 | ok 帧占比 **≥60%**（现状 7.0%），产出日文歌词轨迹事件 |
| 零回归 | 11.mp4 / DMG 轨迹 JSON 与 `.ass` **逐字节相同**；`pytest tests/ -q` 与 `ruff check --select F` 全绿 |
| 坐标不变量 | 新增自动校验：任取两个 `ok` 帧，用 `_homography_between` 往返映射同一行框，误差 ≤ 2 px（在 1080p 素材上）；违反即测试失败 |
| 事件锚点不变量 | 12.mp4/4K 的静止 `\pos` 锚点距其跨度内每一帧真实锚点 ≤ `collapse_settle_tol_px`（复用 `test_run/check_collapse.py` 的同口径校验；该目录是临时工作区、可能已被清理，清理后按「关闭塌缩的逐段 `\move` 作真值」重建即可） |
| 内存 | 4K 轨迹管线峰值 ≤ 2.2 GB |
| 误捕获 | 构造「平面离场 + 相似干扰物」合成场景：**不得**把干扰物当成平面（允许不重捕获） |

## 任务分解

### Task 0: 建立不可破坏的基线

**Files:**
- Create: `docs/superpowers/evidence/2026-09-20-plane-reacquisition-baseline/`（manifest、pytest 输出、工作树状态、三份轨迹 JSON 的 SHA-256）

- [ ] 运行 `git rev-parse HEAD`、`git status --short`、`.venv/bin/python -m pytest tests/ -q`，保存完整输出与计数（当前基线 **974 passed, 1 skipped**）。
- [ ] 生成 `test_run/bl_traj_{11,dmg,4k}.json` 与 `test_run/out/{11_overlap,dmg_overlap}.ass` 的 SHA-256 清单。
- [ ] 记录 12.mp4 / 4K 的当前 ok 覆盖率与 ok 窗口（本文件「背景与实测证据」表即基线）。
- [ ] 只提交证据文件，不改实现。

### Task 1: 丢锁原因实测分类（先测后调）

**Files:**
- Modify: `core/scene_plane_tracker.py`（仅加 `lost_reason` 记录，不改门限）
- Test: `tests/test_scene_plane_tracker.py`

- [ ] 先写失败测试：各门限失败路径分别产出正确 `lost_reason`；`ok` 帧不带原因；字段向后兼容（旧 JSON 可读）。
- [ ] 实现 `lost_reason` 记录（主循环各失败分支显式赋值），并在 `[1/5]` 摘要打印各原因计数。
- [ ] 在 12.mp4（0–1870）与 4K（0–800）上实测，输出：**各 lost 原因占比**、失败帧与锚帧的**共同特征数分布**（中位数/P90）、失败帧与锚帧的**匹配对分布**。
- [ ] 依据数据写下结论：主因是「匹配对不足」还是「内点率/重投影不达标」，并据此**修订本计划 Task 2 的参数默认值**（把修订记录写进本文件末尾的「实测修订」小节）。
- [ ] 运行 `pytest tests/test_scene_plane_tracker.py -q`；提交 `feat: record plane-tracking loss reasons`。

### Task 2: Phase 1 分级重捕获

**Files:**
- Modify: `core/scene_plane_tracker.py`、`core/motion_ass.py`（`MotionAssConfig` 的 `track_*` 字段）、`scripts/motion_ass.py`（透传 + summary）
- Test: `tests/test_scene_plane_tracker.py`、`tests/test_motion_ass_cli.py`

- [ ] 先写失败测试（合成视频）：① 空白引导 → 内容大变（特征全变）→ **重捕获成功**且 `h_total` 直测锚帧（用合成单应真值校验映射误差 ≤ 1 px）；② 全程遮挡 → 不误报（保持 lost）；③ 「平面离场 + 相似干扰物」→ 不重捕获；④ 连续 lost 未达 `reacquire_after_lost` 时不触发（与旧实现逐字节一致）；⑤ 重捕获后 `lost_reason="reacquired"` 可观测。
- [ ] 实现 `anchor_pts/anchor_desc` 留存、连续 lost 计数、两级分级尝试与几何闸门（参数与默认值见「设计 Phase 1」；按 Task 1 实测修订）。
- [ ] `MotionAssConfig` 增加 `track_reacquire_*` 字段并透传；`build_motion_events` 摘要增加重捕获次数与链数。
- [ ] 运行定向测试；提交 `feat: reacquire plane after tracking loss`。

### Task 3: 阈值口径一致性

**Files:**
- Modify: `core/scene_plane_tracker.py`（归一化像素量阈值）、`tests/test_scene_plane_tracker.py`
- Docs: `CHANGELOG.md`（口径说明）

- [ ] 先写失败测试：`video_height=2160` 时 `max_reproj_error` / `JUMP_FLOOR_PX` / 搜索外扩绝对下限按 `h/1080` 缩放；`video_height` 缺省或 1080 时**逐字节不变**。
- [ ] 实现归一（若 Task 1 数据显示归一反而有害，则改为在文档中给出不归一的实测理由）。
- [ ] 复跑 11.mp4 / DMG 轨迹 JSON 与 `.ass`，确认逐字节一致；提交 `feat: normalize tracker pixel thresholds by resolution`。

### Task 4: 12.mp4 与 4K 真实素材验收

**Files:**
- Create: `docs/superpowers/evidence/2026-09-20-plane-reacquisition/`（轨迹 JSON、ASS、渲染帧、覆盖率表）
- Modify: `docs/testing.md`

- [ ] 跑 12.mp4（0–1870，mask）与 4K（0–800，overlap），记录 ok 占比、链数、重捕获次数、事件数，与验收门槛比对。
- [ ] 用 `test/burn_frames.sh` 在重捕获点前后各烧 3 帧，目检字幕是否落在文字上（重点：重捕获后是否跳位错误、是否出现幽灵文字）。
- [ ] 若未达门槛：回到 Task 1 补测（例如「平面真的离场」的证据），并在本文件记录是否需要 Phase 2；**不要靠继续放宽门限硬凑指标**。
- [ ] 更新 `docs/testing.md` 与 `CHANGELOG.md`（已知限制 → 修复，或如实记录未达标与原因）。

### Task 5: `lost_hold_sec` 交互与文档收尾

**Files:**
- Modify: `core/motion_ass.py`（桥接逻辑 + 注释）、`tests/test_motion_ass*.py`
- Docs: `CHANGELOG.md`、`docs/development_plan.md`（schema 说明）

- [ ] 先写失败测试：重捕获缺口不被 `lost_hold_sec > 0` 桥接（对比桥接前后的事件端点）。
- [ ] 实现并补注释；跑全量 `.venv/bin/python -m pytest tests/ -q` 与 `ruff check . --select F`。
- [ ] 提交并同步文档。

## 风险与缓解

| 风险 | 缓解 |
| --- | --- |
| 放宽门限导致**误捕获**（相似纹理被当成平面） | 几何闸门（凸性/面积比/跳变上限）+ 「相对最后已知位置」约束 + Task 2 的干扰物合成用例 + 真实素材目检 |
| 重捕获后**坐标错位**（最危险，且静默） | 强制 `h_total` 直测锚帧；新增「任取两 ok 帧往返映射误差 ≤ 2 px」的自动校验作为常驻测试 |
| 参数凭直觉调，越调越糟 | Task 1 先实测分类、Task 2 按数据定值并把修订记进本文件；真实素材验收不达标时回到实测而非继续放宽 |
| 重捕获引入内存/耗时退化 | 重捕获只在连续 lost 后触发；4K 峰值 ≤ 2.2 GB 作为硬门槛 |
| 12.mp4/4K 的根因是「平面离场」而非「内容改变」 | Task 1 的实测（共同特征数分布）给出判据；若确认离场则 Phase 1 本就不该成功，转 Phase 2 评估 |

## 实测修订

> 执行 Task 1 / Task 4 后在此记录对本计划参数与结论的修订（含实测数据与日期）。

**2026-09-20 · Task 1 实测（等价于本计划的「先测后调」）已完成，结论是 Phase 1 方案被证伪，已改做锚定判据修复。**

### 1. Phase 1（分级重捕获）实测无效

用本计划自己的默认参数（`reacquire_lowe_ratio=0.85`、`reacquire_min_matches=8`、
`reacquire_ransac_thresh=5.0`、`reacquire_max_jump_diag=1.0`）在 12.mp4 上跑
100–800 帧：ok 由 13/700 变 **14/700**（等于没有改善）。原因：

- 帧 113 是**硬切**（单帧内匹配数 903 → 0），之后锚帧与当前帧的共同特征数长期只有
  **2–10**（门槛 8），能过闸门的都是垃圾（例如面积比 0.05、非凸）。
- 放松到 `lowe=0.9`、`min_matches=6`、跳变 5× 边长、面积比 ±2.0 时确能「恢复」
  272–447 帧，但**把锚帧特征数由 2000 改成 4000 这个无关参数就让「恢复」消失**——
  不可复现的匹配不是跟踪。同时该组合会接受**面积为 0 的塌缩四边形**（`ok` 帧
  `area_ratio=0.0`），正是本计划列为「最危险」的静默坐标错位。
- §4.1 的论证前提不成立：「锚帧含内容无关的结构（屏框/边框/机身轮廓）」在该素材上
  为假——锚帧 34 的 quad 内部特征数为 **0**；且「提高 `reacquire_feature_count`
  能补上锚帧结构」也不成立——锚帧特征由 `region0` 掩码与 init 时的 `feature_count`
  决定，2000 与 4000 都只测到 61 个。
- §4.3 的阈值归一（Task 3）与实测失败模式正交：实测主因是**特征饥饿**（`few_matches`），
  不是阈值偏严。
- 需注意 12.mp4 的 ROI 是 **2D 动画 ED montage**（标题卡 → 聊天 UI 图形），不是
  「同一物理平面上的内容变化」，跨切镜重捕获亦与本计划「非目标」自相冲突；
  §9 的 ≥60% 门槛只能靠误匹配达到。

### 2. 根因实测：锚定帧选择（已修，见 CHANGELOG「修复」节）

旧规则在 `region0`（quad 外扩 `max(24, 0.35·diag)`，面积约 quad 的 3 倍）内取**第一个**
≥ `strong_need`（48）的帧就 `break`；邻域纹理足以让「平面本身完全空白」的帧达标。
实测（`feature_count=2000`，quad 内部 = bbox 外扩 24px）：

| 素材 | 旧锚定帧 | 该帧 region0 / quad 内部 | 真实内容起点 | 修复后锚定帧 | 修复后 ok 占比 |
| --- | --- | --- | --- | --- | --- |
| 12.mp4 | 34（t=1.42s） | 61 / **0** | 帧 130（quad 内 1937） | **123** | 4.2% → **92.4%** |
| 4K NCOP | 156（t=6.51s） | 50 / **0** | 帧 234（quad 内 1393） | **230** | 7.0% → **31.8%** |
| 11.mp4 | 0 | 2000 / 2000 | — | 0（不变） | 651/651 不变 |
| DMG | 0 | 2000 / 2000 | — | 0（不变） | 174/246 不变 |

阈值 `_INIT_ROI_MIN_FEATURES=192` 由分离度导出（坏锚定帧 quad 内 0–151、好锚定帧
1393–2000）；**未按本计划 §4.3 归一像素阈值**（理由见上，且与实测失败模式无关）。

### 3. 未做与剩余

- **未做**：`reacquire_*` 匹配机制（本轮证伪）、Task 3 阈值归一（正交）、
  `reacquired` 字段与 `test_run/cmp_traj.py`（重捕获不做则不需要）。
- **已完成**：`lost_reason` + `pre_anchor` 可观测性（本计划「必要配套」第 1 条）、
  摘要 `lost_reasons`/`chains`（第 3 条）——实测直方图已可用于定位退化：
  DMG `non_convex=72`、12.mp4 `pre_anchor=123, few_matches=19`、
  4K `few_matches=264, pre_anchor=230, low_inlier=40, non_convex=9, area_jump=3`。
- **Phase 2 已按另解落地（2026-09-20，第二轮）**：未采用「`TrackedQuad` 加链标识 +
  下游按链处理」，改用**分块重锚定**——`scan_content_windows` 扫内容窗口，对覆盖率
  不足的窗口按 ≤120 帧切块，**每块独立跑一趟** `build_motion_events`（各自锚定、
  `h_total=I`、单链），按窗口替换其它趟次的重叠事件后合并。因为每趟都是单链，
  `build_line_tracks` 的单 `ref_frame` 约定与本文 §3.2 的坐标不变量**逐段成立**，
  下游无需改动。实测 4K ok 31.8% → **66.4%**（四行歌词全部产出），11.mp4/DMG/12.mp4
  不触发（ok 占比 ≥0.6）故四份 `.ass` 与 11.mp4 轨迹 JSON 仍逐字节相同。
- **新发现的独立缺陷（不属于本计划范围）**：4K 歌词条的位姿由跟踪侧给出且实测正确
  （行心稳定在平面 (1914, 2072)），但 `verify_screen_pose`（默认开）的模板匹配校正
  把它改到 y≈1858–1957（偏离 115–215px、超出条带）并把帧 619–682 判为不可见删除，
  使一行歌词碎成 10 条错位事件；关闭该校正后输出为单条正确 `\pos`。该组件是既有
  实现，且 11.mp4/DMG 的 `.ass` 逐字节门槛依赖其当前行为，故未改，需单独立项
  （详见 CHANGELOG「已知限制」）。
- 本计划 Global Constraints 的零回归硬门槛已满足：11.mp4/DMG 的轨迹 JSON 与四份
  `.ass` 全部逐字节相同（见 `docs/testing.md` 2026-09-20 节）。
