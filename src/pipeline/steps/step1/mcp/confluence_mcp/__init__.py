# -*- coding: utf-8 -*-
"""
Confluence MCP 客户端 (package 模式)
====================================
facade，重导出 ConfluenceMCPClient / FileModeClient / get_confluence_client。

子模块：
    _mcp_client.py  — ConfluenceMCPClient (MCP stdio 协议)
    _file_mode.py   — FileModeClient (本地文件模式)

所有原有导入路径保持不变（通过本文件重导出保证向后兼容）：
    from src.pipeline.steps.step1.mcp.confluence_mcp import (
        ConfluenceMCPClient, FileModeClient, get_confluence_client
    )
"""

import logging

_log = logging.getLogger(__name__)

from ._mcp_client import ConfluenceMCPClient
from ._file_mode import FileModeClient


def get_confluence_client():
    """根据 CONFLUENCE_MODE 返回对应的 Confluence 客户端实例。"""
    from src.core.config_loader import config

    mode = config.confluence_mode
    if mode == "file":
        _log.info("[Confluence] 使用 FileMode 模式 → 从 CONFLUENCE_URL 读取本地文件")
        return FileModeClient()
    if mode == "mcp":
        pat = config.confluence_pat
        base = config.confluence_mcp_api_base_path
        if not pat or not base:
            _log.info("[Confluence] MCP 配置不完整，回退到 REST 模式")
            from src.pipeline.steps.step1.confluence_client import confluence_client

            return confluence_client
        _log.info("[Confluence] 使用 MCP 模式 → %s", base)
        client = ConfluenceMCPClient()
        try:
            client.start(base, pat)
            return client
        except Exception as e:
            _log.warning("[Confluence] MCP 启动失败，回退到 REST: %s", e)
            from src.pipeline.steps.step1.confluence_client import confluence_client

            return confluence_client
    else:
        from src.pipeline.steps.step1.confluence_client import confluence_client

        return confluence_client


__all__ = ["ConfluenceMCPClient", "FileModeClient", "get_confluence_client"]
