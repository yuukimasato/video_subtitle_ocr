#!/bin/bash

# 创建模型存储目录
ROOT_DIR="paddle_ocr_models"
mkdir -p "$ROOT_DIR"
cd "$ROOT_DIR"

# 下载函数（简化版）
download_and_extract() {
    local target_dir="$1"
    local model_name="$2"
    local model_url="$3"
    local config_url="$4"
    
    echo "下载模型: $model_name"
    mkdir -p "$target_dir/$model_name"
    
    # 下载配置文件
    if [ -n "$config_url" ]; then
        echo "  下载配置文件..."
        wget -q --no-check-certificate -O "$target_dir/$model_name.yaml" "${config_url/blob\/develop/raw\/develop}"
    fi
    
    # 下载模型
    echo "  下载模型文件..."
    wget -q --no-check-certificate -O "$target_dir/$model_name.tar" "$model_url"
    
    # 解压模型
    echo "  解压模型..."
    tar -xf "$target_dir/$model_name.tar" -C "$target_dir/$model_name" --strip-components=1
    
    # 清理临时文件
    rm -f "$target_dir/$model_name.tar"
}

# ========== 模型分类下载 ==========

# 文本检测模块
echo "下载文本检测模块..."
category="text_detection"
download_and_extract "$category" "PP-OCRv5_server_det" \
    "https://paddle-model-ecology.bj.bcebos.com/paddlex/official_inference_model/paddle3.0.0/PP-OCRv5_server_det_infer.tar" \
    "https://github.com/PaddlePaddle/PaddleX/blob/develop/paddlex/configs/modules/text_detection/PP-OCRv5_server_det.yaml"

# 印章文本检测模块
echo "下载印章文本检测模块..."
category="seal_text_detection"
download_and_extract "$category" "PP-OCRv4_server_seal_det" \
    "https://paddle-model-ecology.bj.bcebos.com/paddlex/official_inference_model/paddle3.0.0/PP-OCRv4_server_seal_det_infer.tar" \
    "https://github.com/PaddlePaddle/PaddleX/blob/develop/paddlex/configs/modules/seal_text_detection/PP-OCRv4_server_seal_det.yaml"

# 文本识别模块-中文
echo "下载中文文本识别模型..."
category="text_recognition/chinese"
download_and_extract "$category" "PP-OCRv4_server_rec_doc" \
    "https://paddle-model-ecology.bj.bcebos.com/paddlex/official_inference_model/paddle3.0.0/PP-OCRv4_server_rec_doc_infer.tar" \
    "https://github.com/PaddlePaddle/PaddleX/blob/develop/paddlex/configs/modules/text_recognition/PP-OCRv4_server_rec_doc.yaml"

# 文本识别模块-英文
echo "下载英文文本识别模型..."
category="text_recognition/english"
download_and_extract "$category" "en_PP-OCRv3_mobile_rec" \
    "https://paddle-model-ecology.bj.bcebos.com/paddlex/official_inference_model/paddle3.0.0/en_PP-OCRv3_mobile_rec_infer.tar" \
    "https://github.com/PaddlePaddle/PaddleX/blob/develop/paddlex/configs/modules/text_recognition/en_PP-OCRv3_mobile_rec.yaml"

# 文本识别模块-多语言
echo "下载多语言文本识别模型..."
category="text_recognition/multilingual"
langs=("korean" "japan" "chinese_cht" "te" "ka" "ta" "latin" "arabic" "cyrillic" "devanagari")
for lang in "${langs[@]}"; do
    model_name="${lang}_PP-OCRv3_mobile_rec"
    download_and_extract "$category" "$model_name" \
        "https://paddle-model-ecology.bj.bcebos.com/paddlex/official_inference_model/paddle3.0.0/${model_name}_infer.tar" \
        "https://github.com/PaddlePaddle/PaddleX/blob/develop/paddlex/configs/modules/text_recognition/${model_name}.yaml"
done

# 公式识别模块
echo "下载公式识别模型..."
category="formula_recognition"
download_and_extract "$category" "PP-FormulaNet_plus-L" \
    "https://paddle-model-ecology.bj.bcebos.com/paddlex/official_inference_model/paddle3.0.0/PP-FormulaNet_plus-L_infer.tar" \
    "https://github.com/PaddlePaddle/PaddleX/blob/develop/paddlex/configs/modules/formula_recognition/PP-FormulaNet_plus-L.yaml"

# 表格结构识别模块
echo "下载表格结构识别模型..."
category="table_structure_recognition"
download_and_extract "$category" "SLANeXt_wired" \
    "https://paddle-model-ecology.bj.bcebos.com/paddlex/official_inference_model/paddle3.0.0/SLANeXt_wired_infer.tar" \
    "https://github.com/PaddlePaddle/PaddleX/blob/develop/paddlex/configs/modules/table_structure_recognition/SLANeXt_wired.yaml"

