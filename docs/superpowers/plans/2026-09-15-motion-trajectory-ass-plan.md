# 移动文字轨迹 → ASS 轨迹字幕 实施计划(阶段一)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 打通「手动 quad → 逐帧平面跟踪 → 清晰关键帧 OCR 投票 → 轨迹合成 ASS(`\move` 分段 / `\t` / 帧级 `\pos` 兜底)」全链路,并在 `test/[DMG] 冴えない彼女の育てかた♭ 第07話19.mp4` 手机场景完成验收。

**Architecture:** 旁路脚本管线,不动主流水线既有事件路径。新增 `core/keyframe_selector.py`(清晰关键帧选取)与 `core/motion_ass.py`(纯函数合成器);从 `core/ocr_optimizer.py` 行为保持地抽取模块级融合函数供脚本复用;新增 `scripts/motion_ass.py`(端到端 CLI)与 `scripts/verify_motion_ass.py`(libass 渲染偏差校验)。

**Tech Stack:** Python 3、OpenCV(已有)、numpy、pytest、ffmpeg(libass)、git。无新增第三方依赖;无新增服务端调用(VLM 复核不在本阶段)。

**Spec:** `docs/superpowers/specs/2026-09-15-motion-trajectory-ass-design.md`

## Global Constraints

- `generator.py` 及主流水线行为零改动;`ocr_optimizer.py` 仅允许行为保持的重构(方法委托模块级函数)。
- `core/motion_ass.py`、`core/keyframe_selector.py` 不 import PySide6(便于 CLI 与单测);时间一律用 `TrackedQuad.time_sec` 浮点,仅在格式化时转 centisecond。
- lost 帧不外推、不产出无证据轨迹点(沿用 tracker 哲学);相邻段边界必须共享同一格式化时间字符串。
- 修改前记录基线;每任务完成跑定向测试,全部完成后跑全量;每任务一个 commit。
- 服务端调用(如有)遵循有界退避;本计划默认不触发任何服务端调用。

---

### Task 1: 记录基线

**Files:** 无新文件(记录用)

- [ ] **Step 1: 检查工作区**

```bash
git -C /home/hope/Tools/video_subtitle_ocr/video_subtitle_ocr status --short --branch
```

Expected: 分支 `main`,工作区无未提交的本计划相关改动。

- [ ] **Step 2: 基线全量测试**

```bash
cd /home/hope/Tools/video_subtitle_ocr/video_subtitle_ocr && .venv/bin/pytest -q
```

Expected: 与最近记录一致(参考 `350 passed, 1 skipped`;若不同,记录实际值并先报告再继续)。

### Task 2: 抽取模块级按位置融合函数(行为保持)

**Files:**
- Modify: `core/ocr_optimizer.py`
- Test: `tests/test_scene_text_fusion.py`(已有用例必须全绿)

**Interfaces:**
- Produces: `core.ocr_optimizer.fuse_samples_by_position(sample_results, anchor_aabbs, *, vlm_refine_min_confidence: float) -> Tuple[Dict, List[int], Optional[List[List[str]]]]`(模块级;`_LineBox`/`_line_aabbs` 随迁或导出)。
- `_OCRToASSOptimizer._fuse_samples_by_position` 改为委托该函数(签名不变)。

- [ ] **Step 1: 写定向测试**——直接调用模块级函数,复用现有测试的构造样例(2~3 个用例即可:缺失观测不投票、锚定帧漏读救回)。
- [ ] **Step 2: 确认失败**(函数不存在)后实施抽取;`self.vlm_refine_min_confidence` 改为显式参数传入。
- [ ] **Step 3: 验证**

```bash
.venv/bin/pytest -q tests/test_scene_text_fusion.py tests/test_ocr_optimizer*.py
```

Expected: 全绿,原有方法路径无回归。

### Task 3: 清晰关键帧选取 `core/keyframe_selector.py`

**Files:**
- Create: `core/keyframe_selector.py`
- Test: `tests/test_keyframe_selector.py`

**Interfaces:**
- Produces: `select_keyframes(video_path: str, tracks: List[TrackedQuad], k: int = 3, min_gap_sec: float = 0.0, plane_size: Optional[Tuple[int, int]] = None) -> List[int]`(返回 `TrackedQuad.frame_num` 列表,仅从 `status=="ok"` 帧中选)。
- 质量分:`unwarp_canonical(frame, homography_inv, plane_size)` → 灰度 → `cv2.Laplacian` 方差;`plane_size` 缺省由 init_quad 边长推导;`min_gap_sec` 保证时间分散,分数并列取更早帧。

