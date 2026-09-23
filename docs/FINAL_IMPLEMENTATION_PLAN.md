# video_subtitle_ocr 最终实施计划（AI交接版）

> **For agentic workers:** 按复选框逐项执行并自审；直接使用你的可用工具；不依赖Codex、私有MCP或某个指定执行技能。先理解契约再改代码，不把示例当作完整补丁。

**Goal:** 修复真实样本暴露的时序、遮挡和配置布局问题，保留可恢复基线和可重复的画面验收。

**Architecture:** 在现有静态/轨迹双路径内修复，不重写主流程。事件时间与内容先正确，遮挡消费明确的采样证据，CLI/GUI 使用一致配置，样式最后调整。

**Tech Stack:** Python 3.12、pytest、Paddle OCR、OpenCV、PySide6、FFmpeg/libass、Git。

**Spec:** [最终优化方案](FINAL_OPTIMIZATION_DESIGN.md)，必须与本计划一起阅读。

## Global Constraints

- 所有下方待办尚未实施；上一轮已完成基础优化见方案，不能重复执行。
- 生产源码基准为 `20231ba3f296c1149eddd691f33c962699e68f1d`，原始基线 `ead48f9` 和既有备份保留。
- 不修改 `/home/hope/Tools/video_subtitle_ocr/test/` 中的视频与 ROI；新输出使用新目录，不覆盖本轮证据。
- 不新增运行依赖、不自动安装字体、不调用真实翻译/VLM、不发布包；既有有界 429 重试保持。
- 优先修复错误时间和丢内容；不能靠删零时长事件、删除元信息或关闭整个能力通过验收。
- Scene 基础字号按行几何约束；旋转/颜色仍受 pose 控制；自定义模板优先。
- 分步提交，以 revert 或独立 worktree 回退；不用 reset --hard/clean。


## 接手路线、文件与执行记录

只读取[最终方案](FINAL_OPTIMIZATION_DESIGN.md)和本文作为需求入口，之后读取各任务列出的源码/测试。外层目录不是Git根；所有shell命令除另注外都在 `/home/hope/Tools/video_subtitle_ocr/video_subtitle_ocr` 运行。测试统一使用 `.venv/bin/python -m pytest`，不要依赖系统pytest。

任务顺序：**G0 → C1 → A1 → A2 → A3 → B1/B2 → B3 → B4 → D1 → C2 → G1**。B1/B2为一个完整交付，不能只修抽样就发布全段蒙版；C1先于B3避免配置接口缺失。P0任务优先不意味着可以跳过依赖。不同任务会共同修改cli/generator/motion脚本，默认串行；如自行并行必须明确文件所有权，禁止互相覆盖。

| 任务 | 修改/新增模块 | 可独立验收结果 |
| --- | --- | --- |
| G0 | Git/外部备份/测试日志 | 可恢复生产基准和文档基准 |
| C1 | roi_runtime_config、cli、pipeline_worker | 有效配置一致 |
| A1 | subtitle_generator/event_merge | 共存行不截零 |
| A2 | scene_timeline、scene_text_policy、generator | 按活动行集合布局 |
| A3 | trajectory_fidelity、motion脚本、生成器/入口 | 内容接管与候选生命周期 |
| B1/B2 | occlusion_mask、occlusion_timeline、motion_ass、step_segmentation | 采样证据与时间局部clip |
| B3 | scene_brightness、screen_luma、入口/生成器 | mask/text共用亮度时间线 |
| B4 | occluder_contours、遮挡/策略集成 | 独立前景边缘与最终图层裁剪 |
| D1 | text_motion_evidence、pose_verify、step_segmentation | 静字/背景运动解耦 |
| C2 | line_geometry、styling、generator | 字号约束与模板兼容 |
| G1 | 测试与新证据目录 | 全量回归、GUI烟测和真实视频验收 |

下方新增模块/接口都属于计划内容，当前树中尚不存在；现有类字段/参数以源码核对，不能因ImportError以为环境损坏。源代码引用采用路径+函数名，行号会随提交变化。每任务记录四项：红测试结果、修改提交hash、绿测试结果、剩余风险。将记录填到本文末尾，不另写第二份实施计划；日志/截图放新evidence目录。

本轮文档收敛移除了重复的 2026-09-23 草案：`docs/superpowers/specs/2026-09-23-source-optimization-design.md`、`docs/superpowers/plans/2026-09-23-source-optimization.md`、`docs/superpowers/specs/2026-09-23-real-video-optimization-design.md` 及同日期的 `real-video-{implementation,timing,occlusion,config-layout,motion-isolation}.md`。内容已按当前源码与实测结果合并到本文和最终方案；完整旧文档可从 `baseline/final-plan-consolidation-20260923` 或外部目录 `/home/hope/Tools/video_subtitle_ocr_backups/20260923-final-plan-consolidation/docs-before-consolidation.tar` 恢复。其他日期的独立功能计划、验收记录、历史 `optimization_analysis.md` / `development_plan.md` 与需求文档保留，不作为本轮待办入口。

### 跨模块契约（高于旧草案示例）

- 时间：所有候选事件用半开区间[start,end)，ASS百分秒量化后再检查严格正时长。采样帧号从0开始；原素材24000/1001fps。浮点时间换算复用已有函数，禁止引入不同舍入规则。
- 身份：同正文不同空间行是不同实例；ROI ID沿用roi_0等。motion候选必须带roi，不靠正文匹配判断归属。显式无ROI motion-quad作为独立附加输出。
- 接管：coverage只是必要条件；fidelity/几何证据判定通过后才抑制静态。被拒ROI的轨迹候选必须同时从最终输出移除，不能只清motion_roi_ids。所有后续翻译/字体/写出只消费选中的事件。
- 几何：OCR恢复框为视频屏幕坐标，旧occlusions是展开平面坐标；新的screen_occlusions明确为屏幕坐标；到ASS PlayRes恰好变换一次。新屏幕轮廓统一携带 `ContourResult` 状态：`valid` 有轮廓、`clear` 有证据且无前景、`unknown` 未采样或失败；缺失帧按unknown处理。
- 层次：mask layer0、文字layer1，mask_only文本为Comment；同一物体前景clip同时作用于两层。NoteBox离开物体不继承该clip。
- 动画：切分后重建绝对时间运动/亮度/fade，禁止复制相对t再改事件起点。原fade边界保留，内部切片不重启动画。
- 用户优先级：ROI语言>显式全局lang>文件顶层>ch；显式scene-policy>保存值>overlap。姿态与颜色仍受pose开关；自定义模板优先于自动字号。
- 不确定性：分割失败/未采样为unknown，成功且无前景为clear；缺失不能伪造为clear，也不能无限期延用旧clip。低可信轨迹回退有证据的静态结果，仍须通过邮件遮挡/phone11内容验收。

