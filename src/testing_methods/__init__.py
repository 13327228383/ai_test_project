# -*- coding: utf-8 -*-
"""测试方法引擎 — 12种经典软件测试设计方法 + 多层分类学筛选

黑盒方法 (8): 等价类划分 · 边界值分析 · 因果图 · 判定表 · 正交实验 · 状态迁移 · 场景法 · 错误推测
白盒方法 (4): 语句覆盖 · 分支覆盖 · 条件覆盖 · 路径覆盖

+ 多层筛选: TaxonomyFilter (L1页面结构 → L2需求分析 → L3方法适配)
+ 方法路由: MethodRouter (根据需求特征自动选择适用的方法组合)
"""

from .base import BaseMethod, TestCase, MethodResult
from .method_router import MethodRouter
from .taxonomy_filter import TaxonomyFilter, FilteredTaxonomy

# 黑盒方法
from .equivalence_partitioning import EquivalencePartitioning
from .boundary_value import BoundaryValueAnalysis
from .cause_effect_graph import CauseEffectGraph
from .decision_table import DecisionTable
from .orthogonal_array import OrthogonalArray
from .state_transition import StateTransition
from .scenario_method import ScenarioMethod
from .error_guessing import ErrorGuessing

# 白盒方法
from .statement_coverage import StatementCoverage
from .branch_coverage import BranchCoverage
from .condition_coverage import ConditionCoverage
from .path_coverage import PathCoverage

__all__ = [
    "BaseMethod", "TestCase", "MethodResult",
    "MethodRouter", "TaxonomyFilter", "FilteredTaxonomy",
    "EquivalencePartitioning", "BoundaryValueAnalysis",
    "CauseEffectGraph", "DecisionTable", "OrthogonalArray",
    "StateTransition", "ScenarioMethod", "ErrorGuessing",
    "StatementCoverage", "BranchCoverage", "ConditionCoverage", "PathCoverage",
]