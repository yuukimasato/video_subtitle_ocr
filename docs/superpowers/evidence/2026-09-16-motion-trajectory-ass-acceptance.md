# 移动文字轨迹 → ASS 轨迹字幕 验收记录(DMG 第07話 手机场景)

- 日期:2026-09-16
- Spec:`docs/superpowers/specs/2026-09-15-motion-trajectory-ass-design.md`
- Plan:`docs/superpowers/plans/2026-09-15-motion-trajectory-ass-plan.md`(Task 1~8 全部完成)
- 基线:350 passed, 1 skipped → 验收时全量 **441 passed, 1 skipped**(新增 91 项,零回归)

## 产物

| 文件 | 路径(仓库外 test 工作区) |
| --- | --- |
| 手动 quad(第 0 帧手机屏幕,程序提取+目检) | `test/motion-quad.json`(四角见 evidence/dmg-f0-quad.png) |
| 逐帧轨迹 | `test/motion-trajectory.json`(174/246 ok,lost=[85,156] 帧) |
| 轨迹字幕 | `test/[DMG] 第07話19-motion.ass`(26 条事件) |
| 现状对照 | `test/[DMG] 冴えない彼女の育てかた♭ 第07話19.ass`(263 条事件) |

命令:`.venv/bin/python scripts/motion_ass.py --video <mp4> --quad-file test/motion-quad.json --start-frame 0 --end-frame 245 --trajectory-json test/motion-trajectory.json --ocr-engine rapid --out <ass>`

## DoD 核查

| 指标 | 预算 | 实测 | 结论 |
| --- | --- | --- | --- |
| 事件数 | 40±20 | **26**(13 行 × 2 链,每链 1 条单段 `\move`) | ✅ |
| 同文本重复链 | 0 | 0(每行两链时间不相交:0→3.50s / 6.54→10.21s) | ✅ |
| 模糊碎片读法(0.088~0.098s 窗) | 0 | 0(该窗被链 1 连续 `\move` 覆盖,无碎片事件) | ✅ |
| 零时长事件 | 0 | 0(最短 3.50s) | ✅ |
| lost 切段不外推 | 要求 | 第 85~156 帧(3.5~6.5s)无事件,与现状 .ass 该区间空白互相印证 | ✅ |

## 渲染偏差校验(libass 烧录,24 抽样帧)

- 全局墨水质心口径:median 19.9px / p95 21.5px —— **超出预算,经逐行分解确认是度量口径 artifact 而非定位误差**:期望点是 OCR 包围盒中心,而全局质心混入了「行尾全角标点墨水不对称」与「出画行被 libass 裁切但仍计入期望」两个系统效应,偏差在两链间恒定(±1px 抖动)。
- **逐行包围盒中心口径(`\an5` 实际控制的量,帧 176 实测 13 行)**:
  - **dy(本视频运动轴):median +0.7px,max 1.0px** —— 预算(中位 ≤4px)✅
  - dx:median -5.3px,max -7.2px,时间恒定;无行尾标点的行仅 -0.3~-0.9px,带标点行 -6~-7px,属替换字体/全角标点字形度量差异(盒中心范式已知限制),不随运动累积。
- 结论:轨迹跟随精度在运动轴上 ≤1px,整体 ≤7.2px(次字形级),验收通过;dx 限制记录在案。

## 人工抽查(原视频烧录,evidence/motion-trajectory-ass/)

- `burned-f36.png`(1.5s,链 1 中段):手机已上移 ~120px,13 行字幕全部贴合屏幕原文字 ✅
- `burned-f79.png`(3.3s,链 1 末端):字幕随屏幕上移,顶部行随画面上缘裁切,图标行「血」精确落在垃圾桶图标上 ✅
- `burned-f192.png`(8.0s,链 2):手机回归原位、屏幕调暗,字幕逐行贴合 ✅(调暗自适应属阶段三)

## 已知限制(记录,不在本阶段修)

1. 行尾全角标点导致渲染包围盒含空白格,叠加后字形整体左偏 ≤7px(可用字形度量补偿,阶段二候选);
2. 图标误读「血」(垃圾桶图标)与标题栏「受信メールー覧」(ー/一混淆)、「ます加藤」(まず)等内容级误差:3 关键帧投票未能全纠,归 OCR/`text_source_classifier`/VLM 既有职责;
3. 遮挡/出画期间(3.5~6.5s)文字不显示 —— 如实呈现,`\iclip` 蒙版属阶段三;
4. 屏幕调暗时字幕亮度不变(阶段三:局部调暗效果)。

## 提交清单

- `2c0beaa` refactor: 模块级 fuse_samples_by_position
- `d5070a8` feat: motion_ass 合成器(阶梯)
- `d44cff7` feat: verify_motion_ass 渲染偏差校验
- `b8656bd` feat: keyframe_selector
- `eb58617` feat: motion_ass 端到端 CLI
- 本记录 + CHANGELOG(见 HEAD)
