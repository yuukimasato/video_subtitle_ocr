# video_subtitle_ocr 优化开发方案与开发计划

> 文档版本：v1.1（2026-09-08）
> 前置文档：[optimization_analysis.md](./optimization_analysis.md)（现状分析与事实核查）
> 人力假设：1 名开发人员全职；总周期约 3 周（15 个工作日，含缓冲）

## ✅ 执行状态（2026-09-08 第二轮更新）

Phase 1–4 与全部遗留项已实施完成并通过验证（多子智能体协同 + 人工集成审查）：

- **已完成（第一轮）**：依赖升级（paddlepaddle 3.2.1 + paddleocr 3.7.0）；PP-OCRv6 引擎接入（三级降级链）；引擎选项链路（语言/档位从 UI 贯通引擎）；语言回落；MKLDNN 恢复；`predict_batch` 批量投票；SSIM 降采样；LRU 缓存；Levenshtein 容错；LLM 退避总上限；RapidOCR 迁移 rapidocr 3.9.2；删除 unlimited 死代码；单元测试；基准脚本 + 测试视频生成器。
- **已完成（第二轮，遗留项清理）**：
  - 语言配置持久化到 ROI 配置 JSON（新格式 `{"ocr_lang":…, "rois":[…]}`，旧列表格式向后兼容）；
  - **自动字幕区域定位**（`core/subtitle_roi_suggester.py` + 主窗口"自动检测字幕ROI"按钮：采样全画幅 OCR → 底部文本聚类 → 生成候选 ROI，追加/替换三选对话框保护用户数据）；
  - **VLM 难帧兜底**（`core/vlm_refine.py`：任何 OpenAI 兼容视觉端点可用，含 vLLM 部署的 HunyuanOCR；环境变量 `VLM_REFINE_BASE_URL/API_KEY/MODEL` 配置，投票不过半或置信度 <0.85 时触发，失败静默保留原结果）；
  - i18n：35 条新增文案补齐中/日翻译并重编译 `.qm`；
  - 修复外层 `generate_test_video.sh` 的 bash 5.2 引号解析 bug；
  - 引擎支持显式 `ocr_version` 覆盖（基准脚本 `--ocr-version`）；
  - **修复 RapidOCR 批量路径归一化缺陷**（扁平三元组被二次包装导致批量结果清空；基类新增 `normalize_batch_result`，RapidOCR 覆写，优化器带回退）+ 回归测试。
- **基准对比**（15s 测试视频 / CPU / 底部 ROI / 450 帧）：

| 配置 | 行准确率 | ocr_calls | OCR 阶段 | 总耗时 |
|---|---|---|---|---|
| 旧代码 + paddleocr 3.1（升级前基线，git worktree 实测） | 75%（3/4） | 117 | 61.5s | **62.4s** |
| 新代码 + PP-OCRv5（--ocr-version） | 100%（4/4） | 123 | 14.2s | 14.4s |
| **新代码 + PP-OCRv6 medium（默认）** | **100%（4/4）** | 123 | **9.1s** | **9.2s** |
| 新代码 + RapidOCR v3.9（ONNX） | 75%（3/4，det 分片致匹配差异） | 113 | 87.0s | 87.2s |

  端到端相对升级前基线：**提速约 6.8×，准确率 75% → 100%**。测试视频为清晰合成字幕，真实片源的精度差距预计更大（v5 列为升级前默认 mobile 档；v6 medium 检测 +4.6%/识别 +5.1% 为官方对 v5_server 的数据）。旧基线环境保留在 `/tmp/vso_old_baseline`（git worktree）+ `/tmp/vso_old_venv`，可复现。
- **测试**：85 passed + 1 skipped（真实 OCR 集成测试需 `VIDEO_SUBTITLE_OCR_REAL_TEST=1`）。
- **仍开放**：RapidOCR 的 ONNX Runtime CPU 单线程吞吐偏低（0.77s/帧），可调 `intra_op` 线程数；ASS 多行文本的换行标签规范化（`\n` → `\N`）；HunyuanOCR 实机联调（需部署 vLLM 服务）。

