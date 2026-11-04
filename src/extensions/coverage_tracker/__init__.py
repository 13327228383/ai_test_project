# -*- coding: utf-8 -*-
"""
需求→测试用例覆盖追踪器 (CoverageTracker) — v6（包入口）
==========================================================
在 Step3 测试用例生成完成后，对需求条目 × 测试维度构建覆盖矩阵，
检测缺口并自动补生成缺失测试用例。

核心能力：
1. 覆盖矩阵计算：每个 REQ 在 9 个维度上的覆盖状态（5 测试 + 4 数据源）
2. 缺口检测：自动发现覆盖不足的需求
3. 自动补全：缺口自动调用 AI 补生成测试用例
4. 输出：coverage_matrix.xlsx + Markdown 报告章节

维度定义（测试方法论 5 维）:
  POSITIVE   — 正向/场景法覆盖（至少 1 条）
  NEGATIVE   — 异常/错误推测覆盖（涉及输入的需求至少 1 条）
  BOUNDARY   — 边界值覆盖（涉及数值/长度/日期的需求至少 1 条）
  SECURITY   — 安全覆盖（涉及用户输入的需求至少 1 条）
  API_CHECK  — API 轨校验覆盖（至少 1 个意图动作含 api_checks）

维度定义（数据源追溯 4 维，v6 新增）:
  API_COVERAGE   — 关联的 API endpoint 是否被至少 1 个测试用例覆盖
  DB_COVERAGE    — 关联的 DB 表是否被至少 1 个测试用例的 db_checks 覆盖
  PAGE_COVERAGE  — 关联的 Figma 页面是否被至少 1 个测试用例的动作覆盖
  TRACE_COMPLETE — 追溯链路是否完整（REQ→API→DB→Page 不断链）

【2026-06-18 重构】原 505 行单文件已按职责拆为：
  keywords.py    — TECHNIQUE_TO_DIM / SECURITY_KEYWORDS / BOUNDARY_KEYWORDS 常量
  evaluator.py   — _CoverageEvaluatorMixin（compute_coverage 等核心评估方法）
  gap_filler.py  — _GapFillerMixin（auto_fill_gaps + format_report_section）
  persistence.py — _PersistenceMixin（save_matrix + Excel 导入/导出 + load_from_db）
"""

import logging
from pathlib import Path
from typing import Any

from .keywords import TECHNIQUE_TO_DIM, SECURITY_KEYWORDS, BOUNDARY_KEYWORDS
from .evaluator import _CoverageEvaluatorMixin
from .gap_filler import _GapFillerMixin
from .persistence import _PersistenceMixin

_log = logging.getLogger(__name__)


class CoverageTracker(_CoverageEvaluatorMixin, _GapFillerMixin, _PersistenceMixin):
    """需求覆盖追踪器。"""

    # 类级常量（保留在主类，子 mixin 通过 self.SECURITY_KEYWORDS 访问）
    TECHNIQUE_TO_DIM = TECHNIQUE_TO_DIM
    SECURITY_KEYWORDS = SECURITY_KEYWORDS
    BOUNDARY_KEYWORDS = BOUNDARY_KEYWORDS

    def __init__(self, project_root: Path = None):
        self._root = project_root or Path(".")
        self._flaky_intents: set = set()  # ★ P1-4: Flaky 测试用例集合
        self._trace_links: dict[str, Any] = (
            {}
        )  # v6: 数据源追溯维度（实例级，避免多实例泄漏）

    def record_flaky(self, case_id: str):
        """★ P1-4: 记录 Flaky 测试用例。"""
        self._flaky_intents.add(case_id)

    def get_flaky_rate(self, total: int) -> float:
        """★ P1-4: 获取 Flaky 率。"""
        if total <= 0:
            return 0.0
        return len(self._flaky_intents) / total * 100


# ★ 方案C 改造：coverage_tracker 改为 PerProjectRegistry，按 project 隔离
#   原实现：模块级单例，_flaky_intents/_trace_links 跨项目混在一起
#   工厂函数在创建实例时读取当前 project 的 config.project_root
from src.core.utils.per_project_registry import PerProjectRegistry


def _make_coverage_tracker():
    """工厂函数：创建 CoverageTracker 实例，读取当前 project 的 project_root。"""
    try:
        from src.core.config_loader import config

        return CoverageTracker(config.project_root)
    except Exception as _e:
        logging.getLogger("coverage_tracker").warning(
            "无法读取项目配置，回退到当前目录: %s", str(_e)[:120]
        )
        return CoverageTracker()


coverage_tracker = PerProjectRegistry(_make_coverage_tracker)