## G0. 实施前确立基线

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

## C1. 解析配置一次，所有路径使用同一结果

**Files:** 新增 `core/roi_runtime_config.py`、`tests/test_roi_runtime_config.py`；修改 `cli.py`、`core/pipeline_worker.py`；补 `tests/test_cli_shared_stages.py`、`tests/test_roi_ocr_lang.py`、`tests/test_roi_pose_tags.py`、`tests/test_motion_integration.py`。

**Interfaces:**

```python
# 新模块中的 dataclass 与纯函数签名
# RoiFileConfig: rois:list[dict], ocr_lang:str|None
# read_roi_config(path:str)->RoiFileConfig
# effective_ocr_options(global_options:dict, roi:dict, *,
#     file_lang:str|None=None, cli_lang:str|None=None)->dict
# resolve_roi_policies(rois:list[dict], explicit_policy:str|None)->list[dict]
# collect_roi_pose_tags(rois:list[dict])->dict[str,dict]
```

`cli.load_roi_file` 保持返回 list，内部委托 `read_roi_config`；CLI 主入口改读完整对象。引擎参数字典必须复制，不能被后一个 ROI 修改前一个 ROI 的设置。

- [ ] 红测试（文件不存在前 ImportError；之后确保真实参数不丢）：

```python
def test_motion_options_keep_tier_and_roi_language():
    from core.roi_runtime_config import effective_ocr_options
    base = {'model_tier':'small','lang':'ch'}
    out = effective_ocr_options(base, {'ocr_lang':'japan'},
                               file_lang='ch', cli_lang='en')
    assert out['model_tier'] == 'small'
    assert out['lang'] == 'japan'
    assert base == {'model_tier':'small','lang':'ch'}

def test_explicit_overlap_overrides_saved_mask():
    from core.roi_runtime_config import resolve_roi_policies
    rois = [{'scene_text_policy':'mask'}]
    assert resolve_roi_policies(rois,'overlap')[0]['scene_text_policy'] == 'overlap'
    assert resolve_roi_policies(rois,None)[0]['scene_text_policy'] == 'mask'
    assert rois[0]['scene_text_policy'] == 'mask'
```

- [ ] 核实 `_engine_options_from_args` 实际 model tier 的键名（当前为 model_tier）；实现以下优先级而不另造引擎配置名：

```python
from copy import deepcopy

def effective_ocr_options(global_options, roi, *, file_lang=None, cli_lang=None):
    options = deepcopy(global_options)
    options['lang'] = roi.get('ocr_lang') or cli_lang or file_lang or 'ch'
    return options

def resolve_roi_policies(rois, explicit_policy):
    out = deepcopy(rois)
    for roi in out:
        roi['scene_text_policy'] = (explicit_policy if explicit_policy is not None
                                    else roi.get('scene_text_policy') or 'overlap')
    return out
```

- [ ] `--lang`、`--scene-text-policy` 的 argparse 默认改为None；帮助文本说明无文件默认仍 ch/overlap。所有用这两个 args 的调用点改为已解析值，补旧测试 Namespace 缺字段时的 getattr 兼容。提取现有 ROI 文件校验到 read_roi_config，顶层语言保存到对象；裸数组语言None，不改变视频/帧范围和原始文件。
- [ ] 在抽帧、静态引擎创建和 `collect_motion_roi_specs` **之前** resolve；ROI与显式 motion-quad 均传完整 engine_options。删除执行完运动后才施加 policy 的重复分支。GUI 用同一 effective_ocr_options，GUI 当前全局语言当 cli_lang 参数传入；ROI覆盖语义相同。
- [ ] `collect_roi_pose_tags` 复制 GUI 现有判断（write_pose_tags 为真、pose为dict）；CLI/GUI 转换器都传它，合并 ROI 时不伪造无法解释的合成姿态。记录有效配置与回退理由，不记录环境密钥。
- [ ] 入口级 spy 测试验证实际传给静态/运动 engine builder 的完整参数，不只测 helper；覆盖显式small、顶层japan、ROI多语、旧裸数组、显式overlap、保存pose和无pose。确认 GUI仍保留边界精修、CLI仍不凭空启用。
- [ ] 运行六个相关测试文件；复跑4K日志确认不再加载medium，输出含保存pose；提交 `fix: resolve ROI runtime options before all processing stages`。

## A1. 修复共存行被截为零

**Files:** 修改 `core/subtitle_generator/event_merge.py`；新增 `tests/test_scene_event_timing.py`；回归 `tests/test_boundary_precision_fixes.py`、`tests/test_regression_fixes.py`。

**Interfaces:** 保留 `_clamp_same_roi_event_overlaps(events)->list[dict]`；不改变外部调用。函数先复制输入字典，避免修改调用者原件。

- [ ] 写红测试并运行 `QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest tests/test_scene_event_timing.py -q`。以下用真实转换器，无模型：

```python
from core.subtitle_generator import OCRToASSOptimizer

def test_scene_rows_coexist(tmp_path):
    opt = OCRToASSOptimizer(video_path='unused',
        output_path=str(tmp_path/'out.ass'), fps=25, width=1920, height=1080)
    rows = [dict(roi='roi_0', style='Scene', start_time='0:00:01.00',
                 end_time='0:00:01.12', tags=f'{{\\pos(100,{y})}}', body=t)
            for y, t in [(100, 'first'), (200, 'second')]]
    out = opt._clamp_same_roi_event_overlaps(rows)
    assert [e['end_time'] for e in out] == ['0:00:01.12'] * 2
    assert rows[0]['end_time'] == '0:00:01.12'
```

- [ ] 在循环中加入保守守卫；已有同 ROI/style、policy 守卫保留。非 Scene 没有明确通道时仅对同 tags 的传统字幕齐平，禁止等起点齐平：

```python
if prev.get('style') == 'Scene':
    continue
if prev.get('tags', '') != cur.get('tags', ''):
    continue
prev_start = self._parse_ass_time_to_seconds(prev['start_time'])
if cur_start <= prev_start:
    continue
# 仍须满足既有 0 < prev_end - cur_start <= 0.2
```

- [ ] 增加参数化：相同起点 Default 不截零；不同 ROI 不变；policy 遮罩不变；Default 相同 tags 的 `[1,2.04)` 与 `[2,3)` 应齐平为2秒；Scene 不同位置相同正文继续共存。经 `_merge_temporal_near_duplicate_events` 再验证不同位置不被合并。
- [ ] 运行上述三个文件并确认红用例变绿；重跑 phone12 统计，不能通过丢掉1138条来消除零时长。提交 `fix: preserve simultaneous scene event durations`，只暂存本任务文件。

## A2. 策略按活动集合分段

