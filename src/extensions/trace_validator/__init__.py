# -*- coding: utf-8 -*-
"""
追溯链路校验器 (TraceValidator)
================================
校验测试用例中的 db_checks / api_checks 是否匹配追溯链路中的表/API。

★ P1: 防止AI幻觉——校验测试用例中的表名/API路径是否在trace_links中存在。

【2026-06-18 重构】原 407 行单文件按职责拆分：
- db_checks.py     — _DbChecksMixin（_validate_db_checks / _validate_single_db_check / _rag_fallback_check）
- api_extractors.py — _ApiAndExtractMixin（_validate_api_checks + _extract_*）
- fuzzy_match.py   — _FuzzyMatchMixin（_find_similar_table / _field / _api）
- __init__.py      — facade TraceValidator + validate_intent + 索引构建 + 便捷函数
"""

from typing import Any
import logging

from .db_checks import _DbChecksMixin
from .api_extractors import _ApiAndExtractMixin
from .fuzzy_match import _FuzzyMatchMixin


_log = logging.getLogger(__name__)


class TraceValidator(_DbChecksMixin, _ApiAndExtractMixin, _FuzzyMatchMixin):
    """
    追溯链路校验器。

    功能：
    1. 校验 db_checks 中的表名是否在 trace_links 中存在
    2. 校验 api_checks 中的 API 路径是否在 trace_links 中存在
    3. 校验 db_checks 中的字段名是否在对应表的 schema 中存在
    4. 收集校验结果，生成报告
    """

    def __init__(
        self, trace_links: dict[str, Any] = None, db_schema: dict[str, Any] = None
    ):
        """
        Args:
            trace_links: 追溯链路数据 {req_id: {"api_endpoints": [...], "db_tables": {...}}}
            db_schema: 数据库schema {table: {col: type, ...}, ...}
        """
        self.trace_links = trace_links or {}
        self.db_schema = db_schema or {}
        self._all_tables = set()
        self._all_apis = set()
        self._all_fields = {}  # {table: set(fields)}
        self._build_index()

    def _build_index(self):
        """构建快速查找索引。"""
        # 收集所有表名
        for req_id, link in self.trace_links.items():
            if isinstance(link, dict):
                db_tables = link.get("db_tables", {})
                api_endpoints = link.get("api_endpoints", [])
            else:
                # TraceLink dataclass
                db_tables = getattr(link, "db_tables", {})
                api_endpoints = getattr(link, "api_endpoints", [])
            if isinstance(db_tables, dict):
                for table_name in db_tables:
                    self._all_tables.add(table_name.upper())
                    # 收集字段
                    fields = (
                        db_tables.get(table_name, [])
                        if isinstance(db_tables, dict)
                        else getattr(db_tables, table_name, [])
                    )
                    if isinstance(fields, list):
                        if table_name.upper() not in self._all_fields:
                            self._all_fields[table_name.upper()] = set()
                        self._all_fields[table_name.upper()].update(
                            f.upper() for f in fields
                        )
            if isinstance(api_endpoints, list):
                for ep in api_endpoints:
                    if isinstance(ep, dict):
                        path = ep.get("path", "")
                        if path:
                            self._all_apis.add(path)

        # 从db_schema补充字段信息
        for table, cols in self.db_schema.items():
            table_upper = table.upper()
            if table_upper not in self._all_fields:
                self._all_fields[table_upper] = set()
            if isinstance(cols, dict):
                self._all_fields[table_upper].update(c.upper() for c in cols.keys())
            elif isinstance(cols, list):
                self._all_fields[table_upper].update(c.upper() for c in cols)

        _log.info(
            "[TraceValidator] 索引构建完成: %d 表, %d API, %d 表有字段信息",
            len(self._all_tables),
            len(self._all_apis),
            len(self._all_fields),
        )

    # ══════════════════════════════════════════════════════════
    # 校验入口
    # ══════════════════════════════════════════════════════════

    def validate_intent(
        self, intent: dict[str, Any], req_id: str = None
    ) -> dict[str, Any]:
        """
        校验单个测试用例的 db_checks 和 api_checks。

        Returns:
            {
                "valid": bool,
                "db_checks": {"valid": bool, "issues": [...], "warnings": [...]},
                "api_checks": {"valid": bool, "issues": [...], "warnings": [...]},
                "summary": str
            }
        """
        result = {
            "valid": True,
            "db_checks": {"valid": True, "issues": [], "warnings": []},
            "api_checks": {"valid": True, "issues": [], "warnings": []},
            "summary": "",
        }

        # 获取测试用例中的 checks
        steps = intent.get("steps", [])
        all_db_checks = []
        all_api_checks = []

        for step in steps:
            if isinstance(step, dict):
                db_checks = step.get("db_checks", [])
                api_checks = step.get("api_checks", [])
                if db_checks:
                    all_db_checks.extend(
                        db_checks if isinstance(db_checks, list) else [db_checks]
                    )
                if api_checks:
                    all_api_checks.extend(
                        api_checks if isinstance(api_checks, list) else [api_checks]
                    )

        # 校验 db_checks
        if all_db_checks:
            db_result = self._validate_db_checks(all_db_checks, req_id)
            result["db_checks"] = db_result
            if not db_result["valid"]:
                result["valid"] = False

        # 校验 api_checks
        if all_api_checks:
            api_result = self._validate_api_checks(all_api_checks, req_id)
            result["api_checks"] = api_result
            if not api_result["valid"]:
                result["valid"] = False

        # 生成摘要
        parts = []
        if all_db_checks:
            parts.append(
                "DB校验: %d条, %d问题, %d警告"
                % (
                    len(all_db_checks),
                    len(result["db_checks"]["issues"]),
                    len(result["db_checks"]["warnings"]),
                )
            )
        if all_api_checks:
            parts.append(
                "API校验: %d条, %d问题, %d警告"
                % (
                    len(all_api_checks),
                    len(result["api_checks"]["issues"]),
                    len(result["api_checks"]["warnings"]),
                )
            )
        result["summary"] = "; ".join(parts) if parts else "无校验项"

        return result