---

---

## 1. 项目概述

### 1.1 背景与目标

基于优化分析文档的结论，本轮开发围绕四条主线：

| 主线 | 目标（可验收） |
|---|---|
| 模型升级 | PaddleOCR 升级至 3.7.0、模型切换为 PP-OCRv6；在固定测试集上字幕行识别准确率相对基线 **≥ +4%** |
| 性能优化 | 相同测试视频、相同输出前提下，端到端耗时 **≤ 基线的 50%**（CPU 环境），`ocr_calls` 不增加 |
| 质量与功能 | 语言参数从 UI 贯通引擎；日语/英语字幕准确率明显改善；RapidOCR 备用引擎升级到 PP-OCRv6 级模型 |
| 工程治理 | 单元测试覆盖引擎层与优化器核心逻辑；LLM 退避增加总时长上限；清除死代码与依赖冲突 |

### 1.2 非目标（本轮不做）

- 不引入 HunyuanOCR / PaddleOCR-VL 难帧兜底（列入 Backlog，见第 7 节）
- 不做全自动字幕区域定位（Backlog）
- 不做检测/识别解耦与 ffmpeg freezedetect 预筛（Backlog）
- 不改动 ASS 生成、LLM 润色的业务逻辑（仅加退避上限）

---

## 2. 总体技术方案

### 2.1 依赖与版本策略

| 文件 | 变更 |
|---|---|
| `requirements.txt` | `paddlepaddle>=3.2.1,<3.3`、`paddleocr>=3.7.0,<4.0`；新增可选注释 `rapidocr>=3.9,<4.0`（不强制安装） |
| `requirements-gpu.txt` | `paddlepaddle-gpu>=3.2.1,<3.3`、`paddleocr>=3.7.0,<4.0` |
| `requirements-a.txt` | 升级完成后重新生成环境快照，**同时清除 opencv-python 与 opencv-contrib-python 并存问题** |

版本依据：PP-OCRv6 要求 paddlepaddle ≥ 3.2.1；3.2.x 不触发 3.3 的 oneDNN/new-executor/PIR 回归（当前锁定策略保持 `<3.3` 不变）。

### 2.2 引擎选项传递链路改造（核心架构改动）

**现状问题**：UI 到引擎只传递一个 `ocr_engine_id` 字符串（`control_panel.get_options()` → `main_window` → `PipelineWorker(ocr_engine_id=...)` → `OcrOptimizer` → `set_engine(engine_id)`），而 `ocr_engine_manager.get_engine()` 调用 `initialize()` **不带任何参数**——语言、模型档位、MKLDNN 开关等根本无法到达引擎，这是当前 `lang` 恒为 "ch" 的根因。

**改造设计**：引入 `engine_options: Dict[str, Any]` 贯穿全链路。

```
control_panel（新增 语言/模型档位 控件）
  → get_options() 增加 "ocr_lang"、"ocr_model_tier"
  → main_window → PipelineWorker(ocr_engine_id, engine_options)
  → OcrOptimizer(ocr_engine_id, engine_options)
  → ocr_engine_manager.set_engine(engine_id, options=engine_options)
      # manager 保存 options；get_engine() 时 initialize(**_engine_options)
  → PaddleOCREngine.initialize(lang=..., model_tier=..., device=...)
```

接口变更明细：

| 位置 | 变更 |
|---|---|
| `core/ocr_engine_manager.py` `set_engine()` | 签名改为 `set_engine(engine_id: str, options: Optional[Dict] = None)`，新增模块级 `_engine_options` |
| `core/ocr_engine_manager.py` `get_engine()` | `initialize()` 改为 `initialize(**_engine_options)` |
| `core/ocr_engine_base.py` `OCREngineInfo` | 新增 `supported_model_tiers: List[str] = []` 字段，供 UI 渲染档位下拉 |
| `core/ocr_engine_paddle.py` | `initialize()` 读取 `lang` / `model_tier` / `enable_mkldnn` / `cpu_threads`；`get_engine_info()` 声明 `["tiny", "small", "medium"]` |
| `core/ocr_optimizer.py` `_run_single_ocr()` | `set_engine(self.ocr_engine_id)` 改为传入 `self.engine_options` |
| `core/pipeline_worker.py` | 构造函数新增 `engine_options` 参数并透传（4 处 OcrOptimizer 实例化点：常规/流式/合并 ROI 路径） |
| `components/control_panel.py` | 引擎分组（`engine_group`）内新增"语言"与"模型档位"两个 QComboBox |
| `main_window/`（pipeline_control） | 收集 options 时透传新字段 |

