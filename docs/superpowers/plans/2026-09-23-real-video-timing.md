# A：多行时序与轨迹接管 Implementation Plan

> **For agentic workers:** 按复选框在当前任务逐项实施、自审和提交；不依赖未安装的执行技能，不自动派发子任务。

**Goal:** 保留同屏多行、保证策略不产生未来文字，并拒绝内容不完整的整 ROI 轨迹接管。

**Architecture:** 先修既有合并器，再新增小型纯函数模块管理活动行区间；静态/运动策略共同消费这些区间。轨迹与静态的内容对照发生在策略合并之前，失败时保留静态结果。

**Tech Stack:** Python、pytest、既有 ASS 字典事件和 OCR 分组。

**Spec:** [设计 V1/V2](../specs/2026-09-23-real-video-optimization-design.md)。

## Global Constraints

遵守[总表](2026-09-23-real-video-implementation.md)全部约束，先完成基线。新 helper 用百分秒整数、半开区间；同文不同行身份不能合并；不增加模型调用。

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
- [ ] 运行 `pytest tests/test_scene_timeline.py tests/test_scene_text_policy.py tests/test_scene_text_policy_mask_only.py tests/test_pipeline_scene_text_policy.py -q`；提交 `fix: preserve active text intervals in scene policies`。

## A3. 接管同时验证内容，失败保留静态

**Files:** 新增 `core/trajectory_fidelity.py`、`tests/test_trajectory_fidelity.py`；修改 `scripts/motion_ass.py`、`cli.py`、`core/pipeline_worker.py`、`core/subtitle_generator/generator.py`；补 `tests/test_trajectory_coverage_gate.py`、`tests/test_motion_integration.py`。

**Interfaces:** `TextSpan(start_cs:int,end_cs:int,text:str)` 不可变 dataclass；`FidelityResult(ok:bool,reason:str)` 不可变 dataclass；`check_text_fidelity(static:Sequence[TextSpan], motion:Sequence[TextSpan])->FidelityResult`。`build_motion_events` 现有 summary 字典新增 `pre_policy_events`（复制策略前原始行事件）；转换器新增可选 `motion_evidence: dict[str,list[dict]] | None = None`，旧签名向后兼容。不得把策略生成的 NoteBox 当作原始行证据。

- [ ] 红测试：时间100%覆盖而内容错误必须拒绝，内容完全相同接受；无静态证据返回 `ok=False, reason='unverified'`：

```python
def test_full_coverage_is_not_text_fidelity():
    from core.trajectory_fidelity import TextSpan, check_text_fidelity
    static = [TextSpan(0,100,'甲'), TextSpan(100,200,'乙')]
    motion = [TextSpan(0,200,'甲')]
    assert not check_text_fidelity(static,motion).ok
    assert check_text_fidelity(static,static).ok
    assert not check_text_fidelity([],motion).ok
```

- [ ] 实现无新阈值的保守对照：对两路全部开始/结束端点划片；各片分别收集活动正文，使用 Unicode NFKC、去空白与标点（保留字母数字）规范化，再把字符 Counter 合并；两路 Counter 必须相同且至少有一个有效片。这样不受 OCR 分行差异影响，重复字数仍不能丢。任何 mismatch 返回首个片段位置与原因；不把此证据声称为词序或完整语义证明。非法/非正时长拒绝。
- [ ] 生成器先建立现有静态分组和各组时间，抽出 TextSpan（只作为证据，不新增 OCR），再决定是否跳过静态。现有 coverage 仍为前置必要条件；只有新 fidelity 也通过才允许 motion_roi_ids 抑制整个 ROI。CLI/GUI 将 summary 的 pre_policy_events 按 ROI 转交，不能将已丢弃的候选事件混入最终输出。直接 `--motion-quad` 没有关联静态 ROI 时只作为显式附加输出，不擅自抑制其他 ROI。
- [ ] 旧调用没有新 evidence 时保留静态并记录 unverified；已有手工传 motion_events 的兼容测试要覆盖“附加输出”和“抑制 ROI”两种语义。无需修改 `trajectory_takeover_ok` 的返回元组，避免破坏仅计算覆盖率的调用者。
- [ ] phone11 复跑：验证整段 NoteBox 消失、活动文本随画面更新，并检查设计列出的缺失短句。若静态 OCR 也缺字，追查原始 OCR/分组边界并记录具体失败，不通过提高模型档位或把所有历史文字加入词表蒙混过关。
- [ ] 运行新增 fidelity、coverage、motion integration、CLI shared stages 回归；提交 `fix: require temporal text fidelity before trajectory takeover`。再运行总表最终验收。
