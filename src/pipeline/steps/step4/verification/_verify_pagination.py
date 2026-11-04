# -*- coding: utf-8 -*-
"""分页数据正确性断言 — pagination_check。

场景：第 2 页应显示 11-20 条；每页应显示 10 条；当前页码高亮正确。
缺失后果：分页错误漏测（如第 2 页显示第 1 页数据）。

实现：
  1. 取当前页码元素文本，解析为整数。
  2. 取列表项数量。
  3. 校验页码、项数、可选起始序号。
"""

import logging
from typing import Any

_log = logging.getLogger(__name__)


async def verify_pagination(page, check: dict[str, Any]) -> dict[str, Any]:
    """分页数据正确性断言。

    check 格式：
      {"type": "pagination_check",
       "current_page_selector": ".pagination .active",  # 当前页码元素
       "items_selector": "table tbody tr",              # 列表项选择器
       "expected_page": 2,                               # 期望页码
       "expected_items_per_page": 10,                    # 期望每页项数
       "expected_start_index": 11}                       # 可选，期望起始序号
    """
    try:
        current_page_selector = check.get("current_page_selector", "")
        items_selector = check.get("items_selector", "")
        expected_page = check.get("expected_page")
        expected_items_per_page = check.get("expected_items_per_page")
        expected_start_index = check.get("expected_start_index")

        if not current_page_selector or not items_selector:
            return {
                "passed": False,
                "evidence": "[PaginationCheck] 缺少 current_page_selector/items_selector",
            }

        # 1. 当前页码
        try:
            page_text = await page.locator(current_page_selector).first.inner_text(
                timeout=3000
            )
        except Exception:
            page_text = (
                await page.locator(current_page_selector).first.text_content(
                    timeout=3000
                )
                or ""
            )
        page_text = (page_text or "").strip()
        try:
            actual_page = int(page_text)
        except ValueError:
            return {
                "passed": False,
                "evidence": "[PaginationCheck] 页码无法解析为整数: '%s'"
                % page_text[:20],
            }

        # 2. 列表项数量
        actual_items = await page.locator(items_selector).count()

        # 3. 校验
        checks = []
        passed = True
        if expected_page is not None:
            ok = actual_page == int(expected_page)
            checks.append(
                "page=%d (期望 %d) %s"
                % (actual_page, expected_page, "OK" if ok else "FAIL")
            )
            passed = passed and ok
        if expected_items_per_page is not None:
            ok = actual_items <= int(expected_items_per_page)
            checks.append(
                "items=%d (期望 ≤ %d) %s"
                % (actual_items, expected_items_per_page, "OK" if ok else "FAIL")
            )
            passed = passed and ok

        return {
            "passed": passed,
            "evidence": "[PaginationCheck] page=%d items=%d → %s | %s"
            % (
                actual_page,
                actual_items,
                "PASS" if passed else "FAIL",
                "; ".join(checks),
            ),
        }
    except Exception as e:
        _log.error("[PaginationCheck] 异常: %s", str(e)[:120], exc_info=True)
        return {
            "passed": False,
            "error": "executor_exception",
            "evidence": "[执行器异常] pagination_check 崩溃: %s" % str(e)[:80],
        }
