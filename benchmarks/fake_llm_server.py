# benchmarks/fake_llm_server.py
"""本地假 OpenAI 兼容服务器：给 LLM 相关代码做计时/测试用。

支持三种请求（按 user 消息里的 JSON 特征识别）：
- 润色   {"lines": [...]}            -> {"lines":[{"id","text"}...]}（原样回显）
- 碎片合并 {"candidates": [...]}     -> {"text": "..."}（取最长候选）
- 策略复核 {"current_params": ...}    -> 原参数 + rationale

固定注入响应延迟，模拟真实网络往返。
"""
from __future__ import annotations

import json

import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


def _extract_payload(text: str) -> dict:
    """从 user 消息里提取最后一个真实 JSON 载荷（指令文本里也含花括号示例）。"""
    for key in ('{"lines"', '{"candidates"', '{"current_params"'):
        idx = text.rfind(key)
        if idx != -1:
            try:
                obj, _ = json.JSONDecoder().raw_decode(text[idx:])
                return obj
            except json.JSONDecodeError:
                continue
    return {}


def make_handler(latency_sec: float):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):  # 静默访问日志
            pass

        def _send(self, payload: dict):
            body = json.dumps(payload).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            if self.path.rstrip("/").endswith("/models"):
                self._send({"data": [{"id": "fake-model"}]})
            else:
                self.send_error(404)

        def do_POST(self):
            length = int(self.headers.get("Content-Length", 0))
            request = json.loads(self.rfile.read(length))
            time.sleep(latency_sec)

            messages = request.get("messages", [])
            user_content = ""
            for msg in reversed(messages):
                if msg.get("role") == "user":
                    user_content = msg.get("content", "")
                    break
            blob = _extract_payload(user_content)

            if "lines" in blob:
                # 回显时给文本追加「!」标记：测试据此断言每条结果被写回正确的下标。
                lines = [{"id": row["id"], "text": str(row["text"]) + "!"} for row in blob["lines"]]
                content = json.dumps({"lines": lines}, ensure_ascii=False)
            elif "candidates" in blob:
                best = max(blob["candidates"], key=len)
                content = json.dumps({"text": best}, ensure_ascii=False)
            elif "current_params" in blob:
                content = json.dumps(
                    {**blob["current_params"], "rationale": "ok"}, ensure_ascii=False
                )
            else:
                content = "{}"

            self._send(
                {
                    "choices": [
                        {"message": {"role": "assistant", "content": content}}
                    ]
                }
            )

    return Handler


def serve(latency_sec: float):
    """启动假服务器，返回 (server, port, stats)。在后台线程运行。

    stats: {"total_requests": int, "max_active": int}，线程安全更新，
    用于断言请求确实并发/串行。
    """
    server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(latency_sec))
    stats = {"total_requests": 0, "max_active": 0}
    state = {"active": 0}
    stats_lock = threading.Lock()
    original_thread = server.process_request_thread

    def _track(request, client_address):
        # ThreadingHTTPServer 在 process_request 里就拆出新线程并立即返回，
        # 真正的并发处理发生在 process_request_thread —— 必须包这一层。
        with stats_lock:
            stats["total_requests"] += 1
            state["active"] += 1
            stats["max_active"] = max(stats["max_active"], state["active"])
        try:
            original_thread(request, client_address)
        finally:
            with stats_lock:
                state["active"] -= 1

    server.process_request_thread = _track
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, server.server_address[1], stats
