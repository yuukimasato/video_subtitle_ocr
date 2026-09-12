# video_subtitle_ocr 模型升级与优化分析

> 分析日期：2026-09-07
> 分析对象：本仓库当前代码 + PaddleOCR / RapidOCR / 多模态 OCR 生态最新状态
> 附：对另一份 AI 分析内容的逐条事实核查（见第二节）

## 结论速览（TL;DR）

1. **模型升级完全可行且收益明确**：升级到 PaddleOCR 3.7.0 + PP-OCRv6 是首选路径，改动集中在 `core/ocr_engine_paddle.py` 与依赖版本，风险低。相比项目当前实际运行的模型（paddleocr 3.1.0 默认的 PP-OCRv5 **mobile** 档），PP-OCRv6 medium 档检测精度 +4.6%、识别精度 +5.1%（对比对象还是更高的 PP-OCRv5_server）。
2. **另一份分析中若干"建议新增"的能力项目里其实已经实现**（智能跳帧、多帧投票、结果去重合并、LLM 语义纠错、GPU 切换），真正缺失的是：OCR 语言参数从 UI 传递、全自动字幕区域定位、超分辨率预处理、批量推理。
3. **两处事实错误需要纠正**：PaddleOCR-VL **不支持视频输入**（仅图片/PDF，"视频版 / VideosProcessor"说法不实）；"AnyOCR" 项目无法找到，其描述的功能与 RapidOCR/Paddle2ONNX 重合。
4. 追求极致精度可增加 **HunyuanOCR**（腾讯，1B 端到端，已核实支持字幕提取场景）作为可选引擎或"难帧兜底"，但它需要 vLLM 部署，不适合作为默认逐帧引擎。

---

## 一、项目现状盘点

### 1.1 架构与流水线

- GUI：PySide6，处理跑在 `QThread`（`core/pipeline_worker.py`），支持取消、流式分桶（time_slice）模式。
- 流水线：ROI 提取（`core/roi_extractor.py`，grab/retrieve 顺序解码）→ 智能跳帧 OCR（`core/ocr_optimizer.py`）→ 坐标还原（`core/coordinate_restorer.py`）→ ASS 生成（`core/subtitle_generator/` 包）。
- 引擎抽象：`BaseOCREngine` + 注册表（`core/ocr_engine_base.py`），已有三个适配器：
  - **PaddleOCR**（主力，`core/ocr_engine_paddle.py`）
  - **RapidOCR**（ONNX 备选，`core/ocr_engine_rapid.py`，使用已停更的旧包 `rapidocr_onnxruntime`）
  - **Unlimited-OCR**（`core/ocr_engine_unlimited.py`，包装的 `unlimited_ocr` pip 包**实际不存在**——真实的 Unlimited-OCR 是需 vLLM 部署的 3.3B 模型，此适配器是死代码）
- 辅助智能模块：`video_type_detector.py`（元数据+采样帧自动判别视频类型）、`text_source_classifier.py`（叠加字幕 vs 场景文字五层级联分类，可选 LLM 辅助）、`color_presence_gate.py`（颜色门控）、`subtitle_llm_polish.py`（LLM 字幕润色，UI 已暴露 deepseek_polish / 策略复核 / 碎片合并选项）。
- 输出统一格式 `dt_polys / rec_texts / rec_scores / rec_boxes`，`normalize_result` 已兼容 PaddleOCR 3.x 的 dict 格式与旧版行格式。

### 1.2 版本锁定与实际运行模型

| 项 | 现状 | 说明 |
|---|---|---|
| paddleocr | 环境快照 `requirements-a.txt` 锁 **3.1.0**（2025-05）；`requirements.txt` 允许 `>=2.10,<4.0` | 3.1.0 默认模型为 PP-OCRv5 **mobile** 档（3.2.0 起官方默认才切换到 server 档），即当前并非 PP-OCRv5 最强档 |
| paddlepaddle | `>=3.1,<3.3`（规避 3.3 的 oneDNN/new-executor/PIR 回归） | 为兼容性在 `ocr_engine_paddle.py:24-28` 全局关闭了 MKLDNN，CPU 推理偏慢 |
| rapidocr_onnxruntime | 1.3.x | 该包已停止更新（止于 1.4.4），模型停留在 PP-OCRv4 时代 |
| 语言参数 | 引擎声明支持 11 种语言，但 `lang` 从未由 UI 传入，`initialize()` 恒为 `"ch"` | 日语字幕（项目含 ja_JP 翻译，明显目标场景）一直在用中文模型 |
| 测试 | 无任何测试文件 | dev 依赖装了 pytest；引擎抽象层很适合补 mock 单测 |

