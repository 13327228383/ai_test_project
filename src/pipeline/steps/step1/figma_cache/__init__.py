# -*- coding: utf-8 -*-
"""
Figma 缓存管理 + 元数据检查 — v4.0
===================================
MySQL 持久化缓存层 + headless 浏览器元数据检查（不消耗 REST API 配额）。
从 figma_mcp.py 拆分出，保持独立模块。

使用方式：
    from src.pipeline.steps.step1.figma_cache import FigmaCache, get_figma_cache_info

模块级状态（供外部引用）：
    _meta_last_result  — 最近一次元数据检查结果
    _meta_last_check   — 最近一次检查时间戳
    _META_CACHE_TTL    — 元数据缓存有效期（秒）
"""

from src.pipeline.steps.step1.figma_cache.base import _FigmaCacheBaseMixin
from src.pipeline.steps.step1.figma_cache.read import _FigmaCacheReadMixin
from src.pipeline.steps.step1.figma_cache.write import _FigmaCacheWriteMixin
from src.pipeline.steps.step1.figma_cache.invalidate import _FigmaCacheInvalidateMixin
from src.pipeline.steps.step1.figma_cache.health import _FigmaCacheHealthMixin

# =============================================================================
# FigmaCache — MySQL 持久化缓存（Facade：多继承 Mixin 模式）
# =============================================================================


class FigmaCache(
    _FigmaCacheBaseMixin,
    _FigmaCacheReadMixin,
    _FigmaCacheWriteMixin,
    _FigmaCacheInvalidateMixin,
    _FigmaCacheHealthMixin,
):
    """
    Figma MCP 响应的 MySQL 持久化缓存。
    表 figma_mcp_cache 由 schema.sql 统一创建。
    """

    pass


# ★ 方案C 改造：figma_cache 改为 PerProjectRegistry，按 project 隔离
from src.core.utils.per_project_registry import PerProjectRegistry

figma_cache = PerProjectRegistry(FigmaCache)

# =============================================================================
# 重导出子模块的公共接口（保持外部导入路径不变）
# =============================================================================

from ._cache_info import get_figma_cache_info
from ._meta_check import (
    check_figma_modified_time,
    fetch_figma_nodes,
    _meta_last_result,
    _meta_last_check,
    _META_CACHE_TTL,
    _parse_iso,
)
