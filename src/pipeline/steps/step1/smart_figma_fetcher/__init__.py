# -*- coding: utf-8 -*-
"""
智能分层 Figma 拉取器 (SmartFigmaFetcher) — v3.0（包入口 facade）
==========================================================
四层智能拉取，解决全量拉取 429 限流问题，并实现节点级变更检测。

分层架构：
  Layer 1: GET /v1/files/:key?depth=1 → 轻量级目录（仅顶层页面/FRAME）
  Layer 2: 全量保留（RAG 按需检索，不再做 AI 筛选）
  Layer 3: GET /v1/files/:key/nodes?ids=... → 批量拉取 + 节点级哈希变更检测
  Layer 4: 变更感知输出 → ⚠️ 标记变更页面，✨ 标记新增页面

入口：仅接受 Figma 文件 URL，自动提取 file_key。

使用方式：
    from src.pipeline.steps.step1.smart_figma_fetcher import smart_figma_fetcher
    result = smart_figma_fetcher.fetch_smart("https://www.figma.com/design/xxx/...")

【2026-06-18 重构】facade + mixins 模式：
  version_aware.py — _VersionAwareMixin（set_version + inherit_hashes_from）
  incremental.py   — _IncrementalMixin（4 个增量/全量/缓存模式方法）
  persistence.py   — _PersistenceMixin（节点哈希 + 变更日志的 5 个 IO 方法）
  _layer1.py       — _Layer1Mixin（Layer 1 目录拉取）
  _layer2.py       — _Layer2Mixin（全量保留）
  _layer3.py       — _Layer3Mixin（Layer 3 批量节点拉取 + 变更检测）
  _entry.py        — _EntryMixin（fetch_smart 主入口）
  __init__.py      — facade（类定义 + 单例）
"""

import logging
from typing import Any

from src.pipeline.steps.step1.figma_client import FigmaClient
from src.pipeline.steps.step1.figma_hasher import compute_node_hash
from src.core.log_utils import fmt_log

# ---------------------------------------------------------------------------
# 拆分出的子模块（零业务逻辑变更，仅物理搬迁）
# ---------------------------------------------------------------------------
from src.pipeline.steps.step1._smart_fetcher_utils import (
    extract_file_key,
    parse_json_response,
    detect_paid_token,
    extract_top_pages,
    extract_elements_flat,
)
from src.pipeline.steps.step1._smart_fetcher_nodes import (
    extract_nodes_from_full_doc,
    extract_nodes_from_document,
    batch_fetch_nodes_cached,
)
from src.pipeline.steps.step1._smart_fetcher_format import format_smart_output
from src.pipeline.steps.step1._smart_fetcher_quota import check_quota, get_quota_info

# ---------------------------------------------------------------------------
# 本包内拆分的 mixin
# ---------------------------------------------------------------------------
from .version_aware import _VersionAwareMixin
from .incremental import _IncrementalMixin
from .persistence import _PersistenceMixin
from ._layer1 import _Layer1Mixin
from ._layer2 import _Layer2Mixin
from ._layer3 import _Layer3Mixin
from ._entry import _EntryMixin

_log = logging.getLogger(__name__)


# =============================================================================
# SmartFigmaFetcher — facade
# =============================================================================


class SmartFigmaFetcher(
    _VersionAwareMixin,
    _IncrementalMixin,
    _PersistenceMixin,
    _Layer1Mixin,
    _Layer2Mixin,
    _Layer3Mixin,
    _EntryMixin,
):
    """智能分层 Figma 拉取器。"""

    @property
    def _CACHE_DIR(self):
        from src.core.config_loader import config
        from src.core.paths import ProjectPaths

        return ProjectPaths(config.project_root).raw_design_dir

    @property
    def _NODE_HASH_PATH(self):
        """★ 版本感知：根据 _version_label 返回对应版本的节点哈希文件。"""
        from src.core.config_loader import config
        from src.core.paths import ProjectPaths

        return ProjectPaths(config.project_root).raw_figma_node_hashes_by_version(
            self._version_label
        )

    @property
    def _CHANGE_LOG_PATH(self):
        """★ 版本感知：根据 _version_label 返回对应版本的变更日志文件。"""
        from src.core.config_loader import config
        from src.core.paths import ProjectPaths

        return ProjectPaths(config.project_root).process_figma_change_log_by_version(
            self._version_label
        )

    _BATCH_SIZE = 15  # 分层回退时每批拉取节点数
    _BATCH_DELAY_MIN = 0.5  # 批次间最小延迟（秒）
    _BATCH_DELAY_MAX = 1.0  # 批次间最大延迟（秒）

    # 免费账号配额估算：Tier 1 端点约 6 次/月
    # 付费账号（figd_ Token）：500 次/分钟，无月度限制
    _FREE_TIER1_MONTHLY_QUOTA = 6
    _PAID_RATE_LIMIT_PER_MINUTE = 500  # Figma Professional/Org plan

    def __init__(self) -> None:
        self._client = FigmaClient()
        self._version_label = ""  # ★ 版本标签，空字符串 = 全局模式（向后兼容）
        self._node_hashes: dict[str, str] = self._load_node_hashes()
        self._change_log: dict[str, Any] = self._load_change_log()
        self._api_call_count = 0  # 本次会话 API 调用计数
        self._is_paid = detect_paid_token()


# 单例
# ★ 方案C 改造：smart_figma_fetcher 改为 PerProjectRegistry，按 project 隔离
from src.core.utils.per_project_registry import PerProjectRegistry

smart_figma_fetcher = PerProjectRegistry(SmartFigmaFetcher)