### 2.3 PaddleOCR 引擎升级（PP-OCRv6）

**初始化与语言回落**（`core/ocr_engine_paddle.py`）：

```python
def initialize(self, **kwargs) -> None:
    from paddleocr import PaddleOCR
    device = kwargs.get("device") or self._get_device_mode()
    lang = kwargs.get("lang", "ch")
    tier = kwargs.get("model_tier", "medium")   # tiny / small / medium

    # PP-OCRv6 统一模型覆盖：中/英/日 + 46 种拉丁系语言
    V6_COVERED = {"ch", "en", "japan"} | LATIN_LANGS
    if lang in V6_COVERED:
        self._ocr = PaddleOCR(
            text_detection_model_name=f"PP-OCRv6_{tier}_det",
            text_recognition_model_name=f"PP-OCRv6_{tier}_rec",
            use_doc_orientation_classify=False,
            use_doc_unwarping=False,
            use_textline_orientation=False,
            enable_mkldnn=kwargs.get("enable_mkldnn", True),   # 见 2.3.2
            cpu_threads=kwargs.get("cpu_threads", 0) or None,
            lang=lang, device=device,
        )
    else:
        # 韩语/俄语/阿拉伯语等：回落 PP-OCRv5 多语言识别模型
        self._ocr = PaddleOCR(
            ocr_version="PP-OCRv5",
            text_detection_model_name="PP-OCRv5_server_det",
            text_recognition_model_name=V5_MULTILINGUAL_REC.get(lang, ...),
            ...同上开关...,
            lang=lang, device=device,
        )
```

> 说明：具体模型名（`PP-OCRv6_{tier}_det/rec`、各语言 multilingual rec 名）在实现时以 paddleocr 3.7 安装后 `paddleocr` 包内模型列表为准，此处的命名模式来自官方文档，需在 T1.2 任务中核对。

**2.3.2 MKLDNN 恢复**：删除 `ocr_engine_paddle.py:24-28` 的五个 `FLAGS_*` 环境变量 workaround（其存在意义是规避 paddle 3.3 回归，锁定 3.2.1 后不再需要），改为构造参数 `enable_mkldnn=True`。**验收门禁**：开启后连续跑完 3 个测试视频无崩溃/挂死，否则回退为参数可选项（默认关）并在 UI 暴露。

**2.3.3 批量 predict**：

- `core/ocr_engine_base.py` 新增默认方法 `predict_batch(images: List) -> List`（默认实现：循环调 `predict`，保证 RapidOCR 等引擎不改也能用）。
- `PaddleOCREngine.predict_batch()` 直接把 list 传给 `self._ocr.predict()`（3.x 原生支持），一次锁一批。
- `core/ocr_optimizer.py` `_get_best_ocr_result_from_sequence()`：把首/中/尾三帧的 3 次 `_run_single_ocr` 改为一次 `predict_batch` + 逐帧 `normalize_result`。注意保持与 `run_batch_ocr` 的 JSON 落盘语义兼容（批量路径仅在 `save_json=False` 或聚合模式下使用，见 2.5.3）。

### 2.4 RapidOCR 适配器迁移

- import 由 `rapidocr_onnxruntime` 改为 `rapidocr`（v3.9.x）。
- 构造参数按 v3 体系重写（默认 openvino 后端不可用时回落 onnxruntime），`text_score` 阈值语义保持。
- `normalize_result()` 按 v3 返回结构适配（实现时以实际输出为准写适配 + 单测锁定）。
- `requirements.txt` 中 rapidocr 保持**可选**依赖（`is_available()` 已能优雅降级，无需强制）。
- `preload_models.py` 的 `preload_rapidocr()` 同步迁移。

