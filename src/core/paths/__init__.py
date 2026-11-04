# -*- coding: utf-8 -*-
"""
统一路径管理模块 (ProjectPaths)
===============================
集中管理项目下所有文件/目录路径，避免各模块硬编码路径。

目录结构（v3 重构）：
  data/{project}/
    ├── 原始数据/                 ← Step 1 拉取的外部数据源（只读）
    │   ├── 需求文档/             ← Confluence 页面
    │   ├── 设计稿/               ← Figma 节点哈希
    │   ├── 接口文档/             ← YApi docs
    │   └── 数据库/               ← DB catalog
    ├── 过程产物/                 ← Step 2-3 AI 生成的过程文件
    │   ├── 需求拆解/             ← requirements_breakdown.xlsx
    │   ├── 测试用例/             ← Validation_Intent*.xlsx / Core_Intent*.xlsx
    │   └── 测试设计/             ← Test_Design*.xmind
    ├── 测试结果/                 ← Step 4-5 交付物
    │   ├── 测试报告/             ← Test_Report*.md
    │   ├── 执行截图/             ← screenshots
    │   └── 执行日志/             ← run logs
    ├── 智能分析/                 ← AI 增强分析产物
    │   ├── 追溯链路/             ← trace_links.json
    │   ├── 覆盖率/               ← coverage_report 已迁移至 MySQL generated_reports
    │   ├── 知识库/               ← ★ 已废弃（RAG 已迁移至 MySQL rag_chunks 表）
    │   ├── 变更差异/             ← diff snapshots
    │   ├── 系统导航/             ← system_nav_map.json
    │   └── 接口意图/             ← test_intents.json
    ├── 运行缓存/                 ← 可随时清除的缓存（已走 MySQL）
    └── 项目配置/                 ← 个性化配置
        ├── BG/                   ← 背景图
        └── 测试数据/             ← 业务测试数据

【2026-06-18 重构】原 405 行单文件按目录树拆分：
- raw_dirs.py       — 原始数据/（Confluence/Figma/YApi/DB/目标系统）
- process_result.py — 过程产物/ + 测试结果/
- analysis_cache.py — 智能分析/ + 运行缓存/ + 项目配置/
- legacy_aliases.py — ensure_dirs / find_latest + 向后兼容别名
- __init__.py       — facade ProjectPaths + 构造器/根属性/版本标签清理
"""

from pathlib import Path

from .raw_dirs import _RawDirsMixin
from .process_result import _ProcessResultMixin
from .analysis_cache import _AnalysisCacheMixin
from .legacy_aliases import _LegacyAliasMixin


class ProjectPaths(
    _RawDirsMixin, _ProcessResultMixin, _AnalysisCacheMixin, _LegacyAliasMixin
):
    """项目路径管理器。每次通过 project_root 构建路径。"""

    def __init__(self, project_root: Path):
        self._root = Path(project_root)

    @staticmethod
    def _sanitize_version_suffix(label: str) -> str:
        """统一清理版本标签中的非法文件名字符（/ \\ 空格 → -）。"""
        return (
            label.replace("/", "-").replace("\\", "-").replace(" ", "") if label else ""
        )

    @property
    def project_root(self) -> Path:
        return self._root
