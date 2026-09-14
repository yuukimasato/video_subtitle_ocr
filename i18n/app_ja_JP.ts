<?xml version="1.0" encoding="utf-8"?>
<!DOCTYPE TS>
<TS version="2.1" language="ja_JP">
<context>
    <name>ColorGatePreviewDialog</name>
    <message>
        <location filename="../components/color_gate_preview_dialog.py" line="+43"/>
        <source>颜色门控：预览</source>
        <translation type="unfinished"></translation>
    </message>
    <message>
        <location line="+18"/>
        <source>以下缩略来自 ROI 区间内均匀采样。请确认「判定为保留」与您期望一致。若不满意，请选择「取消」或直接关闭对话框，并保持主界面选项关闭或未确认。</source>
        <translation type="unfinished"></translation>
    </message>
    <message>
        <location line="+10"/>
        <source>阈值与预估</source>
        <translation type="unfinished"></translation>
    </message>
    <message>
        <location line="+8"/>
        <source>命中面积占比下限：</source>
        <translation type="unfinished"></translation>
    </message>
    <message>
        <location line="+21"/>
        <source>采用（用于本次识别）</source>
        <translation type="unfinished"></translation>
    </message>
    <message>
        <location line="+3"/>
        <source>不采用</source>
        <translation type="unfinished"></translation>
    </message>
    <message>
        <location line="+25"/>
        <source>采样帧数：{}；在当前阈值下「保留」帧数：{}（约 {:.1f}%）；按此比例粗略估计全流程 ROI 图数量约：{} / {}（原计划）。</source>
        <translation type="unfinished"></translation>
    </message>
    <message>
        <location line="+12"/>
        <source>保留</source>
        <translation type="unfinished"></translation>
    </message>
    <message>
        <location line="+2"/>
        <source>跳过</source>
        <translation type="unfinished"></translation>
    </message>
    <message>
        <location line="+3"/>
        <source>帧 {} · {}</source>
        <translation type="unfinished"></translation>
    </message>
    <message>
        <location line="+3"/>
        <source>max 占比：{ratio:.4f}</source>
        <translation type="unfinished"></translation>
    </message>
