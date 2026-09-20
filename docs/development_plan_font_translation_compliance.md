# 字体智能与翻译扩展开发计划（font_intel / translation / compliance）

> 文档版本：v1.0（2026-09-21）
> 方案文档：[feature_plan_font_translation_compliance.md](./feature_plan_font_translation_compliance.md) v1.1（设计与选型依据，本文档只做排期与验收）
> 前置版本：v2.7.3（`docs/development_plan.md` Phase 1–4 已完成）
> 人力假设：1 名开发人员全职；总周期约 5.5 周（28 个工作日，含缓冲）
> 硬约束（全局，贯穿每个任务）：所有服务端调用（LLM 结构化、翻译、VLM、模型权重下载）必须复用 `core/llm_client.py` 的 429 有界退避封装——最大重试 10 次、单次等待 ≤60s、总等待 ≤300s、尊重 Retry-After（钳到同预算）、耗尽后以 `LlmApiError` 优雅降级，不得绕过、不得在降级路径中断任务链。

---

## 1. 目标（可验收）

| # | 目标 | 验收标准 |
|---|---|---|
| G1 | 中日字体参考映射库与合规决策可用 | Seekladoom 映射表 + 免费商用清单入库（含溯源与置信度）；合规规则（类别×场景→动作）经 GUI/CLI/JSON 三处配置一致生效；随 ASS 输出 `*_compliance_report.md/.json` |
| G2 | AI 翻译作为新流水线阶段可用 | 云端 API / 本地 Sakura / VLM 三级提供方按序降级；429 模拟下总等待有界、耗尽后保留原文继续出片；源语言按 ROI `ocr_lang` 路由；术语表与行长约束生效 |
| G3 | 字体识别端到端闭环可用 | 视频字块 → Top-N 候选（YuzuMarker + 字形重排）→ 合规决策 → 字体名落 ASS（Style 行 / `\fn`）/ 相似替换 → 缺口生成更新建议包 → 用户逐条确认入覆盖层 |
| G4 | 零回归 | 全部新功能关闭时，3 个测试视频的 ASS 输出与 2.7.3 基线 **逐字节一致**；默认配置（未导入映射表、未下载模型）下行为与现状相同 |
| G5 | 工程治理 | 新增单元测试全绿；429 降级路径有自动化测试；`vso-font` 独立入口进打包清单；i18n 四语言无缺译 |

## 2. 非目标（本轮不做）

- 不实现字体文件嵌入（`[Fonts]` UUEncode）——闸门规则本期定义，能力延后
- 不做 DeepFont 系英文/拉丁字体识别补充（Backlog）
- 不做字体字形自动变形/艺术化修改（只输出 OFL 衍生指导说明）
- 不做联网上报/拉取字体数据（缺口只进本地更新建议包）
- 不改动现有 OCR 主链路（阶段 1–3）业务逻辑

## 3. 架构红线（评审 checklist，违反即打回）

1. **阶段归属**：阶段 1–3 运行于 chunk worker 子进程，禁止服务端调用。翻译与 LLM 缺口分析只能挂主进程阶段 4（与 `subtitle_llm_polish` 同侧）；`font_identify` 是本地计算，作为主进程后置阶段（阶段 3 之后、ASS 生成之前）。必须有单测断言。
2. **字块图像不跨阶段携带**：阶段 4 只有文本与框坐标；字块裁剪用 `roi_extractor.extract_single_roi_crop_with_time` 随机访问取帧（边界精修同款模式），按字幕组采样、按 (roi, 稳定文本段) 缓存。
3. **字体名落点**：默认落 `styling.py` `_get_ass_header()` 的 Style 行；混排多字体走新增逐事件 `\fn` 通道；用户模板（`template_path`）只报告不重写。
4. **种子数据只含元数据**：fonts.db 不分发任何字体文件本体；本机已安装字体仅索引路径；rar 样张不进打包链。
5. **429 有界退避**：见文档头硬约束；翻译降级 = 保留原文，ETL 降级 = 记录待人工，模型下载降级 = 可恢复错误提示。
6. **CLI 兼容**：现有 `cli.py`（单命令 + 位置参数 `video`）保持不动；`vso-font` 走独立入口 `font_cli.py` + `/usr/bin/vso-font` 启动器。
7. **合规硬红线写死在代码**：不提供、不链接、不缓存破解版/非官方渠道字体文件；`commercial_paid` 只输出官方授权/购买链接；`unknown` 默认从严。

