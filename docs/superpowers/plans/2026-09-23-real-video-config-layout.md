# C：配置一致性与行几何 Implementation Plan

> **For agentic workers:** 逐项实施、自审和提交；不依赖未安装的执行技能，不自动派发子任务。

**Goal:** 用户配置在静态/运动和 CLI/GUI 中一致生效，避免默认字号把小字放大或使 4K 字幕偏位。

**Architecture:** 新增一个轻量配置解析模块，入口先解析再运行阶段。布局沿用现有定位/对齐模块，字号消费已有行框；不引入自动字体识别或新的模型路径。

**Tech Stack:** Python、argparse、pytest、现有 OCR 行几何、FFmpeg/libass。

**Spec:** [设计 V4/V5](../specs/2026-09-23-real-video-optimization-design.md)。

## Global Constraints

遵守[总表](2026-09-23-real-video-implementation.md)。语言优先级为 ROI > 显式 CLI 全局语言 > 文件顶层语言 > ch；模型和 scene policy 的显式 CLI 参数优先。无参数时默认行为兼容。自定义模板不被自动字号覆盖；不开启 pose 时不附加旋转/取色。

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

## C2. 按行框约束字号，保留样式控制权

**Files:** 新增 `core/line_geometry.py`、`tests/test_line_geometry.py`；修改 `core/subtitle_generator/styling.py`、`core/subtitle_generator/generator.py`；补 `tests/test_roi_pose_tags.py` 与新增 `tests/test_line_geometry_render.py`。

**Interfaces:** `fit_line_size(box:tuple[float,float,float,float], measured:tuple[float,float]|None, base_size:float=100.0)->float|None`；box为屏幕坐标，measured为目标字体在base_size时的墨迹宽/高；无可靠测量时只按行高保守估计。不在OCR循环里启动逐行FFmpeg。

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
- [ ] 运行 geometry、geometry_render、roi_pose_tags 和 scene policy 回归；提交 `fix: constrain scene text size using observed line geometry`。然后执行总表整体验收。
