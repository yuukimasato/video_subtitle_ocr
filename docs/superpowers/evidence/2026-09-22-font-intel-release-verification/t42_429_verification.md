# T4.2 三条服务端链路 429/降级专项验证（2026-09-22）

命令：QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest tests/test_llm_client_backoff.py "tests/test_translation.py::TestDegradationChain::test_cloud_llm_api_error_falls_to_sakura" "tests/test_font_etl_llm.py::TestDegradation" tests/test_font_identify_stage.py -v

结果：65 passed in 0.46s（exit 0）

覆盖：
- LLM API 429 有界退避（tests/test_llm_client_backoff.py，全文件）
- 翻译链：云端 LlmApiError → Sakura 降级（TestDegradationChain）
- ETL 结构化链：LlmApiError → 单批 pending、其余批次继续 + RetryError 归一（TestDegradation）
- 权重下载链：bounded_download 重试/耗尽/总预算/urllib 兜底（test_font_identify_stage.py 362-432 行各节点）
