# 字体智能与翻译扩展方案（字体识别 / AI 翻译 / 中日字体匹配 / 合规决策）

> 文档版本：v1.1（2026-09-21，按当前代码 v2.7.3 逐模块复核修订）
> 前置：[development_plan.md](./development_plan.md) Phase 1–4 已完成（当前版本 2.7.3）
> 状态：设计稿，未实施；开发排期见 [development_plan_font_translation_compliance.md](./development_plan_font_translation_compliance.md)
> 硬约束：所有新增服务端调用必须复用 `core/llm_client.py` 的 429 有界退避封装（最大重试次数、单次等待上限、总等待预算、尊重 Retry-After、耗尽后优雅降级），不得绕过。

---

## 1. 需求概述与总体判断

| # | 需求 | 总体判断 |
|---|---|---|
| 1 | 集成开源字体识别（类似求字体网） | 求字体网为闭源商业服务，无完整开源平替；走**多开源项目组合**路线：CJK 深度模型（YuzuMarker.FontDetection）生成候选 + 本地字体库字形重排裁决，可选 DeepFont 系项目补英文场景 |
| 2 | AI 翻译 | 项目已有 OpenAI 兼容 LLM 客户端与润色阶段；翻译作为**新流水线阶段**接入，提供云端 API / 本地模型（Sakura）/ VLM 三级提供方 |
| 3 | 中日字体匹配 | Seekladoom/Japanese-Chinese-Fonts-adaptation（MIT）**导入本地字体数据库，作为参考映射表**（用户已确认此定位），不作为权威判决 |
| 4 | 合规决策模块 | 独立决策引擎 + 字体许可元数据库；**硬红线（不可配置）：系统绝不提供、不链接、不缓存破解版或非官方渠道字体文件** |

四项需求共享一个基础设施：**本地字体数据库**（SQLite 单文件，无服务依赖）。识别结果要查库（字体名→许可）、匹配要查库（日文→中文映射链）、合规要查库（许可类别→决策），因此数据库是第一阶段交付物。

---

## 2. 总体架构

新增顶层包 `font_intel/`，与现有流水线的衔接点全部走既有的阶段化结构（`pipeline_stages.py`），可逐项开关、逐项降级：

```
font_intel/
├── fonts_db.py            # 本地字体数据库（SQLite）：字体元数据/许可、中日映射、替代链
├── fontlib_index.py       # 本地字体库扫描索引（fontTools 读 name/OS2 表，Pillow 渲染参考字形）
├── recognizer/
│   ├── base.py            # 识别器接口：crop+text -> [FontCandidate(name, score, source)]
│   ├── yuzu.py            # YuzuMarker.FontDetection 深度模型（候选生成）
│   └── glyph_rerank.py    # 字形重排：渲染参考字形 vs 视频字块，SSIM/pHash/HoG 裁决
├── matching.py            # 中日映射查询：lookup_jp_font / open_source_alternates（三级链）
├── compliance.py          # 合规决策引擎（规则表）+ 合规报告生成
└── translation.py         # 翻译阶段（三级提供方 + 字幕特化）
```

数据流（运行时；字体识别与 LLM 匹配均为可勾选项，关闭时跳过对应环节）：

```
OCR 管线（已有，输出 det 四边形 + 文本）
  → [font_intel 识别] 字块裁剪 + 已知文本 → 候选字体 Top-N
  → [matching 查询] 许可类别 + 语言映射链（感知翻译目标语言）
  → [compliance 决策] allow（直接写原字体）/ prompt / replace_auto（相似字体）/ report_only
  → subtitle_generator/styling.py 落字体名（替换前后写入报告）
  → 缺口回写：unknown 字体 / 低置信映射 / 更优建议 → 更新建议包 → 用户确认入覆盖层
文本行 → [translation 翻译] → 目标语言字体映射 →（同一合规闸门）→ ASS 生成
```

