# -*- coding: utf-8 -*-
"""智能分析 + 运行缓存 + 项目配置目录 — 从 paths.py 拆出，零业务逻辑变更。"""

from pathlib import Path


class _AnalysisCacheMixin:
    """智能分析（追溯/覆盖率/RAG/diff/导航/接口意图）+ 运行缓存（figma db）+ 项目配置。"""

    # ═══════════════════════════════════════════════════════════════
    # 智能分析/ — AI 增强分析产物
    # ═══════════════════════════════════════════════════════════════

    @property
    def analysis_dir(self) -> Path:
        return self._root / "智能分析"

    # -- 追溯链路 --
    @property
    def analysis_trace_dir(self) -> Path:
        return self.analysis_dir / "追溯链路"

    @property
    def analysis_trace_links_json(self) -> Path:
        return self.analysis_trace_dir / "trace_links.json"

    # -- 覆盖率 --
    @property
    def analysis_coverage_dir(self) -> Path:
        return self.analysis_dir / "覆盖率"

    # -- 知识库 (RAG) --
    @property
    def analysis_rag_dir(self) -> Path:
        return self.analysis_dir / "知识库"

    @property
    def analysis_rag_index_db(self) -> Path:
        """已废弃：RAG 已迁移到 MySQL rag_chunks 表，不再使用本地文件。"""
        return self.analysis_rag_dir / "rag_index.db"

    # -- 变更差异 --
    @property
    def analysis_diff_dir(self) -> Path:
        return self.analysis_dir / "变更差异"

    # -- 接口意图 --
    @property
    def analysis_intents_dir(self) -> Path:
        return self.analysis_dir / "接口意图"

    @property
    def analysis_test_intents_json(self) -> Path:
        return self.analysis_intents_dir / "test_intents.json"

    # ═══════════════════════════════════════════════════════════════
    # 运行缓存/ — 可随时清除
    # ═══════════════════════════════════════════════════════════════

    @property
    def cache_dir(self) -> Path:
        return self._root / "运行缓存"

    @property
    def cache_figma_dir(self) -> Path:
        return self.cache_dir / "figma"

    @property
    def cache_figma_db(self) -> Path:
        return self.cache_figma_dir / "figma_cache.db"

    @property
    def cache_figma_quota_json(self) -> Path:
        return self.cache_figma_dir / "figma_api_quota.json"

    # ═══════════════════════════════════════════════════════════════
    # 项目配置/ — 个性化
    # ═══════════════════════════════════════════════════════════════

    @property
    def project_config_dir(self) -> Path:
        return self._root / "项目配置"

    @property
    def config_bg_dir(self) -> Path:
        return self.project_config_dir / "BG"

    @property
    def config_testdata_dir(self) -> Path:
        return self.project_config_dir / "测试数据"

    @property
    def config_driver_dir(self) -> Path:
        return self.project_config_dir / "驱动"
