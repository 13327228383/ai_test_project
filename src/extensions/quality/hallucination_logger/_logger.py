# -*- coding: utf-8 -*-
from __future__ import annotations

"""
★ P2-2: 幻觉案例记录与分析器 (HallucinationLogger) — Facade
====================================================
执行失败的测试用例中，自动分类失败原因——"环境问题" vs "测试用例本身幻觉"。
幻觉案例沉淀到知识库，触发规则/Prompt 更新。

数据流：
  执行失败 → classify_failure() → hallucination? → save_sample()
                                → env? → 忽略
                                → unknown? → 人工标注
  积累 >=10 样本 → analyze_and_update_rules() → 自动更新 test_rules.json

本模块为 Facade，通过 Mixin 多继承组合各职责模块：
  - ``_classifier``: HallucinationClassifierMixin（失败原因分类）
  - ``_persistence``: HallucinationPersistenceMixin（样本持久化）
  - ``_analyzer``: HallucinationAnalyzerMixin（分析与规则更新）
  - ``_cluster``: HallucinationClusterMixin（聚类与反馈）
"""

import logging
from pathlib import Path
from typing import Any

_log = logging.getLogger(__name__)

# 从 Mixin 模块重新导出，保持外部导入路径不变
from ._analyzer import _summarize_reasons  # noqa: F401, E402
from ._cluster import _FAILURE_KEYWORDS  # noqa: F401, E402
from ._classifier import HallucinationClassifierMixin  # noqa: E402
from ._persistence import HallucinationPersistenceMixin  # noqa: E402
from ._analyzer import HallucinationAnalyzerMixin  # noqa: E402
from ._cluster import HallucinationClusterMixin  # noqa: E402


class HallucinationLogger(
    HallucinationClassifierMixin,
    HallucinationPersistenceMixin,
    HallucinationAnalyzerMixin,
    HallucinationClusterMixin,
):
    """幻觉案例记录与分析器（Facade）。

    通过 Mixin 多继承组合以下职责：
    - 失败原因分类（HallucinationClassifierMixin）
    - 样本持久化（HallucinationPersistenceMixin）
    - 分析与规则更新（HallucinationAnalyzerMixin）
    - 聚类与反馈（HallucinationClusterMixin）
    """

    def __init__(
        self,
        project_name: str = "sample_project",
        samples_dir: Path = None,
        ai_store: Any = None,
    ):
        self._project_name = project_name
        # 允许注入 samples_dir / ai_store（便于测试与替换 MySQL 实现）
        self._samples_dir_override: Path | None = samples_dir
        self._ai_store = ai_store

    @property
    def samples_dir(self) -> Path:
        if self._samples_dir_override is not None:
            return self._samples_dir_override
        from src.core.config_loader import config
        from src.core.paths import ProjectPaths

        return ProjectPaths(config.project_root).analysis_dir / "幻觉案例"

    @samples_dir.setter
    def samples_dir(self, value: Path) -> None:
        self._samples_dir_override = Path(value) if value is not None else None

    @property
    def samples_path(self) -> Path:
        return self.samples_dir / "hallucination_samples.jsonl"

    @property
    def ai_store(self):
        """懒加载 MySQL store（主事实源），支持在单测中注入可替换实现。"""
        if self._ai_store is None:
            from src.pipeline.utils.ai_generation_logs_store import (
                ai_generation_logs_store as _default,
            )

            self._ai_store = _default
        return self._ai_store

    @ai_store.setter
    def ai_store(self, value: Any) -> None:
        self._ai_store = value


# 单例
# ★ 方案C 改造：hallucination_logger 改为 PerProjectRegistry，按 project 隔离
from src.core.utils.per_project_registry import PerProjectRegistry

hallucination_logger = PerProjectRegistry(HallucinationLogger)