**落字体名的准确位置（按 2.7.3 代码现状）**：字体名目前写在 `_get_ass_header()` 生成的 `Style:` 行（styling.py:264-270，Default/CH/JP/KO/RU/Top/Scene/NoteBox 各一条），代码中尚无逐事件 `\fn`。因此合规闸门有两个写入口：① **Style 行字体名替换**（识别/替换结果落到样式层，改动最小、与现状结构一致）；② **逐事件 `\fn` 覆盖通道**（同一字幕组内混排多字体时用，属新增能力）。用户自带模板（`template_path`）会整体替换头部，**不重写用户模板**——模板引用的字体只进合规报告。

**阶段归属约束（pipeline_stages.py 既有设计）**：阶段 1–3 在分块并行模式下运行于 chunk worker 子进程，**禁止任何服务端调用**（这是该模块的明确设计约束）。因此：翻译与 LLM 缺口分析只能挂在主进程的阶段 4（与 LLM 润色同侧）；字体识别是本地计算，作为阶段 3 之后、ASS 生成之前的**主进程后置阶段**执行。阶段 4 只有文本与框坐标、没有图像——字块图像按既有随机访问取帧模式（`roi_extractor.extract_single_roi_crop_with_time`，边界精修同款）对每个字幕组采样重裁，不跨阶段携带图像。

数据库文件位置：`~/.local/share/video_subtitle_ocr/fonts.db`（与 `utils/secret_store.py` 已有的 `XDG_DATA_HOME` 约定一致，尊重环境变量覆盖），schema 版本化并带迁移。**种子数据只入库"名称 / 许可 / 映射"等事实元数据，不分发任何字体文件本体**——字体文件仅在本机已安装时索引其路径，避免许可风险随 deb 扩散。

---

## 3. 需求 1：字体识别（多项目组合、本地集成）

### 3.1 组件选型（均为已核实的开源项目）

