# mask / mask_only(仅遮罩)真实视频端到端验收(2026-09-18)

- 素材:`test/[DMG] 冴えない彼女の育てかた♭ 第07話19.mp4`(1920×1080 @23.976,
  246 帧;帧 0–84 与 157–245 两段静止链,85–156 遮挡 lost)
- 轨迹:`test/motion-quad.json`(手机屏幕四角,轴对齐);OCR `rapid`
- 基线:单元测试 764 passed, 1 skipped;`ruff check --select F` 全绿;GUI 离屏冒烟通过

## 本轮修复(验收中发现并修正)

**矩形遮罩链尾 +1 帧延伸**:`core/scene_text_policy.py` 的
`_mask_events_for_block` 此前链尾结束时间取尾帧自身 `time_sec`,而文本事件
已经过 motion_ass 的「链尾 +1 帧距」修复——链尾最后一帧文本仍在而遮罩已消失,
原字透出约 1 帧时长。现对齐同款修复(末段 `t1 = _next_frame_time(...)`,
多边形/external/whitespace 路径原本已覆盖):真实数据上遮罩与文本事件结束
时间逐条一致(3.54s / 10.26s),帧 84(链 1 尾帧 t=3.504)遮罩仍覆盖。

另修两处 lint:`core/screen_luma.py` 补 `Dict` 导入(F821)、
`tests/test_review_fixes_2.py` 删未用导入(F401)。

## 事件结构(rapid,246 帧)

| 模式 | 事件 | applied | 说明 |
| --- | --- | --- | --- |
| mask | 36 = 12 条 layer-0 `\p1` 遮罩(6 块 × 2 链)+ 24 条 layer-1 文本 | mask | 「血」图标行被融合噪声过滤剔除,13→12 行、7→6 块(2026-09-17 基线为 40 事件的既知差异) |
| mask_only | 36 = 同上 12 条遮罩 + 12 条 `Comment:` 原文(layer 1) | mask_only | 播放器不渲染原文;Aegisub 网格可见原文与时间供排版对照 |

遮罩与文本事件结束时间全部对齐:链 1 → `0:00:03.54`,链 2 → `0:00:10.26`。

## libass 烧录目检(帧 36 / 84 / 176)

- `crop-original-f36.png`(原帧):手机邮件原文,双重曝光的「原字」来源;
- `crop-mask-f36.png`:取样色块(逐块 `\1c`,如 `E0E2DF`/`F5F8F6`)无缝盖住
  原字,重渲染文本清晰无透字——重影消除;
- `crop-maskonly-f36.png`:仅遮罩——原字全被覆盖、无任何渲染文本,layer 1
  留白供排版覆写(即 `\p` 手绘工作流的自动化);
- `crop-mask-f84.png`(链 1 尾帧):遮罩 + 文本均在,链尾无原字透出(本轮
  修复的实证;修复前遮罩止于 3.50,该帧已裸露);
- f84 三份 PNG(原帧 / mask / maskonly)MD5 互不相同,遮罩在该帧确已渲染。

## 复现

```bash
V='test/[DMG] 冴えない彼女の育てかた♭ 第07話19.mp4'
E=docs/superpowers/evidence/2026-09-18-mask-only-acceptance
.venv/bin/python scripts/motion_ass.py --video "$V" \
  --quad-file test/motion-quad.json --start-frame 0 --end-frame 245 \
  --ocr-engine rapid --scene-text-policy mask --out "$E/mask.ass"
.venv/bin/python scripts/motion_ass.py --video "$V" \
  --quad-file test/motion-quad.json --start-frame 0 --end-frame 245 \
  --ocr-engine rapid --scene-text-policy mask_only --out "$E/mask-only.ass"
ffmpeg -nostdin -hide_banner -loglevel error -y -i "$V" \
  -vf "ass='$E/mask.ass',select='eq(n,84)'" -vsync 0 -frames:v 1 "$E/mask-f84.png"
```
