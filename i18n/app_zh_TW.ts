<?xml version="1.0" encoding="utf-8"?>
<!DOCTYPE TS>
<TS version="2.1" language="zh_TW">
<context>
    <name>ColorGatePreviewDialog</name>
    <message>
        <location filename="../components/color_gate_preview_dialog.py" line="+43"/>
        <source>颜色门控：预览</source>
        <translation>顏色門檻：預覽</translation>
    </message>
    <message>
        <location line="+18"/>
        <source>以下缩略来自 ROI 区间内均匀采样。请确认「判定为保留」与您期望一致。若不满意，请选择「取消」或直接关闭对话框，并保持主界面选项关闭或未确认。</source>
        <translation>以下縮圖取自 ROI 區間內的均勻取樣。請確認「判定為保留」與您的期望一致。若不滿意，請選擇「取消」或直接關閉對話框，並保持主介面選項關閉或未確認。</translation>
    </message>
    <message>
        <location line="+10"/>
        <source>阈值与预估</source>
        <translation>閾值與預估</translation>
    </message>
    <message>
        <location line="+8"/>
        <source>命中面积占比下限：</source>
        <translation>命中面積占比下限：</translation>
    </message>
    <message>
        <location line="+21"/>
        <source>采用（用于本次识别）</source>
        <translation>採用（用於本次辨識）</translation>
    </message>
    <message>
        <location line="+3"/>
        <source>不采用</source>
        <translation>不採用</translation>
    </message>
    <message>
        <location line="+25"/>
        <source>采样帧数：{}；在当前阈值下「保留」帧数：{}（约 {:.1f}%）；按此比例粗略估计全流程 ROI 图数量约：{} / {}（原计划）。</source>
        <translation>取樣影格數：{}；在目前閾值下「保留」影格數：{}（約 {:.1f}%）；按此比例粗略估計全流程 ROI 圖數量約：{} / {}（原計畫）。</translation>
    </message>
    <message>
        <location line="+12"/>
        <source>保留</source>
        <translation>保留</translation>
    </message>
    <message>
        <location line="+2"/>
        <source>跳过</source>
        <translation>跳過</translation>
    </message>
    <message>
        <location line="+3"/>
        <source>帧 {} · {}</source>
        <translation>影格 {} · {}</translation>
    </message>
    <message>
        <location line="+3"/>
        <source>max 占比：{ratio:.4f}</source>
        <translation>max 占比：{ratio:.4f}</translation>
    </message>