</context>
<context>
    <name>ControlPanelWidget</name>
    <message>
        <source>Style Template (Optional)</source>
        <translation type="vanished">スタイルテンプレート（オプション）</translation>
    </message>
    <message>
        <source>Click browse to select .ass template file</source>
        <translation type="vanished">.assテンプレートファイルを選択するために参照をクリックしてください</translation>
    </message>
    <message>
        <source>Browse...</source>
        <translation type="vanished">参照...</translation>
    </message>
    <message>
        <source>Drawing Mode</source>
        <translation type="vanished">描画モード</translation>
    </message>
    <message>
        <source>Rectangle (Drag)</source>
        <translation type="vanished">四角形（ドラッグ）</translation>
    </message>
    <message>
        <source>Polygon (Click)</source>
        <translation type="vanished">多角形（クリック）</translation>
    </message>
    <message>
        <source>Subtitle Generation</source>
        <translation type="vanished">字幕生成</translation>
    </message>
    <message>
        <source>Subtitle OCR Recognition</source>
        <translation type="vanished">字幕OCR認識</translation>
    </message>
    <message>
        <source>Debug Mode</source>
        <translation type="vanished">デバッグモード</translation>
    </message>
    <message>
        <source>Visualize Output</source>
        <translation type="vanished">出力可視化</translation>
    </message>
    <message>
        <source>In-Memory Mode (Experimental)</source>
        <translation type="vanished">インメモリモード（実験的）</translation>
    </message>
    <message>
        <location filename="../components/control_panel.py" line="+119"/>
        <source>识别语言：</source>
        <translation>認識言語：</translation>
    </message>
    <message>
        <location line="+69"/>
        <source>模型档位：</source>
        <translation>モデル精度（Tier）：</translation>
    </message>
    <message>
        <location line="-66"/>
        <source>选择字幕语言。中文/英语/日语使用 PP-OCRv6 模型，其他语言自动回落 PP-OCRv5 多语言模型。</source>
        <translation>字幕の言語を選択します。中国語/英語/日本語は PP-OCRv6 モデルを使用し、その他の言語は自動的に PP-OCRv5 多言語モデルへフォールバックします。</translation>
    </message>
    <message>
        <location line="-22"/>
        <source>样式模板（可选）</source>
        <translation type="unfinished"></translation>
    </message>
    <message>
        <location line="+5"/>
        <source>点击“浏览”选择 .ass 模板文件</source>
        <translation type="unfinished"></translation>
    </message>
    <message>
        <location line="+1"/>
        <source>浏览…</source>
        <translation type="unfinished"></translation>
    </message>
    <message>
        <location line="+74"/>
        <source>引擎选择：</source>
        <translation type="unfinished"></translation>
    </message>
    <message>
        <location line="+3"/>
        <source>检测可用引擎</source>
        <translation type="unfinished"></translation>
    </message>
    <message>
        <location line="+8"/>
        <source>PaddleOCR 模型档位：Tiny 最快，Small 均衡，Medium 最准。“自动”使用 PP-OCRv6 默认模型。</source>
        <translation>PaddleOCR のモデル tiers：Tiny は最速、Small はバランス、Medium は最も正確。「自動」では PP-OCRv6 のデフォルトモデルを使用します。</translation>
    </message>
    <message>
        <location line="+24"/>
        <source>文字来源过滤（上下文语义分析）</source>
        <translation type="unfinished"></translation>
    </message>
    <message>
        <location line="+7"/>
        <source>启用文字来源过滤（实验性）</source>
        <translation type="unfinished"></translation>
    </message>
    <message>
        <location line="+6"/>
        <source>勾选后，OCR 识别结果将经过上下文语义分析，自动区分后期叠加字幕与实拍场景文字。未勾选时保留所有识别到的文字。</source>
        <translation type="unfinished"></translation>
    </message>
    <message>
        <location line="+11"/>
        <source>🔄 加载视频后将自动分析...</source>
        <translation type="unfinished"></translation>
    </message>
    <message>
        <location line="+9"/>
        <source>场景预设：</source>
        <translation type="unfinished"></translation>
    </message>
    <message>
        <location line="+2"/>
        <source>选择适合当前视频的场景类型，自动配置文字来源过滤规则</source>
        <translation type="unfinished"></translation>
    </message>
    <message>
        <location line="+7"/>
        <source>保留后期叠加文字 (OVERLAY)
    字幕、标题、水印、UI 按钮、弹幕、特效文字</source>
        <translation type="unfinished"></translation>
    </message>
    <message>
        <location line="+6"/>
        <source>保留实拍场景文字 (SCENE)
    店铺招牌、路牌、宣传海报、书本、屏幕、标牌</source>
        <translation type="unfinished"></translation>
    </message>
    <message>
        <location line="+6"/>
        <source>保留无法判定文字 (UNKNOWN)
    分类器置信度不足的边界情况</source>
        <translation type="unfinished"></translation>
    </message>
    <message>
        <location line="+12"/>
        <source>💡 备选方案:</source>
        <translation type="unfinished"></translation>
    </message>
    <message>
        <location line="+16"/>
        <source>🔄 重新自动检测</source>
        <translation type="unfinished"></translation>
    </message>
    <message>
        <location line="+1"/>
        <source>重新采样分析当前视频并更新类型判定</source>
        <translation type="unfinished"></translation>
    </message>
    <message>
        <location line="+1"/>
        <source>重置为预设默认值</source>
        <translation type="unfinished"></translation>
    </message>
    <message>
        <location line="+8"/>
        <source>绘制模式</source>
        <translation>描画モード</translation>
    </message>
    <message>
        <location line="+4"/>
        <source>矩形（拖动）</source>
        <translation>矩形（ドラッグ）</translation>
    </message>
    <message>
        <location line="+1"/>
        <source>多边形（点击）</source>
        <translation>多角形（クリック）</translation>
    </message>
    <message>
        <location line="+1"/>
        <source>编辑（拖动调整）</source>
        <translation>編集（ドラッグで調整）</translation>
    </message>
    <message>
        <location line="+2"/>
        <source>在画面上直接调整已有 ROI：拖动整体移动；矩形拖 8 个控制点缩放；多边形拖顶点改形。自动检测的 ROI 可用此模式微调。</source>
        <translation>画面上で既存 ROI を直接調整：ドラッグで全体移動、矩形は 8 つのハンドルで拡大縮小、多角形は頂点をドラッグして変形します。自動検出した ROI の微調整に使用します。</translation>
    </message>
    <message>
        <location line="+13"/>
        <source>字幕生成</source>
        <translation type="unfinished"></translation>
    </message>
    <message>
        <location line="-215"/>
        <source>识别设置</source>
        <translation>認識設定</translation>
    </message>
    <message>
        <location line="+37"/>
        <source>文字保留：</source>
        <translation>文字の保持：</translation>
    </message>
    <message>
        <location line="+3"/>
        <source>控制识别结果保留哪些文字。「保留全部文字」不做任何过滤；更精细的规则可在完整设置中调整。</source>
        <translation>認識結果でどの文字を保持するかを制御します。「すべての文字を保持」はフィルタリングを行いません。より細かいルールは詳細設定で調整できます。</translation>
    </message>
    <message>
        <location line="+8"/>
        <source>保留全部文字</source>
        <translation>すべての文字を保持</translation>
    </message>
    <message>
        <location line="+2"/>
        <source>只保留字幕</source>
        <translation>字幕のみ保持</translation>
    </message>
    <message>
        <location line="+2"/>
        <source>字幕和画面文字</source>
        <translation>字幕と画面内の文字</translation>
    </message>
    <message>
        <location line="+2"/>
        <source>自定义（在完整设置中调整）</source>
        <translation>カスタム（詳細設定で調整）</translation>
    </message>
    <message>
        <location line="+8"/>
        <source>OCR 引擎（自动选择）</source>
        <translation>OCRエンジン（自動選択）</translation>
    </message>
    <message>
        <location line="+157"/>
        <source>开始识别并导出</source>
        <translation>認識して書き出す</translation>
    </message>
    <message>
        <location line="+2"/>
        <source>调整字幕区域</source>
        <translation>字幕領域を調整</translation>
    </message>
    <message>
        <location line="+2"/>
        <source>进入 ROI 编辑状态：在画面上拖动、缩放或微调已检测到的字幕区域。</source>
        <translation>ROI編集状態に入る：画面上で検出済みの字幕領域をドラッグ・拡大縮小・微調整できます。</translation>
    </message>
    <message>
        <location line="+5"/>
        <source>重新检测</source>
        <translation>再検出</translation>
    </message>
    <message>
        <location line="+2"/>
        <source>对当前视频重新执行自动分析（场景类型判定）。</source>
        <translation>現在の動画に対して自動分析（シーンタイプ判定）を再実行します。</translation>
    </message>
    <message>
        <location line="+18"/>
        <source>调试模式</source>
        <translation type="unfinished"></translation>
    </message>
    <message>
        <location line="+1"/>
        <source>可视化输出</source>
        <translation type="unfinished"></translation>
    </message>
    <message>
        <location line="+1"/>
        <source>内存模式（实验性）</source>
        <translation type="unfinished"></translation>
    </message>
    <message>
        <location line="+1"/>
        <source>保存中间 JSON</source>
        <translation type="unfinished"></translation>
    </message>
    <message>
        <location line="+1"/>
        <source>按时间分片并行（可选）</source>
        <translation type="unfinished"></translation>
    </message>
    <message>
        <location line="+1"/>
        <source>合并 ROI（每帧只 OCR 一次）</source>
        <translation type="unfinished"></translation>
    </message>
    <message>
        <location line="+2"/>
        <location line="+3"/>
        <source>秒</source>
        <translation type="unfinished"></translation>
    </message>
    <message>
        <location line="+16"/>
        <source>加载视频后自动检测字幕 ROI</source>
        <translation>動画読み込み後に字幕 ROI を自動検出</translation>
    </message>
    <message>
        <location line="+3"/>
        <source>加载视频后在后台采样扫描全片，自动生成顶部/底部字幕带 ROI（文字过滤策略为「自动过滤」，可随时在 ROI 列表右键切换）。</source>
        <translation>動画読み込み後、バックグラウンドで全編をサンプリングスキャンし、上部/下部の字幕帯 ROI を自動生成します（テキストフィルタポリシーは「自動フィルタ」。ROI リストの右クリックでいつでも切り替え可能）。</translation>
    </message>
    <message>
        <location line="+8"/>
        <source>进程分片（长视频提速）</source>
        <translation>プロセス分割（長動画の高速化）</translation>
    </message>
    <message>
        <location line="+4"/>
        <source>自动</source>
        <translation type="unfinished"></translation>
    </message>
    <message>
        <location line="+4"/>
        <source>把长视频按时间切成多个窗口，用多个进程并行识别（与「按时间分片并行」的线程桶不同）。「自动」按 CPU 核数与可用内存决定；仅 CPU 模式生效，短视频自动走单进程。每个并行进程约占 600MB 内存。</source>
        <translation>長動画を時間軸で複数のウィンドウに分割し、複数のプロセスで並列認識します（「時間分割並列」のスレッド方式とは異なります）。「自動」はCPUコア数と空きメモリから決定します。CPUモードのみ有効で、短い動画は自動的に単一プロセスになります。並列プロセス1つにつき約600MBのメモリを使用します。</translation>
    </message>
    <message>
        <location line="+25"/>
        <source>半自动：按字幕颜色跳过疑似无字帧（默认关，需预览并确认后才生效）</source>
        <translation type="unfinished"></translation>
    </message>
    <message>
        <location line="+7"/>
        <source>在阶段一抽样 ROI 区间内若干帧并在当前画面上自动标定 HSV。仅当预览结果满意并在对话框中点击「采用」后，才会在本轮 OCR 启用；可随时关闭恢复默认逻辑。若预览不满意或选择「不采用」，请保持勾选关闭或未确认——程序将按原版流程输出全部 ROI 帧。</source>
        <translation type="unfinished"></translation>
    </message>
    <message>
        <location line="+8"/>
        <source>预览检测效果…</source>
        <translation type="unfinished"></translation>
    </message>
    <message>
        <location line="+16"/>
        <source>DeepSeek 字幕润色</source>
        <translation type="unfinished"></translation>
    </message>
    <message>
        <location line="+2"/>
        <source>兼容 OpenAI 的接口。默认提供方为 DeepSeek，会自动填充 Base URL；当你输入 API Key 后，应用会调用 /v1/models 拉取模型列表（也可手动编辑模型 ID）。</source>
        <translation>OpenAI 互換のインターフェース。既定のプロバイダーは DeepSeek で、Base URL は自動入力されます。API Key を入力すると /v1/models を呼び出してモデル一覧を取得します（モデル ID は手動編集も可能）。</translation>
    </message>
    <message>
        <location line="+7"/>
        <source>DeepSeek 合并碎片字幕（选择最完整文本并合并时间范围）</source>
        <translation type="unfinished"></translation>
    </message>
    <message>
        <location line="+6"/>
        <source>API Key</source>
        <translation type="unfinished"></translation>
    </message>
    <message>
        <location line="+3"/>
        <source>API Key 不会以明文写入配置文件：优先保存在系统钥匙串（需安装 keyring）；不可用时使用本地加密存储（密钥文件仅当前用户可读）。清除可点击右侧按钮。</source>
        <translation>API Key は設定ファイルに平文で保存されません：まずシステムキーチェーン（keyring のインストールが必要）に保存し、利用できない場合はローカルの暗号化ストレージ（鍵ファイルは現在のユーザーのみ読み取り可能）を使用します。消去するには右のボタンをクリックしてください。</translation>
    </message>
    <message>
        <location line="+8"/>
        <source>API Base URL</source>
        <translation type="unfinished"></translation>
    </message>
    <message>
        <location line="+3"/>
        <source>大模型提供方</source>
        <translation type="unfinished"></translation>
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
        <translation type="unfinished"></translation>
    </message>
    <message>
        <location line="+8"/>
        <source>模型 ID（可从 API 自动拉取）</source>
        <translation type="unfinished"></translation>
    </message>
    <message>
        <location line="+4"/>
        <source>刷新模型列表</source>
        <translation type="unfinished"></translation>
    </message>
    <message>
        <location line="+1"/>
        <location line="+350"/>
        <source>使用 API Key 和 Base URL 拉取 /v1/models。</source>
        <translation type="unfinished"></translation>
    </message>
    <message>
        <location line="-348"/>
        <source>清除已存密钥</source>
        <translation type="unfinished"></translation>
    </message>
    <message>
        <location line="+2"/>
        <source>从本机配置中删除已保存的 API Key（输入框会清空）。Base URL 与模型仍会保留。</source>
        <translation type="unfinished"></translation>
    </message>
    <message>
        <location line="+7"/>
        <source>DeepSeek 合并策略复核（按更合适的阈值重新合并）</source>
        <translation type="unfinished"></translation>
    </message>
    <message>
        <location line="+135"/>
        <source>🔒 文字来源过滤已关闭，将保留所有识别到的文字。</source>
        <translation type="unfinished"></translation>
    </message>
    <message>
        <location line="+4"/>
        <source>🔄 文字来源过滤已启用，加载视频后将自动分析...</source>
        <translation type="unfinished"></translation>
    </message>
    <message>
        <location line="+76"/>
        <source>颜色门控：已关闭（默认）。</source>
        <translation type="unfinished"></translation>
    </message>
    <message>
        <location line="+2"/>
        <source>颜色门控：已勾选，尚未确认。请点击「预览检测效果」并在满意时选择「采用」。</source>
        <translation type="unfinished"></translation>
    </message>
    <message>
        <location line="+4"/>
        <source>颜色门控：已确认，将用于下一轮「字幕 OCR 识别」阶段一截取。</source>
        <translation type="unfinished"></translation>
    </message>
    <message>
        <location line="+229"/>
        <source>（无可用引擎）</source>
        <translation type="unfinished"></translation>
    </message>
    <message>
        <location line="+3"/>
        <source>⚠ 未检测到可用 OCR 引擎</source>
        <translation type="unfinished"></translation>
    </message>
    <message>
        <location line="+7"/>
        <source>✅ 已就绪</source>
        <translation type="unfinished"></translation>
    </message>
    <message>
        <location line="+89"/>
        <source>🔄 正在分析视频类型...</source>
        <translation type="unfinished"></translation>
    </message>
    <message>
        <location line="+98"/>
        <location line="+18"/>
        <source>手动模式（已自定义）</source>
        <translation type="unfinished"></translation>
    </message>
    <message>
        <location line="+76"/>
        <source>已恢复上次保存的手动设置</source>
        <translation type="unfinished"></translation>
    </message>
    <message>
        <source>中文简体</source>
        <translation type="vanished">簡体字中国語</translation>
    </message>
    <message>
        <source>English</source>
        <translation type="vanished">英語</translation>
    </message>
    <message>
        <source>日本語</source>
        <translation type="vanished">日本語</translation>
    </message>
    <message>
        <source>한국어</source>
        <translation type="vanished">韓国語</translation>
    </message>
    <message>
        <source>Русский</source>
        <translation type="vanished">ロシア語</translation>
    </message>
    <message>
        <source>Français</source>
        <translation type="vanished">フランス語</translation>
    </message>
    <message>
        <source>Deutsch</source>
        <translation type="vanished">ドイツ語</translation>
    </message>
    <message>
        <source>Italiano</source>
        <translation type="vanished">イタリア語</translation>
    </message>
    <message>
        <source>Español</source>
        <translation type="vanished">スペイン語</translation>
    </message>
    <message>
        <source>Português</source>
        <translation type="vanished">ポルトガル語</translation>
    </message>
    <message>
        <source>العربية</source>
        <translation type="vanished">アラビア語</translation>
    </message>
    <message>
        <location line="-298"/>
        <source>自动(Auto)</source>
        <translation>自動（Auto）</translation>
    </message>
    <message>
        <source>Tiny(最快)</source>
        <translation type="vanished">Tiny（最速）</translation>
    </message>
    <message>
        <source>Small(均衡)</source>
        <translation type="vanished">Small（バランス）</translation>
    </message>
    <message>
        <source>Medium(最准)</source>
        <translation type="vanished">Medium（高精度）</translation>
    </message>
    <message>
        <location line="-610"/>
        <source>大模型润色（DeepSeek / OpenAI，可选）</source>
        <translation>大規模モデルによる字幕ポリッシュ（DeepSeek / OpenAI、任意）</translation>
    </message>
    <message>
        <location line="+3"/>
        <source>高级选项</source>
        <translation>詳細オプション</translation>
    </message>
