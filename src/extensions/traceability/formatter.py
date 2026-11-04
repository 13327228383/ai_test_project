# -*- coding: utf-8 -*-
"""
追溯链路引擎 — 报告格式化
========================
将覆盖率报告和追溯链路格式化为 Markdown 文本（注入 Step5 报告）。
"""

from .models import CoverageReport


class TraceabilityFormatter:
    """报告格式化器。"""

    def __init__(self, links: dict = None):
        self._links = links or {}

    def format_report_section(self, coverage_report: CoverageReport) -> str:
        """将覆盖率报告格式化为 Markdown 章节（注入 Step5 报告）。"""
        s = coverage_report.summary

        lines = [
            "",
            "## 数据源追溯覆盖率",
            "",
            "| 维度 | 总数 | 已覆盖 | 覆盖率 |",
            "|------|------|--------|--------|",
            "| API 接口 | %d | %d | %.1f%% |"
            % (s["api"]["total"], s["api"]["covered"], s["api"]["rate"]),
            "| 数据库表 | %d | %d | %.1f%% |"
            % (s["db"]["total"], s["db"]["covered"], s["db"]["rate"]),
            "| 前端页面 | %d | %d | %.1f%% |"
            % (s["page"]["total"], s["page"]["covered"], s["page"]["rate"]),
            "| 追溯链路 | %d | %d | %.1f%% |"
            % (s["trace"]["total"], s["trace"]["complete"], s["trace"]["rate"]),
            "",
        ]

        # 未覆盖的 API
        uncovered_apis = [
            k for k, v in coverage_report.api_coverage.items() if not v["covered"]
        ]
        if uncovered_apis:
            lines.append("### 未覆盖的 API 接口")
            for key in uncovered_apis[:20]:
                lines.append("- `%s`" % key)
            if len(uncovered_apis) > 20:
                lines.append("- ... 共 %d 个" % len(uncovered_apis))
            lines.append("")

        # 未覆盖的 DB 表
        uncovered_tables = [
            k for k, v in coverage_report.db_coverage.items() if not v["covered"]
        ]
        if uncovered_tables:
            lines.append("### 未覆盖的数据库表")
            for t in uncovered_tables[:20]:
                lines.append("- `%s`" % t)
            if len(uncovered_tables) > 20:
                lines.append("- ... 共 %d 个" % len(uncovered_tables))
            lines.append("")

        # 链路断裂明细
        broken = {
            rid: v
            for rid, v in coverage_report.trace_completeness.items()
            if not v["complete"]
        }
        if broken:
            lines.append("### 追溯链路断裂明细")
            lines.append("| 需求编号 | 模块 | 缺失环节 |")
            lines.append("|----------|------|----------|")
            for rid, info in list(broken.items())[:30]:
                missing_str = ", ".join(info["missing_links"])
                lines.append(
                    "| %s | %s | %s |" % (rid, info.get("module", ""), missing_str)
                )
            lines.append("")

        return "\n".join(lines)

    def format_trace_matrix_md(self) -> str:
        """格式化所有追溯链路为 Markdown 矩阵。"""
        if not self._links:
            return "（无追溯链路数据）"

        lines = [
            "",
            "## 需求追溯矩阵",
            "",
            "| 需求编号 | 模块 | API数 | DB表数 | 页面数 | 置信度 |",
            "|----------|------|-------|--------|--------|--------|",
        ]
        for rid, link in sorted(self._links.items()):
            lines.append(
                "| %s | %s | %d | %d | %d | %s |"
                % (
                    rid,
                    link.module[:20] if link.module else "-",
                    len(link.api_endpoints),
                    len(link.db_tables),
                    len(link.figma_pages),
                    link.confidence,
                )
            )
        lines.append("")
        return "\n".join(lines)