---

## 4. 任务分解（WBS）

### Phase 0：基线与准备（2 天）

| 编号 | 任务 | 涉及文件 | 验收标准 |
|---|---|---|---|
| T0.1 | 分支 + 回归基线：现有测试套件全绿确认；用 `benchmarks/make_test_video.py` 生成中/日测试视频，录制"全部新功能关闭"的 ASS 输出与 `ocr_calls` 基线 | `benchmarks/` | 基线存档可复现；pytest 全绿（当前 69 个测试文件） |
| T0.2 | 外部数据源准备：free-font / chinese-fonts 清单拉取与许可快照存档；Seekladoom rar 本机解包（unrar/7z，不进打包链）+ 三大厂商表/勘误 txt 内容盘点；YuzuMarker 权重、许可、HF 仓核对 | 仓库外数据目录 | 每个数据源有许可说明 + 内容清单；rar 可重复解包脚本化 |
| T0.3 | torch + paddlepaddle 共存 spike：同 venv 安装后 `import` 双框架，记录内存峰值与兼容性；得出依赖形态决策（同 venv 可选依赖 vs `vso-font` 独立 venv 子进程） | `requirements-fontintel.txt`（草稿） | 决策记录写入本文档附录；P3 任务按决策执行 |
| T0.4 | fonts.db schema v1 设计：`fonts / aliases / jp_cn_font_map / license_rules / user_overrides / schema_version` 表 + 迁移框架 | `font_intel/fonts_db.py`（骨架） | schema 评审通过；种子层/覆盖层双层查询接口定型 |

### Phase 1：数据库 + ETL + 合规引擎（10 天）→ 里程碑 M1

| 编号 | 任务 | 涉及文件 | 验收标准 |
|---|---|---|---|
| T1.1 | `fonts_db.py` 实现：SQLite 单文件、schema 版本迁移、种子层（只读）+ 用户覆盖层（可重置）查询（覆盖层优先） | `font_intel/fonts_db.py` | 单测 `tests/test_fonts_db.py`：迁移、双层合并、重置 |
| T1.2 | 本机字体库索引：系统+用户目录扫描；fontTools 读 name/OS2 表（规范名/中日英别名/厂商/license 字段）；Pillow 渲染常用字参考字形缓存 | `font_intel/fontlib_index.py` | 索引 500+ 本机字体无崩溃；许可字段入库率与缺字段处理有单测 |
| T1.3 | ETL 规则解析（S0+S1）：GBK/Big5→UTF-8、CRLF→LF、全半角归一；厂商前缀规则表 + 六类句式规则（`→` 映射 / `（旧：…）` 改名 / 字重列举 / `追加搭配：` / `没有…对应` 负映射 / 坑名清单） | `scripts/import_jp_cn_font_map.py`、`font_intel/etl/` | 黄金样本快照单测 `tests/test_font_etl_rules.py`（txt 勘误逐行类型判定） |
| T1.4 | ETL LLM 结构化（S2）+ 校验（S3）：自由格式行交 LLM 分类切分（只做结构化不做知识推断），JSON Schema 六类记录；走 `call_llm` 有界退避、小批量多批；词表校验未命中降级"待人工" | `font_intel/etl/` | mock LLM 单测；429 模拟下批量任务有界等待后降级为待人工清单 |
| T1.5 | ETL 人工复核 GUI（S5）+ 入库溯源（S6）：低置信记录逐条采纳/否决（复用 `scan_review_dialog.py` 勾选行交互模式）；每条带 `source_file / line_no / method / confidence`，`source="Seekladoom/Japanese-Chinese-Fonts-adaptation (MIT)"` | 新建复核对话框 | 复核结果写入覆盖层；种子层不可变、可整体重置 |
| T1.6 | 合规决策引擎：类别×场景→动作规则表；配置持久化（GUI 设置 + CLI 参数 + 配置 JSON 三处一致）；硬红线代码化；"已获授权"勾选写覆盖层放行 | `font_intel/compliance.py` | 规则表全组合单测 `tests/test_compliance.py`；三处配置读写一致 |
| T1.7 | styling 集成：`_get_ass_header()` Style 行字体名过闸 + 逐事件 `\fn` 覆盖通道 + 模板字体仅报告；`replace_auto` 需置信度阈值且留痕可回退 | `core/subtitle_generator/styling.py` | **回归门禁：全关时输出与 T0.1 基线逐字节一致**；`tests/test_compliance_styling.py` 覆盖 allow/prompt/replace_auto/report_only 四动作 |
| T1.8 | 合规报告：`*_compliance_report.md` + `.json`（字体→置信度→类别→决策→依据→官方链接）+ 固定免责声明 | `font_intel/compliance.py` | 报告随 ASS 同目录输出；字段与决策留痕一致 |
| T1.9 | i18n（zh_CN/zh_TW/ja_JP/en 四份 ts→qm）+ 打包（`requirements-fontintel.txt`、deb `Suggests`）+ README / packaging.md / CHANGELOG 同步 | `i18n/`、打包树 | 四语言无缺译；deb 构建通过；文档与实际一致 |

