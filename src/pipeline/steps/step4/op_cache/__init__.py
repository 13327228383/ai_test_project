# -*- coding: utf-8 -*-
"""
Step4 操作指令语义缓存 (OperationCache)
========================================
基于「页面结构指纹 + 步骤目标」的语义级缓存。
缓存时不存 ai_id，只保留 target_hint + fallback；
执行时实时解析 target_hint 到当前页面 ai_id。

★ v3: project_name → project（统一列名，对齐 schema.sql v3.0 step4_op_cache 表）

使用方式：
    from src.pipeline.steps.step4.op_cache import op_cache
    cached = op_cache.get(goal, page_url, compact_state, config_hash)
    op_cache.set(goal, page_url, compact_state, config_hash, actions)
"""
import logging
from src.core.guarded import guard
from typing import Any

_log = logging.getLogger(__name__)

# 从 _helpers 导入公开辅助函数
from ._helpers import (
    normalize_goal,
    compute_semantic_fingerprint,
    resolve_ai_ids,
    compute_config_hash,
    _strip_and_placeholder,
    _get_project_name,
    _active_version_id,
)

from src.pipeline.steps.step4.op_cache.base import _OpCacheBaseMixin
from src.pipeline.steps.step4.op_cache.cache_ops import _OpCacheOpsMixin
from src.pipeline.steps.step4.op_cache.eviction import _OpCacheEvictionMixin
from src.pipeline.steps.step4.op_cache.stats import _OpCacheStatsMixin


class OperationCache(
    _OpCacheBaseMixin, _OpCacheOpsMixin, _OpCacheEvictionMixin, _OpCacheStatsMixin
):
    """Step4 操作指令 MySQL 持久化缓存（表: step4_op_cache）。"""

    pass


# ★ 方案C 改造：op_cache 改为 PerProjectRegistry，按 project 隔离
from src.core.utils.per_project_registry import PerProjectRegistry

op_cache = PerProjectRegistry(OperationCache)


# ═══════════════════════════════════════════════
# ★ P2-3: Figma/API Schema变更联动缓存失效
# ═══════════════════════════════════════════════