</context>
<context>
    <name>ControlPanelWidget</name>
    <message>
        <location filename="../components/control_panel.py" line="+100"/>
        <source>样式模板（可选）</source>
        <translation>樣式範本（可選）</translation>
    </message>
    <message>
        <location line="+5"/>
        <source>点击“浏览”选择 .ass 模板文件</source>
        <translation>點擊「瀏覽」選擇 .ass 範本檔案</translation>
    </message>
    <message>
        <location line="+1"/>
        <source>浏览…</source>
        <translation>瀏覽…</translation>
    </message>
    <message>
        <location line="+7"/>
        <source>识别设置</source>
        <translation>辨識設定</translation>
    </message>
    <message>
        <location line="+6"/>
        <source>识别语言：</source>
        <translation>辨識語言：</translation>
    </message>
    <message>
        <location line="+3"/>
        <source>选择字幕语言。中文/英语/日语使用 PP-OCRv6 模型，其他语言自动回落 PP-OCRv5 多语言模型。</source>
        <translation>選擇字幕語言。中文／英文／日文使用 PP-OCRv6 模型，其他語言自動回落 PP-OCRv5 多語言模型。</translation>
    </message>
    <message>
        <location line="+28"/>
        <source>文字保留：</source>
        <translation>文字保留：</translation>
    </message>
    <message>
        <location line="+3"/>
        <source>控制识别结果保留哪些文字。「保留全部文字」不做任何过滤；更精细的规则可在完整设置中调整。</source>
        <translation>控制辨識結果保留哪些文字。「保留全部文字」不做任何過濾；更精細的規則可在完整設定中調整。</translation>
    </message>
    <message>
        <location line="+8"/>
        <source>保留全部文字</source>
        <translation>保留全部文字</translation>
    </message>
    <message>
        <location line="+2"/>
        <source>只保留字幕</source>
        <translation>只保留字幕</translation>
    </message>
    <message>
        <location line="+2"/>
        <source>字幕和画面文字</source>
        <translation>字幕和畫面文字</translation>
    </message>
    <message>
        <location line="+2"/>
        <source>自定义（在完整设置中调整）</source>
        <translation>自訂（在完整設定中調整）</translation>
    </message>
    <message>
        <location line="+8"/>
        <source>OCR 引擎（自动选择）</source>
        <translation>OCR 引擎（自動選擇）</translation>
    </message>
    <message>
        <location line="+5"/>
        <source>引擎选择：</source>
        <translation>引擎選擇：</translation>
    </message>
    <message>
        <location line="+3"/>
        <source>检测可用引擎</source>
        <translation>偵測可用引擎</translation>
    </message>
    <message>
        <location line="+5"/>
        <source>模型档位：</source>
        <translation>模型等級：</translation>
    </message>
    <message>
        <location line="+3"/>
        <source>PaddleOCR 模型档位：Tiny 最快，Small 均衡，Medium 最准。“自动”使用 PP-OCRv6 默认模型。</source>
        <translation>PaddleOCR 模型等級：Tiny 最快，Small 均衡，Medium 最準。「自動」使用 PP-OCRv6 預設模型。</translation>
    </message>
    <message>
        <location line="+24"/>
        <source>文字来源过滤（上下文语义分析）</source>
        <translation>文字來源過濾（上下文語意分析）</translation>
    </message>
    <message>
        <location line="+7"/>
        <source>启用文字来源过滤（实验性）</source>
        <translation>啟用文字來源過濾（實驗性）</translation>
    </message>
    <message>
        <location line="+6"/>
        <source>勾选后，OCR 识别结果将经过上下文语义分析，自动区分后期叠加字幕与实拍场景文字。未勾选时保留所有识别到的文字。</source>
        <translation>勾選後，OCR 辨識結果將經過上下文語意分析，自動區分後期疊加字幕與實拍場景文字。未勾選時保留所有辨識到的文字。</translation>
    </message>
    <message>
        <location line="+11"/>
        <source>🔄 加载视频后将自动分析...</source>
        <translation>🔄 載入影片後將自動分析...</translation>
    </message>
    <message>
        <location line="+9"/>
        <source>场景预设：</source>
        <translation>場景預設：</translation>
    </message>
    <message>
        <location line="+2"/>
        <source>选择适合当前视频的场景类型，自动配置文字来源过滤规则</source>
        <translation>選擇適合目前影片的場景類型，自動設定文字來源過濾規則</translation>
    </message>
    <message>
        <location line="+7"/>
        <source>保留后期叠加文字 (OVERLAY)
    字幕、标题、水印、UI 按钮、弹幕、特效文字</source>
        <translation>保留後期疊加文字 (OVERLAY)
    字幕、標題、浮水印、UI 按鈕、彈幕、特效文字</translation>
    </message>
    <message>
        <location line="+6"/>
        <source>保留实拍场景文字 (SCENE)
    店铺招牌、路牌、宣传海报、书本、屏幕、标牌</source>
        <translation>保留實拍場景文字 (SCENE)
    店鋪招牌、路牌、宣傳海報、書本、螢幕、標牌</translation>
    </message>
    <message>
        <location line="+6"/>
        <source>保留无法判定文字 (UNKNOWN)
    分类器置信度不足的边界情况</source>
        <translation>保留無法判定文字 (UNKNOWN)
    分類器信心度不足的邊界情況</translation>
    </message>
    <message>
        <location line="+12"/>
        <source>💡 备选方案:</source>
        <translation>💡 備選方案:</translation>
    </message>
    <message>
        <location line="+16"/>
        <source>🔄 重新自动检测</source>
        <translation>🔄 重新自動偵測</translation>
    </message>
    <message>
        <location line="+1"/>
        <source>重新采样分析当前视频并更新类型判定</source>
        <translation>重新取樣分析目前影片並更新類型判定</translation>
    </message>
    <message>
        <location line="+1"/>
        <source>重置为预设默认值</source>
        <translation>重設為預設值</translation>
    </message>
    <message>
        <location line="+8"/>
        <source>绘制模式</source>
        <translation>繪製模式</translation>
    </message>
    <message>
        <location line="+4"/>
        <source>矩形（拖动）</source>
        <translation>矩形（拖曳）</translation>
    </message>
    <message>
        <location line="+1"/>
        <source>多边形（点击）</source>
        <translation>多邊形（點擊）</translation>
    </message>
    <message>
        <location line="+1"/>
        <source>编辑（拖动调整）</source>
        <translation>編輯（拖曳調整）</translation>
    </message>
    <message>
        <location line="+2"/>
        <source>在画面上直接调整已有 ROI：拖动整体移动；矩形拖 8 个控制点缩放；多边形拖顶点改形。自动检测的 ROI 可用此模式微调。</source>
        <translation>在畫面上直接調整已有 ROI：拖曳整體移動；矩形拖曳 8 個控制點縮放；多邊形拖曳頂點改形。自動偵測的 ROI 可用此模式微調。</translation>
    </message>
    <message>
        <location line="+13"/>
        <source>字幕生成</source>
        <translation>字幕產生</translation>
    </message>
    <message>
        <location line="+4"/>
        <source>开始识别并导出</source>
        <translation>開始辨識並匯出</translation>
    </message>
    <message>
        <location line="+5"/>
        <source>调整字幕区域</source>
        <translation>調整字幕區域</translation>
    </message>
    <message>
        <location line="+2"/>
        <source>进入 ROI 编辑状态：在画面上拖动、缩放或微调已检测到的字幕区域。</source>
        <translation>進入 ROI 編輯狀態：在畫面上拖曳、縮放或微調已偵測到的字幕區域。</translation>
    </message>
    <message>
        <location line="+5"/>
        <source>重新检测</source>
        <translation>重新偵測</translation>
    </message>
    <message>
        <location line="+2"/>
        <source>对当前视频重新执行自动分析（场景类型判定）。</source>
        <translation>對目前影片重新執行自動分析（場景類型判定）。</translation>
    </message>
    <message>
        <location line="+9"/>
        <source>大模型润色（DeepSeek / OpenAI，可选）</source>
        <translation>大型模型潤飾（DeepSeek / OpenAI，可選）</translation>
    </message>
    <message>
        <location line="+3"/>
        <source>高级选项</source>
        <translation>進階選項</translation>
    </message>
    <message>
        <location line="+6"/>
        <source>调试模式</source>
        <translation>除錯模式</translation>
    </message>
    <message>
        <location line="+1"/>
        <source>可视化输出</source>
        <translation>視覺化輸出</translation>
    </message>
    <message>
        <location line="+1"/>
        <source>内存模式（实验性）</source>
        <translation>記憶體模式（實驗性）</translation>
    </message>
    <message>
        <location line="+1"/>
        <source>保存中间 JSON</source>
        <translation>儲存中繼 JSON</translation>
    </message>
    <message>
        <location line="+1"/>
        <source>按时间分片并行（可选）</source>
        <translation>依時間分片並行（可選）</translation>
    </message>
    <message>
        <location line="+1"/>
        <source>合并 ROI（每帧只 OCR 一次）</source>
        <translation>合併 ROI（每影格只 OCR 一次）</translation>
    </message>
    <message>
        <location line="+2"/>
        <location line="+3"/>
        <source>秒</source>
        <translation>秒</translation>
    </message>
    <message>
        <location line="+16"/>
        <source>加载视频后自动检测字幕 ROI</source>
        <translation>載入影片後自動偵測字幕 ROI</translation>
    </message>
    <message>
        <location line="+3"/>
        <source>加载视频后在后台采样扫描全片，自动生成顶部/底部字幕带 ROI（文字过滤策略为「自动过滤」，可随时在 ROI 列表右键切换）。</source>
        <translation>載入影片後在背景取樣掃描全片，自動產生頂部／底部字幕帶 ROI（文字過濾策略為「自動過濾」，可隨時在 ROI 清單右鍵切換）。</translation>
    </message>
    <message>
        <location line="+8"/>
        <source>进程分片（长视频提速）</source>
        <translation>處理程序分片（加速長影片）</translation>
    </message>
    <message>
        <location line="+4"/>
        <source>自动</source>
        <translation>自動</translation>
    </message>
    <message>
        <location line="+4"/>
        <source>把长视频按时间切成多个窗口，用多个进程并行识别（与「按时间分片并行」的线程桶不同）。「自动」按 CPU 核数与可用内存决定；仅 CPU 模式生效，短视频自动走单进程。每个并行进程约占 600MB 内存。</source>
        <translation>把長影片依時間切成多個視窗，用多個處理程序並行辨識（與「依時間分片並行」的執行緒桶不同）。「自動」依 CPU 核心數與可用記憶體決定；僅 CPU 模式生效，短片自動走單一處理程序。每個並行處理程序約佔 600MB 記憶體。</translation>
    </message>
    <message>
        <location line="+25"/>
        <source>半自动：按字幕颜色跳过疑似无字帧（默认关，需预览并确认后才生效）</source>
        <translation>半自動：依字幕顏色跳過疑似無字影格（預設關閉，需預覽並確認後才生效）</translation>
    </message>
    <message>
        <location line="+7"/>
        <source>在阶段一抽样 ROI 区间内若干帧并在当前画面上自动标定 HSV。仅当预览结果满意并在对话框中点击「采用」后，才会在本轮 OCR 启用；可随时关闭恢复默认逻辑。若预览不满意或选择「不采用」，请保持勾选关闭或未确认——程序将按原版流程输出全部 ROI 帧。</source>
        <translation>在階段一取樣 ROI 區間內若干影格並在目前畫面上自動標定 HSV。僅當預覽結果滿意並在對話框中點擊「採用」後，才會在本輪 OCR 啟用；可隨時關閉恢復預設邏輯。若預覽不滿意或選擇「不採用」，請保持勾選關閉或未確認——程式將按原版流程輸出全部 ROI 影格。</translation>
    </message>
    <message>
        <location line="+8"/>
        <source>预览检测效果…</source>
        <translation>預覽偵測效果…</translation>
    </message>
    <message>
        <location line="+16"/>
        <source>DeepSeek 字幕润色</source>
        <translation>DeepSeek 字幕潤飾</translation>
    </message>
    <message>
        <location line="+2"/>
        <source>兼容 OpenAI 的接口。默认提供方为 DeepSeek，会自动填充 Base URL；当你输入 API Key 后，应用会调用 /v1/models 拉取模型列表（也可手动编辑模型 ID）。</source>
        <translation>相容 OpenAI 的介面。預設提供方為 DeepSeek，會自動填入 Base URL；當您輸入 API Key 後，應用會呼叫 /v1/models 拉取模型清單（也可手動編輯模型 ID）。</translation>
    </message>
    <message>
        <location line="+7"/>
        <source>DeepSeek 合并碎片字幕（选择最完整文本并合并时间范围）</source>
        <translation>DeepSeek 合併碎片字幕（挑選最完整文字並合併時間範圍）</translation>
    </message>
    <message>
        <location line="+6"/>
        <source>API Key</source>
        <translation type="unfinished"></translation>
    </message>
    <message>
        <location line="+3"/>
        <source>API Key 不会以明文写入配置文件：优先保存在系统钥匙串（需安装 keyring）；不可用时使用本地加密存储（密钥文件仅当前用户可读）。清除可点击右侧按钮。</source>
        <translation>API Key 不會以明文寫入設定檔：優先儲存在系統鑰匙圈（需安裝 keyring）；無法使用時改用本機加密儲存（金鑰檔案僅目前使用者可讀）。清除可點擊右側按鈕。</translation>
    </message>
    <message>
        <location line="+8"/>
        <source>API Base URL</source>
        <translation type="unfinished"></translation>
    </message>
    <message>
        <location line="+3"/>
        <source>大模型提供方</source>
        <translation>大型模型提供方</translation>
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
        <translation>自訂（手動 Base URL）</translation>
    </message>
    <message>
        <location line="+8"/>
        <source>模型 ID（可从 API 自动拉取）</source>
        <translation>模型 ID（可從 API 自動拉取）</translation>
    </message>
    <message>
        <location line="+4"/>
        <source>刷新模型列表</source>
        <translation>重新整理模型清單</translation>
    </message>
    <message>
        <location line="+1"/>
        <location line="+459"/>
        <source>使用 API Key 和 Base URL 拉取 /v1/models。</source>
        <translation>使用 API Key 和 Base URL 拉取 /v1/models。</translation>
    </message>
    <message>
        <location line="-457"/>
        <source>清除已存密钥</source>
        <translation>清除已存金鑰</translation>
    </message>
    <message>
        <location line="+2"/>
        <source>从本机配置中删除已保存的 API Key（输入框会清空）。Base URL 与模型仍会保留。</source>
        <translation>從本機設定中刪除已儲存的 API Key（輸入框會清空）。Base URL 與模型仍會保留。</translation>
    </message>
    <message>
        <location line="+7"/>
        <source>DeepSeek 合并策略复核（按更合适的阈值重新合并）</source>
        <translation>DeepSeek 合併策略複核（依更合適的閾值重新合併）</translation>
    </message>
    <message>
        <location line="+55"/>
        <source>完整设置</source>
        <translation>完整設定</translation>
    </message>
    <message>
        <location line="+3"/>
        <source>显示全部设置（文字来源过滤、绘制模式、引擎详情）。已调整的选项保持不变。</source>
        <translation>顯示全部設定（文字來源過濾、繪製模式、引擎詳細資訊）。已調整的選項保持不變。</translation>
    </message>
    <message>
        <location line="+5"/>
        <source>简洁界面</source>
        <translation>簡潔介面</translation>
    </message>
    <message>
        <location line="+3"/>
        <source>返回一键简洁视图，隐藏高级设置。所有选项保持不变。</source>
        <translation>返回一鍵簡潔檢視，隱藏進階設定。所有選項保持不變。</translation>
    </message>
    <message>
        <location line="+178"/>
        <source>🔒 文字来源过滤已关闭，将保留所有识别到的文字。</source>
        <translation>🔒 文字來源過濾已關閉，將保留所有辨識到的文字。</translation>
    </message>
    <message>
        <location line="+4"/>
        <source>🔄 文字来源过滤已启用，加载视频后将自动分析...</source>
        <translation>🔄 文字來源過濾已啟用，載入影片後將自動分析...</translation>
    </message>
    <message>
        <location line="+76"/>
        <source>颜色门控：已关闭（默认）。</source>
        <translation>顏色門檻：已關閉（預設）。</translation>
    </message>
    <message>
        <location line="+2"/>
        <source>颜色门控：已勾选，尚未确认。请点击「预览检测效果」并在满意时选择「采用」。</source>
        <translation>顏色門檻：已勾選，尚未確認。請點擊「預覽偵測效果」並在滿意時選擇「採用」。</translation>
    </message>
    <message>
        <location line="+4"/>
        <source>颜色门控：已确认，将用于下一轮「字幕 OCR 识别」阶段一截取。</source>
        <translation>顏色門檻：已確認，將用於下一輪「字幕 OCR 辨識」階段一擷取。</translation>
    </message>
    <message>
        <location line="+222"/>
        <source>自动(Auto)</source>
        <translation>自動(Auto)</translation>
    </message>
    <message>
        <location line="+7"/>
        <source>（无可用引擎）</source>
        <translation>（無可用引擎）</translation>
    </message>
    <message>
        <location line="+3"/>
        <source>⚠ 未检测到可用 OCR 引擎</source>
        <translation>⚠ 未偵測到可用 OCR 引擎</translation>
    </message>
    <message>
        <location line="+7"/>
        <source>✅ 已就绪</source>
        <translation>✅ 已就緒</translation>
    </message>
    <message>
        <location line="+89"/>
        <source>🔄 正在分析视频类型...</source>
        <translation>🔄 正在分析影片類型...</translation>
    </message>
    <message>
        <location line="+98"/>
        <location line="+18"/>
        <source>手动模式（已自定义）</source>
        <translation>手動模式（已自訂）</translation>
    </message>
    <message>
        <location line="+76"/>
        <source>已恢复上次保存的手动设置</source>
        <translation>已回復上次儲存的手動設定</translation>
    </message>