</context>
<context>
    <name>DeepSeekProgressPanel</name>
    <message>
        <location filename="../components/deepseek_progress_panel.py" line="+13"/>
        <source>DeepSeek / 大模型处理进度</source>
        <translation type="unfinished"></translation>
    </message>
    <message>
        <location line="+12"/>
        <source>启用 DeepSeek 后，此处显示处理进度，以及各批次润色前后的字幕对比、碎片合并候选与结果、策略复核说明等，便于核对模型具体改动了哪些字。</source>
        <translation type="unfinished"></translation>
    </message>
    <message>
        <location line="+9"/>
        <source>清空日志</source>
        <translation type="unfinished"></translation>
    </message>
</context>
<context>
    <name>FileOperationsWidget</name>
    <message>
        <source>File Operations (Drag &amp; Drop Supported)</source>
        <translation type="vanished">ファイル操作（ドラッグ＆ドロップ対応）</translation>
    </message>
    <message>
        <source>Load Video</source>
        <translation type="vanished">ビデオを読み込む</translation>
    </message>
    <message>
        <source>Save ROI Config</source>
        <translation type="vanished">対象領域(ROI)設定を保存</translation>
    </message>
    <message>
        <source>Load ROI Config</source>
        <translation type="vanished">対象領域(ROI)設定を読み込む</translation>
    </message>
    <message>
        <location filename="../components/file_operations.py" line="+13"/>
        <source>文件操作（支持拖放）</source>
        <translation type="unfinished"></translation>
    </message>
    <message>
        <location line="+6"/>
        <source>加载视频</source>
        <translation type="unfinished"></translation>
    </message>
    <message>
        <location line="+1"/>
        <source>保存 ROI 配置</source>
        <translation type="unfinished"></translation>
    </message>
    <message>
        <location line="+1"/>
        <source>加载 ROI 配置</source>
        <translation type="unfinished"></translation>
    </message>
    <message>
        <location line="+1"/>
        <source>自动检测字幕ROI</source>
        <translation>字幕ROI自動検出</translation>
    </message>
    <message>
        <location line="+1"/>
        <source>深度扫描（水印/场景字）…</source>
        <translation>詳細スキャン（透かし/シーン文字）…</translation>
    </message>
</context>
<context>
    <name>LogViewerWidget</name>
    <message>
        <source>Logs and Progress</source>
        <translation type="vanished">ログと進捗</translation>
    </message>
    <message>
        <location filename="../components/log_viewer.py" line="+10"/>
        <source>日志与进度</source>
        <translation type="unfinished"></translation>
    </message>
</context>
<context>
    <name>RoiDefinitionWidget</name>
    <message>
        <source>ROI Definition</source>
        <translation type="vanished">対象領域(ROI)の定義</translation>
    </message>
    <message>
        <source>Go back 1 frame (short press) / Continuous (long press)</source>
        <translation type="vanished">1フレーム戻る（短押し）／連続（長押し）</translation>
    </message>
    <message>
        <source>Go forward 1 frame (short press) / Continuous (long press)</source>
        <translation type="vanished">1フレーム進む（短押し）／連続（長押し）</translation>
    </message>
    <message>
        <source>Enter time (hh:mm:ss.ms) or frame number</source>
        <translation type="vanished">時間（時:分:秒.ミリ秒）またはフレーム番号を入力</translation>
    </message>
    <message>
        <source>Start Time:</source>
        <translation type="vanished">開始時間:</translation>
    </message>
    <message>
        <source>Set as Start Time</source>
        <translation type="vanished">開始時間として設定</translation>
    </message>
    <message>
        <source>End Time:</source>
        <translation type="vanished">終了時間:</translation>
    </message>
    <message>
        <source>Set as End Time</source>
        <translation type="vanished">終了時間として設定</translation>
    </message>
    <message>
        <source>Add New ROI</source>
        <translation type="vanished">新しい対象領域(ROI)を追加</translation>
    </message>
    <message>
        <source>Update Selected ROI</source>
        <translation type="vanished">選択した対象領域(ROI)を更新</translation>
    </message>
    <message>
        <source>Delete Selected ROI</source>
        <translation type="vanished">選択した対象領域(ROI)を削除</translation>
    </message>
    <message>
        <location filename="../components/roi_definition.py" line="+31"/>
        <source>ROI 定义</source>
        <translation type="unfinished"></translation>
    </message>
    <message>
        <location line="+16"/>
        <location line="+23"/>
        <source>后退 1 帧（短按）/ 连续（长按）</source>
        <translation type="unfinished"></translation>
    </message>
    <message>
        <location line="-18"/>
        <location line="+23"/>
        <source>前进 1 帧（短按）/ 连续（长按）</source>
        <translation type="unfinished"></translation>
    </message>
    <message>
        <location line="-20"/>
        <location line="+23"/>
        <source>输入时间（时:分:秒.毫秒）或帧号</source>
        <translation type="unfinished"></translation>
    </message>
    <message>
        <location line="-19"/>
        <source>开始时间：</source>
        <translation type="unfinished"></translation>
    </message>
    <message>
        <location line="+2"/>
        <source>设为开始时间</source>
        <translation type="unfinished"></translation>
    </message>
    <message>
        <location line="+21"/>
        <source>结束时间：</source>
        <translation type="unfinished"></translation>
    </message>
    <message>
        <location line="+2"/>
        <source>设为结束时间</source>
        <translation type="unfinished"></translation>
    </message>
    <message>
        <location line="+5"/>
        <source>按文字/描边/阴影颜色限制 OCR（不匹配像素将被遮罩）</source>
        <translation type="unfinished"></translation>
    </message>
    <message>
        <location line="+3"/>
        <source>将与所选颜色不接近的像素在 OCR 前置为白色，减少字幕笔画之外的误检。</source>
        <translation type="unfinished"></translation>
    </message>
    <message>
        <location line="+7"/>
        <source>OCR 前对 ROI 轻微模糊（降低锯齿/噪声）</source>
        <translation type="unfinished"></translation>
    </message>
    <message>
        <location line="+2"/>
        <source>在 OCR 前对 ROI 裁剪图应用轻微高斯模糊。对噪声大/压缩重的字幕更有帮助。</source>
        <translation type="unfinished"></translation>
    </message>
    <message>
        <location line="+8"/>
        <source>淡入/淡出微调（在检测到文字边界附近逐帧 OCR，找更精确的起止时间）</source>
        <translation type="unfinished"></translation>
    </message>
    <message>
        <location line="+7"/>
        <source>开启后，会只在文字出现/消失的边界附近逐帧 OCR 用于校准时间；ROI 其余部分仍可使用跳帧优化。</source>
        <translation type="unfinished"></translation>
    </message>
    <message>
        <location line="+8"/>
        <source>写入画面位置标签（\pos \frz \frx \fry，多边形自动计算位置与倾角）</source>
        <translation>画面位置タグを書き出す（\pos \frz \frx \fry。多角形から位置と傾きを自動計算）</translation>
    </message>
    <message>
        <location line="+7"/>
        <source>开启后，该 ROI 识别出的字幕事件将附带位置与旋转标签：多边形 ROI 自动计算中心点(\pos)与长边倾角(\frz)；\frx/\fry 保存为 0，可手动微调透视。适合实拍场景文字的原位重铺。</source>
        <translation>有効にすると、この ROI から書き出される字幕イベントに位置と回転のタグが付きます。多角形 ROI では中心点(\pos)と長辺の傾き(\frz)を自動計算し、\frx/\fry は 0 として保存されるため手動調整が可能です。実写シーン文字の原位置再配置に適します。</translation>
    </message>
    <message>
        <location line="+17"/>
        <source>文字颜色…</source>
        <translation type="unfinished"></translation>
    </message>
    <message>
        <location line="+1"/>
        <source>描边颜色…</source>
        <translation type="unfinished"></translation>
    </message>
    <message>
        <location line="+1"/>
        <source>阴影颜色…</source>
        <translation type="unfinished"></translation>
    </message>
    <message>
        <location line="+11"/>
        <source>RGB 容差：</source>
        <translation type="unfinished"></translation>
    </message>
    <message>
        <location line="+5"/>
        <source>单通道距离 0–255；数值越大，包含越多相近色阶（抗锯齿/渐变更稳）。</source>
        <translation type="unfinished"></translation>
    </message>
    <message>
        <location line="+3"/>
        <source>形态学：</source>
        <translation type="unfinished"></translation>
    </message>
    <message>
        <location line="+5"/>
        <source>闭运算核大小（奇数）；可在遮罩后连接断裂笔画。</source>
        <translation type="unfinished"></translation>
    </message>
    <message>
        <location line="+8"/>
        <source>添加新 ROI</source>
        <translation type="unfinished"></translation>
    </message>
    <message>
        <location line="+1"/>
        <source>更新选中 ROI</source>
        <translation type="unfinished"></translation>
    </message>
    <message>
        <location line="+1"/>
        <source>删除选中 ROI</source>
        <translation type="unfinished"></translation>
    </message>
    <message>
        <location line="+70"/>
        <source>字幕文字颜色</source>
        <translation type="unfinished"></translation>
    </message>
    <message>
        <location line="+10"/>
        <source>字幕描边颜色</source>
        <translation type="unfinished"></translation>
    </message>
    <message>
        <location line="+10"/>
        <source>字幕阴影颜色</source>
        <translation type="unfinished"></translation>
    </message>
