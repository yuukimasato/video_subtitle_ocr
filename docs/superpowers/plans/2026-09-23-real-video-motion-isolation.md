# D：静止文字与移动背景解耦 Implementation Plan

> **For agentic workers:** 逐项实施、自审和提交；不依赖未安装的执行技能，不自动派发子任务。

**Goal:** 11号视频的静止聊天文字不随右向左移动的风景漂移，同时保留真实文字运动和亮度动画。

**Architecture:** 在已有pose_verify上加入以字形为依据的屏幕测量和固定锚点候选。以文字可见实例为单位判定静止，未知测量交给静态回退；背景平面只能提供候选，不能决定文字运动。

**Tech Stack:** Python、NumPy、OpenCV、pytest、既有LineTrack与ASS合成器。

**Spec:** [设计 V6](../specs/2026-09-23-real-video-optimization-design.md)，同时遵守V2的时间/内容约束。

## Global Constraints

先完成[实施总表](2026-09-23-real-video-implementation.md)基线。不是全片禁止move，也不是删除所有t；颜色/alpha的t合法。不能把文字不再可见视为仍静止显示。保留已有解码缓存和内存上限。

## D1. 字形支持的屏幕位置测量

**Files:** 新增 `core/text_motion_evidence.py`、`tests/test_text_motion_evidence.py`；修改 `core/pose_verify.py`、`core/step_segmentation.py`、`scripts/motion_ass.py`；补 `tests/test_pose_verify.py`、`tests/test_motion_ass.py`、`tests/test_motion_ass_collapse.py`。

**Interfaces:**

- `TextMotionEvidence(centers:dict[int,tuple[float,float]], scores:dict[int,float], state:str, anchor:tuple[float,float]|None)` dataclass，state为 `static/moving/unknown`。
- `classify_text_centers(centers:dict[int,tuple[float,float]], scores:dict[int,float], *, tolerance_px:float=2.5, min_score:float=0.45, min_samples:int=3)->TextMotionEvidence`。只消费可信字形匹配；不接受直接从homography生成的center冒充实测。
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
- [ ] 在verify_line_tracks中按可见片段使用新测量；static把该片段LinePose吸附到可信中心、保留参考角度/scale；moving用可信字形轨迹；unknown不继续插值背景漂移，输出诊断供A3拒绝接管。不同可见片段不能因为正文相同而连成一个长hold；文字切换/消失保持原可见性门控。
- [ ] step segmentation只能消费可信文字测量，不能把全局背景平移产生的跳点切成假hold/fade。片段static经synthesize_events输出pos，几何标签检查在policy之前进行。日志补state、证据数、字形score和拒绝原因。
- [ ] 测试ASS标签：静止片段无`\move`，t中无frx/fry/frz/fscx/fscy；允许`\t(...,\1c...\alpha...)`。同一测试检查真实移动片段确实仍输出move或等价正确逐帧pos，不能因“禁止标签”把运动锁死。
- [ ] 运行text_motion_evidence、pose_verify、motion_ass、motion_ass_collapse回归；复跑phone11保存policy前轨迹JSON及最终ASS。用户确认的静止片段在f24/f240/f527附近各取前后至少5帧核对，不用跨内容变化的大时间差推断运动。
- [ ] 提交 `fix: isolate text motion from moving scene backgrounds`；完成总表真实验收。保留亮度t，并说明剩余字体/识别差异。
