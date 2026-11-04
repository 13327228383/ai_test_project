# -*- coding: utf-8 -*-
"""测试方法基类 — 所有12种方法的标准接口"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Optional
from enum import Enum


class MethodCategory(Enum):
    BLACK_BOX = "black_box"  # 黑盒：不依赖代码
    WHITE_BOX = "white_box"  # 白盒：依赖代码/页面结构


@dataclass
class TestCase:
    """统一测试用例格式"""
    case_id: str
    name: str
    method: str  # 生成此用例的方法名
    category: str  # black_box / white_box
    priority: str  # critical / high / medium / low
    module: str = ""
    precondition: str = ""
    test_data: dict = field(default_factory=dict)
    steps: list[dict] = field(default_factory=list)  # [{action, target, value, desc}]
    assertions: list[dict] = field(default_factory=list)  # [{type, target, desc}]
    coverage_dimension: str = ""  # 覆盖维度说明
    tags: list[str] = field(default_factory=list)


@dataclass
class MethodResult:
    """单个方法的分析+用例生成结果"""
    method_name: str
    method_category: str
    total_cases: int
    cases: list[TestCase]
    coverage_report: dict = field(default_factory=dict)  # 覆盖维度统计
    warnings: list[str] = field(default_factory=list)


class BaseMethod(ABC):
    """所有测试方法的抽象基类"""

    method_name: str = ""
    method_category: MethodCategory = MethodCategory.BLACK_BOX
    description: str = ""
    applicable_scenarios: list[str] = []  # 适用场景关键词

    def __init__(self):
        if not self.method_name:
            raise ValueError(f"{self.__class__.__name__} must define method_name")

    @abstractmethod
    def analyze(self, requirement: dict, context: Optional[dict] = None) -> dict:
        """分析需求/模型，提取测试条件

        Args:
            requirement: {title, description, fields, rules, states, actions, ...}
            context: 额外上下文 {page_structure, db_schema, api_spec, ...}

        Returns:
            分析结果 dict，格式由各方法自行定义
        """
        pass

    @abstractmethod
    def generate_cases(self, analysis: dict, filtered_taxonomy: dict) -> list[TestCase]:
        """根据分析结果 + 筛选后的分类学，生成测试用例

        Args:
            analysis: self.analyze() 的返回值
            filtered_taxonomy: TaxonomyFilter 三层筛选后的 {actions, assertions}

        Returns:
            list[TestCase]
        """
        pass

    def run(self, requirement: dict, filtered_taxonomy: dict, context: Optional[dict] = None) -> MethodResult:
        """标准流程：分析 → 生成用例 → 汇总"""
        analysis = self.analyze(requirement, context)
        cases = self.generate_cases(analysis, filtered_taxonomy)
        return MethodResult(
            method_name=self.method_name,
            method_category=self.method_category.value,
            total_cases=len(cases),
            cases=cases,
            coverage_report=self._build_coverage_report(analysis, cases),
        )

    def _build_coverage_report(self, analysis: dict, cases: list[TestCase]) -> dict:
        """构建覆盖维度报告"""
        return {
            "method": self.method_name,
            "dimensions": list(analysis.keys()) if analysis else [],
            "case_count": len(cases),
            "actions_used": list(set(
                s["action"] for c in cases for s in c.steps
            )),
            "assertions_used": list(set(
                a["type"] for c in cases for a in c.assertions
            )),
        }