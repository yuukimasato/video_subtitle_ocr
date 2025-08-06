#!/bin/bash
set -euo pipefail

# 获取脚本所在目录
SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)

# 加载公共函数和变量
source "${SCRIPT_DIR}/scripts/install-common.sh"

# 初始化环境
init_environment "$@"

# 主安装流程
echo "开始安装PaddleOCR环境..."

# 1. 检测GPU
run_step "检测GPU" ./scripts/detect_gpu.sh

# 2. 安装系统依赖
if ! $SKIP_SYSTEM_DEPS; then
    run_step "安装系统依赖" ./scripts/install_deps.sh
fi

# 3. 安装Python依赖
run_step "安装Python依赖" ./scripts/install_python.sh

# 4. 下载模型
#run_step "下载模型" ./scripts/model_list_download.sh

# 环境验证
validate_environment

echo -e "\nPaddleOCR环境安装完成！"