</context>
<context>
    <name>DeepSeekProgressPanel</name>
    <message>
        <location filename="../components/deepseek_progress_panel.py" line="+13"/>
        <source>DeepSeek / 大模型处理进度</source>
        <translation>DeepSeek / 大型模型處理進度</translation>
    </message>
    <message>
        <location line="+12"/>
        <source>启用 DeepSeek 后，此处显示处理进度，以及各批次润色前后的字幕对比、碎片合并候选与结果、策略复核说明等，便于核对模型具体改动了哪些字。</source>
        <translation>啟用 DeepSeek 後，此處顯示處理進度，以及各批次潤飾前後的字幕對比、碎片合併候選與結果、策略複核說明等，便於核對模型具體改動了哪些字。</translation>
    </message>
    <message>
        <location line="+9"/>
        <source>清空日志</source>
        <translation>清空日誌</translation>
    </message>
</context>
<context>
    <name>FileOperationsWidget</name>
    <message>
        <location filename="../components/file_operations.py" line="+25"/>
        <source>文件操作（支持拖放）</source>
        <translation>檔案操作（支援拖放）</translation>
    </message>
    <message>
        <location line="+6"/>
        <source>加载视频</source>
        <translation>載入影片</translation>
    </message>
    <message>
        <location line="+1"/>
        <source>保存 ROI 配置</source>
        <translation>儲存 ROI 設定</translation>
    </message>
    <message>
        <location line="+1"/>
        <source>加载 ROI 配置</source>
        <translation>載入 ROI 設定</translation>
    </message>
    <message>
        <location line="+1"/>
        <source>自动检测字幕ROI</source>
        <translation>自動偵測字幕ROI</translation>
    </message>
    <message>
        <location line="+1"/>
        <source>深度扫描（水印/场景字）…</source>
        <translation>深度掃描（浮水印／場景字）…</translation>
    </message>
    <message>
        <location line="+21"/>
        <source>语言</source>
        <translation>語言</translation>
    </message>
    <message>
        <location line="+3"/>
        <source>自动（跟随系统）</source>
        <translation>自動（跟隨系統）</translation>
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
        <translation>切換語言</translation>
    </message>
    <message>
        <location line="-19"/>
        <source>语言设置将在重启应用后生效。要立即重启吗？</source>
        <translation>語言設定將在重新啟動應用後生效。要立即重新啟動嗎？</translation>
    </message>
    <message>
        <location line="+20"/>
        <source>无法自动重启，请手动重启应用以应用新语言。</source>
        <translation>無法自動重新啟動，請手動重新啟動應用以套用新語言。</translation>
    </message>
