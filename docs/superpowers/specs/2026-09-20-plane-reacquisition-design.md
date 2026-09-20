# 平面跟踪丢锁重捕获设计（实施规格）

- 日期：2026-09-20
- 状态：**已作废（Phase 1 被实测证伪）**。实施前的实测（等价于 Task 1）显示：按本文
  §7 默认参数分级重捕获在 12.mp4 上 ok 仅由 13/700 变 14/700，跨帧 113 硬切后锚帧与
  当前帧共同特征数长期只有 2–10（门槛 8），且 §4.1 的论证前提「锚帧含内容无关结构」在
  素材上为假（锚帧 quad 内部特征数 0）。根因实为**锚定帧选择**（§3.3 所述 region0 判据
  可被邻域纹理满足），已改做「quad 内部特征丰富度优先」的锚定判据并落地（12.mp4 ok
  4.2% → 92.4%；详见 `CHANGELOG.md`「修复」节、`docs/testing.md` 2026-09-20 节、
  实施计划文末「实测修订」）。本文保留作为**证伪记录**：§3.2 的坐标不变量、§4.2 的
  序列化兼容约定、§8 的不变量校验与 §11 的风险表仍然有效，其中「重捕获必须直测锚帧」
  一条是后续 Phase 2 按链重锚定必须继续遵守的约束。
- 实施计划：[`docs/superpowers/plans/2026-09-20-plane-reacquisition-plan.md`](../plans/2026-09-20-plane-reacquisition-plan.md)
- 前置：`core/scene_plane_tracker.py`（流式帧来源已落地）、`core/motion_ass.py`（`_homography_between` / `build_line_tracks` / `_split_runs`）、`scripts/motion_ass.py`（关键帧分批 OCR 与覆盖率告警已落地）
- 实测依据：`CHANGELOG.md`「已知限制」两条、`docs/testing.md`「未发布修复轮」表

## 1. 问题

轨迹 ROI 一旦丢锁就**永久**丢锁：跟踪器只在成功时推进参考状态，失败后没有任何重捕获分支。两次独立复现（均为「ok 窗口在文字出现之前」）：

| 素材 | ok 帧 | ok 窗口 | 后果 |
| --- | --- | --- | --- |
| 12.mp4（1080p，1871 帧） | 79/1871（4.2%） | t=1.42–4.67 s（画面空白） | 关键帧池无文字 → `RuntimeError` → 该 ROI 回退静态策略（mask→external，1 条 NoteBox） |
| 4K NCOP（3840×2160，801 帧） | 56/801（7.0%） | t=6.51–8.80 s | 同上（exit 2） |

同一区域走静态主流水线可识别出 1600 条事件（12.mp4，t>10 s 占 1579）与 4 条歌词（4K 0–33 s）——文字存在，瓶颈在跟踪。

失败链路（现状）：
`track_plane` 覆盖率低 → `select_keyframes` 池落在无文字窗口 → `_batch_has_text` 全批无文字 → `RuntimeError("no text lines recognized on any of N candidate keyframes")` → `core/pipeline_worker.py` 捕获告警 → 该 ROI 静态事件保留。

## 2. 目标 / 非目标

### 目标

1. 平面**短暂或部分丢失后能重新捕获**，令 ok 覆盖率与可出字幕的时间窗显著扩大（量化门槛见 §9）。
2. **绝不违反坐标不变量**：任何 `ok` 帧的 `homography` 必须把「初始锚帧平面坐标」映到该帧画面。
3. 丢锁**原因可观测**（lost 帧为何被拒、重捕获如何恢复），使参数调整由数据驱动。
4. 对「从不丢锁」的视频**输出逐字节不变**。

### 非目标

- 不承诺「内容彻底改变（切镜/平面离场）后仍必然跟住」——那需要 per-chain 坐标系（§4.5，条件触发）。
- 不改现有门限默认值语义、不改 `track_plane` / `track_plane_frames` 公开签名（只加可选参数）。
- 不引入随机 seek / 跳帧读取。
- 不改 OCR 侧内存行为（4K 剩余支配项另案）。

## 3. 现状机制与坐标约束（实现前必读）