# ══════════════════════════════════════════════════════════
# Step3/Step4 checks[] 格式适配器（v3.0+ 测试用例格式）
# ══════════════════════════════════════════════════════════

import re as _re


def _extract_tables_from_sql(sql: str) -> list:
    """从 SQL 语句中提取表名（FROM/JOIN/INTO/UPDATE 后的标识符）。"""
    if not sql:
        return []
    tables = []
    # 匹配 FROM table_name, JOIN table_name, INTO table_name, UPDATE table_name
    for m in _re.finditer(
        r"(?:FROM|JOIN|INTO|UPDATE)\s+`?(\w+)`?", sql, _re.IGNORECASE
    ):
        t = m.group(1).upper()
        if t not in (
            "SELECT",
            "WHERE",
            "SET",
            "VALUES",
            "COUNT",
            "MAX",
            "MIN",
            "AVG",
            "SUM",
        ):
            tables.append(t)
    return tables


def _extract_api_path(check_content) -> str:
    """从 check 内容中提取 API 路径。"""
    if isinstance(check_content, dict):
        return check_content.get("path", "") or check_content.get("url", "")
    elif isinstance(check_content, str):
        m = _re.search(
            r"(?:GET|POST|PUT|DELETE|PATCH)\s+(/\S*)", check_content, _re.IGNORECASE
        )
        if m:
            return m.group(1)
    return ""


