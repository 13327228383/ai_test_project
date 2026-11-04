# -*- coding: utf-8 -*-
"""
部署文档 匹配模块
================
DeploymentMatcher 类 + 两阶段语义匹配 + 报告格式化。

重构说明：
- 本文件现为 facade，通过多继承组合各 mixin 的能力。
- 具体实现已拆分到 matcher_mixins/ 子目录：
  - _base.py      : 初始化 + embedding + 代理静态方法
  - _phase1.py    : Phase1 模块名模糊匹配
  - _phase2.py    : Phase2 功能点语义匹配
  - _orchestrate.py: 匹配编排 + 主入口
"""

from typing import Any

from src.extensions.deployment.matcher.base import _MatcherBaseMixin
from src.extensions.deployment.matcher.phase1 import _MatcherPhase1Mixin
from src.extensions.deployment.matcher.phase2 import _MatcherPhase2Mixin
from src.extensions.deployment.matcher.orchestrate import _MatcherOrchestrateMixin

from src.extensions.deployment.parser import MatchReport


class DeploymentMatcher(
    _MatcherBaseMixin,
    _MatcherPhase1Mixin,
    _MatcherPhase2Mixin,
    _MatcherOrchestrateMixin,
):
    """
    部署文档匹配器。

    用法：
        matcher = DeploymentMatcher()
        result = matcher.filter_intents_by_deployment(
            # ★ 某SPA系统业务名已统一到 config.demo.sample_project
            doc_path=f"projects/{DEMO_PROJECT}/deployment.md",
            intents=all_test_cases,
            version="V2.5.6",
        )
    """

    pass


# ============================================================
# 报告格式化
# ============================================================


def format_report(report: MatchReport, version: str = "") -> str:
    """将匹配报告格式化为可读文本。"""
    lines = []
    sep = "=" * 60
    lines.append(sep)
    lines.append(f"  Deployment Matching Report (version: {version or 'ALL'})")
    lines.append(sep)
    lines.append(f"  Total features:     {report.total_features}")
    lines.append(f"  Matched features:   {report.matched_features}")
    lines.append(f"  Unmatched features: {len(report.unmatched_features)}")
    lines.append(f"  Low confidence:     {len(report.low_confidence_matches)}")
    lines.append("")

    if report.results:
        lines.append("--- Per Feature Detail ---")
        for r in report.results:
            row = r.deployment_row
            ver_tag = f"[{row.version}] " if row.version else ""
            lines.append(f"\n  {ver_tag}{row.module} > {row.feature}")

            if r.matched_intents:
                lines.append(f"    ✅ Matched ({len(r.matched_intents)} intents):")
                for d in r.matched_intents[:5]:
                    lines.append(
                        f"      - {d.case_id} [{d.combined_score:.2f}] {d.title}"
                    )
                if len(r.matched_intents) > 5:
                    lines.append(f"      ... and {len(r.matched_intents) - 5} more")

            if r.low_confidence:
                lines.append(f"    ⚠️  Low confidence ({len(r.low_confidence)}):")
                for d in r.low_confidence:
                    lines.append(
                        f"      - {d.case_id} [{d.combined_score:.2f}] {d.title} "
                        f"(mod={d.phase1_score:.2f}, feat={d.phase2_score:.2f})"
                    )

            if r.unmatched:
                lines.append(f"    ❌ NO INTENTS MATCHED — 建议新增测试用例")

    if report.unmatched_features:
        lines.append(
            f"\n--- ❌ Unmatched Features ({len(report.unmatched_features)}) ---"
        )
        for row in report.unmatched_features:
            ver_tag = f"[{row.version}] " if row.version else ""
            lines.append(f"  - {ver_tag}{row.module} > {row.feature}")

    if report.low_confidence_matches:
        lines.append(f"\n--- ⚠️  Low Confidence (Manual Review Recommended) ---")
        for row, detail in report.low_confidence_matches:
            ver_tag = f"[{row.version}] " if row.version else ""
            lines.append(f"  - {ver_tag}{row.module} > {row.feature}")
            lines.append(
                f"    → {detail.case_id} [{detail.combined_score:.2f}] "
                f"{detail.title} (mod={detail.phase1_score:.2f}, feat={detail.phase2_score:.2f})"
            )

    lines.append("\n" + sep)
    return "\n".join(lines)


# ============================================================
# 便捷函数（供外部直接调用）
# ============================================================


def filter_intents_by_deployment(
    doc_path: str = "",
    intents: list[dict] = None,
    version: str = "",
    doc_content: str = "",
) -> dict[str, Any]:
    """
    便捷函数：一步完成部署文档 → 测试用例匹配。
    优先使用 doc_content（内存字符串），否则回退 doc_path 磁盘文件。
    """
    matcher = DeploymentMatcher()
    return matcher.filter_intents_by_deployment(
        doc_path=doc_path,
        intents=intents,
        version=version,
        doc_content=doc_content,
    )