### 1.3 已实现能力清单（避免重复造轮子）

以下能力**已经存在**，后续优化是"改进"而非"新增"：

| 能力 | 位置 |
|---|---|
| 智能跳帧（SSIM 两阶段搜索：粗扫+二分） | `core/ocr_optimizer.py:287-308` |
| 多帧采样投票（首/中/尾三帧，逐行文本投票+置信度） | `core/ocr_optimizer.py:167-225` |
| 帧间运动哨兵（absdiff 阈值跳过） | `core/ocr_optimizer.py:97-125` |
| OCR 结果去重与合并（连续帧分组成 SubtitleGroup） | `core/subtitle_generator/data_grouping.py` `_group_consecutive_frames` |
| LLM 语义纠错/润色（OpenAI 兼容 + tenacity 有界退避） | `core/subtitle_llm_polish.py`、`core/llm_client.py:77-81` |
| GPU/CPU 切换（env → .gpu_mode 文件 → 运行时探测，三级回落） | `core/ocr_engine_paddle.py:78-146` |
| ROI 预处理 / 颜色门控 | `core/roi_extractor.py`（`apply_roi_preprocess_to_crop` 等）、`core/color_presence_gate.py` |

---

## 二、对另一份 AI 分析的事实核查

| 其分析中的说法 | 核查结果 | 说明 |
|---|---|---|
| PP-OCRv6：+4.6% 检测 / +5.1% 识别（vs PP-OCRv5_server）、tiny/small/medium 三档、单模型 50 语言、OpenVINO CPU 5.2× | ✅ 属实 | 2026-06-11 随 PaddleOCR 3.7.0 发布 |
| PaddleOCR-VL 是"PaddleOCR 的视频版"，支持整视频输入，可参考 VideosProcessor 代码 | ❌ **不实** | PaddleOCR-VL 是**文档解析**模型，官方输入仅支持图片与 PDF；视频/弹幕/字幕支持列在其**未来路线图**中。transformers 实现的 processor 是图像预处理，无 VideosProcessor 用于输入 |
| PaddleOCR-VL 96.33% SOTA | ⚠️ 需限定语境 | SOTA 指文档解析基准（OmniDocBench v1.5 上 VL-1.5 为 94.5%；更高数字来自特定榜单/1.6 版本），不代表视频字幕场景 |
| HunyuanOCR：腾讯端到端 OCR，视频字幕提取表现好、准确率 ~92.87% | ✅ 基本属实 | 2025-11-25 开源，1B 参数、约 2GB 模型文件、100+ 语言、官方推荐 vLLM 部署；已有视频字幕提取实践；">92% 整体准确率优于 Gemini/Qwen/Seed"来自第三方评测，官方主打文档 OCR SOTA。另有更新的 HunyuanOCR-1.5 |
| AnyOCR：基于 ONNXRuntime、可将 PaddleOCR 模型转 ONNX | ❌ 未找到该项目 | 所述功能与 RapidOCR / Paddle2ONNX / paddleocr_convert 完全重合，疑为混淆。本项目的 ONNX 路线应走 RapidOCR 新包 |
| Qwen3-VL 具备视频 OCR 能力 | ✅ 属实 | 视频文字读取是其强项，但部署重（GPU + vLLM/sglang），只适合作为可选高精度引擎 |
| VideOCR / videocr-PaddleOCR / Extract-Subtitles-by-OCR 可参考 | ⚠️ 未逐一核实 | 作为参考线索保留；本项目的跳帧+投票架构已覆盖它们的大部分设计 |
| 建议"实现智能跳帧" | ℹ️ 已实现 | 见 1.3；可做的是降采样加速 SSIM |
| 建议"结果去重与合并"、"多帧融合" | ℹ️ 已实现 | 分组合并 + 首/中/尾投票 |
| 建议"引入 LLM 语义修正" | ℹ️ 已实现 | `subtitle_llm_polish` + UI 选项 |
| 建议"确保 GPU 加速" | ℹ️ 已实现 | 三级设备探测 |
| 建议"动态字幕区域检测（自动定位 ROI）" | ✅ 确实缺失 | 有 video_type_detector / text_source_classifier 辅助，但字幕区域仍需手动框选，是有效的功能方向 |
| 建议预处理增强/降噪/超分 | ⚠️ 部分缺失 | 已有 ROI 预处理与颜色门控；对比度增强/锐化/超分未有，超分性价比需实测（低分辨率字幕才值得） |

---

## 三、模型升级路径

### 3.0 PaddleOCR 版本时间线（2025-08 → 2026-09）

