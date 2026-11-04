# -*- coding: utf-8 -*-
"""DB 与 UI 一致性断言 — db_ui_consistency_check。

场景：UI 列表显示 10 条 vs DB 应有 10 条对应记录。
缺失后果：UI 和 DB 各自测了一遍但没关联，数据不一致漏测。

实现：
  1. 取 UI 列表项数量（page.locator(ui_selector).count()）。
  2. 取 DB COUNT(*) 数量。
  3. 按 expected_relation 比较（eq/neq/gte/lte）。
"""

import logging
from typing import Any

_log = logging.getLogger(__name__)


async def verify_db_ui_consistency(page, check: dict[str, Any]) -> dict[str, Any]:
    """DB 与 UI 一致性断言。

    check 格式：
      {"type": "db_ui_consistency_check",
       "ui_selector": "table tbody tr",            # UI 列表项选择器
       "db_table": "orders",                         # DB 表名
       "db_where": "status='active'",                # 可选 WHERE 条件
       "expected_relation": "eq"}                    # eq/neq/gte/lte
    """
    try:
        from src.pipeline.steps.step4.db_verifier import verify_db
        from src.core.db_security import validate_table_name

        ui_selector = check.get("ui_selector", "")
        db_table = check.get("db_table", "")
        db_where = check.get("db_where", "")
        relation = (check.get("expected_relation") or "eq").strip().lower()

        if not ui_selector:
            return {"passed": False, "evidence": "[DbUiCheck] 缺少 ui_selector 参数"}
        if not db_table:
            return {"passed": False, "evidence": "[DbUiCheck] 缺少 db_table 参数"}
        if not validate_table_name(db_table):
            return {"passed": False, "evidence": "[DbUiCheck] 非法表名: %s" % db_table}
        if relation not in ("eq", "neq", "gte", "lte"):
            return {
                "passed": False,
                "evidence": "[DbUiCheck] 未知 relation: %s" % relation,
            }

        # 1. 取 UI 数量
        try:
            ui_count = await page.locator(ui_selector).count()
        except Exception as e:
            return {
                "passed": False,
                "evidence": "[DbUiCheck] UI 元素计数失败: %s" % str(e)[:80],
            }

        # 2. 取 DB 数量
        sql = "SELECT COUNT(*) AS cnt FROM %s" % db_table
        if db_where:
            sql += " WHERE %s" % db_where
        result = verify_db(sql, use_ai=False)
        if not result.get("success"):
            return {
                "passed": False,
                "evidence": "[DbUiCheck] DB 查询失败: %s"
                % result.get("evidence", "")[:80],
            }
        rows = result.get("data", [])
        db_count = rows[0].get("cnt", 0) if rows else 0

        # 3. 比较
        if relation == "eq":
            passed = ui_count == db_count
        elif relation == "neq":
            passed = ui_count != db_count
        elif relation == "gte":
            passed = ui_count >= db_count
        else:  # lte
            passed = ui_count <= db_count

        return {
            "passed": passed,
            "evidence": "[DbUiCheck] UI(%s)=%d vs DB(%s%s)=%d → %s %s → %s"
            % (
                ui_selector,
                ui_count,
                db_table,
                " WHERE %s" % db_where if db_where else "",
                db_count,
                ui_count,
                relation,
                "PASS" if passed else "FAIL",
            ),
        }
    except Exception as e:
        _log.error("[DbUiCheck] 异常: %s", str(e)[:120], exc_info=True)
        return {
            "passed": False,
            "error": "executor_exception",
            "evidence": "[执行器异常] db_ui_consistency_check 崩溃: %s" % str(e)[:80],
        }
