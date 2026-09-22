<?xml version="1.0" encoding="utf-8"?>
<!DOCTYPE TS>
<TS version="2.1" language="en_US">
<context>
    <name>ColorGatePreviewDialog</name>
    <message>
        <location filename="../components/color_gate_preview_dialog.py" line="+43"/>
        <source>颜色门控：预览</source>
        <translation>Color gate: preview</translation>
    </message>
    <message>
        <location line="+18"/>
        <source>以下缩略来自 ROI 区间内均匀采样。请确认「判定为保留」与您期望一致。若不满意，请选择「取消」或直接关闭对话框，并保持主界面选项关闭或未确认。</source>
        <translation>The thumbnails below are sampled uniformly across the ROI span. Confirm that frames marked &quot;keep&quot; match your expectation. If not, choose &quot;Cancel&quot; or close this dialog, and leave the main-window option off or unconfirmed.</translation>
    </message>
    <message>
        <location line="+10"/>
        <source>阈值与预估</source>
        <translation>Thresholds &amp; estimates</translation>
    </message>
    <message>
        <location line="+8"/>
        <source>命中面积占比下限：</source>
        <translation>Minimum hit-area ratio:</translation>
    </message>
    <message>
        <location line="+21"/>
        <source>采用（用于本次识别）</source>
        <translation>Apply (for this recognition run)</translation>
    </message>
    <message>
        <location line="+3"/>
        <source>不采用</source>
        <translation>Don&apos;t apply</translation>
    </message>
    <message>
        <location line="+25"/>
        <source>采样帧数：{}；在当前阈值下「保留」帧数：{}（约 {:.1f}%）；按此比例粗略估计全流程 ROI 图数量约：{} / {}（原计划）。</source>
        <translation>Sampled frames: {}; frames kept at current threshold: {} (~{:.1f}%). Rough estimate of ROI images for the full run: {} / {} (original plan).</translation>
    </message>
    <message>
        <location line="+12"/>
        <source>保留</source>
        <translation>Keep</translation>
    </message>
    <message>
        <location line="+2"/>
        <source>跳过</source>
        <translation>Skip</translation>
    </message>
    <message>
        <location line="+3"/>
        <source>帧 {} · {}</source>
        <translation>Frame {} · {}</translation>
    </message>
    <message>
        <location line="+3"/>
        <source>max 占比：{ratio:.4f}</source>
        <translation>max ratio: {ratio:.4f}</translation>
    </message>
