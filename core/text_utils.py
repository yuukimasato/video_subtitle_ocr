"""多语言文本处理工具

统一的文本分析工具，支持CJK和世界多语言字符统计。
"""

import re

# ==================== Unicode 字符范围定义 ====================

# 按字符计数的语言（不使用空格分词）
# 包括: CJK（中日韩）+ 东南亚/南亚语言（泰文/缅甸文/高棉文/印地语等）
_NO_SPACE_LANGUAGES = (
    r"[一-鿿぀-ゟ゠-ヿ"
    r"가-힯฀-໿က-႟"
    r"ក-៿ऀ-෿]"
)



def count_words(text: str) -> int:
    """统计文本字符/单词数

    按字符计数的语言（不使用空格分词）:
    - CJK (中文、日文、韩文)
    - 泰文、缅甸文、高棉文、印地语等

    按单词计数的语言（使用空格分词）:
    - 拉丁字母语言 (英语、法语、德语、西班牙语等)
    - 西里尔字母语言 (俄语、乌克兰语、保加利亚语等)
    - 希腊字母、阿拉伯字母、希伯来字母等

    混合文本处理:
    - 按字符计数的语言统计字符数
    - 按单词计数的语言统计单词数
    - 返回总和

    Args:
        text: 待统计的文本

    Returns:
        字符数 + 单词数
    """
    if not text:
        return 0

    # 统计不使用空格的语言的字符数（CJK + 泰文/缅甸文等）
    char_count = len(re.findall(_NO_SPACE_LANGUAGES, text))

    # 移除不使用空格的字符后，统计使用空格的语言的单词数
    word_text = re.sub(_NO_SPACE_LANGUAGES, " ", text)
    word_count = len(word_text.strip().split())

    return char_count + word_count


def is_noise_text(body: str) -> bool:
    """判定 OCR 文本行是否为噪声(与静态路径 ``_is_noise_body`` 同一判据)。

    噪声 = 空行/纯括号/纯数字/短编号(≤4 位数字母混排)/单字非标点——
    手机状态栏与导航栏图标(<、>、000、单字象形)的典型误读。轨迹管线
    (``scripts.motion_ass.build_motion_events`` 的 ``junk_line_filter``)
    与主流水线静态路径共用本判据,行为完全一致。
    """
    b = (body or "").replace("\r", "").strip()
    if not b:
        return True
    # Remove ASS explicit line breaks for judgement
    z = re.sub(r"(?i)\\[nN]", "", b)
    z = re.sub(r"\s+", "", z)
    if not z:
        return True
    if z in ("()", "（）", "[]", "【】", "{}"):
        return True
    if re.fullmatch(r"[\(\)\[\]\{\}（）【】]+", z):
        return True
    if re.fullmatch(r"[0-9]+", z):
        return True
    if re.fullmatch(r"[0-9]+[A-Za-z]+", z) or re.fullmatch(r"[A-Za-z]+[0-9]+", z):
        # 常见 OCR 垃圾：短促闪烁的编号/序号
        return len(z) <= 4
    if len(z) == 1 and z not in ("，", "。", "！", "？", ".", "!", "?"):
        return True
    return False