- [ ] **Step 1: 失败测试**——合成序列:清晰文字帧 + 高噪/模糊帧(高斯模糊),断言选中清晰帧、lost 帧被排除、k 超过 ok 数时返回全部。
- [ ] **Step 2: 实现**至测试通过。
- [ ] **Step 3: 验证**

```bash
.venv/bin/pytest -q tests/test_keyframe_selector.py tests/test_scene_plane_tracker.py
```

### Task 4: 合成器 `core/motion_ass.py` —— 轨迹重建与平滑

**Files:**
- Create: `core/motion_ass.py`
- Test: `tests/test_motion_ass.py`

**Interfaces:**
- Produces:
  - `MotionAssConfig` dataclass:`move_tol_px=2.0, min_seg_frames=3, smooth_window=5, rot_thresh_deg=1.0, scale_thresh=0.02, keyframe_count=3, lost_hold_sec=0.0, dense_pos_fallback=True, dense_stride=2, max_segments_per_sec=6`。
  - `homography_between(tracks, frame_a, frame_b) -> np.ndarray`(`H(a→b) = H(init→b)·inv(H(init→a))`;端点为 lost 时抛 `KeyError`)。
  - `build_line_tracks(line_boxes, texts, tracks, ref_frame) -> List[LineTrack]`:`LineTrack = {text, ref_box(平面坐标), poses: Dict[frame_num, Pose]}`,`Pose = {center, angle_deg, scale}`;行框四角经 `homography_between(ref→t)` 映射;lost 帧无条目。
  - `smooth_line_track(track, window=5)`——滑动平均;窗口内 lost 缺失帧跳过,不改趋势。

- [ ] **Step 1: 失败测试**——构造纯平移轨迹(逐帧 quad 手工生成):断言每帧 center 与真值误差 <0.5px、scale≈1、angle≈0;旋转 10° 的轨迹 angle 逐帧递增;lost 帧无 pose;平滑不改变线性趋势(斜率偏差 <1%)。
- [ ] **Step 2: 实现**至测试通过。
- [ ] **Step 3: 验证** `pytest -q tests/test_motion_ass.py`

### Task 5: 合成器 —— 分段与标签阶梯

**Files:**
- Modify: `core/motion_ass.py`
- Test: `tests/test_motion_ass.py`

**Interfaces:**
- Produces: `simplify_and_segment(track, cfg) -> List[Tuple[int, int]]`(帧号区间列表)与 `synthesize_events(line_tracks, tracks, cfg, style="Scene") -> List[Dict]`(dict 含 `start_time/end_time/style/name/tags/body`,`name="motion"`)。

判定阶梯(spec §5):
1. DP 简化(容差 `move_tol_px`)得分段点;并入角度 ≥`rot_thresh_deg`、缩放 ≥`scale_thresh` 显著变化点;最小段长 `min_seg_frames`;
2. 单段 → `{\an5\fs(H)\move(x1,y1,x2,y2,0,T)}`(H=参考行高,`\move` t 毫秒取整);
3. 多段 → 每段一条事件,`end_time` 与下一段 `start_time` 同字符串;段内角度/缩放超阈值时 `基值标签+\t(t1,t2,\frz(b)\fscx(..)\fscy(..))`(如 `\frz(a)…\t(…,\frz(b))`);
4. 段数爆炸(平均段长 < 2×`min_seg_frames` 或每秒段数 > `max_segments_per_sec`)且 `dense_pos_fallback` → 帧级 `\pos`(隔 `dense_stride` 帧);
5. lost 间隔:切段;`lost_hold_sec>0` 时 ≤ 该时长的 lost 用前段末位保持;零长段丢弃。

- [ ] **Step 1: 失败测试**——①匀速直线 → 恰 1 条事件,`\move` 端点=首末 center、t2=段长 ms;②圆弧 → ≥2 段、相邻边界时间字符串相等、前段 `\move` 终点=后段起点(±0.5px);③线性旋转 15° → 事件含 `\frz` 与 `\t`;④高频抖动 → 触发 `\pos` 兜底,事件数 = 好帧数/stride;⑤中段 lost → 2 条链、时间不重叠、lost 段无事件;⑥`format_ass_time` 往返(0、0.04、3599.99)。
- [ ] **Step 2: 实现**至测试通过。
- [ ] **Step 3: 验证** `pytest -q tests/test_motion_ass.py`

### Task 6: 端到端 CLI `scripts/motion_ass.py`

**Files:**
- Create: `scripts/motion_ass.py`
- Test: `tests/test_motion_ass_cli.py`