### 3.1 主循环（`_track_plane_source` 内，`track_plane_frames` 与 `track_plane` 共用）

```
for idx in range(init_index + 1, total):
    gray = cvtColor(frame_at(idx))
    cur_pts, cur_desc = _detect_features(gray, feature_count)
    region = _bbox_expand(ref.quad, max(24.0, SEARCH_PAD_RATIO * ref_diag))
    src, dst = _match_points(ref.pts, ref.desc, cur_pts, cur_desc, region, lowe_ratio)
    if len(src) >= min_matches:
        h_mat, mask = findHomography(src, dst, RANSAC, ransac_thresh)
        inlier_ratio = ...; reproj = _reproj_rmse(...)
        candidate = perspectiveTransform(ref.quad, h_mat)
        ok = finite(candidate) and _quad_is_convex(candidate) and reproj <= max_reproj_error
             and area_ratio in [1-max_area_jump, 1+max_area_jump]
             and all(jumps <= max(JUMP_FLOOR_PX, max_jump_ratio * _mean_edge_length(ref.quad)))
    if ok:
        h_total = h_mat @ ref.h_total          # 锚帧平面 → 当前帧
        ref = _RefState(gray, cur_pts, cur_desc, candidate, h_total, frame_num)
        tracks.append(status="ok", quad=candidate, homography=h_total, ...)
    else:
        tracks.append(status="lost", ...)      # ref 保持不变（仅「等门限变好」，不会重捕获）
```

### 3.2 坐标约束（本设计的第一原则）

- `TrackedQuad.homography` 的语义是 **锚帧平面 → 该帧画面**（`core/motion_ass._homography_between(a,b) = H_b · inv(H_a)`，`build_line_tracks` 用同一 `ref` 把所有 ok 帧的行框映射出来）。
- 因此**所有 ok 帧必须同属初始锚帧平面**。任何「另起参考帧往后跟」的实现若不把单应对回锚帧平面，`build_line_tracks` 会静默产出错位字幕——**比断链更糟**，因为它是无声的。
- 推论：重捕获必须给出**直测锚帧**的单应，而不是「新参考帧的累加链」。这是 Phase 1 能在不改下游的前提下成立的全部原因（§4.1 的三级尝试都满足这一点）。

### 3.3 已可直接复用的事实（省掉一半工作量）

- **锚帧特征已经在手**：初始化探测把锚帧候选存进 `cand_feats[init_index] = (anchor_gray, anchor_pts, anchor_desc, anchor_near)`，随后解包为局部变量并用于构造 `ref`。重捕获所需的 `anchor_pts` / `anchor_desc` **无需新增缓存**，只要把局部变量在函数作用域内保留到主循环即可（探测器已按「只缓存会被读到的候选」收敛，锚帧必留）。
- 现成的几何工具：`_quad_is_convex` / `_quad_area` / `_mean_edge_length` / `_reproj_rmse` / `_bbox_expand` / `_match_points` / `_detect_features(gray, count, mask=None)`。
- 现成的诊断入口：`log` 回调、`[1/5]` 摘要行、`build_motion_events` 的 `summary` dict。

## 4. 设计

### 4.1 Phase 1 分级重捕获（必要，默认开启）

**触发**：`lost_streak`（连续 lost 计数，成功清零）≥ `reacquire_after_lost`（默认 12）后，每 `reacquire_interval`（默认 6）帧尝试一次，避免每帧都做放宽匹配。

**三级尝试**（命中即止；全程顺序解码、不 seek）：

| 级 | 参照 | 单应 | 命中后的 `h_total` |
| --- | --- | --- | --- |
| a | 锚帧 `anchor_pts/anchor_desc` | `h_r = findHomography(anchor_pts, cur_pts)` | `h_total = h_r`（**直测锚帧，不累加**） |
| b | 最后一个好帧 `ref` | `h_step = findHomography(ref.pts, cur_pts)` | `h_total = h_step @ ref.h_total` |