download_and_extract "$category" "SLANeXt_wireless" \
    "https://paddle-model-ecology.bj.bcebos.com/paddlex/official_inference_model/paddle3.0.0/SLANeXt_wireless_infer.tar" \
    "https://github.com/PaddlePaddle/PaddleX/blob/develop/paddlex/configs/modules/table_structure_recognition/SLANeXt_wireless.yaml"

# 表格单元格检测模块
echo "下载表格单元格检测模型..."
category="table_cells_detection"
download_and_extract "$category" "RT-DETR-L_wired_table_cell_det" \
    "https://paddle-model-ecology.bj.bcebos.com/paddlex/official_inference_model/paddle3.0.0/RT-DETR-L_wired_table_cell_det_infer.tar" \
    "https://github.com/PaddlePaddle/PaddleX/blob/develop/paddlex/configs/modules/table_cells_detection/RT-DETR-L_wired_table_cell_det.yaml"

download_and_extract "$category" "RT-DETR-L_wireless_table_cell_det" \
    "https://paddle-model-ecology.bj.bcebos.com/paddlex/official_inference_model/paddle3.0.0/RT-DETR-L_wireless_table_cell_det_infer.tar" \
    "https://github.com/PaddlePaddle/PaddleX/blob/develop/paddlex/configs/modules/table_cells_detection/RT-DETR-L_wireless_table_cell_det.yaml"

# 表格分类模块
echo "下载表格分类模型..."
category="table_classification"
download_and_extract "$category" "PP-LCNet_x1_0_table_cls" \
    "https://paddle-model-ecology.bj.bcebos.com/paddlex/official_inference_model/paddle3.0.0/PP-LCNet_x1_0_table_cls_infer.tar" \
    "https://github.com/PaddlePaddle/PaddleX/blob/develop/paddlex/configs/modules/table_classification/PP-LCNet_x1_0_table_cls.yaml"

# 文本图像矫正模块
echo "下载文本图像矫正模型..."
category="text_image_unwarping"
download_and_extract "$category" "UVDoc" \
    "https://paddle-model-ecology.bj.bcebos.com/paddlex/official_inference_model/paddle3.0.0/UVDoc_infer.tar" \
    "https://github.com/PaddlePaddle/PaddleX/blob/develop/paddlex/configs/modules/image_unwarping/UVDoc.yaml"

# 版面区域检测模块
echo "下载版面区域检测模型..."
category="layout_detection"
models=(
    "PP-DocLayout_plus-L"
    "PP-DocBlockLayout"
    "PP-DocLayout-L"
    "PicoDet_layout_1x_table"
    "RT-DETR-H_layout_3cls"
    "PicoDet_layout_1x"
    "RT-DETR-H_layout_17cls"
)

for model in "${models[@]}"; do
    download_and_extract "$category" "$model" \
        "https://paddle-model-ecology.bj.bcebos.com/paddlex/official_inference_model/paddle3.0.0/${model}_infer.tar" \
        "https://github.com/PaddlePaddle/PaddleX/blob/develop/paddlex/configs/modules/layout_detection/${model}.yaml"
done

# 文档图像方向分类模块
echo "下载文档图像方向分类模型..."
category="doc_img_orientation"
download_and_extract "$category" "PP-LCNet_x1_0_doc_ori" \
    "https://paddle-model-ecology.bj.bcebos.com/paddlex/official_inference_model/paddle3.0.0/PP-LCNet_x1_0_doc_ori_infer.tar" \
    "https://github.com/PaddlePaddle/PaddleX/blob/develop/paddlex/configs/modules/doc_text_orientation/PP-LCNet_x1_0_doc_ori.yaml"

# 文本行方向分类模块
echo "下载文本行方向分类模型..."
category="textline_orientation"
download_and_extract "$category" "PP-LCNet_x0_25_textline_ori" \
    "https://paddle-model-ecology.bj.bcebos.com/paddlex/official_inference_model/paddle3.0.0/PP-LCNet_x1_0_doc_ori_infer.tar" \
    "https://github.com/PaddlePaddle/PaddleX/blob/develop/paddlex/configs/modules/textline_orientation/PP-LCNet_x0_25_textline_ori.yaml"

# 文档类视觉语言模型模块
echo "下载文档视觉语言模型..."
category="doc_vlm"
models=(
    "PP-DocBee-2B"
    "PP-DocBee-7B"
    "PP-DocBee2-3B"
)

# for model in "${models[@]}"; do
#     download_and_extract "$category" "$model" \
#         "https://paddle-model-ecology.bj.bcebos.com/paddlex/official_inference_model/paddle3.0.0/${model}_infer.tar" \
#         ""
# done

echo "所有模型下载完成！"