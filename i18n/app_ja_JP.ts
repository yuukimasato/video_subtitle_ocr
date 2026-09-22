<?xml version="1.0" encoding="utf-8"?>
<!DOCTYPE TS>
<TS version="2.1" language="ja_JP">
<context>
    <name>ColorGatePreviewDialog</name>
    <message>
        <location filename="../components/color_gate_preview_dialog.py" line="+43"/>
        <source>颜色门控：预览</source>
        <translation>カラーゲート：プレビュー</translation>
    </message>
    <message>
        <location line="+18"/>
        <source>以下缩略来自 ROI 区间内均匀采样。请确认「判定为保留」与您期望一致。若不满意，请选择「取消」或直接关闭对话框，并保持主界面选项关闭或未确认。</source>
        <translation>以下のサムネイルは ROI 区間から均等にサンプリングしたものです。「保持」と判定されたフレームが期待どおりかご確認ください。満足できない場合は「キャンセル」を選ぶかダイアログを閉じ、メイン画面のオプションをオフまたは未確認のままにしてください。</translation>
    </message>
    <message>
        <location line="+10"/>
        <source>阈值与预估</source>
        <translation>しきい値と推定</translation>
    </message>
    <message>
        <location line="+8"/>
        <source>命中面积占比下限：</source>
        <translation>ヒット面積比率の下限：</translation>
    </message>
    <message>
        <location line="+21"/>
        <source>采用（用于本次识别）</source>
        <translation>採用（今回の認識で使用）</translation>
    </message>
    <message>
        <location line="+3"/>
        <source>不采用</source>
        <translation>不採用</translation>
    </message>
    <message>
        <location line="+25"/>
        <source>采样帧数：{}；在当前阈值下「保留」帧数：{}（约 {:.1f}%）；按此比例粗略估计全流程 ROI 图数量约：{} / {}（原计划）。</source>
        <translation>サンプリング枚数：{}。現在のしきい値で「保持」されたフレーム：{}（約 {:.1f}%）。この比率から全工程の ROI 画像数を概算すると 約 {} / {}（当初予定）。</translation>
    </message>
    <message>
        <location line="+12"/>
        <source>保留</source>
        <translation>保持</translation>
    </message>
    <message>
        <location line="+2"/>
        <source>跳过</source>
        <translation>スキップ</translation>
    </message>
    <message>
        <location line="+3"/>
        <source>帧 {} · {}</source>
        <translation>フレーム {} · {}</translation>
    </message>
    <message>
        <location line="+3"/>
        <source>max 占比：{ratio:.4f}</source>
        <translation>max 比率：{ratio:.4f}</translation>
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
        <location filename="../components/control_panel.py" line="+129"/>
        <source>识别语言：</source>
        <translation>認識言語：</translation>
    </message>
    <message>
        <location line="+70"/>
        <source>模型档位：</source>
        <translation>モデル精度（Tier）：</translation>
    </message>
    <message>
        <source>选择字幕语言。中文/英语/日语使用 PP-OCRv6 模型，其他语言自动回落 PP-OCRv5 多语言模型。</source>
        <translation type="vanished">字幕の言語を選択します。中国語/英語/日本語は PP-OCRv6 モデルを使用し、その他の言語は自動的に PP-OCRv5 多言語モデルへフォールバックします。</translation>
    </message>
    <message>
        <location line="-89"/>
        <source>样式模板（可选）</source>
        <translation>スタイルテンプレート（任意）</translation>
    </message>
    <message>
        <location line="+5"/>
        <source>点击“浏览”选择 .ass 模板文件</source>
        <translation>「参照」をクリックして .ass テンプレートファイルを選択</translation>
    </message>
    <message>
        <location line="+1"/>
        <location line="+530"/>
        <location line="+104"/>
        <location line="+25"/>
        <source>浏览…</source>
        <translation>参照…</translation>
    </message>
    <message>
        <location line="-643"/>
        <source>选择字幕语言。中文简体/繁体/英语/日语及拉丁语系使用 PP-OCRv6 模型，韩/俄/阿拉伯语等自动回落 PP-OCRv5 多语言模型。</source>
        <translation type="unfinished">字幕の言語を選択します。簡体字中国語/繁体字中国語/英語/日本語およびラテン系言語は PP-OCRv6 モデルを使用し、韓国語/ロシア語/アラビア語などは自動的に PP-OCRv5 多言語モデルへフォールバックします。</translation>
    </message>
    <message>
        <location line="+59"/>
        <source>引擎选择：</source>
        <translation>エンジン選択：</translation>
    </message>
    <message>
        <location line="+3"/>
        <source>检测可用引擎</source>
        <translation>利用可能なエンジンを検出</translation>
    </message>
    <message>
        <location line="+8"/>
        <source>PaddleOCR 模型档位：Tiny 最快，Small 均衡，Medium 最准。“自动”使用 PP-OCRv6 默认模型。</source>
        <translation>PaddleOCR のモデル tiers：Tiny は最速、Small はバランス、Medium は最も正確。「自動」では PP-OCRv6 のデフォルトモデルを使用します。</translation>
    </message>
    <message>
        <location line="+24"/>
        <source>文字来源过滤（上下文语义分析）</source>
        <translation>テキストソースフィルタ（文脈意味解析）</translation>
    </message>
    <message>
        <location line="+7"/>
        <source>启用文字来源过滤（实验性）</source>
        <translation>テキストソースフィルタを有効化（実験的）</translation>
    </message>
    <message>
        <location line="+6"/>
        <source>勾选后，OCR 识别结果将经过上下文语义分析，自动区分后期叠加字幕与实拍场景文字。未勾选时保留所有识别到的文字。</source>
        <translation>チェックすると、OCR 結果を文脈意味解析で処理し、後付けの字幕と実写シーン内のテキストを自動的に区別します。未チェックの場合は認識したすべてのテキストを保持します。</translation>
    </message>
    <message>
        <location line="+11"/>
        <source>🔄 加载视频后将自动分析...</source>
        <translation>🔄 動画を読み込むと自動解析を開始します...</translation>
    </message>
    <message>
        <location line="+9"/>
        <source>场景预设：</source>
        <translation>シーンプリセット：</translation>
    </message>
    <message>
        <location line="+2"/>
        <source>选择适合当前视频的场景类型，自动配置文字来源过滤规则</source>
        <translation>この動画に合ったシーンタイプを選ぶと、テキストソースフィルタのルールを自動設定します</translation>
    </message>
    <message>
        <location line="+7"/>
        <source>保留后期叠加文字 (OVERLAY)
    字幕、标题、水印、UI 按钮、弹幕、特效文字</source>
        <translation>後付けの重ね文字を保持 (OVERLAY)
    字幕、タイトル、ウォーターマーク、UI ボタン、弾幕、演出テキスト</translation>
    </message>
    <message>
        <location line="+6"/>
        <source>保留实拍场景文字 (SCENE)
    店铺招牌、路牌、宣传海报、书本、屏幕、标牌</source>
        <translation>実写シーン内のテキストを保持 (SCENE)
    店の看板、道路標識、ポスター、本、スクリーン、プレート</translation>
    </message>
    <message>
        <location line="+6"/>
        <source>保留无法判定文字 (UNKNOWN)
    分类器置信度不足的边界情况</source>
        <translation>判定不能のテキストを保持 (UNKNOWN)
    分類器の信頼度が不足している境界ケース</translation>
    </message>
    <message>
        <location line="+12"/>
        <source>💡 备选方案:</source>
        <translation>💡 代替案:</translation>
    </message>
    <message>
        <location line="+16"/>
        <source>🔄 重新自动检测</source>
        <translation>🔄 自動検出をやり直す</translation>
    </message>
    <message>
        <location line="+1"/>
        <source>重新采样分析当前视频并更新类型判定</source>
        <translation>この動画を再サンプリングして解析し、タイプ判定を更新します</translation>
    </message>
    <message>
        <location line="+1"/>
        <source>重置为预设默认值</source>
        <translation>プリセットの既定値にリセット</translation>
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
        <translation>字幕生成</translation>
    </message>
    <message>
        <location line="-216"/>
        <source>识别设置</source>
        <translation>認識設定</translation>
    </message>
    <message>
        <source>选择字幕语言。中文简体/繁体/英语/日语使用 PP-OCRv6 模型，其他语言自动回落 PP-OCRv5 多语言模型。</source>
        <translation type="obsolete">字幕の言語を選択します。簡体字中国語/繁体字中国語/英語/日本語は PP-OCRv6 モデルを使用し、その他の言語は自動的に PP-OCRv5 多言語モデルへフォールバックします。</translation>
    </message>
    <message>
        <location line="+38"/>
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
        <location line="+5"/>
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
        <translation>デバッグモード</translation>
    </message>
    <message>
        <location line="+1"/>
        <source>可视化输出</source>
        <translation>可視化出力</translation>
    </message>
    <message>
        <location line="+1"/>
        <source>内存模式（实验性）</source>
        <translation>メモリモード（実験的）</translation>
    </message>
    <message>
        <location line="+1"/>
        <source>保存中间 JSON</source>
        <translation>中間 JSON を保存</translation>
    </message>
    <message>
        <location line="+1"/>
        <source>按时间分片并行（可选）</source>
        <translation>時間分割並列（任意）</translation>
    </message>
    <message>
        <location line="+1"/>
        <source>合并 ROI（每帧只 OCR 一次）</source>
        <translation>ROI を統合（フレームごとに OCR 1 回）</translation>
    </message>
    <message>
        <location line="+2"/>
        <location line="+3"/>
        <source>秒</source>
        <translation>秒</translation>
    </message>
    <message>
        <location line="+18"/>
        <source>自动检测移动文字（轨迹字幕）</source>
        <translation>移動テキストを自動検出（軌跡字幕）</translation>
    </message>
    <message>
        <location line="+7"/>
        <source>无需手动勾选「写入画面位置标签」：开始识别时在每个 ROI 范围内采样 OCR，若文字行心位移超过移动门限（24px），自动对该区域走移动文字轨迹管线并抑制静态碎片事件。检测成本与采样密度（0.5 秒/帧）成正比，建议 ROI 尽量圈紧移动文字。</source>
        <translation>「画面位置タグを書き込む」を手動でチェックする必要はありません:認識開始時に各 ROI 範囲をサンプリング OCR し、テキスト行中心の移動がしきい値（24px）を超えると、その領域を自動的に移動テキスト軌跡パイプラインに回し、静的な断片イベントを抑制します。検出コストはサンプリング密度（0.5 秒/フレーム）に比例するため、ROI は移動テキストにぴったり合わせることを推奨します。</translation>
    </message>
    <message>
        <location line="+10"/>
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
        <translation>自動</translation>
    </message>
    <message>
        <location line="+4"/>
        <source>把长视频按时间切成多个窗口，用多个进程并行识别（与「按时间分片并行」的线程桶不同）。「自动」按 CPU 核数与可用内存决定；仅 CPU 模式生效，短视频自动走单进程。每个并行进程约占 600MB 内存。</source>
        <translation>長動画を時間軸で複数のウィンドウに分割し、複数のプロセスで並列認識します（「時間分割並列」のスレッド方式とは異なります）。「自動」はCPUコア数と空きメモリから決定します。CPUモードのみ有効で、短い動画は自動的に単一プロセスになります。並列プロセス1つにつき約600MBのメモリを使用します。</translation>
    </message>
    <message>
        <location line="+25"/>
        <source>半自动：按字幕颜色跳过疑似无字帧（默认关，需预览并确认后才生效）</source>
        <translation>半自動：字幕の色で無文字と思われるフレームをスキップ（既定オフ。プレビューして確認した後に有効）</translation>
    </message>
    <message>
        <location line="+7"/>
        <source>在阶段一抽样 ROI 区间内若干帧并在当前画面上自动标定 HSV。仅当预览结果满意并在对话框中点击「采用」后，才会在本轮 OCR 启用；可随时关闭恢复默认逻辑。若预览不满意或选择「不采用」，请保持勾选关闭或未确认——程序将按原版流程输出全部 ROI 帧。</source>
        <translation>ステージ 1 で ROI 区間内のいくつかのフレームをサンプリングし、現在の画面上で HSV を自動較正します。プレビュー結果に満足してダイアログで「採用」をクリックした場合にのみ、今回の OCR で有効になります。いつでもオフにして既定のロジックに戻せます。プレビューに不満がある場合や「不採用」を選んだ場合は、チェックを外すか未確認のままにしてください——プログラムは通常どおりすべての ROI フレームを出力します。</translation>
    </message>
    <message>
        <location line="+8"/>
        <source>预览检测效果…</source>
        <translation>検出結果をプレビュー…</translation>
    </message>
    <message>
        <location line="+16"/>
        <source>DeepSeek 字幕润色</source>
        <translation>DeepSeek 字幕ポリッシュ</translation>
    </message>
    <message>
        <location line="+2"/>
        <source>兼容 OpenAI 的接口。默认提供方为 DeepSeek，会自动填充 Base URL；当你输入 API Key 后，应用会调用 /v1/models 拉取模型列表（也可手动编辑模型 ID）。</source>
        <translation>OpenAI 互換のインターフェース。既定のプロバイダーは DeepSeek で、Base URL は自動入力されます。API Key を入力すると /v1/models を呼び出してモデル一覧を取得します（モデル ID は手動編集も可能）。</translation>
    </message>
    <message>
        <location line="+7"/>
        <source>DeepSeek 合并碎片字幕（选择最完整文本并合并时间范围）</source>
        <translation>DeepSeek 断片字幕のマージ（最も完全なテキストを選択し時間範囲を統合）</translation>
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
        <translation>LLM プロバイダー</translation>
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
        <translation>カスタム（Base URL を手動入力）</translation>
    </message>
    <message>
        <location line="+8"/>
        <source>模型 ID（可从 API 自动拉取）</source>
        <translation>モデル ID（API から自動取得できます）</translation>
    </message>
    <message>
        <location line="+4"/>
        <source>刷新模型列表</source>
        <translation>モデル一覧を更新</translation>
    </message>
    <message>
        <location line="+1"/>
        <location line="+880"/>
        <source>使用 API Key 和 Base URL 拉取 /v1/models。</source>
        <translation>API Key と Base URL で /v1/models を取得します。</translation>
    </message>
    <message>
        <location line="-878"/>
        <source>清除已存密钥</source>
        <translation>保存済みキーを削除</translation>
    </message>
    <message>
        <location line="+2"/>
        <source>从本机配置中删除已保存的 API Key（输入框会清空）。Base URL 与模型仍会保留。</source>
        <translation>このマシンの設定から保存済みの API Key を削除します（入力欄はクリアされます）。Base URL とモデルは保持されます。</translation>
    </message>
    <message>
        <location line="+7"/>
        <source>DeepSeek 合并策略复核（按更合适的阈值重新合并）</source>
        <translation>DeepSeek マージ戦略レビュー（より適切なしきい値で再マージ）</translation>
    </message>
    <message>
        <location line="+36"/>
        <source>AI 翻译（可选）</source>
        <translation>AI 翻訳（任意）</translation>
    </message>
    <message>
        <location line="+4"/>
        <source>把识别出的字幕行交给大模型翻译成目标语言（在润色之后、写出之前执行）。提供方、API Key、Base URL 与模型复用上方「大模型润色」区的配置；失败时自动降级到 VLM 兜底，全部失败保留原文。</source>
        <translation>認識した字幕行を大規模言語モデルで目標言語へ翻訳します（ポリッシュの後、書き出しの前に実行）。プロバイダー・API Key・Base URL・モデルは上の「大規模モデルによる字幕ポリッシュ」欄の設定を再利用します。失敗した場合は自動的に VLM フォールバックへ切り替わり、すべて失敗した場合は原文を保持します。</translation>
    </message>
    <message>
        <location line="+33"/>
        <source>使用上方「大模型润色」的提供方、API Key、Base URL 与模型。</source>
        <translation>上の「大規模モデルによる字幕ポリッシュ」欄のプロバイダー・API Key・Base URL・モデルを使用します。</translation>
    </message>
    <message>
        <source>把识别出的字幕行交给大模型翻译成目标语言（在润色之后、写出之前执行）。云端提供方复用「大模型润色」区已保存的 API Key；某一级未配置或失败时自动降级到下一级，全部失败保留原文。</source>
        <translation type="vanished">認識した字幕行を大規模言語モデルで目標言語へ翻訳します（ポリッシュの後、書き出しの前に実行）。クラウド プロバイダーは「大規模モデルによる字幕ポリッシュ」欄に保存済みの API Key を再利用します。未設定または失敗した段は自動的に次の段へフォールバックし、すべて失敗した場合は原文を保持します。</translation>
    </message>
    <message>
        <location line="-21"/>
        <source>目标语言：</source>
        <translation>翻訳先の言語：</translation>
    </message>
    <message>
        <source>翻译提供方：</source>
        <translation type="vanished">翻訳プロバイダー：</translation>
    </message>
    <message>
        <source>云端 API：OpenAI 兼容端点（默认 DeepSeek）；本地 Sakura：本地部署的 Sakura 日中翻译模型（OpenAI 兼容 /v1 端点）；VLM 兜底：复用 VLM_REFINE_* 环境变量，零配置。</source>
        <translation type="vanished">クラウド API: OpenAI 互換エンドポイント（既定は DeepSeek）。ローカル Sakura: ローカル配置の Sakura 日中翻訳モデル（OpenAI 互換 /v1 エンドポイント）。VLM フォールバック: VLM_REFINE_* 環境変数を再利用、設定不要。</translation>
    </message>
    <message>
        <source>云端 API</source>
        <translation type="vanished">クラウド API</translation>
    </message>
    <message>
        <source>本地 Sakura</source>
        <translation type="vanished">ローカル Sakura</translation>
    </message>
    <message>
        <source>VLM 兜底</source>
        <translation type="vanished">VLM フォールバック</translation>
    </message>
    <message>
        <source>API Base URL（云端 / Sakura 共用）</source>
        <translation type="vanished">API Base URL（クラウド / Sakura 共通）</translation>
    </message>
    <message>
        <source>模型名（如 deepseek-chat / sakura-14b）</source>
        <translation type="vanished">モデル名（例: deepseek-chat / sakura-14b）</translation>
    </message>
    <message>
        <location line="+31"/>
        <source>术语表 JSON：</source>
        <translation>用語集 JSON：</translation>
    </message>
    <message>
        <location line="+3"/>
        <source>可选。JSON 文件（{&quot;术语&quot;: &quot;译名&quot;}），译文中术语强制一致；文件缺失或格式错误时自动忽略。</source>
        <translation>任意。JSON ファイル（{&quot;用語&quot;: &quot;訳語&quot;}）で訳文の用語を強制的に一致させます。ファイルが欠落しているか不正な形式の場合は自動的に無視されます。</translation>
    </message>
    <message>
        <location line="+20"/>
        <source>上下文行数：</source>
        <translation>コンテキスト行数：</translation>
    </message>
    <message>
        <location line="+6"/>
        <source>最大行长：</source>
        <translation>最大行長：</translation>
    </message>
    <message>
        <location line="+23"/>
        <source>字体识别（可选）</source>
        <translation>フォント識別（任意）</translation>
    </message>
    <message>
        <location line="+4"/>
        <source>对识别出的字幕行做字体识别（本地计算），输出 Top-N 候选并查询许可类别；识别完成后可在复核对话框中逐条确认字体库更新建议。</source>
        <translation>認識した字幕行のフォントを識別（ローカル計算）し、Top-N 候補とライセンス区分を照会します。識別完了後、レビューダイアログでフォントデータベース更新提案を 1 件ずつ確認できます。</translation>
    </message>
    <message>
        <location line="+11"/>
        <source>Top-N 候选数：</source>
        <translation>Top-N 候補数：</translation>
    </message>
    <message>
        <location line="+6"/>
        <source>每组字幕字块输出的候选字体数量。</source>
        <translation>グループごとに出力する候補フォントの数。</translation>
    </message>
    <message>
        <location line="+6"/>
        <source>置信度阈值：</source>
        <translation>信頼度しきい値：</translation>
    </message>
    <message>
        <location line="+6"/>
        <source>低于阈值的候选只展示、不参与自动替换；0 = 不标记。</source>
        <translation>しきい値未満の候補は表示のみで自動置換されません。0 = マークしない。</translation>
    </message>
    <message>
        <location line="+17"/>
        <source>字体库目录：</source>
        <translation>フォントディレクトリ：</translation>
    </message>
    <message>
        <location line="+4"/>
        <source>留空 = 仅系统字体目录</source>
        <translation>空欄 = システムフォントディレクトリのみ</translation>
    </message>
    <message>
        <location line="+3"/>
        <source>额外扫描的字体文件目录（可选）；留空 = 仅扫描系统字体目录。</source>
        <translation>追加でスキャンするフォントファイルのディレクトリ（任意）。空欄 = システムフォントディレクトリのみスキャン。</translation>
    </message>
    <message>
        <location line="+18"/>
        <source>fonts.db 路径：</source>
        <translation>fonts.db パス：</translation>
    </message>
    <message>
        <location line="+4"/>
        <source>留空 = 默认字体库路径</source>
        <translation>空欄 = 既定のフォントデータベースパス</translation>
    </message>
    <message>
        <location line="+3"/>
        <source>字体许可/映射查询库；留空使用默认 XDG 数据目录下的 fonts.db。</source>
        <translation>フォントライセンス／マッピング照会用データベース。空欄 = 既定の XDG データディレクトリの fonts.db。</translation>
    </message>
    <message>
        <location line="+46"/>
        <source>完整设置</source>
        <translation>詳細設定</translation>
    </message>
    <message>
        <location line="+3"/>
        <source>显示全部设置（文字来源过滤、绘制模式、引擎详情）。已调整的选项保持不变。</source>
        <translation>すべての設定（文字ソースフィルタ、描画モード、エンジン詳細）を表示します。調整済みの選択肢は変わりません。</translation>
    </message>
    <message>
        <location line="+5"/>
        <source>简洁界面</source>
        <translation>シンプル表示</translation>
    </message>
    <message>
        <location line="+3"/>
        <source>返回一键简洁视图，隐藏高级设置。所有选项保持不变。</source>
        <translation>ワンクリックのシンプル表示に戻り、詳細設定を隠します。すべての選択肢は変わりません。</translation>
    </message>
    <message>
        <location line="+216"/>
        <source>🔒 文字来源过滤已关闭，将保留所有识别到的文字。</source>
        <translation>🔒 テキストソースフィルタはオフです。認識したすべてのテキストを保持します。</translation>
    </message>
    <message>
        <location line="+4"/>
        <source>🔄 文字来源过滤已启用，加载视频后将自动分析...</source>
        <translation>🔄 テキストソースフィルタはオンです。動画を読み込むと自動解析を開始します...</translation>
    </message>
    <message>
        <location line="+81"/>
        <source>颜色门控：已关闭（默认）。</source>
        <translation>カラーゲート：オフ（既定）。</translation>
    </message>
    <message>
        <location line="+2"/>
        <source>颜色门控：已勾选，尚未确认。请点击「预览检测效果」并在满意时选择「采用」。</source>
        <translation>カラーゲート：チェック済み、未確認。「検出結果をプレビュー」をクリックし、満足できたら「採用」を選んでください。</translation>
    </message>
    <message>
        <location line="+4"/>
        <source>颜色门控：已确认，将用于下一轮「字幕 OCR 识别」阶段一截取。</source>
        <translation>カラーゲート：確認済み。次の「字幕 OCR 認識」のステージ 1 で使用されます。</translation>
    </message>
    <message>
        <location line="+165"/>
        <source>选择术语表 JSON 文件</source>
        <translation>用語集 JSON ファイルを選択</translation>
    </message>
    <message>
        <location line="+2"/>
        <source>术语表 JSON (*.json)</source>
        <translation>用語集 JSON (*.json)</translation>
    </message>
    <message>
        <location line="+84"/>
        <source>选择字体库目录</source>
        <translation>フォントディレクトリを選択</translation>
    </message>
    <message>
        <location line="+9"/>
        <source>选择 fonts.db 文件</source>
        <translation>fonts.db ファイルを選択</translation>
    </message>
    <message>
        <location line="+2"/>
        <source>SQLite 字体库 (*.db)</source>
        <translation>SQLite フォントデータベース (*.db)</translation>
    </message>
    <message>
        <location line="+185"/>
        <source>（无可用引擎）</source>
        <translation>（利用可能なエンジンなし）</translation>
    </message>
    <message>
        <location line="+3"/>
        <source>⚠ 未检测到可用 OCR 引擎</source>
        <translation>⚠ 使用可能な OCR エンジンが検出されません</translation>
    </message>
    <message>
        <location line="+7"/>
        <source>✅ 已就绪</source>
        <translation>✅ 準備完了</translation>
    </message>
    <message>
        <location line="+89"/>
        <source>🔄 正在分析视频类型...</source>
        <translation>🔄 動画タイプを解析中...</translation>
    </message>
    <message>
        <location line="+98"/>
        <location line="+18"/>
        <source>手动模式（已自定义）</source>
        <translation>手動モード（カスタマイズ済み）</translation>
    </message>
    <message>
        <location line="+76"/>
        <source>已恢复上次保存的手动设置</source>
        <translation>前回保存した手動設定を復元しました</translation>
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
        <location line="-1207"/>
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
        <translation>DeepSeek / LLM 処理の進行状況</translation>
    </message>
    <message>
        <location line="+12"/>
        <source>启用 DeepSeek 后，此处显示处理进度，以及各批次润色前后的字幕对比、碎片合并候选与结果、策略复核说明等，便于核对模型具体改动了哪些字。</source>
        <translation>DeepSeek を有効にすると、ここに処理の進行状況、バッチごとのポリッシュ前後の字幕比較、断片マージの候補と結果、戦略レビューの説明などが表示され、モデルがどの文字をどう変更したかを確認できます。</translation>
    </message>
    <message>
        <location line="+9"/>
        <source>清空日志</source>
        <translation>ログをクリア</translation>
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
        <location filename="../components/file_operations.py" line="+25"/>
        <source>文件操作（支持拖放）</source>
        <translation>ファイル操作（ドラッグ＆ドロップ対応）</translation>
    </message>
    <message>
        <location line="+6"/>
        <source>加载视频</source>
        <translation>動画を読み込む</translation>
    </message>
    <message>
        <location line="+1"/>
        <source>保存 ROI 配置</source>
        <translation>ROI 設定を保存</translation>
    </message>
    <message>
        <location line="+1"/>
        <source>加载 ROI 配置</source>
        <translation>ROI 設定を読み込む</translation>
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
    <message>
        <location line="+21"/>
        <source>语言</source>
        <translation>言語</translation>
    </message>
    <message>
        <location line="+3"/>
        <source>自动（跟随系统）</source>
        <translation>自動（システムに従う）</translation>
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
        <translation type="unfinished">英語</translation>
    </message>
    <message>
        <location line="+1"/>
        <source>日本語</source>
        <translation type="unfinished">日本語</translation>
    </message>
    <message>
        <location line="+23"/>
        <location line="+20"/>
        <source>切换语言</source>
        <translation>言語の切り替え</translation>
    </message>
    <message>
        <location line="-19"/>
        <source>语言设置将在重启应用后生效。要立即重启吗？</source>
        <translation>言語の設定はアプリ再起動後に有効になります。今すぐ再起動しますか？</translation>
    </message>
    <message>
        <location line="+20"/>
        <source>无法自动重启，请手动重启应用以应用新语言。</source>
        <translation>自動再起動できませんでした。新しい言語を適用するにはアプリを手動で再起動してください。</translation>
    </message>
