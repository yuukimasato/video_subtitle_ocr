# B：遮挡时间线与可见裁剪 Implementation Plan

> **For agentic workers:** 逐项实施、自审和提交；不依赖未安装的执行技能，不自动派发子任务。

**Goal:** 检测到的遮挡必须进入实际渲染，且不影响尚未出现遮挡的画面。

**Architecture:** 检测输出区分命中、确认清晰、未知。应用层消费同一采样时间线；受遮挡行在原事件内按帧合成离散姿态及静态 iclip，策略和亮度在切分后处理。

**Tech Stack:** Python、NumPy、OpenCV、pytest、FFmpeg/libass。

**Spec:** [设计 V3/V7](../specs/2026-09-23-real-video-optimization-design.md)。

## Global Constraints

遵守[总表](2026-09-23-real-video-implementation.md)。`sample_max_frames=7` 不再作为应用层删除检测证据的上限；保留旧配置字段兼容读取，不新增 UI 开关。不开启遮挡的输出不变。蒙版不得依赖 vector iclip 动画插值。

## B1. 保留采样证据并消除二次漏采

**Files:** 修改 `core/occlusion_mask.py` 的 collect/attach；补 `tests/test_occlusion_mask.py`。

**Interfaces:** 兼容 `collect_occlusions(...)->dict[int,list[np.ndarray]]` 原返回值；新增可选 `samples: dict[int,list[np.ndarray]|None] | None = None` 输出参数，调用者传空字典后获得每个计划采样点。None 表示解码/检测失败或无效跟踪，[] 表示成功检测且无遮挡，非空表示命中。`attach_occlusion_clips` 新增同名可选输入，旧 sparse occlusions 仍接受，缺失点为未知。

- [ ] 加入反例：100..199 帧、单次检测103，与旧均匀七帧不相交；行框覆盖多边形。复用现有测试工厂，输入事件改为4.00..8.00秒：

```python
def test_real_detection_frame_is_not_resampled_away():
    tracks = make_tracks(200, dx=0)[100:]
    poly = np.array([[120,110],[180,110],[180,140],[120,140]], dtype=float)
    event = make_event()
    event.update(start_time='0:00:04.00', end_time='0:00:08.00',
                 tags=r'{\an5\pos(150,125)\fs50}')
    out = attach_occlusion_clips([event], tracks=tracks,
        line_tracks=[make_line_track()], occlusions={103:[poly]},
        cfg=OcclusionConfig())
    assert any(r'\iclip(' in e['tags'] for e in out)
```

- [ ] 执行该测试确认红；实现 collect 的 side-channel，在成功检测后始终保存 [] 或 polys；异常保存 None；原返回 dict 仍只保存非空 polys，以兼容原调用者的布尔判断和统计。
- [ ] attach 从 `occlusions` 实际键与有效 tracks/event 的交集取候选，再按行框重叠门限筛选；不调用 `_even_subset(ok_frames,7)`。这一阶段不单独交付全段蒙版，必须与 B2 一起完成后才用真实视频验收。
- [ ] 补测最后一采样帧必测、清晰帧[]、失败None、视频解码中断后剩余计划点为None、非法 line_idx 保持事件；原无遮挡返回值兼容。运行 `pytest tests/test_occlusion_mask.py -q`，保留红绿记录。

## B2. 离散蒙版与动画绝对时间保持

**Files:** 新增 `core/occlusion_timeline.py`、`tests/test_occlusion_timeline.py`、`tests/test_occlusion_render.py`；修改 `core/occlusion_mask.py`、`core/motion_ass.py`、`scripts/motion_ass.py`、`core/step_segmentation.py`；回归 `tests/test_motion_ass.py`、`tests/test_motion_ass_collapse.py`。

**Interfaces:**