</context>
<context>
    <name>LogViewerWidget</name>
    <message>
        <location filename="../components/log_viewer.py" line="+10"/>
        <source>日志与进度</source>
        <translation>日誌與進度</translation>
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
        <location filename="../core/subtitle_generator/event_merge.py" line="+192"/>
        <source>Merged {} fragmented ASS lines into {} dialogue events.</source>
        <translation type="unfinished"></translation>
    </message>
    <message>
        <location filename="../core/subtitle_generator/generator.py" line="+109"/>
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
        <location line="+10"/>
        <source>--- Starting conversion from in-memory data to ASS subtitles ---</source>
        <translation type="unfinished"></translation>
    </message>
    <message>
        <location line="+12"/>
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
        <source>ROI: {} generated {} subtitle groups.</source>
        <translation type="unfinished"></translation>
    </message>
    <message>
        <location line="+39"/>
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
        <location line="+22"/>
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
        <location line="+9"/>
        <source>No valid subtitle groups formed for any ROI, an empty ASS file will be generated.</source>
        <translation type="unfinished"></translation>
    </message>
    <message>
        <location line="+17"/>
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
        <location filename="../core/subtitle_generator/llm_merge.py" line="+112"/>
        <source>Step 4/4: DeepSeek merging fragmented Scene subtitles (call {})...</source>
        <translation type="unfinished"></translation>
    </message>
    <message>
        <location line="+59"/>
        <source>DeepSeek merged fragmented events: {} -&gt; {}.</source>
        <translation type="unfinished"></translation>
    </message>
    <message>
        <location filename="../core/subtitle_generator/roi_filters.py" line="+150"/>
        <source>Watermark filter removed {} text line(s).</source>
        <translation type="unfinished"></translation>
    </message>
    <message>
        <location filename="../core/subtitle_generator/source_classification.py" line="+109"/>
        <source>Text source classification: {} OVERLAY, {} SCENE, {} UNKNOWN. Filtered {} -&gt; {} text lines (keep_overlay={}, keep_scene={}, keep_unknown={}).</source>
        <translation type="unfinished"></translation>
    </message>
    <message>
        <location line="+12"/>
        <source>Text source filter skipped keep-all ROIs: {}.</source>
        <translation type="unfinished"></translation>
    </message>
    <message>
        <location filename="../core/subtitle_generator/styling.py" line="+121"/>
        <source>No &apos;[Events]&apos; tag found in template file. Events will be appended at the end of the file.</source>
        <translation type="unfinished"></translation>
    </message>
    <message>
        <location line="+3"/>
        <source>Failed to read template file {}: {}. Using default styles.</source>
        <translation type="unfinished"></translation>
    </message>
</context>
<context>
    <name>RoiDefinitionWidget</name>
    <message>
        <location filename="../components/roi_definition.py" line="+31"/>
        <source>ROI 定义</source>
        <translation>ROI 定義</translation>
    </message>
    <message>
        <location line="+16"/>
        <location line="+23"/>
        <source>后退 1 帧（短按）/ 连续（长按）</source>
        <translation>後退 1 影格（短按）／連續（長按）</translation>
    </message>
    <message>
        <location line="-18"/>
        <location line="+23"/>
        <source>前进 1 帧（短按）/ 连续（长按）</source>
        <translation>前進 1 影格（短按）／連續（長按）</translation>
    </message>
    <message>
        <location line="-20"/>
        <location line="+23"/>
        <source>输入时间（时:分:秒.毫秒）或帧号</source>
        <translation>輸入時間（時:分:秒.毫秒）或影格號</translation>
    </message>
    <message>
        <location line="-19"/>
        <source>开始时间：</source>
        <translation>開始時間：</translation>
    </message>
    <message>
        <location line="+2"/>
        <source>设为开始时间</source>
        <translation>設為開始時間</translation>
    </message>
    <message>
        <location line="+21"/>
        <source>结束时间：</source>
        <translation>結束時間：</translation>
    </message>
    <message>
        <location line="+2"/>
        <source>设为结束时间</source>
        <translation>設為結束時間</translation>
    </message>
    <message>
        <location line="+5"/>
        <source>按文字/描边/阴影颜色限制 OCR（不匹配像素将被遮罩）</source>
        <translation>依文字／描邊／陰影顏色限制 OCR（不相符的像素會被遮罩）</translation>
    </message>
    <message>
        <location line="+3"/>
        <source>将与所选颜色不接近的像素在 OCR 前置为白色，减少字幕笔画之外的误检。</source>
        <translation>將與所選顏色不接近的像素在 OCR 前設為白色，減少字幕筆畫之外的誤檢。</translation>
    </message>
    <message>
        <location line="+7"/>
        <source>OCR 前对 ROI 轻微模糊（降低锯齿/噪声）</source>
        <translation>OCR 前對 ROI 輕微模糊（降低鋸齒／雜訊）</translation>
    </message>
    <message>
        <location line="+2"/>
        <source>在 OCR 前对 ROI 裁剪图应用轻微高斯模糊。对噪声大/压缩重的字幕更有帮助。</source>
        <translation>在 OCR 前對 ROI 裁剪圖套用輕微高斯模糊。對雜訊大／壓縮重的字幕更有幫助。</translation>
    </message>
    <message>
        <location line="+8"/>
        <source>淡入/淡出微调（在检测到文字边界附近逐帧 OCR，找更精确的起止时间）</source>
        <translation>淡入／淡出微調（在偵測到文字邊界附近逐影格 OCR，找更精確的起止時間）</translation>
    </message>
    <message>
        <location line="+7"/>
        <source>开启后，会只在文字出现/消失的边界附近逐帧 OCR 用于校准时间；ROI 其余部分仍可使用跳帧优化。</source>
        <translation>開啟後，會只在文字出現／消失的邊界附近逐影格 OCR 用於校準時間；ROI 其餘部分仍可使用跳影格最佳化。</translation>
    </message>
    <message>
        <location line="+8"/>
        <source>写入画面位置标签（\pos \frz \frx \fry，多边形自动计算位置与倾角）</source>
        <translation>寫入畫面位置標籤（\pos \frz \frx \fry，多邊形自動計算位置與傾角）</translation>
    </message>
    <message>
        <location line="+7"/>
        <source>开启后，该 ROI 识别出的字幕事件将附带位置与旋转标签：多边形 ROI 自动计算中心点(\pos)与长边倾角(\frz)；\frx/\fry 保存为 0，可手动微调透视。适合实拍场景文字的原位重铺。</source>
        <translation>開啟後，該 ROI 辨識出的字幕事件將附帶位置與旋轉標籤：多邊形 ROI 自動計算中心點(\pos)與長邊傾角(\frz)；\frx/\fry 儲存為 0，可手動微調透視。適合實拍場景文字的原位重鋪。</translation>
    </message>
    <message>
        <location line="+17"/>
        <source>文字颜色…</source>
        <translation>文字顏色…</translation>
    </message>
    <message>
        <location line="+1"/>
        <source>描边颜色…</source>
        <translation>描邊顏色…</translation>
    </message>
    <message>
        <location line="+1"/>
        <source>阴影颜色…</source>
        <translation>陰影顏色…</translation>
    </message>
    <message>
        <location line="+11"/>
        <source>RGB 容差：</source>
        <translation>RGB 容差：</translation>
    </message>
    <message>
        <location line="+5"/>
        <source>单通道距离 0–255；数值越大，包含越多相近色阶（抗锯齿/渐变更稳）。</source>
        <translation>單通道距離 0–255；數值越大，包含越多相近色階（抗鋸齒／漸層更穩）。</translation>
    </message>
    <message>
        <location line="+3"/>
        <source>形态学：</source>
        <translation>形態學：</translation>
    </message>
    <message>
        <location line="+5"/>
        <source>闭运算核大小（奇数）；可在遮罩后连接断裂笔画。</source>
        <translation>閉運算核大小（奇數）；可在遮罩後連接斷裂筆畫。</translation>
    </message>
    <message>
        <location line="+8"/>
        <source>添加新 ROI</source>
        <translation>新增 ROI</translation>
    </message>
    <message>
        <location line="+1"/>
        <source>更新选中 ROI</source>
        <translation>更新所選 ROI</translation>
    </message>
    <message>
        <location line="+1"/>
        <source>删除选中 ROI</source>
        <translation>刪除所選 ROI</translation>
    </message>
    <message>
        <location line="+70"/>
        <source>字幕文字颜色</source>
        <translation>字幕文字顏色</translation>
    </message>
    <message>
        <location line="+10"/>
        <source>字幕描边颜色</source>
        <translation>字幕描邊顏色</translation>
    </message>
    <message>
        <location line="+10"/>
        <source>字幕阴影颜色</source>
        <translation>字幕陰影顏色</translation>
    </message>