**Files:** 新增 `core/scene_timeline.py`、`tests/test_scene_timeline.py`；修改 `core/scene_text_policy.py::_apply_external`、`core/subtitle_generator/generator.py::_finish_scene_policy_events`；补充 `tests/test_scene_text_policy.py`、`tests/test_scene_text_policy_mask_only.py`。

**Interfaces:** 新文件定义两个不可变 dataclass 和一个纯函数：

```python
from dataclasses import dataclass
from typing import Sequence

@dataclass(frozen=True)
class TimedRow:
    row_id: int
    start_cs: int
    end_cs: int

@dataclass(frozen=True)
class ActiveSlice:
    start_cs: int
    end_cs: int
    row_ids: tuple[int, ...]

# 函数完整实现见本任务第二个代码块。
```

`active_slices(rows: Sequence[TimedRow]) -> list[ActiveSlice]` 的完整算法在下一步定义；非正时长抛 ValueError，不静默删除。row_id 是已排序行的稳定索引，调用层保存到原始 line_idx 的映射。

- [ ] 写红测试：

```python
def test_active_rows_do_not_leak_to_other_times():
    from core.scene_timeline import TimedRow, ActiveSlice, active_slices
    rows = [TimedRow(0,0,200), TimedRow(1,100,300), TimedRow(2,400,500)]
    assert active_slices(rows) == [ActiveSlice(0,100,(0,)),
        ActiveSlice(100,200,(0,1)), ActiveSlice(200,300,(1,)),
        ActiveSlice(400,500,(2,))]
```

- [ ] 实现端点扫描，计数器处理同 row_id 重叠区间，不用集合删一个区间误删另一个；以 row_id 排序稳定行序；空段跳过；仅合并相邻同身份集合。

```python
from collections import Counter, defaultdict

def active_slices(rows):
    boundaries = defaultdict(Counter)
    for row in rows:
        if row.end_cs <= row.start_cs:
            raise ValueError('non-positive row duration')
        boundaries[row.start_cs][row.row_id] += 1
        boundaries[row.end_cs][row.row_id] -= 1
    times = sorted(boundaries)
    live, out = Counter(), []
    for i, start in enumerate(times[:-1]):
        live.update(boundaries[start])
        ids = tuple(sorted(k for k, n in live.items() if n > 0))
        end = times[i+1]
        if not ids:
            continue
        if out and out[-1].end_cs == start and out[-1].row_ids == ids:
            out[-1] = ActiveSlice(out[-1].start_cs, end, ids)
        else:
            out.append(ActiveSlice(start, end, ids))
    return out
```

- [ ] 运动 external：根据事件 line_idx 和 orig_indices 建 TimedRow；每个 ActiveSlice 仅把当前行传入现有 wrap/fit/NoteBox 布局。legacy 无 line_idx 仅在正文唯一匹配时映射，多个同文实例歧义记录诊断并保留原事件；禁止退回全轨迹跨度拼所有行。
- [ ] 静态路径：将 ctx rows/row_times/row_meta 同序索引传入 active_slices；每段只调用当前 rows 的 `apply_policy_static`，spec 的局部 rows/row 索引映射回原索引；所有 text/mask/note/scene_ws 时间夹在该 slice 内。位置偏移、颜色、Comment、layer 等仍由现有分支生成。结束后仅合并所有显示属性相同的相邻事件，不能再次取全 ROI min/max。
- [ ] 增加真实入口回归：两个同文不同位置行部分重叠；空白 gap；mask→external 回退；whitespace 合成行；mask_only 的 Comment 与 mask 同时段且图层顺序不变；每个边界±1cs 比较输入输出活动身份。旧“重叠块整段联合正文”测试改成新精确时间契约，保留布局断言。
- [ ] 运行 `.venv/bin/python -m pytest tests/test_scene_timeline.py tests/test_scene_text_policy.py tests/test_scene_text_policy_mask_only.py tests/test_pipeline_scene_text_policy.py -q`；提交 `fix: preserve active text intervals in scene policies`。

## A3. 接管同时验证内容，失败保留静态

**Files:** 新增 `core/trajectory_fidelity.py`、`tests/test_trajectory_fidelity.py`；修改 `scripts/motion_ass.py`、`cli.py`、`core/pipeline_worker.py`、`core/subtitle_generator/generator.py`；补 `tests/test_trajectory_coverage_gate.py`、`tests/test_motion_integration.py`。

**Interfaces:** `TextSpan(start_cs:int,end_cs:int,text:str,row_order:int=0)` 不可变 dataclass；`FidelityResult(ok:bool,reason:str)` 不可变 dataclass；`check_text_fidelity(static:Sequence[TextSpan], motion:Sequence[TextSpan])->FidelityResult`。`build_motion_events` 现有 summary 字典新增 `pre_policy_events`（在任何occlusion/hold碎片扩增前复制按原始行身份合并的可见区间，并保留稳定row_order；不能把逐帧重复样本重复计为共存行）；转换器新增可选 `motion_evidence: dict[str,list[dict]] | None = None`，旧签名向后兼容。CLI/GUI发出的自动ROI轨迹事件都显式加 `roi`，无ROI事件仅用于手工独立quad。不得把策略生成的 NoteBox 当作原始行证据。

- [ ] 红测试：时间100%覆盖而内容错误必须拒绝，内容完全相同接受；无静态证据返回 `ok=False, reason='unverified'`：

```python
def test_full_coverage_is_not_text_fidelity():
    from core.trajectory_fidelity import TextSpan, check_text_fidelity
    static = [TextSpan(0,100,'甲'), TextSpan(100,200,'乙')]
    motion = [TextSpan(0,200,'甲')]
    assert not check_text_fidelity(static,motion).ok
    assert check_text_fidelity(static,static).ok
    assert not check_text_fidelity([],motion).ok
    assert not check_text_fidelity([TextSpan(0,100,'甲乙')],
                                   [TextSpan(0,100,'乙甲')]).ok
```

