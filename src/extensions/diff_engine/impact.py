# -*- coding: utf-8 -*-
"""ImpactAnalyzer — 基于 TraceLink 交叉分析变更影响。从 diff_engine.py 拆出，零逻辑变更。"""

import logging

from .models import DiffReport, ImpactResult

_log = logging.getLogger(__name__)


class ImpactAnalyzer:
    """变更影响分析器：基于 TraceLink 交叉分析变更影响。"""

    def analyze(
        self,
        diff_report: DiffReport,
        trace_links: dict = None,
        test_cases: list[dict] = None,
    ) -> ImpactResult:
        """
        分析变更影响的需求和测试用例。

        参数:
            diff_report: DiffEngine 产生的变更报告
            trace_links: {req_id: TraceLink} 追溯链路
            test_cases:  现有测试用例列表

        返回:
            ImpactResult: 受影响的需求/测试用例 + 建议操作
        """
        result = ImpactResult()

        if not diff_report.has_changes:
            _log.info("[ImpactAnalyzer] 无变更，无影响")
            return result

        affected_reqs = set()
        affected_intents = set()

        for entry in diff_report.entries:
            if entry.entity_type == "requirement":
                # 需求本身变更
                if entry.change_type in ("modified", "removed"):
                    affected_reqs.add(entry.entity_id)
                    # 通过 case_id 命名约定找出受影响的测试用例
                    if test_cases:
                        req_num = entry.entity_id.replace("REQ-", "")
                        for intent in test_cases:
                            cid = intent.get("case_id", "")
                            if f"TC-{req_num}-" in cid:
                                affected_intents.add(cid)

            elif entry.entity_type == "api_endpoint":
                # API 变更 → 通过 TraceLink 反向查找受影响的需求
                if trace_links:
                    method_path = entry.entity_id.split(":", 1)
                    if len(method_path) == 2:
                        method, path = method_path
                    else:
                        method, path = "", entry.entity_id

                    for rid, link in trace_links.items():
                        # ★ P1: 优先用 isinstance 判断类型，避免 hasattr 对 dict 的误判
                        if isinstance(link, dict):
                            # dict 类型：用 .get() 安全访问
                            endpoints = link.get("api_endpoints", [])
                        elif hasattr(link, "api_endpoints"):
                            endpoints = link.api_endpoints
                        else:
                            continue
                        for ep in endpoints:
                            ep_key = f"{ep.get('method','')}:{ep.get('path','')}"
                            if ep_key == entry.entity_id:
                                affected_reqs.add(rid)
                                break

            elif entry.entity_type in ("db_table", "db_column"):
                # DB 变更 → 通过 TraceLink 反向查找受影响的需求
                if trace_links:
                    table_name = (
                        entry.entity_id.split(".")[0]
                        if "." in entry.entity_id
                        else entry.entity_id
                    )
                    for rid, link in trace_links.items():
                        # ★ P1: 优先用 isinstance 判断类型，避免 hasattr 对 dict 的误判
                        if isinstance(link, dict):
                            tables_attr = link.get("db_tables", {})
                        elif hasattr(link, "db_tables"):
                            tables_attr = link.db_tables
                        else:
                            continue
                        if table_name.upper() in (t.upper() for t in tables_attr):
                            affected_reqs.add(rid)

        # 为受影响的需求找出对应的测试用例
        if affected_reqs and test_cases:
            for rid in affected_reqs:
                req_num = rid.replace("REQ-", "")
                for intent in test_cases:
                    cid = intent.get("case_id", "")
                    if f"TC-{req_num}-" in cid and cid not in affected_intents:
                        affected_intents.add(cid)

        # 建议操作
        suggestions = []
        if affected_reqs:
            suggestions.append(f"重新审查 {len(affected_reqs)} 条受影响的需求")
        if affected_intents:
            suggestions.append(f"重新生成 {len(affected_intents)} 条受影响的测试用例")
            suggestions.append("建议运行选择性重新生成（而非全量）以节省时间")
        if not affected_reqs and not affected_intents:
            suggestions.append("无直接影响，建议按常规流程处理")

        # 严重程度
        if len(affected_reqs) > 5 or len(affected_intents) > 20:
            severity = "high"
        elif len(affected_reqs) > 0 or len(affected_intents) > 0:
            severity = "medium"
        else:
            severity = "low"

        result.affected_requirements = sorted(affected_reqs)
        result.affected_test_cases = sorted(affected_intents)
        result.suggested_actions = suggestions
        result.severity = severity

        _log.info(
            "[ImpactAnalyzer] 影响分析: %d 需求, %d 测试用例, 严重度=%s"
            % (len(affected_reqs), len(affected_intents), severity)
        )
        return result

    @staticmethod
    def format_impact_report(impact: ImpactResult) -> str:
        """格式化影响分析报告。"""
        lines = [
            "",
            "## 变更影响分析",
            "",
            f"**严重程度**: {impact.severity.upper()}",
            f"**受影响需求**: {len(impact.affected_requirements)} 条",
            f"**受影响测试用例**: {len(impact.affected_test_cases)} 条",
            "",
            "### 建议操作",
        ]
        for action in impact.suggested_actions:
            lines.append(f"- {action}")

        if impact.affected_requirements:
            lines.append("")
            lines.append("### 受影响的需求")
            for rid in impact.affected_requirements[:20]:
                lines.append(f"- {rid}")
            if len(impact.affected_requirements) > 20:
                lines.append(f"- ... 共 {len(impact.affected_requirements)} 条")

        if impact.affected_test_cases:
            lines.append("")
            lines.append("### 受影响的测试用例")
            for cid in impact.affected_test_cases[:20]:
                lines.append(f"- {cid}")
            if len(impact.affected_test_cases) > 20:
                lines.append(f"- ... 共 {len(impact.affected_test_cases)} 条")

        lines.append("")
        return "\n".join(lines)