</context>
<context>
    <name>RoiListWidget</name>
    <message>
        <source>ROI List</source>
        <translation type="vanished">対象領域(ROI)リスト</translation>
    </message>
    <message>
        <source>ROI {}: F[{}-{}] T[{} - {}]</source>
        <translation type="vanished">対象領域(ROI) {}: F[{}-{}] T[{} - {}]</translation>
    </message>
    <message>
        <source>Copy</source>
        <translation type="vanished">コピー</translation>
    </message>
    <message>
        <source>Paste After This Item</source>
        <translation type="vanished">この項目の後に貼り付け</translation>
    </message>
    <message>
        <source>Delete</source>
        <translation type="vanished">削除</translation>
    </message>
    <message>
        <source>Paste to End</source>
        <translation type="vanished">末尾に貼り付け</translation>
    </message>
    <message>
        <source>Confirm Deletion</source>
        <translation type="vanished">削除の確認</translation>
    </message>
    <message>
        <source>Are you sure you want to delete ROI {}?</source>
        <translation type="vanished">対象領域(ROI) {}を削除してもよろしいですか？</translation>
    </message>
    <message>
        <location filename="../components/roi_list.py" line="+18"/>
        <source>ROI 列表</source>
        <translation type="unfinished"></translation>
    </message>
    <message>
        <location line="+33"/>
        <source>ROI {}：帧[{}-{}] 时间[{} - {}]</source>
        <translation type="unfinished"></translation>
    </message>
    <message>
        <location line="+3"/>
        <source>[颜色掩膜]</source>
        <translation type="unfinished"></translation>
    </message>
    <message>
        <location line="+2"/>
        <source>[模糊]</source>
        <translation type="unfinished"></translation>
    </message>
    <message>
        <location line="+3"/>
        <source>[淡入淡出微调]</source>
        <translation type="unfinished"></translation>
    </message>
    <message>
        <location line="+2"/>
        <source>[自动过滤]</source>
        <translation>[自動フィルタ]</translation>
    </message>
    <message>
        <location line="+22"/>
        <source>复制</source>
        <translation type="unfinished"></translation>
    </message>
    <message>
        <location line="+5"/>
        <source>粘贴到此项之后</source>
        <translation type="unfinished"></translation>
    </message>
    <message>
        <location line="+4"/>
        <source>删除</source>
        <translation type="unfinished"></translation>
    </message>
    <message>
        <location line="+5"/>
        <source>切换文字过滤策略（当前：{}）</source>
        <translation>テキストフィルタポリシーを切り替え（現在：{}）</translation>
    </message>
    <message>
        <location line="+3"/>
        <source>自动过滤</source>
        <translation>自動フィルタ</translation>
    </message>
    <message>
        <location line="+2"/>
        <source>全部保留</source>
        <translation>すべて保持</translation>
    </message>
    <message>
        <location line="+10"/>
        <source>粘贴到末尾</source>
        <translation type="unfinished"></translation>
    </message>
    <message>
        <location line="+12"/>
        <source>确认删除</source>
        <translation type="unfinished"></translation>
    </message>
    <message>
        <location line="+1"/>
        <source>确定要删除 ROI {} 吗？</source>
        <translation type="unfinished"></translation>
    </message>
</context>
<context>
    <name>ScanReviewDialog</name>
    <message>
        <source>深度扫描复核</source>
        <translation type="vanished">詳細スキャンの確認</translation>
    </message>
    <message>
        <source>字幕带 ROI（自动检测的主字幕区）</source>
        <translation type="vanished">字幕帯 ROI（自動検出されたメイン字幕領域）</translation>
    </message>
    <message>
        <source>底部</source>
        <translation type="vanished">下部</translation>
    </message>
    <message>
        <source>顶部</source>
        <translation type="vanished">上部</translation>
    </message>
    <message>
        <source>中部</source>
        <translation type="vanished">中央</translation>
    </message>
    <message>
        <source>{0}  帧 [{1}-{2}]</source>
        <translation type="vanished">{0}  フレーム [{1}-{2}]</translation>
    </message>
    <message>
        <source>用所选字幕带替换现有 ROI 列表（否则追加）</source>
        <translation type="vanished">選択した字幕帯で既存の ROI リストを置き換える（チェックなしは追加）</translation>
    </message>
    <message>
        <source>未检测到字幕带。</source>
        <translation type="vanished">字幕帯は検出されませんでした。</translation>
    </message>
    <message>
        <source>疑似水印（恒定文本 + 恒定位置，勾选=识别时剔除）</source>
        <translation type="vanished">疑わしい透かし（恒定的テキスト＋位置、チェック＝認識時に除去）</translation>
    </message>
    <message>
        <source>「{0}」 出现率 {1:.0%}</source>
        <translation type="vanished">「{0}」 出現率 {1:.0%}</translation>
    </message>
    <message>
        <source>未检测到疑似水印。</source>
        <translation type="vanished">疑わしい透かしは検出されませんでした。</translation>
    </message>
    <message>
        <source>画面中部文字（场景字候选，勾选=导入为 ROI，全部保留）</source>
        <translation type="vanished">画面中央のテキスト（シーン文字候補、チェック＝ROI として取り込み、すべて保持）</translation>
    </message>
    <message>
        <source>「{0}」 出现 {1} 次（帧 {2}-{3}）</source>
        <translation type="vanished">「{0}」 {1}回出現（フレーム {2}-{3}）</translation>
    </message>
    <message>
        <source>（其余 {} 处低频文字未列出）</source>
        <translation type="vanished">（その他 {}箇所の低頻度テキストは未表示）</translation>
    </message>
    <message>
        <source>未检测到画面中部文字。</source>
        <translation type="vanished">画面中央のテキストは検出されませんでした。</translation>
    </message>
    <message>
        <source>应用</source>
        <translation type="vanished">適用</translation>
    </message>
    <message>
        <source>取消</source>
        <translation type="vanished">キャンセル</translation>
    </message>