- [ ] 实现无新阈值的保守对照：对两路全部开始/结束端点划片；各片分别收集活动正文，使用 Unicode NFKC、去空白与标点（保留字母数字）规范化，再按稳定屏幕阅读顺序保留每行文本，生成行字符串元组；两路规范化元组必须相同且至少有一个有效片。不要使用字符Counter：它会把“甲乙”和“乙甲”误判为相同。行分割或顺序不一致先保守拒绝并记录，不通过宽松拼接消除歧义。任何 mismatch 返回首个片段位置与原因；此门控是保守筛选，不能代替几何/语义真值。非法/非正时长拒绝。
- [ ] 生成器先建立现有静态分组和各组时间，抽出 TextSpan（只作为证据，不新增 OCR；静态组内先依屏幕y/x和已有行身份确定row_order），再决定是否跳过静态。现有 coverage 仍为前置必要条件；只有新 fidelity 也通过才允许 motion_roi_ids 抑制整个 ROI。CLI/GUI 将 summary 的 pre_policy_events 按 ROI 转交，不能将已丢弃的候选事件混入最终输出。直接 `--motion-quad` 没有关联静态 ROI 时只作为显式附加输出，不擅自抑制其他 ROI。
- [ ] 在生成器决定每个ROI后构造唯一selected_motion列表：通过的ROI加入，拒绝/unknown的ROI候选整体移除；无roi的显式quad保留为附加输出。将self.motion_events更新为selected_motion，再进入字体、翻译、空静态结果和最终write路径；禁止只修改motion_roi_ids。增加集成断言：错误候选body不出现在最终Dialogue，静态body恰好出现一次。
- [ ] 旧调用没有新 evidence 时保留静态并记录 unverified；已有手工传 motion_events 的兼容测试要覆盖“附加输出”和“抑制 ROI”两种语义。无需修改 `trajectory_takeover_ok` 的返回元组，避免破坏仅计算覆盖率的调用者。
- [ ] 严格fidelity可能拒绝有益的轨迹，必须同时复跑mail和4K检查路由：拒绝后静态结果仍须达到遮挡/亮度/内容要求，不能把“统一回退静态”当作完成。若修正采样端点归一，按fps明确规则并补边界测试，不随意放宽容差。
- [ ] phone11 复跑：验证整段 NoteBox 消失、活动文本随画面更新，并检查设计列出的缺失短句。若静态 OCR 也缺字，追查原始 OCR/分组边界并记录具体失败，不通过提高模型档位或把所有历史文字加入词表蒙混过关。
- [ ] 运行新增 fidelity、coverage、motion integration、CLI shared stages 回归；提交 `fix: require temporal text fidelity before trajectory takeover`。再运行G1最终验收。

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
- [ ] 补测最后一采样帧必测、清晰帧[]、失败None、视频解码中断后剩余计划点为None、非法 line_idx 保持事件；原无遮挡返回值兼容。运行 `.venv/bin/python -m pytest tests/test_occlusion_mask.py -q`，保留红绿记录。

## B2. 离散蒙版与动画绝对时间保持

**Files:** 新增 `core/occlusion_timeline.py`、`tests/test_occlusion_timeline.py`、`tests/test_occlusion_render.py`；修改 `core/occlusion_mask.py`、`core/motion_ass.py`、`scripts/motion_ass.py`、`core/step_segmentation.py`；回归 `tests/test_motion_ass.py`、`tests/test_motion_ass_collapse.py`。

**Interfaces:**

- `sample_for_frame(frame:int,samples:dict[int,list|None],max_distance:int)->int|None`：选择距离最近的已计划样本，平局取较早；先选最近再判断 None，不能越过未知点找更远命中。超距返回None。
- `pose_events_for_frames(line_track:LineTrack, tracks:Sequence[TrackedQuad], start_sec:float, end_sec:float, cfg:MotionAssConfig, alignment:str, line_idx:int)->list[dict]`：在现有 `_chain_events` 内抽取单帧姿态标签生成逻辑，供此 helper 和既有 dense fallback 共享。时间来自 tracks 的 time_sec；末帧由相邻帧时间差补结束并夹到原区间；量化前合并可合并的同属性相邻帧；若有效帧在量化后坍缩，不静默丢弃，返回明确失败并走有证据的静态回退/报告错误，不自动延长到后续文字。
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

- [ ] 对有有效相交遮挡的文字事件，按原事件覆盖的帧重建逐帧 `pos/frz/scale/fs`；每帧从最近样本得到平面多边形，再使用**当前帧** homography 映射到屏幕。该帧无命中时不带 iclip；有命中时写静态 `\iclip(m ... l ...)`，不在 `\t` 内写 vector clip。相邻完全相同正文/标签/图层的帧事件才可压缩合并。没有任何相交命中的事件原样返回。
- [ ] 把 step fades 应用移到遮挡切分之后，并让 `attach_step_fades` 以原 hold 的绝对边界计算透明度；内部切片边界不能重新从0渐显。亮度原本已位于策略之后，继续用绝对采样曲线与每个新事件 start/end 生成；不搬运原 `\t` 相对时间。mask 的背景遮罩若位于同一物理平面，最终通过B4策略后公共层应用同一遮挡几何；external/whitespace 移出物体后不带原位置裁剪。
- [ ] 增加逐帧 pose helper 回归：原有10px/s平移和0.5→1亮度，在绝对1.2秒处切片前后位置差≤1px、亮度通道差≤2；原事件开头/末尾 fade 保留，新增内部边界没有fade；line_idx、name、layer、policy 字段不丢失。
- [ ] 渲染回归使用 FFmpeg 生成灰底短片，固定字体 `DejaVu Sans`、白色文本 `TEST`、矩形遮挡覆盖其右半；分别渲染无遮挡、遮挡前、命中时、确认清晰后。新测试 helper `render_ass_frame(ass_path:Path,frame:int,fps:int=25)->np.ndarray` 以 subprocess 参数数组执行 FFmpeg，timeout=30秒，用输出 PNG 读取像素。无 FFmpeg 的开发机可 skip，但本文G0/G1交付环境必须执行。

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

- [ ] 运行 `.venv/bin/python -m pytest tests/test_occlusion_mask.py tests/test_occlusion_timeline.py tests/test_occlusion_render.py tests/test_motion_ass.py tests/test_motion_ass_collapse.py -q`；复跑 mail 基础和 protected，检查 f168/f216/f222/f228，保存源图/ASS/纯ASS。对字体之外的画面不做修改。
- [ ] 一并提交 B1/B2 为 `fix: apply occlusion samples on their supported time intervals`。输出量上限按原有有效帧数×受影响行数计算，不能为了减少事件重新全段并集；记录邮件的实际事件增量和耗时。

## B3. 有色遮罩与屏幕亮度绑定

**Files:** 新增 `core/scene_brightness.py`、`tests/test_scene_brightness.py`；修改 `scripts/motion_ass.py`、`core/screen_luma.py`、`core/subtitle_generator/generator.py`、`core/scene_text_policy.py`、`core/roi_runtime_config.py`、`cli.py`、`core/pipeline_worker.py`。补 `tests/test_scene_text_policy.py`、`tests/test_occlusion_render.py`。配置模块已由先行的C1建立，严格按本文任务顺序执行。

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