</context>
<context>
    <name>ControlPanelWidget</name>
    <message>
        <location filename="../components/control_panel.py" line="+110"/>
        <source>样式模板（可选）</source>
        <translation>Style template (optional)</translation>
    </message>
    <message>
        <location line="+5"/>
        <source>点击“浏览”选择 .ass 模板文件</source>
        <translation>Click &quot;Browse&quot; to pick an .ass template file</translation>
    </message>
    <message>
        <location line="+1"/>
        <location line="+530"/>
        <location line="+104"/>
        <location line="+25"/>
        <source>浏览…</source>
        <translation>Browse…</translation>
    </message>
    <message>
        <location line="-652"/>
        <source>识别设置</source>
        <translation>Recognition settings</translation>
    </message>
    <message>
        <location line="+6"/>
        <source>识别语言：</source>
        <translation>Recognition language:</translation>
    </message>
    <message>
        <source>选择字幕语言。中文/英语/日语使用 PP-OCRv6 模型，其他语言自动回落 PP-OCRv5 多语言模型。</source>
        <translation type="vanished">Subtitle language. Chinese/English/Japanese use PP-OCRv6 models; other languages automatically fall back to the multilingual PP-OCRv5 models.</translation>
    </message>
    <message>
        <source>选择字幕语言。中文简体/繁体/英语/日语使用 PP-OCRv6 模型，其他语言自动回落 PP-OCRv5 多语言模型。</source>
        <translation type="vanished">Subtitle language. Simplified/Traditional Chinese, English and Japanese use PP-OCRv6 models; other languages automatically fall back to the multilingual PP-OCRv5 models.</translation>
    </message>
    <message>
        <location line="+3"/>
        <source>选择字幕语言。中文简体/繁体/英语/日语及拉丁语系使用 PP-OCRv6 模型，韩/俄/阿拉伯语等自动回落 PP-OCRv5 多语言模型。</source>
        <translation>Subtitle language. Simplified/Traditional Chinese, English, Japanese and Latin-script languages use PP-OCRv6 models; Korean/Russian/Arabic etc. automatically fall back to the multilingual PP-OCRv5 models.</translation>
    </message>
    <message>
        <location line="+29"/>
        <source>文字保留：</source>
        <translation>Text to keep:</translation>
    </message>
    <message>
        <location line="+3"/>
        <source>控制识别结果保留哪些文字。「保留全部文字」不做任何过滤；更精细的规则可在完整设置中调整。</source>
        <translation>Controls which recognized text is kept. &quot;Keep all text&quot; applies no filtering; finer rules are available in Full settings.</translation>
    </message>
    <message>
        <location line="+8"/>
        <source>保留全部文字</source>
        <translation>Keep all text</translation>
    </message>
    <message>
        <location line="+2"/>
        <source>只保留字幕</source>
        <translation>Subtitles only</translation>
    </message>
    <message>
        <location line="+2"/>
        <source>字幕和画面文字</source>
        <translation>Subtitles and on-screen text</translation>
    </message>
    <message>
        <location line="+2"/>
        <source>自定义（在完整设置中调整）</source>
        <translation>Custom (adjust in Full settings)</translation>
    </message>
    <message>
        <location line="+8"/>
        <source>OCR 引擎（自动选择）</source>
        <translation>OCR engine (auto-select)</translation>
    </message>
    <message>
        <location line="+5"/>
        <source>引擎选择：</source>
        <translation>Engine:</translation>
    </message>
    <message>
        <location line="+3"/>
        <source>检测可用引擎</source>
        <translation>Detect available engines</translation>
    </message>
    <message>
        <location line="+5"/>
        <source>模型档位：</source>
        <translation>Model tier:</translation>
    </message>
    <message>
        <location line="+3"/>
        <source>PaddleOCR 模型档位：Tiny 最快，Small 均衡，Medium 最准。“自动”使用 PP-OCRv6 默认模型。</source>
        <translation>PaddleOCR model tier: Tiny is fastest, Small is balanced, Medium is most accurate. &quot;Auto&quot; uses the default PP-OCRv6 models.</translation>
    </message>
    <message>
        <location line="+24"/>
        <source>文字来源过滤（上下文语义分析）</source>
        <translation>Text source filter (contextual semantics)</translation>
    </message>
    <message>
        <location line="+7"/>
        <source>启用文字来源过滤（实验性）</source>
        <translation>Enable text source filter (experimental)</translation>
    </message>
    <message>
        <location line="+6"/>
        <source>勾选后，OCR 识别结果将经过上下文语义分析，自动区分后期叠加字幕与实拍场景文字。未勾选时保留所有识别到的文字。</source>
        <translation>When checked, OCR results pass contextual semantic analysis that separates overlay subtitles from in-scene text. When unchecked, all recognized text is kept.</translation>
    </message>
    <message>
        <location line="+11"/>
        <source>🔄 加载视频后将自动分析...</source>
        <translation>🔄 Auto analysis starts after a video is loaded...</translation>
    </message>
    <message>
        <location line="+9"/>
        <source>场景预设：</source>
        <translation>Scene preset:</translation>
    </message>
    <message>
        <location line="+2"/>
        <source>选择适合当前视频的场景类型，自动配置文字来源过滤规则</source>
        <translation>Pick the scene type matching this video to auto-configure text source filter rules</translation>
    </message>
    <message>
        <location line="+7"/>
        <source>保留后期叠加文字 (OVERLAY)
    字幕、标题、水印、UI 按钮、弹幕、特效文字</source>
        <translation>Keep overlaid text (OVERLAY)
    Subtitles, titles, watermarks, UI buttons, danmaku, effects text</translation>
    </message>
    <message>
        <location line="+6"/>
        <source>保留实拍场景文字 (SCENE)
    店铺招牌、路牌、宣传海报、书本、屏幕、标牌</source>
        <translation>Keep in-scene text (SCENE)
    Shop signs, street signs, posters, books, screens, plaques</translation>
    </message>
    <message>
        <location line="+6"/>
        <source>保留无法判定文字 (UNKNOWN)
    分类器置信度不足的边界情况</source>
        <translation>Keep undetermined text (UNKNOWN)
    Borderline cases the classifier can&apos;t decide</translation>
    </message>
    <message>
        <location line="+12"/>
        <source>💡 备选方案:</source>
        <translation>💡 Alternatives:</translation>
    </message>
    <message>
        <location line="+16"/>
        <source>🔄 重新自动检测</source>
        <translation>🔄 Re-run auto detection</translation>
    </message>
    <message>
        <location line="+1"/>
        <source>重新采样分析当前视频并更新类型判定</source>
        <translation>Re-sample and analyze this video to update the type verdict</translation>
    </message>
    <message>
        <location line="+1"/>
        <source>重置为预设默认值</source>
        <translation>Reset to preset defaults</translation>
    </message>
    <message>
        <location line="+8"/>
        <source>绘制模式</source>
        <translation>Drawing mode</translation>
    </message>
    <message>
        <location line="+4"/>
        <source>矩形（拖动）</source>
        <translation>Rectangle (drag)</translation>
    </message>
    <message>
        <location line="+1"/>
        <source>多边形（点击）</source>
        <translation>Polygon (click)</translation>
    </message>
    <message>
        <location line="+1"/>
        <source>编辑（拖动调整）</source>
        <translation>Edit (drag to adjust)</translation>
    </message>
    <message>
        <location line="+2"/>
        <source>在画面上直接调整已有 ROI：拖动整体移动；矩形拖 8 个控制点缩放；多边形拖顶点改形。自动检测的 ROI 可用此模式微调。</source>
        <translation>Adjust existing ROIs directly on the frame: drag to move, rectangles have 8 handles to resize, polygons reshape via vertices. Use this mode to fine-tune auto-detected ROIs.</translation>
    </message>
    <message>
        <location line="+13"/>
        <source>字幕生成</source>
        <translation>Subtitle generation</translation>
    </message>
    <message>
        <location line="+4"/>
        <source>开始识别并导出</source>
        <translation>Start recognition &amp; export</translation>
    </message>
    <message>
        <location line="+5"/>
        <source>调整字幕区域</source>
        <translation>Adjust subtitle regions</translation>
    </message>
    <message>
        <location line="+2"/>
        <source>进入 ROI 编辑状态：在画面上拖动、缩放或微调已检测到的字幕区域。</source>
        <translation>Enter ROI edit mode: drag, resize, or fine-tune the detected subtitle regions on the frame.</translation>
    </message>
    <message>
        <location line="+5"/>
        <source>重新检测</source>
        <translation>Re-detect</translation>
    </message>
    <message>
        <location line="+2"/>
        <source>对当前视频重新执行自动分析（场景类型判定）。</source>
        <translation>Re-run automatic analysis of this video (scene type verdict).</translation>
    </message>
    <message>
        <location line="+9"/>
        <source>大模型润色（DeepSeek / OpenAI，可选）</source>
        <translation>LLM polish (DeepSeek / OpenAI, optional)</translation>
    </message>
    <message>
        <location line="+3"/>
        <source>高级选项</source>
        <translation>Advanced options</translation>
    </message>
    <message>
        <location line="+6"/>
        <source>调试模式</source>
        <translation>Debug mode</translation>
    </message>
    <message>
        <location line="+1"/>
        <source>可视化输出</source>
        <translation>Visualization output</translation>
    </message>
    <message>
        <location line="+1"/>
        <source>内存模式（实验性）</source>
        <translation>In-memory mode (experimental)</translation>
    </message>
    <message>
        <location line="+1"/>
        <source>保存中间 JSON</source>
        <translation>Save intermediate JSON</translation>
    </message>
    <message>
        <location line="+1"/>
        <source>按时间分片并行（可选）</source>
        <translation>Parallel by time slices (optional)</translation>
    </message>
    <message>
        <location line="+1"/>
        <source>合并 ROI（每帧只 OCR 一次）</source>
        <translation>Merge ROIs (one OCR pass per frame)</translation>
    </message>
    <message>
        <location line="+2"/>
        <location line="+3"/>
        <source>秒</source>
        <translation>s</translation>
    </message>
    <message>
        <location line="+18"/>
        <source>自动检测移动文字（轨迹字幕）</source>
        <translation>Auto-detect moving text (trajectory subtitles)</translation>
    </message>
    <message>
        <location line="+7"/>
        <source>无需手动勾选「写入画面位置标签」：开始识别时在每个 ROI 范围内采样 OCR，若文字行心位移超过移动门限（24px），自动对该区域走移动文字轨迹管线并抑制静态碎片事件。检测成本与采样密度（0.5 秒/帧）成正比，建议 ROI 尽量圈紧移动文字。</source>
        <translation>No need to tick &quot;Write plane-position tags&quot; manually: at recognition start, each ROI is sample-OCR&apos;ed, and any text line whose centre moves beyond the movement threshold (24 px) is handed to the motion-trajectory pipeline, suppressing its static fragment events. Detection cost scales with sampling density (0.5 s/frame); keep the ROI tight around the moving text.</translation>
    </message>
    <message>
        <location line="+10"/>
        <source>加载视频后自动检测字幕 ROI</source>
        <translation>Auto-detect subtitle ROIs after loading a video</translation>
    </message>
    <message>
        <location line="+3"/>
        <source>加载视频后在后台采样扫描全片，自动生成顶部/底部字幕带 ROI（文字过滤策略为「自动过滤」，可随时在 ROI 列表右键切换）。</source>
        <translation>After a video is loaded, a background sampling scan of the whole file auto-creates top/bottom subtitle band ROIs (text filter policy &quot;auto filter&quot;; right-click any ROI in the list to switch anytime).</translation>
    </message>
    <message>
        <location line="+8"/>
        <source>进程分片（长视频提速）</source>
        <translation>Process sharding (speeds up long videos)</translation>
    </message>
    <message>
        <location line="+4"/>
        <source>自动</source>
        <translation>Auto</translation>
    </message>
    <message>
        <location line="+4"/>
        <source>把长视频按时间切成多个窗口，用多个进程并行识别（与「按时间分片并行」的线程桶不同）。「自动」按 CPU 核数与可用内存决定；仅 CPU 模式生效，短视频自动走单进程。每个并行进程约占 600MB 内存。</source>
        <translation>Splits long videos into time windows recognized by multiple worker processes (different from the thread pools of &quot;parallel by time slices&quot;). &quot;Auto&quot; decides by CPU cores and available memory; CPU mode only, short videos use a single process. Each worker uses about 600MB of memory.</translation>
    </message>
    <message>
        <location line="+25"/>
        <source>半自动：按字幕颜色跳过疑似无字帧（默认关，需预览并确认后才生效）</source>
        <translation>Semi-auto: skip likely textless frames by subtitle color (off by default; takes effect only after preview &amp; confirm)</translation>
    </message>
    <message>
        <location line="+7"/>
        <source>在阶段一抽样 ROI 区间内若干帧并在当前画面上自动标定 HSV。仅当预览结果满意并在对话框中点击「采用」后，才会在本轮 OCR 启用；可随时关闭恢复默认逻辑。若预览不满意或选择「不采用」，请保持勾选关闭或未确认——程序将按原版流程输出全部 ROI 帧。</source>
        <translation>In stage one, samples frames within the ROI span and auto-calibrates HSV on the current frame. It only applies to this OCR run after you click &quot;Apply&quot; in the preview dialog; turn it off anytime to restore default behavior. If the preview looks wrong or you choose &quot;Don&apos;t apply&quot;, keep it unchecked or unconfirmed — the program will process all ROI frames as usual.</translation>
    </message>
    <message>
        <location line="+8"/>
        <source>预览检测效果…</source>
        <translation>Preview detection…</translation>
    </message>
    <message>
        <location line="+16"/>
        <source>DeepSeek 字幕润色</source>
        <translation>DeepSeek subtitle polish</translation>
    </message>
    <message>
        <location line="+2"/>
        <source>兼容 OpenAI 的接口。默认提供方为 DeepSeek，会自动填充 Base URL；当你输入 API Key 后，应用会调用 /v1/models 拉取模型列表（也可手动编辑模型 ID）。</source>
        <translation>OpenAI-compatible API. DeepSeek is the default provider and fills the Base URL automatically; once you enter an API Key, the app fetches /v1/models for the model list (model IDs remain editable).</translation>
    </message>
    <message>
        <location line="+7"/>
        <source>DeepSeek 合并碎片字幕（选择最完整文本并合并时间范围）</source>
        <translation>DeepSeek fragment merge (pick the most complete text and merge time ranges)</translation>
    </message>
    <message>
        <location line="+6"/>
        <source>API Key</source>
        <translation type="unfinished"></translation>
    </message>
    <message>
        <location line="+3"/>
        <source>API Key 不会以明文写入配置文件：优先保存在系统钥匙串（需安装 keyring）；不可用时使用本地加密存储（密钥文件仅当前用户可读）。清除可点击右侧按钮。</source>
        <translation>The API Key is never written to config files in plain text: it is stored in the system keychain when keyring is available, otherwise in a locally encrypted store readable only by the current user. Click the button on the right to clear it.</translation>
    </message>
    <message>
        <location line="+8"/>
        <source>API Base URL</source>
        <translation type="unfinished"></translation>
    </message>
    <message>
        <location line="+3"/>
        <source>大模型提供方</source>
        <translation>LLM provider</translation>
    </message>
    <message>
        <location line="+2"/>
        <source>DeepSeek</source>
        <translation type="unfinished"></translation>
    </message>
    <message>
        <location line="+1"/>
        <source>OpenAI</source>
        <translation type="unfinished"></translation>
    </message>
    <message>
        <location line="+1"/>
        <source>自定义（手动 Base URL）</source>
        <translation>Custom (manual Base URL)</translation>
    </message>
    <message>
        <location line="+8"/>
        <source>模型 ID（可从 API 自动拉取）</source>
        <translation>Model ID (can be fetched from the API)</translation>
    </message>
    <message>
        <location line="+4"/>
        <source>刷新模型列表</source>
        <translation>Refresh model list</translation>
    </message>
    <message>
        <location line="+1"/>
        <location line="+880"/>
        <source>使用 API Key 和 Base URL 拉取 /v1/models。</source>
        <translation>Fetch /v1/models using the API Key and Base URL.</translation>
    </message>
    <message>
        <location line="-878"/>
        <source>清除已存密钥</source>
        <translation>Clear saved key</translation>
    </message>
    <message>
        <location line="+2"/>
        <source>从本机配置中删除已保存的 API Key（输入框会清空）。Base URL 与模型仍会保留。</source>
        <translation>Deletes the saved API Key from this machine&apos;s config (the field is cleared). Base URL and model are kept.</translation>
    </message>
    <message>
        <location line="+7"/>
        <source>DeepSeek 合并策略复核（按更合适的阈值重新合并）</source>
        <translation>DeepSeek merge strategy review (re-merge with better-suited thresholds)</translation>
    </message>
    <message>
        <location line="+36"/>
        <source>AI 翻译（可选）</source>
        <translation>AI translation (optional)</translation>
    </message>
    <message>
        <location line="+4"/>
        <source>把识别出的字幕行交给大模型翻译成目标语言（在润色之后、写出之前执行）。提供方、API Key、Base URL 与模型复用上方「大模型润色」区的配置；失败时自动降级到 VLM 兜底，全部失败保留原文。</source>
        <translation>Translate recognized subtitle lines into the target language with an LLM (runs after polish, before the ASS is written). Provider, API key, base URL and model are reused from the LLM polish section above; a failure falls back to the VLM fallback automatically, and a fully exhausted chain keeps the original text.</translation>
    </message>
    <message>
        <location line="+33"/>
        <source>使用上方「大模型润色」的提供方、API Key、Base URL 与模型。</source>
        <translation>Uses the provider, API key, base URL and model from the LLM polish section above.</translation>
    </message>
    <message>
        <source>把识别出的字幕行交给大模型翻译成目标语言（在润色之后、写出之前执行）。云端提供方复用「大模型润色」区已保存的 API Key；某一级未配置或失败时自动降级到下一级，全部失败保留原文。</source>
        <translation type="vanished">Translate recognized subtitle lines into the target language with an LLM (runs after polish, before the ASS is written). The cloud provider reuses the API key saved in the LLM polish section; an unconfigured or failed level automatically falls back to the next one, and a fully exhausted chain keeps the original text.</translation>
    </message>
    <message>
        <location line="-21"/>
        <source>目标语言：</source>
        <translation>Target language:</translation>
    </message>
    <message>
        <source>翻译提供方：</source>
        <translation type="vanished">Translation provider:</translation>
    </message>
    <message>
        <source>云端 API：OpenAI 兼容端点（默认 DeepSeek）；本地 Sakura：本地部署的 Sakura 日中翻译模型（OpenAI 兼容 /v1 端点）；VLM 兜底：复用 VLM_REFINE_* 环境变量，零配置。</source>
        <translation type="vanished">Cloud API: OpenAI-compatible endpoint (DeepSeek by default). Local Sakura: a locally deployed Sakura JP-ZH translation model (OpenAI-compatible /v1 endpoint). VLM fallback: reuses the VLM_REFINE_* environment variables, zero configuration.</translation>
    </message>
    <message>
        <source>云端 API</source>
        <translation type="vanished">Cloud API</translation>
    </message>
    <message>
        <source>本地 Sakura</source>
        <translation type="vanished">Local Sakura</translation>
    </message>
    <message>
        <source>VLM 兜底</source>
        <translation type="vanished">VLM fallback</translation>
    </message>
    <message>
        <source>API Base URL（云端 / Sakura 共用）</source>
        <translation type="vanished">API base URL (shared by cloud / Sakura)</translation>
    </message>
    <message>
        <source>模型名（如 deepseek-chat / sakura-14b）</source>
        <translation type="vanished">Model name (e.g. deepseek-chat / sakura-14b)</translation>
    </message>
    <message>
        <location line="+31"/>
        <source>术语表 JSON：</source>
        <translation>Glossary JSON:</translation>
    </message>
    <message>
        <location line="+3"/>
        <source>可选。JSON 文件（{&quot;术语&quot;: &quot;译名&quot;}），译文中术语强制一致；文件缺失或格式错误时自动忽略。</source>
        <translation>Optional. A JSON file ({&quot;term&quot;: &quot;translation&quot;}) enforcing consistent terminology in every translation; missing or malformed files are ignored automatically.</translation>
    </message>
    <message>
        <location line="+20"/>
        <source>上下文行数：</source>
        <translation>Context lines:</translation>
    </message>
    <message>
        <location line="+6"/>
        <source>最大行长：</source>
        <translation>Max line length:</translation>
    </message>
    <message>
        <location line="+23"/>
        <source>字体识别（可选）</source>
        <translation>Font recognition (optional)</translation>
    </message>
    <message>
        <location line="+4"/>
        <source>对识别出的字幕行做字体识别（本地计算），输出 Top-N 候选并查询许可类别；识别完成后可在复核对话框中逐条确认字体库更新建议。</source>
        <translation>Recognize the fonts in detected subtitle lines (local computation) and output Top-N candidates with license categories; after recognition, confirm font database update suggestions one by one in the review dialog.</translation>
    </message>
    <message>
        <location line="+11"/>
        <source>Top-N 候选数：</source>
        <translation>Top-N candidates:</translation>
    </message>
    <message>
        <location line="+6"/>
        <source>每组字幕字块输出的候选字体数量。</source>
        <translation>Number of candidate fonts output per subtitle text group.</translation>
    </message>
    <message>
        <location line="+6"/>
        <source>置信度阈值：</source>
        <translation>Confidence threshold:</translation>
    </message>
    <message>
        <location line="+6"/>
        <source>低于阈值的候选只展示、不参与自动替换；0 = 不标记。</source>
        <translation>Candidates below the threshold are display-only and never auto-replaced; 0 = no marking.</translation>
    </message>
    <message>
        <location line="+17"/>
        <source>字体库目录：</source>
        <translation>Font directory:</translation>
    </message>
    <message>
        <location line="+4"/>
        <source>留空 = 仅系统字体目录</source>
        <translation>Empty = system font directories only</translation>
    </message>
    <message>
        <location line="+3"/>
        <source>额外扫描的字体文件目录（可选）；留空 = 仅扫描系统字体目录。</source>
        <translation>Extra font file directories to scan (optional); empty = scan system font directories only.</translation>
    </message>
    <message>
        <location line="+18"/>
        <source>fonts.db 路径：</source>
        <translation>fonts.db path:</translation>
    </message>
    <message>
        <location line="+4"/>
        <source>留空 = 默认字体库路径</source>
        <translation>Empty = default font database path</translation>
    </message>
    <message>
        <location line="+3"/>
        <source>字体许可/映射查询库；留空使用默认 XDG 数据目录下的 fonts.db。</source>
        <translation>Font license/mapping lookup database; empty = fonts.db in the default XDG data directory.</translation>
    </message>
    <message>
        <location line="+46"/>
        <source>完整设置</source>
        <translation>Full settings</translation>
    </message>
    <message>
        <location line="+3"/>
        <source>显示全部设置（文字来源过滤、绘制模式、引擎详情）。已调整的选项保持不变。</source>
        <translation>Show all settings (text source filter, drawing mode, engine details). Adjusted options are preserved.</translation>
    </message>
    <message>
        <location line="+5"/>
        <source>简洁界面</source>
        <translation>Simple view</translation>
    </message>
    <message>
        <location line="+3"/>
        <source>返回一键简洁视图，隐藏高级设置。所有选项保持不变。</source>
        <translation>Return to the one-click simple view and hide advanced settings. All options are preserved.</translation>
    </message>
    <message>
        <location line="+216"/>
        <source>🔒 文字来源过滤已关闭，将保留所有识别到的文字。</source>
        <translation>🔒 Text source filter is off; all recognized text will be kept.</translation>
    </message>
    <message>
        <location line="+4"/>
        <source>🔄 文字来源过滤已启用，加载视频后将自动分析...</source>
        <translation>🔄 Text source filter is on; auto analysis starts after a video is loaded...</translation>
    </message>
    <message>
        <location line="+81"/>
        <source>颜色门控：已关闭（默认）。</source>
        <translation>Color gate: off (default).</translation>
    </message>
    <message>
        <location line="+2"/>
        <source>颜色门控：已勾选，尚未确认。请点击「预览检测效果」并在满意时选择「采用」。</source>
        <translation>Color gate: checked but not confirmed. Click &quot;Preview detection&quot; and choose &quot;Apply&quot; if satisfied.</translation>
    </message>
    <message>
        <location line="+4"/>
        <source>颜色门控：已确认，将用于下一轮「字幕 OCR 识别」阶段一截取。</source>
        <translation>Color gate: confirmed; it will apply in stage one of the next &quot;subtitle OCR&quot; run.</translation>
    </message>
    <message>
        <location line="+165"/>
        <source>选择术语表 JSON 文件</source>
        <translation>Select glossary JSON file</translation>
    </message>
    <message>
        <location line="+2"/>
        <source>术语表 JSON (*.json)</source>
        <translation>Glossary JSON (*.json)</translation>
    </message>
    <message>
        <location line="+84"/>
        <source>选择字体库目录</source>
        <translation>Select font directory</translation>
    </message>
    <message>
        <location line="+9"/>
        <source>选择 fonts.db 文件</source>
        <translation>Select fonts.db file</translation>
    </message>
    <message>
        <location line="+2"/>
        <source>SQLite 字体库 (*.db)</source>
        <translation>SQLite font database (*.db)</translation>
    </message>
    <message>
        <location line="+178"/>
        <source>自动(Auto)</source>
        <translation>Auto</translation>
    </message>
    <message>
        <location line="+7"/>
        <source>（无可用引擎）</source>
        <translation>(no available engine)</translation>
    </message>
    <message>
        <location line="+3"/>
        <source>⚠ 未检测到可用 OCR 引擎</source>
        <translation>⚠ No usable OCR engine detected</translation>
    </message>
    <message>
        <location line="+7"/>
        <source>✅ 已就绪</source>
        <translation>✅ Ready</translation>
    </message>
    <message>
        <location line="+89"/>
        <source>🔄 正在分析视频类型...</source>
        <translation>🔄 Analyzing video type...</translation>
    </message>
    <message>
        <location line="+98"/>
        <location line="+18"/>
        <source>手动模式（已自定义）</source>
        <translation>Manual mode (customized)</translation>
    </message>
    <message>
        <location line="+76"/>
        <source>已恢复上次保存的手动设置</source>
        <translation>Restored your previously saved manual settings</translation>
    </message>
