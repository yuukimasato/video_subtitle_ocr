# 场景文字显示策略端到端 ASS 与视觉验收（Task 6）

- 日期：2026-09-17
- Spec：`docs/superpowers/specs/2026-09-17-scene-text-policy-audit-and-hardening.md`（验收标准 §5.4/§5.5）
- Plan：`docs/superpowers/plans/2026-09-17-scene-text-policy-hardening.md`（Task 6）
- 前序：Task 0–5 已完成（`a323017`→`dbc27be`→`ed63a2c`→`5ca3fdd`→`5864b6c`→`1a44dac`），Task 0 基线 739 passed, 1 skipped, 11 warnings
- 素材：`test/[DMG] 冴えない彼女の育てかた♭ 第07話19.mp4`（1920×1080 @24000/1001，246 帧，10.26s；帧 0–84 与 157–245 为两段静止链，85–156 遮挡/lost）
- 轨迹配置：`test/motion-quad.json`（第 0 帧手机屏幕四角 [592,283]–[1324,1079]，**轴对齐矩形**）；OCR 引擎统一 `rapid`，关键帧 [11, 3, 81] 四次运行完全一致

## 1. 四份 ASS（同一 quad、同一 OCR 引擎、仅策略不同）

生成命令（工作目录 = 仓库根，`V` 为视频路径、`E` 为本目录）：

```bash
.venv/bin/python scripts/motion_ass.py --video "$V" \
  --quad-file /home/hope/Tools/video_subtitle_ocr/test/motion-quad.json \
  --start-frame 0 --end-frame 245 --ocr-engine rapid \
  --scene-text-policy <mode> --out "$E/<mode>.ass"   # stderr → $E/run-stderr-<mode>.txt
```

| 模式 | 事件数 | applied | 回退 note（stderr 原文） |
| --- | --- | --- | --- |
| overlap | 26（13 行 × 2 链） | overlap | 无（不进 apply_policy，直接返回原事件） |
| mask | 40 = 14 条 layer-0 `\p1` 遮罩（7 块 × 2 链）+ 26 条 layer-1 文本 | mask | 无 |
| external | 1（NoteBox 单事件，13 行 `\N` 合并，`\an2\pos(960,1040)`，0→10.21s 全程） | external | 无 |
| whitespace | 40（与 mask.ass **逐字节一致**，`diff` 为空） | **mask** | `whitespace->mask: no whitespace band (need >= 2 x line_h 30.1px, width >= 0.6 x plane width)` |

**whitespace 实际行为**：合并 13 行文本块高 ≈420px，平面下半空白带不满足「高度 ≥2×行高且宽度 ≥0.6×平面宽」的候选条件 → 按设计回退 mask，告警与 `scene-text-policy: whitespace -> mask` 均落 stderr；输出与 mask 逐字节一致（上次 2026-09-16 验收同样结论，属该视频的预期行为）。

### 逐模式 diagnostics（`<mode>-policy-diagnostics.json`）

捕获方式：monkeypatch 包装 `core.scene_text_policy.apply_policy`（记录 `PolicyResult` 全量字段后委托原函数），进程内重跑 4 次真实 `run_pipeline`；与 CLI 产物的事件数/关键帧逐一吻合。

| 项 | mask / whitespace（回退后） | external | overlap |
| --- | --- | --- | --- |
| requested → applied | mask→mask；whitespace→mask | external→external | 不调用（0 次 apply_policy） |
| ref_frame | 0（source=inferred，tracks[0]） | 0（inferred） | — |
| 行框校验 | total 13 / valid 13 / invalid 0 / clipped 0，invalid_reasons=[] | 同左 | — |
| background_blocks | 逐块 std / MAD / IQR / n_samples / confidence（如块 0：std 14.42，MAD 16.31，IQR 17.05，n=6722，conf 0.199，uniform） | []（external 不取色） | — |
| mask_perspective | []（轴对齐 quad，误差 ≈0，不触发降级） | [] | — |

## 2. libass 烧录抽帧（相同帧号 0 / 36 / 79 / 176 / 192）

