# 视频字幕OCR工具 (Video Subtitle OCR Tool)

## 目录

- [项目简介](#项目简介)
- [核心功能](#核心功能)
- [技术栈](#技术栈)
- [项目结构](#项目结构)
- [核心工作流详解](#核心工作流详解)
- [安装与运行](#安装与运行)
- [使用指南](#使用指南)
- [核心模块代码解析](#核心模块代码解析)
  - [1. `main_window.py` - UI与逻辑总控](#1-main_windowpy---ui与逻辑总控)
  - [2. `core/pipeline_worker.py` - 异步处理流水线](#2-corepipeline_workerpy---异步处理流水线)
  - [3. `core/ocr_optimizer.py` - 智能OCR优化器](#3-coreocr_optimizerpy---智能ocr优化器)
  - [4. `core/subtitle_generator.py` - 高级ASS字幕生成器](#4-coresubtitle_generatorpy---高级ass字幕生成器)
- [未来可拓展方向](#未来可拓展方向)

## 项目简介

本项目是一个基于 PySide6 (Qt for Python) 和 PaddleOCR 开发的桌面应用程序，旨在帮助用户从视频文件中提取硬字幕（即内嵌在视频画面中的字幕），并将其转换为标准的 `.ass` 格式字幕文件。


![preview1](https://github.com/yuukimasato/video_subtitle_ocr/blob/main/resources/preview1.png)


![preview2](https://github.com/yuukimasato/video_subtitle_ocr/blob/main/resources/preview3.png)

软件提供了交互式的图形用户界面（GUI），用户可以直观地在视频上框选字幕区域（ROI），设置其生效的时间范围，并通过一键式操作启动后台OCR识别和字幕生成流程。项目内置了智能优化算法，能够大幅提升处理效率并保证识别的准确性。

## 核心功能

- **交互式GUI界面**：基于 PySide6 构建，提供视频预览、时间轴控制、参数设置等功能，操作直观。
- **灵活的ROI定义**：
  - 支持 **矩形** 和 **多边形** 两种方式绘制字幕区域，适应各种不规则形状的字幕。
  - 支持定义多个ROI，每个ROI可以有独立的生效时间段，轻松应对多位置、多类型的字幕。
- **智能OCR优化**：
  - **跳帧处理**：通过 `skimage.metrics.ssim` 比较帧间图像的结构相似性，自动跳过内容未发生变化的冗余帧，极大缩短OCR处理时间。
  - **投票机制**：对内容相似的连续帧序列进行多点采样（首、中、尾），对OCR结果进行投票，选出最可信的文本，有效提高识别准确率，对抗单帧识别不稳定的问题。
- **高级ASS字幕生成**：
  - **智能样式分配**：能根据字幕在视频中的位置（底部、顶部、场景文字）和内容语言（中/日/韩/俄/英）自动匹配不同的ASS样式。
  - **动态定位**：对于场景文字，能够生成带 `\pos` (位置)、`\fs` (字号)、`\frz` (旋转) 标签的ASS条目，精准还原其在画面中的状态。
  - **自定义模板**：支持加载外部 `.ass` 文件作为样式模板，实现高度个性化的字幕风格。
- **异步处理与进度反馈**：
  - 核心处理流程在独立的 `QThread` 中运行，避免UI冻结。
  - 提供实时的进度条和日志反馈，用户可随时了解处理阶段和状态。
- **便捷的文件操作**：
  - 支持拖拽加载视频文件、ROI配置文件 (`.json`) 和ASS模板文件 (`.ass`)。
  - ROI配置可以保存和加载，方便重复使用。

## 技术栈

- **GUI框架**: `PySide6` (Qt for Python)
- **OCR引擎**: `paddleocr` & `paddlepaddle`
- **图像处理**: `OpenCV-Python`, `NumPy`
- **图像相似度计算**: `scikit-image`
- **文本相似度计算**: `python-levenshtein`

## 项目结构

```
video_subtitle_ocr/
├── main.py                   # 应用程序入口
├── main_window.py            # 主窗口类，整合所有UI组件和业务逻辑
├── requirements-a.txt        # 依赖文件
├── config.ass                # 配置文件
├── Source_Han_Sans_Medium.otf # 字体文件
├── video_subtitle_ocr.desktop # 桌面快捷方式

components/                   # UI组件模块
├── control_panel.py          # 控制面板（运行按钮、模式切换、选项）
├── file_operations.py        # 文件操作面板（加载视频/ROI）
├── log_viewer.py             # 日志显示组件
├── roi_definition.py         # ROI时间定义与操作面板
├── roi_list.py               # ROI列表显示与管理组件
└── video_display.py          # 视频显示与绘制核心组件

core/                         # 核心业务逻辑模块
├── pipeline_worker.py        # 核心处理流水线的工作线程
├── roi_extractor.py          # 从视频中提取ROI图像帧
├── ocr_processor.py          # PaddleOCR的封装与调用
├── ocr_optimizer.py          # 智能OCR优化器（跳帧、投票）
├── coordinate_restorer.py    # 将ROI内坐标还原为视频全局坐标
└── subtitle_generator.py     # 从OCR结果生成ASS字幕文件

utils/                        # 工具类模块
├── logger.py                 # 日志配置，包含一个Qt信号处理器
└── time_utils.py             # 时间格式化与解析工具

scripts/                      # 脚本目录
├── detect_gpu.sh
├── download_paddleocr.sh
├── install_deps.sh
├── install_python.sh
└── install-common.sh
```

## 核心工作流详解

当用户点击“字幕OCR识别”按钮后，`PipelineWorker` 线程启动，并按以下顺序执行核心处理流水线：

1.  **【步骤一】ROI帧提取 (`roi_extractor.py`)**
    - 遍历所有ROI配置，计算出需要在哪些视频帧上进行操作。
    - 逐帧读取视频，如果当前帧位于某个ROI的时间范围内，则根据该ROI的坐标（矩形或多边形）从当前帧画面中裁剪出对应的图像区域。
    - 这些裁剪出的图像（以及其元数据）被传递给下一步。支持“内存模式”和“磁盘模式”。

2.  **【步骤二】智能OCR识别 (`ocr_optimizer.py`)**
    - 这是整个流程的性能和准确性关键。它接收到所有待处理的ROI图像帧。
    - **分组**：首先将所有帧按其所属的ROI进行分组。
    - **智能处理**：对每个组内的帧序列进行优化处理：
        - 对序列中的第一帧进行OCR，获取基准文本。
        - 使用高效的 **图像结构相似性(SSIM)** 算法，快速向后搜索，找到与第一帧内容不再相似的边界。
        - 在这个相似的帧序列中，通过 **多点采样和投票机制** 选出最稳定、最准确的OCR结果。
        - 将这个最佳结果应用到整个相似序列的所有帧上，从而避免了大量的重复OCR计算。
    - 输出带有OCR文本和坐标信息的数据。

3.  **【步骤三】坐标还原 (`coordinate_restorer.py`)**
    - OCR识别出的文本坐标是相对于被裁剪的ROI小图的。
    - 此步骤读取OCR结果，并根据原始ROI在视频中的偏移量，将这些局部坐标转换回视频画面的全局坐标。

4.  **【步骤四】ASS字幕生成 (`subtitle_generator.py`)**
    - 接收所有带有全局坐标的OCR结果。
    - **分组与合并**：将文本内容和位置都相似的连续帧合并成一个字幕组（`SubtitleGroup`）。
    - **样式决策**：根据字幕组的平均位置（底部、顶部、场景）和文本内容（语言检测），为其分配合适的ASS样式和定位标签。
    - **格式化输出**：将所有字幕组转换成ASS `Dialogue` 行，并结合ASS文件头（可来自模板），最终生成 `.ass` 字幕文件。

## 安装与运行

1.  **克隆或下载项目**
    ```bash
    git clone git@github.com:yuukimasato/video_subtitle_ocr.git
    cd video_subtitle_ocr
    ```
    
    **项目推送说明**：
    - 项目首次推送于2025年8月5日
    - 使用`git push --force origin main`强制推送覆盖了远程仓库内容
    - 已配置.gitignore文件排除.venv目录

2.  **安装依赖**
    可执行项目目录的 bash install-optimized.sh 

    也可创建一个虚拟环境。核心依赖如下，你可以将它们保存到 `requirements.txt` 文件中然后通过 `pip install -r requirements.txt` 安装。

    # requirements.txt
    ```
    PySide6
    opencv-python
    numpy
    paddlepaddle  # 或者 paddlepaddle-gpu (推荐，需配置CUDA)
    paddleocr
    scikit-image
    python-levenshtein
    ```
    **注意**: 为了获得最佳性能，推荐安装 `paddlepaddle-gpu` 版本，并确保你的NVIDIA显卡驱动和CUDA环境已正确配置。

3.  **运行程序**
    在项目根目录下，执行：
    
    python main.py
    

## 使用指南

1.  **加载视频**：点击“加载视频”按钮或直接将视频文件拖拽到主窗口。
2.  **定义ROI**：
    - 在右侧“绘制模式”中选择“矩形”或“多边形”。
    - 在视频画面上拖拽（矩形）或依次点击（多边形，右键结束绘制）来框选字幕区域。
    - 在“ROI 定义”面板中，通过微调按钮或直接输入来设定该ROI的“开始时间”和“结束时间”。
    - 点击“添加新ROI”按钮，该ROI会出现在下方的“ROI 列表”中。
    - 重复此过程可添加多个ROI。
3.  **（可选）加载配置**：
    - 如果有之前保存的ROI配置 (`.json`)，可点击“加载ROI配置”直接导入。
    - 如果有ASS样式模板 (`.ass`)，可点击“浏览...”按钮或拖拽文件到窗口来指定。
4.  **运行OCR**：
    - 在“字幕生成”面板中，根据需要勾选“调试模式”、“可视化输出”等选项。
    - 点击“字幕OCR识别”按钮。
    - 在弹出的对话框中确认或修改输出的 `.ass` 文件名和路径。
    - 等待进度条完成即可。

## 核心模块代码解析

#### 1. `main_window.py` - UI与逻辑总控

这是项目的中枢神经。`SubtitleOCRGUI` 类负责：
- **UI初始化**：加载并组织 `components` 目录下的所有UI组件，构建出完整的主界面。
- **状态管理**：维护如 `video_path`, `roi_data` 等核心状态变量，并根据这些状态动态更新UI（例如，未加载视频时禁用某些按钮）。
- **信号与槽连接**：通过Qt的信号-槽机制，将UI组件的用户操作（如按钮点击）连接到对应的业务逻辑处理函数。例如，`file_ops_widget.load_video_requested` 信号连接到 `self.load_video` 槽函数。
- **事件处理**：处理拖拽事件 (`dragEnterEvent`, `dropEvent`) 和窗口关闭事件 (`closeEvent`)。
- **任务启动**：当用户请求运行OCR时，它负责收集所有UI上的配置参数，创建并启动 `PipelineWorker` 后台线程。

#### 2. `core/pipeline_worker.py` - 异步处理流水线

为了防止在进行耗时的OCR任务时UI卡死，所有核心逻辑都被封装在这个 `QThread` 子类中。
- **职责**：它的 `run` 方法是整个处理流程的入口，严格按照 **提取 -> 识别 -> 还原 -> 生成** 的顺序调用其他核心模块。
- **进度与通信**：通过定义 `progress_updated`, `finished`, `error` 等信号，向主线程（UI）报告当前进度、最终结果或发生的错误，主线程接收到信号后更新进度条和提示信息。
- **资源管理**：负责创建和清理临时工作目录。在非调试模式下，任务完成后会自动删除所有中间文件。
- **可取消**：包含一个 `is_cancelled` 标志位，允许用户通过UI取消正在进行中的任务。

#### 3. `core/ocr_optimizer.py` - 智能OCR优化器

这是本项目的技术亮点，极大地提升了处理效率和准确性。
- **核心思想**：硬字幕在视频中通常会持续数秒，这意味着大量连续帧的字幕区域是完全相同或高度相似的。对每一帧都进行OCR是巨大的浪费。
- **`process_roi_group` 方法**：
  1.  **两阶段搜索**：当处理一个新文本的帧时，它首先使用计算成本极低的 `ssim` 算法进行一个大步长的“粗略”搜索，快速跳到可能发生变化的区域。然后在这个范围内进行二分查找式的“精确”搜索，准确地找到内容保持相同的最后一帧。
  2.  **多点采样与投票 (`_get_best_ocr_result_from_sequence`)**：在确定了内容相似的帧序列（例如从第100帧到第150帧）后，它不会只信任第一帧的OCR结果。而是对这个序列的 **首、中、尾** 三帧进行OCR。然后对识别出的每一行文本进行投票，选择票数最多且平均置信度最高的文本作为最终结果。
  3.  **结果填充**：将这个经过投票优选出的“最佳结果”应用到从100帧到150帧的所有帧上。
- **缓存**：内部使用缓存来存储已计算的图像灰度图，进一步加速 `ssim` 计算。

#### 4. `core/subtitle_generator.py` - 高级ASS字幕生成器

该模块负责将零散的、带坐标的OCR文本数据，转换成结构化、带样式的高级字幕文件。
- **数据结构**：使用 `dataclasses` (`TextLine`, `FrameData`, `SubtitleGroup`) 来清晰地组织数据。
- **`_group_consecutive_frames` 方法**：将具有相同文本内容和相近位置的连续 `FrameData` 对象合并成一个 `SubtitleGroup`，这代表了一句完整的字幕。
- **`_determine_style_and_position` 方法**：这是实现智能样式的核心。
  - 它会计算字幕组的平均Y坐标，判断其属于 **底部区域** (`BOTTOM`)、**顶部区域** (`TOP`) 还是 **场景文字** (`SCENE`)。
  - 对于底部字幕，它会调用 `_detect_language`（通过字符的Unicode范围判断）来选择合适的样式名（如 `CH`, `JP`, `Default`）。
  - 对于场景文字，它会计算其中心点、旋转角度和高度，并生成包含 `\pos`, `\frz`, `\fs` 等ASS覆盖标签的文本行。
- **模板支持 (`_get_ass_header`)**：如果用户提供了模板 `.ass` 文件，它会读取该文件的 `[V4+ Styles]` 部分，并将其用作生成的字幕文件的样式定义，实现了高度的自定义性。

## 未来可拓展方向
- **多语言支持**：对UI界面本身进行国际化（i18n）支持。


