# i18n/translations_en.py
"""English translations for Chinese source strings (GUI contexts).

Keyed by exact source text (context-independent). Core modules log in
English already, so their English-source strings need no entry — Qt
falls back to the source text.

Apply with: python i18n/apply_translations.py i18n/app_en.ts
"""

DICT = {
    # ── ColorGatePreviewDialog ──
    "颜色门控：预览": "Color gate: preview",
    "以下缩略来自 ROI 区间内均匀采样。请确认「判定为保留」与您期望一致。若不满意，请选择「取消」或直接关闭对话框，并保持主界面选项关闭或未确认。":
        "The thumbnails below are sampled uniformly across the ROI span. Confirm that frames marked \"keep\" match your expectation. If not, choose \"Cancel\" or close this dialog, and leave the main-window option off or unconfirmed.",
    "阈值与预估": "Thresholds & estimates",
    "命中面积占比下限：": "Minimum hit-area ratio:",
    "采用（用于本次识别）": "Apply (for this recognition run)",
    "不采用": "Don't apply",
    "采样帧数：{}；在当前阈值下「保留」帧数：{}（约 {:.1f}%）；按此比例粗略估计全流程 ROI 图数量约：{} / {}（原计划）。":
        "Sampled frames: {}; frames kept at current threshold: {} (~{:.1f}%). Rough estimate of ROI images for the full run: {} / {} (original plan).",
    "保留": "Keep",
    "跳过": "Skip",
    "帧 {} · {}": "Frame {} · {}",
    "max 占比：{ratio:.4f}": "max ratio: {ratio:.4f}",

    # ── ControlPanelWidget ──
    "识别语言：": "Recognition language:",
    "模型档位：": "Model tier:",
    "选择字幕语言。中文简体/繁体/英语/日语及拉丁语系使用 PP-OCRv6 模型，韩/俄/阿拉伯语等自动回落 PP-OCRv5 多语言模型。":
        "Subtitle language. Simplified/Traditional Chinese, English, Japanese and Latin-script languages use PP-OCRv6 models; Korean/Russian/Arabic etc. automatically fall back to the multilingual PP-OCRv5 models.",
    "样式模板（可选）": "Style template (optional)",
    "点击“浏览”选择 .ass 模板文件": "Click \"Browse\" to pick an .ass template file",
    "浏览…": "Browse…",
    "引擎选择：": "Engine:",
    "检测可用引擎": "Detect available engines",
    "PaddleOCR 模型档位：Tiny 最快，Small 均衡，Medium 最准。“自动”使用 PP-OCRv6 默认模型。":
        "PaddleOCR model tier: Tiny is fastest, Small is balanced, Medium is most accurate. \"Auto\" uses the default PP-OCRv6 models.",
    "文字来源过滤（上下文语义分析）": "Text source filter (contextual semantics)",
    "启用文字来源过滤（实验性）": "Enable text source filter (experimental)",
    "勾选后，OCR 识别结果将经过上下文语义分析，自动区分后期叠加字幕与实拍场景文字。未勾选时保留所有识别到的文字。":
        "When checked, OCR results pass contextual semantic analysis that separates overlay subtitles from in-scene text. When unchecked, all recognized text is kept.",
    "🔄 加载视频后将自动分析...": "🔄 Auto analysis starts after a video is loaded...",
    "场景预设：": "Scene preset:",
    "选择适合当前视频的场景类型，自动配置文字来源过滤规则":
        "Pick the scene type matching this video to auto-configure text source filter rules",
    "保留后期叠加文字 (OVERLAY)\n    字幕、标题、水印、UI 按钮、弹幕、特效文字":
        "Keep overlaid text (OVERLAY)\n    Subtitles, titles, watermarks, UI buttons, danmaku, effects text",
    "保留实拍场景文字 (SCENE)\n    店铺招牌、路牌、宣传海报、书本、屏幕、标牌":
        "Keep in-scene text (SCENE)\n    Shop signs, street signs, posters, books, screens, plaques",
    "保留无法判定文字 (UNKNOWN)\n    分类器置信度不足的边界情况":
        "Keep undetermined text (UNKNOWN)\n    Borderline cases the classifier can't decide",
    "💡 备选方案:": "💡 Alternatives:",
    "🔄 重新自动检测": "🔄 Re-run auto detection",
    "重新采样分析当前视频并更新类型判定": "Re-sample and analyze this video to update the type verdict",
    "重置为预设默认值": "Reset to preset defaults",
    "绘制模式": "Drawing mode",
    "矩形（拖动）": "Rectangle (drag)",
    "多边形（点击）": "Polygon (click)",
    "编辑（拖动调整）": "Edit (drag to adjust)",
    "在画面上直接调整已有 ROI：拖动整体移动；矩形拖 8 个控制点缩放；多边形拖顶点改形。自动检测的 ROI 可用此模式微调。":
        "Adjust existing ROIs directly on the frame: drag to move, rectangles have 8 handles to resize, polygons reshape via vertices. Use this mode to fine-tune auto-detected ROIs.",
    "字幕生成": "Subtitle generation",
    "识别设置": "Recognition settings",
    "文字保留：": "Text to keep:",
    "控制识别结果保留哪些文字。「保留全部文字」不做任何过滤；更精细的规则可在完整设置中调整。":
        "Controls which recognized text is kept. \"Keep all text\" applies no filtering; finer rules are available in Full settings.",
    "保留全部文字": "Keep all text",
    "只保留字幕": "Subtitles only",
    "字幕和画面文字": "Subtitles and on-screen text",
    "自定义（在完整设置中调整）": "Custom (adjust in Full settings)",
    "OCR 引擎（自动选择）": "OCR engine (auto-select)",
    "开始识别并导出": "Start recognition & export",
    "调整字幕区域": "Adjust subtitle regions",
    "进入 ROI 编辑状态：在画面上拖动、缩放或微调已检测到的字幕区域。":
        "Enter ROI edit mode: drag, resize, or fine-tune the detected subtitle regions on the frame.",
    "重新检测": "Re-detect",
    "对当前视频重新执行自动分析（场景类型判定）。":
        "Re-run automatic analysis of this video (scene type verdict).",
    "调试模式": "Debug mode",
    "可视化输出": "Visualization output",
    "内存模式（实验性）": "In-memory mode (experimental)",
    "保存中间 JSON": "Save intermediate JSON",
    "按时间分片并行（可选）": "Parallel by time slices (optional)",
    "合并 ROI（每帧只 OCR 一次）": "Merge ROIs (one OCR pass per frame)",
    "秒": "s",
    "加载视频后自动检测字幕 ROI": "Auto-detect subtitle ROIs after loading a video",
    "加载视频后在后台采样扫描全片，自动生成顶部/底部字幕带 ROI（文字过滤策略为「自动过滤」，可随时在 ROI 列表右键切换）。":
        "After a video is loaded, a background sampling scan of the whole file auto-creates top/bottom subtitle band ROIs (text filter policy \"auto filter\"; right-click any ROI in the list to switch anytime).",
    "进程分片（长视频提速）": "Process sharding (speeds up long videos)",
    "自动": "Auto",
    "把长视频按时间切成多个窗口，用多个进程并行识别（与「按时间分片并行」的线程桶不同）。「自动」按 CPU 核数与可用内存决定；仅 CPU 模式生效，短视频自动走单进程。每个并行进程约占 600MB 内存。":
        "Splits long videos into time windows recognized by multiple worker processes (different from the thread pools of \"parallel by time slices\"). \"Auto\" decides by CPU cores and available memory; CPU mode only, short videos use a single process. Each worker uses about 600MB of memory.",
    "半自动：按字幕颜色跳过疑似无字帧（默认关，需预览并确认后才生效）":
        "Semi-auto: skip likely textless frames by subtitle color (off by default; takes effect only after preview & confirm)",
    "在阶段一抽样 ROI 区间内若干帧并在当前画面上自动标定 HSV。仅当预览结果满意并在对话框中点击「采用」后，才会在本轮 OCR 启用；可随时关闭恢复默认逻辑。若预览不满意或选择「不采用」，请保持勾选关闭或未确认——程序将按原版流程输出全部 ROI 帧。":
        "In stage one, samples frames within the ROI span and auto-calibrates HSV on the current frame. It only applies to this OCR run after you click \"Apply\" in the preview dialog; turn it off anytime to restore default behavior. If the preview looks wrong or you choose \"Don't apply\", keep it unchecked or unconfirmed — the program will process all ROI frames as usual.",
    "预览检测效果…": "Preview detection…",
    "DeepSeek 字幕润色": "DeepSeek subtitle polish",
    "兼容 OpenAI 的接口。默认提供方为 DeepSeek，会自动填充 Base URL；当你输入 API Key 后，应用会调用 /v1/models 拉取模型列表（也可手动编辑模型 ID）。":
        "OpenAI-compatible API. DeepSeek is the default provider and fills the Base URL automatically; once you enter an API Key, the app fetches /v1/models for the model list (model IDs remain editable).",
    "DeepSeek 合并碎片字幕（选择最完整文本并合并时间范围）":
        "DeepSeek fragment merge (pick the most complete text and merge time ranges)",
    "API Key 不会以明文写入配置文件：优先保存在系统钥匙串（需安装 keyring）；不可用时使用本地加密存储（密钥文件仅当前用户可读）。清除可点击右侧按钮。":
        "The API Key is never written to config files in plain text: it is stored in the system keychain when keyring is available, otherwise in a locally encrypted store readable only by the current user. Click the button on the right to clear it.",
    "大模型提供方": "LLM provider",
    "自定义（手动 Base URL）": "Custom (manual Base URL)",
    "模型 ID（可从 API 自动拉取）": "Model ID (can be fetched from the API)",
    "刷新模型列表": "Refresh model list",
    "使用 API Key 和 Base URL 拉取 /v1/models。": "Fetch /v1/models using the API Key and Base URL.",
    "清除已存密钥": "Clear saved key",
    "从本机配置中删除已保存的 API Key（输入框会清空）。Base URL 与模型仍会保留。":
        "Deletes the saved API Key from this machine's config (the field is cleared). Base URL and model are kept.",
    "DeepSeek 合并策略复核（按更合适的阈值重新合并）":
        "DeepSeek merge strategy review (re-merge with better-suited thresholds)",
    "完整设置": "Full settings",
    "显示全部设置（文字来源过滤、绘制模式、引擎详情）。已调整的选项保持不变。":
        "Show all settings (text source filter, drawing mode, engine details). Adjusted options are preserved.",
    "简洁界面": "Simple view",
    "返回一键简洁视图，隐藏高级设置。所有选项保持不变。":
        "Return to the one-click simple view and hide advanced settings. All options are preserved.",
    "🔒 文字来源过滤已关闭，将保留所有识别到的文字。":
        "🔒 Text source filter is off; all recognized text will be kept.",
    "🔄 文字来源过滤已启用，加载视频后将自动分析...":
        "🔄 Text source filter is on; auto analysis starts after a video is loaded...",
    "颜色门控：已关闭（默认）。": "Color gate: off (default).",
    "颜色门控：已勾选，尚未确认。请点击「预览检测效果」并在满意时选择「采用」。":
        "Color gate: checked but not confirmed. Click \"Preview detection\" and choose \"Apply\" if satisfied.",
    "颜色门控：已确认，将用于下一轮「字幕 OCR 识别」阶段一截取。":
        "Color gate: confirmed; it will apply in stage one of the next \"subtitle OCR\" run.",
    "（无可用引擎）": "(no available engine)",
    "⚠ 未检测到可用 OCR 引擎": "⚠ No usable OCR engine detected",
    "✅ 已就绪": "✅ Ready",
    "🔄 正在分析视频类型...": "🔄 Analyzing video type...",
    "手动模式（已自定义）": "Manual mode (customized)",
    "已恢复上次保存的手动设置": "Restored your previously saved manual settings",
    "中文简体": "中文简体",
    "中文繁體": "中文繁體",
    "日本語": "日本語",
    "自动(Auto)": "Auto",
    "Tiny(最快)": "Tiny (fastest)",
    "Small(均衡)": "Small (balanced)",
    "Medium(最准)": "Medium (most accurate)",
    "大模型润色（DeepSeek / OpenAI，可选）": "LLM polish (DeepSeek / OpenAI, optional)",
    "高级选项": "Advanced options",

    # ── DeepSeekProgressPanel ──
    "DeepSeek / 大模型处理进度": "DeepSeek / LLM progress",
    "启用 DeepSeek 后，此处显示处理进度，以及各批次润色前后的字幕对比、碎片合并候选与结果、策略复核说明等，便于核对模型具体改动了哪些字。":
        "With DeepSeek enabled, this panel shows progress, per-batch before/after subtitle diffs, fragment merge candidates and results, and strategy review notes, so you can verify exactly what the model changed.",
    "清空日志": "Clear log",

    # ── FileOperationsWidget ──
    "文件操作（支持拖放）": "File operations (drag & drop supported)",
    "加载视频": "Load video",
    "保存 ROI 配置": "Save ROI config",
    "加载 ROI 配置": "Load ROI config",
    "自动检测字幕ROI": "Auto-detect subtitle ROIs",
    "深度扫描（水印/场景字）…": "Deep scan (watermarks/in-scene text)…",
    "语言": "Language",
    "自动（跟随系统）": "Auto (follow system)",
    "切换语言": "Change language",
    "语言设置将在重启应用后生效。要立即重启吗？":
        "The language change takes effect after the app restarts. Restart now?",
    "立即重启": "Restart now",
    "稍后": "Later",
    "无法自动重启，请手动重启应用以应用新语言。":
        "Could not restart automatically. Please restart the app manually to apply the new language.",

    # ── LogViewerWidget ──
    "日志与进度": "Logs & progress",

    # ── RoiDefinitionWidget ──
    "ROI 定义": "ROI definition",
    "后退 1 帧（短按）/ 连续（长按）": "Back 1 frame (tap) / continuous (hold)",
    "前进 1 帧（短按）/ 连续（长按）": "Forward 1 frame (tap) / continuous (hold)",
    "输入时间（时:分:秒.毫秒）或帧号": "Enter time (h:m:s.ms) or frame number",
    "开始时间：": "Start time:",
    "设为开始时间": "Set as start time",
    "结束时间：": "End time:",
    "设为结束时间": "Set as end time",
    "按文字/描边/阴影颜色限制 OCR（不匹配像素将被遮罩）":
        "Restrict OCR by text/outline/shadow color (non-matching pixels are masked)",
    "将与所选颜色不接近的像素在 OCR 前置为白色，减少字幕笔画之外的误检。":
        "Pixels not close to the chosen colors are turned white before OCR to reduce false detections outside subtitle strokes.",
    "OCR 前对 ROI 轻微模糊（降低锯齿/噪声）": "Slight blur before OCR (reduces aliasing/noise)",
    "在 OCR 前对 ROI 裁剪图应用轻微高斯模糊。对噪声大/压缩重的字幕更有帮助。":
        "Applies a light Gaussian blur to the ROI crop before OCR. Most useful for noisy or heavily compressed subtitles.",
    "淡入/淡出微调（在检测到文字边界附近逐帧 OCR，找更精确的起止时间）":
        "Fade in/out refinement (frame-by-frame OCR near detected text boundaries for precise timing)",
    "开启后，会只在文字出现/消失的边界附近逐帧 OCR 用于校准时间；ROI 其余部分仍可使用跳帧优化。":
        "When on, frame-by-frame OCR runs only near text appear/disappear boundaries to calibrate timing; the rest of the ROI still benefits from frame-skip optimization.",
    "写入画面位置标签（\\pos \\frz \\frx \\fry，多边形自动计算位置与倾角）":
        "Write positioning tags (\\pos \\frz \\frx \\fry; polygons compute position and angle automatically)",
    "开启后，该 ROI 识别出的字幕事件将附带位置与旋转标签：多边形 ROI 自动计算中心点(\\pos)与长边倾角(\\frz)；\\frx/\\fry 保存为 0，可手动微调透视。适合实拍场景文字的原位重铺。":
        "When on, subtitle events from this ROI carry position and rotation tags: polygon ROIs compute the center (\\pos) and long-edge angle (\\frz) automatically; \\frx/\\fry are saved as 0 and can be tweaked manually for perspective. Ideal for re-laying in-scene text in place.",
    "场景文字显示：": "Scene text display:",
    "叠加（默认）": "Overlay (default)",
    "遮罩原文字": "Mask original text",
    "仅遮罩（供排版覆写）": "Mask only (for re-typesetting)",
    "外置展示框": "External note box",
    "空白区放置": "Place in whitespace",
    "仅作用于场景文字（画面文字）事件；所选模式不可用时自动回退（空白区→遮罩→外置；仅遮罩→外置）。「仅遮罩」把遮罩下方的识别文本写成 Comment 注释行（播放器不渲染），便于在遮罩上自行排版覆写（如绘制译文或 \\p 矢量字）。":
        "Applies to in-scene (picture text) events only; unavailable modes fall back automatically (whitespace → mask → external; mask-only → external). \"Mask only\" writes the recognized text under the patch as Comment lines (not rendered), so you can re-typeset over the patch by hand (e.g. draw translated text or \\p vector art).",
    "文字颜色…": "Text color…",
    "描边颜色…": "Outline color…",
    "阴影颜色…": "Shadow color…",
    "RGB 容差：": "RGB tolerance:",
    "单通道距离 0–255；数值越大，包含越多相近色阶（抗锯齿/渐变更稳）。":
        "Per-channel distance 0–255; higher values admit more similar shades (stabler with anti-aliasing/gradients).",
    "形态学：": "Morphology:",
    "闭运算核大小（奇数）；可在遮罩后连接断裂笔画。":
        "Closing kernel size (odd); reconnects broken strokes after masking.",
    "添加新 ROI": "Add new ROI",
    "更新选中 ROI": "Update selected ROI",
    "删除选中 ROI": "Delete selected ROI",
    "字幕文字颜色": "Subtitle text color",
    "字幕描边颜色": "Subtitle outline color",
    "字幕阴影颜色": "Subtitle shadow color",

    # ── RoiListWidget ──
    "ROI 列表": "ROI list",
    "ROI {}：帧[{}-{}] 时间[{} - {}]": "ROI {}: frames[{}-{}] time[{} - {}]",
    "[颜色掩膜]": "[color mask]",
    "[模糊]": "[blur]",
    "[淡入淡出微调]": "[fade refinement]",
    "[自动过滤]": "[auto filter]",
    "复制": "Copy",
    "粘贴到此项之后": "Paste after this item",
    "删除": "Delete",
    "切换文字过滤策略（当前：{}）": "Toggle text filter policy (current: {})",
    "自动过滤": "Auto filter",
    "全部保留": "Keep all",
    "粘贴到末尾": "Paste at end",
    "确认删除": "Confirm deletion",
    "确定要删除 ROI {} 吗？": "Delete ROI {}?",

    # ── SubtitleOCRGUI ──
    "未加载视频": "No video loaded",
    "请先加载视频文件。": "Load a video file first.",
    "视频字幕 OCR 工具": "Video Subtitle OCR Tool",
    "正在深度扫描…": "Deep scan in progress…",
    "正在检测字幕区域…": "Detecting subtitle regions…",
    "已通过拖放加载 ASS 模板：{}": "ASS template loaded via drag & drop: {}",
    "选择视频文件": "Select video file",
    "视频文件 (*.mp4 *.avi *.mov *.mkv)": "Video files (*.mp4 *.avi *.mov *.mkv)",
    "无法打开视频文件": "Could not open the video file",
    "视频字幕 OCR 工具 - {}": "Video Subtitle OCR Tool - {}",
    "视频已加载：{}": "Video loaded: {}",
    "分辨率：{}x{}，帧率：{:.2f}，总帧数：{}": "Resolution: {}x{}, FPS: {:.2f}, total frames: {}",
    "已自动加载 ROI 配置：{}": "ROI config auto-loaded: {}",
    "自动加载 ROI 配置失败：{}": "Failed to auto-load ROI config: {}",
    "无效的时间/帧号输入：'{}'": "Invalid time/frame input: '{}'",
    "已添加新 ROI，帧范围：{}-{}": "New ROI added, frame range: {}-{}",
    "已更新 ROI {}，新帧范围：{}-{}": "ROI {} updated, new frame range: {}-{}",
    "已删除 ROI {}": "ROI {} deleted",
    "ROI {} 文字过滤策略已切换为：{}": "ROI {} text filter policy switched to: {}",
    "按场景预设过滤": "Filter by scene preset",
    "全部保留（不过滤）": "Keep all (no filtering)",
    "已在画面上调整 ROI {} 的区域": "ROI {} region adjusted on the frame",
    "警告": "Warning",
    "开始时间不能晚于结束时间。": "Start time cannot be after end time.",
    "创建 ROI 条目时出错：{}": "Error creating ROI entry: {}",
    "创建 ROI 时发生错误：{}": "Error creating ROI: {}",
    "JSON 文件 (*.json)": "JSON files (*.json)",
    "ROI 配置已保存到：{}": "ROI config saved to: {}",
    "保存 ROI 配置失败：{}": "Failed to save ROI config: {}",
    "自动备份 ROI 配置失败（识别仍会继续）：{}": "Failed to back up ROI config (recognition continues): {}",
    "为防意外中断，已自动备份 ROI 配置到：{}": "ROI config auto-backed up in case of interruption: {}",
    "ROI 配置已从 {} 加载": "ROI config loaded from {}",
    "加载 ROI 配置失败：{}": "Failed to load ROI config: {}",
    "已将 ROI {} 复制到剪贴板。": "ROI {} copied to clipboard.",
    "已粘贴到 ROI {} 之后。": "Pasted after ROI {}.",
    "已将 ROI 粘贴到列表末尾。": "ROI pasted at the end of the list.",
    "选择 ASS 模板文件": "Select ASS template file",
    "ASS 字幕文件 (*.ass)": "ASS subtitle files (*.ass)",
    "请先加载视频并至少定义一个 ROI。": "Load a video and define at least one ROI first.",
    "当前帧不在任一 ROI 的时间范围内。请将时间轴移到含字幕的典型帧上，用于自动标定颜色。":
        "The current frame is outside every ROI's time span. Move the timeline to a typical frame with subtitles for automatic color calibration.",
    "颜色门控预览失败：{}": "Color gate preview failed: {}",
    "预览失败": "Preview failed",
    "颜色门控已确认；下次运行「字幕 OCR 识别」时将在阶段一启用。":
        "Color gate confirmed; it will be enabled in stage one of the next subtitle OCR run.",
    "已有 OCR 任务在运行中，请等待其完成或先取消。":
        "An OCR task is already running. Wait for it to finish or cancel it first.",
    "已勾选「按字幕颜色跳过疑似无字帧」，但尚未通过预览确认。请点击「预览检测效果」并在满意时选择「采用」，或取消勾选以使用默认流程。":
        "\"Skip likely textless frames by subtitle color\" is checked but not yet confirmed via preview. Click \"Preview detection\" and choose \"Apply\" if satisfied, or uncheck it to use the default flow.",
    "已启用 DeepSeek 功能（润色/碎片合并/策略复核），但未填写 API Key。请填写 API Key，或设置环境变量 DEEPSEEK_API_KEY。":
        "DeepSeek features (polish/fragment merge/strategy review) are enabled but no API Key is set. Enter an API Key or set the DEEPSEEK_API_KEY environment variable.",
    "保存字幕文件": "Save subtitle file",
    "ASS 字幕 (*.ass)": "ASS subtitles (*.ass)",
    "用户取消保存，OCR 任务已中止。": "Save cancelled by user; OCR task aborted.",
    "[LLM] 已启用 DeepSeek —— 第 4 步的大模型进度会显示在下方。":
        "[LLM] DeepSeek enabled — step 4 LLM progress is shown below.",
    "字幕 OCR + DeepSeek": "Subtitle OCR + DeepSeek",
    "字幕 OCR": "Subtitle OCR",
    "正在处理视频...": "Processing video...",
    "取消": "Cancel",
    "正在识别…": "Recognizing…",
    "[LLM] 完成 —— 已写入 ASS 文件。": "[LLM] Done — ASS file written.",
    "完成": "Done",
    "字幕文件已生成：{}": "Subtitle file created: {}",
    "打开目录": "Open folder",
    "是否打开包含该文件的文件夹？": "Open the folder containing this file?",
    "[LLM] 已中止或出错 —— 请查看弹窗提示。": "[LLM] Aborted or failed — see the dialog for details.",
    "处理过程中发生错误：\n{}": "An error occurred during processing:\n{}",
    "已恢复上次保存的文字来源过滤设置。": "Restored the previously saved text source filter settings.",
    "视频类型自动检测完成：{} (置信度 {:.0%})": "Video type auto-detection finished: {} (confidence {:.0%})",
    "视频类型自动检测失败：{}": "Video type auto-detection failed: {}",
    "自动分析失败，请手动选择场景类型": "Auto analysis failed; choose the scene type manually",
    "正在后台扫描字幕分布（采样 {} 帧）…": "Scanning subtitle distribution in the background ({} sampled frames)…",
    "请选择自动检测到的字幕区域的处理方式：\n\n「是」：追加到现有 ROI 列表末尾（保留现有 ROI）；\n「否」：替换全部现有 ROI（现有 ROI 将被清除，不可恢复）；\n「取消」：中止本次检测。":
        "How should the auto-detected subtitle regions be applied?\n\nYes: append to the end of the existing ROI list (keeps existing ROIs);\nNo: replace all existing ROIs (they will be cleared and cannot be recovered);\nCancel: abort this detection.",
    "替换现有 ROI？": "Replace existing ROIs?",
    "即将清除现有 {} 个 ROI 并用检测结果替换，此操作不可恢复。是否继续？":
        "This will clear {} existing ROIs and replace them with the detection result. This cannot be undone. Continue?",
    "确认退出": "Confirm exit",
    "后台任务仍在运行，确定要退出吗？": "Background tasks are still running. Quit anyway?",
    "错误": "Error",
    "自动字幕定位模块不可用：{}\n请确认 core/subtitle_roi_suggester 模块及其依赖已正确安装。":
        "Automatic subtitle localization module unavailable: {}\nMake sure the core/subtitle_roi_suggester module and its dependencies are installed.",
    "正在自动检测字幕区域…": "Auto-detecting subtitle regions…",
    "自动字幕检测进度：{}/{}（采样帧）": "Subtitle auto-detection progress: {}/{} (sampled frames)",
    "未检测到字幕区域。请确认视频中确实存在字幕，或调整识别语言后重试。":
        "No subtitle regions detected. Make sure the video actually contains subtitles, or adjust the recognition language and retry.",
    "自动检测完成：检测到 {} 个字幕区域，时间范围 {} ～ {}":
        "Auto-detection finished: {} subtitle regions found, time span {} – {}",
    "已用 {} 个自动检测的 ROI 替换全部现有 ROI。": "Replaced all existing ROIs with {} auto-detected ROIs.",
    "正在深度扫描（采样 {} 帧，含水印/场景字统计）…": "Deep scan in progress ({} sampled frames, with watermark/in-scene text stats)…",
    "深度扫描": "Deep scan",
    "扫描完成：未检测到字幕带、水印或场景文字。": "Scan finished: no subtitle bands, watermarks, or in-scene text detected.",
    "将在识别时剔除 {} 处水印文本。": "{} watermark text(s) will be removed during recognition.",
    "未启用水印剔除。": "Watermark removal not enabled.",
    "已导入 {} 个字幕带 ROI、{} 个场景字 ROI。": "Imported {} subtitle band ROI(s) and {} in-scene text ROI(s).",
    "字幕扫描进度：{}/{}（采样帧）": "Subtitle scan progress: {}/{} (sampled frames)",
    "后台扫描未检测到字幕带，可手动绘制 ROI。": "The background scan found no subtitle bands; you can draw ROIs manually.",
    "已追加 {} 个自动检测的 ROI 到列表末尾。": "Appended {} auto-detected ROI(s) to the end of the list.",
    "检测到 {} 处疑似水印（恒定文本/位置）。可运行深度扫描复核后自动剔除。":
        "{} suspected watermark(s) detected (constant text/position). Run a deep scan review to remove them automatically.",
    "检测到 {} 处画面中部文字（场景字候选）。可在深度扫描复核中导入为 ROI。":
        "{} mid-frame text(s) detected (in-scene text candidates). Import them as ROIs in the deep scan review.",
    "自动检测失败：{}": "Auto-detection failed: {}",

    # ── ScanReviewDialog ──
    "深度扫描复核": "Deep scan review",
    "字幕带 ROI（自动检测的主字幕区）": "Subtitle band ROIs (auto-detected main subtitle regions)",
    "底部": "Bottom",
    "顶部": "Top",
    "中部": "Middle",
    "{0}  帧 [{1}-{2}]": "{0}  frames [{1}-{2}]",
    "用所选字幕带替换现有 ROI 列表（否则追加）": "Replace the existing ROI list with the selected bands (otherwise append)",
    "未检测到字幕带。": "No subtitle bands detected.",
    "疑似水印（恒定文本 + 恒定位置，勾选=识别时剔除）": "Suspected watermarks (constant text + position; checked = removed during recognition)",
    "「{0}」 出现率 {1:.0%}": "\"{0}\" presence {1:.0%}",
    "未检测到疑似水印。": "No suspected watermarks detected.",
    "画面中部文字（场景字候选，勾选=导入为 ROI，全部保留）": "Mid-frame text (in-scene candidates; checked = import as ROI, keep all)",
    "「{0}」 出现 {1} 次（帧 {2}-{3}）": "\"{0}\" seen {1} time(s) (frames {2}-{3})",
    "（其余 {} 处低频文字未列出）": "(remaining {} low-frequency text(s) not listed)",
    "未检测到画面中部文字。": "No mid-frame text detected.",
    "应用": "Apply",

    # ── VideoFrameLabel ──
    "绘制错误：{}": "Drawing error: {}",

    # ── 移动文字轨迹管线集成(pose 绑定 + 亮度自适应) ──
    '开启后，该 ROI 识别出的字幕事件将附带位置与旋转标签：多边形 ROI 自动计算中心点(\\pos)与长边倾角(\\frz)；\\frx/\\fry 保存为 0，可手动微调透视。适合实拍场景文字的原位重铺。四点多边形 ROI 会走移动文字轨迹管线：逐帧跟踪平面并合成 \\move 运动字幕，静态标签跟不动的画面由轨迹跟随。': 'When enabled, subtitle events from this ROI carry placement and rotation tags: polygon ROIs auto-compute the center (\\pos) and long-edge tilt (\\frz); \\frx/\\fry are saved as 0 for manual perspective tuning. Ideal for re-laying live-action on-screen text in place. Four-point polygon ROIs go through the moving-text trajectory pipeline: the plane is tracked frame by frame and \\move motion subtitles are synthesized — movement that static tags cannot follow is handled by the trajectory.',
    '亮度自适应（轨迹字幕跟随屏幕明暗）': 'Brightness adaptive (trajectory subtitles follow screen dimming)',
    '需勾选「写入画面位置标签」且 ROI 为四点多边形（此时走移动文字轨迹管线）。开启后逐帧测量文字平面亮度，为轨迹事件追加 \\1c/\\alpha \\t 标签链，字幕颜色与透明度忠实跟随屏幕变暗/变亮（如手机息屏）。默认关闭。': 'Requires “Write picture position tags” and a four-point polygon ROI (which runs the moving-text trajectory pipeline). Measures the text-plane brightness frame by frame and appends \\1c/\\alpha \\t tag chains to trajectory events, so subtitle color and opacity faithfully follow the screen dimming/brightening (e.g. a phone screen turning off). Off by default.',
    'ROI {} 画面位置标签已{}': 'ROI {} picture position tags {}',
    '开启': 'enabled',
    '关闭': 'disabled',
    '[LLM] 已取消 —— 未生成字幕文件。': '[LLM] Cancelled - no subtitle file was generated.',
    '遮挡蒙版（\\iclip，手部遮挡时不渲染字幕）':
        'Occlusion mask (\\iclip: hide subtitles where the hand covers the screen)',
    '需勾选「写入画面位置标签」（轨迹模式）。开启后检测手部等遮挡物覆盖文字平面的帧，为受影响的事件追加 \\iclip 逆向蒙版，字幕不再渲染到遮挡物上（遮挡结束时自动恢复显示）。默认关闭。':
        'Requires "Write plane-position tags" (trajectory mode). Detects frames where a hand or another occluder covers the text plane and appends an \\iclip inverse mask to affected events, so subtitles never render over the occluder (display resumes when the occlusion ends). Off by default.',
    '自动检测移动文字（轨迹字幕）':
        'Auto-detect moving text (trajectory subtitles)',
    '无需手动勾选「写入画面位置标签」：开始识别时在每个 ROI 范围内采样 OCR，若文字行心位移超过移动门限（24px），自动对该区域走移动文字轨迹管线并抑制静态碎片事件。检测成本与采样密度（0.5 秒/帧）成正比，建议 ROI 尽量圈紧移动文字。':
        'No need to tick "Write plane-position tags" manually: at recognition start, each ROI is sample-OCR\'ed, and any text line whose centre moves beyond the movement threshold (24 px) is handed to the motion-trajectory pipeline, suppressing its static fragment events. Detection cost scales with sampling density (0.5 s/frame); keep the ROI tight around the moving text.',

    'ROI {} 亮度自适应已{}': 'ROI {}: brightness adaptation {}',
    '当前未选中任何 ROI：该开关只会写入之后「添加新 ROI」的条目；如需应用到已有 ROI，请先在列表中选中它再勾选。':
        'No ROI is currently selected: this switch will only apply to entries added later via "Add ROI". To apply it to an existing ROI, select that ROI in the list first, then toggle.',
    'ROI {} 遮挡蒙版已{}': 'ROI {}: occlusion mask {}',
    'ROI {} 场景文字显示已设为 {}': 'ROI {}: scene-text display set to {}',


    '自动（跟随全局）': 'Auto (follow global)',
    '该 ROI 单独使用的 OCR 识别语言；默认「自动」跟随控制面板的全局「识别语言」。双语字幕请给每种语言各画一个 ROI 并分别指定语言（如繁中行设「中文繁體」、日语行设「日本語」），各 ROI 用各自的识别模型，避免单一模型漏认另一种文字。注意：每个不同语言会多加载一份识别模型，内存占用相应增加；RapidOCR 引擎不区分语言，此设置仅对 PaddleOCR 生效。': 'OCR language used only by this ROI; the default "Auto" follows the global "Recognition language" in the control panel. For bilingual subtitles, draw one ROI per language and assign each its own language (e.g. the Traditional-Chinese line as 中文繁體, the Japanese line as 日本語), so each ROI runs its own recognition model instead of one model missing the other script. Note: every distinct language loads an extra recognition model and memory use grows accordingly; the RapidOCR engine uses a single multilingual model and ignores this setting — it applies to PaddleOCR only.',
    'ROI {} 识别语言已设为 {}': 'ROI {}: recognition language set to {}',
    'Motion trajectory for {0} covers only {1:.0f}% of the ROI time range; falling back to static pose tags.': 'Motion trajectory for {0} covers only {1:.0f}% of the ROI time range; using static pose tags instead.',

    # ── T2.5 AI 翻译选项组（ControlPanelWidget / SubtitleOCRGUI）──
    "AI 翻译（可选）": "AI translation (optional)",
    "目标语言：": "Target language:",
    "翻译提供方：": "Translation provider:",
    "云端 API": "Cloud API",
    "本地 Sakura": "Local Sakura",
    "VLM 兜底": "VLM fallback",
    "API Base URL（云端 / Sakura 共用）": "API base URL (shared by cloud / Sakura)",
    "模型名（如 deepseek-chat / sakura-14b）": "Model name (e.g. deepseek-chat / sakura-14b)",
    "术语表 JSON：": "Glossary JSON:",
    "选择术语表 JSON 文件": "Select glossary JSON file",
    "术语表 JSON (*.json)": "Glossary JSON (*.json)",
    "上下文行数：": "Context lines:",
    "最大行长：": "Max line length:",
    "把识别出的字幕行交给大模型翻译成目标语言（在润色之后、写出之前执行）。云端提供方复用「大模型润色」区已保存的 API Key；某一级未配置或失败时自动降级到下一级，全部失败保留原文。":
        "Translate recognized subtitle lines into the target language with an LLM (runs after polish, before the ASS is written). The cloud provider reuses the API key saved in the LLM polish section; an unconfigured or failed level automatically falls back to the next one, and a fully exhausted chain keeps the original text.",
    "云端 API：OpenAI 兼容端点（默认 DeepSeek）；本地 Sakura：本地部署的 Sakura 日中翻译模型（OpenAI 兼容 /v1 端点）；VLM 兜底：复用 VLM_REFINE_* 环境变量，零配置。":
        "Cloud API: OpenAI-compatible endpoint (DeepSeek by default). Local Sakura: a locally deployed Sakura JP-ZH translation model (OpenAI-compatible /v1 endpoint). VLM fallback: reuses the VLM_REFINE_* environment variables, zero configuration.",
    "可选。JSON 文件（{\"术语\": \"译名\"}），译文中术语强制一致；文件缺失或格式错误时自动忽略。":
        'Optional. A JSON file ({"term": "translation"}) enforcing consistent terminology in every translation; missing or malformed files are ignored automatically.',
    "已启用 AI 翻译（云端 API），但未填写 API Key。请在大模型润色区填写 API Key，或设置环境变量 DEEPSEEK_API_KEY。":
        "AI translation (cloud API) is enabled, but no API key has been entered. Enter an API key in the LLM polish section, or set the DEEPSEEK_API_KEY environment variable.",
    "已启用 AI 翻译（本地 Sakura），但未填写 Base URL。请填写本地 Sakura 服务器的 OpenAI 兼容端点地址（如 http://127.0.0.1:8080/v1）。":
        "AI translation (local Sakura) is enabled, but no base URL has been entered. Enter the OpenAI-compatible endpoint address of the local Sakura server (e.g. http://127.0.0.1:8080/v1).",
    "已启用 AI 翻译，但翻译模块不可用（{0}）。请先安装 font_intel 依赖（requirements-fontintel.txt），或取消勾选「AI 翻译」后重试。":
        "AI translation is enabled, but the translation module is unavailable ({0}). Install the font_intel dependencies (requirements-fontintel.txt) first, or untick \"AI translation\" and try again.",
    "[LLM] 已启用 AI 翻译 —— 翻译进度会显示在下方。":
        "[LLM] AI translation enabled — translation progress appears below.",

    # ── T2.5 FontMapReviewDialog（T1.5 新增文案的 i18n 欠账补齐）──
    "字体映射复核": "Font mapping review",
    "无待复核记录。": "No entries pending review.",
    "低置信度，建议人工确认": "Low confidence; manual review recommended",
    "规则未能解析该行": "The rule pass could not parse this line",
    "疑似幻觉字体名（归一化后不是原文行子串）": "Suspected hallucinated font name (not a substring of the source line after normalization)",
    "字体名未命中本地词表": "Font name not found in the local lexicon",
    "LLM 调用失败（有界退避耗尽），本行未结构化": "LLM call failed (bounded backoff exhausted); this line was not structured",
    "LLM 输出不符合约定格式，本行未结构化": "LLM output did not match the agreed format; this line was not structured",
    "未配置 API Key，本行未结构化": "No API key configured; this line was not structured",
    "空行": "Empty line",
    "注释行": "Comment line",
    "第 {0} 行：{1} → {2}": "Line {0}: {1} → {2}",
    "第 {0} 行：{1} →（负映射）": "Line {0}: {1} → (negative mapping)",
    "第 {0} 行：{1}": "Line {0}: {1}",
    "原文：{0}": "Original: {0}",
    "建议：{0} → {1}": "Suggestion: {0} → {1}",
    "建议：{0} 无日文本家对应": "Suggestion: {0} has no Japanese official counterpart",
    "类型：{0} ｜ 置信度：{1} ｜ 来源：{2} ｜ 文件：{3}": "Type: {0} | Confidence: {1} | Source: {2} | File: {3}",
    "理由：{0}": "Reason: {0}",
    "（该条目不写库；如需修正请在源表修订后重跑导入）": "(this entry is not written to the database; to fix it, revise the source table and rerun the import)",
}