- `sample_for_frame(frame:int,samples:dict[int,list|None],max_distance:int)->int|None`：选择距离最近的已计划样本，平局取较早；先选最近再判断 None，不能越过未知点找更远命中。超距返回None。
- `pose_events_for_frames(line_track:LineTrack, tracks:Sequence[TrackedQuad], start_sec:float, end_sec:float, cfg:MotionAssConfig, alignment:str, line_idx:int)->list[dict]`：在现有 `_chain_events` 内抽取单帧姿态标签生成逻辑，供此 helper 和既有 dense fallback 共享。时间来自 tracks 的 time_sec；末帧由相邻帧时间差补结束并夹到原区间；量化后非正段不写出，并记录输入异常，不自动延长到后续文字。
- `attach_occlusion_clips` 新增可选 `motion_cfg:MotionAssConfig|None=None` 与 `alignments:Sequence[str]|None=None`；生产路径必须传原 cfg/对齐结果，兼容调用无配置时使用既有默认。

- [ ] 新增采样时间测试（不依赖模型）：

```python
def test_nearest_sample_preserves_clear_and_unknown():
    from core.occlusion_timeline import sample_for_frame
    samples = {100:[], 103:['polygon'], 106:[], 109:None}
    assert sample_for_frame(100,samples,1) == 100
    assert sample_for_frame(103,samples,1) == 103
    assert sample_for_frame(106,samples,1) == 106
    assert sample_for_frame(109,samples,1) is None
    assert sample_for_frame(120,samples,1) is None
```

- [ ] 实现样本选择：

```python
def sample_for_frame(frame, samples, max_distance):
    if not samples:
        return None
    key = min(samples, key=lambda f: (abs(f-frame), f))
    if abs(key-frame) > max_distance or samples[key] is None:
        return None
    return key
```

生产 `max_distance = max(0, cfg.sample_stride_frames // 2)`；通过有序键二分优化大样本查找，保持上述结果。未知点不创建蒙版，也不把它断言为画面清晰。

- [ ] 对有有效相交遮挡的事件，按原事件覆盖的帧重建逐帧 `pos/frz/scale/fs`；每帧从最近样本得到平面多边形，再使用**当前帧** homography 映射到屏幕。该帧无命中时不带 iclip；有命中时写静态 `\iclip(m ... l ...)`，不在 `\t` 内写 vector clip。相邻完全相同正文/标签/图层的帧事件才可压缩合并。没有任何相交命中的事件原样返回。
- [ ] 把 step fades 应用移到遮挡切分之后，并让 `attach_step_fades` 以原 hold 的绝对边界计算透明度；内部切片边界不能重新从0渐显。亮度原本已位于策略之后，继续用绝对采样曲线与每个新事件 start/end 生成；不搬运原 `\t` 相对时间。mask 的背景遮罩若位于同一物理平面，也应用同一遮挡几何；external/whitespace 移出物体后不带原位置裁剪。
- [ ] 增加逐帧 pose helper 回归：原有10px/s平移和0.5→1亮度，在绝对1.2秒处切片前后位置差≤1px、亮度通道差≤2；原事件开头/末尾 fade 保留，新增内部边界没有fade；line_idx、name、layer、policy 字段不丢失。
- [ ] 渲染回归使用 FFmpeg 生成灰底短片，固定字体 `DejaVu Sans`、白色文本 `TEST`、矩形遮挡覆盖其右半；分别渲染无遮挡、遮挡前、命中时、确认清晰后。新测试 helper `render_ass_frame(ass_path:Path,frame:int,fps:int=25)->np.ndarray` 以 subprocess 参数数组执行 FFmpeg，timeout=30秒，用输出 PNG 读取像素。无 FFmpeg 的开发机可 skip，但总表交付环境必须执行。

```python
# render_ass_frame 是本任务测试文件定义的 helper，返回 RGB uint8。
# clear/hit 取同帧相同字幕的无裁剪/有裁剪版本；以灰背景差分识别新增字形。
base = np.array([48,48,48])
clear_ink = np.any(np.abs(clear.astype(int)-base) > 20, axis=2)
hit_ink = np.any(np.abs(hit.astype(int)-base) > 20, axis=2)
assert hit_ink[target].sum() <= clear_ink[target].sum() * 0.2
assert np.mean(clear[outside] != hit[outside]) <= 0.01
```

target/outside 用测试已知矩形坐标生成布尔掩码；target 内 clear_ink 必须大于0，避免空白图假通过。清晰前后帧比较同时间的无裁剪参考，不能拿不同运动时刻相互比较。