### 2.5 性能优化设计

**2.5.1 SSIM 降采样**（`core/ocr_optimizer.py`）

- `_get_grayscale_image()` 中灰度化后统一 `cv2.resize` 到目标宽度（默认 320px，`INTER_AREA`），再进 `_feature_cache`。
- 新增构造参数 `similarity_target_width: int = 320`；0 表示禁用（保持原行为，便于对照测试）。
- 连带影响：`_motion_sentinel_skip` 使用同一灰度图，`motion_change_ratio_threshold` 语义变为降采样后的比例，需在标定任务（T2.4）中用测试视频重标定默认值。
- 验收：三个测试视频上 `ocr_calls` 与基线一致（±2%）、最终 ASS 输出与基线 diff 为空或仅时间戳毫秒级差异。

**2.5.2 特征缓存治理**

- `_feature_cache` / `_image_cache` 增加 LRU 上限（`functools.lru_cache` 不可直接用于 ndarray 参数，用 `OrderedDict` 手写或 `cachetools.LRUCache`，不引新依赖则手写）。
- 默认上限：`_feature_cache` 2048 条、`_image_cache` 256 条；流式（time_slice）模式下沿用分桶清理路径。
- 验收：90 分钟视频全流程内存峰值 < 4GB（记录基线对比）。

**2.5.3 JSON 聚合**

- `core/ocr_engine_manager.py` `run_batch_ocr()`：非 debug 模式改为追加写单个 `ocr_results.jsonl`（每行一帧的结果）；debug 模式保留逐帧 JSON 便于排查。
- 注意核对 `save_ocr_json` 与 worker `save_intermediate_json`（默认仅 debug 保留）的现有联动，避免重复落盘。

### 2.6 质量优化设计

**2.6.1 语言贯通**：UI 语言列表（`ch / en / japan / korean / russian / arabic / french / german / ...`）→ `engine_options["ocr_lang"]` → 引擎按 2.3 的回落逻辑选型。配置持久化到 ROI 配置 JSON（`main_window` 保存配置处增加字段，向后兼容：缺省 "ch"）。

**2.6.2 文本相似容错**（`ocr_optimizer.py:158-165`）：

```python
def _are_frames_similar(self, result1, result2) -> bool:
    text1 = "".join(result1.get('rec_texts', [])).strip()
    text2 = "".join(result2.get('rec_texts', [])).strip()
    if not text1 or not text2:
        return False
    if text1 == text2:
        return True
    # 单字抖动容错：编辑距离比率 >= 0.9 视为相同
    import Levenshtein
    max_len = max(len(text1), len(text2))
    return (1.0 - Levenshtein.distance(text1, text2) / max_len) >= 0.9
```

阈值 0.9 作为默认值并在标定任务中复核。验收：构造 OCR 抖动用例（同句一字之差）不再提前切断序列。

**2.6.3 LLM 退避总时长上限**（`core/llm_client.py:77-81`）：

```python
@retry(
    stop=stop_after_attempt(10) | stop_after_delay(300),   # 新增总时长上限 5 分钟
    wait=wait_random_exponential(multiplier=1, min=5, max=60),
    retry=retry_if_exception_type(openai.RateLimitError),
)
```

耗尽后维持现有异常路径；`subtitle_llm_polish` 调用处确认已有"润色失败→使用未润色字幕继续"的降级（无则补 try/except，验证任务 T4.3）。

### 2.7 测试方案

**2.7.1 单元测试**（新增 `tests/`，pytest，CI 可本地跑）：

