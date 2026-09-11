# 视频字幕OCR工具 (Video Subtitle OCR Tool)

## 目录

- [项目简介](#项目简介)
- [核心功能](#核心功能)
- [技术栈](#技术栈)
- [项目结构](#项目结构)
- [核心工作流详解](#核心工作流详解)
- [安装与运行](#安装与运行)
- [DEB 包安装与打包](#deb-包安装与打包)
- [命令行工具 (CLI)](#命令行工具-cli)
- [测试与回归基准](#测试与回归基准)
- [使用指南](#使用指南)
- [核心模块代码解析](#核心模块代码解析)
  - [1. `main_window/` 包 - UI与逻辑总控](#1-main_window-包---ui与逻辑总控)
  - [2. `core/pipeline_worker.py` - 异步处理流水线](#2-corepipeline_workerpy---异步处理流水线)
  - [3. `core/ocr_optimizer.py` - 智能OCR优化器](#3-coreocr_optimizerpy---智能ocr优化器)
  - [4. `core/subtitle_generator/` 包 - 高级ASS字幕生成器](#4-coresubtitle_generator-包---高级ass字幕生成器)
- [未来可拓展方向](#未来可拓展方向)

## 项目简介

本项目是一个基于 PySide6 (Qt for Python) 和 PaddleOCR 开发的桌面应用程序，旨在帮助用户从视频文件中提取硬字幕（即内嵌在视频画面中的字幕），并将其转换为标准的 `.ass` 格式字幕文件。


![preview1](https://github.com/yuukimasato/video_subtitle_ocr/blob/main/resources/preview1.png)
![preview2](https://github.com/yuukimasato/video_subtitle_ocr/blob/main/resources/preview2.png)
软件提供了交互式的图形用户界面（GUI），用户可以直观地在视频上框选字幕区域（ROI），设置其生效的时间范围，并通过一键式操作启动后台OCR识别和字幕生成流程。项目内置了智能优化算法，能够大幅提升处理效率并保证识别的准确性。

## 核心功能

- **交互式GUI界面**：基于 PySide6 构建，提供视频预览、时间轴控制、参数设置等功能，操作直观。
- **灵活的ROI定义**：
  ROI(Region of Interest)指视频中需要特别关注的区域，本系统支持：
  - 支持 **矩形** (矩形抓取区域)和 **多边形**(多边形抓取区域) 两种方式绘制字幕区域，适应各种不规则形状的字幕。
  - **画布直接编辑**：绘制模式切换到「编辑（拖动调整）」后，可直接在画面上调整已有 ROI——拖动整体移动、矩形拖 8 个控制点缩放、多边形拖顶点改形；自动检测的 ROI 不准时可手工微调。
  - 支持定义多个ROI，每个ROI可以有独立的生效时间段，轻松应对多位置、多类型的字幕。
  - **增强的帧导航**：通过短按按钮实现单帧步进，长按实现连续帧导航，精确调整ROI时间范围。
  - **ROI复制/粘贴**：支持ROI的复制、粘贴到指定位置或列表末尾，方便快速复用已定义的区域。
- **智能OCR优化**：
  - **跳帧处理**：通过 `skimage.metrics.ssim` 比较帧间图像的结构相似性，自动跳过内容未发生变化的冗余帧，极大缩短OCR处理时间。
  - **投票机制**：对内容相似的连续帧序列进行多点采样（首、中、尾），对OCR结果进行投票，选出最可信的文本，有效提高识别准确率，对抗单帧识别不稳定的问题。
- **自动字幕定位与全片扫描**：
  - **加载即检测**：加载视频后自动在后台采样扫描全片（顶部+底部带），自动生成字幕带 ROI，无需手动绘制；可在高级选项中关闭。
  - **倾斜字幕带识别为多边形**：利用 OCR 返回的文本行四边形，检测到字幕带明显倾斜（长边偏角 > 4°）时自动输出 4 点旋转矩形 ROI（而非外扩的轴对齐矩形），贴合并避免引入大量背景；轴对齐字幕带仍输出矩形。
  - **深度扫描复核**：「深度扫描（水印/场景字）…」以更多采样统计全片文字：字幕带 / 疑似水印（恒定文本+恒定位置）/ 场景文字三类分栏呈现（附快照缩略图），逐项勾选后应用。
  - **水印剔除**：复核确认的水印清单在生成阶段按文本/位置匹配剔除，独立于场景过滤开关。
- **按 ROI 的文字过滤策略**：
  - **手动 ROI 永不过滤**：手动绘制（矩形/多边形/全宽带）的 ROI 标记为「全部保留」，场景字等识别结果全部保留——这是带标签 ASS（`\pos`/`\frz`）生产的保障。
  - **自动字幕带可过滤**：自动检测的字幕带 ROI 标记为「自动过滤」，配合场景预设进行语义过滤；ROI 列表右键可随时切换策略。
- **帧级时间轴精度**：
  - **边界精修**：对淡入/淡出造成的首尾偏差，在字幕出现/消失边界附近用小窗口逐帧 OCR 重新对齐（自动 ROI 默认启用），并可越过 ROI 时间范围边界向外走查（±2s），修复 ROI 范围恰好截断字幕头尾的场景。
  - **尾部再校准**：对 SSIM 误填充到 ROI 末尾的淡出尾部，逐帧真实 OCR 回溯修剪。基准视频中淡入淡出字幕的头尾误差 ≤1 帧。
- **高级ASS字幕生成**：
  - **智能样式分配**：能根据字幕在视频中的位置（底部、顶部、场景文字）和内容语言（中/日/韩/俄/英）自动匹配不同的ASS样式。
  - **动态定位**：对于场景文字，能够生成带 `\pos` (位置)、`\fs` (字号)、`\frz` (旋转) 标签的ASS条目，精准还原其在画面中的状态。
  - **自定义模板**：支持加载外部 `.ass` 文件作为样式模板，实现高度个性化的字幕风格。
- **识别质量增强（可选）**：
  - **难帧 VLM 兜底**：投票不一致或低置信度的"难帧"可交给 OpenAI 兼容视觉大模型裁决（环境变量 `VLM_REFINE_BASE_URL` / `VLM_REFINE_API_KEY` / `VLM_REFINE_MODEL` 配置），失败时自动保留原 OCR 结果。
  - **LLM 字幕润色**：可选调用 OpenAI 兼容接口（如 DeepSeek）对识别文本批量校对，自动重试与自验证。
- **异步处理与进度反馈**：
  - 核心处理流程在独立的 `QThread` 中运行，避免UI冻结。
  - 提供实时的进度条和日志反馈，用户可随时了解处理阶段和状态。
- **便捷的文件操作**：
  - 支持 **拖拽加载** 视频文件、ROI配置文件 (`.json`) 和ASS模板文件 (`.ass`)。
  - ROI配置可以保存和加载，方便重复使用。

## 技术栈

- **GUI框架**: `PySide6` (Qt for Python)
- **OCR引擎**: `paddleocr>=3.7,<4.0`（**PP-OCRv6** 模型，支持 tiny/small/medium 档位；非中/英/日语言自动回落 PP-OCRv5 多语言模型）+ `paddlepaddle>=3.2.1,<3.3` (推荐 `paddlepaddle-gpu` 以利用CUDA加速；3.3.x 存在 oneDNN/PIR 回归故予钉版)
  - 备选引擎：`rapidocr>=3.9`（ONNX Runtime，集成 PP-OCRv6 模型，无需 PaddlePaddle，CPU 友好）
- **图像处理**: `OpenCV-Python` (支持CUDA GPU加速ROI提取) , `NumPy`
- **图像相似度计算**: `scikit-image`（跳帧判定在降采样灰度图上进行，默认目标宽度 320px，可配置）
- **文本相似度计算**: `python-levenshtein`（帧间文本比对容错，默认阈值 0.9）
- **可选 LLM 集成**: `openai` + `tenacity`（有界指数退避，429 限流重试有次数/时长上限）、`json-repair`

## 项目结构

```
video_subtitle_ocr/
├── main.py                   # 应用程序入口
├── cli.py                    # 命令行入口（headless 流水线，无 GUI）
├── preload_models.py         # OCR 模型预下载脚本
├── requirements.txt          # 运行必需依赖（CPU 版）
├── requirements-gpu.txt      # GPU 版依赖（paddlepaddle-gpu）
├── requirements-dev.txt      # 开发依赖（pytest、ruff）
├── requirements-a.txt        # 历史/环境快照（可选，含较多可选项）
├── requirements-full-pinned.txt  # 全量钉版环境快照（可选）
├── config.ass                # 默认样式模板
├── video_subtitle_ocr.desktop # 桌面快捷方式
├── CHANGELOG.md              # 更新日志（版本历史）

main_window/                  # 主窗口包（由原单一 main_window.py 拆分，纯结构重构）
├── __init__.py               # re-export SubtitleOCRGUI（对外接口不变）
├── window.py                 # SubtitleOCRGUI 主类组装（Mixin）
├── video_playback.py         # 视频加载、播放与帧导航
├── roi_editing.py            # ROI 绘制/编辑/复制粘贴
├── roi_config_io.py          # ROI 配置文件读写
├── source_config.py          # ASS 模板等外部源配置
├── auto_detection.py         # 加载即自动字幕 ROI 检测
├── scan_control.py           # 深度扫描入口与结果应用
└── pipeline_control.py       # OCR 任务启动与进度处理

components/                   # UI组件模块
├── control_panel.py          # 控制面板（运行按钮、模式切换、选项）
├── file_operations.py        # 文件操作面板（加载视频/ROI/深度扫描入口）
├── log_viewer.py             # 日志显示组件
├── roi_definition.py         # ROI时间定义与操作面板
├── roi_list.py               # ROI列表显示与管理组件
├── video_display.py          # 视频显示与绘制核心组件
├── video_detector_thread.py  # 视频类型检测后台线程
├── scan_worker_thread.py     # 全片扫描后台线程
├── scan_review_dialog.py     # 深度扫描结果复核对话框
├── color_gate_preview_dialog.py  # 颜色门槛预览对话框
└── deepseek_progress_panel.py    # LLM 润色进度面板

core/                         # 核心业务逻辑模块
├── pipeline_worker.py        # 核心处理流水线的工作线程
├── roi_extractor.py          # 从视频中提取ROI图像帧
├── ffmpeg_roi_segmenter.py   # 基于 ffmpeg 的 ROI 片段快速抽取（可选加速）
├── ocr_processor.py          # OCR批处理入口（兼容旧调用）
├── ocr_optimizer.py          # 智能OCR优化器（跳帧、投票、批量预测、LRU缓存）
├── ocr_engine_base.py        # OCR引擎抽象基类与注册表
├── ocr_engine_manager.py     # 引擎选择/惰性初始化/统一批量入口
├── ocr_engine_paddle.py      # PaddleOCR 引擎（PP-OCRv6/v5回落、语言与档位路由）
├── ocr_engine_rapid.py       # RapidOCR 引擎（rapidocr v3.x，ONNX）
├── fullframe_scanner.py      # 全片扫描普查（字幕带/水印/场景文字统计）
├── subtitle_roi_suggester.py # 加载即自动字幕带 ROI 建议（顶部+底部带）
├── watermark_filter.py       # 水印文本剔除（文本/位置匹配）
├── coordinate_restorer.py    # 将ROI内坐标还原为视频全局坐标
├── subtitle_generator/       # ASS 字幕生成包（由原单一模块拆分，纯结构重构）
│   ├── __init__.py           # re-export OCRToASSOptimizer/TextLine/FrameData/SubtitleGroup
│   ├── generator.py          # OCRToASSOptimizer 主类与 convert_from_memory 编排
│   ├── models.py             # TextLine / FrameData / SubtitleGroup 数据类
│   ├── data_grouping.py      # OCR 数据装载与帧分组/合并
│   ├── timeline.py           # ASS 时间解析/格式化
│   ├── event_merge.py        # 事件过滤、时间轴去重合并、多数投票
│   ├── llm_merge.py          # LLM 碎片字幕合并（可选）
│   ├── roi_filters.py        # ROI 剖面学习、高度过滤、水印与策略过滤
│   ├── source_classification.py  # overlay/scene 文字来源分类
│   └── styling.py            # 语言检测、\pos/\frz 标签、样式定位与 ASS 头
├── boundary_refine.py        # 字幕边界帧级精修（淡入淡出对齐、越界走查、尾部再校准）
├── aligner.py                # 字幕时间轴对齐辅助
├── vlm_refine.py             # 难帧视觉大模型兜底（OpenAI兼容，可选）
├── subtitle_llm_polish.py    # LLM字幕润色（可选）
├── llm_client.py             # OpenAI兼容LLM客户端（有界退避）
├── llm_prompts.py / prompts/ # LLM 提示词模板（fragment_merge / subtitle / text_classify 等）
├── scene_presets.py          # 场景预设（film_tv / anime 等）
├── color_presence_gate.py / classification_features.py / text_source_classifier.py
│                             # 文字来源分类辅助（颜色门槛、特征构造）
├── text_utils.py / video_type_detector.py  # 文本工具与视频类型检测
└── utils/ → logger.py（日志）/ time_utils.py / secret_store.py / app_qsettings.py

tests/                        # 单元测试（pytest，离线mock，无需安装OCR引擎）
scripts/
└── benchmark_regression.py   # 回归基准脚本（headless跑流水线并输出准确率/耗时报告）
benchmarks/
├── make_test_video.py        # 生成带已知字幕的测试视频 + ground-truth JSON
└── *.json                    # 基准报告

docs/
├── packaging.md              # DEB 打包与安装说明
├── testing.md                # 测试指南与测试记录
├── optimization_analysis.md  # 模型升级与优化分析
└── development_plan.md       # 开发方案与计划

i18n/                         # 国际化（源文案为中文，跟随系统 locale 加载）
├── app_ja_JP.qm			  # 日语翻译编译文件（lrelease 生成，随包发布）
├── app_ja_JP.ts			  # 日语翻译源文件
├── app_zh_CN.qm			  # 中文翻译编译文件（源文案即中文，基本为恒等映射）
├── app_zh_CN.ts			  # 中文翻译源文件
└── translator.py			  # QTranslator 封装（供按语言代码加载 .qm）

# 仓库外层（打包与测试辅助）
├── build_deb.sh              # 一键构建 .deb 包
├── video-subtitle-ocr/       # deb 打包树（DEBIAN/ + usr/ + opt/，见 docs/packaging.md）
└── generate_test_video.sh    # ffmpeg 生成带字幕的测试视频

```

## 核心工作流详解

当用户点击“字幕OCR识别”按钮后，`PipelineWorker` 线程启动，并按以下顺序执行核心处理流水线：

1.  **【步骤一】ROI帧提取 (`roi_extractor.py`)**
    - 遍历所有ROI配置，计算出需要在哪些视频帧上进行操作。
    - 逐帧读取视频，如果当前帧位于某个ROI的时间范围内，则根据该ROI的坐标（矩形或多边形）从当前帧画面中裁剪出对应的图像区域。
    - **模式选择**：支持“内存模式”（`in_memory_ocr=True`）和“磁盘模式”（`save_to_disk=True`）。内存模式直接将裁剪的图像数据传递给下一步，减少磁盘I/O；磁盘模式则将图像保存到临时目录。
    - **GPU加速**：如果系统检测到CUDA-enabled GPU且OpenCV支持CUDA，将尝试利用GPU进行图像裁剪，进一步提升性能。
    - 这些裁剪出的图像（以及其元数据）被传递给下一步。

2.  **【步骤二】智能OCR识别 (`ocr_optimizer.py`)**
    - 这是整个流程的性能和准确性关键。它接收到所有待处理的ROI图像帧。
    - **分组**：首先将所有帧按其所属的ROI进行分组。
    - **智能处理**：对每个组内的帧序列进行优化处理：
        - 对序列中的第一帧进行OCR，获取基准文本。
        - **两阶段搜索**：
            - **粗略搜索**：使用高效的 **图像结构相似性(SSIM)** 算法，以较大步长（`search_step`）快速向后搜索，找到与第一帧内容不再视觉相似的边界。
            - **精确搜索**：在粗略搜索确定的范围内，进行二分查找式的“精确”搜索，准确地找到内容保持相同的最后一帧。
        - **多点采样与投票 (`_get_best_ocr_result_from_sequence`)**：在确定了内容相似的帧序列（例如从第100帧到第150帧）后，它不会只信任第一帧的OCR结果。而是对这个序列的 **首、中、尾** 三帧进行OCR。然后对识别出的每一行文本进行投票，选择票数最多且平均置信度最高的文本作为最终结果。
        - 将这个经过投票优选出的“最佳结果”应用到整个相似序列的所有帧上，从而避免了大量的重复OCR计算。
    - 输出带有OCR文本和坐标信息的数据。

3.  **【步骤三】坐标还原 (`coordinate_restorer.py`)**
    - OCR识别出的文本坐标是相对于被裁剪的ROI小图的。
    - 此步骤读取OCR结果，并根据原始ROI在视频中的偏移量，将这些局部坐标转换回视频画面的全局坐标。

4.  **【步骤四】ASS字幕生成 (`subtitle_generator.py`)**
    - 接收所有带有全局坐标的OCR结果。
    - **分组与合并**：将文本内容和位置都相似的连续帧合并成一个字幕组（`SubtitleGroup`），这代表了一句完整的字幕。
    - **样式决策**：根据字幕组的平均位置（底部、顶部、场景）和文本内容（语言检测），为其分配合适的ASS样式和定位标签。
    - **格式化输出**：将所有字幕组转换成ASS `Dialogue` 行，并结合ASS文件头（可来自模板），最终生成 `.ass` 字幕文件。

## 安装与运行

1.  **克隆或下载项目**
    
    git clone git@github.com:yuukimasato/video_subtitle_ocr.git
    cd video_subtitle_ocr
    
    
    **项目推送说明**：
    - 项目首次推送于2025年8月5日
    - 使用`git push --force origin main`强制推送覆盖了远程仓库内容
    - 已配置.gitignore文件排除.venv目录

2.  **安装依赖**
    本仓库当前提供两套依赖清单：
    - `requirements.txt`：**运行必需**（推荐）
    - `requirements-a.txt`：历史/环境快照，包含较多可选依赖（可能更慢、更易冲突）

    建议使用虚拟环境安装（示例）：

```
    python3 -m venv .venv
    source .venv/bin/activate
    pip install -U pip
    pip install -r requirements.txt
```
    **注意**:
    - **OCR 模型**：默认使用 **PP-OCRv6**（paddleocr≥3.7）。首次运行会自动下载模型；
      可在界面"OCR 引擎"区选择**识别语言**（中/英/日走 v6，韩/俄/阿等自动回落 v5 多语言模型）
      与**模型档位**（Tiny 最快 / Small 均衡 / Medium 最准，默认自动）。
    - **CPU/GPU 选择**：
      - 默认安装 `requirements.txt` 会使用 **CPU 版** `paddlepaddle`（≥3.2.1，启用 MKLDNN 加速）。
      - 如需 GPU 加速，请按你的 CUDA/驱动环境安装对应的 `paddlepaddle-gpu`（不同平台/版本差异较大，建议以 PaddlePaddle 官方安装指引为准），并确保 NVIDIA 驱动与 CUDA 正确配置。
      - 无 PaddlePaddle 环境时可安装备选引擎：`pip install "rapidocr>=3.9,<4.0"`，并在界面切换到 RapidOCR。
    - **OpenCV 版本选择（避免冲突）**：
      - 请不要同时安装 `opencv-python` 与 `opencv-contrib-python`（二选一），否则容易出现 ABI/导入冲突。
      - 本项目默认依赖 `opencv-contrib-python==4.10.0.84`（由 `paddleocr` 的 paddlex 依赖硬性钉版）；`requirements.txt` 已按此固定，请勿手动替换为 `opencv-python`，也不要再额外安装其他 OpenCV 包。
    - **OpenCV GPU**：
      - PyPI 的 `opencv-python` 一般 **不包含 CUDA**；若要用 OpenCV CUDA 加速 ROI 裁剪，需要使用 CUDA 编译的 OpenCV（源码编译或第三方预编译包）。
    - **运行设备配置**：
      - PaddleOCR 的运行设备（CPU/GPU）通过项目目录下的 `.gpu_mode` 文件控制，内容示例：

```
export MODE="cpu"
```

```
export MODE="gpu"
```


3.  **运行程序**
    在项目根目录下，执行：


```
    python3 main.py
```

    也可以使用命令行方式（无需 GUI）：

```
    python3 cli.py 视频.mp4 -o 输出.ass
```

4. **快速自检（推荐）**

在虚拟环境中执行以下命令，用于快速检查关键依赖能否正常导入：

```
python3 -c "import cv2, numpy; from paddleocr import PaddleOCR; import skimage; import Levenshtein; print('imports ok')"
```
    

## DEB 包安装与打包

项目提供开箱即用的 Debian 软件包支持（适用于 Debian / Ubuntu / Linux Mint 及衍生版）。

### 安装已构建的 .deb

```bash
sudo dpkg -i video-subtitle-ocr_<版本>_all.deb
# 若依赖配置未完成（如网络中断），修复后重新配置：
sudo dpkg --configure -a
```

安装过程说明：

- 包本身仅约 5 MB（应用源码 + 启动器 + 桌面集成文件）。
- `postinst` 安装脚本会在 `/opt/apps/video_subtitle_ocr/.venv` 中创建独立 Python
  虚拟环境，并从 PyPI 安装 `requirements.txt` 中的依赖（需联网，下载量数百 MB）。
  依赖安装采用**有界退避重试**：最多 5 次尝试，等待 5/10/20/40/60 秒（总等待上限 135 秒），
  重试耗尽后返回可恢复错误并给出修复指引。
- OCR 模型（几十 MB）在首次使用时自动下载，缓存在 `~/.paddlex/`。

安装后可用命令：

| 命令 | 说明 |
| --- | --- |
| `video-subtitle-ocr` | 启动图形界面 |
| `video-subtitle-ocr-cli` | 命令行无界面提取字幕（见下文） |

桌面集成文件安装到：

- `/usr/share/applications/video_subtitle_ocr.desktop`（应用菜单入口）
- `/usr/share/icons/hicolor/256x256/apps/video-subtitle-ocr.png`（应用图标）
- `/usr/share/man/man1/video-subtitle-ocr*.1.gz`（手册页，`man video-subtitle-ocr-cli`）
- `/usr/share/doc/video-subtitle-ocr/copyright`（版权与许可证）

卸载：

```bash
sudo dpkg -r video-subtitle-ocr     # 保留 .venv（重装时免重新下载依赖）
sudo dpkg -P video-subtitle-ocr     # 彻底清除（含 .venv 与运行残留）
```

### 自行构建 .deb

在仓库根目录执行：

```bash
./build_deb.sh
```

脚本会自动完成：构建环境检测 → 将 `video_subtitle_ocr/` 源码同步进打包树
`video-subtitle-ocr/`（排除 `.git`、`.venv`、`__pycache__`、`tests/` 等）→ 校验必要文件 →
清理与权限设置 → 压缩 man 手册页 → 计算安装大小 → `dpkg-deb` 构建输出
`video-subtitle-ocr_<版本>_all.deb`。

打包树的目录布局、维护脚本（`postinst`/`prerm`/`postrm`）的实现细节，以及
无 root 环境下的安装测试方法，见 [docs/packaging.md](docs/packaging.md)。

## 命令行工具 (CLI)

`cli.py`（安装后为 `video-subtitle-ocr-cli`）复用与 GUI 完全相同的四段流水线
（ROI 帧提取 → SSIM 跳帧优化 OCR → 坐标还原 → ASS 生成），可在无显示器的服务器上运行：

```bash
# 默认：识别画面底部字幕带（帧高的 20%，至少 160px，可容纳双行字幕），输出 <视频名>.ass
python3 cli.py 视频.mp4

# 自定义 ROI（仅 5s~20s 生效）+ 样式模板 + RapidOCR 引擎
python3 cli.py 视频.mp4 --roi 0,560,1280,160@5-20 --template style.ass --engine rapid

# 保留临时目录便于调试；静默模式
python3 cli.py 视频.mp4 --keep-temp -q

# 全片扫描模式：自动定位字幕带+场景字区域、自动剔除水印
python3 cli.py 视频.mp4 --scan --scan-samples 24 --preset film_tv
```

常用参数：

| 参数 | 说明 |
| --- | --- |
| `-o, --output` | 输出 `.ass` 路径（默认 `<视频>.ass`） |
| `--roi` | 矩形区域 `x,y,w,h` 或 `x,y,w,h@起-止`（秒，可省略单端），可重复 |
| `--scan` | 全片扫描模式（采样统计字幕带/水印/场景字，替代默认 ROI） |
| `--scan-samples` | 扫描采样帧数（默认 24） |
| `--watermark-mode` | `auto`（默认，剔除检测到的水印文本）/ `keep`（仅报告不剔除） |
| `--no-scene-roi` | 扫描时不把画面中部场景字候选导入为 ROI |
| `--preset` | 场景预设 ID（如 `film_tv`、`anime`），对自动字幕带启用文字来源过滤 |
| `--engine` | `auto`（默认，自动选择第一个可用引擎） / `paddle` / `rapid`（ONNX 备用引擎） |
| `--lang` | 识别语言，默认 `ch` |
| `--model-tier` | `auto` / `tiny` / `small` / `medium` |
| `--template` | 外部 `.ass` 样式模板 |
| `--keep-temp` | 保留临时目录（调试） |
| `-q, --quiet` / `-V, --version` | 静默 / 查看版本 |

## 测试与回归基准

```bash
# 单元测试（离线 mock，无需安装 OCR 引擎，约 3 秒）
python -m pytest tests/ -v

# 端到端流水线回归基准（headless，输出准确率/耗时/内存报告）
python scripts/benchmark_regression.py \
    --video benchmarks/test_video_subtitle.mp4 \
    --ground-truth benchmarks/gt.json \
    --output my_run.json --baseline benchmarks/ppocrv6_run2.json

# 重新生成测试视频与 ground-truth（需要 ffmpeg）
python benchmarks/make_test_video.py --video test.mp4 --gt gt.json
```

当前基准：测试视频 5 条字幕（含 1 条淡入淡出），行准确率 100%，字幕头尾误差
≤1 帧（PP-OCRv6，475/480 帧 / 133 次 OCR 调用 / 约 15 秒，含默认启用的边界精修）。
历史基准报告见 `benchmarks/*.json`。

完整的五层测试方法（单元测试 → CLI 冒烟 → GUI 离屏冒烟 → 回归基准 →
deb 安装测试）、判定标准与最近一次测试记录见
[docs/testing.md](docs/testing.md)。开发依赖（`pytest`、`ruff`）见
`requirements-dev.txt`。各版本变更历史见 [CHANGELOG.md](CHANGELOG.md)。

## 使用指南

1.  **加载视频**：点击“加载视频”按钮，或直接将视频文件 **拖拽** 到主窗口的视频显示区域。
2.  **定义ROI**：
    - 在右侧“绘制模式”中选择“Rectangle (Drag)”或“Polygon (Click)”。
    - 在视频画面上拖拽（矩形）或依次点击（多边形，右键结束绘制）来框选字幕区域。
    - 在“ROI Definition”面板中，通过微调按钮（支持短按单帧、长按连续导航）或直接输入（支持时间格式 hh:mm:ss.ms 或帧号）来设定该ROI的“Start Time”和“End Time”。
    - 点击“Add New ROI”按钮，该ROI会出现在下方的“ROI List”中。
    - 重复此过程可添加多个ROI。
    - **ROI列表操作**：在“ROI List”中，右键点击列表项可进行“Copy”、“Paste After This Item”或“Delete”操作。列表下方也有“Paste to End”选项。
3.  **（可选）加载配置**：
    - 如果有之前保存的ROI配置 (`.json`)，可点击“Load ROI Config”直接导入，或直接将 `.json` 文件 **拖拽** 到主窗口。
    - 如果有ASS样式模板 (`.ass`)，可点击“Browse...”按钮选择，或直接将 `.ass` 文件 **拖拽** 到主窗口。
4.  **运行OCR**：
    - 在“Subtitle Generation”面板中，根据需要勾选“Debug Mode”、“Visualize Output”和“In-Memory Mode (Experimental)”等选项。
    - 点击“Subtitle OCR Recognition”按钮。
    - 在弹出的对话框中确认或修改输出的 `.ass` 文件名和路径。
    - 等待进度条完成即可。任务完成后，会提示是否打开文件所在目录。

## 核心模块代码解析

#### 1. `main_window/` 包 - UI与逻辑总控

主窗口由原单一 `main_window.py` 模块（1481 行的单一 `SubtitleOCRGUI` 类）拆分为
按功能组织的 Mixin 包，类名、方法签名与行为完全不变（纯结构重构）：

- **`window.py`**：`SubtitleOCRGUI` 主类，按顺序组装下列 Mixin。
- **`video_playback.py`**：视频加载、播放控制与帧导航（含短按单帧/长按连续导航）。
- **`roi_editing.py`**：ROI 绘制、编辑、复制/粘贴与 ROI 列表右键操作。
- **`roi_config_io.py`**：ROI 配置文件 (`.json`) 的保存与加载（含拖拽导入）。
- **`source_config.py`**：ASS 样式模板等外部文件配置。
- **`auto_detection.py`**：加载视频后自动检测字幕带并填充 ROI（可关闭）。
- **`scan_control.py`**：「深度扫描」入口、复核对话框结果应用与水印剔除清单。
- **`pipeline_control.py`**：收集 UI 参数、启动/取消 `PipelineWorker`，处理进度与完成信号。

`SubtitleOCRGUI` 类负责：
- **UI初始化**：加载并组织 `components` 目录下的所有UI组件，构建出完整的主界面。
- **状态管理**：维护如 `video_path`, `roi_data`, `clipboard_roi` 等核心状态变量，并根据这些状态动态更新UI（例如，未加载视频时禁用某些按钮）。
- **信号与槽连接**：通过Qt的信号-槽机制，将UI组件的用户操作（如按钮点击、滑块拖动）连接到对应的业务逻辑处理函数。例如，`file_ops_widget.load_video_requested` 信号连接到 `self.load_video` 槽函数。
- **事件处理**：
  - 处理 **拖拽事件** (`dragEnterEvent`, `dropEvent`)，支持视频文件、ROI配置文件 (`.json`) 和ASS模板文件 (`.ass`) 的便捷加载。
  - 处理窗口关闭事件 (`closeEvent`)，在后台任务运行时提供退出确认。
- **国际化支持**：界面语言**跟随系统 locale** 自动选择——启动时经 `QLocale().system()` 加载
  `i18n/app_<语言>.qm` 翻译文件。当前内置 **简体中文**（源文案）与 **日语**（`app_ja_JP`）
  两套界面文案；无对应翻译文件的 locale（如英语）回退显示中文源文案。
  如需临时切换语言，可通过环境变量指定 locale，例如 `LANGUAGE=ja_JP ./video-subtitle-ocr`
  或 `LC_ALL=zh_CN.UTF-8 python3 main.py`。

#### 2. `core/pipeline_worker.py` - 异步处理流水线

为了防止在进行耗时的OCR任务时UI卡死，所有核心逻辑都被封装在这个 `QThread` 子类中。
- **职责**：它的 `run` 方法是整个处理流程的入口，严格按照 **提取 -> 识别 -> 还原 -> 生成** 的顺序调用其他核心模块。
- **进度与通信**：通过定义 `progress_updated`, `finished`, `error` 等信号，向主线程（UI）报告当前进度、最终结果或发生的错误，主线程接收到信号后更新进度条和提示信息。
- **资源管理**：负责创建和清理临时工作目录 (`work_dir`)。在非调试模式下，任务完成后会自动删除所有中间文件。
- **可取消**：包含一个 `is_cancelled` 标志位，允许用户通过UI取消正在进行中的任务，并在各阶段检查此标志以实现优雅退出。

#### 3. `core/ocr_optimizer.py` - 智能OCR优化器

这是本项目的技术亮点，极大地提升了处理效率和准确性。
- **核心思想**：硬字幕在视频中通常会持续数秒，这意味着大量连续帧的字幕区域是完全相同或高度相似的。对每一帧都进行OCR是巨大的浪费。
- **`process_roi_group` 方法**：
  1.  **两阶段搜索**：当处理一个新文本的帧时，它首先使用计算成本极低的 `ssim` 算法进行一个大步长的“粗略”搜索，快速跳到可能发生变化的区域。然后在这个范围内进行二分查找式的“精确”搜索，准确地找到内容保持相同的最后一帧。
  2.  **多点采样与投票 (`_get_best_ocr_result_from_sequence`)**：在确定了内容相似的帧序列（例如从第100帧到第150帧）后，它不会只信任第一帧的OCR结果。而是对这个序列的 **首、中、尾** 三帧进行OCR。然后对识别出的每一行文本进行投票，选择票数最多且平均置信度最高的文本作为最终结果。
  3.  **结果填充**：将这个经过投票优选出的“最佳结果”应用到从100帧到150帧的所有帧上。
- **缓存**：内部使用 `_image_cache` 和 `_feature_cache` 来存储已读取的图像和计算的灰度图，进一步加速 `ssim` 计算，尤其是在“内存模式”下。
- **模式支持**：能够处理来自磁盘路径的图像，也能直接处理内存中的 `numpy` 数组图像。

#### 4. `core/subtitle_generator/` 包 - 高级ASS字幕生成器

该模块负责将零散的、带坐标的OCR文本数据，转换成结构化、带样式的高级字幕文件。
由原单一 `subtitle_generator.py` 模块（约 1367 行）拆分为按职责组织的包，
`OCRToASSOptimizer` 及 `TextLine`/`FrameData`/`SubtitleGroup` 等公开符号经
`__init__.py` re-export，对外接口不变（纯结构重构）：

- **`models.py`**：`dataclasses`（`TextLine`, `FrameData`, `SubtitleGroup`）清晰组织数据。
- **`generator.py`**：`OCRToASSOptimizer` 主类与 `convert_from_memory` 编排主流程。
- **`data_grouping.py`**：`_load_and_organize_ocr_data` 及帧分组/相似度/桥接合并。
- **`timeline.py`**：ASS 时间解析、格式化与末时估计。
- **`event_merge.py`**：文本规范化、噪声判定、事件过滤、时间轴去重合并、多数投票——
  `_group_consecutive_frames` 将相同文本且位置相近的连续帧合并成一句完整字幕。
- **`roi_filters.py`**：ROI 剖面学习、高度过滤、水印剔除与按 ROI 过滤策略
  （手动 ROI `keep_all` 永不过滤，自动字幕带 `auto` 受场景过滤控制）。
- **`source_classification.py`**：overlay/scene 文字来源分类与时序统计特征。
- **`styling.py`**：`_determine_style_and_position` 智能样式核心——按平均 Y 坐标判断
  底部/顶部/场景文字，`_detect_language`（Unicode 字符区间）选择样式名；
  场景文字计算中心点/旋转角/高度并生成 `\pos`/`\frz`/`\fs` 标签。
  `_get_ass_header` 支持用户模板 `.ass` 的 `[V4+ Styles]` 样式复用。
- **`llm_merge.py`**：可选 LLM 碎片字幕合并。

## 未来可拓展方向
