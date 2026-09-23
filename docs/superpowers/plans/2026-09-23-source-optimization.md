# 源码分析优化实施计划

**Goal:** 保持功能和默认算法的前提下，消除 CLI/GUI 核心流程分歧、减少无效解码并保护失败路径。
**Architecture:** 使用现有 PipelineContext 和公共阶段；资源清理集中在 finally；ASS 写入使用小型公共原子写入函数。
**Tech Stack:** Python 3.12、PySide6、OpenCV、Paddle/RapidOCR、pytest。
**Spec:** docs/superpowers/specs/2026-09-23-source-optimization-design.md

## 约束

本会话直接实施；未调用不可用的执行技能或新子任务。保留默认算法、不发布安装包。先记录基线，每项独立提交。现有文档不作为实现依据。

## Task 1：公共阶段与生命周期

文件：cli.py、core/pipeline_stages.py、tests/test_pipeline_stage_lifecycle.py、tests/test_cli_shared_stages.py。

- [ ] 用 fake optimizer 捕获每次输入的 ROI 标识，断言 `len({frame[3] for frame in frames}) == 1`；通过 fake generator 的 finally 标志检验异常后关闭。
- [ ] 对正常/处理抛错/取消，断言创建的 optimizer 全部 cleanup；流式任务失败后没有继续运行的任务。
- [ ] CLI 提取 `_build_pipeline_context(args, video_path, roi_entries, info, work_dir)`，由顺序与分块复用；顺序调用 `extract_and_ocr_stage` 和 `restore_stage`，不调用 refine。
- [ ] 主 optimizer 和 generator 初始化为 None，finally 关闭；线程任务 `try/finally: local_opt.cleanup()`；内部 Event 与外部取消函数组合；退出时先发停止信号再 `shutdown(wait=True, cancel_futures=True)`。
- [ ] 用 Lock 保护进度，忽略 `pct <= last_pct`。
- [ ] 运行上述新增测试与 tests/test_cli_workers.py、tests/test_pipeline_refine_integration.py、tests/test_chunk_worker.py，独立提交。

## Task 2：避免 ROI 结束后的无效解码

文件：core/roi_extractor.py、tests/test_roi_extractor_seek.py。

- [ ] fake capture 记录 grab 次数，ROI 2..4、总长 100，期望只 grab 5 次，产出帧 [2,3,4]；覆盖普通/合成两种抽帧器。
- [ ] 构建区间事件时累积 `last_active_frame = max(last_active_frame, ev_end)`，循环上限使用该值，保留帧号和端点约定。
- [ ] 测试中覆盖末帧 ROI、多 ROI、空 ROI，运行实际 seek 视频用例，独立提交。

## Task 3：保护 ASS 输出

文件：utils/atomic_write.py、core/subtitle_generator/generator.py、tests/test_atomic_ass_output.py。

- [ ] 写入原文件 OLD，模拟 `os.replace` 抛出 OSError，断言原文件仍 OLD、目录没有残留 tmp；模拟 fsync 错误同样断言。
- [ ] 实现 `atomic_write_text(path, content, *, encoding="utf-8")`，NamedTemporaryFile(dir=目标父目录, delete=False)，flush/fsync，os.replace，finally 删除残余临时文件。
- [ ] ASS 三个写入点均使用原子函数，encoding="utf-8-sig"；对非空和空结果验证 BOM、完整事件及无临时文件。
- [ ] 运行输出、翻译和场景策略测试，独立提交。

## Task 4：唯一的有界重试控制

文件：core/llm_client.py、tests/test_llm_client_backoff.py。

- [ ] 构造真实 OpenAI 客户端断言 max_retries == 0；用 httpx.MockTransport 连续返回 429，注入不等待的 tenacity retry 并缩短尝试数，断言 HTTP 调用数等于设定上限且抛出 RateLimitError。
- [ ] client_kwargs 中设置 `"max_retries": 0`，不改变外层上限和失败降级。
- [ ] 运行 backoff、API error degradation、translation 测试，独立提交。

## Task 5：整体验收

- [ ] `QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest -q --junitxml=备份目录/optimized-tests.xml`。
- [ ] `.venv/bin/ruff check --select F . --exclude docs` 与 `git diff --check`。
- [ ] 在临时隔离源码副本与当前源码分别使用 RapidOCR 处理 benchmarks/test_video_subtitle.mp4，输出写入备份目录；比较 ASS，记录耗时，仅作为小样本验证。
- [ ] 更新本次新建文档的完成情况、证据、后续工作范围；验证备份 SHA256SUMS，保持代码工作区干净。
