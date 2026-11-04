# -*- coding: utf-8 -*-
"""
Figma 客户端 (FigmaClient) — v3 MCP 版
======================================
底层改为通过 MCP 协议与 figma-developer-mcp 通信，彻底解决 429 限流问题。
内建 MySQL 缓存层（figma_mcp_cache 表），缓存优先 → MCP 调用 → 更新缓存。

★ 2026-06-23: 批量节点拉取改用 Figma REST API（MCP 协议仅支持单个 nodeId，
  逐节点调 MCP 耗时 = N × 3~10s，改用 /v1/files/:key/nodes?ids=... 一次拉 15 个）。

API 与 v2 完全兼容：

    from src.pipeline.steps.step1.figma_client import figma_client
    result = figma_client.fetch_figma_file()
"""

import logging
from src.core.guarded import guard
from typing import Any

from src.core.config_loader import config

_log = logging.getLogger(__name__)


class FigmaClient:
    """Figma API 封装（v3 MCP 版）。

    ★ 修复（2026-06-22）：原本 __init__ 把 config.figma_* 快照到实例字段，
    模块级单例导致用户在 UI 改 PAT/file_key 后客户端读不到。改成 @property。
    """

    def __init__(self) -> None:
        self._mcp = None
        self._cache = None  # ★ 延迟初始化 MySQL 缓存实例

    @property
    def _figma_file_key(self) -> str:
        return config.figma_file_key

    @property
    def _figma_token(self) -> str:
        return config.figma_key

    def _ensure_mcp(self):
        """延迟初始化 MCP 客户端。
        ★ 显式传递 token，确保 MCP Server 进程拿到正确的 FIGMA_API_KEY，
        而非依赖全局 config.figma_key（可能在无用户上下文时被调用返回空）。
        """
        if self._mcp is None:
            if not self._figma_token:
                _log.info("[FigmaClient] FIGMA_KEY 未配置，跳过 MCP 初始化")
                return
            from src.pipeline.steps.step1.mcp.figma_mcp import (
                FigmaMCPClient,
                get_mcp_session,
                start_mcp_server,
            )

            start_mcp_server(token=self._figma_token)
            sess = get_mcp_session()
            if sess is None:
                _log.info("[FigmaClient] MCP 会话未建立（token 可能为空），跳过")
                return
            self._mcp = FigmaMCPClient(session=sess)

    def _ensure_cache(self):
        """延迟初始化 MySQL 缓存实例（不启动 MCP 进程）。"""
        if self._cache is None:
            from src.pipeline.steps.step1.figma_cache import FigmaCache

            self._cache = FigmaCache()

    # ==================================================================
    # 公共 API（与 v2 完全兼容）
    # ==================================================================

    def fetch_figma_file(
        self, file_key: str = None, force_refresh: bool = False, depth: int = None
    ) -> dict[str, Any]:
        """
        拉取 Figma 文件的完整结构化数据。

        参数:
            file_key:      文件 KEY，不传则使用 config.py 中的配置
            force_refresh: True = 跳过所有缓存强制拉取
            depth:         查询深度（None=默认全量, 1=仅顶层结构）
        """
        target_key = file_key or self._figma_file_key
        if not target_key:
            return self._error("Figma file_key 未配置")

        self._ensure_mcp()
        if self._mcp is None:
            return self._error("Figma MCP 未初始化（FIGMA_KEY 未配置或 MCP 启动失败）")
        return self._mcp.fetch_file(
            file_key=target_key,
            force_refresh=force_refresh,
            depth=depth if depth is not None else 0,
        )

    def fetch_figma_file_nodes(
        self,
        node_ids: list[str] = None,
        file_key: str = None,
        force_refresh: bool = False,
    ) -> dict[str, Any]:
        """
        按需拉取指定节点的详细数据。

        ★ 2026-06-23: 多节点时使用 Figma REST API 批量拉取（MCP 逐节点太慢）。
        单节点仍走 MCP（享受数据简化能力）。

        参数:
            node_ids:      节点 ID 列表，为空则退化为全量拉取
            file_key:      文件 KEY
            force_refresh: True = 跳过缓存强制拉取
        """
        target_key = file_key or self._figma_file_key
        if not target_key:
            return self._error("Figma file_key 未配置")
        if not node_ids:
            return self.fetch_figma_file(
                file_key=target_key, force_refresh=force_refresh
            )

        # ★ 多节点 → REST API 批量拉取（一次 API 调用拉整批）
        if len(node_ids) > 1:
            # 先检查缓存（force_refresh 时跳过缓存检查）
            self._ensure_cache()
            cached_nodes = {}
            missing_ids = []
            if not force_refresh:
                for nid in node_ids:
                    cached = self._cache.get(target_key, nid, depth=6)
                    if cached is not None:
                        cached_nodes[nid] = cached
                    else:
                        missing_ids.append(nid)
            else:
                missing_ids = list(node_ids)
            if not missing_ids:
                _log.info(
                    "  [REST Cache] 所有节点缓存命中 (%d/%d)",
                    len(cached_nodes),
                    len(node_ids),
                )
                return self._ok(
                    file_key=target_key,
                    nodes=cached_nodes,
                    name=self._get_file_name(target_key),
                )
            # 调用 REST API 批量拉取缺失节点
            result = self._fetch_nodes_rest_batch(target_key, missing_ids)
            if result.get("success"):
                merged = {**cached_nodes, **result["data"].get("nodes", {})}
                return self._ok(
                    file_key=target_key,
                    nodes=merged,
                    name=result["data"].get("name", ""),
                )
            # REST API 失败 → 回退 MCP
            _log.warning(
                "  [REST] 批量拉取失败，回退 MCP: %s", result.get("error", "")[:100]
            )
            # ★ REST 失败时 force_refresh 保持原值传给 MCP 回退

        # ★ 单节点 / force_refresh / REST 失败回退 → MCP
        self._ensure_mcp()
        if self._mcp is None:
            return self._error("Figma MCP 未初始化（FIGMA_KEY 未配置或 MCP 启动失败）")
        return self._mcp.fetch_nodes(
            file_key=target_key,
            node_ids=node_ids,
            force_refresh=force_refresh,
        )

    # ==================================================================
    # 缓存访问（供 SmartFigmaFetcher 使用）
    # ==================================================================

    @property
    def cache(self):
        """暴露缓存对象供 SmartFigmaFetcher 查询（纯 MySQL，不启动 MCP）。"""
        self._ensure_cache()
        return self._cache

    def download_images(
        self, file_key: str = None, nodes=None, local_path: str = "", png_scale: int = 2
    ) -> dict[str, Any]:
        """下载 Figma 图片节点到 --image-dir 目录（委托 MCP）。

        Args:
            file_key: Figma 文件 key（默认用 config.figma_file_key）
            nodes:    图片节点列表 [{nodeId, fileName, imageRef?}, ...]
            local_path: 相对 --image-dir 的子目录
            png_scale:  PNG 缩放比
        """
        target_key = file_key or self._figma_file_key
        if not target_key or not nodes:
            return self._error("file_key 或 nodes 为空")
        self._ensure_mcp()
        if self._mcp is None:
            return self._error("Figma MCP 未初始化")
        return self._mcp.download_images(
            file_key=target_key,
            nodes=nodes,
            local_path=local_path,
            png_scale=png_scale,
        )

    # ==================================================================
    # REST API 批量节点拉取（绕过 MCP，一次 API 调用拉多节点）
    # ==================================================================

    def _fetch_nodes_rest_batch(
        self, file_key: str, node_ids: list[str], depth: int = 6
    ) -> dict[str, Any]:
        """
        通过 Figma REST API 批量拉取节点数据。
        GET /v1/files/:key/nodes?ids=id1,id2,...&depth=N

        ★ MCP 协议的 get_figma_data 只支持单个 nodeId，逐节点调 MCP
          对大型设计稿极慢（60 节点 × 3-10s = 180-600s）。此方法绕过 MCP，
          直接调用 Figma REST API，一次请求拉取整批节点。
        """
        import requests

        figma_key = self._figma_token
        if not figma_key:
            return self._error("Figma API Token 未配置，无法使用 REST API 批量拉取")

        ids_param = ",".join(node_ids)
        url = f"https://api.figma.com/v1/files/{file_key}/nodes"
        _log.info(
            "  [REST] 批量拉取 %d 个节点: %s...",
            len(node_ids),
            ids_param[:60] + ("..." if len(ids_param) > 60 else ""),
        )

        try:
            resp = requests.get(
                url,
                params={"ids": ids_param, "depth": depth},
                headers={"X-Figma-Token": figma_key},
                timeout=120,
            )
            if resp.status_code == 429:
                retry_after = resp.headers.get("Retry-After", "未知")
                return self._error("Figma API 限流 (429)，请 %ss 后重试" % retry_after)
            if resp.status_code == 403:
                return self._error(
                    "Figma API 403 Forbidden，Token 无效或无权限访问此文件"
                )
            resp.raise_for_status()
        except Exception as e:
            return self._error("Figma REST API 请求失败: %s" % str(e)[:120])

        try:
            data = resp.json()
        except Exception as e:
            return self._error("Figma REST API 响应解析失败: %s" % str(e)[:120])

        # ★ 缓存每个节点到 MySQL（供后续增量/缓存模式使用）
        raw_nodes = data.get("nodes", {})
        if raw_nodes:
            with guard("_fetch_nodes_rest_batch: self._ensure_cache()", log=_log, level=logging.WARNING):
                self._ensure_cache()
                for nid, node_info in raw_nodes.items():
                    self._cache.set(file_key, nid, depth, node_info)
                _log.info("  [REST Cache] %d 个节点已缓存", len(raw_nodes))

        return self._ok(data)

    def _get_file_name(self, file_key: str) -> str:
        """从缓存中读取文件名（不调用 API）。"""
        self._ensure_cache()
        full = self._cache.get_full_file(file_key)
        if full:
            return full.get("name", "")
        return ""

    # ==================================================================
    # 工具方法
    # ==================================================================

    @staticmethod
    def _ok(data: Any = None, **extra) -> dict[str, Any]:
        if data is None:
            data = dict(extra) if extra else {}
        return {"success": True, "data": data}

    @staticmethod
    def _error(message: str, status_code: int = None) -> dict[str, Any]:
        result = {"success": False, "error": message}
        if status_code is not None:
            result["status_code"] = status_code
        return result


# =============================================================================
# 模块级单例（保持与 v2 兼容）
# =============================================================================
# ★ 方案C 改造：figma_client 改为 PerProjectRegistry，按 project 隔离
from src.core.utils.per_project_registry import PerProjectRegistry

figma_client = PerProjectRegistry(FigmaClient)
