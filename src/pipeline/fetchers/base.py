# -*- coding: utf-8 -*-
"""
Step1DataFetcher 基础 mixin

职责：
- 常量定义
- _setup_version — 版本管理（获取/创建活跃版本）
- _check_health — 健康检查
- _validate_config — 配置校验（所有数据源凭据检查）
- _build_sources_status — 初始化 sources_status
"""

import logging
from src.core.guarded import guard
from typing import Any

from src.core.config_loader import config

_log = logging.getLogger(__name__)

# 常量
_CACHE_TTL_SECONDS = 7776000  # 90天，API 拉取慢，长缓存减少不必要调用
_TRUNC_DETAIL = 80
_TRUNC_STATUS = 100
_TRUNC_SUMMARY = 120
_TRUNC_HASH_COMBINED = 128
_TRUNC_REPORT = 60


class _Step1BaseMixin:
    """Step1 基础能力。"""

    def _setup_version(self) -> tuple:
        """获取/创建活跃版本，返回 (active_ver, prev_active_version_label, version_label)。"""
        from src.users.manager import (
            get_active_version,
            create_project_version,
            activate_project_version,
            get_project_versions,
        )
        from datetime import datetime as _dt

        active_ver = get_active_version(config.project_name)
        prev_active_version_label = ""

        if active_ver:
            with guard("_setup_version: _versions = get_project_versions(con", log=_log, level=logging.WARNING):
                _versions = get_project_versions(config.project_name)
                _active_id = int(active_ver.get("id") or 0)
                _prev_candidates = [
                    v
                    for v in _versions
                    if int(v.get("id") or 0) != _active_id
                    and str(v.get("version_label", "") or "")
                ]
                _prev_candidates = sorted(
                    _prev_candidates,
                    key=lambda v: str(v.get("created_at") or ""),
                    reverse=True,
                )
                prev_active_version_label = (
                    str(_prev_candidates[0].get("version_label") or "")
                    if _prev_candidates
                    else ""
                )

        if not active_ver:
            now_str = _dt.now().strftime("%Y%m%d_%H%M%S")
            label = config.project_version or ("v" + now_str)
            vid = create_project_version(
                config.project_name, label, "自动创建", "system"
            )
            if vid:
                activate_project_version(config.project_name, vid)
                active_ver = get_active_version(config.project_name)
                _log.info("自动创建版本: %s (id=%s)", label, vid)
                _log.info("  ✓ 自动创建版本: %s (id=%s)", label, vid)
            else:
                _log.warning("版本自动创建失败，使用匿名模式")

        _version_label = active_ver["version_label"] if active_ver else ""
        return active_ver, prev_active_version_label, _version_label

    def _check_health(self) -> dict[str, Any]:
        """数据源健康检查。"""
        from src.pipeline.step1_health_checks import build_source_health_checks

        return build_source_health_checks(config, probe_database=True)

    def _validate_config(self) -> list[str]:
        """校验所有启用数据源的凭据是否齐全。"""
        missing = []

        # Confluence
        if config.confluence_page_ids:
            mode = config.confluence_mode
            if mode == "file":
                if not config.confluence_url or not str(config.confluence_url).strip():
                    missing.append(
                        "Confluence (File): 缺少 CONFLUENCE_URL（本地文件路径）"
                    )
            elif mode == "mcp":
                if not config.confluence_pat:
                    missing.append(
                        "Confluence (MCP): 缺少 CONFLUENCE_PAT / CONFLUENCE_PASSWORD"
                    )
            else:
                if not config.confluence_username:
                    missing.append("Confluence (REST): 缺少 CONFLUENCE_USERNAME")
                if not config.confluence_password:
                    missing.append("Confluence (REST): 缺少 CONFLUENCE_PASSWORD")

        # Figma
        if config.figma_url:
            fig_url = str(config.figma_url or "").strip()
            if fig_url.startswith("http") and not config.figma_key:
                missing.append("Figma: 缺少 FIGMA_KEY (Personal Access Token)")

        # YApi
        if config.api_docs_enabled:
            api_url = str(config.api_docs_base_url or "").strip()
            if api_url.startswith("http"):
                if not config.api_docs_username:
                    missing.append("YApi: 缺少 API_DOCS_USERNAME")
                if not config.api_docs_password:
                    missing.append("YApi: 缺少 API_DOCS_PASSWORD")

        # 数据库
        if config.db_enabled:
            db_type = str(config.db_type or "").lower()
            if db_type == "sqlite":
                if not config.db_name:
                    missing.append(
                        "数据库 (SQLite): 缺少 TARGET_DB_NAME（.db 文件路径）"
                    )
            else:
                if not config.db_host:
                    missing.append("数据库: 缺少 TARGET_DB_HOST")
                if not config.db_user:
                    missing.append("数据库: 缺少 DB_USER")
                if not config.db_password:
                    missing.append("数据库: 缺少 DB_PASSWORD")
                if not config.db_name:
                    missing.append("数据库: 缺少 TARGET_DB_NAME")

        return missing

    def _build_sources_status(self) -> dict[str, dict[str, str]]:
        """★ 通用性修复: 动态构建 sources_status。
        
        从 DataSourceRegistry 读取所有已注册数据源，而非硬编码 7 个 key。
        """
        try:
            from src.pipeline.datasource_registry import registry
            _keys = registry.source_keys()
        except Exception:
            from src.pipeline.datasource_registry import BUILTIN_SOURCE_KEYS
            _keys = list(BUILTIN_SOURCE_KEYS)
        
        result = {k: {"status": "skipped", "detail": ""} for k in _keys}
        # 额外非数据源 key（上传、DOM、多源提取）
        result["dom_crawl"] = {"status": "skipped", "detail": ""}
        result["upload"] = {"status": "skipped", "detail": ""}
        result["multi_source_features"] = {"status": "skipped", "detail": ""}
        return result