</context>
<context>
    <name>RoiListWidget</name>
    <message>
        <location filename="../components/roi_list.py" line="+18"/>
        <source>ROI 列表</source>
        <translation>ROI 清單</translation>
    </message>
    <message>
        <location line="+33"/>
        <source>ROI {}：帧[{}-{}] 时间[{} - {}]</source>
        <translation>ROI {}：影格[{}-{}] 時間[{} - {}]</translation>
    </message>
    <message>
        <location line="+3"/>
        <source>[颜色掩膜]</source>
        <translation>[顏色遮罩]</translation>
    </message>
    <message>
        <location line="+2"/>
        <source>[模糊]</source>
        <translation>[模糊]</translation>
    </message>
    <message>
        <location line="+3"/>
        <source>[淡入淡出微调]</source>
        <translation>[淡入淡出微調]</translation>
    </message>
    <message>
        <location line="+2"/>
        <source>[自动过滤]</source>
        <translation>[自動過濾]</translation>
    </message>
    <message>
        <location line="+22"/>
        <source>复制</source>
        <translation>複製</translation>
    </message>
    <message>
        <location line="+5"/>
        <source>粘贴到此项之后</source>
        <translation>貼上至此項目之後</translation>
    </message>
    <message>
        <location line="+4"/>
        <source>删除</source>
        <translation>刪除</translation>
    </message>
    <message>
        <location line="+5"/>
        <source>切换文字过滤策略（当前：{}）</source>
        <translation>切換文字過濾策略（目前：{}）</translation>
    </message>
    <message>
        <location line="+3"/>
        <source>自动过滤</source>
        <translation>自動過濾</translation>
    </message>
    <message>
        <location line="+2"/>
        <source>全部保留</source>
        <translation>全部保留</translation>
    </message>
    <message>
        <location line="+10"/>
        <source>粘贴到末尾</source>
        <translation>貼上至結尾</translation>
    </message>
    <message>
        <location line="+12"/>
        <source>确认删除</source>
        <translation>確認刪除</translation>
    </message>
    <message>
        <location line="+1"/>
        <source>确定要删除 ROI {} 吗？</source>
        <translation>確定要刪除 ROI {} 嗎？</translation>
    </message>
</context>
<context>
    <name>ScanReviewDialog</name>
    <message>
        <location filename="../components/scan_review_dialog.py" line="+61"/>
        <source>深度扫描复核</source>
        <translation>深度掃描複核</translation>
    </message>
    <message>
        <location line="+23"/>
        <source>字幕带 ROI（自动检测的主字幕区）</source>
        <translation>字幕帶 ROI（自動偵測的主字幕區）</translation>
    </message>
    <message>
        <location line="+6"/>
        <source>底部</source>
        <translation>底部</translation>
    </message>
    <message>
        <location line="+1"/>
        <source>顶部</source>
        <translation>頂部</translation>
    </message>
    <message>
        <location line="+0"/>
        <source>中部</source>
        <translation>中部</translation>
    </message>
    <message>
        <location line="+3"/>
        <source>{0}  帧 [{1}-{2}]</source>
        <translation>{0}  影格 [{1}-{2}]</translation>
    </message>
    <message>
        <location line="+7"/>
        <source>用所选字幕带替换现有 ROI 列表（否则追加）</source>
        <translation>用所選字幕帶取代現有 ROI 清單（否則附加）</translation>
    </message>
    <message>
        <location line="+4"/>
        <source>未检测到字幕带。</source>
        <translation>未偵測到字幕帶。</translation>
    </message>
    <message>
        <location line="+4"/>
        <source>疑似水印（恒定文本 + 恒定位置，勾选=识别时剔除）</source>
        <translation>疑似浮水印（固定文字＋固定位置，勾選＝辨識時剔除）</translation>
    </message>
    <message>
        <location line="+6"/>
        <source>「{0}」 出现率 {1:.0%}</source>
        <translation>「{0}」 出現率 {1:.0%}</translation>
    </message>
    <message>
        <location line="+10"/>
        <source>未检测到疑似水印。</source>
        <translation>未偵測到疑似浮水印。</translation>
    </message>
    <message>
        <location line="+4"/>
        <source>画面中部文字（场景字候选，勾选=导入为 ROI，全部保留）</source>
        <translation>畫面中部文字（場景字候選，勾選＝匯入為 ROI，全部保留）</translation>
    </message>
    <message>
        <location line="+5"/>
        <source>「{0}」 出现 {1} 次（帧 {2}-{3}）</source>
        <translation>「{0}」 出現 {1} 次（影格 {2}-{3}）</translation>
    </message>
    <message>
        <location line="+14"/>
        <source>（其余 {} 处低频文字未列出）</source>
        <translation>（其餘 {} 處低頻文字未列出）</translation>
    </message>
    <message>
        <location line="+5"/>
        <source>未检测到画面中部文字。</source>
        <translation>未偵測到畫面中部文字。</translation>
    </message>
    <message>
        <location line="+12"/>
        <source>应用</source>
        <translation>套用</translation>
    </message>
    <message>
        <location line="+1"/>
        <source>取消</source>
        <translation type="unfinished">取消</translation>
    </message>