</context>
<context>
    <name>SubtitleOCRGUI</name>
    <message>
        <source>Video Subtitle OCR Tool</source>
        <translation type="vanished">ビデオ字幕OCRツール</translation>
    </message>
    <message>
        <source>ASS template file loaded via drag and drop: {}</source>
        <translation type="vanished">ドラッグ＆ドロップでASSテンプレートファイルを読み込みました: {}</translation>
    </message>
    <message>
        <source>Select Video File</source>
        <translation type="vanished">ビデオファイルを選択</translation>
    </message>
    <message>
        <source>Video Files (*.mp4 *.avi *.mov *.mkv)</source>
        <translation type="vanished">ビデオファイル (*.mp4 *.avi *.mov *.mkv)</translation>
    </message>
    <message>
        <source>Error</source>
        <translation type="vanished">エラー</translation>
    </message>
    <message>
        <source>Unable to open video file</source>
        <translation type="vanished">ビデオファイルを開けません</translation>
    </message>
    <message>
        <source>Video Subtitle OCR Tool - {}</source>
        <translation type="vanished">ビデオ字幕OCRツール - {}</translation>
    </message>
    <message>
        <source>Video loaded: {}</source>
        <translation type="vanished">ビデオを読み込みました: {}</translation>
    </message>
    <message>
        <source>Resolution: {}x{}, FPS: {:.2f}, Total frames: {}</source>
        <translation type="vanished">解像度: {}x{}, FPS: {:.2f}, 総フレーム数: {}</translation>
    </message>
    <message>
        <source>Invalid time/frame number input: &apos;{}&apos;</source>
        <translation type="vanished">無効な時間/フレーム番号入力: &apos;{}&apos;</translation>
    </message>
    <message>
        <source>Added new ROI, frame range: {}-{}</source>
        <translation type="vanished">新しい対象領域(ROI)を追加しました、フレーム範囲: {}-{}</translation>
    </message>
    <message>
        <source>Updated ROI {}, new frame range: {}-{}</source>
        <translation type="vanished">対象領域(ROI) {}を更新しました、新しいフレーム範囲: {}-{}</translation>
    </message>
    <message>
        <source>Deleted ROI {}</source>
        <translation type="vanished">対象領域(ROI) {}を削除しました</translation>
    </message>
    <message>
        <source>Warning</source>
        <translation type="vanished">警告</translation>
    </message>
    <message>
        <source>Start time cannot be later than end time.</source>
        <translation type="vanished">開始時間は終了時間より遅くすることはできません。</translation>
    </message>
    <message>
        <source>Error creating ROI entry: {}</source>
        <translation type="vanished">対象領域(ROI)エントリの作成中にエラーが発生しました: {}</translation>
    </message>
    <message>
        <source>An error occurred while creating ROI: {}</source>
        <translation type="vanished">対象領域(ROI)の作成中にエラーが発生しました: {}</translation>
    </message>
    <message>
        <source>Save ROI Configuration</source>
        <translation type="vanished">対象領域(ROI)設定を保存</translation>
    </message>
    <message>
        <source>JSON Files (*.json)</source>
        <translation type="vanished">JSONファイル (*.json)</translation>
    </message>
    <message>
        <source>ROI configuration saved to: {}</source>
        <translation type="vanished">対象領域(ROI)設定を保存しました: {}</translation>
    </message>
    <message>
        <source>Failed to save ROI configuration: {}</source>
        <translation type="vanished">対象領域(ROI)設定の保存に失敗しました: {}</translation>
    </message>
    <message>
        <source>Please load a video file first.</source>
        <translation type="vanished">まずビデオファイルを読み込んでください。</translation>
    </message>
    <message>
        <source>Load ROI Configuration</source>
        <translation type="vanished">対象領域(ROI)設定を読み込む</translation>
    </message>
    <message>
        <source>ROI configuration loaded from {}</source>
        <translation type="vanished">対象領域(ROI)設定を{}から読み込みました</translation>
    </message>
    <message>
        <source>Failed to load ROI configuration: {}</source>
        <translation type="vanished">対象領域(ROI)設定の読み込みに失敗しました: {}</translation>
    </message>
    <message>
        <source>Copied ROI {} to clipboard.</source>
        <translation type="vanished">対象領域(ROI) {}をクリップボードにコピーしました。</translation>
    </message>
    <message>
        <source>Pasted after ROI {}.</source>
        <translation type="vanished">対象領域(ROI) {}の後に貼り付けました。</translation>
    </message>
    <message>
        <source>Pasted ROI at the end of the list.</source>
        <translation type="vanished">対象領域(ROI)をリストの末尾に貼り付けました。</translation>
    </message>
    <message>
        <source>Select ASS Template File</source>
        <translation type="vanished">ASSテンプレートファイルを選択</translation>
    </message>
    <message>
        <source>ASS Subtitle Files (*.ass)</source>
        <translation type="vanished">ASS字幕ファイル (*.ass)</translation>
    </message>
    <message>
        <source>Please load a video and define at least one ROI first.</source>
        <translation type="vanished">まずビデオを読み込み、少なくとも1つの対象領域(ROI)を定義してください。</translation>
    </message>
    <message>
        <source>Save Subtitle File</source>
        <translation type="vanished">字幕ファイルを保存</translation>
    </message>
    <message>
        <source>ASS Subtitles (*.ass)</source>
        <translation type="vanished">ASS字幕 (*.ass)</translation>
    </message>
    <message>
        <source>User canceled save operation, OCR task aborted.</source>
        <translation type="vanished">ユーザーが保存操作をキャンセルしたため、OCRタスクは中止されました。</translation>
    </message>
    <message>
        <source>Processing video...</source>
        <translation type="vanished">ビデオを処理中...</translation>
    </message>
    <message>
        <source>Cancel</source>
        <translation type="vanished">キャンセル</translation>
    </message>
    <message>
        <source>Complete</source>
        <translation type="vanished">完了</translation>
    </message>
    <message>
        <source>Subtitle file generated: {}</source>
        <translation type="vanished">字幕ファイルを生成しました: {}</translation>
    </message>
    <message>
        <source>Open Directory</source>
        <translation type="vanished">ディレクトリを開く</translation>
    </message>
    <message>
        <source>Do you want to open the folder containing the file?</source>
        <translation type="vanished">ファイルを含むフォルダを開きますか？</translation>
    </message>
    <message>
        <source>An error occurred during processing:
{}</source>
        <translation type="vanished">処理中にエラーが発生しました:
{}</translation>
    </message>
    <message>
        <source>Confirm Exit</source>
        <translation type="vanished">終了の確認</translation>
    </message>
    <message>
        <source>Background task is still running, are you sure you want to exit?</source>
        <translation type="vanished">バックグラウンドタスクが実行中です。終了してもよろしいですか？</translation>
    </message>
    <message>
        <location filename="../main_window/scan_control.py" line="+62"/>
        <location line="+36"/>
        <location line="+46"/>
        <source>未加载视频</source>
        <translation>動画が読み込まれていません</translation>
    </message>
    <message>
        <location filename="../main_window/roi_config_io.py" line="+85"/>
        <location filename="../main_window/scan_control.py" line="-81"/>
        <location line="+36"/>
        <location line="+46"/>
        <source>请先加载视频文件。</source>
        <translation>先に動画ファイルを読み込んでください。</translation>
    </message>
    <message>
        <location filename="../main_window/window.py" line="+94"/>
        <source>视频字幕 OCR 工具</source>
        <translation type="unfinished"></translation>
    </message>
    <message>
        <location line="+143"/>
        <source>已通过拖放加载 ASS 模板：{}</source>
        <translation type="unfinished"></translation>
    </message>
    <message>
        <location filename="../main_window/video_playback.py" line="+19"/>
        <source>选择视频文件</source>
        <translation type="unfinished"></translation>
    </message>
    <message>
        <location line="+2"/>
        <source>视频文件 (*.mp4 *.avi *.mov *.mkv)</source>
        <translation type="unfinished"></translation>
    </message>
    <message>
        <location line="+8"/>
        <source>无法打开视频文件</source>
        <translation type="unfinished"></translation>
    </message>
    <message>
        <location line="+27"/>
        <source>视频字幕 OCR 工具 - {}</source>
        <translation type="unfinished"></translation>
    </message>
    <message>
        <location line="+1"/>
        <source>视频已加载：{}</source>
        <translation type="unfinished"></translation>
    </message>
    <message>
        <location line="+1"/>
        <source>分辨率：{}x{}，帧率：{:.2f}，总帧数：{}</source>
        <translation type="unfinished"></translation>
    </message>
    <message>
        <location line="+22"/>
        <source>已自动加载 ROI 配置：{}</source>
        <translation type="unfinished"></translation>
    </message>
    <message>
        <location line="+4"/>
        <source>自动加载 ROI 配置失败：{}</source>
        <translation type="unfinished"></translation>
    </message>
    <message>
        <location line="+92"/>
        <source>无效的时间/帧号输入：&apos;{}&apos;</source>
        <translation type="unfinished"></translation>
    </message>
    <message>
        <location filename="../main_window/roi_editing.py" line="+40"/>
        <source>已添加新 ROI，帧范围：{}-{}</source>
        <translation type="unfinished"></translation>
    </message>
    <message>
        <location line="+15"/>
        <source>已更新 ROI {}，新帧范围：{}-{}</source>
        <translation type="unfinished"></translation>
    </message>
    <message>
        <location line="+20"/>
        <source>已删除 ROI {}</source>
        <translation type="unfinished"></translation>
    </message>
    <message>
        <location line="+13"/>
        <source>ROI {} 文字过滤策略已切换为：{}</source>
        <translation>ROI {} のテキストフィルタポリシーを変更しました：{}</translation>
    </message>
    <message>
        <location line="+4"/>
        <source>按场景预设过滤</source>
        <translation>シーンプリセットでフィルタ</translation>
    </message>
    <message>
        <location line="+2"/>
        <source>全部保留（不过滤）</source>
        <translation>すべて保持（フィルタなし）</translation>
    </message>
    <message>
        <location line="+206"/>
        <source>已在画面上调整 ROI {} 的区域</source>
        <translation>画面上で ROI {} の領域を調整しました</translation>
    </message>
    <message>
        <location filename="../main_window/pipeline_control.py" line="+17"/>
        <location line="+6"/>
        <location line="+10"/>
        <location line="+19"/>
        <location filename="../main_window/roi_config_io.py" line="-1"/>
        <location filename="../main_window/roi_editing.py" line="-150"/>
        <location filename="../main_window/source_config.py" line="+17"/>
        <location line="+12"/>
        <source>警告</source>
        <translation type="unfinished"></translation>
    </message>
    <message>
        <location filename="../main_window/roi_editing.py" line="+1"/>
        <source>开始时间不能晚于结束时间。</source>
        <translation type="unfinished"></translation>
    </message>
    <message>
        <location line="+50"/>
        <source>创建 ROI 条目时出错：{}</source>
        <translation type="unfinished"></translation>
    </message>
    <message>
        <location line="+3"/>
        <source>创建 ROI 时发生错误：{}</source>
        <translation type="unfinished"></translation>
    </message>
    <message>
        <location filename="../main_window/roi_config_io.py" line="-66"/>
        <source>保存 ROI 配置</source>
        <translation type="unfinished"></translation>
    </message>
    <message>
        <location line="+2"/>
        <location line="+72"/>
        <source>JSON 文件 (*.json)</source>
        <translation type="unfinished"></translation>
    </message>
    <message>
        <location line="-63"/>
        <source>ROI 配置已保存到：{}</source>
        <translation type="unfinished"></translation>
    </message>
    <message>
        <location line="+4"/>
        <location line="+1"/>
        <source>保存 ROI 配置失败：{}</source>
        <translation type="unfinished"></translation>
    </message>
    <message>
        <location line="+33"/>
        <source>自动备份 ROI 配置失败（识别仍会继续）：{}</source>
        <translation type="unfinished"></translation>
    </message>
    <message>
        <location line="+7"/>
        <source>为防意外中断，已自动备份 ROI 配置到：{}</source>
        <translation type="unfinished"></translation>
    </message>
    <message>
        <location line="+16"/>
        <source>加载 ROI 配置</source>
        <translation type="unfinished"></translation>
    </message>
    <message>
        <location line="+24"/>
        <source>ROI 配置已从 {} 加载</source>
        <translation type="unfinished"></translation>
    </message>
    <message>
        <location line="+4"/>
        <location line="+1"/>
        <source>加载 ROI 配置失败：{}</source>
        <translation type="unfinished"></translation>
    </message>
    <message>
        <location filename="../main_window/roi_editing.py" line="+104"/>
        <source>已将 ROI {} 复制到剪贴板。</source>
        <translation type="unfinished"></translation>
    </message>
    <message>
        <location line="+10"/>
        <source>已粘贴到 ROI {} 之后。</source>
        <translation type="unfinished"></translation>
    </message>
    <message>
        <location line="+10"/>
        <source>已将 ROI 粘贴到列表末尾。</source>
        <translation type="unfinished"></translation>
    </message>
    <message>
        <location filename="../main_window/roi_config_io.py" line="+4"/>
        <source>选择 ASS 模板文件</source>
        <translation type="unfinished"></translation>
    </message>
    <message>
        <location line="+2"/>
        <source>ASS 字幕文件 (*.ass)</source>
        <translation type="unfinished"></translation>
    </message>
    <message>
        <location filename="../main_window/pipeline_control.py" line="-34"/>
        <location filename="../main_window/source_config.py" line="-11"/>
        <source>请先加载视频并至少定义一个 ROI。</source>
        <translation type="unfinished"></translation>
    </message>
    <message>
        <location filename="../main_window/source_config.py" line="+12"/>
        <source>当前帧不在任一 ROI 的时间范围内。请将时间轴移到含字幕的典型帧上，用于自动标定颜色。</source>
        <translation type="unfinished"></translation>
    </message>
    <message>
        <location line="+21"/>
        <source>颜色门控预览失败：{}</source>
        <translation>カラーゲートのプレビューに失敗：{}</translation>
    </message>
    <message>
        <location line="+5"/>
        <source>预览失败</source>
        <translation type="unfinished"></translation>
    </message>
    <message>
        <location line="+9"/>
        <source>颜色门控已确认；下次运行「字幕 OCR 识别」时将在阶段一启用。</source>
        <translation type="unfinished"></translation>
    </message>
    <message>
        <location filename="../main_window/pipeline_control.py" line="+6"/>
        <source>已有 OCR 任务在运行中，请等待其完成或先取消。</source>
        <translation>OCR タスクが実行中です。完了を待つか、先にキャンセルしてください。</translation>
    </message>
    <message>
        <location line="+10"/>
        <source>已勾选「按字幕颜色跳过疑似无字帧」，但尚未通过预览确认。请点击「预览检测效果」并在满意时选择「采用」，或取消勾选以使用默认流程。</source>
        <translation type="unfinished"></translation>
    </message>
    <message>
        <location line="+19"/>
        <source>已启用 DeepSeek 功能（润色/碎片合并/策略复核），但未填写 API Key。请填写 API Key，或设置环境变量 DEEPSEEK_API_KEY。</source>
        <translation type="unfinished"></translation>
    </message>
    <message>
        <location line="+24"/>
        <source>保存字幕文件</source>
        <translation type="unfinished"></translation>
    </message>
    <message>
        <location line="+2"/>
        <source>ASS 字幕 (*.ass)</source>
        <translation type="unfinished"></translation>
    </message>
    <message>
        <location line="+3"/>
        <source>用户取消保存，OCR 任务已中止。</source>
        <translation type="unfinished"></translation>
    </message>
    <message>
        <location line="+10"/>
        <source>[LLM] 已启用 DeepSeek —— 第 4 步的大模型进度会显示在下方。</source>
        <translation type="unfinished"></translation>
    </message>
    <message>
        <location line="+6"/>
        <source>字幕 OCR + DeepSeek</source>
        <translation type="unfinished"></translation>
    </message>
    <message>
        <location line="+2"/>
        <source>字幕 OCR</source>
        <translation type="unfinished"></translation>
    </message>
    <message>
        <location line="+2"/>
        <source>正在处理视频...</source>
        <translation type="unfinished"></translation>
    </message>
    <message>
        <location line="+1"/>
        <source>取消</source>
        <translation>キャンセル</translation>
    </message>
    <message>
        <location line="+91"/>
        <source>[LLM] 完成 —— 已写入 ASS 文件。</source>
        <translation type="unfinished"></translation>
    </message>
    <message>
        <location line="+5"/>
        <source>完成</source>
        <translation type="unfinished"></translation>
    </message>
    <message>
        <location line="+1"/>
        <source>字幕文件已生成：{}</source>
        <translation type="unfinished"></translation>
    </message>
    <message>
        <location line="+2"/>
        <source>打开目录</source>
        <translation type="unfinished"></translation>
    </message>
    <message>
        <location line="+1"/>
        <source>是否打开包含该文件的文件夹？</source>
        <translation type="unfinished"></translation>
    </message>
    <message>
        <location line="+10"/>
        <source>[LLM] 已中止或出错 —— 请查看弹窗提示。</source>
        <translation type="unfinished"></translation>
    </message>
    <message>
        <location line="+5"/>
        <source>处理过程中发生错误：
{}</source>
        <translation type="unfinished"></translation>
    </message>
    <message>
        <location filename="../main_window/auto_detection.py" line="+43"/>
        <source>已恢复上次保存的文字来源过滤设置。</source>
        <translation type="unfinished"></translation>
    </message>
    <message>
        <location line="+35"/>
        <source>视频类型自动检测完成：{} (置信度 {:.0%})</source>
        <translation type="unfinished"></translation>
    </message>
    <message>
        <location line="+9"/>
        <source>视频类型自动检测失败：{}</source>
        <translation type="unfinished"></translation>
    </message>
    <message>
        <location line="+7"/>
        <source>自动分析失败，请手动选择场景类型</source>
        <translation type="unfinished"></translation>
    </message>
    <message>
        <location filename="../main_window/scan_control.py" line="-56"/>
        <source>正在后台扫描字幕分布（采样 {} 帧）…</source>
        <translation>バックグラウンドで字幕分布をスキャン中（{}フレームをサンプリング）…</translation>
    </message>
    <message>
        <location line="+20"/>
        <location line="+146"/>
        <location line="+52"/>
        <source>自动检测字幕ROI</source>
        <translation>字幕ROI自動検出</translation>
    </message>
    <message>
        <location line="-197"/>
        <source>请选择自动检测到的字幕区域的处理方式：