### Phase 2：翻译阶段（5 天）→ 里程碑 M2

| 编号 | 任务 | 涉及文件 | 验收标准 |
|---|---|---|---|
| T2.1 | 翻译提供方链：云端（复用 `_post_chat`）→ 本地 Sakura（OpenAI 兼容零适配，GGUF/Ollama 文档化）→ VLM 兜底（`VLM_REFINE_*` 环境变量）；`LlmApiError` → 保留原文 | `font_intel/translation.py` | `tests/test_translation.py`：fake_llm_server 注入 429 + Retry-After，总等待 ≤300s 后保留原文，任务链不退 |
| T2.2 | 字幕特化：前后 N 行上下文窗口；`glossary.json` 术语表写入 prompt；行长约束（`MarginL/R` 估算 + 缩译/`\N` 断行，统一 `\n`→`\N`） | `font_intel/translation.py` | 术语强制一致用例；超长行断行用例 |
| T2.3 | 流水线接入：阶段 4（润色之后）；`PipelineWorker` / CLI 贯通选项（`engine_options` 同款模式）；进度/取消走 `polish_progress_callback` 同款回调；双语按 ROI `ocr_lang` 各自翻译（未配置回退 `_detect_language`） | `core/pipeline_worker.py`、`cli.py` | **单测断言翻译只挂主进程阶段 4、chunk worker 路径无网络调用**；GUI/CLI 开关生效 |
| T2.4 | 翻译联动字体映射：译文目标语言 → `matching.py` 三级链（日文字体→中文对位→开源替代）→ 合规闸门 → 落名 + 报告；表中无对应走同风格类别，仍无则保持原字体名+报告标注 | `font_intel/matching.py` | 三级链查询单测 `tests/test_matching.py`；无对应路径不强行替换 |
| T2.5 | GUI 选项组（`components/control_panel.py`）+ `main_window/pipeline_control.py` 选项透传 + CLI `--translate-*` 参数 + i18n | 控制面板、CLI、i18n | 翻译开关从 UI 贯通到输出；四语言无缺译 |

### Phase 3：字体识别闭环（8 天）→ 里程碑 M3