- [ ] 运行 `pytest tests/test_occlusion_mask.py tests/test_occlusion_timeline.py tests/test_occlusion_render.py tests/test_motion_ass.py tests/test_motion_ass_collapse.py -q`；复跑 mail 基础和 protected，检查 f168/f216/f222/f228，保存源图/ASS/纯ASS。对字体之外的画面不做修改。
- [ ] 一并提交 B1/B2 为 `fix: apply occlusion samples on their supported time intervals`。输出量上限按原有有效帧数×受影响行数计算，不能为了减少事件重新全段并集；记录邮件的实际事件增量和耗时。

## B3. 有色遮罩与屏幕亮度绑定

**Files:** 新增 `core/scene_brightness.py`、`tests/test_scene_brightness.py`；修改 `scripts/motion_ass.py`、`core/screen_luma.py`、`core/subtitle_generator/generator.py`、`core/scene_text_policy.py`、`core/roi_runtime_config.py`、`cli.py`、`core/pipeline_worker.py`。补 `tests/test_scene_text_policy.py`、`tests/test_occlusion_render.py`。配置模块由C1建立；实施顺序可先完成B1/B2，再C1，再B3/B4。

**Interfaces:**

- `apply_scene_brightness(events:list[dict], curve:list[tuple[float,float]], *, use_alpha:bool=True)->list[dict]` 移出运动脚本已有循环，使用事件start/end和 `base_color`；复制输入，base_color始终保存为元数据，不在处理时pop丢失。
- 转换器新增 `roi_auto_brightness:dict[str,bool]|None=None`；CLI/GUI按有效ROI与全局开关传递。静态mask事件保留spec中的base_color及所属ROI/采样矩形。
- `sample_visible_background_luma(frame:np.ndarray, background_mask:np.ndarray, occluder_mask:np.ndarray|None)->float|None` 只取背景mask且非手部前景的灰度中位数；有效像素少于64返回None。未知点不得用黑色或手部颜色代替。

- [ ] 红测试明确颜色和透明度职责：

```python
def test_colored_mask_dims_without_revealing_original_text():
    from core.scene_brightness import apply_scene_brightness
    mask = dict(start_time='0:00:00.00', end_time='0:00:01.00',
                tags=r'{\p1\1c&HC8C8C8&}', body='', base_color=(200,200,200))
    result = apply_scene_brightness([mask], [(0,0.5),(1,0.5)])
    assert r'\1c&H646464&' in result[0]['tags']
    assert r'\alpha' not in result[0]['tags']
    assert result[0]['base_color'] == (200,200,200)
    assert mask['tags'] == r'{\p1\1c&HC8C8C8&}'
```

- [ ] 将既有 brightness_tag_chain 调用共用化；有base_color时 `use_alpha=False`，无base_color文字保留现有行为。不要给iclip修改颜色，颜色只属于绘图/文字。静态和运动都在策略生成最终事件之后调用。位置/形变切分后按各段真实起止时刻求亮度，不重置曲线。
- [ ] 静态ROI按帧采样有效背景，排除文字笔画/遮挡，默认每3帧和边界采样；强明暗变化（相邻比值差>0.1）补中间帧，补采受原有效帧范围限制。保留原曲线基线百分位策略。没有可靠像素时用最近可靠样本最多跨3帧；超过上限则不生成新的亮度推断并记录unknown。
- [ ] 遮罩与文字共用绝对采样时间和ROI有效亮度；局部逐行曲线存在时，mask用其实际覆盖背景测量，不用被手覆盖的字行亮度。增加测试：屏幕由200降到80、前景手始终230，输出mask应跟随80而非230；alpha保持不透明。
- [ ] 用邮件ROI副本设为mask（不修改原ROI文件）运行一组控制；与基础overlap的5次基线分开命名。核对f168/f216/f222：有色遮罩不能成为亮色贴片，也不能覆盖手；如果策略因背景不满足条件回退external，记录回退而不冒充mask验收，改用固定亮底合成屏幕完成确定性mask测试。
- [ ] 运行scene_brightness、scene_text_policy、occlusion_render及motion相关回归；提交 `fix: share scene brightness across text and colored masks`。

## B4. 前景轮廓分割与逐帧 iclip