**Files:** 新增 `core/occluder_contours.py`、`tests/test_occluder_contours.py`；修改 `core/occlusion_mask.py`、`scripts/motion_ass.py`、`core/scene_text_policy.py`、`core/subtitle_generator/generator.py`；扩展 `tests/test_occlusion_render.py`。本轮证据原型在 `docs/superpowers/evidence/2026-09-23-real-video/hand_edge_demo.py`，不得把其中针对此样本的肤色阈值当作通用规则直接搬进生产。

**Interfaces:**

- `ContourRing(points:np.ndarray,is_hole:bool,parent:int|None)` dataclass，points为N×2有限屏幕坐标；parent索引指向所属外轮廓。`ContourResult(rings:list[ContourRing], status:str, reason:str)` dataclass，status仅 `valid/clear/unknown`，rings用当前视频屏幕坐标；clear必须有有效检测依据。
- `refine_occluder_contours(frame_bgr:np.ndarray, foreground_seed:np.ndarray, background_seed:np.ndarray, domain:np.ndarray, *, previous_mask:np.ndarray|None=None)->ContourResult`：全图同尺寸uint8二值mask；冲突seed、前景不足64像素或没有可靠背景返回unknown。
- 新的屏幕轮廓输入统一为 `screen_occlusions:Mapping[int,ContourResult]`，由 `apply_screen_occlusion` 消费；B4轮廓不混入B1的平面 `occlusions`。屏幕轮廓不得再乘homography；从视频屏幕坐标转换到ASS PlayRes恰好一次；原平面检测路径仍按B2映射。
- `apply_screen_occlusion(events:list[dict], screen_occlusions:Mapping[int,ContourResult], *, fps:float, event_geometry:Mapping[str,object])->list[dict]`：映射键为视频帧号，缺失按unknown处理。
- `contours_to_iclip(rings:list[ContourRing])->str`：返回一条包含多个闭合子路径的iclip；无ring返回空串，非有限坐标拒绝；输入点在转换后使用ASS PlayRes坐标。

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
- [ ] 原attach依赖line_idx，无法直接处理没有line_idx的有色mask。实现上述 `apply_screen_occlusion`，在最终策略后按事件自身几何/绝对时间切片，文字和mask分别判断屏幕轮廓相交，不强造mask行号。event_geometry按事件稳定ID保存重建来源（文字LineTrack或mask块四角轨迹）。静态事件以固定行/块框重建，不依赖motion接管成功。mask_only也走该层；`valid`写入iclip，`clear`不裁剪，`unknown`仅允许复用不超过一帧的上一有效轮廓并记录诊断，之后静态回退或不应用裁剪；缺失轮廓按unknown处理。
- [ ] 生成原文字和有色mask的共同iclip（限同一被遮挡物体）；external/whitespace被移走则不带原处裁剪。轮廓消失后恢复正常渲染，不能把曾出现的手轮廓合成全程并集。
- [ ] 合成测试要求主体分割IoU≥0.95、指缝中心像素不属前景、有效裁剪区域字形减少≥80%；邮件f216/f222/f228人工对照边缘，不接受手机背景整体被分成前景。保存失败路径和原因；真实轮廓无人工逐像素真值时不报告虚构IoU。
- [ ] 运行contours、occlusion_mask、occlusion_render和motion集成回归；复跑邮件亮暗与手势序列，并提交 `feat: refine foreground contours for time-local ASS clipping`。生产接入前仍以本文G0/G1基线保护；本轮只是文档和独立视觉试验。

## D1. 字形支持的屏幕位置测量

**Files:** 新增 `core/text_motion_evidence.py`、`tests/test_text_motion_evidence.py`；修改 `core/pose_verify.py`、`core/step_segmentation.py`、`scripts/motion_ass.py`；补 `tests/test_pose_verify.py`、`tests/test_motion_ass.py`、`tests/test_motion_ass_collapse.py`。

**Interfaces:**

- `TextMotionEvidence(centers:dict[int,tuple[float,float]], scores:dict[int,float], state:str, anchor:tuple[float,float]|None)` dataclass，state为 `static/moving/unknown`。
- `classify_text_centers(centers:dict[int,tuple[float,float]], scores:dict[int,float], *, tolerance_px:float=2.5, min_score:float=0.45, min_samples:int=3)->TextMotionEvidence`。只消费可信字形匹配；不接受直接从homography生成的center冒充实测。纯中心判定只给出平移证据，不能据此断言角度/尺度不变；static最终还需glyph的角度/尺度证据或参考一致性。
- `masked_text_match(frame_gray:np.ndarray, reference_gray:np.ndarray, glyph_mask:np.ndarray, search_box:tuple[int,int,int,int])->tuple[float,float,float]|None` 返回当前屏幕中心/置信度；无有效字形、单峰不明确返回None。

- [ ] 红测试以已有LinePose构造静字和真移动两种证据：

```python
def test_stationary_text_ignores_background_trajectory():
    from core.text_motion_evidence import classify_text_centers
    centers = {0:(100,100), 10:(100.5,100), 20:(99.5,100)}
    result = classify_text_centers(centers,{0:0.9,10:0.9,20:0.9})
    assert result.state == 'static'
    assert abs(result.anchor[0]-100) < 1

def test_real_motion_is_preserved():
    from core.text_motion_evidence import classify_text_centers
    result = classify_text_centers({0:(100,100),10:(110,100),20:(120,100)},
                                  {0:0.9,10:0.9,20:0.9})
    assert result.state == 'moving'

def test_background_only_match_cannot_establish_motion():
    from core.text_motion_evidence import classify_text_centers
    result = classify_text_centers({0:(100,100),10:(80,100)}, {0:0.2,10:0.2})
    assert result.state == 'unknown'
```

- [ ] 实现判定：有效score≥min_score且中心有限；数量不足返回unknown；坐标中位数为anchor，每点到anchor距离最大值≤按分辨率缩放的2.5px才static。moving还要求至少3个可信点、两个连续间隔方向一致且位移都超过容差；不符合者unknown，不能把随机背景跳点判为连续运动。
- [ ] 参考模板只在OCR行polygon内取字形支持：局部亮度归一、适应暗字/亮字的笔画候选、连通组件去除气泡大边界与大片背景；至少32个有效像素且覆盖率在1%–60%，否则unknown。已有OCR静态行框可以提供独立位置候选，不能使用未来文字实例填补当前行。
- [ ] 在固定参考锚点、前次可信文字位置、平面预测位置各搜索一次（复用解码帧）。对所有候选使用同一个glyph_mask评分，采用masked TM_CCORR_NORMED或显式加权归一相关；最高峰与排除一个行宽范围后的次高峰之差<0.05视为歧义。无字形支持的全灰度相关峰不能替代结果。
- [ ] 将上面测试扩展成图像端到端：96×240随机纹理以每帧5px左移，叠加固定的`TEST`字形；同样背景再让文字每帧2px右移；再做只有背景没有字。可用如下确定性构图，不依赖OCR模型：