| 测试文件 | 覆盖 |
|---|---|
| `tests/test_normalize_paddle.py` | `normalize_result` 两种输入格式（3.x dict / 旧版行格式）→ 统一结构；空结果；畸形行 |
| `tests/test_normalize_rapid.py` | RapidOCR v3 输出结构适配（用录制样例锁定） |
| `tests/test_ocr_optimizer.py` | 合成帧序列 + monkeypatch 引擎：序列分组边界、首中尾投票选多数文本、motion sentinel 跳过、取消信号提前退出、LRU 淘汰 |
| `tests/test_similarity.py` | 降采样 SSIM 与全分辨率 SSIM 对相同/不同帧对的判定一致性；`_are_frames_similar` 容错阈值边界 |
| `tests/test_engine_manager.py` | `set_engine(id, options)` → `initialize(**options)` 参数贯通；引擎不可用时降级 |

**2.7.2 回归基准**（新增 `scripts/benchmark_regression.py`）：

- 输入：3 个固定测试视频（用仓库外层 `generate_test_video.sh` 生成中/英/日各一 + 1 个特效字幕样例，字幕 ground-truth 已知）。
- 指标：字幕行准确率（与 ground-truth 逐行比对，Levenshtein 相似度 ≥0.95 记为正确）、`ocr_calls`、各阶段耗时、内存峰值。
- 输出：JSON 报告存 `benchmarks/`，含基线快照，供每次 Phase 验收对比。
- **基线必须在任何代码改动前采集**（任务 T0）。

### 2.8 打包与文档同步

- `build_deb.sh`：依赖检查逻辑已兼容（grep `paddlepaddle` / `rapidocr`），无需大改；验证 deb 构建通过。
- `preload_models.py`：按 2.3 的模型名/语言回落逻辑同步预载。
- `README.md`：技术栈、安装说明、项目结构（README 中的 `scripts/` 目录实际不存在，一并修正）、新 UI 参数说明。
- i18n：新增控件文案补入 `app_zh_CN.ts` / `app_ja_JP.ts` 并重编译 `.qm`。

---

## 3. 任务分解（WBS）

### Phase 0：基线与准备（0.5 天）

| 编号 | 任务 | 产出 |
|---|---|---|
| T0.1 | 搭建/确认 venv，安装当前锁定版本 | 可运行基线环境 |
| T0.2 | 生成 3+1 测试视频，人工核对 ground-truth | `benchmarks/videos/` |
| T0.3 | 编写并运行 `benchmark_regression.py` 采集基线 | `benchmarks/baseline.json` |
| T0.4 | 核对本机 Python 版本与 paddleocr 3.7 兼容性（官方推荐 3.8–3.10，3.11+ 需实测） | 兼容性结论记录 |

### Phase 1：模型升级（1.5 天）→ 里程碑 M1

| 编号 | 任务 | 涉及文件 | 验收标准 |
|---|---|---|---|
| T1.1 | 依赖升级（CPU/GPU 两份 requirements） | `requirements*.txt` | 全新 venv 安装成功、`import paddleocr` 版本 3.7.x |
| T1.2 | 核对并锁定 PP-OCRv6 模型名（各档位 + v5 multilingual 回落表） | — | 模型名清单写入引擎代码注释 |
| T1.3 | 引擎选项链路改造（2.2 全部接口） | `ocr_engine_manager/base/paddle.py`、`pipeline_worker.py`、`control_panel.py`、`main_window/` | UI 选语言/档位后日志输出对应模型；默认行为不回退 |
| T1.4 | PP-OCRv6 初始化 + 语言回落逻辑 | `ocr_engine_paddle.py` | 中/英/日走 v6，韩/俄/阿走 v5 回落，各跑通一个短视频 |
| T1.5 | `preload_models.py` 与打包脚本同步 | `preload_models.py`、`build_deb.sh` | 预载脚本跑通；deb 构建通过 |
| T1.6 | Phase 1 回归：跑基准对比基线 | `benchmarks/` | 行准确率 ≥ 基线 +4%，耗时劣化 < 10%（性能属 Phase 2） |

### Phase 2：性能优化（2.5 天）→ 里程碑 M2

