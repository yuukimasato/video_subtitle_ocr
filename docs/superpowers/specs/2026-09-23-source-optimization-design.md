# 基于源代码的功能分析与优化设计

日期：2026-09-23。分析依据仅为 Python/Shell 源码、测试代码与本次实际执行结果；未阅读现有项目说明、设计文档或历史测试报告。用户已授权分析、制定方案及落实，允许必要的功能精简。

## 基线与恢复

源码仓库：`/home/hope/Tools/video_subtitle_ocr/video_subtitle_ocr`。起始分支 `feature/font-intel-p1`，干净工作区，提交 `ead48f9c3381291bedce2199bab585d53461b4c0`。

- 基线标签：`baseline/source-optimization-20260923`。
- 实施分支：`optimize/source-analysis-20260923`。
- 独立备份：`/home/hope/Tools/video_subtitle_ocr_backups/20260923-source-optimization/`。
- `repository.bundle` 包含全部已存在 Git 引用及完整历史，已通过 `git bundle verify`。
- `source.tar` 保存基线所有已跟踪文件；`build_deb.sh` 单独保存仓库外构建脚本；校验和见 `SHA256SUMS`。
- 原版测试：1572 passed, 1 skipped, 14 warnings，72.44 秒；日志及 JUnit XML 在上述备份目录。`ruff check --select F . --exclude docs` 通过。
- 备份不包含虚拟环境、忽略的运行缓存和外层视频素材；本次不修改这些用户数据。备份与源码位于同一机器，防误改，不代替异地灾备。

无损查看原版：`git worktree add --detach /home/hope/Tools/video_subtitle_ocr-original baseline/source-optimization-20260923`。仓库损坏时：`git clone /home/hope/Tools/video_subtitle_ocr_backups/20260923-source-optimization/repository.bundle /home/hope/Tools/video_subtitle_ocr-restored`，然后切换到上述提交。撤销单项优化使用对应提交的 `git revert`，不通过硬重置丢弃后续工作。

## 功能图谱