**Files:** 新增 `core/occluder_contours.py`、`tests/test_occluder_contours.py`；修改 `core/occlusion_mask.py`、`scripts/motion_ass.py`；扩展 `tests/test_occlusion_render.py`。本轮证据原型在 `docs/superpowers/evidence/2026-09-23-real-video/hand_edge_demo.py`，不得把其中针对此样本的肤色阈值当作通用规则直接搬进生产。

**Interfaces:**

- `ContourResult(polygons:list[np.ndarray], status:str, reason:str)` dataclass，status仅 `valid/clear/unknown`，polygons用当前视频屏幕坐标；clear必须有有效检测依据。
- `refine_occluder_contours(frame_bgr:np.ndarray, foreground_seed:np.ndarray, background_seed:np.ndarray, domain:np.ndarray, *, previous_mask:np.ndarray|None=None)->ContourResult`：全图同尺寸uint8二值mask；冲突seed、前景不足64像素或没有可靠背景返回unknown。
- `attach_occlusion_clips` 新增可选 `screen_occlusions:dict[int,list[np.ndarray]]|None=None`；B4的屏幕轮廓只经此参数传递，不混入B1原有平面坐标字典。屏幕轮廓不得再乘homography；逐帧细化后直接裁剪，原平面检测路径仍按B2映射。
- `contours_to_iclip(polygons:list[np.ndarray])->str`：返回一条包含多个闭合子路径的iclip；无polygon返回空串，非有限坐标拒绝；坐标与PlayRes一致。

- [ ] 写红测试：合成两个肤色无关的前景指状块，前景为蓝/绿而非硬编码肤色，白/暗背景各一张；指缝保持背景。另测纯全局调暗不产生前景、强纹理移动不能仅凭差分判手、缺种子返回unknown。
- [ ] 候选来自既有亮度归一差分，至少结合连续两采样的区域连贯性或可靠前景外观；细碎字形变化不能作强前景种子。domain只覆盖待保护文字所在物体及适当外扩区。用腐蚀后的可信前景作GC_FGD，domain之外及远离候选的可靠背景作GC_BGD；其余边界带为概率区域。

```python
# foreground_seed/background_seed/domain 均来自明确证据，不是整ROI粗框。
labels = np.full(domain.shape, cv2.GC_PR_BGD, np.uint8)
labels[domain == 0] = cv2.GC_BGD
labels[background_seed > 0] = cv2.GC_BGD
labels[foreground_seed > 0] = cv2.GC_FGD
cv2.grabCut(frame_bgr, labels, None, np.zeros((1,65)), np.zeros((1,65)),
            3, cv2.GC_INIT_WITH_MASK)
binary = np.isin(labels, [cv2.GC_FGD, cv2.GC_PR_FGD]).astype(np.uint8)
```

- [ ] 只保留与强前景种子相连的区域；不通过凸包把指缝填死。`findContours(RETR_CCOMP)` 保留洞结构，内部洞用反向绕序闭合子路径；先用libass合成环形mask验证填充规则，验证不通过则分解为无洞多边形，不能静默丢洞。`approxPolyDP` 误差上限为1080p的1.5px，顶点上限512/轮廓；若仍超限以多个路径保存并记录复杂度，不增加误差吃掉指缝。
- [ ] 前景独立于手机平面：轮廓在当前屏幕上细化；若中间缓存为平面坐标，反变换后仍需当前帧边缘校正。相邻轮廓差异>2px或IoU<0.9、手刚进入/离开时，将该采样间隔加密到每帧；不跨镜头传播。未知点不复用超过一帧的过期轮廓。
- [ ] 生成原文字和有色mask的共同iclip（限同一被遮挡物体）；external/whitespace被移走则不带原处裁剪。轮廓消失后恢复正常渲染，不能把曾出现的手轮廓合成全程并集。
- [ ] 合成测试要求主体分割IoU≥0.95、指缝中心像素不属前景、有效裁剪区域字形减少≥80%；邮件f216/f222/f228人工对照边缘，不接受手机背景整体被分成前景。保存失败路径和原因；真实轮廓无人工逐像素真值时不报告虚构IoU。
- [ ] 运行contours、occlusion_mask、occlusion_render和motion集成回归；复跑邮件亮暗与手势序列，并提交 `feat: refine foreground contours for time-local ASS clipping`。生产接入前仍以总表基线保护；本轮只是文档和独立视觉试验。
