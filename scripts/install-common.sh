#!/bin/bash
set -euo pipefail

# ========== 公共变量 ==========
FORCE_DOWNLOAD=false
VERBOSE=false
SKIP_SYSTEM_DEPS=false
USE_NEW_REQS=false
MODE="auto"

# ========== 帮助信息 ==========
show_help() {
    echo "PaddleOCR环境安装脚本"
    echo "用法: ./install-optimized.sh [选项]"
    echo ""
    echo "选项:"
    echo "  --with-gpu         使用GPU模式安装"
    echo "  --cpu-only         使用CPU模式安装"
    echo "  --force-download  强制重新下载模型"
    echo "  -v, --verbose     显示详细输出"
    echo "  --skip-system-deps 跳过系统依赖安装"
    echo "  --use-new-reqs    使用新的requirements文件"
    echo "  --download-all    下载所有模型(包括可选模型)"
    echo "  -h, --help        显示此帮助信息"
}

# ========== 参数解析 ==========
init_environment() {
    while [[ $# -gt 0 ]]; do
        case "$1" in
            --with-gpu)
                MODE="gpu"
                shift
                ;;
            --with-cpu|--cpu-only)
                MODE="cpu"
                shift
                ;;
            --force-download)
                FORCE_DOWNLOAD=true
                shift
                ;;
            -v|--verbose)
                VERBOSE=true
                shift
                ;;
            --skip-system-deps)
                SKIP_SYSTEM_DEPS=true
                shift
                ;;
            --use-new-reqs)
                USE_NEW_REQS=true
                shift
                ;;
            --download-all)
                FORCE_DOWNLOAD=true
                shift
                ;;
            -h|--help)
                show_help
                exit 0
                ;;
            *)
                echo "未知参数: $1"
                show_help
                exit 1
                ;;
        esac
    done

    export FORCE_DOWNLOAD
    export VERBOSE
}

# ========== 步骤运行函数 ==========
run_step() {
    local step_name="$1"
    local step_command="$2"
    
    echo -e "\n=== $step_name ==="
    if $VERBOSE; then
        $step_command
    else
        $step_command >/dev/null 2>&1
    fi
}

# ========== 环境验证 ==========
validate_environment() {
    echo -e "\n=== 环境验证 ==="
    source .venv/bin/activate
    python -c "
try:
    import setuptools
except ImportError:
    print('安装setuptools...')
    import subprocess
    import sys
    subprocess.check_call([sys.executable, '-m', 'pip', 'install', 'setuptools'])
    
import paddle
print(f'PaddlePaddle版本: {paddle.__version__}')
print(f'运行设备: {'GPU' if paddle.is_compiled_with_cuda() else 'CPU'}')
"
}