# font-intel 分支发布前独立验证（2026-09-22）

本目录是对 `feature/font-intel-p1` 分支的**独立复现验证**记录（子代理逐项审计 + 主会话复现），补录开发计划审计指出的归档缺口。

## 1. 全量回归复现（T4.1）

- 命令：`QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest tests/ -q --junitxml=…`
- 结果：**1523 passed, 1 skipped（71.55s）**，与 T4.1 归档声明一致。
- 工件：`benchmarks/baseline/test_report_verification_20260922.xml`、
  `benchmarks/baseline/hashes_font_intel_branch_20260922.txt`（分支态 206 文件 sha256）。

## 2. G4 门禁：全部新功能关闭时输出与基线逐字节一致（T0.1/G4 补录）

- 方法：基线 commit `3e5a423`（benchmarks/baseline/git_head.txt 记录的开发起点）
  建 git worktree，与当前分支对同一测试视频（`benchmarks/make_test_video.py`
  生成，480 帧）各跑一次 CLI 流水线，均不带任何 font-intel/翻译/合规参数
  （即全部默认关闭）。
- 结果：**两份 ASS 输出逐字节一致**（sha256 均为
  `e32119bb254580ac7b293fc1c4e45290114685c5499d6a4814f7a08d7dd7dccf`），
  ocr_calls 均为 150。
- 工件：`g4_baseline_3e5a423.ass` / `g4_current_branch.ass` / `g4_pipeline_runs.log`。
- 复现方式：
  ```bash
  git worktree add /tmp/vso_g4_baseline 3e5a423
  .venv/bin/python benchmarks/make_test_video.py --video /tmp/g4_gate/test.mp4
  cd /tmp/vso_g4_baseline && <project>/.venv/bin/python cli.py /tmp/g4_gate/test.mp4 -o /tmp/g4_gate/baseline.ass
  cd <project> && .venv/bin/python cli.py /tmp/g4_gate/test.mp4 -o /tmp/g4_gate/current.ass
  diff /tmp/g4_gate/baseline.ass /tmp/g4_gate/current.ass   # 应为空
  ```
- 局限：测试视频为中文合成字幕（make_test_video.py 仅支持 `lang=zh`），
  日文片源用例未覆盖——已知的 T0.1 遗留，随发布前冒烟补齐。

## 3. 三条服务端链路 429/降级专项（T4.2 补录）

- 命令与结果见 `t42_429_verification.md` / `t42_429_pytest_output.txt`
  （65 passed）。覆盖：LLM API 429 有界退避、翻译链云端→Sakura 降级、
  ETL 结构化链批级降级、权重下载链 bounded_download 有界重试。

## 4. 外部数据源许可存档（T0.2 补录）

- `data_sources_manifest.json`：5 个上游数据/模型源的许可（SPDX）、
  HEAD commit、用途与使用策略，取自 GitHub API（2026-09-22）。
- 要点：wordshub/free-font 与 yuleshow/chinese-fonts 无 LICENSE 文件，
  仅提取事实性元数据并逐条标注 source，不转载原文；
  SakuraLLM 为 GPL-3.0，本项目仅经 OpenAI 兼容 API 调用，无代码链接。

## 5. 仍未完成（发布动作，需人工决策）

- T4.3：deb 构建 + 三入口冒烟（build_deb.sh 在仓库外工作区）。
- T4.4：版本 2.7.3 → 2.8.0、CHANGELOG 定稿（当前"未发布"段）、tag。
- 日文测试视频基线（上述第 2 节局限）。
