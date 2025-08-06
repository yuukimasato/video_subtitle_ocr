#!/bin/bash
set -euo pipefail

# 获取脚本所在目录
SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)

# 加载公共变量
source "${SCRIPT_DIR}/install-common.sh"

# 清理旧虚拟环境
echo "清理旧虚拟环境..."
rm -rf test_venv

# 虚拟环境设置
if [ ! -d ".venv" ]; then
    echo "创建Python虚拟环境..."
    python3 -m venv .venv
fi
source .venv/bin/activate

# 安装Python依赖
echo "安装Python依赖..."
if $VERBOSE; then
    pip install --upgrade pip
else
    pip install --upgrade pip >/dev/null 2>&1
fi

# 使用清华镜像源
pip_source="https://pypi.tuna.tsinghua.edu.cn/simple"

# 安装基础依赖
echo "安装基础Python依赖..."
if $USE_NEW_REQS; then
    reqs_file="${SCRIPT_DIR}/requirements/gpu-requirements.txt"
    echo "GPU环境...安装基础Python依赖..."
else
    reqs_file="${SCRIPT_DIR}/requirements/requirements.txt"
    echo "CPU环境...安装基础Python依赖..."
fi

if $VERBOSE; then
    pip install -r "$reqs_file" -i "$pip_source"
else
    pip install -r "$reqs_file" -i "$pip_source" >/dev/null 2>&1
fi

# 根据模式安装额外依赖
if [ "$MODE" = "gpu" ]; then
    echo "安装GPU相关依赖..."
    if $VERBOSE; then
        pip install paddlepaddle-gpu -i "$pip_source"
    else
        pip install paddlepaddle-gpu -i "$pip_source" >/dev/null 2>&1
    fi
else
    echo "安装CPU相关依赖..."
    if $VERBOSE; then
        pip install paddlepaddle -i "$pip_source"
    else
        pip install paddlepaddle -i "$pip_source" >/dev/null 2>&1
    fi
fi

# 验证安装
echo -e "\n=== 验证安装 ==="
if $VERBOSE; then
    pip list | grep paddle
    python -c "import paddle; print(paddle.version)"
fi


echo "Python依赖安装完成"
source .venv/bin/activate && python -c "import paddleocr; print(f'PaddlePaddle版本: {paddleocr.__version__}')"

