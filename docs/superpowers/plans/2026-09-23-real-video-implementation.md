# 真实视频质量第二轮 Implementation Plan

> **For agentic workers:** 按复选框逐项执行并自审；当前会话未提供 superpowers:subagent-driven-development / superpowers:executing-plans，不依赖不存在的技能，也不自动派发子任务。

**Goal:** 修复真实样本暴露的时序、遮挡和配置布局问题，保留可恢复基线和可重复的画面验收。

**Architecture:** 在现有静态/轨迹双路径内修复，不重写主流程。事件时间与内容先正确，遮挡消费明确的采样证据，CLI/GUI 使用一致配置，样式最后调整。

**Tech Stack:** Python 3.12、pytest、Paddle OCR、OpenCV、PySide6、FFmpeg/libass、Git。

**Spec:** [真实视频优化设计](../specs/2026-09-23-real-video-optimization-design.md)，必须与本计划一起阅读。

## Global Constraints

- 本文是下一轮待实施计划；本轮仅运行素材、分析和写文档。
- 生产源码基准为 `20231ba3f296c1149eddd691f33c962699e68f1d`，原始基线 `ead48f9` 和既有备份保留。
- 不修改 `/home/hope/Tools/video_subtitle_ocr/test/` 中的视频与 ROI；新输出使用新目录，不覆盖本轮证据。
- 不新增运行依赖、不自动安装字体、不调用真实翻译/VLM、不发布包；既有有界 429 重试保持。
- 优先修复错误时间和丢内容；不能靠删零时长事件、删除元信息或关闭整个能力通过验收。
- Scene 基础字号按行几何约束；旋转/颜色仍受 pose 控制；自定义模板优先。
- 分步提交，以 revert 或独立 worktree 回退；不用 reset --hard/clean。

## 执行顺序

| 顺序 | 子计划 | 独立交付 | 依赖 |
| --- | --- | --- | --- |
| 0 | 下方基线步骤 | 标签、bundle、校验、基准日志 | 无 |
| 1 | [A：时序与接管](2026-09-23-real-video-timing.md) | 共存行、活动集合策略、保守接管 | 基线 |
| 2 | [B1/B2：遮挡](2026-09-23-real-video-occlusion.md) | 采样契约与真实裁剪 | 基线；合并 A 后验证策略顺序 |
| 3 | [C：配置与布局](2026-09-23-real-video-config-layout.md) | 有效参数一致、几何字号 | 基线；A/B 完成后做最后真视频比较 |
| 3.5 | [B3/B4：亮度与轮廓](2026-09-23-real-video-occlusion.md) | 有色mask亮度绑定、逐帧前景轮廓 | B1/B2、C1 |
| 4 | [D：文字与背景运动解耦](2026-09-23-real-video-motion-isolation.md) | 静字不跟背景漂移、真运动保留 | A 的策略前证据；C 的配置贯通 |
| 5 | 下方整体验收 | 新 ASS、截图、统计、测试结果 | A/B/C/D |

## 0. 实施前确立基线

- [ ] 在仓库运行以下只读检查，确认没有其他人的未提交生产改动；若存在则先完整快照并记录归属，不覆盖。

```bash
cd /home/hope/Tools/video_subtitle_ocr/video_subtitle_ocr
git status --short
git rev-parse HEAD
git log -5 --oneline
git tag --list 'baseline/*'
```

- [ ] 固定经过本轮实测的源码提交，独立备份当前全部引用和此时文档。若同名标签/目录已经存在，先核对目标和校验，不能覆盖已有备份。

```bash
git tag -a baseline/real-video-optimization-20260923 20231ba3f296c1149eddd691f33c962699e68f1d -m 'Before real-video timing, occlusion and config fixes'
mkdir /home/hope/Tools/video_subtitle_ocr_backups/20260923-real-video-optimization
git bundle create /home/hope/Tools/video_subtitle_ocr_backups/20260923-real-video-optimization/repository.bundle --all
git bundle verify /home/hope/Tools/video_subtitle_ocr_backups/20260923-real-video-optimization/repository.bundle
git archive --format=tar -o /home/hope/Tools/video_subtitle_ocr_backups/20260923-real-video-optimization/source-and-docs.tar HEAD
```

- [ ] 对备份生成 SHA256SUMS：