命令全文见 `commands.txt`（27 条，含 5 条无字幕原帧、2 条 patch-only 参照），ffmpeg stderr 收于 `render-stderr.txt`（**0 字节 = 全部无告警**）。单帧模板（`select=eq(n,F)` 按解码帧号精确抽样，避免 `-ss` 时间戳重位；路径用单引号包裹，做法同 `scripts/verify_motion_ass.py`）：

```bash
ffmpeg -nostdin -hide_banner -loglevel error -y -i "$V" \
  -vf "ass='$E/<mode>.ass',select='eq(n,36)'" -vsync 0 -frames:v 1 "$E/<mode>-f36.png"
```

产物：`<mode>-f{0,36,79,176,192}.png`（overlap/mask/external/original 各 5 张；whitespace-f36.png 保留作逐字节一致性凭证，其余 4 张与 mask-f*.png MD5 相同已去重，MD5 记录于 `pixel-checks.json`）；局部放大 `crop-{original,overlap,mask,external}-f{36,176}.png`（手机屏幕区 560,260–1340,1010）。

## 3. 像素 / 人工检查（`pixel-checks.json`）

### 3.1 mask：原字不透出（无重影）✅

方法：mask.ass 去掉 layer-1 文本行得到 `mask-patchonly.ass`（14 条纯遮罩事件），同帧号烧录得 `patchonly-f{36,176}.png` 参照；按事件 `\move`+`\p1` 尺寸解析每帧的 7 个遮罩矩形（f36 随轨迹上移 ~123px，f176 与初始位姿重合，帧 176 矩形与 diagnostics 块框差 <1px）。

- **遮罩真实渲染成不透明色块**：7/7 矩形 patch-only 内部灰度 std = 0.00（两帧均然）——遮挡层确实存在且均匀；
- **原字几何上不可能透出**：遮罩矩形 = OCR 行块并框（原字 ⊆ 块框），块内全部被不透明层覆盖；3px 外环逐边检测：除体块贴满屏宽的左右两侧（外环即手机边框，暗占比 1.0，属预期）外，其余各边暗占比最大 **5.75%（f36）/ 6.48%（f176）**，折合 ≤0.2px 等效覆盖 —— 紧贴 OCR 框边缘的原字抗锯齿亚像素残留，100% 缩放不可见，无任何字形碎片；
- **目检**：`crop-mask-f36.png`（链 1 中段，遮罩随屏幕上移，白块与亮屏无缝、替换文本清晰单像）；`crop-mask-f176.png`（暗屏段，7 块补丁边沿干净、无原字透出、无重影）；
- **已知边界（设计内）**：未开 `--auto-brightness` 时遮罩色取自锚定帧（亮屏 F5F8F6），暗屏段（f176 原图中位亮度 173）补丁偏亮——亮度自适应属轨迹管线可选功能，非本次策略边界。

### 3.2 external：展示框位置稳定 ✅

NoteBox 为 BorderStyle=3 逐行不透明黑底（libass 行为）。以「烧录帧显著比原帧暗（g<50 且 o−g>18）」定位盒子：**5 帧上沿 row=519、列范围 641–1278 完全一致**（下沿 965–1000 的差异是 f36 盒子压在深色边框上所致的检测极限，非渲染差异）；ASS 层面单条静态 `\pos(960,1040)` 事件 0→10.21s，位置恒定由构造保证。目检 `crop-external-f36.png`/`crop-external-f176.png`：14 行布局、逐行黑底白字、亮度标签正常。

### 3.3 whitespace：空白带判定与回退 ✅（回退为设计预期）

该视频文本量偏大（13 行 ≈420px 高），无合格空白带 → 按回退链降级 mask（原因与告警见 §1）；渲染帧与 mask **逐字节一致**（5 帧 PNG MD5 相同）。whitespace 适用场景（文本少 + 留白多）由单测覆盖，此处记录真实视频的边界行为。

### 3.4 overlap：与基线一致 ✅

不带 `--scene-text-policy`（默认 overlap）重跑同参数 → 输出与 `overlap.ass` **逐字节一致**（MD5 `d2ee6c9fd02a78ddbdf9a3823081b530`，26 事件，关键帧 [11,3,81] 相同）；mask.ass 的 26 条文本事件与 overlap.ass 逐事件同 `\move/\fs` 标签（仅新增 layer 与遮罩事件），默认行为零改变。

