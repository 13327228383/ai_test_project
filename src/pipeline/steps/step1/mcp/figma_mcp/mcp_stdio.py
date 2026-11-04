# -*- coding: utf-8 -*-
"""
MCPStdioClient — JSON-RPC 2.0 通过 stdio 与 MCP Server 通信
"""

import json
import os
import subprocess
import sys
import threading
import queue
from typing import Any
import logging

_log = logging.getLogger(__name__)


class MCPStdioClient:
    """MCP JSON-RPC 2.0 客户端，通过子进程 stdio 通信。"""

    def __init__(
        self,
        command: str,
        args: list[str] = None,
        env: dict[str, str] = None,
        timeout: float = 120.0,
    ):
        self._command = command
        self._args = args or []
        self._env = env or {}
        self._timeout = timeout
        self._process: subprocess.Popen = None
        self._lock = threading.Lock()
        self._request_id = 0
        self._pending: dict[int, queue.Queue] = {}
        self._reader_thread: threading.Thread = None
        self._running = False
        self._server_info = None

    # ------------------------------------------------------------------
    # 生命周期
    # ------------------------------------------------------------------

    def start(self):
        """启动 MCP Server 子进程并开始读取 stdout。"""
        if self._running:
            return

        full_env = dict(os.environ)
        full_env.update(self._env)
        full_env.setdefault("FRAMELINK_TELEMETRY", "off")
        full_env.setdefault("DO_NOT_TRACK", "1")
        # 清除代理设置，避免通过不可用的代理连接 Figma API
        for _proxy_key in (
            "HTTP_PROXY",
            "HTTPS_PROXY",
            "http_proxy",
            "https_proxy",
            "ALL_PROXY",
            "all_proxy",
        ):
            full_env.pop(_proxy_key, None)
        full_env["NO_PROXY"] = "*"
        full_env["no_proxy"] = "*"

        # Windows 上 .cmd 文件需要 cmd /c 包装
        if sys.platform == "win32":
            cmd_line = ["cmd", "/c", self._command] + self._args
        else:
            cmd_line = [self._command] + self._args

        self._process = subprocess.Popen(
            cmd_line,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=full_env,
            text=True,
            encoding="utf-8",
            bufsize=1,
            shell=False,
        )

        self._running = True
        self._reader_thread = threading.Thread(target=self._read_loop, daemon=True)
        self._reader_thread.start()

        # 后台线程收集 stderr 避免管道阻塞
        threading.Thread(target=self._stderr_loop, daemon=True).start()

        _log.info("  [MCP] Server 启动成功 (PID=%d)", self._process.pid)

    def stop(self):
        """优雅终止 MCP Server 子进程。"""
        self._running = False
        if self._process is not None:
            try:
                self._process.stdin.close()
            except Exception as _e:
                _log.debug("[mcp_stdio] stop 异常: %s", _e, exc_info=True)
            try:
                self._process.terminate()
                self._process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self._process.kill()
                self._process.wait(timeout=3)
            except Exception as _e:
                _log.debug("[mcp_stdio] stop 异常: %s", _e, exc_info=True)
            self._process = None
        # 通知所有等待者
        for q in self._pending.values():
            try:
                q.put_nowait(None)
            except Exception as _e:
                _log.debug("[MCP] 关闭队列失败: %s", _e, exc_info=True)
        self._pending.clear()
        _log.info("  [MCP] Server 已关闭")

    def is_alive(self) -> bool:
        """检查 MCP Server 进程是否存活。"""
        if self._process is None:
            return False
        return self._process.poll() is None

    # ------------------------------------------------------------------
    # MCP 协议握手
    # ------------------------------------------------------------------

    def initialize(self) -> dict[str, Any]:
        """MCP initialize 握手。返回服务端能力信息。"""
        result = self._send_request(
            "initialize",
            {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {
                    "name": "e2e-test-engine-figma-mcp-client",
                    "version": "1.0.0",
                },
            },
        )
        self._server_info = result
        # 发送 initialized 通知（不等待响应）
        self._send_notification("notifications/initialized")
        return result

    # ------------------------------------------------------------------
    # 工具调用
    # ------------------------------------------------------------------

    def call_tool(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        """
        调用 MCP 工具并返回结果内容。
        返回: MCP tool result content —— dict 格式
        """
        result = self._send_request(
            "tools/call",
            {
                "name": name,
                "arguments": arguments,
            },
        )
        return self._extract_tool_content(result)

    # ------------------------------------------------------------------
    # JSON-RPC 核心
    # ------------------------------------------------------------------

    def _send_request(self, method: str, params: dict) -> dict:
        """发送 JSON-RPC 请求并等待匹配 id 的响应。"""
        with self._lock:
            self._request_id += 1
            req_id = self._request_id

        payload = {
            "jsonrpc": "2.0",
            "id": req_id,
            "method": method,
            "params": params,
        }

        response_queue: queue.Queue = queue.Queue()
        self._pending[req_id] = response_queue

        self._write_line(json.dumps(payload, ensure_ascii=False))

        try:
            result = response_queue.get(timeout=self._timeout)
        except queue.Empty:
            self._pending.pop(req_id, None)
            raise RuntimeError("MCP 请求超时 (method=%s, id=%d)" % (method, req_id))

        if result is None:
            raise RuntimeError("MCP Server 已断开")

        if "error" in result:
            err = result["error"]
            raise RuntimeError(
                "MCP 错误: %s (code=%s)"
                % (err.get("message", "unknown"), err.get("code", "?"))
            )

        return result.get("result", {})

    def _send_notification(self, method: str, params: dict = None):
        """发送 JSON-RPC 通知（无 id，无响应）。"""
        payload = {
            "jsonrpc": "2.0",
            "method": method,
            "params": params or {},
        }
        self._write_line(json.dumps(payload, ensure_ascii=False))

    def _write_line(self, text: str):
        """向 MCP Server 写入一行 JSON（线程安全）。"""
        if self._process is None or not self.is_alive():
            raise RuntimeError("MCP Server 未运行")
        try:
            self._process.stdin.write(text + "\n")
            self._process.stdin.flush()
        except (BrokenPipeError, OSError) as e:
            raise RuntimeError("MCP Server 写入失败: %s" % e)

    # ------------------------------------------------------------------
    # stdout / stderr 读取
    # ------------------------------------------------------------------

    def _read_loop(self):
        """后台线程：持续读取 stdout，按行解析 JSON-RPC 响应。"""
        buffer = ""
        while self._running and self._process is not None:
            try:
                line = self._process.stdout.readline()
            except Exception as _rx1:
                _log.debug(
                    "[mcp_stdio] 异常降级(break): %s", str(_rx1)[:60], exc_info=True
                )
                break
            if not line:
                break
            line = line.strip()
            if not line:
                continue
            # 可能一行中有多个 JSON 对象（换行缺失的边界情况）
            # 逐行处理
            try:
                msg = json.loads(line)
            except json.JSONDecodeError as _e:
                _log.debug("[mcp_stdio] 消息 JSON 解析失败跳过(§5): %s", _e)
                continue
            msg_id = msg.get("id")
            if msg_id is not None and msg_id in self._pending:
                self._pending[msg_id].put(msg)
            # 无 id 的消息（如通知/进度）静默丢弃
        self._running = False

    def _stderr_loop(self):
        """后台线程：读取 stderr 避免管道阻塞，过滤遥测噪音。"""
        _noise_patterns = ("Usage telemetry", "FRAMELINK_TELEMETRY", "DO_NOT_TRACK")
        while self._running and self._process is not None:
            try:
                line = self._process.stderr.readline()
            except Exception as _rx2:
                _log.debug(
                    "[mcp_stdio] 异常降级(break): %s", str(_rx2)[:60], exc_info=True
                )
                break
            if not line:
                break
            line = line.strip()
            if line and not any(p in line for p in _noise_patterns):
                # ★ 修复：原误用 print 风格 file=sys.stderr 传给 _log.info（logging 无此参数
                # → TypeError），致 _stderr_loop 线程异常退出，MCP 子进程 stderr 不再被消费，
                # 管道缓冲区满后可能反压阻塞子进程 stdout 写入。logging 已统一输出，无需 file。
                _log.info("  [MCP/stderr] %s", line)

    # ------------------------------------------------------------------
    # 辅助
    # ------------------------------------------------------------------

    @staticmethod
    def _extract_tool_content(result: dict) -> dict:
        """从 MCP tools/call 响应中提取文本内容并尝试解析 JSON。"""
        content = result.get("content", [])
        if not content:
            return {}

        # content 是 [{type: "text", text: "..."}] 的列表
        texts = []
        for item in content:
            if item.get("type") == "text":
                texts.append(item.get("text", ""))

        full_text = "\n".join(texts)

        # 尝试解析为 JSON
        try:
            return json.loads(full_text)
        except json.JSONDecodeError as e:
            _log.debug("JSON解析失败，返回原始文本: %s", e)
        # 返回原始文本
        return {"_raw_text": full_text}
