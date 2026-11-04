# -*- coding: utf-8 -*-
from __future__ import annotations

"""
追溯链路引擎 — 覆盖率计算
========================
计算 API、DB 表、Figma 页面的覆盖率，以及追溯链路完整性检测。
"""

from typing import Any

from .models import TraceLink, CoverageReport
from .data_source import DataSourceExtractor


class TraceabilityCoverage:
    """数据源覆盖率计算器。"""

    def compute_all_coverage(
        self,
        trace_links: dict[str, TraceLink],
        test_cases: list[dict] = None,
    ) -> CoverageReport:
        """计算全部数据源维度的覆盖率。"""
        intents = test_cases or []

        report = CoverageReport()
        report.api_coverage = self.compute_api_coverage(trace_links, intents)
        report.db_coverage = self.compute_db_coverage(trace_links, intents)
        report.page_coverage = self.compute_page_coverage(trace_links, intents)
        report.trace_completeness = self.compute_trace_completeness(trace_links)
        report.summary = self._build_coverage_summary(report)
        return report

    def compute_api_coverage(
        self, links: dict[str, TraceLink], intents: list[dict]
    ) -> dict[str, Any]:
        """每个 API endpoint 是否被至少一个测试用例覆盖。"""
        # 收集所有 API endpoint
        all_endpoints = {}
        for link in links.values():
            for ep in link.api_endpoints:
                key = f"{ep.get('method', '?')}:{ep.get('path', '?')}"
                all_endpoints[key] = {
                    "method": ep.get("method", ""),
                    "path": ep.get("path", ""),
                    "covered": False,
                    "case_ids": [],
                }

        # 扫描测试用例中的 api_checks
        for intent in intents:
            cid = intent.get("case_id", "")
            case_actions_json = intent.get("case_actions_json", "")
            # 从步骤中提取 api_checks 引用的接口
            matched = DataSourceExtractor.match_api_in_steps(
                case_actions_json, all_endpoints
            )
            for key in matched:
                if cid not in all_endpoints[key]["case_ids"]:
                    all_endpoints[key]["case_ids"].append(cid)
                all_endpoints[key]["covered"] = True

        return all_endpoints

    def compute_db_coverage(
        self, links: dict[str, TraceLink], intents: list[dict]
    ) -> dict[str, Any]:
        """每个关键业务表是否被至少一个测试用例的 db_checks 覆盖。"""
        all_tables = {}
        for link in links.values():
            for table in link.db_tables:
                if table not in all_tables:
                    all_tables[table] = {"covered": False, "case_ids": []}

        for intent in intents:
            cid = intent.get("case_id", "")
            case_actions_json = intent.get("case_actions_json", "")
            matched = DataSourceExtractor.match_tables_in_steps(
                case_actions_json, all_tables
            )
            for table in matched:
                if cid not in all_tables[table]["case_ids"]:
                    all_tables[table]["case_ids"].append(cid)
                all_tables[table]["covered"] = True

        return all_tables

    def compute_page_coverage(
        self, links: dict[str, TraceLink], intents: list[dict]
    ) -> dict[str, Any]:
        """每个 Figma 页面是否被至少一个测试用例覆盖。"""
        all_pages = {}
        for link in links.values():
            for page in link.figma_pages:
                if page not in all_pages:
                    all_pages[page] = {"covered": False, "case_ids": []}

        for intent in intents:
            cid = intent.get("case_id", "")
            title = intent.get("title", "")
            steps = str(intent.get("case_actions_json", ""))
            text = f"{title} {steps}"
            for page in all_pages:
                if page and len(page) >= 2 and page in text:
                    if cid not in all_pages[page]["case_ids"]:
                        all_pages[page]["case_ids"].append(cid)
                    all_pages[page]["covered"] = True

        return all_pages

    def compute_page_coverage_incremental(
        self,
        links: dict[str, TraceLink],
        intents: list[dict],
        previous: dict[str, Any] | None = None,
        affected_pages: list[str] | None = None,
    ) -> dict[str, Any]:
        """增量计算页面覆盖率；未命中 affected_pages 的页面复用 previous。"""
        if not affected_pages:
            return self.compute_page_coverage(links, intents)
        affected_set = {p for p in affected_pages if p}
        result = dict(previous or {})
        all_pages = set(result.keys())
        for link in links.values():
            all_pages.update(link.figma_pages or [])
        for page in all_pages:
            if page not in affected_set:
                continue
            meta = {"covered": False, "case_ids": []}
            for intent in intents:
                cid = intent.get("case_id", "")
                text = "%s %s" % (
                    intent.get("title", ""),
                    intent.get("case_actions_json", ""),
                )
                if page and page in text:
                    if cid not in meta["case_ids"]:
                        meta["case_ids"].append(cid)
                    meta["covered"] = True
            result[page] = meta
        return result

    def compute_trace_completeness(self, links: dict[str, TraceLink]) -> dict[str, Any]:
        """检测每条需求的追溯链路是否完整（4环节不断链）。"""
        completeness = {}
        for rid, link in links.items():
            chain = {
                "has_api": len(link.api_endpoints) > 0,
                "has_db": len(link.db_tables) > 0,
                "has_page": len(link.figma_pages) > 0,
                "has_dependency": len(link.related_reqs) > 0,
            }
            missing = [k for k, v in chain.items() if not v]
            completeness[rid] = {
                "req_id": rid,
                "module": link.module,
                "chain": chain,
                "complete": len(missing) == 0,
                "missing_links": missing,
            }
        return completeness

    def _build_coverage_summary(self, report: CoverageReport) -> dict:
        """汇总覆盖率统计。"""
        api_total = len(report.api_coverage)
        api_covered = sum(1 for v in report.api_coverage.values() if v["covered"])
        db_total = len(report.db_coverage)
        db_covered = sum(1 for v in report.db_coverage.values() if v["covered"])
        page_total = len(report.page_coverage)
        page_covered = sum(1 for v in report.page_coverage.values() if v["covered"])
        trace_total = len(report.trace_completeness)
        trace_complete = sum(
            1 for v in report.trace_completeness.values() if v["complete"]
        )

        return {
            "api": {
                "total": api_total,
                "covered": api_covered,
                "rate": round(api_covered / max(api_total, 1) * 100, 1),
            },
            "db": {
                "total": db_total,
                "covered": db_covered,
                "rate": round(db_covered / max(db_total, 1) * 100, 1),
            },
            "page": {
                "total": page_total,
                "covered": page_covered,
                "rate": round(page_covered / max(page_total, 1) * 100, 1),
            },
            "trace": {
                "total": trace_total,
                "complete": trace_complete,
                "rate": round(trace_complete / max(trace_total, 1) * 100, 1),
            },
        }