</context>
<context>
    <name>DeepSeekProgressPanel</name>
    <message>
        <location filename="../components/deepseek_progress_panel.py" line="+13"/>
        <source>DeepSeek / 大模型处理进度</source>
        <translation>DeepSeek / LLM progress</translation>
    </message>
    <message>
        <location line="+12"/>
        <source>启用 DeepSeek 后，此处显示处理进度，以及各批次润色前后的字幕对比、碎片合并候选与结果、策略复核说明等，便于核对模型具体改动了哪些字。</source>
        <translation>With DeepSeek enabled, this panel shows progress, per-batch before/after subtitle diffs, fragment merge candidates and results, and strategy review notes, so you can verify exactly what the model changed.</translation>
    </message>
    <message>
        <location line="+9"/>
        <source>清空日志</source>
        <translation>Clear log</translation>
    </message>
</context>
<context>
    <name>FileOperationsWidget</name>
    <message>
        <location filename="../components/file_operations.py" line="+25"/>
        <source>文件操作（支持拖放）</source>
        <translation>File operations (drag &amp; drop supported)</translation>
    </message>
    <message>
        <location line="+6"/>
        <source>加载视频</source>
        <translation>Load video</translation>
    </message>
    <message>
        <location line="+1"/>
        <source>保存 ROI 配置</source>
        <translation>Save ROI config</translation>
    </message>
    <message>
        <location line="+1"/>
        <source>加载 ROI 配置</source>
        <translation>Load ROI config</translation>
    </message>
    <message>
        <location line="+1"/>
        <source>自动检测字幕ROI</source>
        <translation>Auto-detect subtitle ROIs</translation>
    </message>
    <message>
        <location line="+1"/>
        <source>深度扫描（水印/场景字）…</source>
        <translation>Deep scan (watermarks/in-scene text)…</translation>
    </message>
    <message>
        <location line="+21"/>
        <source>语言</source>
        <translation>Language</translation>
    </message>
    <message>
        <location line="+3"/>
        <source>自动（跟随系统）</source>
        <translation>Auto (follow system)</translation>
    </message>
    <message>
        <location line="+1"/>
        <source>简体中文</source>
        <translation type="unfinished"></translation>
    </message>
    <message>
        <location line="+1"/>
        <source>繁體中文</source>
        <translation type="unfinished"></translation>
    </message>
    <message>
        <location line="+1"/>
        <source>English</source>
        <translation type="unfinished"></translation>
    </message>
    <message>
        <location line="+1"/>
        <source>日本語</source>
        <translation>日本語</translation>
    </message>
    <message>
        <location line="+23"/>
        <location line="+20"/>
        <source>切换语言</source>
        <translation>Change language</translation>
    </message>
    <message>
        <location line="-19"/>
        <source>语言设置将在重启应用后生效。要立即重启吗？</source>
        <translation>The language change takes effect after the app restarts. Restart now?</translation>
    </message>
    <message>
        <location line="+20"/>
        <source>无法自动重启，请手动重启应用以应用新语言。</source>
        <translation>Could not restart automatically. Please restart the app manually to apply the new language.</translation>
    </message>
</context>
<context>
    <name>FontMapReviewDialog</name>
    <message>
        <location filename="../components/font_map_review_dialog.py" line="+41"/>
        <source>低置信度，建议人工确认</source>
        <translation>Low confidence; manual review recommended</translation>
    </message>
    <message>
        <location line="+2"/>
        <source>规则未能解析该行</source>
        <translation>The rule pass could not parse this line</translation>
    </message>
    <message>
        <location line="+2"/>
        <source>疑似幻觉字体名（归一化后不是原文行子串）</source>
        <translation>Suspected hallucinated font name (not a substring of the source line after normalization)</translation>
    </message>
    <message>
        <location line="+3"/>
        <source>字体名未命中本地词表</source>
        <translation>Font name not found in the local lexicon</translation>
    </message>
    <message>
        <location line="+2"/>
        <source>LLM 调用失败（有界退避耗尽），本行未结构化</source>
        <translation>LLM call failed (bounded backoff exhausted); this line was not structured</translation>
    </message>
    <message>
        <location line="+3"/>
        <source>LLM 输出不符合约定格式，本行未结构化</source>
        <translation>LLM output did not match the agreed format; this line was not structured</translation>
    </message>
    <message>
        <location line="+3"/>
        <source>未配置 API Key，本行未结构化</source>
        <translation>No API key configured; this line was not structured</translation>
    </message>
    <message>
        <location line="+2"/>
        <source>空行</source>
        <translation>Empty line</translation>
    </message>
    <message>
        <location line="+2"/>
        <source>注释行</source>
        <translation>Comment line</translation>
    </message>
    <message>
        <location line="+34"/>
        <source>第 {0} 行：{1} → {2}</source>
        <translation>Line {0}: {1} → {2}</translation>
    </message>
    <message>
        <location line="+4"/>
        <source>第 {0} 行：{1} →（负映射）</source>
        <translation>Line {0}: {1} → (negative mapping)</translation>
    </message>
    <message>
        <location line="+3"/>
        <source>第 {0} 行：{1}</source>
        <translation>Line {0}: {1}</translation>
    </message>
    <message>
        <location line="+7"/>
        <source>原文：{0}</source>
        <translation>Original: {0}</translation>
    </message>
    <message>
        <location line="+3"/>
        <source>建议：{0} → {1}</source>
        <translation>Suggestion: {0} → {1}</translation>
    </message>
    <message>
        <location line="+4"/>
        <source>建议：{0} 无日文本家对应</source>
        <translation>Suggestion: {0} has no Japanese official counterpart</translation>
    </message>
    <message>
        <location line="+4"/>
        <source>类型：{0} ｜ 置信度：{1} ｜ 来源：{2} ｜ 文件：{3}</source>
        <translation>Type: {0} | Confidence: {1} | Source: {2} | File: {3}</translation>
    </message>
    <message>
        <location line="+5"/>
        <source>理由：{0}</source>
        <translation>Reason: {0}</translation>
    </message>
    <message>
        <location line="+3"/>
        <source>（该条目不写库；如需修正请在源表修订后重跑导入）</source>
        <translation>(this entry is not written to the database; to fix it, revise the source table and rerun the import)</translation>
    </message>
    <message>
        <location line="+11"/>
        <source>字体映射复核</source>
        <translation>Font mapping review</translation>
    </message>
    <message>
        <location line="+18"/>
        <source>无待复核记录。</source>
        <translation>No entries pending review.</translation>
    </message>
    <message>
        <location line="+11"/>
        <source>应用</source>
        <translation>Apply</translation>
    </message>
    <message>
        <location line="+2"/>
        <source>取消</source>
        <translation>Cancel</translation>
    </message>
</context>
<context>
    <name>FontUpdateReviewDialog</name>
    <message>
        <location filename="../components/font_update_review_dialog.py" line="+42"/>
        <source>新增字体记录</source>
        <translation>New font record</translation>
    </message>
    <message>
        <location line="+3"/>
        <source>映射修正</source>
        <translation>Mapping correction</translation>
    </message>
    <message>
        <location line="+3"/>
        <source>新增映射</source>
        <translation>New mapping</translation>
    </message>
    <message>
        <location line="+8"/>
        <source>模型推断（未经人工确认）</source>
        <translation>Model-inferred (not human-confirmed)</translation>
    </message>
    <message>
        <location line="+3"/>
        <source>字形重排证据</source>
        <translation>Glyph-rerank evidence</translation>
    </message>
    <message>
        <location line="+35"/>
        <source>{0}：{1} → {2}</source>
        <translation>{0}: {1} → {2}</translation>
    </message>
    <message>
        <location line="+5"/>
        <source>{0}：{1}</source>
        <translation>{0}: {1}</translation>
    </message>
    <message>
        <location line="+3"/>
        <source>{0}：{1}（不可采纳）</source>
        <translation>{0}: {1} (not actionable)</translation>
    </message>
    <message>
        <location line="+9"/>
        <source>建议：{0} → {1}</source>
        <translation>Suggestion: {0} → {1}</translation>
    </message>
    <message>
        <location line="+5"/>
        <source>建议：新增记录 {0}（许可类别 {1}）</source>
        <translation>Suggestion: add record {0} (license category {1})</translation>
    </message>
    <message>
        <location line="+7"/>
        <source>类型：{0} ｜ 置信度：{1} ｜ 依据：{2} ｜ 方法：{3}</source>
        <translation>Type: {0} | Confidence: {1} | Evidence: {2} | Method: {3}</translation>
    </message>
    <message>
        <location line="+6"/>
        <source>理由：{0}</source>
        <translation>Reason: {0}</translation>
    </message>
    <message>
        <location line="+4"/>
        <source>（该条目无建议值，仅展示）</source>
        <translation>(this entry has no suggested value; display only)</translation>
    </message>
    <message>
        <location line="+11"/>
        <source>字体库更新建议</source>
        <translation>Font database update suggestions</translation>
    </message>
    <message>
        <location line="+13"/>
        <source>勾选 = 采纳并写入用户覆盖层（人工确认语义）；不勾 = 否决。未经确认的模型推断记录不参与自动替换。</source>
        <translation>Check = adopt and write to the user overlay layer (human-confirmed semantics); leave unchecked = reject. Unconfirmed model-inferred records never take part in automatic replacement.</translation>
    </message>
    <message>
        <location line="+13"/>
        <source>无待复核的更新建议。</source>
        <translation>No update suggestions pending review.</translation>
    </message>
    <message>
        <location line="+12"/>
        <source>应用</source>
        <translation>Apply</translation>
    </message>
    <message>
        <location line="+2"/>
        <source>取消</source>
        <translation>Cancel</translation>
    </message>
</context>
<context>
    <name>LogViewerWidget</name>
    <message>
        <location filename="../components/log_viewer.py" line="+10"/>
        <source>日志与进度</source>
        <translation>Logs &amp; progress</translation>
    </message>
