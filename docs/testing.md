# 测试指南

本文档描述 `video-subtitle-ocr` 的测试分层、各层运行方法，以及最近一次
完整测试的记录。新版本发布前建议按顺序完整跑一遍（另见
[packaging.md](packaging.md) 的“版本发布检查清单”）。

## 目录

- [测试分层总览](#测试分层总览)
- [1. 单元测试（离线 mock）](#1-单元测试离线-mock)
- [2. CLI 端到端冒烟](#2-cli-端到端冒烟)
- [3. GUI 离屏冒烟](#3-gui-离屏冒烟)
- [4. 流水线回归基准](#4-流水线回归基准)
- [5. DEB 安装/卸载测试（无 root）](#5-deb-安装卸载测试无-root)
- [测试记录](#测试记录)

## 测试分层总览

| 层级 | 对象 | 依赖 | 耗时 |
| --- | --- | --- | --- |
| 单元测试 | `core/`、`utils/` 等纯逻辑 | 无需 OCR 引擎（离线 mock） | ~3 s |
| CLI 冒烟 | `cli.py` 四段流水线 | OCR 引擎 + 测试视频 | ~10 s（paddle）/ ~100 s（rapid） |
| GUI 冒烟 | `main_window/` 包界面装配 | PySide6（离屏平台） | ~5 s |
| 回归基准 | 准确率 / 耗时 / 内存 | 同 CLI | ~10 s |
| deb 安装测试 | 打包树 + 维护脚本 | dpkg（无 root 可用） | ~1 min（venv 预置） |

测试统一使用项目虚拟环境 `.venv`（开发依赖见 `requirements-dev.txt`：
`pytest`、`ruff`）。以下命令均在 `video_subtitle_ocr/` 目录下执行。

## 1. 单元测试（离线 mock）

```bash
python -m pytest tests/ -v
```

覆盖要点：

- `test_ocr_optimizer.py`：跳帧/投票/批处理回退/缓存淘汰/取消语义；
- `test_llm_client_backoff.py`：LLM 调用的**有界退避**（最大次数、单次与
  总等待上限、仅对 429/限流类错误重试，耗尽后返回可恢复错误）；
- `test_vlm_refine.py`：VLM 复核的触发条件、JSON 修复与异常隔离；
- `test_boundary_refine.py` / `test_pipeline_refine_integration.py`：
  字幕边界精修（含真实视频集成用例）；
- `test_subtitle_roi_suggester.py` / `test_roi_pose_tags.py`：
  ROI 自动建议与 `\pos`/`\frz` 标签计算；
- `test_engine_manager.py` / `test_engine_paddle_selection.py` /
  `test_engine_rapid.py`：引擎注册、选择与档位路由。

全部测试离线运行，不需要安装 PaddleOCR/RapidOCR，也不访问网络。
个别标记为需要真实引擎的用例会在缺引擎时自动 `SKIP`。

## 2. CLI 端到端冒烟

使用 `benchmarks/test_video_subtitle.mp4`（含 4 条已知字幕，ground truth 见
`benchmarks/gt.json`）：

```bash
python cli.py --version
python cli.py benchmarks/test_video_subtitle.mp4 -o /tmp/out.ass
grep '^Dialogue' /tmp/out.ass        # 应为 4 条，文本与 gt.json 一致

# 备用引擎（ONNX，CPU 较慢约 100 s）：
python cli.py benchmarks/test_video_subtitle.mp4 --engine rapid -o /tmp/out_rapid.ass

# 引擎自动选择（auto → 已安装引擎中的注册表默认，通常为 paddle）：
python cli.py benchmarks/test_video_subtitle.mp4 --engine auto -o /tmp/out_auto.ass
```

## 3. GUI 离屏冒烟

无显示器环境验证主窗口可正常装配与显示：

```bash
QT_QPA_PLATFORM=offscreen python -c "
import sys; sys.path.insert(0, '.')
from PySide6.QtWidgets import QApplication
from main_window import SubtitleOCRGUI
app = QApplication([]); w = SubtitleOCRGUI(); w.show()
print('GUI OK:', w.windowTitle())"
```

预期输出 `GUI OK: 视频字幕 OCR 工具`，无 Qt 插件/组件导入错误。

## 4. 流水线回归基准

对比历史基准报告，防止模型或流水线改动造成准确率/性能回退：

```bash
python scripts/benchmark_regression.py \
    --video benchmarks/test_video_subtitle.mp4 \
    --ground-truth benchmarks/gt.json \
    --output my_run.json --baseline benchmarks/ppocrv6_run2.json
```

判定标准：行准确率不得低于基线；`ocr_calls` 显著变化需解释；
耗时/内存变化在 ±20% 内视为噪声（CPU 机器波动较大）。

## 5. DEB 安装/卸载测试（无 root）

完整步骤见 [packaging.md](packaging.md) 的“安装测试（无 root 环境）”。
要点：

1. `./build_deb.sh` 构建后，在 staging 根中用
   `dpkg --root=... --force-not-root --force-script-chrootless -i` 安装；
2. **venv 预置加速**：postinst 会检测已存在的 venv 并跳过创建。将开发机
   已装好依赖的 `.venv` 软链到 staging 根，可跳过数百 MB 的 wheel 下载，
   只验证维护脚本与启动器逻辑：

   ```bash
   ln -s "$PWD/video_subtitle_ocr/.venv" \
         /tmp/instroot/opt/apps/video_subtitle_ocr/.venv
   ```

   注意：这会跳过“全新 venv 创建 + 全量 pip 安装”路径；发布前如改动过
   postinst 的安装逻辑，应至少在干净 staging 根上完整跑一次（联网）。
3. 用 `VSO_APP_DIR` 指向 staging 根验证 `--version` 与真实视频 OCR；
4. `dpkg -r` 验证 remove 保留 venv、`dpkg -P` 验证 purge 全部清除。

## 测试记录

### 2026-09-08 · v2.2.0 · Linux 7.0.0-30-generic x86_64 · Python 3.12.3

| 项目 | 结果 |
| --- | --- |
| 单元测试 | **101 passed, 1 skipped**（3.0 s） |
| CLI paddle 端到端 | 4/4 条字幕正确，450 帧 / 123 次 OCR 调用 / 9.5 s |
| CLI rapid 端到端 | 可用，4 条字幕均识别（约 97 s，存在行切分差异，备用引擎预期内） |
| CLI `--engine auto` | 可用（修复了 auto 未注册导致的 RuntimeError 后复测通过） |
| GUI 离屏冒烟 | 通过（`GUI OK: 视频字幕 OCR 工具`） |
| 回归基准 vs `ppocrv6_run2` | 行准确率 100%（持平）；ocr_calls 123（持平）；峰值内存 −230 MB；总耗时 +0.45 s（噪声范围） |
| deb 构建 | `video-subtitle-ocr_2.2.0_all.deb` 构建成功，`dpkg-deb -I/-c` 校验通过 |
| deb 安装测试 | rootless 安装（**全新 venv + 全量 pip 联网安装路径实测**，非软链加速）→ CLI `--version`/真实视频 OCR（5 条）→ GUI 离屏冒烟 → remove（启动器删除、venv 保留）→ purge（目录与数据库记录全清）全流程通过 |

### 2026-09-10 · v2.3.0 · Linux 7.0.0-30-generic x86_64 · Python 3.12.3

> 本版主要变化：`main_window.py` → `main_window/` 包、`core/subtitle_generator.py` →
> `core/subtitle_generator/` 包（纯结构重构，零行为变化）；测试数量 101 → 170。

| 项目 | 结果 |
| --- | --- |
| ruff | 未配置规则集，默认规则报 1135 条既有风格项（UP/BLE001 等），非缺陷；打包前不做批量改写 |
| 单元测试 | **170 passed, 1 skipped**（3.7 s；skip 为需真实 OCR 引擎的用例） |
| CLI paddle 端到端 | 5/5 条字幕正确（含淡入淡出用例），450 帧 / 8.1 s |
| CLI rapid 端到端 | 可用，85 s，5 条字幕文本均可识别（6 行 Dialogue，存在行切分差异，备用引擎预期内） |
| CLI `--version` | `video-subtitle-ocr-cli 2.3.0` |
| GUI 离屏冒烟 | 通过（`GUI OK: 视频字幕 OCR 工具`） |
| 回归基准 vs `ppocrv6_run2` | 行准确率 100%（持平）；头尾误差 avg 0.007/0.027 s、max 0.033 s（≤1 帧@30fps，达标）；ocr_calls 123→133、frames_filled 450→475（**边界精修默认启用**所致，非回退）；`restore` 时段 0.003→7.79 s 为基准脚本 t3~t4 把边界精修耗时计入所致（新功能工作量，非坐标还原回退）；总耗时 9.2→15.6 s 同源 |
| deb 构建 | `video-subtitle-ocr_2.3.0_all.deb` 构建成功，`dpkg-deb -I/-c` 校验通过 |
| deb 安装测试 | rootless 安装（**全新 venv + 全量 pip 联网安装路径实测**，非软链加速）→ CLI `--version`/真实视频 OCR（5 条）→ GUI 离屏冒烟 → remove（启动器删除、venv 保留）→ purge（目录与数据库记录全清）全流程通过 |

### 2026-09-11 · v2.4.0 · Linux 7.0.0-30-generic x86_64 · Python 3.12.3

> 本版主要变化：ROI 画布编辑（「编辑（拖动调整）」模式）+ 倾斜字幕带多边形 ROI 检测；
> 测试数量 170 → 189。新增模型链路与多语言专项检查。

| 项目 | 结果 |
| --- | --- |
| 单元测试 | **189 passed, 1 skipped**（3.7 s；skip 为需真实 OCR 引擎的用例） |
| CLI paddle 端到端 | 5/5 条字幕正确（含淡入淡出用例），8.2 s |
| CLI rapid 端到端 | 可用，75.5 s，5 条字幕文本均可识别（6 行 Dialogue，行切分差异，备用引擎预期内） |
| CLI `--engine auto` | 可用，7.7 s，5 条字幕正确 |
| CLI `--version` | `video-subtitle-ocr-cli 2.4.0` |
| GUI 离屏冒烟 | 通过（`GUI OK: 视频字幕 OCR 工具`） |
| 回归基准 vs `ppocrv6_run2` | 行准确率 100%（5/5，持平）；头尾误差 avg 0.007/0.027 s、max 0.033 s（≤1 帧@30fps，达标）；ocr_calls 133、frames_filled 475（与 2.3.0 持平）；总耗时 15.3 s（噪声范围） |
| 模型下载（专项） | 未缓存模型按需下载正常：`cyrillic_PP-OCRv5_mobile_rec`（俄语回落路径）、`PP-OCRv6_tiny_det/rec`、`PP-OCRv6_small_det/rec` 均自动下载（aistudio 404 后自动回落 modelscope 源）并完成推理 |
| 模型切换（专项） | 语言路由（ch/en/japan→v6；korean/russian/arabic→v5 多语言）、档位切换（tiny/small/auto）、引擎切换（paddle↔rapid↔paddle）后推理均正常；探测图 2/2 行识别（置信度 ≥0.99） |
| preload 脚本（专项） | `preload_models.py` 正常完成 ch/en 预下载流程 |
| 多语言切换（专项） | 界面语言跟随系统 locale：`zh_CN`→中文（源文案）、`ja_JP`→日语（含 2.4.0 新增文案的翻译）、其余 locale（en_US/zh_TW/ko_KR）回退中文源文案；修复 deb 包描述对本地化语言的过度声明（实际 zh/ja） |
| i18n | `pyside6-lupdate -tr-function-alias translate+=_tr` 刷新 .ts，补译新增文案后 `app_ja_JP.qm` 有效翻译 119 → 131 条，lrelease 编译并经 QTranslator 实测加载 |
| deb 构建 | `video-subtitle-ocr_2.4.0_all.deb` 构建成功（5.3 MB，143 项），`dpkg-deb -I/-c` 校验通过，无 tests/.venv/__pycache__ 泄漏 |
| deb 安装测试 | rootless 安装（**全新 venv + 全量依赖安装路径**，wheel 命中本机 pip 缓存；postinst 自 2.3.0 未改动）→ CLI `--version`（2.4.0）/真实视频 OCR（5 条）→ GUI 启动器与离屏冒烟 → 日语 .qm 从安装目录加载 → remove（启动器删除、venv 保留）→ purge（目录与数据库记录全清）全流程通过 |

### 2026-09-12 · v2.5.0 · Linux 7.2.4-070204-generic x86_64 · Python 3.12.3 · 32 核 / 91GB

> 本版主要变化：进程分片并行（长视频提速）+ `--engine auto` 时引擎选项被忽略的
> 修复。测试数量 214 → **267**。分片基础设施（等价性、容错、旁路）全部达标；
> **CPU 分片的实测提速未达设计目标（≥4×）**，实测数据如实记录如下——该工作
> 负载（合成噪声视频，逐帧触发 OCR）的瓶颈是 batch-1 小模型推理的内存带宽，
> 加进程无法线性扩展；真正的提速杠杆见文末"后续优化项"。

| 项目 | 结果 |
| --- | --- |
| 单元测试 | **267 passed, 1 skipped**（含分片规划 16、接缝合并 8、worker 协议 14、协调者容错 5、CLI 参数 6、引擎选项回归 3） |
| 阶段函数重构（T1） | 213→214 全绿；重构前后同机对照：行准确率 100%、ocr_calls 133（475 填充）完全一致，总耗时 6.01→6.06s（±20% 噪声带内） |
| **等价性门禁** | 基准视频（16s）真实 spawn×3 vs 单进程：生成 ASS 事件完全一致 |
| **接缝敌意夹具** | 150s 合成视频（跨缝字幕/缝上淡入/快对白/同文重现），`run_seam_equivalence.py`：分片（3 worker，缝在 50s/100s）vs 单进程 **14/14 事件完全一致**（真实并行路径） |
| 红线指标 | 回归基准行准确率 100%（5/5）、头尾误差 ≤0.033s 维持 |
| CLI `--version` | `video-subtitle-ocr-cli 2.5.0` |
| 性能 · 24min 720p 合成视频（tiny 档，450 事件全识别） | 32 核：CLI 无精修 单进程 159s → 分片 114s（**1.39×**）；GUI 含精修 单进程 470.9s → 分片 338-433s（**1.09-1.39×**）。taskset 限 8 核：178s vs 183s（**0.97×，持平**） |
| 性能 · 干扰定位 | 单个 spawn worker 独跑 84.5s（与进程内 solo 一致，spawn 无额外开销）；6 实例并发时单 worker 退化至 330-433s（2-5×/实例）。瓶颈为 batch-1 推理的内存带宽/缓存争用（OMP_WAIT_POLICY=PASSIVE 仅 433→363s），非锁/启动/spawn 问题 |
| 引擎选项修复的实测影响 | 修复前 `--engine auto` 下 `--model-tier tiny` 被静默忽略、实际用 medium：同视频无精修 medium ≈ 630-707s vs tiny 159s（**受影响用户的真实提速 ≈ 4×**）；修复后 stderr 确认 tiny 生效 |
| **Phase 2 · 并行精修**（同日追加） | 24min 视频精修阶段 290.3s → **202.6s（1.43×）**、GUI 管线全程 470.9s → **349.3s（1.35×）**：边界任务化（原始结果上扫描 job → 按任务序合并写入）+ 每线程独立引擎/VideoCapture（≤4 线程，按核数/内存自适应）+ ground window 一次 `predict_batch`。等价验证：桩引擎单元测试（文本/时间戳级与 legacy 一致）、管线级双门禁通过（分片 worker 仍用串行精修，避免引擎数 = worker×线程的内存放大） |
| Phase 2 · 已试并否决 | 回走探测批处理（每 4 帧一次推测性 `predict_batch`）：精修 222.7 → 318.9s，**反而变慢**——短回走在停止点之外的浪费超过批处理节省，CPU 上 `predict(list)` 也不比逐帧省。已回退，代码不入库 |
| **Phase 3 · GPU 感知批量通道**（同日追加，无 GPU 硬件验证） | ①`predict_batch` 按设备分块（GPU 12 / CPU 6，实例内缓存探测结果），投票采样与精修 ground window 走统一通道；②GPU 下并行精修线程数收敛至 ≤2（VRAM 保护），CPU 维持 ≤4；③架构结论入 `optimization_analysis.md`：锚帧/二分 OCR 为决策关键串行链，"先选帧后批量"不可行，P7 需硬件实测暂缓。**CPU 验证**：275 单测全绿（新增分块对齐/计数/GPU 尺寸/GPU 线程帽 5 项）、回归基准与双等价门禁通过、ocr_calls 133 不变；**GPU 收益预期数倍但未实测**，待硬件落地后按 testing.md 流程补测 |
| i18n（Phase 1-3 新增文案） | lupdate 刷新（349 条，新增精修并行提示），zh/ja 补译并 lrelease 编译（ja 118 / zh 86 条有效） |
| man 页 | CLI man 新增 `--workers` 说明、版本头 2.4.2 → 2.5.0；GUI man 版本头同步 |
| deb 构建 | `video-subtitle-ocr_2.5.0_all.deb` 构建成功（5.4MB，152 项），`dpkg-deb -I/-c` 校验通过：含 chunk_*/refine_executor 全部新模块，无 tests/.venv/__pycache__ 泄漏 |
| deb 安装测试 | rootless 全新 venv + 全量 pip 依赖安装路径（wheel 命中本机缓存）→ CLI `--version`（2.5.0）→ 真实视频 OCR（5/5 条，3.8s）→ GUI 离屏冒烟（`GUI OK: 视频字幕 OCR 工具`）→ remove（启动器删除、venv 保留）→ purge（应用目录全清、数据库无记录）全流程通过 |
| 短视频旁路 | 16s 视频自动走单进程原路径（规划器 min_total_s=600），行为与 2.4.2 一致 |
| 后续优化项 | ①精修探测批处理 + 边界级并行（Phase 2，精修占 GUI 管线 62% 且为逐帧延迟型负载）②GPU 批处理推理（Phase 3，设计文档预期的主要杠杆）③P7 检测/识别解耦评估 |

### 2026-09-12 · v2.5.0 CLI 链路复测（真实 1080p 样本 + 修复验证）

> 发布前用真实动画样本（1920×1080 @23.976，174.7s / 4188 帧，人工逐帧校对
> 字幕 49 条事件）复测 CLI 全链路，发现并修复两个问题（场景预设过滤丢对白、
> `--preset` 无效 id 迟报），修复后 284 单测全绿。测试视频与人工字幕位于
> 仓库外 test/ 目录。

| 项目 | 结果 |
| --- | --- |
| CLI 基础链路 | `--version`（2.5.0）/`--help`/视频不存在（exit 1）/ROI 格式错（exit 2）均正常；默认条带 = 帧高 20%（216px）正确 |
| CLI 端到端（默认条带，medium 档） | 47 事件，192s；与人工字幕对比 46/49 直接匹配、0 多余事件，边界偏差 max 0.02s（1 处 0.19s 为「好熱…」尾部省略号外观差异）；同轨双人对白与双行字幕按设计以单事件 `\N` 输出，无内容丢失 |
| 引擎选项修复实测 | `--engine auto --model-tier tiny` 后 stderr 确认创建 `PP-OCRv6_tiny_*`（45s，medium 为 192s）；默认档创建 medium |
| 其余参数 | `--roi`（GUI 自动检测值 451,875,1050,172）、`--workers 2`（<600s 按设计旁路单进程，输出与单进程逐事件一致）、`-q`（无进度输出 exit 0）、`--template`（样式完整采用模板 7 个 Style）均正常 |
| `--scan` | 24 采样检出底部字幕带 + 场景文字，无水印；`--watermark-mode keep` 仅报告不移除 |
| **修复①场景预设丢对白** | `--scan --preset anime`：修复前 48 → 1 条（2.4.2 同样复现，非 2.5.0 回归；根因为分类器视觉层用未测量默认值扣分）。修复后 47 条，对白保留 |
| **修复②`--preset` 无效 id** | 参数解析即报错 exit 2（修复前先跑完帧提取+全片 OCR 再在阶段 4 失败） |
| GUI | 离屏冒烟通过（zh locale）；`LANGUAGE=ja_JP` 翻译器从安装目录加载成功；启动器 .desktop、CLI/GUI man 页（含 `--workers`、版本头 2.5.0）、i18n .qm 齐全 |
| 长视频分片（698.7s，4188×4 帧拼接） | `--workers 2 --model-tier tiny` 走 2 窗口分片路径成功，输出事件与 `--workers 1` 单进程完全一致（等价性验证，见下） |

### 2026-09-12 · v2.4.2 · Linux 7.2.4-070204-generic x86_64 · Python 3.12.3

> 本版主要变化：CLI 默认底部条带由固定 160px 改为帧高 20%（≥160px，修复 1080p
> 及以上分辨率双行字幕首行被条带截掉而整行丢失）；`cli.py __version__` 补齐与
> DEBIAN/control 的同步（2.4.1 发布时遗漏，且 2.4.1 无测试记录，本记录一并覆盖）。
> man 页同步默认 ROI 描述、版本头（2.2.0 → 2.4.2）并补齐 `--scan` 系列选项。
> 测试数量 189 → 214（2.4.1 期间新增的用例）。

| 项目 | 结果 |
| --- | --- |
| 单元测试 | **214 passed, 1 skipped**（8.9 s；skip 为需真实 OCR 引擎的用例）；ruff 未装于 venv，跳过 |
| CLI paddle 端到端 | 5/5 条字幕正确（含淡入淡出用例），8.0 s（仓库）/ 8.4 s（staging 安装） |
| CLI `--version` | `video-subtitle-ocr-cli 2.4.2`（修复前报 2.4.0） |
| 真实视频专项（1080p 动画，174 s / 4188 帧，含双行字幕） | 修复前（固定 160px）：49 条人工字幕中 2 条双行事件首行整行丢失、1 处乱码碎裂；修复后（216px）：49 条全部覆盖（双行以单事件 `\N` 输出，时间轴帧级吻合），0 缺失 0 多余；剩余 3 处外观级差异（尾部省略号 ×2、行首破折号 ×1）。CPU 全片耗时 129 s → 190 s（条带增高所致，预期内） |
| GUI | 离屏冒烟通过；X11 真机启动渲染正常、加载视频/后台视频类型检测/closeEvent 线程收尾均正常退出 |
| 回归基准 vs `ppocrv6_run2` | 行准确率 100%（5/5，持平）；头尾误差 avg 0.007/0.027 s、max 0.033 s（≤1 帧@30fps，达标）；ocr_calls 133、frames_filled 475（与 2.4.0 持平，基准用固定 ROI 不受默认条带影响）；总耗时 15.9 s（噪声范围） |
| deb 构建 | `video-subtitle-ocr_2.4.2_all.deb` 构建成功（5.4 MB，145 项），`dpkg-deb -I/-c` 校验通过，无 tests/.venv/__pycache__ 泄漏；包内 cli.py 已含新默认条带与 2.4.2 版本号 |
| deb 安装测试 | rootless 安装（**全新 venv + 全量 pip 联网安装路径实测**）→ CLI `--version`（2.4.2）/真实视频 OCR（5 条）→ GUI 离屏冒烟 → remove（启动器删除、venv 保留）→ purge（目录与数据库记录全清）全流程通过 |

### 2026-09-15 · v2.6.0 · Linux 7.2.4-070204-generic x86_64 · Python 3.12.3 · 32 核 / 91GB

> 本版主要变化：一键控制面板（简洁视图）+ 界面语言切换（简中/繁中/英/日 +
> 跟随系统），新增英语与繁体中文全量界面翻译并补齐日文历史缺口。测试数量
> 288 → **326**（新增 quick mode 17 项、i18n 22 项；`get_pipeline_options()`
> 字段与全部既有信号零删改）。

| 项目 | 结果 |
| --- | --- |
| 单元测试 | **326 passed, 1 skipped**（新增 `test_control_panel_quick_mode.py` 17 项：默认首屏/兼容性/条件显示/执行状态；`test_i18n_language.py` 22 项：locale 解析、qm 加载、三语内容、选择器持久化） |
| 一键面板（专项） | 默认 quick mode 首屏仅模板/语言/文字保留/主操作；高级组、LLM 组默认折叠；「完整设置 ↔ 简洁界面」互切不重建控件、不清空用户设置；进程分片、颜色门控等实现术语不出现在默认界面，完整模式下全部可达 |
| 条件显示（专项） | LLM 凭据区仅在任一 LLM 功能启用后显示，「刷新模型列表」还需已填 API Key；「预览检测效果」仅在颜色门控启用后显示（未确认前保持禁用） |
| 执行状态（专项） | 扫描/OCR 运行期间主按钮禁用并显示阶段文本，输入控件统一锁定；完成、失败、取消（含线程收尾兜底路径）统一恢复，条件化启用状态重新套用 |
| i18n · 机制 | 启动语言持久化于 QSettings `ui/language`（默认 auto 跟随系统 locale，zh_HK/MO/TW→繁中、ja→日文、en*→英文、其他→中文源文案）；「文件操作」组语言选择器切换后经确认自动重启生效 |
| i18n · 覆盖 | app_en.qm / app_zh_TW.qm 各 265 条有效翻译（英文自然表述、繁体台湾用语）；**补齐日文历史缺口 166 条**（327 条有效）；lupdate 同文本启发式恢复 ScanReviewDialog 旧译 1 条 |
| i18n · 修复 | `scan_review_dialog.py` 的 `_tr()` 包装因 lupdate 静态解析无法提取（变量实参，`-tr-function-alias` 对 Python 无效），ScanReviewDialog 上下文自 2.4.x 起从翻译目录丢失；改为字面量 `translate()` 调用后四种语言全部恢复 |
| 真实 GUI 冒烟 | 离屏 + `benchmarks/test_video_subtitle.mp4`：加载视频后自动检测 ROI（1 个字幕带）、检测后主按钮可用、运行锁定/取消恢复、语言切换与持久化等 **20/20 通过**；源码目录英语启动实测：主按钮/窗口标题全英文 |
| 回归基准 | 行准确率 100%（5/5）；`get_pipeline_options()` 19 字段完整、默认引擎 auto |
| deb 构建 | `video-subtitle-ocr_2.6.0_all.deb` 构建成功（5.4MB，152 项），`dpkg-deb -I/-c` 校验通过：含 4 语言 .qm（en 48KB / zh_TW 33KB 新增），无 tests/.venv/__pycache__ 泄漏 |
| deb 安装测试 | rootless 全新 venv + 全量 pip 依赖安装（wheel 命中本机缓存）→ CLI `--version`（2.6.0）→ 真实视频 OCR（5/5 条，8.33s）→ 安装目录 GUI 三语言冒烟（en/zh_TW/ja_JP 界面文案与窗口标题逐项断言）→ remove（启动器删除、venv 保留）→ purge（应用目录全清、数据库无记录）全流程通过 |

### 2026-09-15 · v2.6.1 CLI 链路复测（真实 1080p 样本 + 扫描倾斜误判修复）

> 发布后用仓库外 test/ 真实样本（1920×1080 @23.976，174.7s / 4188 帧，人工
> 字幕 49 条事件）复测 CLI 全链路，发现并修复 `--scan` 倾斜带误判缺陷；
> 一并纳入 quick panel ROI 绘制修复（06818a3，2.6.0 deb 构建之后提交）。

| 项目 | 结果 |
| --- | --- |
| 单元测试 | **331 passed, 1 skipped**（327 + 逐行倾斜投票回归用例 4 项） |
| CLI 基础链路 | `--version`（2.6.1）/`--help`/视频不存在（exit 1，含 -q）/ROI 格式错（exit 2）/无效 `--preset`（解析期 exit 2）均正常 |
| CLI 端到端（默认条带） | 47 事件 / 157s；与人工字幕对比 49 条全部有对应、0 缺失 0 多余：46 条直接匹配，ref31+32 双行与 ref38+39 双人同轨按设计合并 `\N` 单事件，ref43 视频实为单行（短语间宽空格，人工参考记作换行）；边界偏差 max 0.19s（「好熱」尾部省略号外观差异，与 2.5.0 记录一致） |
| `--roi`（限时 451,875,1050,172@10-30） | 19.7s，9 事件，时间窗正确截断于 ROI 边界 |
| `--template config.ass` | 样式完整采用模板（Source Han Sans Medium），事件数与默认链路一致（47） |
| **`--scan`（修复前）** | 24 采样把底部水平字幕带误判为倾斜带：全部点的最小外接矩形偏角 ~11° > 4° 阈值 → 输出对角多边形 ROI `[[646,551],[1084,368],[1497,1079],[1059,1079]]`，OCR 截断字幕左右文本，仅 28 事件（23/49 匹配且大量残缺，如「既然來了 不好好享受就虧大了」→「享受就虧大了」） |
| **`--scan`（修复后）** | 倾斜判定改逐行投票（倾斜行 ≥60% 且方向一致 ±8°，仅用倾斜行求外接矩形）；同参数检出底部带为轴对齐 rect [528,601,1049,479]，48 事件、46/49 直接匹配、49 条全部有对应，截断消失；不带预设时较高自动带内背景偶发误识为 `0`/`lo` 碎片混入 7 条事件（外观级） |
| **`--scan --preset anime`** | 47 事件，碎片经场景过滤后清零，46/49 直接匹配（3 处为双行/双人合并与单行空格的设计内差异）；仅 3 条残留 `0\N` 碎片（较高自动带背景误识被分类器判为 overlay，外观级）；边界偏差 max 0.02s |
| GUI 离屏冒烟 | 16/16 通过：窗口构建、真实样本加载（fps/帧数/分辨率）、ROI 自动加载（test/*_roi_autosave.json 恢复 1 个字幕带，主按钮初始禁用→检测后可用）、quick/完整模式互切且绘制模式组常显（06818a3 验证）、`load_language` en/zh_TW/ja_JP/zh_CN 四语、干净退出 |
| 回归基准 | 行准确率 100%（5/5）；ocr_calls=133、frames_filled=475 与 2.6.0 记录持平 |
| 安装态（2.6.0 deb） | `/usr/bin` 启动器 `--version` 正常；基准视频端到端 5/5 条 8.07s；`~/.paddlex` 模型缓存、`/opt/apps` 应用文件、man 页（2.6.1 版本头）、i18n 4 语 .qm 齐全 |
| deb 构建 | 2.6.1 版本号三处同步（cli.py / DEBIAN/control / man ×2）；`./build_deb.sh` 重建，`dpkg-deb -I/-c` 校验通过 |

### 2026-09-16 · v2.6.3 · Linux 7.2.4-070204-generic x86_64 · Python 3.12.3 · 32 核 / 91GB

> 本版主要变化：移动文字轨迹字幕（阶段一：手动框选平面四边形 → 单应逐帧跟踪 →
> 关键帧 OCR → 轨迹合成 `\move`/`\t` ASS，配套 `verify_motion_ass` 渲染偏差校验）
> 与轨迹屏幕亮度自适应（`\1c`/`\alpha` 链）；另含逐行倾斜投票、quick panel ROI
> 绘制入口恢复等修复。单元测试 350 → **466**（2.6.1 发布后累计）。
> DMG 手机邮件场景验收记录见
> `docs/superpowers/evidence/2026-09-16-motion-trajectory-ass-acceptance.md`。

| 项目 | 结果 |
| --- | --- |
| 单元测试 | **466 passed, 1 skipped**（52.85s；skip 为需真实 OCR 引擎的用例） |
| 静态检查 | `ruff check --select F` 全部通过（存量 1135 条风格债务维持现状，非缺陷） |
| README 预览 | 离屏 Qt 抓取 test/ 真实样本更新 preview1（对话字幕带 ROI）/preview2（手机屏幕多边形 ROI），同步刷新图注 |
| deb 构建 | 2.6.3 版本号四处同步（cli.py / DEBIAN/control / man ×2）；`./build_deb.sh` 构建成功（11.3MB，181 项），`dpkg-deb -I/-c` 校验通过；体积较 2.6.2（5.7MB）增长源于 docs/superpowers 轨迹字幕验收证据 PNG 随 docs/ 入包 |
| deb 抽查 | `dpkg-deb -x` 解包：cli.py `__version__` 2.6.3、`scripts/{motion_ass,track_plane,verify_motion_ass}.py` 在包内、4 语言 .qm 齐全、man 页 2.6.3、无 `__pycache__`/`tests/`/`.venv`/`*.pyc` 泄漏 |
| 发布 | 推送 main 后创建 GitHub Release v2.6.3，deb 附于 Release 资产 |

### 场景文字策略验收可复现命令（2026-09-17，Task 6）

> 四种场景文字显示策略（overlap/mask/external/whitespace）的端到端 ASS 生成、
> libass 烧录抽帧与对比方法。完整验收记录与产物见
> `docs/superpowers/evidence/2026-09-17-scene-text-policy-hardening/acceptance.md`。
> 工作目录 = 仓库根；`V` = 测试视频路径，`E` = 上述证据目录。

```bash
# 1) 四份 ASS：同一 quad、同一 OCR 引擎，仅策略不同（stderr 含 applied
#    policy、回退 note；whitespace 在该视频按设计回退 mask）
for mode in overlap mask external whitespace; do
  .venv/bin/python scripts/motion_ass.py --video "$V" \
    --quad-file /home/hope/Tools/video_subtitle_ocr/test/motion-quad.json \
    --start-frame 0 --end-frame 245 --ocr-engine rapid \
    --scene-text-policy "$mode" --out "$E/$mode.ass" \
    2> "$E/run-stderr-$mode.txt"
done

# 2) libass 烧录抽帧（相同帧号；select=eq(n,F) 按解码帧号精确抽样，
#    路径单引号包裹，同 scripts/verify_motion_ass.py 的做法）
ffmpeg -nostdin -hide_banner -loglevel error -y -i "$V" \
  -vf "ass='$E/mask.ass',select='eq(n,36)'" -vsync 0 -frames:v 1 "$E/mask-f36.png"

# 3) overlap 基线一致性：不带策略参数重跑，输出应与 overlap.ass 逐字节一致
.venv/bin/python scripts/motion_ass.py --video "$V" \
  --quad-file /home/hope/Tools/video_subtitle_ocr/test/motion-quad.json \
  --start-frame 0 --end-frame 245 --ocr-engine rapid --out /tmp/no-policy.ass
diff /tmp/no-policy.ass "$E/overlap.ass" && echo IDENTICAL

# 4) 对比方法：原帧 vs 烧录帧目检/差分（mask 无原字透出）、多帧 NoteBox
#    位置比对（external 上沿/列范围跨帧一致）、whitespace 与 mask 逐字节
#    diff（回退生效）、指标见证据目录 pixel-checks.json
```

静态主流水线等价命令：`cli.py "$V" --roi "604,282,713,796@0-10.26" --scene-text-policy mask -o out.ass`（全时段 mask 在移动场景会按移动门限降级 external，属静态路径边界行为；移动文字请用轨迹管线）。透视/杂色降级与单/多 ROI 读取对比的复现脚本口径记录在验收 md §3.5/§4。

### mask / mask_only（仅遮罩）验收可复现命令（2026-09-18）

> `mask`（遮罩 + layer-1 重渲染）与 `mask_only`（仅遮罩，原文写 `Comment:`
> 行供排版覆写）在 DMG 手机场景的端到端产物、libass 烧录抽帧与链尾
> +1 帧对齐修复的实证见
> `docs/superpowers/evidence/2026-09-18-mask-only-acceptance/acceptance.md`
> （含完整复现命令；基线 764 passed, 1 skipped + ruff F 全绿）。
