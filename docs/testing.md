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
