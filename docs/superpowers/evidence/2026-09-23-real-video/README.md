# 2026-09-23 真实视频证据索引

生产源码：`20231ba3f296c1149eddd691f33c962699e68f1d`。本目录是本轮新生成的证据，不是历史测试报告。

完整原始素材、全分辨率 PNG 和运行副本位于 `/home/hope/Tools/video_subtitle_ocr/test_run/visual_review_20260923/`；本目录只归档精选四舍五入帧点的缩略对照、ASS 和可复现数据。视频帧号从0开始、实际时间为 `frame / (24000/1001)`。

## 运行与诊断

| 文件 | 用途 |
| --- | --- |
| [summary.json](summary.json) | 五次运行事件数、零时长数、iclip数、耗时、峰值RSS、ASS哈希 |
| [source_manifest.json](source_manifest.json) | 四个原视频及ROI完整路径、SHA256和ffprobe信息 |
| [runs.json](runs.json) / [all_runs.json](all_runs.json) | 主运行原始命令/环境；后者另含重建的邮件控制命令 |
| [environment.json](environment.json) / [font_render.log](font_render.log) | Python/FFmpeg/字体查询，以及libass实际替代字体 |
| [zero_duration_repro.json](zero_duration_repro.json) | 不同行被截零的最小源码复现输出 |
| [occlusion_diagnostics.json](occlusion_diagnostics.json) | 已检测的9帧、行框、事件时间和漏采结果 |
| [run_cases.py](run_cases.py) | 四个主样本顺序运行器，单例timeout=1200秒 |
| [render_review.py](render_review.py) / [extra_frames.py](extra_frames.py) | 原时间轴渲染和额外邮件f222对照 |
| [diagnose.py](diagnose.py) | 进程内观察遮挡采样；没有修改生产源码 |

`*_samples.json` 保存抽查帧时刻及当时有效ASS事件。`inputs.json` 保存截图秒数与视频路径。日志和ASS直接对应如下：

| 样本 | ASS | 日志 | 抽查时间 |
| --- | --- | --- | --- |
| 邮件 | [mail.ass](mail.ass) | [mail.log](mail.log) | 0、2、5、6、6.5、7、8、8.5、9、9.5、10s附近 |
| 邮件开亮度/遮挡 | [mail_protected.ass](mail_protected.ass) | [mail_protected.log](mail_protected.log) | 2、6.5、9s及f222 |
| 静止聊天/移动背景11 | [phone11.ass](phone11.ass) | [phone11.log](phone11.log) | 1、10、22s附近 |
| 切换聊天12 | [phone12.ass](phone12.ass) | [phone12.log](phone12.log) | 4.5、14、40s附近 |
| 4K双语歌词 | [opening4k.ass](opening4k.ass) | [opening4k.log](opening4k.log) | 12、20、40s附近 |

## 关键截图

三联图顺序为原视频、原视频+ASS、灰底纯ASS。字体环境在同一张图的两种ASS渲染中一致。

- [聊天11：10秒完整画面](phone11_f240_full.jpg)：旁注的位置、固定十行内容。另有 [1秒](phone11_f24_comparison.jpg)、[22秒](phone11_f527_comparison.jpg) 手机局部图；局部裁图不含完整旁注，必须与全图一起阅读。
- [聊天12：14秒](phone12_f336_comparison.jpg)、[40秒](phone12_f959_comparison.jpg)：时间/已读小字被放大，与主正文重叠。
- [邮件：2秒](mail_f48_comparison.jpg)、[9秒基础配置](mail_f216_comparison.jpg)、[9秒开启保护](mail_protected_f216_comparison.jpg)、[f222检测命中时](mail_protected_f222_comparison.jpg)：亮度与遮挡区分。
- [4K：12秒条带](opening4k_f288_bands.jpg)、[40秒条带](opening4k_f959_bands.jpg)：依次是源上/下、叠加上/下、纯ASS上/下，便于观察尺寸和垂直偏移。

## 重现方式

不要从此 Git 归档目录直接运行脚本：原脚本按 `test_run/<目录>/` 的层级定位仓库。复跑时创建 `/home/hope/Tools/video_subtitle_ocr/test_run/visual_review_after_fixes/`，复制脚本与inputs.json，创建logs/outputs/frames子目录。运行器使用同目录输出，不覆盖这里的ASS和日志。

```bash
cd /home/hope/Tools/video_subtitle_ocr/test_run/visual_review_after_fixes
/home/hope/Tools/video_subtitle_ocr/video_subtitle_ocr/.venv/bin/python run_cases.py
/home/hope/Tools/video_subtitle_ocr/video_subtitle_ocr/.venv/bin/python render_review.py mail phone11 phone12 opening4k
```

邮件控制按 all_runs.json 的 mail_protected 命令执行并把输出/日志改为新目录；之后执行 `render_review.py mail_protected` 与 `extra_frames.py`。diagnose.py会重新运行邮件运动路径，不是只读JSON的统计脚本。素材/ROI不变，生产改动后模型与分段数量可能改变。

[最终优化方案](../../../FINAL_OPTIMIZATION_DESIGN.md) · [最终实施计划](../../../FINAL_IMPLEMENTATION_PLAN.md)

`SHA256SUMS` 覆盖此目录除自身外的归档文件；执行 `sha256sum -c SHA256SUMS` 可检验。

## 用户补充后的轮廓可行性试验

新增证据与五次生产CLI输出分开：这些是仓库外视觉试验，不代表产品代码已修复。

- [用户路径](user_hand_path.txt)、[用户路径三帧试验脚本](hand_path_review.py)、[f222对照](hand_path_f222.jpg)：红线为固定路径，绿色是失败的宽松种子GrabCut，会误选手机背景；右图证明固定iclip能执行，但不随手势更新会错位。
- [颜色种子+边缘试验脚本](hand_edge_demo.py)、[轮廓数据](hand_edge_demo.json)：只针对此动画手部的可行性实验。采用样本特定颜色阈值，不是通用手检测方案。
- [暗画面f216](hand_edge_f216.jpg)、[亮画面f222](hand_edge_f222.jpg)、[手势变化f228](hand_edge_f228.jpg)：左为当前帧轮廓，右为实际libass裁剪。分别有 [f216 ASS](mail_edge_demo_f216.ass)、[f222 ASS](mail_edge_demo_f222.ass)、[f228 ASS](mail_edge_demo_f228.ass)。这些ASS中的轮廓只用于对应单帧验证，不可当成全段跟踪字幕播放。
- 未完成功能：通用轮廓初始化、全片时序传播、未知状态回退、与有色mask/亮度的完整集成；均列入设计V7和计划B3/B4。

归档的font_render.log只去除了行尾空白以通过Git格式检查；完整原始日志仍在外层运行目录。
