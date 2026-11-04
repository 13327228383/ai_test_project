# -*- coding: utf-8 -*-
"""方法路由器 — 根据需求特征自动选择适用的测试方法组合

路由规则：
1. 需求类型识别 → 黑盒/白盒方法分类
2. 复杂度评分 → 是否需要多层方法组合
3. 输出：按优先级排序的方法列表 + 每方法的适用理由
"""

import re
import logging
from typing import Optional
from dataclasses import dataclass, field

_log = logging.getLogger(__name__)


@dataclass
class MethodRecommendation:
    method_name: str
    category: str  # black_box / white_box
    priority: int  # 1=首选, 2=次选, 3=补充
    reason: str
    expected_case_count: int = 0


@dataclass
class RouteResult:
    requirement_type: str  # crud / workflow / form / search / permission / complex
    complexity: str  # low / medium / high
    recommendations: list[MethodRecommendation]
    total_estimated_cases: int = 0


class MethodRouter:
    """根据需求文本特征，路由到合适的测试方法"""

    # 需求类型 → 推荐方法（优先级从高到低）
    ROUTE_TABLE = {
        "form": [
            ("equivalence_partitioning", 1, "表单输入域需要等价类划分"),
            ("boundary_value", 1, "输入边界是表单最常见的缺陷来源"),
            ("cause_effect_graph", 2, "多字段联动需要因果图分析"),
            ("error_guessing", 3, "补充异常输入场景"),
        ],
        "crud": [
            ("scenario_method", 1, "CRUD 操作适合端到端场景法"),
            ("equivalence_partitioning", 2, "输入字段用等价类覆盖"),
            ("state_transition", 2, "数据状态变更需要状态迁移覆盖"),
            ("error_guessing", 3, "补充边界和异常"),
        ],
        "search": [
            ("equivalence_partitioning", 1, "搜索条件等价类划分"),
            ("boundary_value", 2, "搜索条件边界值"),
            ("orthogonal_array", 2, "多条件组合搜索正交实验"),
            ("decision_table", 3, "复杂筛选条件判定表"),
        ],
        "workflow": [
            ("state_transition", 1, "工作流核心是状态迁移"),
            ("scenario_method", 1, "端到端业务流程场景"),
            ("path_coverage", 2, "关键路径覆盖"),
            ("branch_coverage", 2, "分支路径覆盖"),
        ],
        "permission": [
            ("decision_table", 1, "权限矩阵适合判定表"),
            ("scenario_method", 2, "不同角色场景验证"),
            ("error_guessing", 3, "越权测试"),
        ],
        "dashboard": [
            ("scenario_method", 1, "看板展示场景"),
            ("equivalence_partitioning", 2, "数据筛选等价类"),
            ("statement_coverage", 2, "确保所有模块可见"),
        ],
        "complex": [
            ("scenario_method", 1, "复杂业务端到端"),
            ("state_transition", 1, "状态机覆盖"),
            ("cause_effect_graph", 2, "因果分析"),
            ("decision_table", 2, "条件组合判定"),
            ("orthogonal_array", 2, "参数组合正交"),
            ("path_coverage", 2, "关键路径覆盖"),
            ("condition_coverage", 3, "条件覆盖"),
        ],
    }

    # 需求特征关键词
    FEATURE_PATTERNS = {
        "form": re.compile(r"表单|输入|填写|提交|字段|文本框|下拉|选择|勾选|日期|上传|附件"),
        "crud": re.compile(r"新增|创建|删除|修改|编辑|查看|详情|列表|添加|移除"),
        "search": re.compile(r"搜索|查询|筛选|过滤|检索|查找"),
        "workflow": re.compile(r"审批|审核|流程|流转|状态|驳回|通过|提交|发布|上线"),
        "permission": re.compile(r"权限|角色|访问控制|授权|越权|管理员|普通用户"),
        "dashboard": re.compile(r"看板|图表|统计|报表|仪表盘|数据展示|可视化"),
    }

    def __init__(self):
        pass

    def route(self, requirement: Optional[dict] = None,
              requirement_text: str = "",
              page_structure: Optional[dict] = None,
              code_available: bool = False) -> RouteResult:
        """根据需求特征路由到合适的测试方法组合

        Args:
            requirement: 需求结构化数据
            requirement_text: 需求文本（用于特征提取）
            page_structure: 页面结构（用于复杂度评估）
            code_available: 是否有源代码可用（影响白盒方法可用性）
        """
        if requirement:
            text = " ".join([
                requirement.get("title", ""),
                requirement.get("description", ""),
                " ".join(requirement.get("acceptance_criteria", [])),
            ])
        else:
            text = requirement_text

        # 1. 识别需求类型
        req_type = self._classify_requirement(text)

        # 2. 评估复杂度
        complexity = self._assess_complexity(text, page_structure)

        # 3. 获取推荐方法
        base_methods = self.ROUTE_TABLE.get(req_type, self.ROUTE_TABLE["complex"])
        recommendations = []
        for method_name, priority, reason in base_methods:
            # 白盒方法需要代码可用
            if method_name in ("statement_coverage", "branch_coverage", "condition_coverage", "path_coverage"):
                if not code_available:
                    if complexity == "high":
                        # 高复杂度时即使没有源码也推荐白盒方法（用页面结构模拟）
                        reason += "（基于页面结构模拟）"
                    else:
                        continue  # 低复杂度 + 无源码 → 跳过白盒方法

            est = self._estimate_case_count(method_name, complexity)
            recommendations.append(MethodRecommendation(
                method_name=method_name,
                category="white_box" if method_name in (
                    "statement_coverage", "branch_coverage", "condition_coverage", "path_coverage"
                ) else "black_box",
                priority=priority,
                reason=reason,
                expected_case_count=est,
            ))

        total = sum(r.expected_case_count for r in recommendations)

        _log.info("MethodRouter: type=%s complexity=%s → %d methods, ~%d cases",
                  req_type, complexity, len(recommendations), total)

        return RouteResult(
            requirement_type=req_type,
            complexity=complexity,
            recommendations=recommendations,
            total_estimated_cases=total,
        )

    def _classify_requirement(self, text: str) -> str:
        """识别需求类型"""
        scores = {}
        for rtype, pattern in self.FEATURE_PATTERNS.items():
            matches = pattern.findall(text)
            scores[rtype] = len(matches)

        if not scores or max(scores.values()) == 0:
            return "crud"  # 默认 CRUD

        # 返回最高分类型
        best = max(scores, key=scores.get)
        if scores[best] >= 3:
            return best

        # 多个类型都有匹配 → complex
        matched = [k for k, v in scores.items() if v > 0]
        if len(matched) >= 3:
            return "complex"

        return best

    def _assess_complexity(self, text: str, page_structure: Optional[dict]) -> str:
        """评估需求复杂度"""
        score = 1  # 基础分

        # 文本长度
        if len(text) > 500:
            score += 1
        if len(text) > 2000:
            score += 1

        # 关键词
        complex_keywords = ["审批", "流程", "权限", "状态", "多角色", "并发", "事务", "回滚", "异步"]
        score += sum(1 for kw in complex_keywords if kw in text)

        # 页面复杂度
        if page_structure:
            el_count = len(page_structure.get("elements", []))
            if el_count > 30:
                score += 1
            if el_count > 80:
                score += 1
            if page_structure.get("has_iframe"):
                score += 1
            form_count = sum(1 for e in page_structure.get("elements", [])
                             if e.get("tag") in ("input", "select", "textarea"))
            if form_count > 10:
                score += 1

        if score <= 2:
            return "low"
        elif score <= 5:
            return "medium"
        else:
            return "high"

    def _estimate_case_count(self, method_name: str, complexity: str) -> int:
        """估算方法产生的用例数"""
        base = {
            "equivalence_partitioning": 8,
            "boundary_value": 12,
            "cause_effect_graph": 10,
            "decision_table": 16,
            "orthogonal_array": 9,
            "state_transition": 6,
            "scenario_method": 5,
            "error_guessing": 8,
            "statement_coverage": 5,
            "branch_coverage": 8,
            "condition_coverage": 10,
            "path_coverage": 12,
        }
        multiplier = {"low": 0.5, "medium": 1.0, "high": 1.5}
        return int(base.get(method_name, 8) * multiplier.get(complexity, 1.0))