```python
rng = np.random.default_rng(23)
background = rng.integers(100,180,(96,240),dtype=np.uint8)
frames = []
for frame in range(12):
    image = np.roll(background, -5*frame, axis=1)
    cv2.putText(image, 'TEST', (70,55), cv2.FONT_HERSHEY_SIMPLEX,
                0.8, 20, 2, cv2.LINE_AA)
    frames.append(image)
```

匹配模板取首帧文字范围、mask只含笔画；静字测得中心最大漂移≤1px，移动组位移方向/量符合2px/帧±1px，背景组unknown。再加入屏幕整体变暗，几何结果不得改变。
- [ ] 在verify_line_tracks中按可见片段使用新测量；只有中心稳定且字形方向/尺度也稳定时，static把该片段LinePose吸附到可信中心、保留参考角度/scale；中心不动但文字真实旋转/缩放的对照必须保留运动；moving用可信字形轨迹；unknown不继续插值背景漂移，输出诊断供A3拒绝接管。不同可见片段不能因为正文相同而连成一个长hold；文字切换/消失保持原可见性门控。
- [ ] step segmentation只能消费可信文字测量，不能把全局背景平移产生的跳点切成假hold/fade。片段static经synthesize_events输出pos，几何标签检查在policy之前进行。日志补state、证据数、字形score和拒绝原因。
- [ ] 测试ASS标签：静止片段无`\move`，t中无frx/fry/frz/fscx/fscy；允许`\t(...,\1c...\alpha...)`。同一测试检查真实移动片段确实仍输出move或等价正确逐帧pos，不能因“禁止标签”把运动锁死。
- [ ] 运行text_motion_evidence、pose_verify、motion_ass、motion_ass_collapse回归；复跑phone11保存policy前轨迹JSON及最终ASS。用户确认的静止片段在f24/f240/f527附近各取前后至少5帧核对，不用跨内容变化的大时间差推断运动。
- [ ] 提交 `fix: isolate text motion from moving scene backgrounds`；完成本文G0/G1真实验收。保留亮度t，并说明剩余字体/识别差异。

## C2. 按行框约束字号，保留样式控制权

**Files:** 新增 `core/line_geometry.py`、`tests/test_line_geometry.py`；修改 `core/subtitle_generator/styling.py`、`core/subtitle_generator/generator.py`；补 `tests/test_roi_pose_tags.py` 与新增 `tests/test_line_geometry_render.py`。

**Interfaces:** `fit_line_size(box:tuple[float,float,float,float], measured:tuple[float,float]|None, base_size:float=100.0)->float|None`；box为屏幕坐标，measured为目标字体在base_size时的墨迹宽/高；无可靠测量时只按行高保守估计。不在OCR循环里启动逐行FFmpeg。Pillow墨迹高度与ASS的em字号并不等价，此函数只提供初值/约束；必须由libass固定字体渲染测试校准，不能仅数值单测通过就声称字号准确。

- [ ] 先写几何单测：

```python
def test_short_metadata_keeps_smaller_size():
    from core.line_geometry import fit_line_size
    assert fit_line_size((0,0,60,12),(500,100)) == 12.0
    assert fit_line_size((0,0,300,30),(500,100)) == 30.0
    assert fit_line_size((0,0,0,30),(500,100)) is None

def test_width_also_limits_size():
    from core.line_geometry import fit_line_size
    assert fit_line_size((0,0,80,30),(500,100)) == 16.0
```

- [ ] 实现纯函数；为非法/NaN/inf/非正框返回None，让调用者保留既有样式并记录诊断。不得把错误框强行变成一像素文字：

```python
import math

def fit_line_size(box, measured, base_size=100.0):
    if len(box) != 4 or not all(math.isfinite(v) for v in box):
        return None
    w, h = box[2]-box[0], box[3]-box[1]
    if w <= 0 or h <= 0:
        return None
    if measured is None:
        return round(h, 2)
    mw, mh = measured
    if not all(math.isfinite(v) and v > 0 for v in (mw,mh,base_size)):
        return round(h, 2)
    return round(base_size * min(w/mw, h/mh), 2)
```

- [ ] Scene styled line 保留 box、height、poly，在无自定义template时写 per-line `\fs`；行位置和对齐继续用现有检测。基础几何不依赖pose；颜色、旋转只由既有 `_apply_roi_pose_tags` 加入。不要通过开启pose来顺带开启不需要的运动处理。
- [ ] 上下字幕在有pose且无模板时保留分行几何；同一个多行事件采用各行 fit 值的最小值，避免组宽超过ROI。保存pose位置优先于默认边距；姿态位置可能不是原字中心，实测只要求正确遵守配置并记录差异。无pose仍按原Top/JP/Default模板字号，不改变传统对白排版。
- [ ] 字体测量只消费已经解析且覆盖正文的本地字体路径，利用既有可选 Pillow 的 `ImageFont.truetype(path,100).getbbox(text)` 测墨迹，LRU缓存上限256项；无路径/缺字/无Pillow用None降级，不能强制引入font_intel可选依赖。实现 `measure_local_text(text:str,font_path:str|None)->tuple[float,float]|None` 于新模块；异常只捕获 OSError/ImportError/ValueError，不能吞其他逻辑错误。本轮不安装字体或自动更换字体族。实际libass字体选择日志仍是最终渲染解释依据。
- [ ] 回归：同屏12px小字和30px正文导出的fs不同；旋转/取色开关关闭时无新增frz/颜色；已有pose标签不被字号拼接破坏；传template_path时不写自动fs；无字体测量时可出片；字号与box成比例，不随全片分辨率错误膨胀。
- [ ] 新渲染测试使用可用固定字体和两个人工行框，测纯ASS字形连通区域：字号层级保持，原本不相交两框内的字形不得相交；允许字体度量差异但记录实测宽高。复跑phone12 f336/f959、4K f288/f480/f959，比较基准图片；4K需保持20条主体双语歌词和静态回退，不以减少正文获得更整洁画面。
- [ ] 运行 geometry、geometry_render、roi_pose_tags 和 scene policy 回归；提交 `fix: constrain scene text size using observed line geometry`。然后执行本文G0/G1整体验收。

## G1. 最终回归和真实画面验收

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
## 可直接运行的复现实验准备

以下代码不是生产功能，不需要改原ROI。第一次执行前确认同名输出目录不存在；重复试验改case后缀，新旧结果都保留。保留原视频和ROI的SHA校验，模型使用现有缓存，不主动安装。

