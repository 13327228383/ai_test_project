# -*- coding: utf-8 -*-
"""原始数据目录（Confluence/Figma/YApi/DB/目标系统）— 从 paths.py 拆出，零业务逻辑变更。"""

from pathlib import Path


class _RawDirsMixin:
    """raw_dir 及其子树：需求文档 / 设计稿 / 接口文档 / 数据库 / 目标系统。"""

    # ═══════════════════════════════════════════════════════════════
    # 原始数据/ — Step 1 拉取的外部数据源（只读）
    # ═══════════════════════════════════════════════════════════════

    @property
    def raw_dir(self) -> Path:
        return self._root / "原始数据"

    # -- 需求文档 (Confluence) --
    @property
    def raw_requirements_dir(self) -> Path:
        return self.raw_dir / "需求文档"

    @property
    def raw_confluence_pages_md(self) -> Path:
        return self.raw_requirements_dir / "confluence_pages.md"

    @property
    def raw_confluence_requirements_md(self) -> Path:
        """Confluence 需求稿（AI 处理后）"""
        return self.raw_requirements_dir / "需求稿_Confluence.md"

    @property
    def raw_confluence_hash_json(self) -> Path:
        return self.raw_requirements_dir / "confluence_hash.json"

    @property
    def raw_confluence_old_md(self) -> Path:
        """（向后兼容）全局旧需求文档，用于 diff 对比。"""
        return self.raw_requirements_dir / "confluence_old.md"

    def raw_confluence_old_by_version(self, version_label: str = "") -> Path:
        """★ 版本感知的旧需求文档。"""
        if not version_label:
            from src.users.manager import get_active_version_label

            version_label = get_active_version_label(self._root.name)
        version_label = self._sanitize_version_suffix(version_label)
        suffix = ("_" + version_label) if version_label else ""
        return self.raw_requirements_dir / f"confluence_old{suffix}.md"

    # -- 设计稿 (Figma) --
    @property
    def raw_design_dir(self) -> Path:
        return self.raw_dir / "设计稿"

    @property
    def raw_figma_node_hashes_json(self) -> Path:
        """（向后兼容）全局 Figma 节点哈希文件，无版本感知时使用。"""
        return self.raw_design_dir / "figma_node_hashes.json"

    def raw_figma_node_hashes_by_version(self, version_label: str = "") -> Path:
        """★ 版本感知的 Figma 节点哈希文件。
        不传 version_label 时自动读取当前活跃版本。
        无活跃版本时回退到全局路径 figma_node_hashes.json。
        """
        if not version_label:
            from src.users.manager import get_active_version_label

            version_label = get_active_version_label(self._root.name)
        version_label = self._sanitize_version_suffix(version_label)
        suffix = ("_" + version_label) if version_label else ""
        return self.raw_design_dir / f"figma_node_hashes{suffix}.json"

    # -- 需求文档哈希：版本感知 --
    def raw_confluence_hash_by_version(self, version_label: str = "") -> Path:
        """★ 版本感知的 Confluence 哈希文件。"""
        if not version_label:
            from src.users.manager import get_active_version_label

            version_label = get_active_version_label(self._root.name)
        version_label = self._sanitize_version_suffix(version_label)
        suffix = ("_" + version_label) if version_label else ""
        return self.raw_requirements_dir / f"confluence_hash{suffix}.json"

    # -- 接口文档 (YApi) --
    @property
    def raw_api_dir(self) -> Path:
        return self.raw_dir / "接口文档"

    @property
    def raw_yapi_docs_md(self) -> Path:
        return self.raw_api_dir / "yapi_docs.md"

    @property
    def raw_yapi_docs_json(self) -> Path:
        return self.raw_api_dir / "yapi_docs.json"

    @property
    def raw_yapi_hash_json(self) -> Path:
        """（向后兼容）全局 YApi 哈希文件。"""
        return self.raw_api_dir / "yapi_hash.json"

    def raw_yapi_hash_by_version(self, version_label: str = "") -> Path:
        """★ 版本感知的 YApi 哈希文件。"""
        if not version_label:
            from src.users.manager import get_active_version_label

            version_label = get_active_version_label(self._root.name)
        version_label = self._sanitize_version_suffix(version_label)
        suffix = ("_" + version_label) if version_label else ""
        return self.raw_api_dir / f"yapi_hash{suffix}.json"

    # -- 数据库 --
    @property
    def raw_database_dir(self) -> Path:
        return self.raw_dir / "数据库"

    @property
    def raw_db_catalog_json(self) -> Path:
        return self.raw_database_dir / "system_data_catalog.json"

    # -- 目标系统 --
    @property
    def raw_target_dir(self) -> Path:
        return self.raw_dir / "目标系统"
