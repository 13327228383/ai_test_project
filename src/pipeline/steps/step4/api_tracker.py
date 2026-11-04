# -*- coding: utf-8 -*-
"""
API 轨迹追踪器 (ApiTracker)
============================
在 Playwright 浏览器启动时注册网络拦截器，记录每个操作触发的 HTTP 请求/响应。

设计原则：
- 零 AI Token 消耗 — 纯 Playwright 网络层监听
- 按时间戳分段，支持「操作后查询」模式
- 自动跳过静态资源

★ W3 T05 关键改动 [已复核-DeepSeek-V4-Flash 2026-08-05]（2026-08-05）：
  - _recent_requests 属性别名（修复 response_time/network/header 三族恒 UNKNOWN）
  - on_response 补 elapsed_ms（ts 差）—— 性能基线数据源。
"""

import time, json
from urllib.parse import urlparse

import logging

_log = logging.getLogger(__name__)


class ApiTracker:
    _MAX_CALLS = 1000

    def __init__(self, page):
        self._page = page
        self._calls: list[dict] = []
        self._started = False

    # ★ W3 T05  + BUG-2 修复 :
    #   _recent_requests 改为 @property 直接返回 self._calls —— 消费方
    #   (_verify_perf/_verify_network/_verify_header/_single.py:185) 原本就读它。
    #   原实现 __init__ 里 `self._recent_requests = self._calls` 是同一对象引用；
    #   on_request 超 _MAX_CALLS 时 `self._calls = self._calls[-1000:]` rebind 新对象
    #   → 别名变陈旧（截断后 is 比对 False，消费方读到旧列表）。
    #   改 property 后始终返回当前列表，接口不变，四处消费方无需改动。
    @property
    def _recent_requests(self) -> list[dict]:
        return self._calls

    def start(self) -> "ApiTracker":
        if self._started:
            return self

        async def on_request(request):
            ts = time.time()
            url = request.url
            path = urlparse(url).path.lower()
            if any(
                path.endswith(e)
                for e in (
                    ".js",
                    ".css",
                    ".png",
                    ".jpg",
                    ".gif",
                    ".svg",
                    ".ico",
                    ".woff",
                    ".ttf",
                    ".map",
                )
            ):
                return
            if "favicon" in path or "hot-update" in path:
                return

            post_data = None
            try:
                post_data = request.post_data
            except Exception as _pass_e:
                _log.warning("api_tracker.py:42 异常被吞: %s", _pass_e, exc_info=True)
            if post_data:
                try:
                    post_data = json.loads(post_data)
                except Exception as _pass_e:
                    _log.warning(
                        "api_tracker.py:45 异常被吞: %s", _pass_e, exc_info=True
                    )
            self._calls.append(
                {
                    "ts": ts,
                    "url": url,
                    "method": request.method,
                    "post_data": post_data,
                    "has_response": False,
                    "status": None,
                    "resp_body": None,
                }
            )
            if len(self._calls) > self._MAX_CALLS:
                self._calls = self._calls[-self._MAX_CALLS :]

        async def on_response(response):
            url = response.url
            now = time.time()
            for call in reversed(self._calls):
                if call["url"] == url and not call["has_response"]:
                    call["has_response"] = True
                    call["status"] = response.status
                    # ★ W3 T05 (2026-08-05): 补 elapsed_ms（响应 ts 差），
                    #   response_time/network/header 三族依赖它（原恒 UNKNOWN）。
                    call["elapsed_ms"] = max(int((now - call["ts"]) * 1000), 0)
                    call["resp_bytes"] = 0  # ★ perf_gate: payload_bytes 数据源
                    # ★ SecurityScanner 集成: 存储响应头供安全头检测
                    try:
                        call["resp_headers"] = dict(response.headers)
                    except Exception:
                        call["resp_headers"] = {}
                    try:
                        body = await response.text()
                        call["resp_bytes"] = len(body.encode("utf-8")) if body else 0
                        if body and len(body) < 10000:
                            try:
                                call["resp_body"] = json.loads(body)
                            except Exception:
                                call["resp_body"] = body[:500]
                    except Exception as _pass_e:
                        _log.warning(
                            "api_tracker.py:64 异常被吞: %s", _pass_e, exc_info=True
                        )
                    break

        self._page.on("request", on_request)
        self._page.on("response", on_response)
        self._started = True
        return self

    def get_calls_since(self, start_time: float) -> list[dict]:
        return [c for c in self._calls if c["ts"] >= start_time]

    def clear(self):
        self._calls.clear()

    def summary(self) -> str:
        lines = []
        for c in self._calls[-20:]:
            post = ""
            resp = ""
            if c.get("post_data"):
                post = " | body:" + json.dumps(c["post_data"], ensure_ascii=False)[:200]
            if c.get("resp_body"):
                resp = " | resp:" + json.dumps(c["resp_body"], ensure_ascii=False)[:200]
            lines.append(
                "  [%s] %s → %s%s%s"
                % (c["method"], c["url"], c.get("status", "?"), post, resp)
            )
        return "\n".join(lines)