**Interfaces:**
- Consumes: `scene_plane_tracker.track_plane / save_trajectory`、`keyframe_selector.select_keyframes`、`ocr_optimizer.fuse_samples_by_position`、`motion_ass.synthesize_events`。
- CLI:`--video --quad-file(4×2 JSON)或 --quad "x1,y1 x2,y2 x3,y3 x4,y4" --start-frame --end-frame --out [--trajectory-json] [--config-json] [--ocr-engine rapid|paddle]`。
- 关键帧 OCR:逐关键帧 `unwarp_canonical` 展开 → OCR 引擎 → 行框已是平面坐标 → 适配 `rec_*` 结构 → `fuse_samples_by_position`(锚定=最清晰帧);阶段一不接 VLM。
- ASS 头:内嵌与 generator 默认一致的样式(含 `Scene` 行),PlayRes 取视频分辨率。

- [ ] **Step 1: 失败测试**——合成视频(现有测试的合成文字卡片模式,平移+缩放)跑 main(),断言:产出 .ass 存在、含 `\move`、无 `\pos` 碎片段(该合成场景应走阶梯 2/3)、时间格式合法;`--trajectory-json` 往返可加载。
- [ ] **Step 2: 实现**;OCR 引擎单测用现有 mock 模式(真实引擎测试沿用 `VIDEO_SUBTITLE_OCR_REAL_TEST=1` 约定)。
- [ ] **Step 3: 验证**

```bash
.venv/bin/pytest -q tests/test_motion_ass_cli.py
```

### Task 7: 渲染偏差校验 `scripts/verify_motion_ass.py`

**Files:**
- Create: `scripts/verify_motion_ass.py`
- Test: `tests/test_verify_motion_ass.py`(仅校验偏差统计纯函数;ffmpeg 集成为可选冒烟)

**Interfaces:**
- CLI:`--video --ass --trajectory-json --frames 20 [--tol-median 4 --tol-p95 8] [--retry]`。
- 方法:ffmpeg `color=black` 底 + libass 烧录指定帧 → PNG → 亮色像素质心 = 字幕中心,与该帧轨迹行中心比偏差;输出 median/p95/逐帧 JSON 报告。
- `--retry`:超预算段将 `move_tol_px` 减半重新合成并复验一次,仍超则退出码非 0 并列明段落。

- [ ] **Step 1: 失败测试**——构造已知质心的合成图,断言质心提取与偏差统计函数正确(median/p95 数学)。
- [ ] **Step 2: 实现**;有 ffmpeg 环境时跑通单事件合成样例的冒烟(标记 skipif 无 ffmpeg)。
- [ ] **Step 3: 验证** `pytest -q tests/test_verify_motion_ass.py`

### Task 8: DMG 视频验收 + 归档

**Files:**
- Create: `test/motion-quad.json`(手机屏幕 4 角,从既有 ROI poly 内取凸四边形)
- Create: `docs/superpowers/evidence/2026-09-15-motion-trajectory-ass.md`(验收记录)
- Modify: `CHANGELOG.md`(「优化」小节,未发布版本)

- [ ] **Step 1: 生成轨迹与 .ass**

```bash
.venv/bin/python scripts/motion_ass.py \
  --video "../test/[DMG] 冴えない彼女の育てかた♭ 第07話19.mp4" \
  --quad-file "../test/motion-quad.json" --start-frame 0 --end-frame 245 \
  --trajectory-json "../test/motion-trajectory.json" \
  --out "../test/[DMG] 第07話19-motion.ass"
```

- [ ] **Step 2: DoD 指标核查**(脚本或手工断言,写入验收记录)
  - 事件数 40±20;无同文本重叠时间的多链重复;0.088~0.098s 模糊窗无碎片读法;零时长事件为 0;
  - `scripts/verify_motion_ass.py` 通过(median ≤4px、p95 ≤8px;不通过则 `--retry` 后复验);
  - 0~3s 慢漂移与 8.9~9.8s 快速移动段各抽 2 帧人工比对(截图入 evidence)。
- [ ] **Step 3: 全量回归** `.venv/bin/pytest -q` —— Expected: 全绿无新增失败。
- [ ] **Step 4: CHANGELOG + evidence 提交;打 tag 与否由维护者定。**

## 风险提示(实施时注意)

- 遮挡期(lost)文字消失是阶段一预期行为,验收记录需注明区间;
- 关键帧 OCR 行框为平面坐标,合成输出前不得混入画面坐标(单测覆盖两种坐标不互窜);
- `unwarp_canonical` 顶点顺序沿用户框选,逆时针会产生镜像——CLI 读入 quad 后校验凸性与方向,不合法即报错退出。