</context>
<context>
    <name>FontMapReviewDialog</name>
    <message>
        <location filename="../components/font_map_review_dialog.py" line="+41"/>
        <source>低置信度，建议人工确认</source>
        <translation>信頼度が低いため、手動確認を推奨</translation>
    </message>
    <message>
        <location line="+2"/>
        <source>规则未能解析该行</source>
        <translation>ルールでこの行を解析できませんでした</translation>
    </message>
    <message>
        <location line="+2"/>
        <source>疑似幻觉字体名（归一化后不是原文行子串）</source>
        <translation>ハルシネーション疑いのフォント名（正規化後に原文行の部分文字列ではありません）</translation>
    </message>
    <message>
        <location line="+3"/>
        <source>字体名未命中本地词表</source>
        <translation>フォント名がローカル語彙に見つかりません</translation>
    </message>
    <message>
        <location line="+2"/>
        <source>LLM 调用失败（有界退避耗尽），本行未结构化</source>
        <translation>LLM 呼び出しが失敗しました（有界バックオフを使い果たし）。この行は構造化されませんでした</translation>
    </message>
    <message>
        <location line="+3"/>
        <source>LLM 输出不符合约定格式，本行未结构化</source>
        <translation>LLM 出力が約定の形式に一致せず、この行は構造化されませんでした</translation>
    </message>
    <message>
        <location line="+3"/>
        <source>未配置 API Key，本行未结构化</source>
        <translation>API Key 未設定のため、この行は構造化されませんでした</translation>
    </message>
    <message>
        <location line="+2"/>
        <source>空行</source>
        <translation>空行</translation>
    </message>
    <message>
        <location line="+2"/>
        <source>注释行</source>
        <translation>コメント行</translation>
    </message>
    <message>
        <location line="+34"/>
        <source>第 {0} 行：{1} → {2}</source>
        <translation>{0} 行目：{1} → {2}</translation>
    </message>
    <message>
        <location line="+4"/>
        <source>第 {0} 行：{1} →（负映射）</source>
        <translation>{0} 行目：{1} →（負マッピング）</translation>
    </message>
    <message>
        <location line="+3"/>
        <source>第 {0} 行：{1}</source>
        <translation>{0} 行目：{1}</translation>
    </message>
    <message>
        <location line="+7"/>
        <source>原文：{0}</source>
        <translation>原文：{0}</translation>
    </message>
    <message>
        <location line="+3"/>
        <source>建议：{0} → {1}</source>
        <translation>提案：{0} → {1}</translation>
    </message>
    <message>
        <location line="+4"/>
        <source>建议：{0} 无日文本家对应</source>
        <translation>提案：{0} に日本語本家の対応なし</translation>
    </message>
    <message>
        <location line="+4"/>
        <source>类型：{0} ｜ 置信度：{1} ｜ 来源：{2} ｜ 文件：{3}</source>
        <translation>種別：{0} ｜ 信頼度：{1} ｜ 取得元：{2} ｜ ファイル：{3}</translation>
    </message>
    <message>
        <location line="+5"/>
        <source>理由：{0}</source>
        <translation>理由：{0}</translation>
    </message>
    <message>
        <location line="+3"/>
        <source>（该条目不写库；如需修正请在源表修订后重跑导入）</source>
        <translation>（この項目は DB に書き込まれません。修正するにはソース表を改修してからインポートを再実行してください）</translation>
    </message>
    <message>
        <location line="+11"/>
        <source>字体映射复核</source>
        <translation>フォントマッピングレビュー</translation>
    </message>
    <message>
        <location line="+18"/>
        <source>无待复核记录。</source>
        <translation>レビュー待ちのレコードはありません。</translation>
    </message>
    <message>
        <location line="+11"/>
        <source>应用</source>
        <translation>適用</translation>
    </message>
    <message>
        <location line="+2"/>
        <source>取消</source>
        <translation>キャンセル</translation>
    </message>
