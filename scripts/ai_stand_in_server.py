# -*- coding: utf-8 -*-
"""AI Stand-in 接力服务（Ollama + OpenAI 双协议兼容）。

职责：在 127.0.0.1:11435 模拟 Ollama(`/api/generate`) 与 OpenAI(`/v1/chat/completions`)
两类 API，把引擎的 AI 调用 prompt 落盘到 relay/pending/，等待外部 AI（本会话智能体）
读取 prompt 后真实生成应答，写入 relay/responses/<id>.json，再原样返回给引擎。

★ 设计纪律（用户 2026-09-22 确认的接管方案）：
- 引擎代码零改动：引擎只看到 OLLAMA_API_BASE / API_BASE_URL 指向本服务；
- 引擎记账照常：usage token 为估算值（len//4），应答文本走 validate_json_output
  原链路；source 仍为 local/cloud（透传），报告中由智能体标注「AI 内容=接力注入」；
- 不做模板填充：应答必须由智能体按真实 prompt 生成；超时（880s）未应答则
  返回 504，由引擎按原失败路径处理（不造假）。
- ★ 双协议共用同一信箱与同一纪律（不重复造轮子）：云端不可用时把引擎的
  API_BASE_URL 指向本服务 `/v1`，即可用**同一条**接力链接管云端 AI I/O。

用法：python scripts/ai_stand_in_server.py  （持久后台运行）
"""
import json
import os
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HOST = "127.0.0.1"
PORT = 11435
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RELAY = os.path.join(ROOT, "logs", "ai_stand_in")
PENDING = os.path.join(RELAY, "pending")
RESPONSES = os.path.join(RELAY, "responses")
ARCHIVE = os.path.join(RELAY, "archive")
WAIT_SECONDS = 880  # 引擎 local_timeout=900，留 20s 余量

os.makedirs(PENDING, exist_ok=True)
os.makedirs(RESPONSES, exist_ok=True)
os.makedirs(ARCHIVE, exist_ok=True)


def _est_tokens(text: str) -> int:
    """粗估 token（中英混合 ~4 chars/token）。仅用于引擎 usage 记账。"""
    return max(1, len(text or "") // 4)


def _relay_wait(req: dict) -> tuple[bool, str]:
    """把请求落盘 pending/，长轮询等待应答注入，归档并清理。返回 (是否应答, 文本)。"""
    rid = req["id"]
    pending_path = os.path.join(PENDING, rid + ".json")
    with open(pending_path, "w", encoding="utf-8") as f:
        json.dump(req, f, ensure_ascii=False, indent=1)
    resp_path = os.path.join(RESPONSES, rid + ".json")
    deadline = time.time() + WAIT_SECONDS
    answered = False
    response_text = ""
    while time.time() < deadline:
        if os.path.exists(resp_path):
            try:
                with open(resp_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                # 接受 {"response": ...}（Ollama）或 {"content": ...}（OpenAI）
                response_text = str(data.get("response", data.get("content", "")))
                answered = True
            except Exception:
                time.sleep(0.5)
                continue
            break
        time.sleep(0.5)
    try:
        with open(os.path.join(ARCHIVE, rid + ".req.json"), "w", encoding="utf-8") as f:
            json.dump(req, f, ensure_ascii=False, indent=1)
        if answered:
            with open(os.path.join(ARCHIVE, rid + ".resp.json"), "w", encoding="utf-8") as f:
                json.dump({"id": rid, "response": response_text}, f, ensure_ascii=False, indent=1)
        for p in (pending_path, resp_path):
            if os.path.exists(p):
                os.remove(p)
    except OSError:
        pass
    return answered, response_text


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):  # 静默默认访问日志
        pass

    def _json(self, code: int, obj: dict) -> None:
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path in ("/api/ps", "/api/tags"):
            # 让 router._detect_local 判定「本地模型可用」
            self._json(200, {"models": [{"name": "qwen2.5:7b", "size_vram": 0}]})
        elif self.path.rstrip("/") in ("/v1/models", "/models"):
            self._json(200, {"object": "list", "data": [
                {"id": "stand-in", "object": "model", "owned_by": "agent"}]})
        else:
            self._json(404, {"error": "not found"})

    def do_POST(self):
        path = self.path.rstrip("/")
        length = int(self.headers.get("Content-Length", 0))
        try:
            payload = json.loads(self.rfile.read(length) or b"{}")
        except Exception:
            self._json(400, {"error": "bad json"})
            return

        rid = "%s_%s" % (time.strftime("%H%M%S"), uuid.uuid4().hex[:6])
        is_openai = path in ("/v1/chat/completions", "/chat/completions")
        if path == "/api/generate":
            req = {
                "id": rid, "protocol": "ollama",
                "received_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                "model": payload.get("model", ""),
                "system": payload.get("system", ""),
                "prompt": payload.get("prompt", ""),
                "options": payload.get("options", {}),
            }
        elif is_openai:
            msgs = payload.get("messages") or []
            system = "\n".join(str(m.get("content", "")) for m in msgs
                               if isinstance(m, dict) and m.get("role") == "system")
            prompt = "\n".join(str(m.get("content", "")) for m in msgs
                               if isinstance(m, dict) and m.get("role") != "system")
            req = {
                "id": rid, "protocol": "openai",
                "received_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                "model": payload.get("model", ""),
                "system": system,
                "prompt": prompt,
                "temperature": payload.get("temperature"),
                "response_format": payload.get("response_format"),
            }
        else:
            self._json(404, {"error": "not found"})
            return

        answered, response_text = _relay_wait(req)
        if not answered:
            self._json(504, {"error": "stand-in relay timeout (%ds): no AI response" % WAIT_SECONDS})
            return

        if is_openai:
            self._json(200, {
                "id": "chatcmpl-" + rid,
                "object": "chat.completion",
                "created": int(time.time()),
                "model": req.get("model") or "stand-in",
                "choices": [{
                    "index": 0,
                    "message": {"role": "assistant", "content": response_text},
                    "finish_reason": "stop",
                }],
                "usage": {
                    "prompt_tokens": _est_tokens(req.get("system", "") + req.get("prompt", "")),
                    "completion_tokens": _est_tokens(response_text),
                    "total_tokens": _est_tokens(req.get("prompt", "")) + _est_tokens(response_text),
                },
            })
            return

        self._json(200, {
            "response": response_text,
            "done": True,
            "prompt_eval_count": _est_tokens(req["prompt"]),
            "eval_count": _est_tokens(response_text),
        })


if __name__ == "__main__":
    print("AI stand-in relay on http://%s:%d, relay dir: %s" % (HOST, PORT, RELAY), flush=True)
    ThreadingHTTPServer((HOST, PORT), Handler).serve_forever()