</context>
<context>
    <name>OCRToASSOptimizer</name>
    <message>
        <location filename="../core/subtitle_generator/data_grouping.py" line="+47"/>
        <source>Frame {} (ROI: {}) data list length mismatch, skipped.</source>
        <translation type="unfinished"></translation>
    </message>
    <message>
        <location line="+11"/>
        <source>Error processing in-memory data for frame {} (ROI: {}): {}</source>
        <translation type="unfinished"></translation>
    </message>
    <message>
        <location line="+19"/>
        <source>Successfully loaded and organized OCR data by {} ROIs.</source>
        <translation type="unfinished"></translation>
    </message>
    <message>
        <location line="+118"/>
        <source>Merged into {} subtitle groups.</source>
        <translation type="unfinished"></translation>
    </message>
    <message>
        <location filename="../core/subtitle_generator/event_merge.py" line="+185"/>
        <source>Merged {} fragmented ASS lines into {} dialogue events.</source>
        <translation type="unfinished"></translation>
    </message>
    <message>
        <location filename="../core/subtitle_generator/generator.py" line="+254"/>
        <source>Subtitle generator initialized: {}x{} @ {:.2f} FPS</source>
        <translation type="unfinished"></translation>
    </message>
    <message>
        <location line="+2"/>
        <source>Using style template: {}</source>
        <translation type="unfinished"></translation>
    </message>
    <message>
        <location line="+2"/>
        <source>No style template used, generating a rich set of default styles.</source>
        <translation type="unfinished"></translation>
    </message>
    <message>
        <location line="+72"/>
        <source>Scene text policy: cannot open video {} for analysis frame; keeping original placement.</source>
        <translation type="unfinished"></translation>
    </message>
    <message>
        <location line="+5"/>
        <source>Scene text policy: failed to read frame {} for analysis; keeping original placement.</source>
        <translation type="unfinished"></translation>
    </message>
    <message>
        <location line="+9"/>
        <source>Scene text policy: analysis rect {} outside video bounds; keeping original placement.</source>
        <translation type="unfinished"></translation>
    </message>
    <message>
        <location line="+13"/>
        <source>Scene text policy: unknown mode {!r} for {}; keeping original placement.</source>
        <translation type="unfinished"></translation>
    </message>
    <message>
        <location line="+10"/>
        <source>Scene text policy: no analysis rect for {}; keeping original placement.</source>
        <translation type="unfinished"></translation>
    </message>
    <message>
        <location line="+53"/>
        <source>Scene text policy: analysis frame probes (frame, luma) = {} for {}; picked luma {:.1f}.</source>
        <translation type="unfinished"></translation>
    </message>
    <message>
        <location line="+74"/>
        <source>Scene text policy: text moves more than {:.0f}px in {}; static {} would misalign, falling back to external.</source>
        <translation type="unfinished"></translation>
    </message>
    <message>
        <location line="+14"/>
        <source>Scene text policy: {} (ROI {}).</source>
        <translation type="unfinished"></translation>
    </message>
    <message>
        <location line="+3"/>
        <source>Scene text policy for {}: {} applied ({} spec(s)).</source>
        <translation type="unfinished"></translation>
    </message>
    <message>
        <location line="+89"/>
        <source>--- Starting conversion from in-memory data to ASS subtitles ---</source>
        <translation type="unfinished"></translation>
    </message>
    <message>
        <location line="+13"/>
        <source>No valid OCR data found; writing motion-trajectory events only.</source>
        <translation type="unfinished"></translation>
    </message>
    <message>
        <location line="+3"/>
        <source>No valid OCR data found, an empty ASS file will be generated.</source>
        <translation type="unfinished"></translation>
    </message>
    <message>
        <location line="+7"/>
        <source>Processing ROI: {}, containing {} valid frames.</source>
        <translation type="unfinished"></translation>
    </message>
    <message>
        <location line="+5"/>
        <source>ROI {} handled by motion-trajectory pipeline; static events skipped.</source>
        <translation type="unfinished"></translation>
    </message>
    <message>
        <location line="+10"/>
        <source>ROI: {} generated {} subtitle groups.</source>
        <translation type="unfinished"></translation>
    </message>
    <message>
        <location line="+81"/>
        <source>Step 4/4: Starting ASS generation and DeepSeek post-processing...</source>
        <translation type="unfinished"></translation>
    </message>
    <message>
        <location line="+47"/>
        <source>Step 4/4: DeepSeek reviewing merge strategy (round {}/{})...</source>
        <translation type="unfinished"></translation>
    </message>
    <message>
        <location line="+16"/>
        <source>DeepSeek strategy review skipped or failed; keeping merge parameters unchanged.</source>
        <translation type="unfinished"></translation>
    </message>
    <message>
        <location line="+6"/>
        <source>DeepSeek strategy note: {}</source>
        <translation type="unfinished"></translation>
    </message>
    <message>
        <location line="+13"/>
        <source>DeepSeek merge parameters converged.</source>
        <translation type="unfinished"></translation>
    </message>
    <message>
        <location line="+11"/>
        <source>Re-merged subtitles with tuned parameters (gap {:.2f}s ratio {:.3f} overlap {}).</source>
        <translation type="unfinished"></translation>
    </message>
    <message>
        <location line="+32"/>
        <source>Step 4/4: DeepSeek polishing subtitles ({}/{} batches)...</source>
        <translation type="unfinished"></translation>
    </message>
    <message>
        <location line="+14"/>
        <source>Polish output length mismatch, using original subtitles.</source>
        <translation type="unfinished"></translation>
    </message>
    <message>
        <location line="+5"/>
        <source>DeepSeek subtitle polishing applied.</source>
        <translation type="unfinished"></translation>
    </message>
    <message>
        <location line="+81"/>
        <source>Translation cancelled; keeping remaining original subtitles.</source>
        <translation type="unfinished"></translation>
    </message>
    <message>
        <location line="+13"/>
        <source>Step 4/4: Translating subtitles ({}/{} batches)...</source>
        <translation type="unfinished"></translation>
    </message>
    <message>
        <location line="+10"/>
        <source>Translation output length mismatch, keeping original subtitles.</source>
        <translation type="unfinished"></translation>
    </message>
    <message>
        <location line="+7"/>
        <source>Translation ({0}): {1}/{2} batches ok, {3} degraded, providers {4}, shortened {5} lines.</source>
        <translation type="unfinished"></translation>
    </message>
    <message>
        <location line="+13"/>
        <source>Translation stage failed; keeping original subtitles: {0}</source>
        <translation type="unfinished"></translation>
    </message>
    <message>
        <location line="+27"/>
        <source>No valid subtitle groups formed for any ROI, an empty ASS file will be generated.</source>
        <translation type="unfinished"></translation>
    </message>
    <message>
        <location line="-133"/>
        <source>--- Conversion successful ---</source>
        <translation type="unfinished"></translation>
    </message>
    <message>
        <location line="+1"/>
        <source>ASS subtitle file saved to: {}</source>
        <translation type="unfinished"></translation>
    </message>
    <message>
        <location line="+3"/>
        <source>--- Conversion failed ---</source>
        <translation type="unfinished"></translation>
    </message>
    <message>
        <location line="+1"/>
        <source>Error: {}</source>
        <translation type="unfinished"></translation>
    </message>
    <message>
        <location filename="../core/subtitle_generator/llm_merge.py" line="+116"/>
        <source>Step 4/4: DeepSeek merging fragmented Scene subtitles (call {})...</source>
        <translation type="unfinished"></translation>
    </message>
    <message>
        <location line="+59"/>
        <source>DeepSeek merged fragmented events: {} -&gt; {}.</source>
        <translation type="unfinished"></translation>
    </message>
    <message>
        <location filename="../core/subtitle_generator/roi_filters.py" line="+149"/>
        <source>Watermark filter removed {} text line(s).</source>
        <translation type="unfinished"></translation>
    </message>
    <message>
        <location filename="../core/subtitle_generator/source_classification.py" line="+105"/>
        <source>Text source classification: {} OVERLAY, {} SCENE, {} UNKNOWN. Filtered {} -&gt; {} text lines (keep_overlay={}, keep_scene={}, keep_unknown={}).</source>
        <translation type="unfinished"></translation>
    </message>
    <message>
        <location line="+12"/>
        <source>Text source filter skipped keep-all ROIs: {}.</source>
        <translation type="unfinished"></translation>
    </message>
    <message>
        <location filename="../core/subtitle_generator/styling.py" line="+277"/>
        <source>Font compliance gate failed; keeping original header: {}</source>
        <translation type="unfinished"></translation>
    </message>
    <message>
        <location line="+18"/>
        <source>Font compliance decision collection failed (output unaffected): {}</source>
        <translation type="unfinished"></translation>
    </message>
    <message>
        <location line="+20"/>
        <source>Compliance report generation failed (export not blocked): {}</source>
        <translation type="unfinished"></translation>
    </message>
    <message>
        <location line="+22"/>
        <source>Font identification capture failed (output unaffected): {}</source>
        <translation type="unfinished"></translation>
    </message>
    <message>
        <location line="+13"/>
        <source>Font identification closure failed (output unaffected): {}</source>
        <translation type="unfinished"></translation>
    </message>
    <message>
        <location line="+31"/>
        <source>Font update suggestions export failed (export not blocked): {}</source>
        <translation type="unfinished"></translation>
    </message>
    <message>
        <location line="+13"/>
        <source>No &apos;[Events]&apos; tag found in template file. Events will be appended at the end of the file.</source>
        <translation type="unfinished"></translation>
    </message>
    <message>
        <location line="+12"/>
        <source>Failed to read template file {}: {}. Using default styles.</source>
        <translation type="unfinished"></translation>
    </message>