</context>
<context>
    <name>FontUpdateReviewDialog</name>
    <message>
        <location filename="../components/font_update_review_dialog.py" line="+42"/>
        <source>新增字体记录</source>
        <translation>フォントレコードを追加</translation>
    </message>
    <message>
        <location line="+3"/>
        <source>映射修正</source>
        <translation>マッピング修正</translation>
    </message>
    <message>
        <location line="+3"/>
        <source>新增映射</source>
        <translation>マッピングを追加</translation>
    </message>
    <message>
        <location line="+8"/>
        <source>模型推断（未经人工确认）</source>
        <translation>モデル推論（人手未確認）</translation>
    </message>
    <message>
        <location line="+3"/>
        <source>字形重排证据</source>
        <translation>グリフ再ランキング証拠</translation>
    </message>
    <message>
        <location line="+35"/>
        <source>{0}：{1} → {2}</source>
        <translation>{0}：{1} → {2}</translation>
    </message>
    <message>
        <location line="+5"/>
        <source>{0}：{1}</source>
        <translation>{0}：{1}</translation>
    </message>
    <message>
        <location line="+3"/>
        <source>{0}：{1}（不可采纳）</source>
        <translation>{0}：{1}（採用不可）</translation>
    </message>
    <message>
        <location line="+9"/>
        <source>建议：{0} → {1}</source>
        <translation>提案：{0} → {1}</translation>
    </message>
    <message>
        <location line="+5"/>
        <source>建议：新增记录 {0}（许可类别 {1}）</source>
        <translation>提案：レコード {0} を追加（ライセンス区分 {1}）</translation>
    </message>
    <message>
        <location line="+7"/>
        <source>类型：{0} ｜ 置信度：{1} ｜ 依据：{2} ｜ 方法：{3}</source>
        <translation>種別：{0} ｜ 信頼度：{1} ｜ 根拠：{2} ｜ 方法：{3}</translation>
    </message>
    <message>
        <location line="+6"/>
        <source>理由：{0}</source>
        <translation>理由：{0}</translation>
    </message>
    <message>
        <location line="+4"/>
        <source>（该条目无建议值，仅展示）</source>
        <translation>（この項目は提案値がなく、表示のみ）</translation>
    </message>
    <message>
        <location line="+11"/>
        <source>字体库更新建议</source>
        <translation>フォントデータベース更新提案</translation>
    </message>
    <message>
        <location line="+13"/>
        <source>勾选 = 采纳并写入用户覆盖层（人工确认语义）；不勾 = 否决。未经确认的模型推断记录不参与自动替换。</source>
        <translation>チェック = 採用してユーザーオーバーレイ層に書き込む（人手確認意味論）。未チェック = 却下。未確認のモデル推論レコードは自動置換に参加しません。</translation>
    </message>
    <message>
        <location line="+13"/>
        <source>无待复核的更新建议。</source>
        <translation>レビュー待ちの更新提案はありません。</translation>
    </message>
    <message>
        <location line="+12"/>
        <source>应用</source>
        <translation>適用</translation>
    </message>
    <message>
        <location line="+2"/>
        <source>取消</source>
        <translation>キャンセル</translation>
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
        <translation>ログと進行状況</translation>
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
        <location filename="../components/roi_definition.py" line="+45"/>
        <source>ROI 定义</source>
        <translation>ROI 定義</translation>
    </message>
    <message>
        <location line="+16"/>
        <location line="+23"/>
        <source>后退 1 帧（短按）/ 连续（长按）</source>
        <translation>1 フレーム戻る（短押し）／連続（長押し）</translation>
    </message>
    <message>
        <location line="-18"/>
        <location line="+23"/>
        <source>前进 1 帧（短按）/ 连续（长按）</source>
        <translation>1 フレーム進む（短押し）／連続（長押し）</translation>
    </message>
    <message>
        <location line="-20"/>
        <location line="+23"/>
        <source>输入时间（时:分:秒.毫秒）或帧号</source>
        <translation>時刻（時:分:秒.ミリ秒）またはフレーム番号を入力</translation>
    </message>
    <message>
        <location line="-19"/>
        <source>开始时间：</source>
        <translation>開始時刻：</translation>
    </message>
    <message>
        <location line="+2"/>
        <source>设为开始时间</source>
        <translation>開始時刻に設定</translation>
    </message>
    <message>
        <location line="+21"/>
        <source>结束时间：</source>
        <translation>終了時刻：</translation>
    </message>
    <message>
        <location line="+2"/>
        <source>设为结束时间</source>
        <translation>終了時刻に設定</translation>
    </message>
    <message>
        <location line="+5"/>
        <source>按文字/描边/阴影颜色限制 OCR（不匹配像素将被遮罩）</source>
        <translation>文字／縁取り／影の色で OCR を制限（一致しないピクセルをマスク）</translation>
    </message>
    <message>
        <location line="+3"/>
        <source>将与所选颜色不接近的像素在 OCR 前置为白色，减少字幕笔画之外的误检。</source>
        <translation>選択した色に近くないピクセルを OCR 前に白へ置き換え、字幕の線以外の誤検出を減らします。</translation>
    </message>
    <message>
        <location line="+7"/>
        <source>OCR 前对 ROI 轻微模糊（降低锯齿/噪声）</source>
        <translation>OCR 前に ROI を軽くぼかす（ギザつき／ノイズを低減）</translation>
    </message>
    <message>
        <location line="+2"/>
        <source>在 OCR 前对 ROI 裁剪图应用轻微高斯模糊。对噪声大/压缩重的字幕更有帮助。</source>
        <translation>OCR 前に ROI の切り抜き画像に軽いガウシアンぼかしを適用します。ノイズが多い・圧縮劣化が激しい字幕に効果的です。</translation>
    </message>
    <message>
        <location line="+8"/>
        <source>淡入/淡出微调（在检测到文字边界附近逐帧 OCR，找更精确的起止时间）</source>
        <translation>フェードイン／アウトの微調整（文字の境界付近をフレーム単位で OCR し、より正確な開始・終了時刻を特定）</translation>
    </message>
    <message>
        <location line="+7"/>
        <source>开启后，会只在文字出现/消失的边界附近逐帧 OCR 用于校准时间；ROI 其余部分仍可使用跳帧优化。</source>
        <translation>オンにすると、文字の出現／消失境界付近のみフレーム単位で OCR して時刻を較正します。ROI のそれ以外はフレームスキップ最適化を引き続き利用できます。</translation>
    </message>
    <message>
        <location line="+8"/>
        <source>写入画面位置标签（\pos \frz \frx \fry，多边形自动计算位置与倾角）</source>
        <translation>画面位置タグを書き出す（\pos \frz \frx \fry。多角形から位置と傾きを自動計算）</translation>
    </message>
    <message>
        <location line="+72"/>
        <source>仅作用于场景文字（画面文字）事件；所选模式不可用时自动回退（空白区→遮罩→外置；仅遮罩→外置）。「仅遮罩」把遮罩下方的识别文本写成 Comment 注释行（播放器不渲染），便于在遮罩上自行排版覆写（如绘制译文或 \p 矢量字）。</source>
        <translation>シーン文字（画面内テキスト）イベントのみに適用されます。選択したモードが利用できない場合は自動でフォールバックします（空白領域→マスク→外側ボックス；マスクのみ→外側ボックス）。「マスクのみ」はマスク下の認識テキストを Comment 行（再生時は非表示）として出力するため、マスクの上に翻訳や \p ベクター文字などを手動で組版し直せます。</translation>
    </message>
    <message>
        <source>开启后，该 ROI 识别出的字幕事件将附带位置与旋转标签：多边形 ROI 自动计算中心点(\pos)与长边倾角(\frz)；\frx/\fry 保存为 0，可手动微调透视。适合实拍场景文字的原位重铺。</source>
        <translation type="vanished">有効にすると、この ROI から書き出される字幕イベントに位置と回転のタグが付きます。多角形 ROI では中心点(\pos)と長辺の傾き(\frz)を自動計算し、\frx/\fry は 0 として保存されるため手動調整が可能です。実写シーン文字の原位置再配置に適します。</translation>
    </message>
    <message>
        <location line="-65"/>
        <source>开启后，该 ROI 识别出的字幕事件将附带位置与旋转标签：多边形 ROI 自动计算中心点(\pos)与长边倾角(\frz)；\frx/\fry 保存为 0，可手动微调透视。适合实拍场景文字的原位重铺。四点多边形 ROI 会走移动文字轨迹管线：逐帧跟踪平面并合成 \move 运动字幕，静态标签跟不动的画面由轨迹跟随。</source>
        <translation>有効にすると、この ROI の字幕イベントに位置と回転のタグが付与されます。多角形 ROI は中心点(\pos)と長辺の傾き(\frz)を自動計算し、\frx/\fry は 0 で保存され手動調整できます。実写の画面内文字を原位置に再配置するのに適します。4 点多角形 ROI は移動文字軌跡パイプラインで処理され、平面を逐フレーム追跡して \move の動く字幕を合成します。静的タグでは追従できない動きも軌跡が追従します。</translation>
    </message>
    <message>
        <location line="+12"/>
        <source>亮度自适应（轨迹字幕跟随屏幕明暗）</source>
        <translation>輝度自動適応（軌跡字幕が画面の明暗に追従）</translation>
    </message>
    <message>
        <location line="+8"/>
        <source>需勾选「写入画面位置标签」且 ROI 为四点多边形（此时走移动文字轨迹管线）。开启后逐帧测量文字平面亮度，为轨迹事件追加 \1c/\alpha \t 标签链，字幕颜色与透明度忠实跟随屏幕变暗/变亮（如手机息屏）。默认关闭。</source>
        <translation>「画面位置タグを書き込む」にチェックがあり、かつ 4 点多角形 ROI の場合（軌跡パイプラインが有効）に使用可能。文字平面の輝度を逐フレーム測定し、軌跡イベントに \1c/\alpha \t タグチェーンを追加して、字幕の色と不透明度が画面の暗転/明転（スマホ画面の消灯など）に忠実に追従します。既定はオフ。</translation>
    </message>
    <message>
        <location line="+14"/>
        <source>遮挡蒙版（\iclip，手部遮挡时不渲染字幕）</source>
        <translation>遮蔽マスク（\iclip、手で隠れた部分に字幕を描画しない）</translation>
    </message>
    <message>
        <location line="+8"/>
        <source>需勾选「写入画面位置标签」（轨迹模式）。开启后检测手部等遮挡物覆盖文字平面的帧，为受影响的事件追加 \iclip 逆向蒙版，字幕不再渲染到遮挡物上（遮挡结束时自动恢复显示）。默认关闭。</source>
        <translation>「画面位置タグを書き込む」（軌跡モード）のチェックが必要です。有効にすると、手などが文字平面を覆うフレームを検出し、該当イベントに \iclip 逆マスクを追加して、字幕が遮蔽物の上に描画されないようにします（遮蔽が終わると自動的に表示を再開）。既定はオフ。</translation>
    </message>
    <message>
        <location line="+15"/>
        <source>叠加（默认）</source>
        <translation>重ねて表示（既定）</translation>
    </message>
    <message>
        <location line="+1"/>
        <source>遮罩原文字</source>
        <translation>元の文字をマスク</translation>
    </message>
    <message>
        <location line="+1"/>
        <source>仅遮罩（供排版覆写）</source>
        <translation>マスクのみ（手動組版用）</translation>
    </message>
    <message>
        <location line="+1"/>
        <source>外置展示框</source>
        <translation>外側ボックスで表示</translation>
    </message>
    <message>
        <location line="+1"/>
        <source>空白区放置</source>
        <translation>空白領域に配置</translation>
    </message>
    <message>
        <source>仅作用于场景文字（画面文字）事件；所选模式不可用时按 空白区→遮罩→外置 自动回退。</source>
        <translation type="vanished">シーン文字（画面内テキスト）イベントのみに適用されます。選択したモードが利用できない場合は 空白領域→マスク→外側ボックス の順に自動でフォールバックします。</translation>
    </message>
    <message>
        <location line="+12"/>
        <source>场景文字显示：</source>
        <translation>シーン文字表示：</translation>
    </message>
    <message>
        <location line="+10"/>
        <source>自动（跟随全局）</source>
        <translation>自動（グローバル設定に従う）</translation>
    </message>
    <message>
        <location line="+1"/>
        <source>中文简体</source>
        <translation type="unfinished">簡体字中国語</translation>
    </message>
    <message>
        <location line="+1"/>
        <source>中文繁體</source>
        <translation>繁体字中国語</translation>
    </message>
    <message>
        <location line="+1"/>
        <source>English</source>
        <translation type="unfinished">英語</translation>
    </message>
    <message>
        <location line="+1"/>
        <source>日本語</source>
        <translation type="unfinished">日本語</translation>
    </message>
    <message>
        <location line="+1"/>
        <source>한국어</source>
        <translation type="unfinished">韓国語</translation>
    </message>
    <message>
        <location line="+1"/>
        <source>Русский</source>
        <translation type="unfinished">ロシア語</translation>
    </message>
    <message>
        <location line="+1"/>
        <source>Français</source>
        <translation type="unfinished">フランス語</translation>
    </message>
    <message>
        <location line="+1"/>
        <source>Deutsch</source>
        <translation type="unfinished">ドイツ語</translation>
    </message>
    <message>
        <location line="+1"/>
        <source>Italiano</source>
        <translation type="unfinished">イタリア語</translation>
    </message>
    <message>
        <location line="+1"/>
        <source>Español</source>
        <translation type="unfinished">スペイン語</translation>
    </message>
    <message>
        <location line="+1"/>
        <source>Português</source>
        <translation type="unfinished">ポルトガル語</translation>
    </message>
    <message>
        <location line="+1"/>
        <source>العربية</source>
        <translation type="unfinished">アラビア語</translation>
    </message>
    <message>
        <location line="+4"/>
        <source>该 ROI 单独使用的 OCR 识别语言；默认「自动」跟随控制面板的全局「识别语言」。双语字幕请给每种语言各画一个 ROI 并分别指定语言（如繁中行设「中文繁體」、日语行设「日本語」），各 ROI 用各自的识别模型，避免单一模型漏认另一种文字。注意：每个不同语言会多加载一份识别模型，内存占用相应增加；RapidOCR 引擎不区分语言，此设置仅对 PaddleOCR 生效。</source>
        <translation>この ROI 専用の OCR 認識言語です。デフォルトの「自動」はコントロールパネルのグローバル「認識言語」に従います。二言語字幕では言語ごとに ROI を描き、それぞれの言語を指定してください（例: 繁体字中国語の行は「繁體中文」、日本語の行は「日本語」）。ROI ごとに専用の認識モデルを使うため、単一モデルでの文字取りこぼしを防げます。注意: 言語ごとに認識モデルを追加で読み込むため、メモリ使用量が増加します。RapidOCR エンジンは単一の多言語モデルのため言語を区別せず、この設定は PaddleOCR のみ有効です。</translation>
    </message>
    <message>
        <location line="+10"/>
        <source>识别语言：</source>
        <translation>認識言語：</translation>
    </message>
    <message>
        <location line="+13"/>
        <source>文字颜色…</source>
        <translation>文字色…</translation>
    </message>
    <message>
        <location line="+1"/>
        <source>描边颜色…</source>
        <translation>縁取り色…</translation>
    </message>
    <message>
        <location line="+1"/>
        <source>阴影颜色…</source>
        <translation>影の色…</translation>
    </message>
    <message>
        <location line="+11"/>
        <source>RGB 容差：</source>
        <translation>RGB 許容差：</translation>
    </message>
    <message>
        <location line="+5"/>
        <source>单通道距离 0–255；数值越大，包含越多相近色阶（抗锯齿/渐变更稳）。</source>
        <translation>チャネルごとの距離 0–255。大きいほど近い階調を広く含みます（アンチエイリアス／グラデーションに安定）。</translation>
    </message>
    <message>
        <location line="+3"/>
        <source>形态学：</source>
        <translation>モルフォロジー：</translation>
    </message>
    <message>
        <location line="+5"/>
        <source>闭运算核大小（奇数）；可在遮罩后连接断裂笔画。</source>
        <translation>クロージングカーネルサイズ（奇数）。マスク後に切れた線をつなぎます。</translation>
    </message>
    <message>
        <location line="+8"/>
        <source>添加新 ROI</source>
        <translation>新しい ROI を追加</translation>
    </message>
    <message>
        <location line="+1"/>
        <source>更新选中 ROI</source>
        <translation>選択中の ROI を更新</translation>
    </message>
    <message>
        <location line="+1"/>
        <source>删除选中 ROI</source>
        <translation>選択中の ROI を削除</translation>
    </message>
    <message>
        <location line="+81"/>
        <source>字幕文字颜色</source>
        <translation>字幕の文字色</translation>
    </message>
    <message>
        <location line="+10"/>
        <source>字幕描边颜色</source>
        <translation>字幕の縁取り色</translation>
    </message>
    <message>
        <location line="+10"/>
        <source>字幕阴影颜色</source>
        <translation>字幕の影の色</translation>
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
        <location filename="../components/roi_list.py" line="+17"/>
        <source>ROI 列表</source>
        <translation>ROI リスト</translation>
    </message>
    <message>
        <location line="+33"/>
        <source>ROI {}：帧[{}-{}] 时间[{} - {}]</source>
        <translation>ROI {}：フレーム[{}-{}] 時間[{} - {}]</translation>
    </message>
    <message>
        <location line="+3"/>
        <source>[颜色掩膜]</source>
        <translation>[カラーマスク]</translation>
    </message>
    <message>
        <location line="+2"/>
        <source>[模糊]</source>
        <translation>[ぼかし]</translation>
    </message>
    <message>
        <location line="+3"/>
        <source>[淡入淡出微调]</source>
        <translation>[フェード微調整]</translation>
    </message>
    <message>
        <location line="+2"/>
        <source>[自动过滤]</source>
        <translation>[自動フィルタ]</translation>
    </message>
    <message>
        <location line="+22"/>
        <source>复制</source>
        <translation>コピー</translation>
    </message>
    <message>
        <location line="+5"/>
        <source>粘贴到此项之后</source>
        <translation>この項目の後に貼り付け</translation>
    </message>
    <message>
        <location line="+4"/>
        <source>删除</source>
        <translation>削除</translation>
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
        <translation>末尾に貼り付け</translation>
    </message>
    <message>
        <location line="+12"/>
        <source>确认删除</source>
        <translation>削除の確認</translation>
    </message>
    <message>
        <location line="+1"/>
        <source>确定要删除 ROI {} 吗？</source>
        <translation>ROI {} を削除しますか？</translation>
    </message>