| 组件 | 项目 | 许可 | 角色 |
|---|---|---|---|
| CJK 深度识别（主） | [YuzuMarker.FontDetection](https://github.com/JeffersonQin/YuzuMarker.FontDetection) | MIT | 首个 CJK 字体识别+样式提取模型；ResNet-18/50 骨干，PyTorch 2.0；HuggingFace 有预训练权重与合成数据集；与本项目中日文字幕场景完全对口 |
| 字形重排（裁决） | 自研（求字体同类算法思路） | — | 本项目独有优势：OCR 已给出"是什么字"与文本框，**无需做字符分割**——直接按已知文本渲染本地字体参考字形，与视频字块归一化后比对（SSIM / 感知哈希 / HoG+余弦），对深度模型候选重排 |
| 英文/拉丁补充（可选，二期） | [twelfth-star/universal-font-recognition](https://github.com/twelfth-star/universal-font-recognition)、[Dexterp37/fontina](https://github.com/Dexterp37/fontina) | — | DeepFont 系复现，仅在英文艺术字场景启用 |

### 3.2 关键设计

- **定位为"候选生成器"而非"判决器"**：YuzuMarker 字体分类 top-1 约 49%，输出 Top-N 候选 + 置信度，经字形重排后交用户确认（UI 提供候选列表与并排样张预览）。确认结果可写回数据库作为后续先验。
- **本地字体库索引**：扫描系统字体目录 + 用户指定目录，fontTools 提取规范名/别名/厂商/许可字段，入库并渲染常用字参考字形缓存，供重排与"已安装字体优先推荐"。
- **依赖与打包**：torch 较重（CPU 版 200MB+），放可选 `requirements-fontintel.txt`；deb 侧用 `Suggests`；模型权重首次使用时从 HuggingFace 下载（支持 `HF_ENDPOINT` 镜像；下载调用同样走有界重试，失败给出可恢复错误提示，不阻塞主流水线）。
- **入口**：独立 CLI `vso-font identify`（图片/视频+ROI）。注意现有 `cli.py` 是"单命令 + 位置参数 video"形态（cli.py:768），直接塞子命令会破坏既有调用兼容——`vso-font` 走独立入口模块（`font_cli.py`）+ 独立启动器 `/usr/bin/vso-font` 进打包清单。GUI 新建"字体复核"对话框（复用 `scan_review_dialog.py` 的勾选行 + 快照缩略图交互模式），主流水线产物按字幕组逐条确认；深度扫描复核对话框仅在扫描期场景字快照上增设识别预览分栏，不承担运行时确认流。

---

## 4. 需求 2：AI 翻译

### 4.1 流水线位置

```
OCR 识别 → LLM 润色/纠错（已有） → 翻译（新增阶段） → ASS 生成
```

翻译阶段可整体开关；源语言按 ROI 的 `ocr_lang` 路由（逐 ROI 语言是 2.7.3 已有能力，含"自动跟随全局"；未配置时用 styling 的 `_detect_language` 文本检测兜底），双语字幕各 ROI 各自翻译；目标语言全局设置（默认简体中文）。

### 4.2 三级提供方（按序降级）

| 级别 | 提供方 | 说明 |
|---|---|---|
| 云端 API | 任意 OpenAI 兼容端点（DeepSeek 等） | 复用 `subtitle_llm_polish._post_chat`（已基于 `llm_client.call_llm`，自动获得 429 有界退避与 `LlmApiError` 归一），新增翻译 system prompt 与参数 |
| 本地模型 | [Sakura-13B-Galgame](https://github.com/SakuraUmi/Sakura-13B-Galgame) | 日中 ACGN 特化翻译模型，契合动画字幕语域；经 llama.cpp / sglang / Ollama 部署后即为 OpenAI 兼容 API，**零代码适配**；显存不足走量化版（GGUF） |
| VLM 通道 | 复用 `vlm_refine` 配置 | 场景文字/艺术字（Logo、手写体）直读图翻译，文本通道失败的兜底 |

### 4.3 字幕特化

- **批处理骨架直接平移**：复用 `polish_subtitle_texts` 已有的批次切分 + 线程池并发（`max_concurrent_requests`）+ 取消检查 + **单批失败保留原文**框架，只换 prompt 与输出校验——不新写一套并发调度。
- **上下文窗口**：逐行翻译时携带前后 N 行与元数据（说话场景），避免逐行孤立产生人称/时态漂移。
- **术语表**：作品名/人名/专有名词强制一致（用户可编辑 `glossary.json`），写入 prompt 约束。
- **行长约束**：翻译后按 ASS 样式 `MarginL/R` 估算可容纳字符数，超长时提示模型缩译或自动断行（`\N`，注意既有 Backlog 项——多行文本换行标签规范化 `\n` → `\N` 一并处理）。
- **失败降级**：捕获 `LlmApiError`（429 退避耗尽归一后的异常类型）保留原文并在日志标注，绝不中断整条任务链；单测沿用 `tests/test_llm_api_error_degradation.py` 与 `benchmarks/fake_llm_server.py` 的模拟模式。
- **VLM 通道**：即 `core/vlm_refine.py` 的既有配置（`VLM_REFINE_BASE_URL/API_KEY/MODEL` 环境变量），不新增配置面。

---

## 5. 需求 3：中日字体匹配（本地数据库化，参考映射）

**用户已确认定位（两期设计）**：**前期（开发期一次性）**由 LLM 主导对 Japanese-Chinese-Fonts-adaptation 做数据清洗，生成种子字体数据库随包分发；**运行时**只做查询、匹配与缺口反馈，不依赖原始仓库文件。

### 5.0 数据源实测结论（2026-09-21 拉取核实）

- `匹配表修改备份 20190805.txt`（28 行，GBK 编码、CRLF）**并非结构化匹配表，而是自由格式的修改笔记/勘误增量**，多种记录类型混排：
  - 映射：`A1明朝 → 华文宋体`
  - 字重清单：`B字重：正中黑`、`E字重：方正粗黑宋，华康俪金黑，MSungGold`
  - 负映射：`华康俪金黑 & 華康儷金黑，没有本家对应的日文版本`
  - 改名：`DFPMaruMojiRD-W12（旧：DFPBrushRD-W12）`
  - 追加搭配：`FOT-Chiaro 追加搭配：汉仪润圆`
  - 坑名清单（"简繁日名称翻车"）：华康新综艺体 / 华康雅宋体 / 华康华综体 / 华康勘亭流 / 华康龙门石碑
  - 待办：`方正悠宋后6个字重待追加`；标记：`森泽UD新黑 简繁通用`、`简繁通用像素字体追加：UniFont`
- **完整的三大厂商（DynaFont/Fontworks/Morisawa）结构化对照表在 rar 压缩包内**（含样张图）。因此清洗主对象是用户本机解包后的 rar 内容，txt 作为增量与勘误参与合并。

### 5.1 清洗管线（规则优先 → LLM 辅助 → 人工兜底）

`scripts/import_jp_cn_font_map.py`（规则引擎）+ `font_intel/etl/`：

| 阶段 | 处理 | 说明 |
|---|---|---|
| S0 解包归一 | rar 本机解包（unrar/7z，不进 deb 打包链）；编码统一 GBK/Big5→UTF-8，CRLF→LF，全半角/破折号归一 | 一次性或低频执行 |
| S1 规则解析 | 厂商前缀规则表（`DF*`→DynaFont、`FOT-*`→Fontworks、`A-OTF`→Morisawa、`华康`→DynaFont 中文名、`方正`→Founder、`汉仪`→HanYi、`蒙纳`→Monotype…）；句式规则（`→` 映射、`（旧：…）` 改名、顿号/逗号字重列举、`追加搭配：`、`没有…对应` 负映射） | 能用规则解决的不走 LLM，省钱且可复现 |
| S2 LLM 辅助结构化 | S1 打不动的自由格式行交 LLM：行类型分类、字体名切分与别名归并（简/繁/日写法同一字体，如 `华康俪金黑 & 華康儷金黑`）、厂商/类别推断 | **只做分类与切分，不做字体知识推断**（防编造映射）；按 JSON Schema 输出六类记录：`mapping / rename / negative_mapping / pitfall / flag_sc_tc / todo` |
| S3 校验 | Schema 校验 + **词表校验**（字体名必须命中本地字体库词表或厂商目录，未命中即降级为"待人工"——防 LLM 幻觉字体名） | 词表来自需求 4 许可库种子 |
| S4 样张核验（可选） | rar 内样张图交 VLM 读取图上字体名与样字，与表记录交叉验证；已安装字体可渲染同字与样张比对 | 复用 VLM 通道配置 |
| S5 人工复核 | 低置信记录在 GUI 逐条采纳/否决（复用深度扫描复核交互），结果写用户覆盖层 | LLM 产出永不免审直接生效 |
| S6 入库溯源 | 写入 `jp_cn_font_map` 表，每条带 `source_file / line_no / method(rule/llm/vlm/human) / confidence` | 逐条标注 `source="Seekladoom/Japanese-Chinese-Fonts-adaptation (MIT)"` |

### 5.2 LLM 辅助分析的约束

- 调用复用 `core/llm_client.py` 的 429 有界退避封装；小批量多批，任务为一次性/低频，量级（百行级）成本可忽略。
- LLM 定位为"结构化器"而非"知识源"：映射关系只接受源文件明示的内容，LLM 不得补充自己知道的（或以为知道的）字体对应关系；想扩充映射只能走 S4 样张核验或人工。
- **数据库分层**：种子层（随包发布，只读）+ 用户覆盖层（本地修正、人工确认记录），查询时覆盖层优先——参考表允许被用户经验修正。
- **查询 API**（`matching.py`）：
  - `lookup_jp_font(name) -> [CN 字体候选]`（日文 → 中文对位）
  - `open_source_alternates(font) -> [开源字体]`（衔接需求 4 的替代链）
  - 组合为三级链：**识别出的日文字体 → 中文对位字体 → 开源替代字体**
- **参考定位的含义**：映射结果以"建议候选"形式呈现，最终由合规决策与用户确认生效；UI 允许对单条映射标记"采纳/否决"并写回覆盖层。

### 5.3 运行时字体匹配流水线（OCR 中，用户勾选 LLM 翻译/分析时）

用户确认的目标流程，按「是否翻译」分两条路径：

```
识别文本 + 识别字体（Top-N）
│
├─ 不翻译（仅识别文本）：合规规则决定字体名落法（Style 行或 \fn）
│   ├─ 用户设置"允许商业字体" → 直接把原字体名写入 ASS（识别即可用）
│   └─ 用户设置"仅非商业字体" → 沿三级链找相似字体（开源替代）替换字体名
│
└─ 翻译到目标语言：源语言字体 → 目标语言字体映射
    ├─ 日 → 中：Seekladoom 参考表三级链（日文字体 → 中文对位 → 开源替代）
    ├─ 中 → 日 / 其他：反向查询同一张表
    └─ 表中无对应（如日 → 英）：同风格类别匹配
        （黑体→Sans、明朝/宋→Serif、楷/手写→Script/Casual；
         在目标语言字体库中用字形重排检索"同类形"，优先开源字体）
```

- 映射查询全部走数据库；LLM 只在**勾选了 LLM 分析**且出现缺口（unknown / 低置信 / 无对应）时介入，给出带理由的建议。
- 目标语言映射结果同样过合规决策（需求 4），开源替代优先。

### 5.4 数据库反馈更新（不准确 / 更好建议 / 新字体）

- **触发条件**：① 识别命中 `unknown` 字体（不在本地库）；② 现有映射置信度低或用户在复核 UI 中否决；③ LLM 匹配分析时发现更优映射候选。
- **处理流程**：LLM 生成「更新建议包」（JSON patch：新增字体记录 / 映射修正 / 新增映射，逐条附理由与依据来源）→ GUI 提示"是否更新字体匹配库" → 用户**逐条确认**后合并入覆盖层（种子层数据不可变，可整体重置）；支持导出/导入 `.json` 建议包留档或分享。
- **边界**：不自动联网上报或拉取（隐私 + 合规）；LLM 推断的新字体记录标注 `method=llm_inferred` 与置信度，未经人工确认不参与 `replace_auto` 自动替换（只作建议展示）。

---

## 6. 需求 4：合规决策模块

### 6.1 许可元数据（fonts.db 内置表）

- 许可类别枚举：`open_source`（OFL/Apache/MIT 等开源）｜`free_commercial`（免费商用、非开源）｜`commercial_paid`（商用需授权）｜`unknown`
- 字段：规范名、中日英别名、厂商、字体类别（黑/宋/楷/圆…）、语言覆盖、许可协议、官方渠道 URL、开源替代链、来源标注
- 种子来源：
  1. [wordshub/free-font](https://github.com/wordshub/free-font)（926+ 免费可商用字体清单，含需厂商授权说明）
  2. [yuleshow/chinese-fonts](https://github.com/yuleshow/chinese-fonts)（免费商用中文字体分类整理）
  3. fontTools 读取本机已安装字体 name 表中的 license 字段
  4. Seekladoom 映射表（构成替代链的"中文对位"一环）
- `unknown` 默认按最严格类别处理（可配置，但默认从严）。

### 6.2 决策引擎（用户可配置默认规则）

规则表维度：**许可类别 × 使用场景（个人/发布/商用）→ 动作**。动作枚举：

| 动作 | 行为 |
|---|---|
| `allow` | **直接把原字体名写入 ASS**（Style 行替换或逐事件 `\fn`，即 5.3 路径一）——用户设置允许商业字体时，识别出的字体即设即用 |
| `prompt` | 逐次弹窗询问（默认对 commercial_paid） |
| `replace_auto` | 用户设置仅非商业字体时，沿三级链 / 同风格类别**匹配相似字体**替换字体名（Style 行或 `\fn`），替换前后写入报告 |
| `report_only` | 仅写入合规报告，不改输出 |

- 用户默认规则持久化到配置（GUI 设置面板 + CLI 参数 + 配置 JSON 三处一致）；单次决策可临时覆盖。
- **硬红线（写死在代码，不可配置）**：系统不提供、不链接、不缓存任何"破解版/非官方渠道"字体文件；对 `commercial_paid` 字体只输出**厂商官方授权/购买页面链接**。
- **三条合规路径的落地**：
  1. **开源替代**：`replace_auto` 按三级映射链替换，报告记录替换前后对照与依据；
  2. **购买授权**：报告附官方链接清单；用户勾选"已获授权"后该字体放行（记录到覆盖层）；
  3. **艺术化修改**：输出指导性说明（基于 OFL 字体做衍生修改的路径与保留字体名规则），系统不自动执行字形变形。
- **字体嵌入单独过闸**：「ASS 引用字体名（`\fn`/Style 行）」与「内嵌字体文件（`[Fonts]` UUEncode）」是合规强度不同的两个动作——commercial_paid 字体未获授权时**禁止嵌入**（只允许引用名或替换为替代字体）；开源 / 免费商用字体可按用户选项嵌入以保证跨机渲染一致。**注意：当前代码完全没有字体嵌入能力，嵌入本身是本方案的可选新增能力（排期在引用名闸门之后，可延后）**——此处先行定义闸门规则，是为了将来实现嵌入时对齐，一期交付不含嵌入。报告需列出"ASS 引用了但播放端可能未安装"的字体清单（缺字体时播放器回退渲染，观感不可控）。

### 6.3 合规报告

随 ASS 同目录输出 `*_compliance_report.md`（+ `.json` 供程序处理）：每个用到的字体 → 识别置信度 → 许可类别 → 决策与依据 → 官方链接。报告附固定免责声明：**不构成法律意见，商用前请自行核实授权条款**。

---

## 7. 与现有代码的集成点（改动清单）

| 现有模块 | 改动 |
|---|---|
| `core/pipeline_worker.py` + `core/pipeline_stages.py` | 新增 `font_identify`（本地计算，主进程后置阶段：阶段 3 之后、ASS 生成之前，随机访问取帧采样字块）与 `translate`（**仅主进程阶段 4**，与 LLM 润色同侧——阶段 1–3 运行于 chunk worker 子进程，禁止服务端调用）两个可选阶段，可跳过、可降级 |
| `core/subtitle_generator/styling.py` | 字体名写入前过合规决策：`_get_ass_header()` 的 Style 行替换 + 新增逐事件 `\fn` 覆盖通道；模板引用字体仅进报告不重写。合规关闭时输出与现状逐字节一致（回归测试保证） |
| `core/llm_client.py` | 仅复用不改动；翻译、ETL 结构化、模型下载重试等新服务端调用全部走该封装（`call_llm` / `LlmApiError`） |
| `components/scan_review_dialog.py` | 深度扫描复核增设"字体"预览分栏；运行时字体确认走新建"字体复核"对话框（复用其勾选行 + 缩略图模式） |
| `components/control_panel.py` + `main_window/pipeline_control.py` | 控制面板新增"字体识别 / 翻译 / 合规"选项组与默认决策规则设置；`pipeline_control` 收集选项透传 `PipelineWorker`（与 `engine_options` 同一贯通模式）。注意选项面板在 `components/` 而非 `main_window/` |
| `cli.py` | 现有 CLI 保持不动（单命令 + 位置参数 `video`，兼容优先）；`vso-font` 为独立入口 `font_cli.py` + 启动器 `/usr/bin/vso-font` 进打包清单（与 `video-subtitle-ocr-cli` 同模式），翻译与合规参数同时进两处 |
| `scripts/import_jp_cn_font_map.py` + `font_intel/etl/` | 新增：参考表解包、规则解析、LLM 结构化、校验、人工复核导出（见 5.1） |
| 依赖 | 新增可选 `requirements-fontintel.txt`（torch、fontTools 等；torch 与 paddlepaddle 同 venv 的兼容性需先验证，冲突则 `vso-font` 独立 venv）；i18n 补齐 zh_CN / zh_TW / ja_JP / en 四份 `.ts`/`.qm` |

---

## 8. 分期计划（1 人）

| 阶段 | 内容 | 周期 | 说明 |
|---|---|---|---|
| P0 | 基线与准备：新功能全关的 ASS 输出基线采集（回归门禁）、外部数据源许可核对、rar 本机解包、torch+paddle 共存性验证 spike | ~0.5 周 | 基线先行，见开发计划 |
| P1 | 本地字体数据库 + 参考表 ETL 清洗导入（规则 + LLM 辅助 + 人工复核）+ 合规决策引擎 + styling 集成（Style 行闸门）+ 合规报告 | ~2 周 | 不依赖深度模型，最快见效；需求 3、4 先落地 |
| P2 | 翻译阶段（三级提供方 + 字幕特化 + 降级链） | ~1 周 | 复用润色批处理骨架与 LLM 基础设施，独立可交付 |
| P3 | 字体识别管线（YuzuMarker 集成 + 字体库索引 + 字形重排 + UI 确认流）+「识别 → 合规决策 → 直接写原字体名 / 相似替换 → 缺口更新建议」端到端闭环 + 翻译联动字体映射 | ~1.5 周 | 依赖 P1 数据库；用户确认的核心运行时流程在此落地 |

P2/P3 相互独立，可按优先级互换；总周期与逐任务排布见 [development_plan_font_translation_compliance.md](./development_plan_font_translation_compliance.md)。

## 9. 风险与对策

| 风险 | 对策 |
|---|---|
| YuzuMarker top-1 约 49% | 定位为候选生成；Top-N + 字形重排 + 人工确认 UI，不承诺全自动准确 |
| torch 依赖过重 | 可选 extra + deb `Suggests` + 模型权重延迟下载 |
| 匹配表/许可清单时效性 | 数据库种子层可随版本更新；报告附免责声明 |
| rar 样张解析（unrar 许可问题） | 只解析 txt 匹配表；样张仅本机可选导入，不进打包链 |
| Sakura 13B 显存门槛 | GGUF 量化 / Ollama 部署；云端 API 作为降级链末端 |
| 合规误判（识别错→错误替换） | 替换动作默认需置信度阈值 + 用户确认（`prompt`），`replace_auto` 仅对高置信度生效且全程留痕可回退 |
| torch 与 paddlepaddle 同 venv（numpy/ABI 冲突、双框架常驻内存 2GB+） | P0 spike 先验证；冲突则 `vso-font` 用独立 venv / 子进程隔离，主流水线不 import torch |
| 翻译/LLM 缺口分析误入 chunk worker 路径（分块并行下服务端调用失控） | 架构约束写死：阶段 1–3 无网络调用；单测断言翻译只挂在主进程阶段 4（与润色同侧） |
| 目标语言缺少同类形字体（如日→英无对应） | 保持原字体名引用 + 报告标注"未映射"，不强行替换；缺口进入 5.4 更新建议 |

## 10. 参考来源

- [Seekladoom/Japanese-Chinese-Fonts-adaptation](https://github.com/Seekladoom/Japanese-Chinese-Fonts-adaptation)（MIT，中日字体匹配表+样张）
- [JeffersonQin/YuzuMarker.FontDetection](https://github.com/JeffersonQin/YuzuMarker.FontDetection)（MIT，CJK 字体识别+样式提取，HF 权重/数据集）
- [twelfth-star/universal-font-recognition](https://github.com/twelfth-star/universal-font-recognition)、[Dexterp37/fontina](https://github.com/Dexterp37/fontina)（DeepFont 系复现）
- [SakuraUmi/Sakura-13B-Galgame](https://github.com/SakuraUmi/Sakura-13B-Galgame)（日中 ACGN 翻译模型，OpenAI 兼容部署）
- [wordshub/free-font](https://github.com/wordshub/free-font)、[yuleshow/chinese-fonts](https://github.com/yuleshow/chinese-fonts)（免费商用字体清单）