- 每级用**放宽参数**重新检测与匹配：`reacquire_feature_count`（4000）、`reacquire_lowe_ratio`（0.85）、`reacquire_min_matches`（8）、`reacquire_ransac_thresh`（5.0）、搜索区 `reacquire_search_pad_ratio`（1.5×diag；仍受帧边界裁剪）。
- **几何闸门**（与常规路径的差别只在「相对谁」）：
  - `candidate = perspectiveTransform(quad0, h_total)`（常规路径是相对 `ref.quad`；重捕获后可能已远离最后已知位置，故相对锚帧 quad 判定面积/凸性）；
  - 凸性、有限性、面积比用**更宽的** `reacquire_max_area_jump`（默认 1.0）；
  - 顶点跳变相对**最后已知位置**（`ref.quad`，若 `ref` 存在）用 `reacquire_max_jump_ratio`（默认 3.0）× 边长，并保留一个**相对量**绝对上限 `reacquire_max_jump_diag`（默认 1.0 × 帧对角线）——用帧对角线为单位而不是像素，天然与分辨率无关，也避免「1080p 下上限大于画幅、形同虚设」的问题；
  - 重投影 `reproj <= reacquire_max_reproj_error`（默认 6.0）。
- **命中后**：写 `status="ok"`、`quad=candidate`、`homography=h_total`、`homography_inv=inv(h_total)`，`reacquired=True`，`ref = _RefState(gray, cur_pts, cur_desc, candidate, h_total, frame_num)`，`lost_streak = 0`。
- **失败**：保持 `lost`，写 `lost_reason`（§4.2），并在尝试帧上 `log` 一行「第几级、matches/inlier/reproj 实测值」。

**为什么这样能修 12.mp4/4K**：新内容出现在**同一物理平面**上，锚帧含**内容无关的结构**（屏框/边框/机身轮廓）；放宽门限 + 提高特征数后锚帧结构特征仍能配上，而 `h_total` 直测锚帧使坐标天然正确。

**为什么默认开启是安全的**：重捕获只在连续 lost ≥ 12 帧后介入；11.mp4（651/651 ok）与 DMG（174/246 ok，但丢锁段是否触发见 §9 验收）不进入该路径时输出不变。

### 4.2 丢锁原因与重捕获标记（必要）

**数据结构**（`core/scene_plane_tracker.py`，`TrackedQuad` 末尾追加两个带默认值的字段）：

```python
lost_reason: Optional[str] = None   # 仅 lost 帧：few_matches / low_inlier / high_reproj /
                                    # area_jump / vertex_jump / degenerate_h / no_ref
reacquired: bool = False            # 仅重捕获恢复的 ok 帧
```

主循环各失败分支显式赋值（当前这些判据只在 `ok` 表达式中求值、不留痕）。`degenerate_h` 对应 `h[2,2]` 退化（已有守卫），`no_ref` 对应 `ref` 缺失/无特征。

**序列化兼容（关键，否则会误伤零回归验收）**：`save_trajectory` 用 `asdict` 展开，若直接加字段会让**每一帧**多出 `"lost_reason": null` / `"reacquired": false`，轨迹 JSON 字节全变。处置：`save_trajectory` 打包时**丢弃值为 `None`/`False` 的这两个新键**——于是：

- 全程无丢锁的视频（11.mp4）轨迹 JSON **逐字节不变**；
- 有丢锁帧的视频（DMG / 12.mp4 / 4K）丢锁条目多出 `lost_reason`，其余字段不变 ⇒ 验收按「**忽略新增可选键后的字段级相等**」判定（§6）；
- `load_trajectory` 无需改动（旧文件缺键 ⇒ 消费方 `.get()`）。

**摘要**：`build_motion_events` 的 `summary` 增加 `lost_reasons`（各原因计数）、`reacquire_count`、`chains`（链数）与各链 ok 窗口；`[1/5]` 行打印同样内容；`core/pipeline_worker.py` 的告警据此给出「该 ROI 仅 X% 可用」的明确提示（替代现在只在 <50% 时的一行文本）。

### 4.3 阈值分辨率口径（必要）