| 版本 | 日期 | 关键内容 |
|---|---|---|
| 3.2.0 | 2025-08 | PP-OCRv5 英/日专用识别模型（英文场景 +11%）；**默认模型 mobile → server**；依赖拆分 |
| 3.3.0 | 2025-10 | PaddleOCR-VL（0.9B 文档解析 VLM）；PP-OCRv5 多语言扩至 109 种 |
| 3.4.0 / 3.4.1 | 2026-01 / 2026-04 | PaddleOCR-VL-1.5；llama.cpp 后端；AMD/Intel GPU 支持 |
| 3.5.0 | 2026-04 | HF Transformers 后端；PaddleOCR.js 浏览器端 |
| 3.6.0 | 2026-05 | PaddleOCR-VL-1.6 |
| **3.7.0** | **2026-06** | **PP-OCRv6**（当前最新大版本） |

### 3.1 首选方案：升级 PaddleOCR 3.7.0 + PP-OCRv6

**模型规格**（统一 PPLCNetV4 骨干 + LightSVTR 多头解码）：

| 档位 | 参数量 | 检测 Hmean | 定位 |
|---|---|---|---|
| Tiny | 1.5M | ~80.6% | 极致速度 / 低配机器 |
| Small | 7.7M | ~84.1% | 速度与精度平衡 |
| Medium | 34.5M | 86.2%（识别 83.2%） | 默认推荐，+4.6% 检测 / +5.1% 识别 vs PP-OCRv5_server |

**依赖与代码改动**：

1. `requirements.txt`：`paddlepaddle>=3.2.1,<3.3` + `paddleocr>=3.7.0,<4.0`（3.2.1 满足 PP-OCRv6 的最低框架要求，且不触发 3.3 的 oneDNN 回归；现有 `<4.0` 上限无需变动）。
2. `core/ocr_engine_paddle.py` 初始化处显式指定：

```python
self._ocr = PaddleOCR(
    ocr_version="PP-OCRv6",            # 3.7 起默认即是，显式写出更稳
    # 或精确选档：
    # text_detection_model_name="PP-OCRv6_medium_det",
    # text_recognition_model_name="PP-OCRv6_medium_rec",
    use_doc_orientation_classify=False,
    use_doc_unwarping=False,
    use_textline_orientation=False,
    lang=lang,
    device=device,
)
```

3. 兼容性评估：`predict` 接口不变；`normalize_result` 已处理 3.x dict 输出。升级风险主要在依赖树（paddle 3.2.1 + 新 paddleocr 的可选依赖拆分反而更轻）。
4. **语言注意**：PP-OCRv6 单模型覆盖中/英/日 + 46 种拉丁系语言；**韩语、俄语、阿拉伯语不在内**，需回落 PP-OCRv5 multilingual 识别模型——建议在 `initialize()` 中按 `lang` 自动做这个回落，同时把 `lang` 从 UI 传进来（当前恒为 "ch"）。
5. 同步更新：`preload_models.py`（加 `ocr_version`）、引擎信息 `version="3.7.0"`、`build_deb.sh` 相关安装脚本。
6. 模型档位建议在 UI 暴露（tiny/small/medium 下拉），配合现有"引擎选择"组合框。

### 3.2 RapidOCR 适配器迁移到新包 `rapidocr`

- 旧包 `rapidocr_onnxruntime` 已停更（止于 1.4.4，PP-OCRv4 模型）。
- 新包 `rapidocr`（当前 v3.9.2）：v3.0 集成 PP-OCRv5、**v3.9.0 集成 PP-OCRv6**；支持 onnxruntime / openvino / paddle / MNN / TensorRT 多后端。
- 迁移收益：无 PaddlePaddle 依赖的 CPU 用户直接获得 PP-OCRv6 级精度；openvino 后端 CPU 吞吐明显优于旧版。
- 改动点：`core/ocr_engine_rapid.py` 的 import（`from rapidocr import RapidOCR`）、构造参数（新版参数体系与 1.x 不兼容，需按 v3 文档调整）、`normalize_result` 适配新版返回结构；`requirements.txt` 增加可选依赖说明。

### 3.3 可选高精度引擎：HunyuanOCR（已核实）