| 编号 | 任务 | 涉及文件 | 验收标准 |
|---|---|---|---|
| T3.1 | 识别器接口 + YuzuMarker 候选生成：`crop+text → [FontCandidate]`；HF 权重延迟下载（`HF_ENDPOINT` 镜像、有界重试、失败可恢复不阻塞主流水线）；torch 依赖按 T0.3 决策隔离 | `font_intel/recognizer/base.py`、`yuzu.py` | 未安装 torch/权重时优雅跳过（功能整体可关）；下载中断可重试 |
| T3.2 | 字形重排裁决：按已知文本渲染候选字体参考字形，与视频字块归一化比对（SSIM / pHash / HoG+余弦），Top-N 重排 + 置信度 | `font_intel/recognizer/glyph_rerank.py` | 合成样张单测 `tests/test_glyph_rerank.py`：正确字体排首位率达标（标定记录） |
| T3.3 | `font_identify` 后置阶段：随机访问取帧采样字块（同 (roi, 稳定文本段) 缓存）；与 OCR 结果按行关联；整体可勾选关闭 | `core/pipeline_worker.py`（阶段 4 前挂载） | 关闭时零开销；开启时识别耗时/内存记录入基准 |
| T3.4 | 端到端闭环：识别 → matching → compliance → 字体名落 ASS + 报告；缺口（unknown/低置信/更优建议）→ 更新建议包（JSON patch，逐条附理由）→ GUI"字体复核"对话框逐条确认入覆盖层；`method=llm_inferred` 未确认不参与 `replace_auto` | 新建字体复核对话框、`font_intel/` | 用测试视频走通「识别→决策→落名→缺口→确认→下次生效」全链路 |
| T3.5 | `font_cli.py`（`vso-font identify`：图片/视频+ROI）+ `/usr/bin/vso-font` 启动器进打包清单 + 基准脚本扩展 | `font_cli.py`、打包树 | `vso-font identify` 对图片与短视频跑通；现有 `cli.py` 行为不变 |
| T3.6 | i18n + README/docs 同步 + Phase 3 全量回归 | `i18n/`、`docs/` | 四语言无缺译；回归门禁全绿 |

### Phase 4：验收与发布（3 天，含缓冲）→ 里程碑 M4

| 编号 | 任务 | 验收标准 |
|---|---|---|
| T4.1 | 全量回归：新功能全关基线逐字节对比 + 现有 pytest 套件 + 新增套件全绿 | G4 达成 |
| T4.2 | 429 降级专项：翻译 / ETL 结构化 / 模型下载三条服务端链路逐一模拟验证 | G5 达成；总等待均有界、降级后任务链存活 |
| T4.3 | 打包验证：deb 构建安装，`vso-font` / 主 CLI / GUI 三入口冒烟；未装 fontintel 依赖时功能优雅缺席 | G1–G3 在 deb 环境冒烟通过 |
| T4.4 | 版本发布：`__version__` → 2.8.0、CHANGELOG、tag | 发布流程符合 packaging.md 检查清单 |

---

## 5. 测试方案

| 测试文件 | 覆盖 |
|---|---|
| `tests/test_fonts_db.py` | schema 迁移、种子/覆盖双层查询优先级、覆盖层重置、并发读写 |
| `tests/test_fontlib_index.py` | 目录扫描、fontTools 字段提取（含缺 license 字段容错）、字形缓存命中 |
| `tests/test_font_etl_rules.py` | S0 归一（GBK/CRLF/全半角）、S1 六类句式规则逐类黄金样本 |
| `tests/test_font_etl_llm.py` | mock LLM 的 S2 分类切分、S3 词表校验降级、429 有界退避后降级为待人工 |
| `tests/test_matching.py` | `lookup_jp_font` / `open_source_alternates` 三级链、反向查询、同风格类别兜底 |
| `tests/test_compliance.py` | 类别×场景→动作全组合、硬红线（破解渠道链接永不出现）、已获授权放行 |
| `tests/test_compliance_styling.py` | Style 行过闸、`\fn` 通道、模板只报告、四动作对 ASS 输出的影响 |
| `tests/test_translation.py` | 三级提供方降级链、术语表、行长约束、`LlmApiError` 保留原文、翻译不在 chunk worker 路径 |
| `tests/test_glyph_rerank.py` | 渲染比对、Top-N 重排、置信度阈值 |
| `tests/test_vso_font_cli.py` | 独立入口参数、图片/视频输入、缺依赖优雅退出 |

- **回归门禁**：T0.1 基线 + 每个 Phase 结束跑一次；判定 = ASS 输出逐字节一致 + `ocr_calls` 不变。
- **429 模拟**：复用 `benchmarks/fake_llm_server.py` 注入 429 + Retry-After，模式沿用 `tests/test_llm_client_backoff.py` / `tests/test_llm_api_error_degradation.py`。
- **外部数据黄金样本**：Seekladoom txt 勘误与厂商表抽样入库为测试夹具（只含文本元数据，不含字体文件与样张图）。

## 6. 进度表（单人全职，按工作日）