</context>
<context>
    <name>ScanReviewDialog</name>
    <message>
        <location filename="../components/scan_review_dialog.py" line="+60"/>
        <source>深度扫描复核</source>
        <translation>詳細スキャンの確認</translation>
    </message>
    <message>
        <location line="+23"/>
        <source>字幕带 ROI（自动检测的主字幕区）</source>
        <translation>字幕帯 ROI（自動検出されたメイン字幕領域）</translation>
    </message>
    <message>
        <location line="+6"/>
        <source>底部</source>
        <translation>下部</translation>
    </message>
    <message>
        <location line="+1"/>
        <source>顶部</source>
        <translation>上部</translation>
    </message>
    <message>
        <location line="+0"/>
        <source>中部</source>
        <translation>中央</translation>
    </message>
    <message>
        <location line="+3"/>
        <source>{0}  帧 [{1}-{2}]</source>
        <translation>{0}  フレーム [{1}-{2}]</translation>
    </message>
    <message>
        <location line="+7"/>
        <source>用所选字幕带替换现有 ROI 列表（否则追加）</source>
        <translation>選択した字幕帯で既存の ROI リストを置き換える（チェックなしは追加）</translation>
    </message>
    <message>
        <location line="+4"/>
        <source>未检测到字幕带。</source>
        <translation>字幕帯は検出されませんでした。</translation>
    </message>
    <message>
        <location line="+4"/>
        <source>疑似水印（恒定文本 + 恒定位置，勾选=识别时剔除）</source>
        <translation>疑わしい透かし（恒定的テキスト＋位置、チェック＝認識時に除去）</translation>
    </message>
    <message>
        <location line="+6"/>
        <source>「{0}」 出现率 {1:.0%}</source>
        <translation>「{0}」 出現率 {1:.0%}</translation>
    </message>
    <message>
        <location line="+10"/>
        <source>未检测到疑似水印。</source>
        <translation>疑わしい透かしは検出されませんでした。</translation>
    </message>
    <message>
        <location line="+4"/>
        <source>画面中部文字（场景字候选，勾选=导入为 ROI，全部保留）</source>
        <translation>画面中央のテキスト（シーン文字候補、チェック＝ROI として取り込み、すべて保持）</translation>
    </message>
    <message>
        <location line="+5"/>
        <source>「{0}」 出现 {1} 次（帧 {2}-{3}）</source>
        <translation>「{0}」 {1}回出現（フレーム {2}-{3}）</translation>
    </message>
    <message>
        <location line="+14"/>
        <source>（其余 {} 处低频文字未列出）</source>
        <translation>（その他 {}箇所の低頻度テキストは未表示）</translation>
    </message>
    <message>
        <location line="+5"/>
        <source>未检测到画面中部文字。</source>
        <translation>画面中央のテキストは検出されませんでした。</translation>
    </message>
    <message>
        <location line="+12"/>
        <source>应用</source>
        <translation>適用</translation>
    </message>
    <message>
        <location line="+1"/>
        <source>取消</source>
        <translation>キャンセル</translation>
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
        <location filename="../main_window/scan_control.py" line="+78"/>
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
        <location filename="../main_window/window.py" line="+95"/>
        <source>视频字幕 OCR 工具</source>
        <translation>動画字幕 OCR ツール</translation>
    </message>
    <message>
        <location line="+118"/>
        <source>正在深度扫描…</source>
        <translation>詳細スキャン中…</translation>
    </message>
    <message>
        <location line="+2"/>
        <source>正在检测字幕区域…</source>
        <translation>字幕領域を検出中…</translation>
    </message>
    <message>
        <location line="+57"/>
        <source>已通过拖放加载 ASS 模板：{}</source>
        <translation>ドラッグ＆ドロップで ASS テンプレートを読み込みました：{}</translation>
    </message>
    <message>
        <location filename="../main_window/video_playback.py" line="+19"/>
        <source>选择视频文件</source>
        <translation>動画ファイルを選択</translation>
    </message>
    <message>
        <location line="+2"/>
        <source>视频文件 (*.mp4 *.avi *.mov *.mkv)</source>
        <translation>動画ファイル (*.mp4 *.avi *.mov *.mkv)</translation>
    </message>
    <message>
        <location line="+8"/>
        <source>无法打开视频文件</source>
        <translation>動画ファイルを開けません</translation>
    </message>
    <message>
        <location line="+27"/>
        <source>视频字幕 OCR 工具 - {}</source>
        <translation>動画字幕 OCR ツール - {}</translation>
    </message>
    <message>
        <location line="+1"/>
        <source>视频已加载：{}</source>
        <translation>動画を読み込みました：{}</translation>
    </message>
    <message>
        <location line="+1"/>
        <source>分辨率：{}x{}，帧率：{:.2f}，总帧数：{}</source>
        <translation>解像度：{}x{}、FPS：{:.2f}、総フレーム数：{}</translation>
    </message>
    <message>
        <location line="+25"/>
        <source>已自动加载 ROI 配置：{}</source>
        <translation>ROI 設定を自動読み込みしました：{}</translation>
    </message>
    <message>
        <location line="+4"/>
        <source>自动加载 ROI 配置失败：{}</source>
        <translation>ROI 設定の自動読み込みに失敗：{}</translation>
    </message>
    <message>
        <location line="+92"/>
        <source>无效的时间/帧号输入：&apos;{}&apos;</source>
        <translation>無効な時刻／フレーム番号の入力：&apos;{}&apos;</translation>
    </message>
    <message>
        <location filename="../main_window/roi_editing.py" line="+40"/>
        <source>已添加新 ROI，帧范围：{}-{}</source>
        <translation>新しい ROI を追加しました。フレーム範囲：{}-{}</translation>
    </message>
    <message>
        <location line="+15"/>
        <source>已更新 ROI {}，新帧范围：{}-{}</source>
        <translation>ROI {} を更新しました。新しいフレーム範囲：{}-{}</translation>
    </message>
    <message>
        <location line="+20"/>
        <source>已删除 ROI {}</source>
        <translation>ROI {} を削除しました</translation>
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
        <location line="+40"/>
        <source>当前未选中任何 ROI：该开关只会写入之后「添加新 ROI」的条目；如需应用到已有 ROI，请先在列表中选中它再勾选。</source>
        <translation>ROI が選択されていません:このスイッチは今後「新規 ROI 追加」で作成する項目にのみ反映されます。既存の ROI に適用するには、先にリストで対象を選択してから切り替えてください。</translation>
    </message>
    <message>
        <location line="+29"/>
        <source>ROI {} 画面位置标签已{}</source>
        <translation>ROI {} の画面位置タグを{}</translation>
    </message>
    <message>
        <location line="+2"/>
        <location line="+20"/>
        <location line="+15"/>
        <source>开启</source>
        <translation>有効にしました</translation>
    </message>
    <message>
        <location line="-34"/>
        <location line="+20"/>
        <location line="+15"/>
        <source>关闭</source>
        <translation>無効にしました</translation>
    </message>
    <message>
        <location line="-18"/>
        <source>ROI {} 亮度自适应已{}</source>
        <translation>ROI {}: 輝度自動適応を{}</translation>
    </message>
    <message>
        <location line="+15"/>
        <source>ROI {} 遮挡蒙版已{}</source>
        <translation>ROI {}: 遮蔽マスクを{}</translation>
    </message>
    <message>
        <location line="+15"/>
        <source>ROI {} 场景文字显示已设为 {}</source>
        <translation>ROI {}: シーンテキスト表示を {} に設定</translation>
    </message>
    <message>
        <location line="+22"/>
        <source>ROI {} 识别语言已设为 {}</source>
        <translation>ROI {}: 認識言語を {} に設定</translation>
    </message>
    <message>
        <location line="+2"/>
        <source>自动（跟随全局）</source>
        <translation>自動（グローバル設定に従う）</translation>
    </message>
    <message>
        <location line="+251"/>
        <source>已在画面上调整 ROI {} 的区域</source>
        <translation>画面上で ROI {} の領域を調整しました</translation>
    </message>
    <message>
        <location filename="../main_window/pipeline_control.py" line="+157"/>
        <location line="+6"/>
        <location line="+10"/>
        <location line="+19"/>
        <location line="+24"/>
        <location line="+11"/>
        <location line="+215"/>
        <location line="+35"/>
        <location filename="../main_window/roi_config_io.py" line="-1"/>
        <location filename="../main_window/roi_editing.py" line="-163"/>
        <location filename="../main_window/source_config.py" line="+17"/>
        <location line="+12"/>
        <source>警告</source>
        <translation>警告</translation>
    </message>
    <message>
        <location filename="../main_window/roi_editing.py" line="+1"/>
        <source>开始时间不能晚于结束时间。</source>
        <translation>開始時刻を終了時刻より後にすることはできません。</translation>
    </message>
    <message>
        <location line="+63"/>
        <source>创建 ROI 条目时出错：{}</source>
        <translation>ROI 項目の作成中にエラー：{}</translation>
    </message>
    <message>
        <location line="+3"/>
        <source>创建 ROI 时发生错误：{}</source>
        <translation>ROI の作成中にエラーが発生しました：{}</translation>
    </message>
    <message>
        <location filename="../main_window/roi_config_io.py" line="-66"/>
        <source>保存 ROI 配置</source>
        <translation>ROI 設定を保存</translation>
    </message>
    <message>
        <location line="+2"/>
        <location line="+72"/>
        <source>JSON 文件 (*.json)</source>
        <translation>JSON ファイル (*.json)</translation>
    </message>
    <message>
        <location line="-63"/>
        <source>ROI 配置已保存到：{}</source>
        <translation>ROI 設定を保存しました：{}</translation>
    </message>
    <message>
        <location line="+4"/>
        <location line="+1"/>
        <source>保存 ROI 配置失败：{}</source>
        <translation>ROI 設定の保存に失敗：{}</translation>
    </message>
    <message>
        <location line="+33"/>
        <source>自动备份 ROI 配置失败（识别仍会继续）：{}</source>
        <translation>ROI 設定の自動バックアップに失敗しました（認識は続行されます）：{}</translation>
    </message>
    <message>
        <location line="+7"/>
        <source>为防意外中断，已自动备份 ROI 配置到：{}</source>
        <translation>予期しない中断に備え、ROI 設定を自動バックアップしました：{}</translation>
    </message>
    <message>
        <location line="+16"/>
        <source>加载 ROI 配置</source>
        <translation>ROI 設定を読み込む</translation>
    </message>
    <message>
        <location line="+27"/>
        <source>ROI 配置已从 {} 加载</source>
        <translation>ROI 設定を {} から読み込みました</translation>
    </message>
    <message>
        <location line="+4"/>
        <location line="+1"/>
        <source>加载 ROI 配置失败：{}</source>
        <translation>ROI 設定の読み込みに失敗：{}</translation>
    </message>
    <message>
        <location filename="../main_window/roi_editing.py" line="+104"/>
        <source>已将 ROI {} 复制到剪贴板。</source>
        <translation>ROI {} をクリップボードにコピーしました。</translation>
    </message>
    <message>
        <location line="+10"/>
        <source>已粘贴到 ROI {} 之后。</source>
        <translation>ROI {} の後に貼り付けました。</translation>
    </message>
    <message>
        <location line="+10"/>
        <source>已将 ROI 粘贴到列表末尾。</source>
        <translation>ROI をリストの末尾に貼り付けました。</translation>
    </message>
    <message>
        <location filename="../main_window/roi_config_io.py" line="+4"/>
        <source>选择 ASS 模板文件</source>
        <translation>ASS テンプレートファイルを選択</translation>
    </message>
    <message>
        <location line="+2"/>
        <source>ASS 字幕文件 (*.ass)</source>
        <translation>ASS 字幕ファイル (*.ass)</translation>
    </message>
    <message>
        <location filename="../main_window/pipeline_control.py" line="-319"/>
        <location filename="../main_window/source_config.py" line="-11"/>
        <source>请先加载视频并至少定义一个 ROI。</source>
        <translation>まず動画を読み込み、ROI を 1 つ以上定義してください。</translation>
    </message>
    <message>
        <location filename="../main_window/source_config.py" line="+12"/>
        <source>当前帧不在任一 ROI 的时间范围内。请将时间轴移到含字幕的典型帧上，用于自动标定颜色。</source>
        <translation>現在のフレームはどの ROI の時間範囲にも含まれていません。色の自動較正のため、字幕が含まれる代表的なフレームにタイムラインを移動してください。</translation>
    </message>
    <message>
        <location line="+21"/>
        <source>颜色门控预览失败：{}</source>
        <translation>カラーゲートのプレビューに失敗：{}</translation>
    </message>
    <message>
        <location line="+5"/>
        <source>预览失败</source>
        <translation>プレビュー失敗</translation>
    </message>
    <message>
        <location line="+9"/>
        <source>颜色门控已确认；下次运行「字幕 OCR 识别」时将在阶段一启用。</source>
        <translation>カラーゲートを確認しました。次回の「字幕 OCR 認識」のステージ 1 で有効になります。</translation>
    </message>
    <message>
        <location filename="../main_window/pipeline_control.py" line="-120"/>
        <source>已启用 AI 翻译，但翻译模块不可用（{0}）。请先安装 font_intel 依赖（requirements-fontintel.txt），或取消勾选「AI 翻译」后重试。</source>
        <translation>AI 翻訳が有効ですが、翻訳モジュールを利用できません（{0}）。先に font_intel の依存関係（requirements-fontintel.txt）をインストールするか、「AI 翻訳」のチェックを外して再試行してください。</translation>
    </message>
    <message>
        <source>已启用 AI 翻译（云端 API），但未填写 API Key。请在大模型润色区填写 API Key，或设置环境变量 DEEPSEEK_API_KEY。</source>
        <translation type="vanished">AI 翻訳（クラウド API）が有効ですが、API Key が未入力です。「大規模モデルによる字幕ポリッシュ」欄に API Key を入力するか、環境変数 DEEPSEEK_API_KEY を設定してください。</translation>
    </message>
    <message>
        <source>已启用 AI 翻译（本地 Sakura），但未填写 Base URL。请填写本地 Sakura 服务器的 OpenAI 兼容端点地址（如 http://127.0.0.1:8080/v1）。</source>
        <translation type="vanished">AI 翻訳（ローカル Sakura）が有効ですが、Base URL が未入力です。ローカル Sakura サーバーの OpenAI 互換エンドポイントアドレス（例: http://127.0.0.1:8080/v1）を入力してください。</translation>
    </message>
    <message>
        <location line="+10"/>
        <source>已启用 AI 翻译，但未填写 API Key。请在大模型润色区填写 API Key，或设置环境变量 DEEPSEEK_API_KEY。</source>
        <translation>AI 翻訳が有効ですが、API Key が未入力です。「大規模モデルによる字幕ポリッシュ」欄に API Key を入力するか、環境変数 DEEPSEEK_API_KEY を設定してください。</translation>
    </message>
    <message>
        <location line="+48"/>
        <source>已启用字体识别，但字体识别模块不可用（{0}）。请先安装 font_intel 依赖（requirements-fontintel.txt），或取消勾选「字体识别」后重试。</source>
        <translation>フォント識別が有効ですが、フォント識別モジュールを利用できません（{0}）。font_intel の依存パッケージ（requirements-fontintel.txt）をインストールするか、「フォント識別」のチェックを外して再試行してください。</translation>
    </message>
    <message>
        <location line="+21"/>
        <source>已启用字体识别，但字体库目录不存在：{0}。请检查路径，或清空后仅使用系统字体目录。</source>
        <translation>フォント識別が有効ですが、フォントディレクトリが存在しません：{0}。パスを確認するか、空欄にしてシステムフォントディレクトリのみを使用してください。</translation>
    </message>
    <message>
        <location line="+47"/>
        <source>已有 OCR 任务在运行中，请等待其完成或先取消。</source>
        <translation>OCR タスクが実行中です。完了を待つか、先にキャンセルしてください。</translation>
    </message>
    <message>
        <location line="+10"/>
        <source>已勾选「按字幕颜色跳过疑似无字帧」，但尚未通过预览确认。请点击「预览检测效果」并在满意时选择「采用」，或取消勾选以使用默认流程。</source>
        <translation>「字幕の色で無文字と思われるフレームをスキップ」にチェックがありますが、プレビューでの確認がまだです。「検出結果をプレビュー」をクリックし、満足できたら「採用」を選ぶか、チェックを外して既定のフローをご利用ください。</translation>
    </message>
    <message>
        <location line="+19"/>
        <source>已启用 DeepSeek 功能（润色/碎片合并/策略复核），但未填写 API Key。请填写 API Key，或设置环境变量 DEEPSEEK_API_KEY。</source>
        <translation>DeepSeek 機能（ポリッシュ／断片マージ／戦略レビュー）が有効ですが、API Key が未入力です。API Key を入力するか、環境変数 DEEPSEEK_API_KEY を設定してください。</translation>
    </message>
    <message>
        <location line="+50"/>
        <source>保存字幕文件</source>
        <translation>字幕ファイルを保存</translation>
    </message>
    <message>
        <location line="+2"/>
        <source>ASS 字幕 (*.ass)</source>
        <translation>ASS 字幕 (*.ass)</translation>
    </message>
    <message>
        <location line="+3"/>
        <source>用户取消保存，OCR 任务已中止。</source>
        <translation>保存がキャンセルされたため、OCR タスクを中止しました。</translation>
    </message>
    <message>
        <location line="+16"/>
        <source>[LLM] 已启用 DeepSeek —— 第 4 步的大模型进度会显示在下方。</source>
        <translation>[LLM] DeepSeek が有効になりました——ステップ 4 の LLM 進行状況は以下に表示されます。</translation>
    </message>
    <message>
        <location line="+4"/>
        <source>[LLM] 已启用 AI 翻译 —— 翻译进度会显示在下方。</source>
        <translation>[LLM] AI 翻訳が有効です —— 翻訳の進捗は下に表示されます。</translation>
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
        <translation>動画を処理中...</translation>
    </message>
    <message>
        <location line="+1"/>
        <source>取消</source>
        <translation>キャンセル</translation>
    </message>
    <message>
        <location line="+52"/>
        <source>正在识别…</source>
        <translation>認識中…</translation>
    </message>
    <message>
        <location line="+17"/>
        <source>[LLM] 已取消 —— 未生成字幕文件。</source>
        <translation>[LLM] 已取消 —— 未生成字幕文件。</translation>
    </message>
    <message>
        <location line="+50"/>
        <source>[LLM] 完成 —— 已写入 ASS 文件。</source>
        <translation>[LLM] 完了——ASS ファイルに書き込みました。</translation>
    </message>
    <message>
        <location line="+5"/>
        <location line="+83"/>
        <source>完成</source>
        <translation>完了</translation>
    </message>
    <message>
        <location line="-82"/>
        <source>字幕文件已生成：{}</source>
        <translation>字幕ファイルを生成しました：{}</translation>
    </message>
    <message>
        <location line="+6"/>
        <source>打开目录</source>
        <translation>フォルダーを開く</translation>
    </message>
    <message>
        <location line="+1"/>
        <source>是否打开包含该文件的文件夹？</source>
        <translation>このファイルを含むフォルダーを開きますか？</translation>
    </message>
    <message>
        <location line="+32"/>
        <source>字体更新建议读取失败，本轮复核已跳过：
{0}</source>
        <translation>フォント更新提案の読み取りに失敗したため、今回のレビューをスキップしました：
{0}</translation>
    </message>
    <message>
        <location line="+35"/>
        <source>字体更新建议写入失败：
{0}</source>
        <translation>フォント更新提案の書き込みに失敗しました：
{0}</translation>
    </message>
    <message>
        <location line="+9"/>
        <source>已把 {0} 条字体更新建议写入用户覆盖层。</source>
        <translation>{0} 件のフォント更新提案をユーザーオーバーレイ層に書き込みました。</translation>
    </message>
    <message>
        <location line="+12"/>
        <source>[LLM] 已中止或出错 —— 请查看弹窗提示。</source>
        <translation>[LLM] 中止またはエラー——ダイアログの表示をご確認ください。</translation>
    </message>
    <message>
        <location line="+5"/>
        <source>处理过程中发生错误：
{}</source>
        <translation>処理中にエラーが発生しました：
{}</translation>
    </message>
    <message>
        <location filename="../main_window/auto_detection.py" line="+43"/>
        <source>已恢复上次保存的文字来源过滤设置。</source>
        <translation>前回保存したテキストソースフィルタの設定を復元しました。</translation>
    </message>
    <message>
        <location line="+35"/>
        <source>视频类型自动检测完成：{} (置信度 {:.0%})</source>
        <translation>動画タイプの自動検出が完了：{}（信頼度 {:.0%}）</translation>
    </message>
    <message>
        <location line="+9"/>
        <source>视频类型自动检测失败：{}</source>
        <translation>動画タイプの自動検出に失敗：{}</translation>
    </message>
    <message>
        <location line="+7"/>
        <source>自动分析失败，请手动选择场景类型</source>
        <translation>自動解析に失敗しました。シーンタイプを手動で選択してください</translation>
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
        <translation>終了の確認</translation>
    </message>
    <message>
        <location line="+1"/>
        <source>后台任务仍在运行，确定要退出吗？</source>
        <translation>バックグラウンドタスクが実行中です。本当に終了しますか？</translation>
    </message>
    <message>
        <location filename="../main_window/pipeline_control.py" line="-1"/>
        <location filename="../main_window/roi_config_io.py" line="-96"/>
        <location line="+88"/>
        <location filename="../main_window/roi_editing.py" line="-125"/>
        <location filename="../main_window/scan_control.py" line="+195"/>
        <location filename="../main_window/video_playback.py" line="-151"/>
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
        <location filename="../components/video_display.py" line="+430"/>
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
        <location filename="../core/ocr_optimizer.py" line="+354"/>
        <source>Could not get image data for frame {}. Input type: {}</source>
        <translation>フレーム{}の画像データを取得できませんでした。入力タイプ: {}</translation>
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
        <translation>OCRオプティマイザーがキャンセル信号を検出したため、早期に終了します。</translation>
    </message>
    <message>
        <location line="+111"/>
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
        <location filename="../core/pipeline_worker.py" line="+610"/>
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
        <location filename="../core/pipeline_stages.py" line="+99"/>
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
        <location line="+46"/>
        <source>Streaming mode enabled (time_slice={}s). OCR will run during extraction to reduce peak memory.</source>
        <translation type="unfinished"></translation>
    </message>
    <message>
        <location line="+145"/>
        <source>Step 1/4: Extracting ROI frames... ({}/{})</source>
        <translation>ステップ1/4: 対象領域(ROI)フレームを抽出中... ({}/{})</translation>
    </message>
    <message>
        <location line="+32"/>
        <source>Step 1/4: ROI frame extraction complete. Total {} ROI frames.</source>
        <translation>ステップ1/4: 対象領域(ROI)フレーム抽出完了。合計{}対象領域(ROI)フレーム。</translation>
    </message>
    <message>
        <source>Error during ROI extraction: {}</source>
        <translation type="vanished">対象領域(ROI)抽出中にエラーが発生しました: {}</translation>
    </message>
    <message>
        <location line="-186"/>
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
        <location line="+113"/>
        <location line="+95"/>
        <source>Step 2/4: OCR recognition in progress... ({}/{})</source>
        <translation>ステップ2/4: OCR認識進行中... ({}/{})</translation>
    </message>
    <message>
        <location line="-257"/>
        <source>Streaming OCR will flush in parallel (cpu_workers={}, max_stream_workers={}).</source>
        <translation type="unfinished"></translation>
    </message>
    <message>
        <location line="+108"/>
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
        <location filename="../core/pipeline_worker.py" line="-521"/>
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
        <translation>モーション軌跡 {0} は ROI 時間範囲の {1:.0f}% しかカバーしていないため、静的ポジションタグにフォールバックします。</translation>
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
        <location line="+90"/>
        <source>Step 1-3/4: Processing chunks in parallel...</source>
        <translation>ステップ 1-3/4: チャンクを並列処理中...</translation>
    </message>
    <message>
        <location line="+29"/>
        <source>Chunk-parallel OCR: {} workers, {} windows.</source>
        <translation>チャンク並列OCR: {} ワーカー、{} ウィンドウ。</translation>
    </message>
    <message>
        <location line="+41"/>
        <source>Step 4/4: Starting ASS subtitle file generation...</source>
        <translation>ステップ4/4: ASS字幕ファイル生成を開始中...</translation>
    </message>
    <message>
        <location line="+79"/>
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
        <translation>処理する対象領域(ROI)フレームの合計{}が事前に計算されました。</translation>
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
        <translation>抽出を開始できません: ビデオパスまたは対象領域(ROI)データが提供されていません。</translation>
    </message>
    <message>
        <location line="-118"/>
        <location line="+122"/>
        <source>In save_to_disk mode, a valid working directory `work_dir` must be provided.</source>
        <translation>save_to_diskモードでは、有効な作業ディレクトリ`work_dir`を指定する必要があります。</translation>
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
        <location line="+23"/>
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