- 腾讯 2025-11-25 开源，1B 参数端到端 OCR 专用 VLM，约 2GB 模型文件，100+ 语言，vLLM/transformers 部署，另有 HunyuanOCR-1.5。
- 已被用于视频字幕提取实践，整体准确率 >92% 的第三方评测优于 Gemini/Qwen/Seed 等通用大模型。
- **定位建议**：不适合逐帧（吞吐远低于 PP-OCRv6），但非常适合两种用法：
  1. **难帧兜底**：首/中/尾投票不一致或平均置信度低于阈值的序列，升级给 HunyuanOCR 精修一次（与现有投票机制天然组合）；
  2. 独立可选引擎（`BaseOCREngine` 新适配器，走 vLLM OpenAI 兼容接口，可复用 `llm_client.py` 的退避逻辑）。

### 3.4 PaddleOCR-VL 与 Qwen3-VL：定位澄清

- **PaddleOCR-VL-1.5/1.6**：文档解析模型（0.9B，基于 ERNIE-4.5-0.3B），**只接受图片/PDF**，视频支持在路线图中。可作为"难帧兜底"的另一候选（与 HunyuanOCR 二选一），但**不能**作为视频整输入引擎。
- **Qwen3-VL**：视频 OCR 能力强，适合"整段视频丢给大模型"的云端/重 GPU 方案，与本工具"本地、轻量、逐 ROI 精控"的定位差异大，仅建议作为远期可选引擎，不列入近期路线。

### 3.5 Unlimited-OCR 适配器处置

删除 `core/ocr_engine_unlimited.py`，或留待真正需要时按 vLLM 部署重写。当前它 `import unlimited_ocr` 永远失败，属于死代码，且引擎描述会误导用户。

---

## 四、代码层优化方向

### 4.1 性能（CPU 环境预估合计 2–5× 吞吐提升）

> **v2.5.0 实施后记（2026-09-12）**：P1/P3/P4/P5 已完成。P2 部分完成（投票采样
> 与精修 ground window 已走原生 `predict_batch`，并按设备分块：GPU 12 / CPU 6）。
> **"先选帧后批量推理"的两段式锚帧批量经评估不可行**：`process_roi_group` 的
> 锚帧 OCR 与二分探测的每一次调用都是决策关键（锚文本决定空读/相似序列走向，
> 每个 mid 的文本决定二分方向），重排会改变决策顺序、威胁精度红线——该前提
> 与算法的串行决策结构不兼容。实测边界（24min 视频，32 核）：批量推理在此
> 负载上是内存带宽受限而非调用开销受限（详见 testing.md v2.5.0）。
> **P7（检测/识别解耦）需 GPU/实测数据评估**：det 占比与 PP-OCRv6 档位组合
> 相关，无硬件环境无法实测，按 YAGNI 暂缓。GPU 批处理（设计文档 Phase 3
> 的主要收益来源）需要真实 GPU 验证后才能定批大小与收益。

| # | 优化点 | 位置 | 说明 |
|---|---|---|---|
| P1 | SSIM 降采样 | `ocr_optimizer.py:148-156` | 相似度判断在全分辨率灰度图上算 SSIM，是跳帧搜索热点（粗扫+二分每次都要算）。统一缩到 1/4 宽再算可提速一个数量级；粗筛甚至可换 dHash/pHash |
| P2 | 批量 predict | `ocr_engine_paddle.py:169-174`、`ocr_optimizer.py:177` | PaddleOCR 3.x `predict` 支持图片列表；首/中/尾三帧从三次串行调用改为一次批量提交；同时审视全局 `_predict_lock`（当前所有引擎单图+全局锁） |
| P3 | 恢复 MKLDNN | `ocr_engine_paddle.py:24-28` | paddle 锁定 3.2.1 后，去掉 `FLAGS_use_mkldnn=0` 等 workaround，改用 `PaddleOCR(enable_mkldnn=True, cpu_threads=N)`，CPU 推理约 2× |
| P4 | 每帧一个 JSON | `ocr_engine_manager.py:113-145` | 默认为每个 OCR 帧写一个 JSON，长视频数千小文件；改聚合单文件或默认关闭 |
| P5 | `_feature_cache` 无上限 | `ocr_optimizer.py:46` | 每帧灰度图永久缓存，长视频内存失控；加 LRU 上限或复用 time_slice 分桶清理 |
| P6 | ffmpeg 静态段预筛 | `core/ffmpeg_roi_segmenter.py`（已有雏形） | 用 ffmpeg `freezedetect` 滤镜在解码层标出静态段，减少进入 SSIM 阶段的帧数 |
| P7 | 检测/识别解耦 | `ocr_optimizer.py` | 字幕基本为水平单行：仅关键帧跑检测，同序列复用检测框只跑识别（识别远比检测便宜）；可配 PP-OCRv6 det 小档 + rec 中档 |

### 4.2 质量