| 编号 | 任务 | 涉及文件 | 验收标准 |
|---|---|---|---|
| T2.1 | `predict_batch` 基础设施 + 投票批量化 | `ocr_engine_base.py`、`ocr_engine_paddle.py`、`ocr_optimizer.py` | 投票序列 3 次调用合并为 1 次；输出与逐帧调用一致 |
| T2.2 | 删除 FLAGS workaround，启用 MKLDNN + `cpu_threads` | `ocr_engine_paddle.py` | 3 视频连跑无崩溃；CPU 单帧推理耗时下降（记录数值） |
| T2.3 | SSIM 降采样（含开关参数） | `ocr_optimizer.py` | 见 2.5.1 验收 |
| T2.4 | 阈值标定：`motion_change_ratio_threshold` 在降采样图上重标定 | `ocr_optimizer.py`、标定脚本 | 标定记录 + 默认值更新 |
| T2.5 | 缓存 LRU 上限 | `ocr_optimizer.py` | 长视频内存峰值达标（2.5.2） |
| T2.6 | JSON 聚合（jsonl） | `ocr_engine_manager.py` | 非 debug 模式不再产生逐帧 JSON；debug 模式不变 |
| T2.7 | Phase 2 回归 | `benchmarks/` | 端到端耗时 ≤ 基线 50%，`ocr_calls` 不增，ASS 输出等价 |

### Phase 3：质量与备用引擎（2.5 天）→ 里程碑 M3

| 编号 | 任务 | 涉及文件 | 验收标准 |
|---|---|---|---|
| T3.1 | 语言配置持久化到 ROI JSON（向后兼容） | `main_window/`（roi_config_io）、`file_operations.py` | 旧配置文件可加载，缺省 lang="ch" |
| T3.2 | Levenshtein 文本容错 | `ocr_optimizer.py` | 抖动用例不再切断序列（单测 + 视频） |
| T3.3 | RapidOCR 迁移到 `rapidocr` v3.9 | `ocr_engine_rapid.py`、`preload_models.py` | 引擎切换后跑通测试视频；单测锁定输出适配 |
| T3.4 | 日/英/韩测试视频专项回归 | `benchmarks/` | 各语言行准确率对比基线提升（日语预期最明显） |
| T3.5 | i18n 文案补齐与 `.qm` 重编译 | `i18n/` | 中/日界面无缺译 |

### Phase 4：工程治理与发布（2 天）→ 里程碑 M4

| 编号 | 任务 | 涉及文件 | 验收标准 |
|---|---|---|---|
| T4.1 | 补齐 2.7.1 全部单元测试 | `tests/` | `pytest` 全绿；覆盖引擎层与优化器核心分支 |
| T4.2 | LLM 退避总上限 + 降级路径确认 | `llm_client.py`、`subtitle_llm_polish.py` | 模拟 429 场景总等待 ≤ 300s 后优雅降级 |
| T4.3 | 删除 `ocr_engine_unlimited.py` 死代码及注册点 | `core/`、引擎注册处 | UI 引擎列表不再出现该项 |
| T4.4 | 重新生成 `requirements-a.txt`（消除 opencv 并存） | `requirements-a.txt` | 快照内只有一个 opencv 包 |
| T4.5 | README 修正与文档更新（含开发文档链接） | `README.md`、`docs/` | 结构描述与实际一致 |
| T4.6 | 全量回归 + 版本发布（tag v1.1.0） | — | 四项总目标（1.1 节）全部达标 |

缓冲：+2 天（依赖安装问题、模型名核对偏差、MKLDNN 兼容性回退等）。

---

## 4. 开发计划（进度表）

单人全职，按工作日排布：

```
工作日  1    2    3    4    5    6    7    8    9    10   11   12   13   14   15
        [P0──][P1──────────][P2────────────][P3────────────][P4───────][缓冲──]
里程碑       M1▲(D3)        M2▲(D6)         M3▲(D9)        M4▲(D12)   发布 D13-15
```

| 里程碑 | 时点 | 交付物 | 达成标志 |
|---|---|---|---|
| M0 | D1 | 基线环境、测试视频、`baseline.json` | 基准脚本可重复运行 |
| M1 | D3 | PP-OCRv6 升级版（含引擎选项链路） | 准确率 ≥ 基线 +4% |
| M2 | D6 | 性能优化版 | 端到端耗时 ≤ 基线 50% |
| M3 | D9 | 多语言 + RapidOCR v3 版 | 各语言回归达标 |
| M4 | D12 | 测试齐备、治理完成 | pytest 全绿、总目标达标 |
| 发布 | D13–15 | tag v1.1.0、deb 包、更新日志 | 全量回归通过 |