def validate_checks_against_trace(
    intents: list[dict[str, Any]],
    trace_links: dict[str, Any] = None,
    db_schema: dict[str, Any] = None,
) -> dict[str, Any]:
    """v3.0+ 适配器：校验 checks[] 格式的测试用例（track=db/api）。

    返回:
        {"valid": bool, "intents": [...], "summary": str}
    """
    if not trace_links:
        return {"valid": True, "intents": [], "summary": "无追溯链路数据，跳过校验"}

    # 1. 从 trace_links 构建索引
    all_tables = set()
    all_apis = set()
    for rid, link in trace_links.items():
        if isinstance(link, dict):
            # 处理 TraceLink 对象
            db_tables = link.get("db_tables") or {}
            if isinstance(db_tables, str):
                try:
                    import json

                    db_tables = json.loads(db_tables)
                except Exception as _je:
                    _log.debug(
                        "[TraceValidator] db_tables JSON 解析失败,降级空dict: %s",
                        str(_je)[:60],
                        exc_info=True,
                    )
                    db_tables = {}
            if isinstance(db_tables, dict):
                for t in db_tables:
                    all_tables.add(str(t).upper())
            elif isinstance(db_tables, list):
                for t in db_tables:
                    all_tables.add(str(t).upper())

            api_endpoints = link.get("api_endpoints") or []
            if isinstance(api_endpoints, str):
                try:
                    import json

                    api_endpoints = json.loads(api_endpoints)
                except Exception:
                    api_endpoints = []
            if isinstance(api_endpoints, list):
                for ep in api_endpoints:
                    if isinstance(ep, dict):
                        path = ep.get("path", "")
                        if path:
                            all_apis.add(path)
                    elif isinstance(ep, str):
                        all_apis.add(ep)

    # 2. 使用模糊匹配器
    validator = TraceValidator(trace_links, db_schema)

    results = []
    all_valid = True

    for intent in intents:
        if not isinstance(intent, dict):
            continue
        case_id = intent.get("case_id", "")
        req_id = intent.get("req_id", "")
        issues = []
        warnings = []

        checks = intent.get("checks", [])
        if not isinstance(checks, list):
            checks = []

        for check in checks:
            if not isinstance(check, dict):
                continue
            track = check.get("track", "")
            precision = check.get("precision", "fuzzy")
            check_content = check.get("check", {})

            # 只校验 precision=exact 的检查（fuzzy 的不需要精确表名/API）
            if precision != "exact":
                continue

            if track == "db":
                sql = ""
                if isinstance(check_content, dict):
                    sql = check_content.get("sql", "")
                elif isinstance(check_content, str):
                    sql = check_content
                if not sql:
                    continue

                tables = _extract_tables_from_sql(sql)
                for t in tables:
                    if t not in all_tables:
                        # 模糊匹配
                        similar = validator._find_similar_table(t)
                        if similar:
                            warnings.append(
                                "[L2降级] 表 '%s' 不在追溯链路中，模糊匹配到 '%s'"
                                % (t, similar)
                            )
                        else:
                            issues.append(
                                "表 '%s' 不在追溯链路中（req=%s）。可用表: %s"
                                % (t, req_id, ", ".join(sorted(all_tables)[:10]))
                            )

            elif track == "api":
                path = _extract_api_path(check_content)
                if not path:
                    continue
                if path not in all_apis:
                    similar = validator._find_similar_api(path)
                    if similar:
                        warnings.append(
                            "API路径 '%s' 不在追溯链路中，相似: '%s'" % (path, similar)
                        )
                    else:
                        issues.append(
                            "API路径 '%s' 不在追溯链路中（req=%s）" % (path, req_id)
                        )

        valid = len(issues) == 0
        if not valid:
            all_valid = False

        results.append(
            {
                "case_id": case_id,
                "title": intent.get("title", ""),
                "result": {
                    "valid": valid,
                    "db_checks": {"valid": True, "issues": [], "warnings": []},
                    "api_checks": {"valid": True, "issues": [], "warnings": []},
                    "issues": issues,
                    "warnings": warnings,
                    "summary": "通过" if valid else ("%d 个问题" % len(issues)),
                },
            }
        )

    return {
        "valid": all_valid,
        "intents": results,
        "summary": "共 %d 测试用例, %d 通过, %d 失败"
        % (
            len(results),
            sum(1 for r in results if r["result"]["valid"]),
            sum(1 for r in results if not r["result"]["valid"]),
        ),
    }
