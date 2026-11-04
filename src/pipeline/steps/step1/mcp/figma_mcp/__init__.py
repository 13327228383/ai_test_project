# -*- coding: utf-8 -*-
"""
Figma MCP 客户端 — v4.0 (package 模式)
======================================
通过 MCP 协议与 figma-developer-mcp 通信。

★ v4 package 拆分后架构：
  figma_mcp/__init__.py     — facade，重导出所有公开符号（本文件）
  figma_mcp/_mcp_stdio.py   — MCPStdioClient (JSON-RPC 2.0 stdio 通信)
  figma_mcp/_figma_client.py — FigmaMCPClient (业务接口)
  figma_mcp/_session.py      — 全局会话管理 (start/stop/get)
  figma_cache.py             — FigmaCache + 缓存摘要 + headless 元数据检查
  figma_quota.py             — FigmaQuotaTracker + API 配额追踪

所有原有导入路径保持不变（通过本文件重导出保证向后兼容）。
"""

import json
import os
import re
import subprocess
import sys
import threading
import time
import queue
from typing import Any
import logging

_log = logging.getLogger(__name__)

# ★ v2: 从拆分后的子模块导入（保留向后兼容）
from src.pipeline.steps.step1.figma_cache import FigmaCache, get_figma_cache_info
from src.pipeline.steps.step1.figma_cache import (
    check_figma_modified_time,
    _meta_last_result,
    _meta_last_check,
    _META_CACHE_TTL,
    _parse_iso,
)
from src.pipeline.steps.step1.figma_quota import FigmaQuotaTracker, quota_tracker

# ★ v4: 从 package 子模块导入
from .mcp_stdio import MCPStdioClient
from .figma_client import FigmaMCPClient
from .session import start_mcp_server, stop_mcp_server, get_mcp_session

__all__ = [
    # MCP 通信层
    "MCPStdioClient",
    # Figma 业务客户端
    "FigmaMCPClient",
    # 会话管理
    "start_mcp_server",
    "stop_mcp_server",
    "get_mcp_session",
    # 缓存（重导出自 figma_cache）
    "FigmaCache",
    "get_figma_cache_info",
    "check_figma_modified_time",
    "_meta_last_result",
    "_meta_last_check",
    "_META_CACHE_TTL",
    "_parse_iso",
    # 配额追踪（重导出自 figma_quota）
    "FigmaQuotaTracker",
    "quota_tracker",
]