```bash
cd /home/hope/Tools/video_subtitle_ocr_backups/20260923-real-video-optimization
sha256sum repository.bundle source-and-docs.tar > SHA256SUMS
sha256sum -c SHA256SUMS
cd /home/hope/Tools/video_subtitle_ocr/video_subtitle_ocr
```

在备份目录运行 `sha256sum -c SHA256SUMS`；记录标签目标、文档提交、当前分支、Python/FFmpeg/字体版本。保留本轮 evidence 中的 ASS 作为输出基准。
- [ ] 在改代码前运行 `QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest -q`，保存日志与 JUnit；预期与已记录的 1595 passed、1 skipped 一致。如不同先记录原因，不能把基线已有失败算成新改动造成。

恢复命令（需要回退时才执行）：

```bash
git worktree add --detach /home/hope/Tools/video_subtitle_ocr-real-video-baseline baseline/real-video-optimization-20260923
# 仓库损坏时，用新目录恢复：
git clone /home/hope/Tools/video_subtitle_ocr_backups/20260923-real-video-optimization/repository.bundle /home/hope/Tools/video_subtitle_ocr-real-video-restored
```

## 5. 最终回归和真实画面验收

- [ ] 完成 A/B/C/D 的针对性红绿回归，分别提交；记录每个提交 hash、失败复现和通过结果。
- [ ] 运行一次全量验证：

```bash
QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest -q
.venv/bin/python -m ruff check --select F . --exclude docs
git diff --check
```

- [ ] 将本轮运行目录的 `run_cases.py`、`render_review.py`、`inputs.json` 复制到同层新目录 `test_run/visual_review_after_fixes/`，创建 `logs/outputs/frames`；在新目录运行四个主样本。脚本通过所在目录定位仓库，不可直接在 Git evidence 归档位置执行。按 `all_runs.json` 的邮件控制命令另跑一次，输出改到新目录。有效参数必须与本轮相同，避免升级模型来掩盖回归。
- [ ] 新目录执行 `render_review.py mail phone11 phone12 opening4k mail_protected`；额外复用 `extra_frames.py` 获取 f222，并补 f168/f120/f144。保留全分辨率原图、叠加图、纯 ASS，源图哈希应与本轮一致。
- [ ] 对 ASS 做结构检查，以下可直接作为独立检查脚本核心；失败时报告具体文件和 Dialogue 原行，不删除失败记录：

```python
from pathlib import Path

def cs(value):
    h, m, s = value.split(':')
    return round((3600 * int(h) + 60 * int(m) + float(s)) * 100)

for path in Path('outputs').glob('*.ass'):
    rows = [line.split(':', 1)[1].strip().split(',', 9)
            for line in path.read_text(encoding='utf-8-sig').splitlines()
            if line.startswith('Dialogue:')]
    bad = [row for row in rows if cs(row[2]) <= cs(row[1])]
    assert not bad, (path, bad[:10])
```

- [ ] 人工逐项填写：phone12 共存行和小字位置；phone11 f24/f240/f527 活动内容（包括设计列出的缺失短句）；邮件无遮挡、切镜、遮挡三类帧；4K f288/f480/f959 上下歌词及后半段静态回退。字体替代与真正几何偏差分开记录。
- [ ] 追加用户指定验收：phone11同一文字实例稳定片段无move/几何t（允许亮度t）；真移动文字不被锁死；邮件有色mask在明/暗两段与背景同亮度且不靠alpha露底，手部轮廓同时裁文字和有色mask。用户路径只作对照，不强制套用全片。
- [ ] 定量阈值：合成同屏行完整保留、零非正时长；策略逐时段活动身份集合完全一致；遮挡合成图目标区新增字形像素减少至少80%，未遮挡对照区差异≤1%；合成动画切片绝对位置误差≤1px、亮度通道误差≤2；有效 small 参数在两路径完全一致。真实图不要求逐像素相等，逐帧审阅不能被这些合成指标替代。
- [ ] 更新新验收记录并提交。若 phone11 静态路径仍未识别出指定可读短句，保留失败截图、追踪缺失最早阶段并进行最小修复后重验；在解决前不能标记整体完成。不直接扩大到全新多锚点架构。

## 覆盖检查

V1→A1；V2→A2/A3；V3→B1/B2；V4→C1；V5→C2；V6→D1；V7→B3/B4。性能数据仅记录，内存重构、边界精修统一、多锚点识别和分段混合接管不在本轮承诺范围。截图证明只覆盖抽查帧，最终记录必须同时说明未测范围。