### 3.5 透视 / 杂色按预期降级 ✅（合成样例；DMG 无此场景）

DMG quad 为轴对齐矩形，不触发透视/杂色降级，故按计划用合成样例直调 `apply_policy`（复用 `tests/test_scene_text_policy.py` 的合成轨迹/平面构造，与单测同口径）：

| 样例 | 输入 | applied | note / diagnostics |
| --- | --- | --- | --- |
| `synthetic-perspective-trapezoid.{json,ass}` | 梯形轨迹 px=0.002（矩形近似误差 ≈0.30） | **external** | `mask->external: block 0 perspective rectangle approximation error 0.300 > mask_max_perspective_error 0.1 (max corner offset / quad diagonal over 10 frames)`；diagnostics `mask_perspective[0].max_error=0.300`；输出无 `\p1` 虚假完美遮罩 |
| `synthetic-noisy-background.{json,ass}` | 彩色噪声平面 + 平移轨迹 | **external** | `mask->external: block 0 background channel std 74.0 > bg_max_std 18` |
| `synthetic-axis-aligned-control.{json,ass}` | 均匀白底 + 平移轨迹（对照） | **mask** | 无 note，正常生成遮罩——证明降级不误伤轴对齐场景 |

## 4. 单 ROI vs 多 ROI 读取对比（静态路径 FrameReader 缓存，`roi-read-comparison.json`）

方法：生成器层实测（免全量 OCR）——以真实视频 + `OCRToASSOptimizer._prepare_scene_policy_context`（→ `_grab_frame_crop` → 共享 `FrameReader`）跑 7 个候选帧探测，FC 内建 opens（VideoCapture 打开数）/seeks（解码帧数）计数；每 ROI 独立 reader 的旧行为作为参照同步实测。手机屏幕 rect 取 GUI 保存的 ROI 外接框 (604,282,1317,1078)。

| 场景 | ROI 数 | opens | seeks | 耗时 |
| --- | --- | --- | --- | --- |
| 单 ROI | 1 | **1** | **7** | 0.507s |
| 双 ROI（同区域复制，均 mask） | 2 | **1** | **7** | 0.508s |
| 双 ROI（不同子区域） | 2 | 1 | 7 | 0.497s |
| 参照：每 ROI 独立 reader（旧行为） | 2 | 2 | 14 | 0.976s |

结论：**读取次数不随 ROI 数线性翻倍**——双 ROI 与单 ROI 的 opens/seeks 完全相同（1/7，第二 ROI 全部帧缓存命中），且 roi_0 分析图与单 ROI 运行**逐字节一致**（`np.array_equal` = true），策略输入不因缓存改变；旧逐 ROI 路径则翻倍至 2/14。不同子区域场景同样 1/7（按帧号缓存与裁剪无关，roi_0 裁剪不同故图不同，属场景定义）。

## 5. 证据文件清单

- ASS：`overlap.ass`(26) / `mask.ass`(40) / `external.ass`(1) / `whitespace.ass`(40，≡mask) / `mask-patchonly.ass`(14，检查用参照) / `synthetic-*.ass`(3)
- 运行日志：`run-stderr-{overlap,mask,external,whitespace}.txt`、`<mode>-policy-diagnostics.json` × 4
- 渲染：`commands.txt`(27 条 ffmpeg)、`render-stderr.txt`(空=无告警)、`<mode>-f*.png`、`patchonly-f{36,176}.png`、`crop-*-f{36,176}.png`
- 检查：`pixel-checks.json`（遮罩环检测/盒子定位/MD5）、`roi-read-comparison.json`、`synthetic-*.json` × 3
- 本记录：`acceptance.md`

## 6. 结论

四种策略在真实视频 + libass 烧录下的行为全部符合规格 §5：mask 无重影、external 位置稳定、whitespace 按设计回退且留痕、overlap 与默认输出逐字节一致；透视/杂色降级与多 ROI 读取缓存均按预期工作。Task 6 验收通过。