| 能力 | 实际代码入口 | 判断 |
| --- | --- | --- |
| GUI 加载/播放、矩形/多边形 ROI、颜色过滤、配置保存 | main_window/*、components/* | 主交互，保留 |
| CLI 视频/ROI 文件、引擎/语言/模型选择 | cli.py | 自动化主入口，保留并消除重复主流程 |
| 全帧扫描、字幕条带/水印/场景文本识别 | fullframe_scanner、subtitle_roi_suggester、video_type_detector | 减少手工标注，保留 |
| 抽帧、相似帧跳过、投票、边界精修、坐标还原 | roi_extractor、ocr_optimizer、pipeline_stages | 核心，优先优化 |
| 长视频进程分块、失败重试、顺序回退 | chunk_planner/worker/parallel_runner/merger | 有实际用途，保留 |
| 运动平面、轨迹、亮度、遮挡、场景文字布局 | scene_plane_tracker、motion_ass、scene_text_policy | 与静态字幕不同的处理能力，保留 |
| ASS 合并、样式、噪声过滤、可选润色 | subtitle_generator/*、subtitle_llm_polish | 输出核心，保留 |
| 可选翻译及字体识别/映射/许可数据库 | font_intel/*、font_cli.py | 独立可选能力，不能仅凭体积判为无用 |
| CPU/GPU 依赖与 DEB 打包 | requirements*.txt、外层 build_deb.sh | 本轮不改安装策略，不自动安装/发布 |

## 已确认的问题

1. `cli.run_pipeline` 将抽帧生成器直接转为大列表，并将多个交错 ROI 一起传给 `OcrOptimizer.process_roi_group`。后者以同一 ROI 的时间序列为前提，查找相似区间时没有 ROI 边界保护。GUI 的公共阶段已按 ROI 分组，因此两入口存在正确性差异和重复维护。
2. 两种顺序抽帧器在 ROI 已全部结束后仍 `grab()` 直至视频末尾。分块 worker 也因此继续解码自己窗口之外的尾部，浪费 CPU/IO。
3. `extract_and_ocr_stage` 的主优化器及两种线程任务只在成功路径清缓存。流式异常退出使用 `shutdown(wait=False)`，已运行线程可能在上层删除工作目录后继续访问临时文件。
4. 流式识别中抽帧仍逐帧发 1–10%，识别发 10–65%，会发生进度倒退。修复时在阶段边界集中保证单调、合并同一百分比更新。
5. `generator.py` 有三处直接打开最终 ASS 路径写入，写入失败会截断旧结果。
6. `get_llm_client` 没关闭 OpenAI SDK 默认重试，与外层 tenacity 叠加，实际 HTTP 尝试数可能超过外层计数。现有 10 次/单次等待 60 秒/总退避预算 300 秒策略应为唯一重试层；请求耗时与退避预算是不同指标。

## 方案比较与选择

A（采用）：在原有架构内合并重复的 CLI 阶段，修复资源生命周期与安全输出，增加行为回归测试。收益明确、可逐项回退。

B：把所有入口强制改为按时间/内存分桶。能降低长视频图像内存，但改变相似区间采样和投票边界，必须先建设多场景准确率数据集；本轮不将其偷偷设为默认。

C：删除运动、字体、翻译模块或重写 GUI。没有使用证据支持删除，回归面大，不采用。

## 实施边界和行为契约

- 删除 CLI 私有的抽帧→OCR→还原实现，调用现有公共阶段；单进程、分块使用同一个上下文构造函数。
- 保持 CLI 不执行边界精修的既有行为；该历史差异明确保留，避免本轮把算法变化混入结构优化。GUI 原有精修照常运行。
- 默认不改时间分片、引擎、字体、翻译、场景策略和网络功能开关。主内存仍随图像数量增长，此轮不声称已经实现全流程有界内存。
- 保留 ROI 时间端点及帧号约定；仅停止最后活动帧后的解码，不修改起点 seek 策略，不纠正历史 ROI 时间钳制语义。
- 优化器无论正常、异常或取消均清理；流式任务使用内部停止事件，退出时取消排队任务并等待运行任务协作结束，再允许删除目录。无法中断底层正在执行的一次 OCR 调用，不承诺即时强杀。
- 公共抽取/OCR 阶段进度单调，重复整数百分比不反复发送；回调调用保持串行。
- ASS 同目录临时文件完整写入、flush/fsync 后 `os.replace`；失败保留旧文件并删除临时文件，所有输出分支包括空 ASS 共用该入口；编码保持 UTF-8 BOM。
- OpenAI SDK `max_retries=0`，保留现有 tenacity 的 429 有界重试与上层降级，不添加无界重试。

## 验收

新增回归覆盖：交错 ROI 的隔离；CLI 公共阶段配置和调用；抽帧停止点与输出帧一致；正常/异常/取消时资源清理；流式进度单调与排队任务取消；ASS 写入/替换失败后原文件完整；SDK 关闭隐式重试且 429 实际 HTTP 尝试数受控。

运行全部测试和 F 类静态检查；执行真实 RapidOCR CLI 小视频冒烟并与基线单 ROI ASS 比较。记录实际结果和局限，不以 mock 测试宣称所有真实视频准确率提高，不发布安装包或改动已安装应用。

## 已实施与验证记录

2026-09-23 已完成本设计范围，按独立提交交付：

| 提交 | 结果 |
| --- | --- |
| deba293 | CLI 复用公共阶段，ROI 隔离，缓存和生成器清理，流式进度单调，流式退出等待任务 |
| 7c39a78 | 普通/合成抽帧在最后活动 ROI 帧后停止解码 |
| 9cb3c7b | 三个 ASS 写入分支使用原子替换，失败保留旧文件 |
| 1249459 | SDK max_retries=0，外层统一控制 429 重试 |
| afaac2e | 非流式并行 ROI 失败时停止其他任务，再退出线程池 |

新增回归先在原实现复现问题：阶段生命周期/CLI 5 个失败；尾部解码 4 个失败；原子输出 5 个失败；SDK 重试用例 1 个失败。修复后对应测试全部通过。测试使用 mock HTTP，不调用真实收费 API。

真实 RapidOCR 小视频：1280×720、30 FPS、480 帧，单 ROI、workers=1。原版与优化版均成功输出 5 条 Dialogue，ASS 逐字节相同，SHA256 为 `db9f9d2e28fe2de1126812ce450e87fa77badb6617f38dd48a60c38e4fdd341f`。两次墙钟耗时分别 83.11/111.76 秒，峰值 RSS 1930768/2000444 KiB；优化版与全量回归并发运行，资源竞争不同，因此这些值仅为运行记录，不作为提速或内存优化证据。

解码专项：同一真实 480 帧视频只提取 0..29 帧。两版帧号和像素哈希完全一致；3 次测量原版约 0.070–0.086 秒，优化版约 0.016–0.017 秒。另有计数型回归保证 ROI 结束后不再 grab，收益仅针对存在不需要处理的尾部，不外推为整体 OCR 的固定提速倍数。

首次完整优化回归：1594 passed, 1 skipped, 14 warnings。最终代码全量验证：1595 passed, 1 skipped, 14 warnings，72.25 秒；F 类静态检查与 git diff --check 通过。新增 23 项测试。

证据目录为本设计开头的独立备份目录，新增 `task1-red.log`、`task2-red.log`、`task3-red.log`、`task4-red.log`、`optimized-tests.*`、`final-tests.*`、`original-cli.log`、`optimized-cli.log`、`cli-comparison.json`、`decode-comparison.json`、`original.ass` 和 `optimized.ass`。

## 明确保留的后续方向

1. 长视频默认内存模式仍一次保留图像；当前优化没有改变投票窗口，后续若采用有界内存分桶，需要短字幕、双语、多 ROI、渐显、移动文字和 4K 素材的准确率对照。
2. GUI 有边界精修而 CLI 无，这是既有差异；如要统一，应单独定义质量/耗时目标及 CLI 参数兼容策略。
3. ROI 配置格式校验、用户参数提前校验、GUI 手工保存的原子性仍可加强；此次未将它们混入核心流程修复。
4. 本轮没有发现足以删除翻译、运动文字或字体功能的证据。实际精简的是重复 CLI 核心实现和运行时签名探测，而非现有用户能力。
5. 真实验证限本地 CPU、缓存模型和单个基准视频；GPU、完整 GUI 手工交互及 DEB 安装未执行，本轮不发布、不改已安装应用。

## 真实视频复核后的优先级修订（2026-09-23）

已按用户追加要求运行四个现有测试视频和一次邮件遮挡对照，逐帧渲染原视频、视频+ASS、纯 ASS。新增证据表明：已有 1595 项回归通过并不覆盖真实多行时序、旁注时间合并与遮挡采样问题。

- P0：phone12 的 1441 事件中 1138 条零时长；phone11 全段被压成一个固定旁注；邮件即使开启遮挡仍零 iclip，已定位检测/应用采样不相交。
- P1：CLI 运动阶段未继承 small 模型，保存的语言/策略/姿态存在传递差异；4K 与聊天小字有尺寸位置偏差，且本机日文字体发生替代。
- 原先“内存分桶、入口精修统一”等后续方向保留，但排在时序正确性、实际遮挡效果与参数一致性之后。运动和静态回退继续保留，没有足够证据删除整个功能。

详细分析、截图、根因与验收已移入 [真实视频优化设计](2026-09-23-real-video-optimization-design.md)，后续执行使用 [第二轮实施总表](../plans/2026-09-23-real-video-implementation.md)。上一轮完成记录不改写为待完成；第二轮此时仅产出文档，未修改生产代码。

用户进一步明确：11号是静止聊天文字叠在横移背景上，新增文字/背景运动解耦；邮件有色遮罩与亮度绑定，手部用逐帧轮廓iclip同时裁文字和遮罩。已补入新设计 V6/V7、实施 D 与 B3/B4，包含三帧轮廓可行性及失败案例。
