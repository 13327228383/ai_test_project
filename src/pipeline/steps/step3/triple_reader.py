# -*- coding: utf-8 -*-
"""
三元组读取器 — 为冒烟用例和测试设计提供统一数据源
==================================================
从 requirements 表读取原子需求点（三元组），
替换原有的 excel_handler.read_requirements() 粗粒度读取。

三元组 = operation + condition_expr + expected_feedback + control_type
每个三元组是原子需求，数量稳定，不受 AI 切片波动影响。

返回字段:
  req_id, module, operation, condition_expr, expected_feedback,
  control_type, description, is_implicit
"""

import logging
from typing import Any

_log = logging.getLogger(__name__)

# 控件类型 → 适用测试类型（用于测试设计 XMind）
CONTROL_TEST_TYPES = {
    "button": ["功能测试", "UI测试"],
    "input": ["功能测试", "边界测试", "安全测试", "数据测试"],
    "select": ["功能测试", "边界测试", "数据测试"],
    "upload": ["功能测试", "边界测试", "安全测试", "数据测试"],
    "nav": ["功能测试", "UI测试"],
    "interactive": ["功能测试", "UI测试", "边界测试"],
    "composite": ["功能测试", "UI测试", "边界测试", "数据测试"],
    # 兜底
    "": ["功能测试", "UI测试", "边界测试", "数据测试", "安全测试"],
}
_FALLBACK_TYPES = CONTROL_TEST_TYPES[""]


def get_test_types_for_control(control_type: str) -> list[str]:
    """根据控件类型返回适用的测试类型列表。"""
    return CONTROL_TEST_TYPES.get(control_type, _FALLBACK_TYPES)


def read_triples(
    project: str,
    version_id: int,
    module_filter: str = "",
    exclude_implicit: bool = True,
    exclude_generated: bool = False,
    limit: int = 0,
) -> list[dict[str, Any]]:
    """从 requirements 表读取三元组。

    Args:
        project: 项目名
        version_id: 版本ID
        module_filter: 模块名过滤（空=全部）
        exclude_implicit: 排除隐含需求（is_implicit=1）
        exclude_generated: 排除已建模需求（is_generated=1）
        limit: 限制条数（0=不限制）

    Returns:
        [{req_id, module, operation, condition_expr, expected_feedback,
          control_type, description, is_implicit}, ...]
    """
    conn = None
    try:
        from src.core.db_manager import get_connection

        conn = get_connection()
        if not conn:
            return []

        where = ["project = %s", "version_id = %s"]
        params = [project, version_id]

        if module_filter:
            where.append("module = %s")
            params.append(module_filter)
        if exclude_implicit:
            where.append("(is_implicit IS NULL OR is_implicit = 0)")
        if exclude_generated:
            where.append("(is_generated IS NULL OR is_generated = 0)")

        sql = (
            "SELECT req_id, module, operation, condition_expr, expected_feedback, "
            "       description, is_implicit "
            "FROM requirements "
            "WHERE %s "
            "ORDER BY module, req_id" % " AND ".join(where)
        )
        if limit > 0:
            sql += " LIMIT %d" % limit

        cur = conn.execute(sql, tuple(params))
        rows = cur.fetchall() if cur else []
        from src.pipeline.step3._rule_extractor import (
            _infer_control_type_from_operation,
        )

        result = []
        for row in rows:
            operation = row.get("operation", "")
            result.append(
                {
                    "req_id": row.get("req_id", ""),
                    "module": row.get("module", ""),
                    "operation": operation,
                    "condition_expr": row.get("condition_expr", ""),
                    "expected_feedback": row.get("expected_feedback", ""),
                    "control_type": _infer_control_type_from_operation(operation),
                    "description": row.get("description", ""),
                    "is_implicit": bool(row.get("is_implicit", 0)),
                }
            )
        _log.info("[triple_reader] 读取 %d 条三元组 (project=%s)", len(result), project)
        return result
    except Exception as e:
        _log.error("[triple_reader] 读取失败: %s", str(e)[:120])
        return []
    finally:
        if conn:
            try:
                conn.close()
            except Exception as _e:
                _log.debug("conn.close 失败: %s", _e, exc_info=True)


def group_triples_by_module(triples: list[dict]) -> dict[str, list[dict]]:
    """按 module 分组三元组。"""
    from collections import defaultdict

    groups = defaultdict(list)
    for t in triples:
        mod = (t.get("module", "") or "").strip() or "默认模块"
        groups[mod].append(t)
    return dict(sorted(groups.items()))


def format_triple_for_prompt(triple: dict, index: int = 0) -> str:
    """将单个三元组格式化为 prompt 友好的文本。"""
    no = "(%d) " % index if index else ""
    parts = []
    if triple.get("operation"):
        parts.append("操作: %s" % triple["operation"])
    if triple.get("condition_expr"):
        parts.append("条件: %s" % triple["condition_expr"])
    if triple.get("expected_feedback"):
        parts.append("预期: %s" % triple["expected_feedback"])
    if triple.get("control_type"):
        parts.append("控件: %s" % triple["control_type"])
    return "%s%s" % (no, " | ".join(parts))