</context>
<context>
    <name>RoiDefinitionWidget</name>
    <message>
        <location filename="../components/roi_definition.py" line="+45"/>
        <source>ROI 定义</source>
        <translation>ROI definition</translation>
    </message>
    <message>
        <location line="+16"/>
        <location line="+23"/>
        <source>后退 1 帧（短按）/ 连续（长按）</source>
        <translation>Back 1 frame (tap) / continuous (hold)</translation>
    </message>
    <message>
        <location line="-18"/>
        <location line="+23"/>
        <source>前进 1 帧（短按）/ 连续（长按）</source>
        <translation>Forward 1 frame (tap) / continuous (hold)</translation>
    </message>
    <message>
        <location line="-20"/>
        <location line="+23"/>
        <source>输入时间（时:分:秒.毫秒）或帧号</source>
        <translation>Enter time (h:m:s.ms) or frame number</translation>
    </message>
    <message>
        <location line="-19"/>
        <source>开始时间：</source>
        <translation>Start time:</translation>
    </message>
    <message>
        <location line="+2"/>
        <source>设为开始时间</source>
        <translation>Set as start time</translation>
    </message>
    <message>
        <location line="+21"/>
        <source>结束时间：</source>
        <translation>End time:</translation>
    </message>
    <message>
        <location line="+2"/>
        <source>设为结束时间</source>
        <translation>Set as end time</translation>
    </message>
    <message>
        <location line="+5"/>
        <source>按文字/描边/阴影颜色限制 OCR（不匹配像素将被遮罩）</source>
        <translation>Restrict OCR by text/outline/shadow color (non-matching pixels are masked)</translation>
    </message>
    <message>
        <location line="+3"/>
        <source>将与所选颜色不接近的像素在 OCR 前置为白色，减少字幕笔画之外的误检。</source>
        <translation>Pixels not close to the chosen colors are turned white before OCR to reduce false detections outside subtitle strokes.</translation>
    </message>
    <message>
        <location line="+7"/>
        <source>OCR 前对 ROI 轻微模糊（降低锯齿/噪声）</source>
        <translation>Slight blur before OCR (reduces aliasing/noise)</translation>
    </message>
    <message>
        <location line="+2"/>
        <source>在 OCR 前对 ROI 裁剪图应用轻微高斯模糊。对噪声大/压缩重的字幕更有帮助。</source>
        <translation>Applies a light Gaussian blur to the ROI crop before OCR. Most useful for noisy or heavily compressed subtitles.</translation>
    </message>
    <message>
        <location line="+8"/>
        <source>淡入/淡出微调（在检测到文字边界附近逐帧 OCR，找更精确的起止时间）</source>
        <translation>Fade in/out refinement (frame-by-frame OCR near detected text boundaries for precise timing)</translation>
    </message>
    <message>
        <location line="+7"/>
        <source>开启后，会只在文字出现/消失的边界附近逐帧 OCR 用于校准时间；ROI 其余部分仍可使用跳帧优化。</source>
        <translation>When on, frame-by-frame OCR runs only near text appear/disappear boundaries to calibrate timing; the rest of the ROI still benefits from frame-skip optimization.</translation>
    </message>
    <message>
        <location line="+8"/>
        <source>写入画面位置标签（\pos \frz \frx \fry，多边形自动计算位置与倾角）</source>
        <translation>Write positioning tags (\pos \frz \frx \fry; polygons compute position and angle automatically)</translation>
    </message>
    <message>
        <location line="+72"/>
        <source>仅作用于场景文字（画面文字）事件；所选模式不可用时自动回退（空白区→遮罩→外置；仅遮罩→外置）。「仅遮罩」把遮罩下方的识别文本写成 Comment 注释行（播放器不渲染），便于在遮罩上自行排版覆写（如绘制译文或 \p 矢量字）。</source>
        <translation>Applies to in-scene (picture text) events only; unavailable modes fall back automatically (whitespace → mask → external; mask-only → external). &quot;Mask only&quot; writes the recognized text under the patch as Comment lines (not rendered), so you can re-typeset over the patch by hand (e.g. draw translated text or \p vector art).</translation>
    </message>
    <message>
        <source>开启后，该 ROI 识别出的字幕事件将附带位置与旋转标签：多边形 ROI 自动计算中心点(\pos)与长边倾角(\frz)；\frx/\fry 保存为 0，可手动微调透视。适合实拍场景文字的原位重铺。</source>
        <translation type="vanished">When on, subtitle events from this ROI carry position and rotation tags: polygon ROIs compute the center (\pos) and long-edge angle (\frz) automatically; \frx/\fry are saved as 0 and can be tweaked manually for perspective. Ideal for re-laying in-scene text in place.</translation>
    </message>
    <message>
        <location line="-65"/>
        <source>开启后，该 ROI 识别出的字幕事件将附带位置与旋转标签：多边形 ROI 自动计算中心点(\pos)与长边倾角(\frz)；\frx/\fry 保存为 0，可手动微调透视。适合实拍场景文字的原位重铺。四点多边形 ROI 会走移动文字轨迹管线：逐帧跟踪平面并合成 \move 运动字幕，静态标签跟不动的画面由轨迹跟随。</source>
        <translation>When enabled, subtitle events from this ROI carry placement and rotation tags: polygon ROIs auto-compute the center (\pos) and long-edge tilt (\frz); \frx/\fry are saved as 0 for manual perspective tuning. Ideal for re-laying live-action on-screen text in place. Four-point polygon ROIs go through the moving-text trajectory pipeline: the plane is tracked frame by frame and \move motion subtitles are synthesized — movement that static tags cannot follow is handled by the trajectory.</translation>
    </message>
    <message>
        <location line="+12"/>
        <source>亮度自适应（轨迹字幕跟随屏幕明暗）</source>
        <translation>Brightness adaptive (trajectory subtitles follow screen dimming)</translation>
    </message>
    <message>
        <location line="+8"/>
        <source>需勾选「写入画面位置标签」且 ROI 为四点多边形（此时走移动文字轨迹管线）。开启后逐帧测量文字平面亮度，为轨迹事件追加 \1c/\alpha \t 标签链，字幕颜色与透明度忠实跟随屏幕变暗/变亮（如手机息屏）。默认关闭。</source>
        <translation>Requires “Write picture position tags” and a four-point polygon ROI (which runs the moving-text trajectory pipeline). Measures the text-plane brightness frame by frame and appends \1c/\alpha \t tag chains to trajectory events, so subtitle color and opacity faithfully follow the screen dimming/brightening (e.g. a phone screen turning off). Off by default.</translation>
    </message>
    <message>
        <location line="+14"/>
        <source>遮挡蒙版（\iclip，手部遮挡时不渲染字幕）</source>
        <translation>Occlusion mask (\iclip: hide subtitles where the hand covers the screen)</translation>
    </message>
    <message>
        <location line="+8"/>
        <source>需勾选「写入画面位置标签」（轨迹模式）。开启后检测手部等遮挡物覆盖文字平面的帧，为受影响的事件追加 \iclip 逆向蒙版，字幕不再渲染到遮挡物上（遮挡结束时自动恢复显示）。默认关闭。</source>
        <translation>Requires &quot;Write plane-position tags&quot; (trajectory mode). Detects frames where a hand or another occluder covers the text plane and appends an \iclip inverse mask to affected events, so subtitles never render over the occluder (display resumes when the occlusion ends). Off by default.</translation>
    </message>
    <message>
        <location line="+15"/>
        <source>叠加（默认）</source>
        <translation>Overlay (default)</translation>
    </message>
    <message>
        <location line="+1"/>
        <source>遮罩原文字</source>
        <translation>Mask original text</translation>
    </message>
    <message>
        <location line="+1"/>
        <source>仅遮罩（供排版覆写）</source>
        <translation>Mask only (for re-typesetting)</translation>
    </message>
    <message>
        <location line="+1"/>
        <source>外置展示框</source>
        <translation>External note box</translation>
    </message>
    <message>
        <location line="+1"/>
        <source>空白区放置</source>
        <translation>Place in whitespace</translation>
    </message>
    <message>
        <source>仅作用于场景文字（画面文字）事件；所选模式不可用时按 空白区→遮罩→外置 自动回退。</source>
        <translation type="vanished">Applies to in-scene (picture text) events only; if the chosen mode is unavailable it falls back automatically: whitespace → mask → external.</translation>
    </message>
    <message>
        <location line="+12"/>
        <source>场景文字显示：</source>
        <translation>Scene text display:</translation>
    </message>
    <message>
        <location line="+10"/>
        <source>自动（跟随全局）</source>
        <translation>Auto (follow global)</translation>
    </message>
    <message>
        <location line="+1"/>
        <source>中文简体</source>
        <translation>中文简体</translation>
    </message>
    <message>
        <location line="+1"/>
        <source>中文繁體</source>
        <translation>中文繁體</translation>
    </message>
    <message>
        <location line="+1"/>
        <source>English</source>
        <translation type="unfinished"></translation>
    </message>
    <message>
        <location line="+1"/>
        <source>日本語</source>
        <translation>日本語</translation>
    </message>
    <message>
        <location line="+1"/>
        <source>한국어</source>
        <translation type="unfinished"></translation>
    </message>
    <message>
        <location line="+1"/>
        <source>Русский</source>
        <translation type="unfinished"></translation>
    </message>
    <message>
        <location line="+1"/>
        <source>Français</source>
        <translation type="unfinished"></translation>
    </message>
    <message>
        <location line="+1"/>
        <source>Deutsch</source>
        <translation type="unfinished"></translation>
    </message>
    <message>
        <location line="+1"/>
        <source>Italiano</source>
        <translation type="unfinished"></translation>
    </message>
    <message>
        <location line="+1"/>
        <source>Español</source>
        <translation type="unfinished"></translation>
    </message>
    <message>
        <location line="+1"/>
        <source>Português</source>
        <translation type="unfinished"></translation>
    </message>
    <message>
        <location line="+1"/>
        <source>العربية</source>
        <translation type="unfinished"></translation>
    </message>
    <message>
        <location line="+4"/>
        <source>该 ROI 单独使用的 OCR 识别语言；默认「自动」跟随控制面板的全局「识别语言」。双语字幕请给每种语言各画一个 ROI 并分别指定语言（如繁中行设「中文繁體」、日语行设「日本語」），各 ROI 用各自的识别模型，避免单一模型漏认另一种文字。注意：每个不同语言会多加载一份识别模型，内存占用相应增加；RapidOCR 引擎不区分语言，此设置仅对 PaddleOCR 生效。</source>
        <translation>OCR language used only by this ROI; the default &quot;Auto&quot; follows the global &quot;Recognition language&quot; in the control panel. For bilingual subtitles, draw one ROI per language and assign each its own language (e.g. the Traditional-Chinese line as 中文繁體, the Japanese line as 日本語), so each ROI runs its own recognition model instead of one model missing the other script. Note: every distinct language loads an extra recognition model and memory use grows accordingly; the RapidOCR engine uses a single multilingual model and ignores this setting — it applies to PaddleOCR only.</translation>
    </message>
    <message>
        <location line="+10"/>
        <source>识别语言：</source>
        <translation>Recognition language:</translation>
    </message>
    <message>
        <location line="+13"/>
        <source>文字颜色…</source>
        <translation>Text color…</translation>
    </message>
    <message>
        <location line="+1"/>
        <source>描边颜色…</source>
        <translation>Outline color…</translation>
    </message>
    <message>
        <location line="+1"/>
        <source>阴影颜色…</source>
        <translation>Shadow color…</translation>
    </message>
    <message>
        <location line="+11"/>
        <source>RGB 容差：</source>
        <translation>RGB tolerance:</translation>
    </message>
    <message>
        <location line="+5"/>
        <source>单通道距离 0–255；数值越大，包含越多相近色阶（抗锯齿/渐变更稳）。</source>
        <translation>Per-channel distance 0–255; higher values admit more similar shades (stabler with anti-aliasing/gradients).</translation>
    </message>
    <message>
        <location line="+3"/>
        <source>形态学：</source>
        <translation>Morphology:</translation>
    </message>
    <message>
        <location line="+5"/>
        <source>闭运算核大小（奇数）；可在遮罩后连接断裂笔画。</source>
        <translation>Closing kernel size (odd); reconnects broken strokes after masking.</translation>
    </message>
    <message>
        <location line="+8"/>
        <source>添加新 ROI</source>
        <translation>Add new ROI</translation>
    </message>
    <message>
        <location line="+1"/>
        <source>更新选中 ROI</source>
        <translation>Update selected ROI</translation>
    </message>
    <message>
        <location line="+1"/>
        <source>删除选中 ROI</source>
        <translation>Delete selected ROI</translation>
    </message>
    <message>
        <location line="+81"/>
        <source>字幕文字颜色</source>
        <translation>Subtitle text color</translation>
    </message>
    <message>
        <location line="+10"/>
        <source>字幕描边颜色</source>
        <translation>Subtitle outline color</translation>
    </message>
    <message>
        <location line="+10"/>
        <source>字幕阴影颜色</source>
        <translation>Subtitle shadow color</translation>
    </message>
</context>
<context>
    <name>RoiListWidget</name>
    <message>
        <location filename="../components/roi_list.py" line="+17"/>
        <source>ROI 列表</source>
        <translation>ROI list</translation>
    </message>
    <message>
        <location line="+33"/>
        <source>ROI {}：帧[{}-{}] 时间[{} - {}]</source>
        <translation>ROI {}: frames[{}-{}] time[{} - {}]</translation>
    </message>
    <message>
        <location line="+3"/>
        <source>[颜色掩膜]</source>
        <translation>[color mask]</translation>
    </message>
    <message>
        <location line="+2"/>
        <source>[模糊]</source>
        <translation>[blur]</translation>
    </message>
    <message>
        <location line="+3"/>
        <source>[淡入淡出微调]</source>
        <translation>[fade refinement]</translation>
    </message>
    <message>
        <location line="+2"/>
        <source>[自动过滤]</source>
        <translation>[auto filter]</translation>
    </message>
    <message>
        <location line="+22"/>
        <source>复制</source>
        <translation>Copy</translation>
    </message>
    <message>
        <location line="+5"/>
        <source>粘贴到此项之后</source>
        <translation>Paste after this item</translation>
    </message>
    <message>
        <location line="+4"/>
        <source>删除</source>
        <translation>Delete</translation>
    </message>
    <message>
        <location line="+5"/>
        <source>切换文字过滤策略（当前：{}）</source>
        <translation>Toggle text filter policy (current: {})</translation>
    </message>
    <message>
        <location line="+3"/>
        <source>自动过滤</source>
        <translation>Auto filter</translation>
    </message>
    <message>
        <location line="+2"/>
        <source>全部保留</source>
        <translation>Keep all</translation>
    </message>
    <message>
        <location line="+10"/>
        <source>粘贴到末尾</source>
        <translation>Paste at end</translation>
    </message>
    <message>
        <location line="+12"/>
        <source>确认删除</source>
        <translation>Confirm deletion</translation>
    </message>
    <message>
        <location line="+1"/>
        <source>确定要删除 ROI {} 吗？</source>
        <translation>Delete ROI {}?</translation>
    </message>
</context>
<context>
    <name>ScanReviewDialog</name>
    <message>
        <location filename="../components/scan_review_dialog.py" line="+60"/>
        <source>深度扫描复核</source>
        <translation>Deep scan review</translation>
    </message>
    <message>
        <location line="+23"/>
        <source>字幕带 ROI（自动检测的主字幕区）</source>
        <translation>Subtitle band ROIs (auto-detected main subtitle regions)</translation>
    </message>
    <message>
        <location line="+6"/>
        <source>底部</source>
        <translation>Bottom</translation>
    </message>
    <message>
        <location line="+1"/>
        <source>顶部</source>
        <translation>Top</translation>
    </message>
    <message>
        <location line="+0"/>
        <source>中部</source>
        <translation>Middle</translation>
    </message>
    <message>
        <location line="+3"/>
        <source>{0}  帧 [{1}-{2}]</source>
        <translation>{0}  frames [{1}-{2}]</translation>
    </message>
    <message>
        <location line="+7"/>
        <source>用所选字幕带替换现有 ROI 列表（否则追加）</source>
        <translation>Replace the existing ROI list with the selected bands (otherwise append)</translation>
    </message>
    <message>
        <location line="+4"/>
        <source>未检测到字幕带。</source>
        <translation>No subtitle bands detected.</translation>
    </message>
    <message>
        <location line="+4"/>
        <source>疑似水印（恒定文本 + 恒定位置，勾选=识别时剔除）</source>
        <translation>Suspected watermarks (constant text + position; checked = removed during recognition)</translation>
    </message>
    <message>
        <location line="+6"/>
        <source>「{0}」 出现率 {1:.0%}</source>
        <translation>&quot;{0}&quot; presence {1:.0%}</translation>
    </message>
    <message>
        <location line="+10"/>
        <source>未检测到疑似水印。</source>
        <translation>No suspected watermarks detected.</translation>
    </message>
    <message>
        <location line="+4"/>
        <source>画面中部文字（场景字候选，勾选=导入为 ROI，全部保留）</source>
        <translation>Mid-frame text (in-scene candidates; checked = import as ROI, keep all)</translation>
    </message>
    <message>
        <location line="+5"/>
        <source>「{0}」 出现 {1} 次（帧 {2}-{3}）</source>
        <translation>&quot;{0}&quot; seen {1} time(s) (frames {2}-{3})</translation>
    </message>
    <message>
        <location line="+14"/>
        <source>（其余 {} 处低频文字未列出）</source>
        <translation>(remaining {} low-frequency text(s) not listed)</translation>
    </message>
    <message>
        <location line="+5"/>
        <source>未检测到画面中部文字。</source>
        <translation>No mid-frame text detected.</translation>
    </message>
    <message>
        <location line="+12"/>
        <source>应用</source>
        <translation>Apply</translation>
    </message>
    <message>
        <location line="+1"/>
        <source>取消</source>
        <translation>Cancel</translation>
    </message>
