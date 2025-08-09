#!/bin/bash
# 国际化工具脚本 (PySide6 兼容版) - v6 (数组增强版，完美处理空格和特殊字符)
# 用法：./i18n_tools.sh
# 此脚本自动发现项目中的 .py 文件并传递给 lupdate，无需维护 .pro 文件。

# --- 配置 ---
SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" &> /dev/null && pwd)
PROJECT_ROOT=$(dirname "$SCRIPT_DIR")

# 定义需要扫描的源文件目录和根目录下的特定文件
SOURCE_DIRS_TO_SCAN=("components" "core")
SOURCE_FILES_IN_ROOT=("main_window.py")

# 语言选项配置
declare -A LANG_OPTIONS=(
    [1]="zh_CN 中文(简体)"
    [2]="en_US 英文(美国)"
    [3]="zh_TW 中文(繁体)"
    [4]="zh_HK 中文(香港)"
    [5]="ja_JP 日语(日本)"
    [6]="ko_KR 韩语(韩国)"
    [7]="ru_RU 俄语(俄罗斯)"
)

# --- 虚拟环境检查 ---
VENV_PATH="$PROJECT_ROOT/.venv"
if [ -n "$VIRTUAL_ENV" ] && [ -f "$VIRTUAL_ENV/bin/pyside6-lupdate" ]; then
    echo "使用已激活的虚拟环境: $VIRTUAL_ENV"
    LUPDATE_CMD="$VIRTUAL_ENV/bin/pyside6-lupdate"
    LRELEASE_CMD="$VIRTUAL_ENV/bin/pyside6-lrelease"
elif [ -f "$VENV_PATH/bin/activate" ]; then
    echo "使用项目虚拟环境: $VENV_PATH"
    LUPDATE_CMD="$VENV_PATH/bin/pyside6-lupdate"
    LRELEASE_CMD="$VENV_PATH/bin/pyside6-lrelease"
else
    echo "警告: 未检测到虚拟环境。将尝试使用系统命令。"
    LUPDATE_CMD=$(command -v pyside6-lupdate)
    LRELEASE_CMD=$(command -v pyside6-lrelease)
    if [ -z "$LUPDATE_CMD" ] || [ -z "$LRELEASE_CMD" ]; then
        echo "错误: 在系统路径中未找到 pyside6-lupdate 或 pyside6-lrelease。"
        echo "请确保已激活虚拟环境或已全局安装 PySide6: pip install pyside6"
        exit 1
    fi
fi

# 切换到项目根目录，这是所有后续操作的基准目录
cd "$PROJECT_ROOT" || exit 1

# --- 主逻辑 ---
echo "请选择操作:"
echo "1) 更新/生成翻译源文件(.ts)"
echo "2) 编译翻译文件(.qm)"
# pyside6-linguist i18n/app_zh_CN.ts
read -r choice

case $choice in
    1|2)
        echo "请选择语言:"
        for i in $(echo "${!LANG_OPTIONS[@]}" | tr ' ' '\n' | sort -n); do
            echo "$i) ${LANG_OPTIONS[$i]}"
        done
        read -r lang_choice

        lang_code_and_desc=${LANG_OPTIONS[$lang_choice]}
        if [ -z "$lang_code_and_desc" ]; then
            echo "无效的语言选项"
            exit 1
        fi
        lang_code=${lang_code_and_desc%% *}
        lang_desc=${lang_code_and_desc#* }

        TS_FILE="i18n/app_${lang_code}.ts"
        QM_FILE="i18n/app_${lang_code}.qm"

        if [ "$choice" -eq 1 ]; then
            # *** 核心改动 1: 使用数组来存储文件名 ***
            echo "---"
            echo "正在扫描源文件..."
            
            # 声明一个空数组
            SOURCE_FILES_ARRAY=()
            
            # 使用 find 和 while read 循环来安全地填充数组，处理任何文件名
            # -print0 和 read -d '' 是处理包含特殊字符文件名的黄金搭档
            while IFS= read -r -d '' file; do
                SOURCE_FILES_ARRAY+=("$file")
            done < <(find "${SOURCE_DIRS_TO_SCAN[@]}" -name "*.py" -print0 2>/dev/null)

            # 将根目录下的文件也添加进来
            for file in "${SOURCE_FILES_IN_ROOT[@]}"; do
                if [ -f "$file" ]; then
                    SOURCE_FILES_ARRAY+=("$file")
                fi
            done

            if [ ${#SOURCE_FILES_ARRAY[@]} -eq 0 ]; then
                echo "错误: 未在指定位置找到任何源文件。"
                echo "请检查脚本中的 SOURCE_DIRS_TO_SCAN 和 SOURCE_FILES_IN_ROOT 配置。"
                exit 1
            fi
            
            echo "发现以下源文件将被处理:"
            # 使用 printf 安全地打印数组中的每个元素
            printf " - %s\n" "${SOURCE_FILES_ARRAY[@]}"
            echo "---"
            echo "正在为 [${lang_desc}] 生成翻译源文件..."
            echo "生成目标文件: $TS_FILE"
            echo "---"

            # *** 核心改动 2: 使用 "${ARRAY[@]}" 语法安全地传递参数 ***
            # 这可以确保每个文件名（即使包含空格）都被当作一个独立的参数
            "$LUPDATE_CMD" "${SOURCE_FILES_ARRAY[@]}" -ts "$TS_FILE"

            if [ $? -eq 0 ]; then
                echo "成功更新翻译源文件: $TS_FILE"
            else
                echo "生成翻译源文件失败"
                exit 1
            fi
        else
            echo "正在编译 [${lang_desc}] 的翻译文件..."
            if [ ! -f "$TS_FILE" ]; then
                echo "错误: 翻译源文件 '$TS_FILE' 不存在。请先运行选项 1 生成它。"
                exit 1
            fi
            "$LRELEASE_CMD" "$TS_FILE" -qm "$QM_FILE"
            if [ $? -eq 0 ]; then
                echo "成功编译翻译文件: $QM_FILE"
            else
                echo "编译翻译文件失败"
                exit 1
            fi
        fi
        ;;
    *)
        echo "无效选项，请输入1或2"
        exit 1
        ;;
esac