</context>
<context>
    <name>SubtitleOCRGUI</name>
    <message>
        <location filename="../main_window/auto_detection.py" line="+43"/>
        <source>已恢复上次保存的文字来源过滤设置。</source>
        <translation>已回復上次儲存的文字來源過濾設定。</translation>
    </message>
    <message>
        <location line="+35"/>
        <source>视频类型自动检测完成：{} (置信度 {:.0%})</source>
        <translation>影片類型自動偵測完成：{} (信心度 {:.0%})</translation>
    </message>
    <message>
        <location line="+9"/>
        <source>视频类型自动检测失败：{}</source>
        <translation>影片類型自動偵測失敗：{}</translation>
    </message>
    <message>
        <location line="+7"/>
        <source>自动分析失败，请手动选择场景类型</source>
        <translation>自動分析失敗，請手動選擇場景類型</translation>
    </message>
    <message>
        <location filename="../main_window/pipeline_control.py" line="+31"/>
        <location line="+6"/>
        <location line="+10"/>
        <location line="+19"/>
        <location filename="../main_window/roi_config_io.py" line="+84"/>
        <location filename="../main_window/roi_editing.py" line="+150"/>
        <location filename="../main_window/source_config.py" line="+17"/>
        <location line="+12"/>
        <source>警告</source>
        <translation>警告</translation>
    </message>
    <message>
        <location line="-34"/>
        <location filename="../main_window/source_config.py" line="-11"/>
        <source>请先加载视频并至少定义一个 ROI。</source>
        <translation>請先載入影片並至少定義一個 ROI。</translation>
    </message>
    <message>
        <location line="+6"/>
        <source>已有 OCR 任务在运行中，请等待其完成或先取消。</source>
        <translation>已有 OCR 工作在執行中，請等待其完成或先取消。</translation>
    </message>
    <message>
        <location line="+10"/>
        <source>已勾选「按字幕颜色跳过疑似无字帧」，但尚未通过预览确认。请点击「预览检测效果」并在满意时选择「采用」，或取消勾选以使用默认流程。</source>
        <translation>已勾選「依字幕顏色跳過疑似無字影格」，但尚未透過預覽確認。請點擊「預覽偵測效果」並在滿意時選擇「採用」，或取消勾選以使用預設流程。</translation>
    </message>
    <message>
        <location line="+19"/>
        <source>已启用 DeepSeek 功能（润色/碎片合并/策略复核），但未填写 API Key。请填写 API Key，或设置环境变量 DEEPSEEK_API_KEY。</source>
        <translation>已啟用 DeepSeek 功能（潤飾／碎片合併／策略複核），但未填寫 API Key。請填寫 API Key，或設定環境變數 DEEPSEEK_API_KEY。</translation>
    </message>
    <message>
        <location line="+24"/>
        <source>保存字幕文件</source>
        <translation>儲存字幕檔案</translation>
    </message>
    <message>
        <location line="+2"/>
        <source>ASS 字幕 (*.ass)</source>
        <translation>ASS 字幕 (*.ass)</translation>
    </message>
    <message>
        <location line="+3"/>
        <source>用户取消保存，OCR 任务已中止。</source>
        <translation>使用者取消儲存，OCR 工作已中止。</translation>
    </message>
    <message>
        <location line="+10"/>
        <source>[LLM] 已启用 DeepSeek —— 第 4 步的大模型进度会显示在下方。</source>
        <translation>[LLM] 已啟用 DeepSeek —— 第 4 步的大型模型進度會顯示在下方。</translation>
    </message>
    <message>
        <location line="+6"/>
        <source>字幕 OCR + DeepSeek</source>
        <translation>字幕 OCR + DeepSeek</translation>
    </message>
    <message>
        <location line="+2"/>
        <source>字幕 OCR</source>
        <translation>字幕 OCR</translation>
    </message>
    <message>
        <location line="+2"/>
        <source>正在处理视频...</source>
        <translation>正在處理影片...</translation>
    </message>
    <message>
        <location line="+1"/>
        <source>取消</source>
        <translation>取消</translation>
    </message>
    <message>
        <location line="+49"/>
        <source>正在识别…</source>
        <translation>正在辨識…</translation>
    </message>
    <message>
        <location line="+51"/>
        <source>[LLM] 完成 —— 已写入 ASS 文件。</source>
        <translation>[LLM] 完成 —— 已寫入 ASS 檔案。</translation>
    </message>
    <message>
        <location line="+5"/>
        <source>完成</source>
        <translation>完成</translation>
    </message>
    <message>
        <location line="+1"/>
        <source>字幕文件已生成：{}</source>
        <translation>字幕檔案已產生：{}</translation>
    </message>
    <message>
        <location line="+2"/>
        <source>打开目录</source>
        <translation>開啟資料夾</translation>
    </message>
    <message>
        <location line="+1"/>
        <source>是否打开包含该文件的文件夹？</source>
        <translation>是否開啟包含該檔案的資料夾？</translation>
    </message>
    <message>
        <location line="+11"/>
        <source>[LLM] 已中止或出错 —— 请查看弹窗提示。</source>
        <translation>[LLM] 已中止或發生錯誤 —— 請查看彈出視窗提示。</translation>
    </message>
    <message>
        <location line="+4"/>
        <location filename="../main_window/roi_config_io.py" line="-52"/>
        <location line="+85"/>
        <location filename="../main_window/roi_editing.py" line="+53"/>
        <location filename="../main_window/scan_control.py" line="+322"/>
        <location filename="../main_window/video_playback.py" line="+28"/>
        <source>错误</source>
        <translation>錯誤</translation>
    </message>
    <message>
        <location line="+1"/>
        <source>处理过程中发生错误：
{}</source>
        <translation>處理過程中發生錯誤：
{}</translation>
    </message>
    <message>
        <location filename="../main_window/roi_config_io.py" line="-99"/>
        <source>保存 ROI 配置</source>
        <translation>儲存 ROI 設定</translation>
    </message>
    <message>
        <location line="+2"/>
        <location line="+72"/>
        <source>JSON 文件 (*.json)</source>
        <translation>JSON 檔案 (*.json)</translation>
    </message>
    <message>
        <location line="-63"/>
        <source>ROI 配置已保存到：{}</source>
        <translation>ROI 設定已儲存至：{}</translation>
    </message>
    <message>
        <location line="+4"/>
        <location line="+1"/>
        <source>保存 ROI 配置失败：{}</source>
        <translation>儲存 ROI 設定失敗：{}</translation>
    </message>
    <message>
        <location line="+33"/>
        <source>自动备份 ROI 配置失败（识别仍会继续）：{}</source>
        <translation>自動備份 ROI 設定失敗（辨識仍會繼續）：{}</translation>
    </message>
    <message>
        <location line="+7"/>
        <source>为防意外中断，已自动备份 ROI 配置到：{}</source>
        <translation>為防意外中斷，已自動備份 ROI 設定至：{}</translation>
    </message>
    <message>
        <location line="+11"/>
        <location filename="../main_window/scan_control.py" line="-259"/>
        <location line="+36"/>
        <location line="+46"/>
        <source>请先加载视频文件。</source>
        <translation>請先載入影片檔案。</translation>
    </message>
    <message>
        <location line="+5"/>
        <source>加载 ROI 配置</source>
        <translation>載入 ROI 設定</translation>
    </message>
    <message>
        <location line="+24"/>
        <source>ROI 配置已从 {} 加载</source>
        <translation>ROI 設定已從 {} 載入</translation>
    </message>
    <message>
        <location line="+4"/>
        <location line="+1"/>
        <source>加载 ROI 配置失败：{}</source>
        <translation>載入 ROI 設定失敗：{}</translation>
    </message>
    <message>
        <location line="+4"/>
        <source>选择 ASS 模板文件</source>
        <translation>選擇 ASS 範本檔案</translation>
    </message>
    <message>
        <location line="+2"/>
        <source>ASS 字幕文件 (*.ass)</source>
        <translation>ASS 字幕檔案 (*.ass)</translation>
    </message>
    <message>
        <location filename="../main_window/roi_editing.py" line="-163"/>
        <source>已添加新 ROI，帧范围：{}-{}</source>
        <translation>已新增 ROI，影格範圍：{}-{}</translation>
    </message>
    <message>
        <location line="+15"/>
        <source>已更新 ROI {}，新帧范围：{}-{}</source>
        <translation>已更新 ROI {}，新影格範圍：{}-{}</translation>
    </message>
    <message>
        <location line="+20"/>
        <source>已删除 ROI {}</source>
        <translation>已刪除 ROI {}</translation>
    </message>
    <message>
        <location line="+13"/>
        <source>ROI {} 文字过滤策略已切换为：{}</source>
        <translation>ROI {} 文字過濾策略已切換為：{}</translation>
    </message>
    <message>
        <location line="+4"/>
        <source>按场景预设过滤</source>
        <translation>依場景預設過濾</translation>
    </message>
    <message>
        <location line="+2"/>
        <source>全部保留（不过滤）</source>
        <translation>全部保留（不過濾）</translation>
    </message>
    <message>
        <location line="+57"/>
        <source>开始时间不能晚于结束时间。</source>
        <translation>開始時間不能晚於結束時間。</translation>
    </message>
    <message>
        <location line="+50"/>
        <source>创建 ROI 条目时出错：{}</source>
        <translation>建立 ROI 項目時發生錯誤：{}</translation>
    </message>
    <message>
        <location line="+3"/>
        <source>创建 ROI 时发生错误：{}</source>
        <translation>建立 ROI 時發生錯誤：{}</translation>
    </message>
    <message>
        <location line="+96"/>
        <source>已在画面上调整 ROI {} 的区域</source>
        <translation>已在畫面上調整 ROI {} 的區域</translation>
    </message>
    <message>
        <location line="+8"/>
        <source>已将 ROI {} 复制到剪贴板。</source>
        <translation>已將 ROI {} 複製到剪貼簿。</translation>
    </message>
    <message>
        <location line="+10"/>
        <source>已粘贴到 ROI {} 之后。</source>
        <translation>已貼上到 ROI {} 之後。</translation>
    </message>
    <message>
        <location line="+10"/>
        <source>已将 ROI 粘贴到列表末尾。</source>
        <translation>已將 ROI 貼上到清單結尾。</translation>
    </message>
    <message>
        <location filename="../main_window/scan_control.py" line="-83"/>
        <location line="+36"/>
        <location line="+46"/>
        <source>未加载视频</source>
        <translation>未載入影片</translation>
    </message>
    <message>
        <location line="-55"/>
        <source>正在后台扫描字幕分布（采样 {} 帧）…</source>
        <translation>正在背景掃描字幕分布（取樣 {} 影格）…</translation>
    </message>
    <message>
        <location line="+20"/>
        <location line="+146"/>
        <location line="+52"/>
        <source>自动检测字幕ROI</source>
        <translation>自動偵測字幕ROI</translation>
    </message>
    <message>
        <location line="-197"/>
        <source>请选择自动检测到的字幕区域的处理方式：