跟踪器阈值仍是绝对量：`max_reproj_error=4.0`、`JUMP_FLOOR_PX=24`、`min_matches=12`、`lowe_ratio=0.75`、`SEARCH_PAD_RATIO=0.35`；而 `collapse_settle_tol_px`、`verify_*` 已按 `video_height/1080` 归一 ⇒ 同一套代码在 4K 与 1080p 上语义不同。

处置：对**像素量**（`max_reproj_error`、`JUMP_FLOOR_PX`、搜索外扩的绝对下限 `max(24.0, …)`、`reacquire_ransac_thresh`、`reacquire_max_reproj_error`）按 `video_height/1080` 缩放；`reacquire_max_jump_diag` 已是以帧对角线为单位的相对量，不参与归一；**与特征/匹配相关的量不缩放**（`min_matches`、`lowe_ratio`、`min_inlier_ratio`、`feature_count` 是结构性量，与分辨率无关）。

- `track_plane_frames` 增加可选 `video_height: Optional[float] = None`；缺省或 ≤0 或 1080 ⇒ 系数 1.0 ⇒ 行为与输出**逐字节不变**。
- `track_plane` 自行读取分辨率并传入（与 `verify_line_tracks` 同一口径）；`MotionAssConfig` 增加 `track_reacquire_*` 镜像字段供 `--config-json` 调。
- 若 Task 1 实测显示归一反而有害（例如 4K 下放宽后的重投影门限导致误配上升），则**改为在文档中写明不归一的实测理由**，不硬做。

### 4.4 Phase 2（条件触发，本次不实现）

触发条件（量化）：Phase 1 落地后 12.mp4 ok 占比仍 < 30%，且 Task 1 数据显示失败帧与锚帧的共同特征数中位数 < `reacquire_min_matches`（说明平面真的离场或内容完全不重叠）。

设计轮廓：`TrackedQuad` 增加 `plane_id`；无法对回初始平面时另起平面（自洽 `h_total` + 自己的参考帧）；下游按链分别做关键帧选择/OCR 融合/行轨迹（`build_line_tracks` 的 `ref` 改为按链取，`pose_verify` 与 `synthesize_events` 的输入按链切分）。影响面比 Phase 1 大一个量级，故以实测为前提。

## 5. 接口与配置变更一览

| 位置 | 变更 | 兼容性 |
| --- | --- | --- |
| `TrackedQuad` | `+lost_reason: Optional[str] = None`、`+reacquired: bool = False` | 末尾追加带默认值 ⇒ 关键字构造与既有位置构造均不受影响 |
| `save_trajectory` | 打包时跳过 `None`/`False` 的新键 | 无丢锁视频 JSON 逐字节不变 |
| `track_plane_frames` | `+video_height=None`、`+reacquire_*`（见 §7） | 全部可选，缺省时输出不变 |
| `track_plane` | 读取分辨率并传入 | 同上 |
| `MotionAssConfig` | `+track_reacquire_*` 镜像字段 | 只增字段，`--config-json` 可调 |
| `build_motion_events` summary | `+lost_reasons / reacquire_count / chains` | 只增键 |
| `_merge_chains_with_hold` | 重捕获缺口**强制不桥接** | `lost_hold_sec=0` 默认行为不变；>0 时行为变化需在 CHANGELOG 说明 |

## 6. 兼容性与零回归口径

1. **ASS 产物逐字节**：11.mp4 与 DMG 的 `.ass` 必须逐字节相同（这是硬门槛）。
2. **轨迹 JSON**：11.mp4 **逐字节相同**；DMG / 12.mp4 / 4K 允许新增可选键，按「忽略 `lost_reason`/`reacquired` 后的字段级相等」判定（提供一个比较助手，如 `test_run/cmp_traj.py`，避免手写 ad-hoc 判据）。
3. **默认路径等价**：连续 lost 未达阈值时不进入重捕获 ⇒ 与旧实现逐字节一致（单元测试固化）。
4. **可视化差异**必须留证据：输入配置、轨迹 JSON、ASS、渲染帧、日志（`docs/superpowers/evidence/2026-09-20-plane-reacquisition/`）。

## 7. 参数表