</context>
<context>
    <name>SubtitleOCRGUI</name>
    <message>
        <location filename="../main_window/auto_detection.py" line="+43"/>
        <source>已恢复上次保存的文字来源过滤设置。</source>
        <translation>Restored the previously saved text source filter settings.</translation>
    </message>
    <message>
        <location line="+35"/>
        <source>视频类型自动检测完成：{} (置信度 {:.0%})</source>
        <translation>Video type auto-detection finished: {} (confidence {:.0%})</translation>
    </message>
    <message>
        <location line="+9"/>
        <source>视频类型自动检测失败：{}</source>
        <translation>Video type auto-detection failed: {}</translation>
    </message>
    <message>
        <location line="+7"/>
        <source>自动分析失败，请手动选择场景类型</source>
        <translation>Auto analysis failed; choose the scene type manually</translation>
    </message>
    <message>
        <location filename="../main_window/pipeline_control.py" line="+38"/>
        <source>已启用 AI 翻译，但翻译模块不可用（{0}）。请先安装 font_intel 依赖（requirements-fontintel.txt），或取消勾选「AI 翻译」后重试。</source>
        <translation>AI translation is enabled, but the translation module is unavailable ({0}). Install the font_intel dependencies (requirements-fontintel.txt) first, or untick &quot;AI translation&quot; and try again.</translation>
    </message>
    <message>
        <source>已启用 AI 翻译（云端 API），但未填写 API Key。请在大模型润色区填写 API Key，或设置环境变量 DEEPSEEK_API_KEY。</source>
        <translation type="vanished">AI translation (cloud API) is enabled, but no API key has been entered. Enter an API key in the LLM polish section, or set the DEEPSEEK_API_KEY environment variable.</translation>
    </message>
    <message>
        <source>已启用 AI 翻译（本地 Sakura），但未填写 Base URL。请填写本地 Sakura 服务器的 OpenAI 兼容端点地址（如 http://127.0.0.1:8080/v1）。</source>
        <translation type="vanished">AI translation (local Sakura) is enabled, but no base URL has been entered. Enter the OpenAI-compatible endpoint address of the local Sakura server (e.g. http://127.0.0.1:8080/v1).</translation>
    </message>
    <message>
        <location line="+10"/>
        <source>已启用 AI 翻译，但未填写 API Key。请在大模型润色区填写 API Key，或设置环境变量 DEEPSEEK_API_KEY。</source>
        <translation>AI translation is enabled, but no API key has been entered. Enter an API key in the LLM polish section, or set the DEEPSEEK_API_KEY environment variable.</translation>
    </message>
    <message>
        <location line="+48"/>
        <source>已启用字体识别，但字体识别模块不可用（{0}）。请先安装 font_intel 依赖（requirements-fontintel.txt），或取消勾选「字体识别」后重试。</source>
        <translation>Font recognition is enabled, but the font recognition module is unavailable ({0}). Install the font_intel dependencies (requirements-fontintel.txt) first, or uncheck &quot;Font recognition&quot; and retry.</translation>
    </message>
    <message>
        <location line="+21"/>
        <source>已启用字体识别，但字体库目录不存在：{0}。请检查路径，或清空后仅使用系统字体目录。</source>
        <translation>Font recognition is enabled, but the font directory does not exist: {0}. Check the path, or clear it to use system font directories only.</translation>
    </message>
    <message>
        <location line="+40"/>
        <location line="+6"/>
        <location line="+10"/>
        <location line="+19"/>
        <location line="+24"/>
        <location line="+11"/>
        <location line="+215"/>
        <location line="+35"/>
        <location filename="../main_window/roi_config_io.py" line="+84"/>
        <location filename="../main_window/roi_editing.py" line="+325"/>
        <location filename="../main_window/source_config.py" line="+17"/>
        <location line="+12"/>
        <source>警告</source>
        <translation>Warning</translation>
    </message>
    <message>
        <location line="-319"/>
        <location filename="../main_window/source_config.py" line="-11"/>
        <source>请先加载视频并至少定义一个 ROI。</source>
        <translation>Load a video and define at least one ROI first.</translation>
    </message>
    <message>
        <location line="+6"/>
        <source>已有 OCR 任务在运行中，请等待其完成或先取消。</source>
        <translation>An OCR task is already running. Wait for it to finish or cancel it first.</translation>
    </message>
    <message>
        <location line="+10"/>
        <source>已勾选「按字幕颜色跳过疑似无字帧」，但尚未通过预览确认。请点击「预览检测效果」并在满意时选择「采用」，或取消勾选以使用默认流程。</source>
        <translation>&quot;Skip likely textless frames by subtitle color&quot; is checked but not yet confirmed via preview. Click &quot;Preview detection&quot; and choose &quot;Apply&quot; if satisfied, or uncheck it to use the default flow.</translation>
    </message>
    <message>
        <location line="+19"/>
        <source>已启用 DeepSeek 功能（润色/碎片合并/策略复核），但未填写 API Key。请填写 API Key，或设置环境变量 DEEPSEEK_API_KEY。</source>
        <translation>DeepSeek features (polish/fragment merge/strategy review) are enabled but no API Key is set. Enter an API Key or set the DEEPSEEK_API_KEY environment variable.</translation>
    </message>
    <message>
        <location line="+50"/>
        <source>保存字幕文件</source>
        <translation>Save subtitle file</translation>
    </message>
    <message>
        <location line="+2"/>
        <source>ASS 字幕 (*.ass)</source>
        <translation>ASS subtitles (*.ass)</translation>
    </message>
    <message>
        <location line="+3"/>
        <source>用户取消保存，OCR 任务已中止。</source>
        <translation>Save cancelled by user; OCR task aborted.</translation>
    </message>
    <message>
        <location line="+16"/>
        <source>[LLM] 已启用 DeepSeek —— 第 4 步的大模型进度会显示在下方。</source>
        <translation>[LLM] DeepSeek enabled — step 4 LLM progress is shown below.</translation>
    </message>
    <message>
        <location line="+4"/>
        <source>[LLM] 已启用 AI 翻译 —— 翻译进度会显示在下方。</source>
        <translation>[LLM] AI translation enabled — translation progress appears below.</translation>
    </message>
    <message>
        <location line="+6"/>
        <source>字幕 OCR + DeepSeek</source>
        <translation>Subtitle OCR + DeepSeek</translation>
    </message>
    <message>
        <location line="+2"/>
        <source>字幕 OCR</source>
        <translation>Subtitle OCR</translation>
    </message>
    <message>
        <location line="+2"/>
        <source>正在处理视频...</source>
        <translation>Processing video...</translation>
    </message>
    <message>
        <location line="+1"/>
        <source>取消</source>
        <translation>Cancel</translation>
    </message>
    <message>
        <location line="+52"/>
        <source>正在识别…</source>
        <translation>Recognizing…</translation>
    </message>
    <message>
        <location line="+17"/>
        <source>[LLM] 已取消 —— 未生成字幕文件。</source>
        <translation>[LLM] Cancelled - no subtitle file was generated.</translation>
    </message>
    <message>
        <location line="+50"/>
        <source>[LLM] 完成 —— 已写入 ASS 文件。</source>
        <translation>[LLM] Done — ASS file written.</translation>
    </message>
    <message>
        <location line="+5"/>
        <location line="+83"/>
        <source>完成</source>
        <translation>Done</translation>
    </message>
    <message>
        <location line="-82"/>
        <source>字幕文件已生成：{}</source>
        <translation>Subtitle file created: {}</translation>
    </message>
    <message>
        <location line="+6"/>
        <source>打开目录</source>
        <translation>Open folder</translation>
    </message>
    <message>
        <location line="+1"/>
        <source>是否打开包含该文件的文件夹？</source>
        <translation>Open the folder containing this file?</translation>
    </message>
    <message>
        <location line="+32"/>
        <source>字体更新建议读取失败，本轮复核已跳过：
{0}</source>
        <translation>Failed to read font update suggestions; review skipped for this run:
{0}</translation>
    </message>
    <message>
        <location line="+35"/>
        <source>字体更新建议写入失败：
{0}</source>
        <translation>Failed to write font update suggestions:
{0}</translation>
    </message>
    <message>
        <location line="+9"/>
        <source>已把 {0} 条字体更新建议写入用户覆盖层。</source>
        <translation>Wrote {0} font update suggestion(s) to the user overlay.</translation>
    </message>
    <message>
        <location line="+12"/>
        <source>[LLM] 已中止或出错 —— 请查看弹窗提示。</source>
        <translation>[LLM] Aborted or failed — see the dialog for details.</translation>
    </message>
    <message>
        <location line="+4"/>
        <location filename="../main_window/roi_config_io.py" line="-52"/>
        <location line="+88"/>
        <location filename="../main_window/roi_editing.py" line="+66"/>
        <location filename="../main_window/scan_control.py" line="+338"/>
        <location filename="../main_window/video_playback.py" line="+28"/>
        <source>错误</source>
        <translation>Error</translation>
    </message>
    <message>
        <location line="+1"/>
        <source>处理过程中发生错误：
{}</source>
        <translation>An error occurred during processing:
{}</translation>
    </message>
    <message>
        <location filename="../main_window/roi_config_io.py" line="-102"/>
        <source>保存 ROI 配置</source>
        <translation>Save ROI config</translation>
    </message>
    <message>
        <location line="+2"/>
        <location line="+72"/>
        <source>JSON 文件 (*.json)</source>
        <translation>JSON files (*.json)</translation>
    </message>
    <message>
        <location line="-63"/>
        <source>ROI 配置已保存到：{}</source>
        <translation>ROI config saved to: {}</translation>
    </message>
    <message>
        <location line="+4"/>
        <location line="+1"/>
        <source>保存 ROI 配置失败：{}</source>
        <translation>Failed to save ROI config: {}</translation>
    </message>
    <message>
        <location line="+33"/>
        <source>自动备份 ROI 配置失败（识别仍会继续）：{}</source>
        <translation>Failed to back up ROI config (recognition continues): {}</translation>
    </message>
    <message>
        <location line="+7"/>
        <source>为防意外中断，已自动备份 ROI 配置到：{}</source>
        <translation>ROI config auto-backed up in case of interruption: {}</translation>
    </message>
    <message>
        <location line="+11"/>
        <location filename="../main_window/scan_control.py" line="-259"/>
        <location line="+36"/>
        <location line="+46"/>
        <source>请先加载视频文件。</source>
        <translation>Load a video file first.</translation>
    </message>
    <message>
        <location line="+5"/>
        <source>加载 ROI 配置</source>
        <translation>Load ROI config</translation>
    </message>
    <message>
        <location line="+27"/>
        <source>ROI 配置已从 {} 加载</source>
        <translation>ROI config loaded from {}</translation>
    </message>
    <message>
        <location line="+4"/>
        <location line="+1"/>
        <source>加载 ROI 配置失败：{}</source>
        <translation>Failed to load ROI config: {}</translation>
    </message>
    <message>
        <location line="+4"/>
        <source>选择 ASS 模板文件</source>
        <translation>Select ASS template file</translation>
    </message>
    <message>
        <location line="+2"/>
        <source>ASS 字幕文件 (*.ass)</source>
        <translation>ASS subtitle files (*.ass)</translation>
    </message>
    <message>
        <location filename="../main_window/roi_editing.py" line="-351"/>
        <source>已添加新 ROI，帧范围：{}-{}</source>
        <translation>New ROI added, frame range: {}-{}</translation>
    </message>
    <message>
        <location line="+15"/>
        <source>已更新 ROI {}，新帧范围：{}-{}</source>
        <translation>ROI {} updated, new frame range: {}-{}</translation>
    </message>
    <message>
        <location line="+20"/>
        <source>已删除 ROI {}</source>
        <translation>ROI {} deleted</translation>
    </message>
    <message>
        <location line="+13"/>
        <source>ROI {} 文字过滤策略已切换为：{}</source>
        <translation>ROI {} text filter policy switched to: {}</translation>
    </message>
    <message>
        <location line="+4"/>
        <source>按场景预设过滤</source>
        <translation>Filter by scene preset</translation>
    </message>
    <message>
        <location line="+2"/>
        <source>全部保留（不过滤）</source>
        <translation>Keep all (no filtering)</translation>
    </message>
    <message>
        <location line="+40"/>
        <source>当前未选中任何 ROI：该开关只会写入之后「添加新 ROI」的条目；如需应用到已有 ROI，请先在列表中选中它再勾选。</source>
        <translation>No ROI is currently selected: this switch will only apply to entries added later via &quot;Add ROI&quot;. To apply it to an existing ROI, select that ROI in the list first, then toggle.</translation>
    </message>
    <message>
        <location line="+29"/>
        <source>ROI {} 画面位置标签已{}</source>
        <translation>ROI {} picture position tags {}</translation>
    </message>
    <message>
        <location line="+2"/>
        <location line="+20"/>
        <location line="+15"/>
        <source>开启</source>
        <translation>enabled</translation>
    </message>
    <message>
        <location line="-34"/>
        <location line="+20"/>
        <location line="+15"/>
        <source>关闭</source>
        <translation>disabled</translation>
    </message>
    <message>
        <location line="-18"/>
        <source>ROI {} 亮度自适应已{}</source>
        <translation>ROI {}: brightness adaptation {}</translation>
    </message>
    <message>
        <location line="+15"/>
        <source>ROI {} 遮挡蒙版已{}</source>
        <translation>ROI {}: occlusion mask {}</translation>
    </message>
    <message>
        <location line="+15"/>
        <source>ROI {} 场景文字显示已设为 {}</source>
        <translation>ROI {}: scene-text display set to {}</translation>
    </message>
    <message>
        <location line="+22"/>
        <source>ROI {} 识别语言已设为 {}</source>
        <translation>ROI {}: recognition language set to {}</translation>
    </message>
    <message>
        <location line="+2"/>
        <source>自动（跟随全局）</source>
        <translation>Auto (follow global)</translation>
    </message>
    <message>
        <location line="+89"/>
        <source>开始时间不能晚于结束时间。</source>
        <translation>Start time cannot be after end time.</translation>
    </message>
    <message>
        <location line="+63"/>
        <source>创建 ROI 条目时出错：{}</source>
        <translation>Error creating ROI entry: {}</translation>
    </message>
    <message>
        <location line="+3"/>
        <source>创建 ROI 时发生错误：{}</source>
        <translation>Error creating ROI: {}</translation>
    </message>
    <message>
        <location line="+96"/>
        <source>已在画面上调整 ROI {} 的区域</source>
        <translation>ROI {} region adjusted on the frame</translation>
    </message>
    <message>
        <location line="+8"/>
        <source>已将 ROI {} 复制到剪贴板。</source>
        <translation>ROI {} copied to clipboard.</translation>
    </message>
    <message>
        <location line="+10"/>
        <source>已粘贴到 ROI {} 之后。</source>
        <translation>Pasted after ROI {}.</translation>
    </message>
    <message>
        <location line="+10"/>
        <source>已将 ROI 粘贴到列表末尾。</source>
        <translation>ROI pasted at the end of the list.</translation>
    </message>
    <message>
        <location filename="../main_window/scan_control.py" line="-83"/>
        <location line="+36"/>
        <location line="+46"/>
        <source>未加载视频</source>
        <translation>No video loaded</translation>
    </message>
    <message>
        <location line="-55"/>
        <source>正在后台扫描字幕分布（采样 {} 帧）…</source>
        <translation>Scanning subtitle distribution in the background ({} sampled frames)…</translation>
    </message>
    <message>
        <location line="+20"/>
        <location line="+146"/>
        <location line="+52"/>
        <source>自动检测字幕ROI</source>
        <translation>Auto-detect subtitle ROIs</translation>
    </message>
    <message>
        <location line="-197"/>
        <source>请选择自动检测到的字幕区域的处理方式：

「是」：追加到现有 ROI 列表末尾（保留现有 ROI）；
「否」：替换全部现有 ROI（现有 ROI 将被清除，不可恢复）；
「取消」：中止本次检测。</source>
        <translation>How should the auto-detected subtitle regions be applied?

Yes: append to the end of the existing ROI list (keeps existing ROIs);
No: replace all existing ROIs (they will be cleared and cannot be recovered);
Cancel: abort this detection.</translation>
    </message>
    <message>
        <location line="+16"/>
        <source>替换现有 ROI？</source>
        <translation>Replace existing ROIs?</translation>
    </message>
    <message>
        <location line="+1"/>
        <source>即将清除现有 {} 个 ROI 并用检测结果替换，此操作不可恢复。是否继续？</source>
        <translation>This will clear {} existing ROIs and replace them with the detection result. This cannot be undone. Continue?</translation>
    </message>
    <message>
        <location line="+47"/>
        <source>正在深度扫描（采样 {} 帧，含水印/场景字统计）…</source>
        <translation>Deep scan in progress ({} sampled frames, with watermark/in-scene text stats)…</translation>
    </message>
    <message>
        <location line="+12"/>
        <source>深度扫描</source>
        <translation>Deep scan</translation>
    </message>
    <message>
        <location line="+1"/>
        <source>扫描完成：未检测到字幕带、水印或场景文字。</source>
        <translation>Scan finished: no subtitle bands, watermarks, or in-scene text detected.</translation>
    </message>
    <message>
        <location line="+23"/>
        <source>将在识别时剔除 {} 处水印文本。</source>
        <translation>{} watermark text(s) will be removed during recognition.</translation>
    </message>
    <message>
        <location line="+6"/>
        <source>未启用水印剔除。</source>
        <translation>Watermark removal not enabled.</translation>
    </message>
    <message>
        <location line="+3"/>
        <source>已导入 {} 个字幕带 ROI、{} 个场景字 ROI。</source>
        <translation>Imported {} subtitle band ROI(s) and {} in-scene text ROI(s).</translation>
    </message>
    <message>
        <location line="+13"/>
        <source>字幕扫描进度：{}/{}（采样帧）</source>
        <translation>Subtitle scan progress: {}/{} (sampled frames)</translation>
    </message>
    <message>
        <location line="+24"/>
        <source>未检测到字幕区域。请确认视频中确实存在字幕，或调整识别语言后重试。</source>
        <translation>No subtitle regions detected. Make sure the video actually contains subtitles, or adjust the recognition language and retry.</translation>
    </message>
    <message>
        <location line="+7"/>
        <source>后台扫描未检测到字幕带，可手动绘制 ROI。</source>
        <translation>The background scan found no subtitle bands; you can draw ROIs manually.</translation>
    </message>
    <message>
        <location line="+7"/>
        <source>已用 {} 个自动检测的 ROI 替换全部现有 ROI。</source>
        <translation>Replaced all existing ROIs with {} auto-detected ROIs.</translation>
    </message>
    <message>
        <location line="+7"/>
        <source>已追加 {} 个自动检测的 ROI 到列表末尾。</source>
        <translation>Appended {} auto-detected ROI(s) to the end of the list.</translation>
    </message>
    <message>
        <location line="+11"/>
        <source>检测到 {} 处疑似水印（恒定文本/位置）。可运行深度扫描复核后自动剔除。</source>
        <translation>{} suspected watermark(s) detected (constant text/position). Run a deep scan review to remove them automatically.</translation>
    </message>
    <message>
        <location line="+7"/>
        <source>检测到 {} 处画面中部文字（场景字候选）。可在深度扫描复核中导入为 ROI。</source>
        <translation>{} mid-frame text(s) detected (in-scene text candidates). Import them as ROIs in the deep scan review.</translation>
    </message>
    <message>
        <location line="+13"/>
        <source>自动检测完成：检测到 {} 个字幕区域，时间范围 {} ～ {}</source>
        <translation>Auto-detection finished: {} subtitle regions found, time span {} – {}</translation>
    </message>
    <message>
        <location line="+9"/>
        <location line="+6"/>
        <source>自动检测失败：{}</source>
        <translation>Auto-detection failed: {}</translation>
    </message>
    <message>
        <location filename="../main_window/source_config.py" line="+12"/>
        <source>当前帧不在任一 ROI 的时间范围内。请将时间轴移到含字幕的典型帧上，用于自动标定颜色。</source>
        <translation>The current frame is outside every ROI&apos;s time span. Move the timeline to a typical frame with subtitles for automatic color calibration.</translation>
    </message>
    <message>
        <location line="+21"/>
        <source>颜色门控预览失败：{}</source>
        <translation>Color gate preview failed: {}</translation>
    </message>
    <message>
        <location line="+5"/>
        <source>预览失败</source>
        <translation>Preview failed</translation>
    </message>
    <message>
        <location line="+9"/>
        <source>颜色门控已确认；下次运行「字幕 OCR 识别」时将在阶段一启用。</source>
        <translation>Color gate confirmed; it will be enabled in stage one of the next subtitle OCR run.</translation>
    </message>
    <message>
        <location filename="../main_window/video_playback.py" line="-9"/>
        <source>选择视频文件</source>
        <translation>Select video file</translation>
    </message>
    <message>
        <location line="+2"/>
        <source>视频文件 (*.mp4 *.avi *.mov *.mkv)</source>
        <translation>Video files (*.mp4 *.avi *.mov *.mkv)</translation>
    </message>
    <message>
        <location line="+8"/>
        <source>无法打开视频文件</source>
        <translation>Could not open the video file</translation>
    </message>
    <message>
        <location line="+27"/>
        <source>视频字幕 OCR 工具 - {}</source>
        <translation>Video Subtitle OCR Tool - {}</translation>
    </message>
    <message>
        <location line="+1"/>
        <source>视频已加载：{}</source>
        <translation>Video loaded: {}</translation>
    </message>
    <message>
        <location line="+1"/>
        <source>分辨率：{}x{}，帧率：{:.2f}，总帧数：{}</source>
        <translation>Resolution: {}x{}, FPS: {:.2f}, total frames: {}</translation>
    </message>
    <message>
        <location line="+25"/>
        <source>已自动加载 ROI 配置：{}</source>
        <translation>ROI config auto-loaded: {}</translation>
    </message>
    <message>
        <location line="+4"/>
        <source>自动加载 ROI 配置失败：{}</source>
        <translation>Failed to auto-load ROI config: {}</translation>
    </message>
    <message>
        <location line="+92"/>
        <source>无效的时间/帧号输入：&apos;{}&apos;</source>
        <translation>Invalid time/frame input: &apos;{}&apos;</translation>
    </message>
    <message>
        <location filename="../main_window/window.py" line="+95"/>
        <source>视频字幕 OCR 工具</source>
        <translation>Video Subtitle OCR Tool</translation>
    </message>
    <message>
        <location line="+118"/>
        <source>正在深度扫描…</source>
        <translation>Deep scan in progress…</translation>
    </message>
    <message>
        <location line="+2"/>
        <source>正在检测字幕区域…</source>
        <translation>Detecting subtitle regions…</translation>
    </message>
    <message>
        <location line="+57"/>
        <source>已通过拖放加载 ASS 模板：{}</source>
        <translation>ASS template loaded via drag &amp; drop: {}</translation>
    </message>
    <message>
        <location line="+26"/>
        <source>确认退出</source>
        <translation>Confirm exit</translation>
    </message>
    <message>
        <location line="+1"/>
        <source>后台任务仍在运行，确定要退出吗？</source>
        <translation>Background tasks are still running. Quit anyway?</translation>
    </message>
</context>
<context>
    <name>VideoFrameLabel</name>
    <message>
        <location filename="../components/video_display.py" line="+430"/>
        <source>绘制错误：{}</source>
        <translation>Drawing error: {}</translation>
    </message>
</context>
<context>
    <name>coordinate_restorer</name>
    <message>
        <location filename="../core/coordinate_restorer.py" line="+18"/>
        <source>A valid working directory `work_dir` must be provided.</source>
        <translation type="unfinished"></translation>
    </message>
    <message>
        <location line="+9"/>
        <source>Frame {} (ROI: {}) has no OCR results, coordinate restoration skipped.</source>
        <translation type="unfinished"></translation>
    </message>
    <message>
        <location line="+10"/>
        <source>Could not get offset for frame {} (ROI: {}), skipped.</source>
        <translation type="unfinished"></translation>
    </message>
    <message>
        <location line="+14"/>
        <source>OCR result file does not exist: {}, skipped.</source>
        <translation type="unfinished"></translation>
    </message>
    <message>
        <location line="+12"/>
        <source>Unknown OCR result data type (Frame {}, ROI {}). Type: {}, skipped.</source>
        <translation type="unfinished"></translation>
    </message>
    <message>
        <location line="+15"/>
        <source>Frame {} (ROI: {}) OCR result is empty, skipping coordinate restoration.</source>
        <translation type="unfinished"></translation>
    </message>
    <message>
        <location line="+19"/>
        <source>Error restoring coordinates for frame {} (ROI: {}): {}</source>
        <translation type="unfinished"></translation>
    </message>
    <message>
        <location line="+23"/>
        <source>Incorrect points format for rectangular ROI: {}</source>
        <translation type="unfinished"></translation>
    </message>
    <message>
        <location line="+11"/>
        <source>Incorrect points format for polygonal ROI: {}</source>
        <translation type="unfinished"></translation>
    </message>
    <message>
        <location line="+14"/>
        <source>Error parsing polygonal ROI offset: {}</source>
        <translation type="unfinished"></translation>
    </message>
    <message>
        <location line="+7"/>
        <source>Unknown ROI type: {}</source>
        <translation type="unfinished"></translation>
    </message>
</context>
<context>
    <name>ffmpeg_roi_segmenter</name>
    <message>
        <location filename="../core/ffmpeg_roi_segmenter.py" line="+114"/>
        <source>freezedetect found {} static segments in ROI crop.</source>
        <translation type="unfinished"></translation>
    </message>
    <message>
        <location line="+6"/>
        <source>freezedetect found no static segments in ROI crop.</source>
        <translation type="unfinished"></translation>
    </message>
</context>
<context>
    <name>ocr_optimizer</name>
    <message>
        <location filename="../core/ocr_optimizer.py" line="+354"/>
        <source>Could not get image data for frame {}. Input type: {}</source>
        <translation type="unfinished"></translation>
    </message>
    <message>
        <location line="+296"/>
        <source>Batch OCR prediction failed; falling back to per-frame OCR.</source>
        <translation type="unfinished"></translation>
    </message>
    <message>
        <location line="+67"/>
        <source>Sampled frames disagree on line count; keeping base frame OCR result.</source>
        <translation type="unfinished"></translation>
    </message>
    <message>
        <location line="+134"/>
        <source>VLM refine returned {0} lines for {1} expected; keeping original result.</source>
        <translation type="unfinished"></translation>
    </message>
    <message>
        <location line="+9"/>
        <source>VLM refine failed; keeping original voting result.</source>
        <translation type="unfinished"></translation>
    </message>
    <message>
        <location line="+21"/>
        <source>OCR optimizer detected cancellation signal, terminating early.</source>
        <translation type="unfinished"></translation>
    </message>
    <message>
        <location line="+111"/>
        <source>Smart frame skipping: ROI &apos;{}&apos; from frame {} to {} has similar content, skipping {} OCR operations.</source>
        <translation type="unfinished"></translation>
    </message>
    <message>
        <location line="+20"/>
        <source>Cleaning up cache.</source>
        <translation type="unfinished"></translation>
    </message>
</context>
<context>
    <name>ocr_processor</name>
    <message>
        <location filename="../core/ocr_processor.py" line="+111"/>
        <source>Batch OCR Image Processing</source>
        <translation type="unfinished"></translation>
    </message>
    <message>
        <location line="+2"/>
        <source>Input image directory path</source>
        <translation type="unfinished"></translation>
    </message>
    <message>
        <location line="+2"/>
        <source>Output results directory path</source>
        <translation type="unfinished"></translation>
    </message>
    <message>
        <location line="+2"/>
        <source>Enable visualization output</source>
        <translation type="unfinished"></translation>
    </message>
</context>
<context>
    <name>pipeline_worker</name>
    <message>
        <location filename="../core/pipeline_stages.py" line="+99"/>
        <source>Step 1/4: Calculating number of ROI frames to process...</source>
        <translation type="unfinished"></translation>
    </message>
    <message>
        <location line="+19"/>
        <source>ROI extraction step did not produce any data. Please check ROI time and region settings.</source>
        <translation type="unfinished"></translation>
    </message>
    <message>
        <location line="+4"/>
        <source>Step 1/4: Calculation complete, total {} frames. Starting extraction...</source>
        <translation type="unfinished"></translation>
    </message>
    <message>
        <location line="+37"/>
        <source>Step 2/4: Starting intelligent OCR recognition... (0/{})</source>
        <translation type="unfinished"></translation>
    </message>
    <message>
        <location line="+9"/>
        <source>Streaming mode enabled (time_slice={}s). OCR will run during extraction to reduce peak memory.</source>
        <translation type="unfinished"></translation>
    </message>
    <message>
        <location line="+68"/>
        <location line="+58"/>
        <location line="+113"/>
        <location line="+95"/>
        <source>Step 2/4: OCR recognition in progress... ({}/{})</source>
        <translation type="unfinished"></translation>
    </message>
    <message>
        <location line="-257"/>
        <source>Streaming OCR will flush in parallel (cpu_workers={}, max_stream_workers={}).</source>
        <translation type="unfinished"></translation>
    </message>
    <message>
        <location line="+68"/>
        <source>Step 1/4: Extracting ROI frames... ({}/{})</source>
        <translation type="unfinished"></translation>
    </message>
    <message>
        <location line="+32"/>
        <source>Step 1/4: ROI frame extraction complete. Total {} ROI frames.</source>
        <translation type="unfinished"></translation>
    </message>
    <message>
        <location line="+8"/>
        <source>ROI extraction done: {} ROI-frames in {:.2f}s ({:.1f} roi-frames/s).</source>
        <translation type="unfinished"></translation>
    </message>
    <message>
        <location line="+8"/>
        <source>ROI extraction yielded no frames. If color presence filtering is enabled, try preview again with a higher ratio threshold or disable it.</source>
        <translation type="unfinished"></translation>
    </message>
    <message>
        <location line="+95"/>
        <source>Running OCR in parallel (device={}, groups={}, max_workers={}, save_json={}).</source>
        <translation type="unfinished"></translation>
    </message>
    <message>
        <location line="+31"/>
        <source>Running OCR sequentially (device={}, groups={}, save_json={}).</source>
        <translation type="unfinished"></translation>
    </message>
    <message>
        <location line="+53"/>
        <source>OCR recognition step did not produce any results.</source>
        <translation type="unfinished"></translation>
    </message>
    <message>
        <location line="+48"/>
        <source>Step 2/4: OCR recognition complete.</source>
        <translation type="unfinished"></translation>
    </message>
    <message>
        <location line="+3"/>
        <source>OCR done: {} roi-frames filled, {} OCR calls (est. skipped {}), {:.2f}s.</source>
        <translation type="unfinished"></translation>
    </message>
    <message>
        <location line="+37"/>
        <source>Step 2/4: Refining subtitle boundaries frame-by-frame... ({}/{})</source>
        <translation type="unfinished"></translation>
    </message>
    <message>
        <location line="+8"/>
        <source>Step 2/4: Refining subtitle boundaries frame-by-frame...</source>
        <translation type="unfinished"></translation>
    </message>
    <message>
        <location line="+21"/>
        <source>Boundary refinement running on {} threads...</source>
        <translation type="unfinished"></translation>
    </message>
    <message>
        <location line="+44"/>
        <source>Step 3/4: Starting coordinate restoration... (0/{})</source>
        <translation type="unfinished"></translation>
    </message>
    <message>
        <location line="+25"/>
        <source>Step 3/4: Restoring coordinates... ({}/{})</source>
        <translation type="unfinished"></translation>
    </message>
    <message>
        <location line="+7"/>
        <source>Coordinate restoration step did not produce any results.</source>
        <translation type="unfinished"></translation>
    </message>
    <message>
        <location line="+2"/>
        <source>Step 3/4: Coordinate restoration complete.</source>
        <translation type="unfinished"></translation>
    </message>
    <message>
        <location line="+3"/>
        <source>Coordinate restoration done: {} frames in {:.2f}s (save_json={}).</source>
        <translation type="unfinished"></translation>
    </message>
    <message>
        <location line="+110"/>
        <source>Boundary refinement used {} extra single-frame OCR calls.</source>
        <translation type="unfinished"></translation>
    </message>
    <message>
        <location filename="../core/pipeline_worker.py" line="+100"/>
        <source>ROIs merged with differing scene text policies ({}); using the first one ({}).</source>
        <translation type="unfinished"></translation>
    </message>
    <message>
        <location line="+321"/>
        <source>Auto motion detection on {0} failed ({1}); skipping it.</source>
        <translation type="unfinished"></translation>
    </message>
    <message>
        <location line="+58"/>
        <source>Step 4/4: Tracking moving-text plane(s) for trajectory subtitles...</source>
        <translation type="unfinished"></translation>
    </message>
    <message>
        <location line="+34"/>
        <source>Motion trajectory for {0} covers only {1:.0f}% of the ROI time range; falling back to static pose tags.</source>
        <translation>Motion trajectory for {0} covers only {1:.0f}% of the ROI time range; using static pose tags instead.</translation>
    </message>
    <message>
        <location line="+9"/>
        <source>Motion trajectory for {0}: {1} event(s) ({2}/{3} frames ok, keyframes {4}, policy {5}).</source>
        <translation type="unfinished"></translation>
    </message>
    <message>
        <location line="+14"/>
        <location line="+7"/>
        <source>Motion trajectory for {0} failed ({1}); falling back to static pose tags.</source>
        <translation type="unfinished"></translation>
    </message>
    <message>
        <location line="+67"/>
        <source>Intermediate files will be saved to: {}</source>
        <translation type="unfinished"></translation>
    </message>
    <message>
        <location line="+6"/>
        <source>in-memory data stream</source>
        <translation type="unfinished"></translation>
    </message>
    <message>
        <location line="+1"/>
        <source>disk file stream</source>
        <translation type="unfinished"></translation>
    </message>
    <message>
        <location line="+4"/>
        <source>OCR pipeline will run in {} mode.</source>
        <translation type="unfinished"></translation>
    </message>
    <message>
        <location line="+12"/>
        <source>Step 1-3/4: Processing chunks in parallel...</source>
        <translation type="unfinished"></translation>
    </message>
    <message>
        <location line="+29"/>
        <source>Chunk-parallel OCR: {} workers, {} windows.</source>
        <translation type="unfinished"></translation>
    </message>
    <message>
        <location line="+41"/>
        <source>Step 4/4: Starting ASS subtitle file generation...</source>
        <translation type="unfinished"></translation>
    </message>
    <message>
        <location line="+79"/>
        <source>Step 4/4: ASS subtitle generation complete.</source>
        <translation type="unfinished"></translation>
    </message>
    <message>
        <location line="+3"/>
        <source>ASS generation done in {:.2f}s.</source>
        <translation type="unfinished"></translation>
    </message>
    <message>
        <location line="+7"/>
        <source>Pipeline total time: {:.2f}s.</source>
        <translation type="unfinished"></translation>
    </message>
    <message>
        <location line="+12"/>
        <source>Pipeline processing failed: {}</source>
        <translation type="unfinished"></translation>
    </message>
    <message>
        <location line="+7"/>
        <source>An error occurred during processing: {}</source>
        <translation type="unfinished"></translation>
    </message>
    <message>
        <location line="+10"/>
        <source>Temporary working directory deleted: {}</source>
        <translation type="unfinished"></translation>
    </message>
    <message>
        <location line="+7"/>
        <source>Could not delete temporary working directory {}: {}</source>
        <translation type="unfinished"></translation>
    </message>
    <message>
        <location line="+8"/>
        <source>Task cancellation request sent.</source>
        <translation type="unfinished"></translation>
    </message>
    <message>
        <location line="+4"/>
        <source>Forcibly terminating thread...</source>
        <translation type="unfinished"></translation>
    </message>
</context>
<context>
    <name>roi_extractor</name>
    <message>
        <location filename="../core/roi_extractor.py" line="+18"/>
        <source>CUDA-enabled GPU detected and available for OpenCV.</source>
        <translation type="unfinished"></translation>
    </message>
    <message>
        <location line="+2"/>
        <source>No CUDA-enabled GPU detected or OpenCV not compiled with CUDA support.</source>
        <translation type="unfinished"></translation>
    </message>
    <message>
        <location line="+2"/>
        <source>OpenCV CUDA module not found. Likely OpenCV was not compiled with CUDA support.</source>
        <translation type="unfinished"></translation>
    </message>
    <message>
        <location line="+2"/>
        <source>Error checking for CUDA GPU: {e}</source>
        <translation type="unfinished"></translation>
    </message>
    <message>
        <location line="+53"/>
        <source>Color restriction produced an empty mask for ROI; skipping mask for this frame crop.</source>
        <translation type="unfinished"></translation>
    </message>
    <message>
        <location line="+132"/>
        <source>Unrecognized time string {!r} for key &apos;{}&apos;; falling back to numeric seconds.</source>
        <translation type="unfinished"></translation>
    </message>
    <message>
        <location line="+30"/>
        <source>Pre-calculated total of {} ROI frames to process.</source>
        <translation type="unfinished"></translation>
    </message>
    <message>
        <location line="+45"/>
        <source>Pre-calculated total of {} merged frames to process (union of ROI intervals).</source>
        <translation type="unfinished"></translation>
    </message>
    <message>
        <location line="+132"/>
        <location line="+121"/>
        <source>Extraction cannot start: video path or ROI data not provided.</source>
        <translation type="unfinished"></translation>
    </message>
    <message>
        <location line="-118"/>
        <location line="+122"/>
        <source>In save_to_disk mode, a valid working directory `work_dir` must be provided.</source>
        <translation type="unfinished"></translation>
    </message>
    <message>
        <location line="-119"/>
        <location line="+128"/>
        <source>Extraction aborted: invalid total_frames.</source>
        <translation type="unfinished"></translation>
    </message>
    <message>
        <location line="-100"/>
        <location line="+123"/>
        <source>Could not open video file for extraction: {}</source>
        <translation type="unfinished"></translation>
    </message>
    <message>
        <location line="+17"/>
        <source>Attempting to use GPU for ROI extraction.</source>
        <translation type="unfinished"></translation>
    </message>
    <message>
        <location line="+2"/>
        <source>Using CPU for ROI extraction (GPU not available or OpenCV not compiled with CUDA).</source>
        <translation type="unfinished"></translation>
    </message>
    <message>
        <location line="+47"/>
        <source>Could not retrieve video frame {}.</source>
        <translation type="unfinished"></translation>
    </message>
    <message>
        <location line="+20"/>
        <source>Failed to upload frame to GPU for frame {}. Falling back to CPU for this frame. Error: {}</source>
        <translation type="unfinished"></translation>
    </message>
    <message>
        <location line="+23"/>
        <source>ROI {} has no &apos;points&apos; field; skipped.</source>
        <translation type="unfinished"></translation>
    </message>
    <message>
        <location line="+24"/>
        <source>Incorrect points format for rectangular ROI: {}</source>
        <translation type="unfinished"></translation>
    </message>
    <message>
        <location line="+11"/>
        <source>Incorrect points format for polygonal ROI: {}</source>
        <translation type="unfinished"></translation>
    </message>
    <message>
        <location line="+29"/>
        <source>Error processing polygonal ROI: {}</source>
        <translation type="unfinished"></translation>
    </message>
    <message>
        <location line="+8"/>
        <source>Unknown ROI type: {}</source>
        <translation type="unfinished"></translation>
    </message>
    <message>
        <location line="+23"/>
        <source>ROI extraction resulted in empty image, frame {}, ROI {}</source>
        <translation type="unfinished"></translation>
    </message>
</context>
</TS>
