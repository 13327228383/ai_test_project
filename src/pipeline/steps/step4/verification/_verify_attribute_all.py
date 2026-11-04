# -*- coding: utf-8 -*-
"""多元素属性一致性断言 — element_attribute_all_check。

场景：列表所有行的"删除"按钮都应 disabled；所有图片都应加载完成。
缺失后果：需 N 次 element_attribute_check，且无法保证"全部一致"。

实现：
  1. 取所有匹配 selector 的元素。
  2. 对每个元素取 attribute 的值。
  3. operator=all → 所有都应等于 expected；none → 所有都不等于；any → 至少一个等于。
"""

import logging
from typing import Any

_log = logging.getLogger(__name__)


async def verify_element_attribute_all(page, check: dict[str, Any]) -> dict[str, Any]:
    """多元素属性一致性断言。

    check 格式：
      {"type": "element_attribute_all_check",
       "selector": "button.delete",                  # 元素选择器（匹配多个）
       "attribute": "disabled",                        # 属性名
       "expected": "true",                              # 期望值
       "operator": "all"}                               # all/none/any
    """
    try:
        selector = check.get("selector", "")
        attribute = check.get("attribute", "")
        expected = check.get("expected", "")
        operator = (check.get("operator") or "all").strip().lower()

        if not selector or not attribute:
            return {
                "passed": False,
                "evidence": "[AttrAllCheck] 缺少 selector/attribute 参数",
            }
        if operator not in ("all", "none", "any"):
            return {
                "passed": False,
                "evidence": "[AttrAllCheck] 未知 operator: %s" % operator,
            }

        # 取所有元素
        items = page.locator(selector)
        count = await items.count()
        if count == 0:
            return {
                "passed": False,
                "evidence": "[AttrAllCheck] 无匹配元素: %s" % selector,
            }

        # 取每个元素的属性值
        values: list[str] = []
        for i in range(count):
            try:
                val = await items.nth(i).get_attribute(attribute)
                values.append(str(val) if val is not None else "NULL")
            except Exception as e:
                return {
                    "passed": False,
                    "evidence": "[AttrAllCheck] 取第 %d 个元素属性失败: %s"
                    % (i, str(e)[:60]),
                }

        # 校验
        str_expected = str(expected)
        if operator == "all":
            passed = all(v == str_expected for v in values)
            detail = "全部 %d 个应等于 '%s'，实际匹配 %d 个" % (
                count,
                str_expected,
                sum(1 for v in values if v == str_expected),
            )
        elif operator == "none":
            passed = all(v != str_expected for v in values)
            detail = "全部 %d 个应不等于 '%s'，实际不匹配 %d 个" % (
                count,
                str_expected,
                sum(1 for v in values if v != str_expected),
            )
        else:  # any
            passed = any(v == str_expected for v in values)
            detail = "至少 1 个应等于 '%s'，实际匹配 %d 个" % (
                str_expected,
                sum(1 for v in values if v == str_expected),
            )

        return {
            "passed": passed,
            "evidence": "[AttrAllCheck] %s[%s] × %d operator=%s → %s | %s"
            % (
                selector,
                attribute,
                count,
                operator,
                "PASS" if passed else "FAIL",
                detail,
            ),
        }
    except Exception as e:
        _log.error("[AttrAllCheck] 异常: %s", str(e)[:120], exc_info=True)
        return {
            "passed": False,
            "error": "executor_exception",
            "evidence": "[执行器异常] element_attribute_all_check 崩溃: %s"
            % str(e)[:80],
        }
