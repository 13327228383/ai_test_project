# -*- coding: utf-8 -*-
"""列表排序顺序断言 — list_order_check。

场景：按价格降序排序后，列表应严格递减；按创建时间升序，应严格递增。
缺失后果：排序错误漏测，N 次 element_text_equals 不可靠。

实现：
  1. 取所有匹配 selector 的列表项。
  2. 对每项取 value_selector 子元素的文本（或该项自身文本）。
  3. 按 data_type 转换为数值或字符串。
  4. 检查是否满足 order（asc 升序 / desc 降序），允许相邻相等（非严格单调）。
"""

import logging
from typing import Any

_log = logging.getLogger(__name__)


async def verify_list_order(page, check: dict[str, Any]) -> dict[str, Any]:
    """列表排序顺序断言。

    check 格式：
      {"type": "list_order_check",
       "selector": "table tbody tr",                 # 列表项选择器
       "value_selector": "td:nth-child(3)",          # 可选，取哪一列的文本
       "data_type": "numeric",                        # numeric / string
       "order": "desc"}                                # asc / desc
    """
    try:
        selector = check.get("selector", "")
        value_selector = check.get("value_selector", "")
        data_type = (check.get("data_type") or "numeric").strip().lower()
        order = (check.get("order") or "asc").strip().lower()

        if not selector:
            return {"passed": False, "evidence": "[ListOrder] 缺少 selector 参数"}
        if data_type not in ("numeric", "string"):
            return {
                "passed": False,
                "evidence": "[ListOrder] 未知 data_type: %s" % data_type,
            }
        if order not in ("asc", "desc"):
            return {"passed": False, "evidence": "[ListOrder] 未知 order: %s" % order}

        # 取列表项数量
        items = page.locator(selector)
        count = await items.count()
        if count < 2:
            return {
                "passed": True,
                "evidence": "[ListOrder] 列表项 < 2，无需排序检查 (%d 项)" % count,
            }

        # 取每项的值
        values: list[Any] = []
        for i in range(count):
            if value_selector:
                # 子元素取值
                cell = items.nth(i).locator(value_selector).first
                try:
                    raw = await cell.inner_text(timeout=3000)
                except Exception:
                    raw = await cell.text_content(timeout=3000) or ""
            else:
                # 列表项自身文本
                try:
                    raw = await items.nth(i).inner_text(timeout=3000)
                except Exception:
                    raw = await items.nth(i).text_content(timeout=3000) or ""
            raw = (raw or "").strip()

            if data_type == "numeric":
                try:
                    # 去除千分位逗号、货币符号
                    cleaned = (
                        raw.replace(",", "").replace("¥", "").replace("$", "").strip()
                    )
                    values.append(float(cleaned))
                except ValueError:
                    return {
                        "passed": False,
                        "evidence": "[ListOrder] 第 %d 项无法转为数值: '%s'"
                        % (i, raw[:30]),
                    }
            else:
                values.append(raw)

        # 检查单调
        if order == "asc":
            violations = [
                (i, values[i - 1], values[i])
                for i in range(1, len(values))
                if values[i - 1] > values[i]
            ]
        else:  # desc
            violations = [
                (i, values[i - 1], values[i])
                for i in range(1, len(values))
                if values[i - 1] < values[i]
            ]

        passed = len(violations) == 0
        viol_detail = "; ".join(["#%d %s>%s" % (i, a, b) for i, a, b in violations[:3]])

        return {
            "passed": passed,
            "evidence": "[ListOrder] %s × %s order=%s → %s | %s%s"
            % (
                selector,
                len(values),
                order,
                "PASS" if passed else "FAIL",
                (
                    "无违反"
                    if passed
                    else "违反 %d 处: %s" % (len(violations), viol_detail)
                ),
                "" if passed else ("..." if len(violations) > 3 else ""),
            ),
        }
    except Exception as e:
        _log.error("[ListOrder] 异常: %s", str(e)[:120], exc_info=True)
        return {
            "passed": False,
            "error": "executor_exception",
            "evidence": "[执行器异常] list_order_check 崩溃: %s" % str(e)[:80],
        }