| # | 优化点 | 位置 | 说明 |
|---|---|---|---|
| Q1 | lang 从 UI 传入引擎 | `control_panel.py` → `pipeline_worker` → engine | 当前恒 "ch"，日语视频用中文模型识别，是现成的精度损失；配合 3.1 节的 PP-OCRv6 语言回落逻辑 |
| Q2 | 二分搜索文本容错 | `ocr_optimizer.py:158-165` | `_are_frames_similar` 用文本完全相等，单字抖动即提前终止序列；改 Levenshtein 比率（依赖已装） |
| Q3 | 难帧 VLM 兜底 | 新增引擎适配器 | 见 3.3；投票不一致/低置信度时升级 HunyuanOCR 或 PaddleOCR-VL |
| Q4 | 预处理增强 | `roi_extractor.py` | 现有预处理上增加可选对比度增强/锐化；超分仅在低分辨率片源实测有效再引入 |
| Q5 | 自动字幕区域定位 | 新模块 | 唯一"大功能"级缺失：采样帧跑检测聚类出候选字幕带，一键生成 ROI 供用户微调（保留手动兜底）。`video_type_detector`/`text_source_classifier` 的特征提取可复用 |

### 4.3 工程

| # | 优化点 | 位置 | 说明 |
|---|---|---|---|
| E1 | 补测试 | 全仓库 | 引擎层 mock 单测（`normalize_result` 多格式）、优化器跳帧/投票逻辑纯函数测试——模型升级的回归保障 |
| E2 | 清理 requirements-a.txt | 快照同时锁 `opencv-python` 与 `opencv-contrib-python` | README 自己警告过的 ABI 冲突组合 |
| E3 | LLM 退避总时长上限 | `llm_client.py:77-81` | 现 10 次 × 最长 60s，最坏总等待约 600s；按规范补 `stop_after_delay` 总上限后优雅降级 |
| E4 | 删除 Unlimited-OCR 死代码 | `core/ocr_engine_unlimited.py` | 见 3.5 |

---

## 五、实施路线图

| 阶段 | 内容 | 工作量 | 收益/风险 |
|---|---|---|---|
| **Phase 1** | 依赖升级 + PP-OCRv6（3.1 节）+ preload/脚本同步 | 0.5–1 天 | 精度 +5% 级、风险低、改动集中 |
| **Phase 2** | 性能三件套：SSIM 降采样、批量 predict、MKLDNN（P1–P3） | 1–2 天 | CPU 吞吐 2–5×；用同视频对比 ocr_calls 与总耗时验证 |
| **Phase 3** | lang UI 传递 + 语言回落、Levenshtein 容错（Q1–Q2）、RapidOCR 新包迁移（3.2） | 1–2 天 | 日/英字幕精度明显改善 |
| **Phase 4** | 测试补齐（E1）、缓存治理（P5）、JSON 聚合（P4）、LLM 总上限（E3）、死代码清理（E4） | 2–3 天 | 稳定性/可维护性 |
| **远期** | 难帧 VLM 兜底（Q3）、自动字幕定位（Q5）、检测/识别解耦（P7）、ffmpeg 预筛（P6） | 按需 | 差异化能力 |

每阶段完成后建议固定 2–3 个测试视频（中/日/英 + 特效字幕各一）做回归对比：识别准确率（人工抽检）、`ocr_calls` 次数、端到端耗时。

---

## 六、参考链接

- PaddleOCR Releases：https://github.com/PaddlePaddle/PaddleOCR/releases
- PaddleOCR 仓库：https://github.com/PaddlePaddle/PaddleOCR
- PP-OCRv5 技术报告：https://arxiv.org/html/2507.05595v1
- PaddleOCR-VL 使用教程（确认仅图片/PDF 输入）：https://github.com/PaddlePaddle/PaddleOCR/blob/main/docs/version3.x/pipeline_usage/PaddleOCR-VL.md
- PaddleOCR-VL-1.5 / 1.6：https://huggingface.co/PaddlePaddle/PaddleOCR-VL-1.5 、https://huggingface.co/PaddlePaddle/PaddleOCR-VL-1.6
- HunyuanOCR 官网：https://vision.hunyuan.tencent.com/ ；GitHub：https://github.com/Tencent-Hunyuan/HunyuanOCR
- Unlimited-OCR（vLLM 部署的 3.3B 模型）：https://github.com/baidu/Unlimited-OCR
- RapidOCR 文档与模型列表：https://rapidai.github.io/RapidOCRDocs/main/model_list/
- Paddle2ONNX / paddleocr_convert：https://github.com/PaddlePaddle/Paddle2ONNX 、https://github.com/RapidAI/PaddleOCRModelConvert
