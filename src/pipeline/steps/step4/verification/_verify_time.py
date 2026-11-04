# -*- coding: utf-8 -*-
from __future__ import annotations

"""时间范围断言 — time_range_check。

场景：created_at 应在 [2026-01-01, 2026-12-31]；订单时间应在当前小时内。
缺失后果：时间边界业务规则漏测（db_field_value_check 的 range 不支持时间类型）。

实现：
  1. 取时间值（元素文本或 DB 字段）。
  2. 尝试多种格式解析为 datetime。
  3. 检查是否在 [start_time, end_time] 范围内。
"""

import logging
from datetime import datetime
from typing import Any

_log = logging.getLogger(__name__)

# 支持的时间格式
_TIME_FORMATS = [
    "%Y-%m-%d %H:%M:%S",
    "%Y-%m-%dT%H:%M:%S",
    "%Y-%m-%d %H:%M",
    "%Y-%m-%d",
    "%Y/%m/%d %H:%M:%S",
    "%Y/%m/%d",
    "%d/%m/%Y %H:%M:%S",
    "%d/%m/%Y",
]


def _parse_time(s: str) -> datetime | None:
    """尝试多种格式解析时间字符串。"""
    s = (s or "").strip()
    if not s:
        return None
    # 先试 ISO
    try:
        return datetime.fromisoformat(s.replace("Z", ""))
    except (ValueError, TypeError) as e:
        _log.debug("ISO时间解析失败: %s", e)
    for fmt in _TIME_FORMATS:
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            continue
    return None


async def verify_time_range(page, check: dict[str, Any]) -> dict[str, Any]:
    """时间范围断言。

    check 格式：
      {"type": "time_range_check",
       "value_source": "element",                     # element / db
       "selector": ".created-at",                     # element 时用
       "db_table": "orders", "db_field": "created_at", "db_where": "id=1",  # db 时用
       "start_time": "2026-01-01 00:00:00",
       "end_time": "2026-12-31 23:59:59"}
    """
    try:
        value_source = (check.get("value_source") or "element").strip().lower()
        start_time = check.get("start_time", "")
        end_time = check.get("end_time", "")

        if not start_time or not end_time:
            return {
                "passed": False,
                "evidence": "[TimeCheck] 缺少 start_time/end_time 参数",
            }

        start_dt = _parse_time(start_time)
        end_dt = _parse_time(end_time)
        if not start_dt or not end_dt:
            return {
                "passed": False,
                "evidence": "[TimeCheck] 时间格式无法解析: start='%s' end='%s'"
                % (start_time, end_time),
            }

        # 取时间值
        raw_value = ""
        if value_source == "db":
            from src.pipeline.steps.step4.db_verifier import verify_db
            from src.core.db_security import validate_table_name, validate_column_name

            table = check.get("db_table", "")
            field = check.get("db_field", "")
            where = check.get("db_where", "")
            if not table or not field or not where:
                return {
                    "passed": False,
                    "evidence": "[TimeCheck] db 模式缺少 db_table/db_field/db_where",
                }
            if not validate_table_name(table) or not validate_column_name(field):
                return {"passed": False, "evidence": "[TimeCheck] 非法表名/字段名"}
            sql = "SELECT %s AS tval FROM %s WHERE %s LIMIT 1" % (field, table, where)
            result = verify_db(sql, use_ai=False)
            if not result.get("success"):
                return {
                    "passed": False,
                    "evidence": "[TimeCheck] DB 查询失败: %s"
                    % result.get("evidence", "")[:80],
                }
            rows = result.get("data", [])
            if not rows:
                return {"passed": False, "evidence": "[TimeCheck] DB 无匹配记录"}
            raw_value = str(rows[0].get("tval", ""))
        else:
            selector = check.get("selector", "")
            if not selector:
                return {
                    "passed": False,
                    "evidence": "[TimeCheck] element 模式缺少 selector",
                }
            try:
                raw_value = await page.locator(selector).first.inner_text(timeout=3000)
            except Exception:
                raw_value = (
                    await page.locator(selector).first.text_content(timeout=3000) or ""
                )
            raw_value = (raw_value or "").strip()

        actual_dt = _parse_time(raw_value)
        if not actual_dt:
            return {
                "passed": False,
                "evidence": "[TimeCheck] 时间值无法解析: '%s'" % raw_value[:30],
            }

        passed = start_dt <= actual_dt <= end_dt
        return {
            "passed": passed,
            "evidence": "[TimeCheck] value='%s' → %s | 范围 [%s, %s] → %s"
            % (
                raw_value[:30],
                actual_dt.strftime("%Y-%m-%d %H:%M:%S"),
                start_dt.strftime("%Y-%m-%d %H:%M:%S"),
                end_dt.strftime("%Y-%m-%d %H:%M:%S"),
                "PASS" if passed else "FAIL",
            ),
        }
    except Exception as e:
        _log.error("[TimeCheck] 异常: %s", str(e)[:120], exc_info=True)
        return {
            "passed": False,
            "error": "executor_exception",
            "evidence": "[执行器异常] time_range_check 崩溃: %s" % str(e)[:80],
        }
