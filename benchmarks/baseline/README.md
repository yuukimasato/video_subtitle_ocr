# 基线存档（v2.7.3 → font-intel 开发起点）

本目录保存 `feature/font-intel-p1` 分支开工前的基线，供"全部新功能关闭时输出与基线一致"的回归门禁与哈希比对使用。

| 文件 | 内容 |
|---|---|
| `git_head.txt` | 基线 commit（main 分支 091e094，feat: v2.7.3） |
| `git_status.txt` | 开工前工作区状态（仅 3 份未跟踪方案文档，已先行 commit） |
| `test_report_v2.7.3.xml` | 全量 pytest JUnit XML：**1057 passed, 1 skipped**（67s，QT_QPA_PLATFORM=offscreen，Python 3.12 / .venv） |
| `hashes_v2.7.3.txt` | 源码/配置 sha256 清单（165 个文件：*.py、requirements*.txt、*.ts、*.desktop、config.ass，排除 .venv/.git/__pycache__） |

## 再生成方式

```bash
find . -path ./.venv -prune -o -path ./.git -prune -o -path ./__pycache__ -prune \
  -o -type f \( -name "*.py" -o -name "requirements*.txt" -o -name "*.ts" \
  -o -name "*.desktop" -o -name "config.ass" \) -print0 | sort -z | xargs -0 sha256sum
QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest -q --junitxml=benchmarks/baseline/test_report_<ver>.xml
```

## 回归门禁用法

每 Phase 结束：`sha256sum -c` 比对哈希清单中被新功能**未触及**的文件应全部一致；
`core/subtitle_generator/styling.py` 等被改文件须以"合规/翻译/识别全关"用例验证输出逐字节等价。
