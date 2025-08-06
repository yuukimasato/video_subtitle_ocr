#!/bin/bash
set -euo pipefail

# 获取脚本所在目录
SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)

# 加载公共变量
source "${SCRIPT_DIR}/install-common.sh"

# 检测GPU并设置MODE环境变量
detect_gpu() {
    # 检查PCI设备中是否有NVIDIA显卡
    if ! lspci | grep -i nvidia &> /dev/null; then
        echo "未检测到NVIDIA显卡硬件，将使用CPU模式"
        return 1
    fi
    
    # 检查驱动是否安装
    if ! command -v nvidia-smi &> /dev/null; then
        echo "检测到NVIDIA显卡但未安装驱动，将使用CPU模式"
        return 1
    fi
    
    # 检查GPU是否可用
    if nvidia-smi | grep -q "NVIDIA-SMI has failed"; then
        echo "检测到NVIDIA驱动但GPU不可用，将使用CPU模式"
        return 1
    fi
    
    echo "检测到可用GPU，将使用GPU加速模式"
    return 0
}

# 默认自动检测GPU
if detect_gpu; then
    MODE="gpu"
    echo "检测到GPU，将安装GPU版本"
else
    MODE="cpu"
    echo "未检测到GPU，将安装CPU版本"
fi

# 输出模式供主脚本使用
echo "export MODE=$MODE" > .gpu_mode