```bash
cd /home/hope/Tools/video_subtitle_ocr/video_subtitle_ocr
export QT_QPA_PLATFORM=offscreen
.venv/bin/python - <<'PY'
from pathlib import Path
import shutil
repo = Path.cwd()
evidence = repo/'docs/superpowers/evidence/2026-09-23-real-video'
run = repo.parent/'test_run/visual_review_after_fixes'
run.mkdir(exist_ok=False)
for name in ('logs','outputs','frames'):
    (run/name).mkdir()
for name in ('run_cases.py','render_review.py','extra_frames.py','inputs.json'):
    shutil.copy2(evidence/name,run/name)
print(run)
PY
cd /home/hope/Tools/video_subtitle_ocr/test_run/visual_review_after_fixes
/home/hope/Tools/video_subtitle_ocr/video_subtitle_ocr/.venv/bin/python run_cases.py
```

运行器固定taskset 0–7，适合本机；其他机器先检查CPU可用亲和核集合。若无这些核/无taskset，修改**新目录脚本副本**为实际可用核并记入运行记录；不同机器不比较纯性能。每个样本timeout=1200秒，超时必须记录且不标成功。大视频渲染同样设有界超时（建议600秒/样本）。

### 邮件开启亮度和遮挡的第五次运行

在Git根运行下列Python片段，复用已归档控制命令，但重新生成输出路径，避免覆盖基准：

```bash
.venv/bin/python - <<'PY'
import json, os, subprocess
from pathlib import Path
repo = Path.cwd()
run = repo.parent/'test_run/visual_review_after_fixes'
records = json.loads((repo/'docs/superpowers/evidence/2026-09-23-real-video/all_runs.json').read_text())
case = next(x for x in records if x['id']=='mail_protected')
cmd = list(case['command'])
cmd[cmd.index('-o')+1] = str(run/'outputs/mail_protected.ass')
env = os.environ.copy()
env.update(case['env_overrides'])
with (run/'logs/mail_protected.log').open('w') as log:
    result = subprocess.run(cmd,cwd=repo,env=env,stdout=log,
                            stderr=subprocess.STDOUT,timeout=1200)
assert result.returncode == 0, result.returncode
PY
cd /home/hope/Tools/video_subtitle_ocr/test_run/visual_review_after_fixes
/home/hope/Tools/video_subtitle_ocr/video_subtitle_ocr/.venv/bin/python render_review.py mail phone11 phone12 opening4k mail_protected
/home/hope/Tools/video_subtitle_ocr/video_subtitle_ocr/.venv/bin/python extra_frames.py
```

四个基础样本和这一控制运行的请求参数保持不变。额外mask/whitespace/mask_only试验另写ROI副本和输出名，不混进基准计数。原始5次结果都是缺陷基准，**不可作为要求逐字节相等的golden**；源图像应相同，字幕应按契约改变。

### G0补充：素材和测试日志

```bash
cd /home/hope/Tools/video_subtitle_ocr/video_subtitle_ocr/docs/superpowers/evidence/2026-09-23-real-video
sha256sum -c SHA256SUMS
cd /home/hope/Tools/video_subtitle_ocr/video_subtitle_ocr
QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest -q --junitxml=/home/hope/Tools/video_subtitle_ocr_backups/20260923-real-video-optimization/baseline-tests.xml > /home/hope/Tools/video_subtitle_ocr_backups/20260923-real-video-optimization/baseline-tests.log 2>&1
```

执行者必须读取pytest退出码与日志，重定向不等于通过。原素材校验示例：

```python
import hashlib,json
from pathlib import Path
manifest = json.loads(Path('docs/superpowers/evidence/2026-09-23-real-video/source_manifest.json').read_text())
for row in manifest:
    for key in ('video','roi'):
        digest = hashlib.sha256()
        with Path(row[key]).open('rb') as f:
            for chunk in iter(lambda: f.read(1024*1024), b''):
                digest.update(chunk)
        assert digest.hexdigest() == row[key+'_sha256'], row[key]
```

## G1补充：历史能力与入口烟测

- [ ] 运行 `tests/test_control_panel_quick_mode.py`、`tests/test_roi_canvas_editing.py`、`tests/test_scene_text_policy_gui.py`：简洁和完整模式均可见矩形/多边形/编辑；已有ROI拖动、缩放、顶点编辑不退化。
- [ ] 在可用GUI中加载现有视频，保存一份ROI副本、重载，核对语言、pose、亮度、遮挡、policy；同一有效配置分别由GUI/CLI出片。二者原有边界精修差异需说明，不要求时间戳逐字节相等。无图形环境则记录未测，并移交有GUI环境完成；不能伪报烟测通过。
- [ ] 运行 `tests/test_scene_text_policy.py`、`tests/test_scene_text_policy_mask_only.py`、`tests/test_pipeline_scene_text_policy.py`，覆盖所有策略、图层、重复正文不同身份、空/非法框、无轨迹、混合ROI以及回退日志。保留原PolicyResult的三元组解包兼容和诊断字段。
- [ ] 在ROI副本中分别设overlap/mask/mask_only/external/whitespace，使用邮件素材生成独立ASS并渲染。检查实际applied policy；回退到external不能冒充mask或whitespace成功。合成均匀背景保证mask路径、构造足够空带保证whitespace路径，补足真实素材未命中的分支。
- [ ] 运行既有翻译/字体闭环单测，使用mock或离线模式；确保轨迹事件新增roi和候选选择不会重复翻译，mask_only的Comment不意外转为可见文本。不要改翻译或字体许可策略。
- [ ] 运行生命周期、ROI末帧、原子ASS和LLM backoff回归；保持SDK max_retries=0，外层最多10次、单次等待上限60秒、总退避300秒的既有约束，失败可恢复。不能将请求耗时与退避预算混成一个指标。

## 交付记录（2026-09-23 实施完成）

实施提交（分支 `optimize/source-analysis-20260923`，基线 `20231ba`，文档基点 `3232162`）：

