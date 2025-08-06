#!/bin/bash

# PaddleOCR 自动安装脚本（CPU服务器版）
set -e

echo "=== 开始安装 PaddleOCR ==="

# 1. 克隆PaddleOCR项目
echo "步骤1: 克隆PaddleOCR项目..."
if [ ! -d "PaddleOCR" ]; then
    git clone https://github.com/PaddlePaddle/PaddleOCR.git
    cd PaddleOCR
else
    echo "PaddleOCR目录已存在，跳过克隆"
    cd PaddleOCR
    git pull
fi
