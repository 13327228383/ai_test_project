# -*- coding: utf-8 -*-
"""CapabilityCache 的建表/字段补齐/旧表注释修正 — 从 capability_cache.py 拆出，零逻辑变更。"""

import logging

_log = logging.getLogger(__name__)


class _CapabilityCacheSchemaMixin:
    """建表 + 字段补齐 + COMMENT 修正 + 老项目策略补齐。"""

    def _ensure_table(self):
        """占位函数（2026-06-22：capability_cache / capability_cache_events / capability_cache_policy 表已统一由 src/core/schema.sql 负责）。"""
        return

    def _ensure_columns(self, conn):
        """占位函数（2026-06-22：列已统一由 src/core/schema.sql 负责，开发阶段删表重建无需兼容旧表）。"""
        return

    def _ensure_existing_comments(self, conn):
        """占位函数（2026-06-22：COMMENT 已统一写入 schema.sql，无需运行时补齐）。"""
        return

    def _ensure_policy_for_existing_projects(self, conn):
        """占位函数（2026-06-22：开发阶段无需为既有项目补齐策略）。"""
        return