每日流程约定：开工先跑 `pytest`；每个任务完成即提交（commit 粒度 = 任务粒度）；每个 Phase 结束跑一次完整基准并归档报告。

---

## 5. 风险与应对

| 风险 | 概率 | 影响 | 应对 |
|---|---|---|---|
| paddleocr 3.7 依赖树与现有环境冲突（paddlex 版本、可选依赖变动） | 中 | 高 | T1.1 在全新 venv 验证；失败则逐项排除可选依赖；3.2.0 起官方已拆分必要/可选依赖，冲突面变小 |
| PP-OCRv6 模型名/参数与文档不符 | 中 | 低 | T1.2 安装后直接从包内模型清单核对，不盲信文档 |
| Python ≥3.11 兼容性未知 | 中 | 中 | T0.4 实测；不兼容则随包提供 Python 版本约束说明 |
| MKLDNN 开启后崩溃/挂死（历史回归在 3.3，3.2.1 理论无碍） | 低 | 中 | 门禁式验收（2.3.2），异常即改为默认关 + UI 开关 |
| 降采样后 SSIM 判定变化导致序列切分漂移 | 中 | 中 | 保留原行为开关；验收要求 ASS 输出等价；阈值标定任务兜底 |
| RapidOCR v3 参数/输出结构与预期不符 | 中 | 低 | 单测录制真实输出锁定；迁移失败可保留旧适配器并降级为"旧版可选" |
| PP-OCRv6 模型下载慢/失败（网络） | 中 | 低 | `preload_models.py` 支持镜像源环境变量；文档给出手动下载路径 |
| 性能目标（≤50% 耗时）未达标 | 低 | 中 | P1–P3 三项独立生效，逐项测量；单项不达标不阻塞其他项，按实测值修订目标 |
| 回滚需求 | 低 | 中 | 每个 Phase 独立分支合入；依赖变更集中在 requirements，回滚 = 还原 requirements + 代码 tag |

---

## 6. 验收标准总表（Definition of Done）

1. 全新环境按 README 安装后可直接运行，UI 可选择语言与模型档位，日志显示所选模型。
2. 基准报告：行准确率 ≥ 基线 +4%；CPU 端到端耗时 ≤ 基线 50%；`ocr_calls` 不增加；90 分钟视频内存峰值 < 4GB。
3. 中/英/日/韩/俄至少各一个视频跑通，语言回落逻辑按预期触发。
4. `pytest` 全绿；`scripts/benchmark_regression.py` 可重复执行并输出对比报告。
5. 模拟 429：LLM 润色总等待 ≤ 300s 后降级为未润色字幕，任务链不退出。
6. deb 构建通过；README 与实际结构一致；i18n 无缺译。
7. 发布 tag v1.1.0 及更新日志。

---

## 7. Backlog（本轮之后）

| 项 | 说明 | 前置 |
|---|---|---|
| 难帧 VLM 兜底 | 投票不一致/低置信度序列升级 HunyuanOCR（vLLM OpenAI 兼容接口，复用 `llm_client` 退避） | 需 GPU 或 API 服务 |
| 自动字幕区域定位 | 采样帧检测聚类出候选字幕带 → 一键生成 ROI 供微调 | 复用 `text_source_classifier` 特征 |
| 检测/识别解耦 | 关键帧检测 + 序列内框复用仅跑识别 | Phase 2 批量化基础设施 |
| ffmpeg freezedetect 预筛 | `ffmpeg_roi_segmenter.py` 雏形产品化 | — |
| 预处理增强/超分 | 对比度增强、锐化、可选超分，按片源实测引入 | — |
| 流水线基准自动化 CI | benchmark 脚本接入 CI 定期跑 | Phase 4 |
