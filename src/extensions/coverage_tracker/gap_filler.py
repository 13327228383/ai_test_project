# -*- coding: utf-8 -*-
"""缺口自动补全 + Markdown 报告章节 — 从 coverage_tracker.py 拆出，零逻辑变更。"""

import logging
from src.core.guarded import guard

_log = logging.getLogger(__name__)


class _GapFillerMixin:
    """auto_fill_gaps + format_report_section。"""

    # ═══════════════════════════════════════════════
    # 缺口自动补全
    # ═══════════════════════════════════════════════

    def auto_fill_gaps(
        self,
        gaps: list[dict],
        requirements: list[dict],
        ui_context: str = "",
        project_context: str = "",
    ) -> list[dict]:
        """
        对覆盖缺口自动调用 AI 补生成缺失测试用例。

        返回: 补生成的测试用例列表
        """
        if not gaps:
            return []
        # 安全默认：避免单测/离线环境误触发真实 AI 调用。
        # 需要自动补全时显式设置 COVERAGE_AUTO_FILL_AI=1。
        import os

        if os.environ.get("COVERAGE_AUTO_FILL_AI", "").lower() not in (
            "1",
            "true",
            "yes",
            "on",
        ):
            _log.info(
                "  [Coverage] 自动补全未启用（设置 COVERAGE_AUTO_FILL_AI=1 后启用）"
            )
            return []

        # 构建 req_id → req 映射
        req_map = {r.get("req_id", ""): r for r in requirements}

        new_intents = []
        for gap in gaps:
            rid = gap["req_id"]
            req = req_map.get(rid)
            if not req:
                continue

            missing = gap["missing_dimensions"]
            _log.info("  [Coverage] %s 缺口: %s → 自动补全...", rid, ", ".join(missing))

            with guard("auto_fill_gaps: from src.ai.ai_client import ai_brai", log=_log, level=logging.INFO):
                from src.ai.ai_client import ai_brain

                result = ai_brain.model_test_cases(
                    requirement=req,
                    project_context=project_context,
                    ui_context=ui_context,
                )
                if result.get("success"):
                    intents = result.get("data", [])
                    if isinstance(intents, list):
                        # 只保留缺失维度的测试用例
                        for c in intents:
                            # 不再写入 ref_req_id，测试用例通过 case_id 与需求关联
                            new_intents.append(c)

        return new_intents

    # ═══════════════════════════════════════════════
    # Markdown 报告章节
    # ═══════════════════════════════════════════════

    @staticmethod
    def format_report_section(coverage_result: dict) -> str:
        """将覆盖矩阵转为 Markdown 报告章节。"""
        summary = coverage_result.get("summary", {})
        matrix = coverage_result.get("matrix", {})
        gaps = coverage_result.get("gaps", [])

        lines = [
            "",
            "## 需求覆盖矩阵",
            "",
            "| 指标 | 数值 |",
            "|------|------|",
            "| 需求总数 | %d |" % coverage_result.get("total_requirements", 0),
            "| 测试用例总数 | %d |" % coverage_result.get("total_cases", 0),
            "| 覆盖缺口 | %d 条需求 |" % summary.get("requirements_with_gaps", 0),
            "| 覆盖率 | %.1f%% |" % summary.get("coverage_rate", 0),
            "| 缺口总数 | %d 个维度 |" % summary.get("total_gaps", 0),
            "",
            "### 维度缺口统计",
            "",
            "| 维度 | 缺口数 |",
            "|------|--------|",
        ]
        for dim, count in summary.get("missing_by_dimension", {}).items():
            dim_label = {
                "positive": "正向覆盖",
                "negative": "异常覆盖",
                "boundary": "边界值",
                "security": "安全",
                "api_check": "API轨校验",
                "api_coverage": "API接口覆盖",
                "db_coverage": "DB表覆盖",
                "page_coverage": "页面覆盖",
                "trace_complete": "追溯链路",
            }.get(dim, dim)
            lines.append("| %s | %d |" % (dim_label, count))

        if gaps:
            lines += ["", "### 覆盖缺口明细", ""]
            lines.append("| 需求编号 | 需求描述 | 缺失维度 | 现有测试用例数 |")
            lines.append("|----------|----------|----------|------------|")
            for g in gaps[:50]:
                lines.append(
                    "| %s | %s | %s | %d |"
                    % (
                        g["req_id"],
                        g["description"][:40],
                        ", ".join(g["missing_dimensions"]),
                        g["total_cases"],
                    )
                )
            if len(gaps) > 50:
                lines.append("| ... | 共 %d 条缺口 | ... | ... |" % len(gaps))

        # 完整矩阵表（前 30 条需求）
        if matrix:
            lines += ["", "### 需求×维度矩阵（前30条）", ""]
            header = "| 需求编号 | 正向 | 异常 | 边界 | 安全 | API校验 | API覆盖 | DB覆盖 | 页面 | 链路 |"
            sep = "|----------|------|------|------|------|---------|---------|--------|------|------|"
            lines.append(header)
            lines.append(sep)
            for rid, dims in list(matrix.items())[:30]:
                icons = []
                for d in [
                    "positive",
                    "negative",
                    "boundary",
                    "security",
                    "api_check",
                    "api_coverage",
                    "db_coverage",
                    "page_coverage",
                    "trace_complete",
                ]:
                    s = dims.get(d, "missing")
                    if s == "covered":
                        icons.append("✅")
                    elif s == "n/a":
                        icons.append("—")
                    else:
                        icons.append("⚠️")
                lines.append("| %s | %s |" % (rid, " | ".join(icons)))

        lines.append("")
        return "\n".join(lines)