```
工作日  1    2    3    4    5    6    7    8    9    10   11   12   13   14   15   16   17   18   19   20   21   22   23   24   25   26   27   28
        [P0──][P1──────────────────────────][P2────────────][P3────────────────────][P4/缓冲──]
              M1▲(D12)                                    M2▲(D17)        M3▲(D24)              发布▲(D26-28)
```

| 里程碑 | 时点 | 交付物 | 达成标志 |
|---|---|---|---|
| M0 | D2 | 基线存档、数据源就绪、torch 共存决策 | 回归门禁可执行 |
| M1 | D12 | fonts.db + ETL 管线 + 合规引擎 + 报告 | G1 达成；全关回归逐字节一致 |
| M2 | D17 | 翻译阶段（三级提供方 + 字幕特化） | G2 达成；429 模拟通过 |
| M3 | D24 | 字体识别端到端闭环 | G3 达成 |
| 发布 | D26–28 | tag v2.8.0、deb 包、更新日志 | G4/G5 达成 |

每日流程约定沿用既有计划：开工先跑 `pytest`；commit 粒度 = 任务粒度；每 Phase 结束归档回归报告。

## 7. 风险与应对（执行视角，选型风险见方案文档 §9）

| 风险 | 概率 | 影响 | 应对 |
|---|---|---|---|
| torch 与 paddlepaddle 同 venv 冲突（numpy/ABI）或双框架常驻内存 2GB+ | 中 | 高 | T0.3 spike 前置到 P0；冲突即改独立 venv + 子进程隔离（主流水线永不 import torch） |
| ETL 数据质量低于预期（rar 表残缺、txt 勘误覆盖率低） | 中 | 中 | 规则解析先行给出可量化覆盖率（T1.3 验收含统计）；缺口走 LLM 结构化 + 人工复核，种子库允许随版本增量补齐 |
| 翻译批处理与润色框架耦合过深，改动引发润色回归 | 中 | 中 | 平移而非修改：`polish_subtitle_texts` 骨架抽取为可复用调度器或复制适配，润色路径有既有测试锁定 |
| 字形重排精度不达标（艺术字/特效字干扰） | 中 | 中 | T3.2 标定任务给出量化指标；不达标则降级为"仅 Top-N 候选展示 + 人工确认"，不承诺自动裁决 |
| HF 权重下载在网络受限环境失败 | 高 | 低 | `HF_ENDPOINT` 镜像 + 有界重试 + 可恢复错误提示；识别功能整体可关，不阻塞主流水线 |
| 模板用户样式与合规替换冲突（模板写死商业字体） | 低 | 中 | 明确"不重写用户模板"红线，模板字体进报告由用户自行决策 |

## 8. 验收标准总表（Definition of Done）

1. G1–G5 全部达成（见第 1 节）。
2. 三条服务端链路（翻译 / ETL 结构化 / 权重下载）429 模拟下均有界等待并优雅降级，任务链不退出。
3. `pytest` 全量（既有 + 新增）全绿；回归基线对比脚本可重复执行。
4. deb 构建通过；`vso-font` 入口、i18n 四语言、README / packaging.md / CHANGELOG 同步更新。
5. 发布 tag v2.8.0。

## 9. Backlog（本轮之后）

| 项 | 说明 | 前置 |
|---|---|---|
| 字体文件嵌入（`[Fonts]` UUEncode） | 嵌入合规闸门规则本期已定义，能力按需求落地 | M1 合规引擎 |
| DeepFont 系英文/拉丁识别补充 | `twelfth-star/universal-font-recognition`、`Dexterp37/fontina`，仅英文艺术字场景 | P3 识别管线 |
| 确认先验写回识别 | 用户确认结果作为先验提升后续识别排序（方案 §3.2 预留） | P3 + 数据积累 |
| 映射表增量在线核对 | 随上游 Seekladoom 仓库更新做 diff 式增量导入 | ETL 管线稳定 |

---

## 附录：T0.3 决策记录（待填）

> spike 完成后回填：venv 形态（同 venv 可选依赖 / 独立 venv 子进程）、双框架同进程内存峰值、numpy 版本约束结论。

- 决策：＿＿＿
- 内存峰值：＿＿＿
- 备注：＿＿＿