「是」：追加到现有 ROI 列表末尾（保留现有 ROI）；
「否」：替换全部现有 ROI（现有 ROI 将被清除，不可恢复）；
「取消」：中止本次检测。</source>
        <translation>請選擇自動偵測到的字幕區域的處理方式：

「是」：附加到現有 ROI 清單結尾（保留現有 ROI）；
「否」：取代全部現有 ROI（現有 ROI 將被清除，無法復原）；
「取消」：中止本次偵測。</translation>
    </message>
    <message>
        <location line="+16"/>
        <source>替换现有 ROI？</source>
        <translation>取代現有 ROI？</translation>
    </message>
    <message>
        <location line="+1"/>
        <source>即将清除现有 {} 个 ROI 并用检测结果替换，此操作不可恢复。是否继续？</source>
        <translation>即將清除現有 {} 個 ROI 並用偵測結果取代，此操作無法復原。是否繼續？</translation>
    </message>
    <message>
        <location line="+47"/>
        <source>正在深度扫描（采样 {} 帧，含水印/场景字统计）…</source>
        <translation>正在深度掃描（取樣 {} 影格，含浮水印／場景字統計）…</translation>
    </message>
    <message>
        <location line="+12"/>
        <source>深度扫描</source>
        <translation>深度掃描</translation>
    </message>
    <message>
        <location line="+1"/>
        <source>扫描完成：未检测到字幕带、水印或场景文字。</source>
        <translation>掃描完成：未偵測到字幕帶、浮水印或場景文字。</translation>
    </message>
    <message>
        <location line="+23"/>
        <source>将在识别时剔除 {} 处水印文本。</source>
        <translation>將在辨識時剔除 {} 處浮水印文字。</translation>
    </message>
    <message>
        <location line="+6"/>
        <source>未启用水印剔除。</source>
        <translation>未啟用浮水印剔除。</translation>
    </message>
    <message>
        <location line="+3"/>
        <source>已导入 {} 个字幕带 ROI、{} 个场景字 ROI。</source>
        <translation>已匯入 {} 個字幕帶 ROI、{} 個場景字 ROI。</translation>
    </message>
    <message>
        <location line="+13"/>
        <source>字幕扫描进度：{}/{}（采样帧）</source>
        <translation>字幕掃描進度：{}/{}（取樣影格）</translation>
    </message>
    <message>
        <location line="+24"/>
        <source>未检测到字幕区域。请确认视频中确实存在字幕，或调整识别语言后重试。</source>
        <translation>未偵測到字幕區域。請確認影片中確實存在字幕，或調整辨識語言後重試。</translation>
    </message>
    <message>
        <location line="+7"/>
        <source>后台扫描未检测到字幕带，可手动绘制 ROI。</source>
        <translation>背景掃描未偵測到字幕帶，可手動繪製 ROI。</translation>
    </message>
    <message>
        <location line="+7"/>
        <source>已用 {} 个自动检测的 ROI 替换全部现有 ROI。</source>
        <translation>已用 {} 個自動偵測的 ROI 取代全部現有 ROI。</translation>
    </message>
    <message>
        <location line="+7"/>
        <source>已追加 {} 个自动检测的 ROI 到列表末尾。</source>
        <translation>已附加 {} 個自動偵測的 ROI 到清單結尾。</translation>
    </message>
    <message>
        <location line="+11"/>
        <source>检测到 {} 处疑似水印（恒定文本/位置）。可运行深度扫描复核后自动剔除。</source>
        <translation>偵測到 {} 處疑似浮水印（固定文字／位置）。可執行深度掃描複核後自動剔除。</translation>
    </message>
    <message>
        <location line="+7"/>
        <source>检测到 {} 处画面中部文字（场景字候选）。可在深度扫描复核中导入为 ROI。</source>
        <translation>偵測到 {} 處畫面中部文字（場景字候選）。可在深度掃描複核中匯入為 ROI。</translation>
    </message>
    <message>
        <location line="+13"/>
        <source>自动检测完成：检测到 {} 个字幕区域，时间范围 {} ～ {}</source>
        <translation>自動偵測完成：偵測到 {} 個字幕區域，時間範圍 {} ～ {}</translation>
    </message>
    <message>
        <location line="+9"/>
        <location line="+6"/>
        <source>自动检测失败：{}</source>
        <translation>自動偵測失敗：{}</translation>
    </message>
    <message>
        <location filename="../main_window/source_config.py" line="+12"/>
        <source>当前帧不在任一 ROI 的时间范围内。请将时间轴移到含字幕的典型帧上，用于自动标定颜色。</source>
        <translation>目前影格不在任一 ROI 的時間範圍內。請將時間軸移到含字幕的典型影格上，用於自動標定顏色。</translation>
    </message>
    <message>
        <location line="+21"/>
        <source>颜色门控预览失败：{}</source>
        <translation>顏色門檻預覽失敗：{}</translation>
    </message>
    <message>
        <location line="+5"/>
        <source>预览失败</source>
        <translation>預覽失敗</translation>
    </message>
    <message>
        <location line="+9"/>
        <source>颜色门控已确认；下次运行「字幕 OCR 识别」时将在阶段一启用。</source>
        <translation>顏色門檻已確認；下次執行「字幕 OCR 辨識」時將在階段一啟用。</translation>
    </message>
    <message>
        <location filename="../main_window/video_playback.py" line="-9"/>
        <source>选择视频文件</source>
        <translation>選擇影片檔案</translation>
    </message>
    <message>
        <location line="+2"/>
        <source>视频文件 (*.mp4 *.avi *.mov *.mkv)</source>
        <translation>影片檔案 (*.mp4 *.avi *.mov *.mkv)</translation>
    </message>
    <message>
        <location line="+8"/>
        <source>无法打开视频文件</source>
        <translation>無法開啟影片檔案</translation>
    </message>
    <message>
        <location line="+27"/>
        <source>视频字幕 OCR 工具 - {}</source>
        <translation>影片字幕 OCR 工具 - {}</translation>
    </message>
    <message>
        <location line="+1"/>
        <source>视频已加载：{}</source>
        <translation>影片已載入：{}</translation>
    </message>
    <message>
        <location line="+1"/>
        <source>分辨率：{}x{}，帧率：{:.2f}，总帧数：{}</source>
        <translation>解析度：{}x{}，影格率：{:.2f}，總影格數：{}</translation>
    </message>
    <message>
        <location line="+22"/>
        <source>已自动加载 ROI 配置：{}</source>
        <translation>已自動載入 ROI 設定：{}</translation>
    </message>
    <message>
        <location line="+4"/>
        <source>自动加载 ROI 配置失败：{}</source>
        <translation>自動載入 ROI 設定失敗：{}</translation>
    </message>
    <message>
        <location line="+92"/>
        <source>无效的时间/帧号输入：&apos;{}&apos;</source>
        <translation>無效的時間／影格號輸入：&apos;{}&apos;</translation>
    </message>
    <message>
        <location filename="../main_window/window.py" line="+95"/>
        <source>视频字幕 OCR 工具</source>
        <translation>影片字幕 OCR 工具</translation>
    </message>
    <message>
        <location line="+106"/>
        <source>正在深度扫描…</source>
        <translation>正在深度掃描…</translation>
    </message>
    <message>
        <location line="+2"/>
        <source>正在检测字幕区域…</source>
        <translation>正在偵測字幕區域…</translation>
    </message>
    <message>
        <location line="+57"/>
        <source>已通过拖放加载 ASS 模板：{}</source>
        <translation>已透過拖放載入 ASS 範本：{}</translation>
    </message>
    <message>
        <location line="+26"/>
        <source>确认退出</source>
        <translation>確認結束</translation>
    </message>
    <message>
        <location line="+1"/>
        <source>后台任务仍在运行，确定要退出吗？</source>
        <translation>背景工作仍在執行，確定要結束嗎？</translation>
    </message>
