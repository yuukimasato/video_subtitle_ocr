# 场景文字显示策略加固 最终交付报告

- 日期：2026-09-17
- Spec：`docs/superpowers/specs/2026-09-17-scene-text-policy-audit-and-hardening.md`
- Plan：`docs/superpowers/plans/2026-09-17-scene-text-policy-hardening.md`（Task 0–7 全部完成）
- 执行方式：每项任务按「失败测试 → 实现 → 定向测试 → 小步 commit」由独立子代理串行落实

## 1. 提交清单（自基线起点 5de594a 起）

| Commit | 类型 | 内容 |
| --- | --- | --- |
| `a323017` | test | Task 0 基线：git 状态、HEAD、pytest 全量输出 + JUnit XML、138 个源文件 SHA-256 清单、manifest.json |
| `dbc27be` | fix | Task 1 策略 API 上下文契约：显式 `ref_frame`、`PolicyResult`（requested/applied/notes/diagnostics）、`_validate_rows` 行框校验、空轨迹降级 |
| `ed63a2c` | fix | Task 2 事件归属与样式：`line_idx` 归属优先/body 兼容回退、自定义 style 全路径传递、多行遮罩按行 union、平滑 `max_gap` 限制 |
| `5ca3fdd` | feat | Task 3 背景分析加固：`BackgroundStats`/robust 模式（MAD/IQR+置信度）、whitespace 百分位/Otsu 阈值、候选带 confidence、诊断字段 |
| `5864b6c` | fix | Task 4 静态路径：逐 ROI 策略上下文、`FrameReader` 缓存（含 opens/seeks 计数）、CLI 覆盖优先级 helper、未知策略报错、median 分析图可选模式 |
| `1a44dac` | fix | Task 5 透视安全边界：矩形近似误差度量与 `mask_max_perspective_error`（默认 0.1）降级、退化 quad 判定、实验开关 `mask_polygon_clip`（`\p1` 四角多边形） |
| `d3b34ad` | test | Task 6 端到端验收：DMG 四策略 ASS + 渲染抽帧 + 诊断捕获 + 单/多 ROI 读取对比 + `docs/testing.md` 可复现命令 |
| 本提交 | docs | Task 7 最终回归记录、CHANGELOG、本报告 |

## 2. 测试对比（Task 0 → Task 7）

| 指标 | 基线（a323017） | 最终 | 变化 |
| --- | --- | --- | --- |
| passed | 666 | **739** | +73（全部为先红后绿新增） |
| failed | 0 | 0 | — |
| skipped | 1 | 1 | 不变 |
| warnings | 11 | 11 | 不变 |

- 命令：`.venv/bin/pytest -q`（Python 3.12.3 / pytest 8.4.2）；最终运行 58.79s。
- ruff（默认配置，项目无 ruff 配置文件）：基线与最终均为 **158 个既有告警，零新增**（存量含 tests 中 1 处 E741，系基线已有）。
- 新测试分布：test_scene_text_policy.py 62→121（+59）、test_motion_ass.py（+平滑/事件归属）、新增 test_pipeline_scene_text_policy.py（9 项）等。

## 3. 默认 overlap 不变的验证（规格验收 §5.2）

- **动态路径**（Task 6）：`motion_ass.py` 不带策略参数重跑输出与 `overlap.ass` **MD5 相同**（`d2ee6c9f…`）。
- **静态路径**（Task 7）：基线 worktree（a323017 代码）与当前代码各跑一次完整 CLI 流水线（`cli.py --engine rapid --roi-file <DMG roi json>`），输出 ASS **逐字节一致**（263 行，`diff` 为空），stderr 一致。
- Task 2 期间另做了 8 场景 overlap 输出的改动前后字节级 dump 对比（含空轨迹/显式 ref_frame/非法框/静态），全部一致。

## 4. 视觉验收结论（详见本目录 acceptance.md）

测试素材 `test/[DMG] 冴えない彼女の育てかた♭ 第07話19.mp4`（1920×1080，246 帧），同一 quad、同一 rapid OCR，渲染帧 0/36/79/176/192：

| 模式 | 结果 | 证据 |
| --- | --- | --- |
| overlap | ✅ 与基线逐字节一致 | `overlap.ass`、`crop-overlap-f*.png` |
| mask | ✅ 无重影（遮罩内 std=0.00，外环无字形碎片，≤0.2px 等效框缘残留） | `mask.ass`、`crop-mask-f*.png`、`mask-patchonly.ass` |
| external | ✅ NoteBox 位置 5 帧完全稳定（上沿 row=519 恒定） | `external.ass`、`crop-external-f*.png` |
| whitespace | ✅ 按设计回退 mask（13 行 ≈420px 高无合格空白带），输出与 mask 逐字节一致 | `whitespace-*.json/png`、回退 note 已留痕 |
| 透视/杂色降级 | ✅ 合成梯形 error 0.300>0.1 → external；噪声平面 std 74>18 → external | `acceptance.md` §透视/杂色 |

诊断捕获：`<mode>-policy-diagnostics.json`（ref_frame、行有效性、逐块 std/MAD/IQR/样本数/置信度、`mask_perspective`）。

## 5. 单/多 ROI 读取对比（`roi-read-comparison.json`）

| 配置 | opens | seeks | 耗时 |
| --- | --- | --- | --- |
| 1 ROI | 1 | 7 | 0.507s |
| 2 ROI（同区域） | **1** | **7** | 0.508s |
| 2 ROI（不同子区域） | 1 | 7 | 0.497s |
| 旧行为参照（每 ROI 独立 reader） | 2 | 14 | 0.976s |

多 ROI 读取次数不随 ROI 数线性增长，roi_0 分析图逐字节一致。

## 6. 已知边界（记录，不在本轮修）

1. whitespace 对文本量大、版面满的场景按设计回退 mask（DMG 即此情形）；适合"少文本 + 多留白"平面。
2. mask 未开 `--auto-brightness` 时，暗屏段补丁保持锚定帧亮色（忠实锚定语义，属设计内）。
3. 静态路径全时段 mask 遇移动文字由移动门限（24px）守界自动降级 external——移动场景应使用轨迹管线。
4. `apply_policy_static` 无逐帧轨迹，不做透视误差检查（其遮罩在平面坐标即矩形）；透视守卫仅动态轨迹路径。
5. `mask_polygon_clip`（`\p1` 多边形）为实验开关，默认关闭；lost 帧区间无四角数据、不生成遮罩。
6. median 分析帧模式仅在生成器构造参数暴露，未接入 GUI/CLI。

## 7. 未纳入项

- 规格建议架构中的完整 `PolicyContext` 结构体重构：以现有函数签名 + 可选参数 + `PolicyResult` 诊断实现等价能力，保留兼容包装（Task 1 决策）。
- 逐帧四角多边形遮罩的默认启用与 libass 兼容性矩阵验证（实验开关先行）。
- 静态路径时间邻域多帧采样的 GUI/CLI 布线。