「是」：追加到现有 ROI 列表末尾（保留现有 ROI）；
「否」：替换全部现有 ROI（现有 ROI 将被清除，不可恢复）；
「取消」：中止本次检测。</source>
        <translation>自動検出した字幕領域の処理方法を選択してください：

「はい」：既存の ROI リストの末尾に追加します（既存の ROI は保持）；
「いいえ」：既存のすべての ROI を置き換えます（既存の ROI は削除され、元に戻せません）；
「キャンセル」：今回の検出を中止します。</translation>
    </message>
    <message>
        <location line="+16"/>
        <source>替换现有 ROI？</source>
        <translation>既存の ROI を置き換えますか？</translation>
    </message>
    <message>
        <location line="+1"/>
        <source>即将清除现有 {} 个 ROI 并用检测结果替换，此操作不可恢复。是否继续？</source>
        <translation>既存の {} 個の ROI を削除して検出結果で置き換えます。この操作は元に戻せません。続行しますか？</translation>
    </message>
    <message>
        <location filename="../main_window/window.py" line="+26"/>
        <source>确认退出</source>
        <translation type="unfinished"></translation>
    </message>
    <message>
        <location line="+1"/>
        <source>后台任务仍在运行，确定要退出吗？</source>
        <translation type="unfinished"></translation>
    </message>
    <message>
        <location filename="../main_window/pipeline_control.py" line="-1"/>
        <location filename="../main_window/roi_config_io.py" line="-93"/>
        <location line="+85"/>
        <location filename="../main_window/roi_editing.py" line="-125"/>
        <location filename="../main_window/scan_control.py" line="+195"/>
        <location filename="../main_window/video_playback.py" line="-148"/>
        <source>错误</source>
        <translation>エラー</translation>
    </message>
    <message>
        <source>自动字幕定位模块不可用：{}
请确认 core/subtitle_roi_suggester 模块及其依赖已正确安装。</source>
        <translation type="vanished">自動字幕位置特定モジュールを利用できません：{}
