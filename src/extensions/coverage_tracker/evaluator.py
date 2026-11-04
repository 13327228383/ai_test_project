# -*- coding: utf-8 -*-
"""覆盖维度评估 mixin — 从 coverage_tracker.py 拆出，零逻辑变更。"""

from typing import Any


class _CoverageEvaluatorMixin:
    """compute_coverage + _evaluate_dimensions + 关键词判别 + 摘要。"""

    # ═══════════════════════════════════════════════
    # 覆盖矩阵计算
    # ═══════════════════════════════════════════════

    def compute_coverage(
        self, requirements: list[dict], intents: list[dict]
    ) -> dict[str, Any]:
        """
        计算需求×维度覆盖矩阵。

        参数:
            requirements: [{"req_id": "REQ-001", "description": "...", ...}, ...]
            intents:        [{"case_id": "TC-001-1", "case_actions_json": "...", ...}, ...]

        返回:
            {"matrix": {req_id: {dim: status}}, "gaps": [...], "summary": {...}}
        """
        # 构建 req_id → intents 映射；显式关联字段优先，TC 命名约定仅兜底。
        req_intents: dict[str, list[dict]] = {}
        for c in intents:
            rid = self._extract_req_id_from_intent(c)
            if rid and rid != "REQ-":
                req_intents.setdefault(rid, []).append(c)

        matrix = {}
        gaps = []

        for req in requirements:
            rid = req.get("req_id", "")
            desc = req.get("description", "")
            if not rid:
                continue

            my_intents = req_intents.get(rid, [])
            dims = self._evaluate_dimensions(rid, desc, my_intents)
            matrix[rid] = dims

            # 检查缺口（所有需求都记录 total_cases，无缺口的需求 missing_dimensions 为空列表）
            missing = [d for d, s in dims.items() if s == "missing"]
            gaps.append(
                {
                    "req_id": rid,
                    "description": desc[:100],
                    "missing_dimensions": missing,
                    "total_cases": len(my_intents),
                }
            )

        summary = self._build_summary(matrix, gaps)

        return {
            "matrix": matrix,
            "gaps": gaps,
            "summary": summary,
            "total_requirements": len(requirements),
            "total_cases": len(intents),
        }

    @staticmethod
    def _extract_req_id_from_intent(intent: dict[str, Any]) -> str:
        """★ P2-01: 仅使用显式关联字段提取需求 ID，不再依赖 TC 命名约定。

        测试用例必须显式写入 req_id / requirement_id / ref_req_id 等字段，
        无法推断时视为未关联，由调用方（compute_coverage）跳过。
        """
        if not isinstance(intent, dict):
            return ""
        for key in (
            "req_id",
            "requirement_id",
            "ref_req_id",
            "source_req_id",
            "requirement_ref",
        ):
            value = str(intent.get(key, "") or "").strip()
            if value:
                return value
        # ★ 不再回退到 TC 命名约定推断
        return ""

    # ★ v6: 数据源追溯维度（通过 set_trace_links 注入，实例属性见 __init__）

    def set_trace_links(self, trace_links: dict[str, Any]) -> None:
        """注入追溯链路数据（来自 TraceabilityEngine），用于数据源维度评估。"""
        self._trace_links = trace_links or {}

    def _evaluate_dimensions(
        self, rid: str, desc: str, intents: list[dict]
    ) -> dict[str, str]:
        """对单个需求评估 9 个维度（5 测试 + 4 数据源）。每个维度状态: covered / missing / n/a。"""
        dims = {}

        # 收集所有测试用例的动作信息用于覆盖度分析
        all_techniques = set()
        has_api_check = False
        intent_text_all = ""
        for c in intents:
            # 检查 api_checks（从 case_actions_json 中解析）
            # ★ 2026-08-04: 兼容多字段名（case_actions_json / steps_json / payload.steps_json）——
            #   不同阶段用例的步骤存放字段不同（test_cases 表两列都有），只读单一字段会漏判技术维度。
            steps = c.get("case_actions_json", "")
            if not steps:
                steps = c.get("steps_json", "")
            if not steps and isinstance(c.get("payload"), dict):
                steps = str(c.get("payload").get("steps_json", "") or "")
            if isinstance(steps, str) and "api_checks" in steps:
                has_api_check = True
            # 从兼容 steps 文本内容推断测试技术
            steps_str = str(steps).lower()
            title = str(c.get("title", "") or "")
            case_type = str(c.get("case_type", "") or "")
            # ★ 2026-09-15 FIX: 把 title + case_type 也纳入技术维度推断 —
            #   AI 生成的 case_actions_json 经常只有 navigate/screenshot，
            #   有意义的技术关键词全在 title（"SQL注入防护"、"边界值验证"、"异常输入校验"）。
            search_text = (steps_str + " " + title.lower() + " " + case_type.lower()).lower()
            if any(kw in search_text for kw in ("场景", "流程", "登录", "探索", "验证", "用例")):
                all_techniques.add("场景法")
            if any(kw in search_text for kw in ("等价类", "边界", "无效", "有效", "异常", "校验")):
                all_techniques.add("等价类")
                all_techniques.add("边界值")
            if any(kw in search_text for kw in ("注入", "xss", "csrf", "安全", "越权", "防护")):
                all_techniques.add("错误推测")
            # 收集所有测试用例文本用于页面覆盖检测
            intent_text_all += f" {title} {steps_str}"

        desc_lower = desc.lower()

        # ═══ 测试方法论 5 维度 ═══

        # Positive: 有场景法/状态迁移
        dims["positive"] = (
            "covered" if all_techniques & {"场景法", "状态迁移"} else "missing"
        )

        # Negative: 有等价类/错误推测/判定表/因果图/探索性
        neg_techs = {"等价类", "错误推测", "判定表", "因果图", "探索性测试"}
        if all_techniques & neg_techs:
            dims["negative"] = "covered"
        elif self._has_input_keywords(desc):
            dims["negative"] = "missing"
        else:
            dims["negative"] = "n/a"

        # Boundary: 有边界值/正交实验
        if all_techniques & {"边界值", "正交实验"}:
            dims["boundary"] = "covered"
        elif self._has_boundary_keywords(desc):
            dims["boundary"] = "missing"
        else:
            dims["boundary"] = "n/a"

        # Security: 类型为"安全"
        security_direct = any(
            str(c.get("case_type", "")).strip() == "安全" for c in intents
        )
        if security_direct:
            dims["security"] = "covered"
        elif self._has_input_keywords(desc):
            dims["security"] = "missing"
        else:
            dims["security"] = "n/a"

        # API check: 至少一个测试用例有 api_checks 或 title 含 API 关键词
        api_keywords = ("api", "接口", "http", "请求", "endpoint", "rest", "graphql", "swagger")
        api_in_title = any(
            any(kw in str(c.get("title", "")).lower() for kw in api_keywords)
            or any(kw in str(c.get("case_actions_json", "")).lower() for kw in api_keywords)
            for c in intents
        )
        if has_api_check or api_in_title:
            dims["api_check"] = "covered"
        else:
            dims["api_check"] = "missing"

        # ═══ 数据源追溯 4 维度（v6 新增）═══

        trace_link = self._trace_links.get(rid) if self._trace_links else None
        if trace_link:
            # 统一获取属性（支持 dict 和 TraceLink 对象）
            def _get(obj, attr, default=None):
                if isinstance(obj, dict):
                    return obj.get(attr, default)
                return getattr(obj, attr, default)

            # API_COVERAGE: 关联的 API 是否在测试用例动作中被引用
            api_endpoints = _get(trace_link, "api_endpoints", [])
            if api_endpoints:
                api_covered = False
                for ep in api_endpoints:
                    path = (
                        ep.get("path", "")
                        if isinstance(ep, dict)
                        else getattr(ep, "path", "")
                    )
                    if path and len(path) > 2 and path in intent_text_all:
                        api_covered = True
                        break
                dims["api_coverage"] = "covered" if api_covered else "missing"
            else:
                dims["api_coverage"] = "n/a"

            # DB_COVERAGE: 关联的 DB 表是否在 db_checks 中被引用
            db_tables = _get(trace_link, "db_tables", {})
            if db_tables:
                db_covered = False
                if isinstance(db_tables, dict):
                    for table in db_tables:
                        if table.upper() in intent_text_all.upper():
                            db_covered = True
                            break
                dims["db_coverage"] = "covered" if db_covered else "missing"
            else:
                dims["db_coverage"] = "n/a"

            # PAGE_COVERAGE: 关联的页面是否在测试用例动作中被引用
            figma_pages = _get(trace_link, "figma_pages", [])
            if figma_pages:
                page_covered = False
                for page in figma_pages:
                    pname = (
                        page
                        if isinstance(page, str)
                        else (
                            page.get("name", "")
                            if isinstance(page, dict)
                            else str(page)
                        )
                    )
                    if pname and len(pname) >= 2 and pname in intent_text_all:
                        page_covered = True
                        break
                dims["page_coverage"] = "covered" if page_covered else "missing"
            else:
                dims["page_coverage"] = "n/a"

            # TRACE_COMPLETE: 4 环节是否不断链
            has_api = bool(api_endpoints)
            has_db = bool(db_tables)
            has_page = bool(figma_pages)
            chain = [has_api, has_db, has_page]
            dims["trace_complete"] = (
                "covered" if all(chain) else ("missing" if any(chain) else "n/a")
            )
        else:
            # 无追溯链路数据
            dims["api_coverage"] = "n/a"
            dims["db_coverage"] = "n/a"
            dims["page_coverage"] = "n/a"
            dims["trace_complete"] = "n/a"

        return dims

    def _has_input_keywords(self, desc: str) -> bool:
        """判断需求是否涉及用户输入。"""
        return any(kw in desc for kw in self.SECURITY_KEYWORDS)

    def _has_boundary_keywords(self, desc: str) -> bool:
        """判断需求是否涉及数值/边界。"""
        return any(kw in desc for kw in self.BOUNDARY_KEYWORDS)

    @staticmethod
    def _build_summary(matrix: dict, gaps: list) -> dict:
        total = len(matrix)
        with_gaps = len(gaps)
        all_dims = [
            "positive",
            "negative",
            "boundary",
            "security",
            "api_check",
            "api_coverage",
            "db_coverage",
            "page_coverage",
            "trace_complete",
        ]
        missing_counts = {d: 0 for d in all_dims}
        for rid, dims in matrix.items():
            for d, s in dims.items():
                if s == "missing" and d in missing_counts:
                    missing_counts[d] += 1
        # ★ 2026-09-15 FIX: coverage_rate 用维度级精确计算（covered / (covered + missing)），
        #   而不是原来的二元判定（有缺口的需求数 / 总需求数）——后者太粗，有 1 个 missing 就算 0%。
        covered_count = sum(
            1 for dims in matrix.values() for v in dims.values() if v == "covered"
        )
        missing_count = sum(
            1 for dims in matrix.values() for v in dims.values() if v == "missing"
        )
        dim_coverage_rate = round(
            covered_count / max(covered_count + missing_count, 1) * 100, 1
        )
        return {
            "total_requirements": total,
            "requirements_with_gaps": with_gaps,
            "coverage_rate": dim_coverage_rate,
            "covered_dims": covered_count,
            "missing_dims": missing_count,
            "missing_by_dimension": missing_counts,
            "total_gaps": sum(missing_counts.values()),
        }