| 参数（`track_plane_frames`） | 默认 | 含义 | 依据 / 备注 |
| --- | --- | --- | --- |
| `reacquire_after_lost` | 12 | 连续 lost 多少帧后开始尝试 | 约 0.5 s@24fps：短于此多为噪声/瞬时遮挡，不值得放宽 |
| `reacquire_interval` | 6 | 之后每隔多少帧尝试一次 | 控制常态开销；1 = 每帧都试（最贵） |
| `reacquire_feature_count` | 4000 | 重捕获时的特征数上限 | 需覆盖「平面仍在但内容全变」；常态 2000 不足 |
| `reacquire_lowe_ratio` | 0.85 | 放宽的 Lowe 比值 | 与 `min_matches` 降低配合；过松会引入错误配对（由几何闸门兜） |
| `reacquire_min_matches` | 8 | 放宽的最少匹配对 | 低于 8 时单应估计不稳 |
| `reacquire_ransac_thresh` | 5.0 | 放宽的 RANSAC 阈值（px@1080p） | 按分辨率归一 |
| `reacquire_search_pad_ratio` | 1.5 | 搜索区外扩（×diag） | 常规 0.35 限制在旧位置附近，重捕获要允许大位移 |
| `reacquire_max_reproj_error` | 6.0 | 放宽的重投影上限（px@1080p） | 按分辨率归一 |
| `reacquire_max_area_jump` | 1.0 | 放宽的面积比突变 | 相对锚帧 quad 判定 |
| `reacquire_max_jump_ratio` | 3.0 | 顶点跳变上限（×平均边长） | 相对最后已知位置 |
| `reacquire_max_jump_diag` | 1.0 | 顶点位移绝对上限（×帧对角线） | 相对量、无需归一；防离谱误配 |

> **所有默认值以 Task 1 实测为准**：Task 1 产出「各 lost 原因占比 + 失败帧与锚帧的共同特征数分布」后，把修订写进实施计划的「实测修订」小节，再据此定值。上表是待验证的初始猜测，不是结论。

## 8. 测试设计

**快测（合成视频，pytest）**

| 用例 | 断言 |
| --- | --- |
| 空白引导 → 内容大变 → 重捕获 | ok 覆盖到内容段；`reacquired=True`；用合成单应真值校验映射误差 ≤ 1 px |
| `h_total` 直测锚帧 | 重捕获帧的 `homography` 与「锚帧→当前帧」真值一致（不是累加链） |
| 未达阈值不触发 | `lost_streak < reacquire_after_lost` 时输出与旧实现逐字节一致 |
| 全程遮挡 | 保持 lost，不误报 ok |
| 平面离场 + 相似干扰物 | **不得**重捕获（允许一直 lost） |
| 各 `lost_reason` 分支 | 各门限失败路径产出正确原因；ok 帧为 `None` |
| 序列化兼容 | 无丢锁视频 JSON 逐字节不变；有丢锁视频仅新增可选键 |
| 阈值归一 | `video_height=2160` 缩放；缺省/1080 逐字节不变 |
| 无 seek | 断言未调用 `cap.set`/`cap.grab`（沿用既有手法） |

**慢测（环境变量门控，如 `MOTION_SLOW_TESTS=1`）**

- 12.mp4（0–1870）与 4K（0–800）：断言 ok 覆盖率与 ok 窗口落入期望区间（把 Task 4 的实测值固化为回归指标，避免静默退化）。

**真实素材验收（人工 + 脚本）**

- 覆盖率/链数/重捕获次数/事件数；`test/burn_frames.sh` 在重捕获点前后各烧 3 帧目检（重点：重捕获后是否跳位错误、是否出现幽灵文字）。

**常驻不变量校验（自动化，防静默错位）**

- 任取两个 `ok` 帧，用 `_homography_between` 往返映射同一行框，误差 ≤ 2 px（1080p 素材）；违反即失败。这是对 §3.2 的直接守护。
- 静止 `\pos` 锚点不变式（沿用 `test_run/check_collapse.py` 同口径：关闭塌缩的逐段 `\move` 作真值）。

## 9. 验收标准