| 任务 | 提交 | 交付记录 |
| --- | --- | --- |
| G0 | 标签 `baseline/real-video-optimization-20260923` → `20231ba` | 备份 `/home/hope/Tools/video_subtitle_ocr_backups/20260923-real-video-optimization/`（bundle/archive/SHA256SUMS 已校验）；基线测试 1595 passed, 1 skipped（baseline-tests.log/xml）；素材 4 条 hash 校验通过 |
| C1 | `5ea3eca` | 新增 `core/roi_runtime_config.py`（read_roi_config/effective_ocr_options/resolve_roi_policies/collect_roi_pose_tags/collect_roi_auto_brightness/collect_roi_occlusion_clip）；`--lang`/`--scene-text-policy` 缺省改 None；策略解析提前到所有阶段之前；CLI/GUI 共用 effective_ocr_options；25 项单测 + 入口级 spy |
| A1 | `9f44870` | `_clamp_same_roi_event_overlaps` 保守守卫（Scene 不参与、等起点不齐平、同 tags 才齐平、复制输入）；phone12 重跑 1441 条 Dialogue 全保留、零时长 1138→0 |
| A2 | `2ae781a` | 新增 `core/scene_timeline.py`（TimedRow/ActiveSlice/active_slices）；`_apply_external` 按活动集合切片（歧义同文保留原事件并记 notes）；生成器静态路径逐切片布局 + 仅同显示属性相邻合并；phone11 重跑 1→50 条活动切片 NoteBox |
| A3 | `04d539a` + `125fd0e`/`62a75bc`/`31dea97`（端点归一/对称噪声） | 新增 `core/trajectory_fidelity.py`；`build_motion_events` summary 增 `pre_policy_events`；转换器 `motion_evidence` 参数 + 拒绝 ROI 候选整体移除；CLI/GUI 显式传 roi。phone11 验证：错误接管被拒（轨迹声称 t=0 起 10 行而静态无）候选移除；mail 经端点归一与对称噪声剔除后按新证据判定（见 G1 局限） |
| B1/B2 | `58321b7` | `collect_occlusions` samples side-channel（[]/None/命中三态）；attach 从检测键取候选（废除二次七帧抽样）；新增 `core/occlusion_timeline.py`（sample_for_frame/pose_events_for_frames）离散逐帧切片 + 当前帧单应静态 iclip；attach_step_fades 移到切分后按原 hold 绝对边界注入；libass 像素回归（test_occlusion_render：命中区字形减≥80%、清晰区不裁） |
| B3 | `fd8e53c` | 新增 `core/scene_brightness.py`（apply_scene_brightness 共用循环、base_color 保留不 pop、遮罩不写 alpha）；`sample_visible_background_luma` 排除前景、<64px None；静态遮罩曲线 `sample_static_mask_luma_curve`；转换器 `roi_auto_brightness` 接线 CLI/GUI；暗屏合成用例通过 |
| B4 | `263664c` + `0484fa9`（窗口门控） | 新增 `core/occluder_contours.py`（build_seeds/refine_occluder_contours/contours_to_iclip/apply_screen_occlusion，ContourResult 三态、RETR_CCOMP 保留指缝洞、顶点≤512）；静态策略路径与普通 Scene 路径接入；屏幕细化限定在平面检测命中窗口（±5 帧）内，无窗口证据不猜测；合成 IoU≥0.95/指缝不吞/unknown 反例通过 |
| D1 | `c0c244b` | 新增 `core/text_motion_evidence.py`（classify_text_centers/masked_text_match/glyph_mask_from_reference）；图像端到端（移动背景+固定 TEST 字形：静字漂移≤1px、真运动 2px/帧被测得、背景组 unknown、调暗不变）；step_segmentation 回退前判定并留痕（`HoldSplitResult.motion_states`），unknown 诊断供 A3 把关 |
| C2 | `c37838b` | 新增 `core/line_geometry.py`（fit_line_size/measure_local_text，LRU≤256）；Scene 行无模板时写 per-line `\fs`（行框约束）；TOP 多行事件 pose 时取各行 fit 最小值；libass 渲染回归（字号层级保持、框间无字形泄漏）；模板传参时不写自动 fs |
| G1 | `672457f`/`79d6a95`/`53d0cad` | 最终全量 **1721 passed, 1 skipped, 14 warnings**（归档 final-tests.log/xml 生成于 53d0cad 之前、记录 1718；2026-09-24 在 HEAD `8d1a8ba` 复跑确认 1721，见 final-tests-head8d1a8ba.log/xml，ruff F 与 git diff --check 同次复验通过）；GUI 烟测 49 passed（quick_mode/roi_canvas_editing/scene_text_policy_gui）；五次真实视频全部 exit 0（新目录 `test_run/visual_review_after_fixes/`），渲染审阅帧 + f120/f144/f168/f222 额外帧齐全；2026-09-24 独立复核：五份 ASS 结构检查（零时长/move/iclip 计数）与 acceptance_summary.json 完全一致 |

### G1 真实验收结果（acceptance_summary.json）

| 样本 | Dialogue | 零时长 | iclip | move | 关键观察 |
| --- | ---: | ---: | ---: | ---: | --- |
| mail | 253 | 0 | 0 | 0 | 静态路径接管（A3 判定见局限）；C1 后运动引擎加载 `PP-OCRv6_small_det/rec`（日志确认） |
| phone11 | 50 | 0 | 0 | 0 | 1→50 条活动切片；无 \move/几何 \t；ごめんね [1.00,3.08)、お父さんには言わないで [20.56,21.02) 恢复 |
| phone12 | 1441 | 0 | 0 | 0 | 共存行完整保留（非删除达标） |
| opening4k | 20 | 0 | 0 | 0 | 主体歌词 20 条保持 |
| mail_protected | 354 | 0 | 140 | 0 | iclip 全部集中在手部窗口（第 8–10 秒，检测 207–237 帧）；f168 清晰无裁剪；C2 行级 fs 生效 |

### 已知局限与未完全达标项（如实记录）

1. **f222 手部边缘残留文字碎片**：静态轮廓裁剪后手部大部分区域干净，但指缘仍有碎片（见 `frames/mail_protected_f222_comparison.jpg`）。原因：单参考帧差分对内容渐显屏幕的对齐局限；按方案 B4 边界（"本轮只是文档和独立视觉试验"）记为部分达标，后续需平面对齐的逐帧细化。
2. **mail/phone11 轨迹接管被 A3 拒绝**：mail 轨迹证据把 13 行内容冻结在整段（含已滚出屏幕的标题行）——正是 V2 形态，判定符合设计；拒绝后静态路径承接输出（内容随时间正确）。mail 事件数 24→253 为轨迹→静态路径切换的预期结果，非重复实现。
3. **zero_duration_repro.json 等基准证据**：原 5 次输出为缺陷基准，非逐字节 golden；本轮新输出按契约改变。
4. GUI 有形环境烟测以离屏单测覆盖（49 项）；未做带显示器的手工 GUI 全流程，无图形环境部分如实未测。

## 给另一位AI的启动指令

> 请在当前源码分支实施 `docs/FINAL_OPTIMIZATION_DESIGN.md` 和 `docs/FINAL_IMPLEMENTATION_PLAN.md`。先读两份最终文件和相应源码；不要从旧main或旧计划重新设计。基础优化已完成，A/B/C/D质量修复尚未实施。先完成G0保护和基线测试，再按任务顺序最小修改、红绿回归、独立提交。保留原素材/ROI和基准证据，不调用付费服务、不安装新模型字体、不发布。真实视频和libass抽帧验收是必需项；不能删除有效事件、关闭能力或提高模型档位掩盖问题。最终将提交与验证结果填回本计划，输出新证据并明确局限。
