# -*- coding: utf-8 -*-
from __future__ import annotations

"""
Confluence MCP 客户端 (ConfluenceMCPClient)
===========================================
通过 MCP stdio 协议调用 @atlassian-dc-mcp/confluence 获取页面内容。

与 ConfluenceClient（REST API 直连）并列，作为可选的 Confluence 接入方式。
根据 config.confluence_mode 自动选择。

依赖：
    - Node.js + npm（需安装 @atlassian-dc-mcp/confluence）
    - config.confluence_pat（Personal Access Token）
    - config.confluence_base_url（自动从 CONFLUENCE_URL 提取）
"""

import json
import os
import queue
import re
import subprocess
import sys
import threading
import time
from typing import Any
import logging

_log = logging.getLogger(__name__)


class ConfluenceMCPClient:
    """轻量 MCP stdio 客户端，仅封装 Confluence 读取操作。"""

    def __init__(self):
        self._proc: subprocess.Popen | None = None
        self._lock = threading.Lock()
        self._request_id = 0
        self._pending: dict[int, queue.Queue] = {}
        self._reader_thread: threading.Thread | None = None
        self._running = False

    # ------------------------------------------------------------------
    # 生命周期
    # ------------------------------------------------------------------

    def start(self, api_base_path: str, token: str, timeout: float = 60.0):
        """启动 MCP 子进程并完成初始化握手。"""
        if self._running:
            return

        env = dict(os.environ)
        env["CONFLUENCE_API_BASE_PATH"] = api_base_path
        env["CONFLUENCE_API_TOKEN"] = token

        if sys.platform == "win32":
            cmd = ["cmd", "/c", "npx", "-y", "@atlassian-dc-mcp/confluence"]
        else:
            cmd = ["npx", "-y", "@atlassian-dc-mcp/confluence"]

        self._proc = subprocess.Popen(
            cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=env,
            text=True,
            encoding="utf-8",
            bufsize=1,
            shell=False,
        )

        # 启动 reader 线程
        self._running = True
        self._reader_thread = threading.Thread(target=self._read_loop, daemon=True)
        self._reader_thread.start()
        # 后台排出 stderr，避免管道阻塞子进程
        threading.Thread(target=self._stderr_loop, daemon=True).start()

        # MCP 初始化握手
        init_result = self._call(
            "initialize",
            {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "ai-test", "version": "1.0"},
            },
            timeout,
        )
        if "error" in init_result:
            raise RuntimeError(f"MCP initialize failed: {init_result['error']}")

        # 发送 initialized 通知
        self._send_notification("notifications/initialized")

    def stop(self):
        """停止 MCP 子进程。"""
        self._running = False
        if self._proc:
            try:
                self._proc.terminate()
                self._proc.wait(timeout=5)
            except Exception:
                try:
                    self._proc.kill()
                except Exception as _e:
                    _log.debug("[_mcp_client] stop 异常: %s", _e, exc_info=True)
            self._proc = None

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.stop()

    # ------------------------------------------------------------------
    # 公共接口（与 ConfluenceClient 兼容）
    # ------------------------------------------------------------------

    def fetch_page(self, page_id: str) -> dict[str, Any]:
        """
        获取单个 Confluence 页面。

        返回格式与 ConfluenceClient 兼容：
            {"success": True, "data": {"text": <markdown>, "title": <title>, "page_id": <id>}}
        """
        result = self._call(
            "tools/call",
            {
                "name": "confluence_getContent",
                "arguments": {"contentId": page_id},
            },
        )
        return self._parse_page_result(result, page_id)

    def fetch_confluence_all(self, page_ids: list[str] = None) -> dict[str, Any]:
        """
        获取多个 Confluence 页面，合并为一份文档。
        多页面时使用 ThreadPoolExecutor 并行拉取（每个 page 独立 MCP 调用）。

        返回格式与 ConfluenceClient.fetch_confluence_all() 兼容：
            {"success": True, "data": {"text": <merged>, "title": <merged>, "pages": [...]}}
        """
        if page_ids is None:
            from src.core.config_loader import config

            page_ids = config.confluence_page_ids
        if not page_ids:
            return {"success": False, "error": "无 page_ids"}

        # ★ 单页面：直接串行（避免线程池开销）
        if len(page_ids) == 1:
            r = self.fetch_page(page_ids[0])
            if not r.get("success"):
                return {"success": False, "error": r.get("error", "获取失败")}
            return {
                "success": True,
                "data": {
                    "text": r["data"].get("text", ""),
                    "title": r["data"].get("title", ""),
                    "pages": [r["data"]],
                },
            }

        # ★ 多页面：并行拉取（每个 page 独立 MCP 网络 I/O）
        from concurrent.futures import ThreadPoolExecutor, as_completed

        _log.info("[ConfluenceMCP] 并行拉取 %d 个页面", len(page_ids))
        pages = []
        errors = []
        with ThreadPoolExecutor(max_workers=min(len(page_ids), 5)) as executor:
            future_map = {
                executor.submit(self.fetch_page, pid): pid for pid in page_ids
            }
            for fut in as_completed(future_map):
                pid = future_map[fut]
                try:
                    r = fut.result()
                    if r.get("success"):
                        pages.append(r["data"])
                    else:
                        errors.append((pid, r.get("error", "未知")))
                except Exception as e:
                    errors.append((pid, str(e)))

        if errors:
            for pid, err in errors:
                _log.error("[ConfluenceMCP] 页面 %s 获取失败: %s", pid, err)

        if not pages:
            return {"success": False, "error": "所有页面获取失败"}

        merged_text = "\n\n---\n\n".join(
            f"## {p.get('title', 'Untitled')}\n\n{p.get('text', '')}" for p in pages
        )
        merged_title = " + ".join(p.get("title", "") for p in pages)

        return {
            "success": True,
            "data": {
                "text": merged_text,
                "title": merged_title,
                "pages": pages,
            },
        }

    def search(self, cql: str, limit: int = 10) -> dict[str, Any]:
        """搜索 Confluence 内容（CQL 查询）。"""
        return self._call(
            "tools/call",
            {
                "name": "confluence_searchContent",
                "arguments": {"cql": cql, "limit": limit},
            },
        )

    # ------------------------------------------------------------------
    # 内部：MCP 协议通信
    # ------------------------------------------------------------------

    def _send_notification(self, method: str):
        """发送 MCP 通知（无需响应）。"""
        if not self._proc or not self._proc.stdin:
            return
        msg = json.dumps({"jsonrpc": "2.0", "method": method}, ensure_ascii=False)
        with self._lock:
            try:
                self._proc.stdin.write(msg + "\n")
                self._proc.stdin.flush()
            except Exception as _e:
                _log.debug(
                    "[_mcp_client] _send_notification 异常: %s", _e, exc_info=True
                )

    def _call(self, method: str, params: dict, timeout: float = 60.0) -> dict:
        """发送 MCP 请求并等待响应。"""
        if not self._proc or not self._proc.stdin:
            return {"error": "进程未启动"}

        req_id = self._request_id
        self._request_id += 1
        q = queue.Queue()
        self._pending[req_id] = q

        req = {
            "jsonrpc": "2.0",
            "id": req_id,
            "method": method,
            "params": params,
        }
        with self._lock:
            try:
                self._proc.stdin.write(json.dumps(req, ensure_ascii=False) + "\n")
                self._proc.stdin.flush()
            except Exception as e:
                self._pending.pop(req_id, None)
                return {"error": f"stdin write failed: {e}"}

        try:
            return q.get(timeout=timeout)
        except queue.Empty:
            self._pending.pop(req_id, None)
            return {"error": "timeout"}

    def _read_loop(self):
        """后台线程：持续读取 MCP 进程 stdout 并分发响应（支持多行 JSON）。"""
        buffer = ""
        try:
            while self._running and self._proc and self._proc.stdout:
                line = self._proc.stdout.readline()
                if not line:
                    break
                buffer += line
                try:
                    msg = json.loads(buffer.strip())
                except json.JSONDecodeError:
                    continue
                buffer = ""
                rid = msg.get("id")
                if rid is not None and rid in self._pending:
                    self._pending[rid].put(msg)
        except Exception as _e:
            _log.debug("[_mcp_client] _read_loop 异常: %s", _e, exc_info=True)

    def _stderr_loop(self):
        """后台排出 stderr，避免管道满阻塞子进程。"""
        try:
            while self._running and self._proc and self._proc.stderr:
                self._proc.stderr.read(4096)
        except Exception as _e:
            _log.debug("[_mcp_client] _stderr_loop 异常: %s", _e, exc_info=True)

    # ------------------------------------------------------------------
    # 结果解析
    # ------------------------------------------------------------------

    def _parse_page_result(self, result: dict, page_id: str) -> dict[str, Any]:
        """将 MCP 返回结果转换为与 ConfluenceClient 兼容的格式。"""
        if "error" in result:
            return {"success": False, "error": result["error"]}

        content_list = result.get("result", {}).get("content", [])
        text_parts = []
        for c in content_list:
            if c.get("type") == "text":
                text_parts.append(c.get("text", ""))

        raw_json = "".join(text_parts)
        try:
            data = json.loads(raw_json)
        except json.JSONDecodeError:
            return {"success": False, "error": f"JSON parse failed: {raw_json[:200]}"}

        if not data.get("success"):
            return {"success": False, "error": data.get("error", "MCP fetch failed")}

        page_data = data.get("data", {})
        title = page_data.get("title", "")
        body = page_data.get("body", {}).get("storage", {}).get("value", "")

        # HTML → Markdown
        markdown = self._html_to_markdown(body)

        return {
            "success": True,
            "data": {
                "text": markdown,
                "title": title,
                "page_id": page_data.get("id", page_id),
                "version": page_data.get("version", {}).get("number", 0),
                "raw_html": body,
            },
        }

    # ------------------------------------------------------------------
    # HTML → Markdown（复用 ConfluenceClient 的转换逻辑）
    # ------------------------------------------------------------------

    def _html_to_markdown(self, html: str) -> str:
        """将 Confluence Storage HTML 转为 Markdown。"""
        from src.pipeline.steps.step1.confluence_client import ConfluenceClient

        return ConfluenceClient._html_to_markdown(html)