core/subtitle_roi_suggester モジュールとその依存関係が正しくインストールされていることを確認してください。</translation>
    </message>
    <message>
        <source>正在自动检测字幕区域…</source>
        <translation type="vanished">字幕領域を自動検出中…</translation>
    </message>
    <message>
        <source>自动字幕检测进度：{}/{}（采样帧）</source>
        <translation type="vanished">自動字幕検出の進捗：{}/{}（サンプリングフレーム）</translation>
    </message>
    <message>
        <location filename="../main_window/scan_control.py" line="-66"/>
        <source>未检测到字幕区域。请确认视频中确实存在字幕，或调整识别语言后重试。</source>
        <translation>字幕領域を検出できませんでした。動画に字幕が存在するか確認するか、認識言語を変更して再試行してください。</translation>
    </message>
    <message>
        <location line="+52"/>
        <source>自动检测完成：检测到 {} 个字幕区域，时间范围 {} ～ {}</source>
        <translation>自動検出が完了しました：{} 個の字幕領域を検出、時間範囲 {} ～ {}</translation>
    </message>
    <message>
        <location line="-38"/>
        <source>已用 {} 个自动检测的 ROI 替换全部现有 ROI。</source>
        <translation>自動検出した {} 個の ROI で既存のすべての ROI を置き換えました。</translation>
    </message>
    <message>
        <location line="-96"/>
        <source>正在深度扫描（采样 {} 帧，含水印/场景字统计）…</source>
        <translation>詳細スキャン中（{}フレームをサンプリング、透かし/シーン文字の統計を含む）…</translation>
    </message>
    <message>
        <location line="+12"/>
        <source>深度扫描</source>
        <translation>詳細スキャン</translation>
    </message>
    <message>
        <location line="+1"/>
        <source>扫描完成：未检测到字幕带、水印或场景文字。</source>
        <translation>スキャン完了：字幕帯・透かし・シーン文字は検出されませんでした。</translation>
    </message>
    <message>
        <location line="+23"/>
        <source>将在识别时剔除 {} 处水印文本。</source>
        <translation>認識時に{}箇所の透かしテキストを除去します。</translation>
    </message>
    <message>
        <location line="+6"/>
        <source>未启用水印剔除。</source>
        <translation>透かし除去は有効ではありません。</translation>
    </message>
    <message>
        <location line="+3"/>
        <source>已导入 {} 个字幕带 ROI、{} 个场景字 ROI。</source>
        <translation>字幕帯 ROI {}件、シーン文字 ROI {}件を取り込みました。</translation>
    </message>
    <message>
        <location line="+13"/>
        <source>字幕扫描进度：{}/{}（采样帧）</source>
        <translation>字幕スキャン進捗：{}/{}（サンプリングフレーム）</translation>
    </message>
    <message>
        <location line="+31"/>
        <source>后台扫描未检测到字幕带，可手动绘制 ROI。</source>
        <translation>バックグラウンドスキャンでは字幕帯を検出できませんでした。手動で ROI を描画できます。</translation>
    </message>
    <message>
        <location line="+14"/>
        <source>已追加 {} 个自动检测的 ROI 到列表末尾。</source>
        <translation>自動検出した {} 個の ROI をリストの末尾に追加しました。</translation>
    </message>
    <message>
        <location line="+11"/>
        <source>检测到 {} 处疑似水印（恒定文本/位置）。可运行深度扫描复核后自动剔除。</source>
        <translation>{}箇所の疑わしい透かし（恒定的なテキスト/位置）を検出しました。詳細スキャンの確認後に自動除去できます。</translation>
    </message>
    <message>
        <location line="+7"/>
        <source>检测到 {} 处画面中部文字（场景字候选）。可在深度扫描复核中导入为 ROI。</source>
        <translation>画面中央のテキストを{}箇所検出しました（シーン文字候補）。詳細スキャンの確認で ROI として取り込めます。</translation>
    </message>
    <message>
        <location line="+22"/>
        <location line="+6"/>
        <source>自动检测失败：{}</source>
        <translation>自動検出に失敗しました：{}</translation>
    </message>
</context>
<context>
    <name>VideoFrameLabel</name>
    <message>
        <source>Drawing error: {}</source>
        <translation type="vanished">描画エラー: {}</translation>
    </message>
    <message>
        <location filename="../components/video_display.py" line="+431"/>
        <source>绘制错误：{}</source>
        <translation>描画エラー：{}</translation>
    </message>
</context>
<context>
    <name>coordinate_restorer</name>
    <message>
        <location filename="../core/coordinate_restorer.py" line="+18"/>
        <source>A valid working directory `work_dir` must be provided.</source>
        <translation>有効な作業ディレクトリ`work_dir`を指定する必要があります。</translation>
    </message>
    <message>
        <location line="+9"/>
        <source>Frame {} (ROI: {}) has no OCR results, coordinate restoration skipped.</source>
        <translation>フレーム{}（対象領域(ROI): {}）にはOCR結果がないため、座標復元はスキップされました。</translation>
    </message>
    <message>
        <location line="+10"/>
        <source>Could not get offset for frame {} (ROI: {}), skipped.</source>
        <translation>フレーム{}（対象領域(ROI): {}）のオフセットを取得できませんでした、スキップされました。</translation>
    </message>
    <message>
        <location line="+14"/>
        <source>OCR result file does not exist: {}, skipped.</source>
        <translation>OCR結果ファイルが存在しません: {}、スキップされました。</translation>
    </message>
    <message>
        <location line="+12"/>
        <source>Unknown OCR result data type (Frame {}, ROI {}). Type: {}, skipped.</source>
        <translation>不明なOCR結果データ型（フレーム{}、対象領域(ROI) {}）。タイプ: {}、スキップされました。</translation>
    </message>
    <message>
        <location line="+15"/>
        <source>Frame {} (ROI: {}) OCR result is empty, skipping coordinate restoration.</source>
        <translation>フレーム{}（対象領域(ROI): {}）のOCR結果が空のため、座標復元をスキップします。</translation>
    </message>
    <message>
        <location line="+19"/>
        <source>Error restoring coordinates for frame {} (ROI: {}): {}</source>
        <translation>フレーム{}（対象領域(ROI): {}）の座標復元中にエラーが発生しました: {}</translation>
    </message>
    <message>
        <location line="+23"/>
        <source>Incorrect points format for rectangular ROI: {}</source>
        <translation>四角形対象領域(ROI)のポイント形式が正しくありません: {}</translation>
    </message>
    <message>
        <location line="+11"/>
        <source>Incorrect points format for polygonal ROI: {}</source>
        <translation>多角形対象領域(ROI)のポイント形式が正しくありません: {}</translation>
    </message>
    <message>
        <location line="+14"/>
        <source>Error parsing polygonal ROI offset: {}</source>
        <translation>多角形対象領域(ROI)オフセットの解析中にエラーが発生しました: {}</translation>
    </message>
    <message>
        <location line="+7"/>
        <source>Unknown ROI type: {}</source>
        <translation>不明な対象領域(ROI)タイプ: {}</translation>
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
        <translation>フレーム{}の画像データを取得できませんでした。入力タイプ: {}</translation>
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
        <translation>OCRオプティマイザーがキャンセル信号を検出したため、早期に終了します。</translation>
    </message>
    <message>
        <location line="+101"/>
        <source>Smart frame skipping: ROI &apos;{}&apos; from frame {} to {} has similar content, skipping {} OCR operations.</source>
        <translation type="unfinished"></translation>
    </message>
    <message>
        <source>Smart frame skipping: ROI &apos;&apos;{}&apos;&apos; from frame {} to {} has similar content, skipping {} OCR operations.</source>
        <translation type="vanished">スマートフレームスキップ: フレーム{}から{}までの対象領域(ROI) &apos;{}&apos;は類似した内容のため、{}個のOCR操作をスキップします。</translation>
    </message>
    <message>
        <location line="+20"/>
        <source>Cleaning up cache.</source>
        <translation>キャッシュをクリーンアップしています。</translation>
    </message>
</context>
<context>
    <name>ocr_processor</name>
    <message>
        <source>Initializing PaddleOCR model...</source>
        <translation type="vanished">PaddleOCRモデルを初期化中...</translation>
    </message>
    <message>
        <source>PaddleOCR will run in {} mode.</source>
        <translation type="vanished">PaddleOCRは{}モードで実行されます。</translation>
    </message>
    <message>
        <source>PaddleOCR model initialized.</source>
        <translation type="vanished">PaddleOCRモデルが初期化されました。</translation>
    </message>
    <message>
        <source>Processing image from path: {}</source>
        <translation type="vanished">パス: {}から画像を処理中</translation>
    </message>
    <message>
        <source>Processing image from memory (frame {}, ROI {})</source>
        <translation type="vanished">メモリから画像を処理中（フレーム{}、対象領域(ROI) {}）</translation>
    </message>
    <message>
        <source>Processed document-level OCR result for frame {}, ROI {}.</source>
        <translation type="vanished">フレーム{}、対象領域(ROI) {}のドキュメントレベルOCR結果を処理しました。</translation>
    </message>
    <message>
        <source>Unexpected line result format for frame {}, ROI {}: {}</source>
        <translation type="vanished">フレーム{}、対象領域(ROI) {}の予期しない行結果形式: {}</translation>
    </message>
    <message>
        <source>Saved OCR results to: {}</source>
        <translation type="vanished">OCR結果を保存しました: {}</translation>
    </message>
    <message>
        <source>OCR Results for frame {}, ROI {}:</source>
        <translation type="vanished">フレーム{}、対象領域(ROI) {}のOCR結果:</translation>
    </message>
    <message>
        <source>  {}. Text: {}, Score: {:.2f}</source>
        <translation type="vanished">  {}. テキスト: {}, スコア: {:.2f}</translation>
    </message>
    <message>
        <source>Saved visualization to directory: {}</source>
        <translation type="vanished">可視化をディレクトリに保存しました: {}</translation>
    </message>
    <message>
        <source>No OCR results found for frame {}, ROI {}.</source>
        <translation type="vanished">フレーム{}、対象領域(ROI) {}のOCR結果が見つかりませんでした。</translation>
    </message>
    <message>
        <source>Saved empty OCR results to: {}</source>
        <translation type="vanished">空のOCR結果を保存しました: {}</translation>
    </message>
    <message>
        <location filename="../core/ocr_processor.py" line="+111"/>
        <source>Batch OCR Image Processing</source>
        <translation>バッチOCR画像処理</translation>
    </message>
    <message>
        <location line="+2"/>
        <source>Input image directory path</source>
        <translation>入力画像ディレクトリパス</translation>
    </message>
    <message>
        <location line="+2"/>
        <source>Output results directory path</source>
        <translation>出力結果ディレクトリパス</translation>
    </message>
    <message>
        <location line="+2"/>
        <source>Enable visualization output</source>
        <translation>可視化出力を有効にする</translation>
    </message>