| 项目 | 门槛 |
| --- | --- |
| 12.mp4 轨迹 ROI | ok 占比 4.2% → **≥60%**；t>10 s 窗口内产出事件（不再是单条 NoteBox 回退） |
| 4K NCOP 0–800 | ok 占比 7.0% → **≥60%**；产出歌词轨迹事件 |
| 零回归 | 11.mp4 / DMG `.ass` **逐字节相同**；`pytest tests/ -q` 全绿；`ruff check . --select F` 全绿 |
| 坐标不变量 | §8 的往返映射校验通过 |
| 内存 | 4K 轨迹管线峰值 ≤ **2.2 GB**（重捕获不得引入整段帧驻留） |
| 误捕获 | 「平面离场 + 相似干扰物」用例不得重捕获 |

**不达标时的动作**：回到 Task 1 补测（例如「平面真的离场」的证据），在实施计划里记录是否需要 Phase 2；**不得**靠继续放宽门限硬凑指标。

## 10. 实施顺序与证据（对应计划 Task 0–5）

| 步骤 | 产物 | 命令 / 检查点 | 回滚 |
| --- | --- | --- | --- |
| Task 0 基线 | `docs/superpowers/evidence/2026-09-20-plane-reacquisition-baseline/`：pytest 输出、`git status`、三份轨迹 JSON 与两份 `.ass` 的 SHA-256 | `.venv/bin/python -m pytest tests/ -q`（基线 974 passed, 1 skipped） | 无需回滚（只加证据） |
| Task 1 实测分类 | 各 lost 原因占比、共同特征数分布、匹配对分布 | 12.mp4（0–1870）与 4K（0–800）各跑一次轨迹管线，读日志摘要 | 只加 `lost_reason` 记录，可独立回退 |
| Task 2 分级重捕获 | 参数定值（写入计划「实测修订」）、合成用例、摘要字段 | `pytest tests/test_scene_plane_tracker.py tests/test_motion_ass_cli.py -q` | 关闭开关（`reacquire_after_lost` 设为极大）即回旧行为 |
| Task 3 阈值口径 | 归一实现 + 文档 | 复跑 11.mp4/DMG 轨迹 JSON 与 `.ass` 对比 | 系数置 1.0 即回旧行为 |
| Task 4 真实素材验收 | `evidence/2026-09-20-plane-reacquisition/`：轨迹 JSON、ASS、渲染帧、覆盖率表 | 按 §9 门槛比对；不达标回 Task 1 | 保留产物，回退实现提交 |
| Task 5 交互与文档 | `lost_hold_sec` 不桥接 + schema 说明 | 全量 `pytest` + `ruff check . --select F` | 逐项回退 |

## 11. 风险与开放问题

| 风险 | 缓解 |
| --- | --- |
| 放宽门限导致**误捕获**（相似纹理被当成平面） | 几何闸门（凸性/面积比/跳变）+「相对最后已知位置」约束 + 干扰物合成用例 + 真实素材目检 |
| 重捕获后**坐标错位**（静默、最危险） | 强制 `h_total` 直测锚帧；§8 的往返映射校验作为常驻测试 |
| 参数凭直觉调，越调越糟 | Task 1 先实测分类；不达标回实测而非继续放宽；参数表标注「待验证」 |
| 12.mp4/4K 的根因其实是「平面离场」 | Task 1 的共同特征数分布给出判据；确认离场则 Phase 1 本就不该成功，转 Phase 2 评估 |
| 默认开启带来意外行为变化 | 只有连续 lost ≥ 12 帧才介入；11.mp4/DMG 的 `.ass` 逐字节作为硬门槛 |

**开放问题**

1. 4K 与 1080p 的「共同特征数」量级差异是否会让同一组 `reacquire_*` 门限在两端表现不同？（Task 1 需分分辨率实测）
2. 重捕获成功但接上的平面其实是**另一块**相似纹理（例如同款手机的两个画面）——现有几何闸门能否拦住？需要 Task 2 的干扰物用例给出结论，必要时引入「内容相似度下界」判据。
3. 是否需要在 GUI 暴露「该 ROI 可跟踪覆盖率」以便用户在识别前就知道会回退？（属交互决策，本设计只保证摘要里有数据）