</context>
<context>
    <name>VideoFrameLabel</name>
    <message>
        <location filename="../components/video_display.py" line="+431"/>
        <source>绘制错误：{}</source>
        <translation>繪製錯誤：{}</translation>
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
        <location filename="../core/ocr_optimizer.py" line="+117"/>
        <source>Could not get image data for frame {}. Input type: {}</source>
        <translation type="unfinished"></translation>
    </message>
    <message>
        <location line="+262"/>
        <source>Batch OCR prediction failed; falling back to per-frame OCR.</source>
        <translation type="unfinished"></translation>
    </message>
    <message>
        <location line="+36"/>
        <source>Sampled frames disagree on line count; keeping base frame OCR result.</source>
        <translation type="unfinished"></translation>
    </message>
    <message>
        <location line="+115"/>
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
        <location line="+101"/>
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
        <location filename="../core/pipeline_stages.py" line="+95"/>
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
        <location line="+35"/>
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
        <location line="+111"/>
        <location line="+95"/>
        <source>Step 2/4: OCR recognition in progress... ({}/{})</source>
        <translation type="unfinished"></translation>
    </message>
    <message>
        <location line="-255"/>
        <source>Streaming OCR will flush in parallel (cpu_workers={}, max_stream_workers={}).</source>
        <translation type="unfinished"></translation>
    </message>
    <message>
        <location line="+68"/>
        <source>Step 1/4: Extracting ROI frames... ({}/{})</source>
        <translation type="unfinished"></translation>
    </message>
    <message>
        <location line="+30"/>
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
        <location filename="../core/pipeline_worker.py" line="+105"/>
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
        <location line="+22"/>
        <source>Chunk-parallel OCR: {} workers, {} windows.</source>
        <translation type="unfinished"></translation>
    </message>
    <message>
        <location line="+25"/>
        <source>Step 4/4: Starting ASS subtitle file generation...</source>
        <translation type="unfinished"></translation>
    </message>
    <message>
        <location line="+50"/>
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
        <location line="+48"/>
        <source>Color restriction produced an empty mask for ROI; skipping mask for this frame crop.</source>
        <translation type="unfinished"></translation>
    </message>
    <message>
        <location line="+145"/>
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
        <location line="+89"/>
        <location line="+117"/>
        <source>Extraction cannot start: video path or ROI data not provided.</source>
        <translation type="unfinished"></translation>
    </message>
    <message>
        <location line="-114"/>
        <location line="+118"/>
        <source>In save_to_disk mode, a valid working directory `work_dir` must be provided.</source>
        <translation type="unfinished"></translation>
    </message>
    <message>
        <location line="-115"/>
        <location line="+122"/>
        <source>Extraction aborted: invalid total_frames.</source>
        <translation type="unfinished"></translation>
    </message>
    <message>
        <location line="-95"/>
        <location line="+118"/>
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
        <location line="+20"/>
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