</context>
<context>
    <name>pipeline_worker</name>
    <message>
        <location filename="../core/pipeline_worker.py" line="+105"/>
        <source>Intermediate files will be saved to: {}</source>
        <translation>中間ファイルは以下に保存されます: {}</translation>
    </message>
    <message>
        <location line="+6"/>
        <source>in-memory data stream</source>
        <translation>インメモリデータストリーム</translation>
    </message>
    <message>
        <location line="+1"/>
        <source>disk file stream</source>
        <translation>ディスクファイルストリーム</translation>
    </message>
    <message>
        <location line="+4"/>
        <source>OCR pipeline will run in {} mode.</source>
        <translation>OCRパイプラインは{}モードで実行されます。</translation>
    </message>
    <message>
        <location filename="../core/pipeline_stages.py" line="+95"/>
        <source>Step 1/4: Calculating number of ROI frames to process...</source>
        <translation>ステップ1/4: 処理する対象領域(ROI)フレーム数を計算中...</translation>
    </message>
    <message>
        <location line="+19"/>
        <source>ROI extraction step did not produce any data. Please check ROI time and region settings.</source>
        <translation>対象領域(ROI)抽出ステップでデータが生成されませんでした。対象領域(ROI)の時間と領域設定を確認してください。</translation>
    </message>
    <message>
        <location line="+4"/>
        <source>Step 1/4: Calculation complete, total {} frames. Starting extraction...</source>
        <translation>ステップ1/4: 計算完了、合計{}フレーム。抽出を開始します...</translation>
    </message>
    <message>
        <location line="+44"/>
        <source>Streaming mode enabled (time_slice={}s). OCR will run during extraction to reduce peak memory.</source>
        <translation type="unfinished"></translation>
    </message>
    <message>
        <location line="+145"/>
        <source>Step 1/4: Extracting ROI frames... ({}/{})</source>
        <translation>ステップ1/4: 対象領域(ROI)フレームを抽出中... ({}/{})</translation>
    </message>
    <message>
        <location line="+30"/>
        <source>Step 1/4: ROI frame extraction complete. Total {} ROI frames.</source>
        <translation>ステップ1/4: 対象領域(ROI)フレーム抽出完了。合計{}対象領域(ROI)フレーム。</translation>
    </message>
    <message>
        <source>Error during ROI extraction: {}</source>
        <translation type="vanished">対象領域(ROI)抽出中にエラーが発生しました: {}</translation>
    </message>
    <message>
        <location line="-184"/>
        <source>Step 2/4: Starting intelligent OCR recognition... (0/{})</source>
        <translation>ステップ2/4: インテリジェントOCR認識を開始中... (0/{})</translation>
    </message>
    <message>
        <source>Starting to process {}, containing {} frames...</source>
        <translation type="vanished">{}の処理を開始します、{}フレームを含みます...</translation>
    </message>
    <message>
        <location line="+77"/>
        <location line="+58"/>
        <location line="+111"/>
        <location line="+95"/>
        <source>Step 2/4: OCR recognition in progress... ({}/{})</source>
        <translation>ステップ2/4: OCR認識進行中... ({}/{})</translation>
    </message>
    <message>
        <location line="-255"/>
        <source>Streaming OCR will flush in parallel (cpu_workers={}, max_stream_workers={}).</source>
        <translation type="unfinished"></translation>
    </message>
    <message>
        <location line="+106"/>
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
        <translation>OCR認識ステップで結果が生成されませんでした。</translation>
    </message>
    <message>
        <location line="+48"/>
        <source>Step 2/4: OCR recognition complete.</source>
        <translation>ステップ2/4: OCR認識完了。</translation>
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
        <translation>境界 refinement を {} スレッドで並列実行中...</translation>
    </message>
    <message>
        <location line="+44"/>
        <source>Step 3/4: Starting coordinate restoration... (0/{})</source>
        <translation>ステップ3/4: 座標復元を開始中... (0/{})</translation>
    </message>
    <message>
        <location line="+25"/>
        <source>Step 3/4: Restoring coordinates... ({}/{})</source>
        <translation>ステップ3/4: 座標を復元中... ({}/{})</translation>
    </message>
    <message>
        <location line="+7"/>
        <source>Coordinate restoration step did not produce any results.</source>
        <translation>座標復元ステップで結果が生成されませんでした。</translation>
    </message>
    <message>
        <location line="+2"/>
        <source>Step 3/4: Coordinate restoration complete.</source>
        <translation>ステップ3/4: 座標復元完了。</translation>
    </message>
    <message>
        <location line="+3"/>
        <source>Coordinate restoration done: {} frames in {:.2f}s (save_json={}).</source>
        <translation type="unfinished"></translation>
    </message>
    <message>
        <location filename="../core/pipeline_worker.py" line="+12"/>
        <source>Step 1-3/4: Processing chunks in parallel...</source>
        <translation>ステップ 1-3/4: チャンクを並列処理中...</translation>
    </message>
    <message>
        <location line="+22"/>
        <source>Chunk-parallel OCR: {} workers, {} windows.</source>
        <translation>チャンク並列OCR: {} ワーカー、{} ウィンドウ。</translation>
    </message>
    <message>
        <location line="+25"/>
        <source>Step 4/4: Starting ASS subtitle file generation...</source>
        <translation>ステップ4/4: ASS字幕ファイル生成を開始中...</translation>
    </message>
    <message>
        <location line="+50"/>
        <source>Step 4/4: ASS subtitle generation complete.</source>
        <translation>ステップ4/4: ASS字幕生成完了。</translation>
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
        <translation>パイプライン処理に失敗しました: {}</translation>
    </message>
    <message>
        <location line="+7"/>
        <source>An error occurred during processing: {}</source>
        <translation>処理中にエラーが発生しました: {}</translation>
    </message>
    <message>
        <location line="+10"/>
        <source>Temporary working directory deleted: {}</source>
        <translation>一時作業ディレクトリを削除しました: {}</translation>
    </message>
    <message>
        <location line="+7"/>
        <source>Could not delete temporary working directory {}: {}</source>
        <translation>一時作業ディレクトリ{}を削除できませんでした: {}</translation>
    </message>
    <message>
        <location line="+8"/>
        <source>Task cancellation request sent.</source>
        <translation>タスクキャンセル要求を送信しました。</translation>
    </message>
    <message>
        <location line="+4"/>
        <source>Forcibly terminating thread...</source>
        <translation>スレッドを強制終了中...</translation>
    </message>
    <message>
        <location filename="../core/pipeline_stages.py" line="+110"/>
        <source>Boundary refinement used {} extra single-frame OCR calls.</source>
        <translation type="unfinished"></translation>
    </message>
</context>
<context>
    <name>roi_extractor</name>
    <message>
        <location filename="../core/roi_extractor.py" line="+18"/>
        <source>CUDA-enabled GPU detected and available for OpenCV.</source>
        <translation>CUDA対応GPUが検出され、OpenCVで使用可能です。</translation>
    </message>
    <message>
        <location line="+2"/>
        <source>No CUDA-enabled GPU detected or OpenCV not compiled with CUDA support.</source>
        <translation>CUDA対応GPUが検出されないか、OpenCVがCUDAサポートでコンパイルされていません。</translation>
    </message>
    <message>
        <location line="+2"/>
        <source>OpenCV CUDA module not found. Likely OpenCV was not compiled with CUDA support.</source>
        <translation>OpenCV CUDAモジュールが見つかりません。OpenCVがCUDAサポートでコンパイルされていない可能性があります。</translation>
    </message>
    <message>
        <location line="+2"/>
        <source>Error checking for CUDA GPU: {e}</source>
        <translation>CUDA GPUのチェック中にエラーが発生しました: {e}</translation>
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
        <translation>処理する対象領域(ROI)フレームの合計{}が事前に計算されました。</translation>
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
        <translation>抽出を開始できません: ビデオパスまたは対象領域(ROI)データが提供されていません。</translation>
    </message>
    <message>
        <location line="-114"/>
        <location line="+118"/>
        <source>In save_to_disk mode, a valid working directory `work_dir` must be provided.</source>
        <translation>save_to_diskモードでは、有効な作業ディレクトリ`work_dir`を指定する必要があります。</translation>
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
        <translation>抽出用のビデオファイルを開けませんでした: {}</translation>
    </message>
    <message>
        <location line="+17"/>
        <source>Attempting to use GPU for ROI extraction.</source>
        <translation>GPUを使用した対象領域(ROI)抽出を試行します。</translation>
    </message>
    <message>
        <location line="+2"/>
        <source>Using CPU for ROI extraction (GPU not available or OpenCV not compiled with CUDA).</source>
        <translation>CPUを使用した対象領域(ROI)抽出を実行中（GPUが利用不可、またはOpenCVがCUDA未対応です）。</translation>
    </message>
    <message>
        <location line="+47"/>
        <source>Could not retrieve video frame {}.</source>
        <translation type="unfinished"></translation>
    </message>
    <message>
        <source>Could not read video frame {}.</source>
        <translation type="vanished">ビデオフレーム{}を読み取れませんでした。</translation>
    </message>
    <message>
        <location line="+20"/>
        <source>Failed to upload frame to GPU for frame {}. Falling back to CPU for this frame. Error: {}</source>
        <translation>フレーム{}のGPUへのアップロードに失敗しました。本フレームはCPUで処理します。エラー: {}</translation>
    </message>
    <message>
        <location line="+20"/>
        <source>ROI {} has no &apos;points&apos; field; skipped.</source>
        <translation type="unfinished"></translation>
    </message>
    <message>
        <location line="+24"/>
        <source>Incorrect points format for rectangular ROI: {}</source>
        <translation>四角形対象領域(ROI)のポイント形式が正しくありません: {}</translation>
    </message>
    <message>
        <location line="+11"/>
        <source>Incorrect points format for polygonal ROI: {}</source>
        <translation>多角形対象領域(ROI)のポイント形式が正しくありません: {}</translation>
    </message>
    <message>
        <location line="+29"/>
        <source>Error processing polygonal ROI: {}</source>
        <translation>多角形対象領域(ROI)の処理中にエラーが発生しました: {}</translation>
    </message>
    <message>
        <location line="+8"/>
        <source>Unknown ROI type: {}</source>
        <translation>不明な対象領域(ROI)タイプ: {}</translation>
    </message>
    <message>
        <location line="+23"/>
        <source>ROI extraction resulted in empty image, frame {}, ROI {}</source>
        <translation>対象領域(ROI)抽出により空の画像が生成されました、フレーム{}、対象領域(ROI) {}</translation>
    </message>
</context>
</TS>
