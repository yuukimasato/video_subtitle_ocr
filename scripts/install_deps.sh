#!/bin/bash
set -euo pipefail

# 获取脚本所在目录
SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)

# 加载公共变量
source "${SCRIPT_DIR}/install-common.sh"

# 加载GPU模式
source "${SCRIPT_DIR}/../.gpu_mode" 2>/dev/null || MODE="cpu"

# 系统检测
OS_ID=$(grep -oP 'ID=\K\w+' /etc/os-release || echo "unknown")
OS_VERSION=$(grep -oP 'VERSION_ID="\K[\d.]+' /etc/os-release || echo "0")

echo "安装系统依赖..."
case "$OS_ID" in
    ubuntu|debian|kali|linuxmint)
        if $VERBOSE; then
            sudo apt-get update
            sudo DEBIAN_FRONTEND=noninteractive apt-get install -y \
                python3-venv \
                python3-dev \
                wget \
                unzip \
                fonts-wqy-zenhei \
                fonts-wqy-microhei \
                libgl1 \
                libglib2.0-0t64 \
                protobuf-compiler \
                cmake \
                python3-gi \
                gir1.2-gtk-3.0 \
                libgirepository1.0-dev 
        else
            sudo apt-get update >/dev/null
            sudo DEBIAN_FRONTEND=noninteractive apt-get install -y \
                python3-venv \
                wget \
                unzip \
                fonts-wqy-zenhei \
                fonts-wqy-microhei \
                libgl1 \
                libglib2.0-0t64 \
                protobuf-compiler \
                cmake >/dev/null
        fi
        
        # GPU特定依赖
        if [ "$MODE" = "gpu" ]; then
            if $VERBOSE; then
                sudo apt-get install -y nvidia-cuda-toolkit
            else
                sudo apt-get install -y nvidia-cuda-toolkit >/dev/null
            fi
            
            # 检测CUDA版本并提示cuDNN安装
            if command -v nvcc &> /dev/null; then
                CUDA_VERSION=$(nvcc --version | grep -oP 'release \K[0-9.]+')
                echo "检测到CUDA版本: ${CUDA_VERSION}"
                echo "请按以下步骤安装cuDNN:"
                echo "1. 访问 https://developer.nvidia.com/cudnn (需要注册账号)"
                echo "2. 下载匹配CUDA ${CUDA_VERSION}的cuDNN包"
                echo "3. 解压后执行以下命令:"
                echo "   sudo cp cuda/include/cudnn*.h /usr/local/cuda/include"
                echo "   sudo cp cuda/lib64/libcudnn* /usr/local/cuda/lib64"
                echo "   sudo chmod a+r /usr/local/cuda/include/cudnn*.h /usr/local/cuda/lib64/libcudnn*"
                echo -e "\n是否继续安装? (缺少cuDNN将导致GPU性能下降)"
                select yn in "继续安装" "退出安装"; do
                    case $yn in
                        "继续安装") break;;
                        "退出安装") exit 1;;
                    esac
                done
            else
                echo "警告: 未检测到CUDA工具包"
                read -p "按回车键继续安装(使用CPU模式)..."
                MODE="cpu"
                echo "export MODE=$MODE" > .gpu_mode
            fi
        fi
        ;;
    *)
        echo "检测到非Debian系系统，请手动安装以下依赖："
        echo "- python3-venv  - wget  - unzip"
        echo "- 中文字体：fonts-wqy-zenhei 和 fonts-wqy-microhei"
        echo "- GPU支持：libgl1-mesa-glx (仅CPU模式也需要)"
        [ "$MODE" = "gpu" ] && echo "- CUDA工具包和cuDNN"
        read -p "按回车键继续安装..." 
        ;;
esac

echo "系统依赖安装完成"