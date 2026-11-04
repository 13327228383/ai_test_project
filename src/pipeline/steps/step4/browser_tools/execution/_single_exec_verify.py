# -*- coding: utf-8 -*-
"""
_execute_tool 的 verify_* 工具处理器（模块级函数）
================================================
★ 架构债清理(2026-07-25): 从 _single.py 的 _execute_tool 闭包中拆出，
  解决 _single.py 单文件超 800 行红线（§4.3）。
  所有函数签名统一为 (engine, args, hint) -> ToolExecutionResult，
  由 _single.py 的分派表通过 lambda 适配为零参调用。
  闭包语义保持不变：engine/args/hint 在单次 _execute_tool 调用内不可变。
"""

from typing import Any

from ._result import ToolExecutionResult
from ..locator import _locate_with_fallback


async def _verify_animation_complete(
    engine, args: dict, hint: str = ""
) -> ToolExecutionResult:
    try:
        from src.pipeline.steps.step4.verification._verify_animation import (
            verify_animation_complete,
        )

        check = {
            "type": "animation_complete_check",
            "selector": args.get("selector", ""),
            "animation_type": args.get("animation_type", "all"),
            "timeout_ms": args.get("timeout_ms", 5000),
        }
        r = await verify_animation_complete(engine._page, check)
        return ToolExecutionResult(r.get("passed", True), r.get("evidence", ""))
    except Exception as e:
        return ToolExecutionResult(False, "verify_animation_complete 失败: " + str(e))


async def _verify_api_call_count(
    engine, args: dict, hint: str = ""
) -> ToolExecutionResult:
    try:
        from src.pipeline.steps.step4.verification._verify_api_ext import verify_api_ext

        api_tracker = getattr(engine, "_api_tracker", None)
        check = {
            "type": "api_call_count_check",
            "url_pattern": args.get("url_pattern", ""),
            "expected_count": args.get("expected_count", 0),
            "operator": args.get("operator", "eq"),
        }
        r = await verify_api_ext(api_tracker, check, 0)
        return ToolExecutionResult(r.get("passed", True), r.get("evidence", ""))
    except Exception as e:
        return ToolExecutionResult(False, "verify_api_call_count 失败: " + str(e))


async def _verify_api_header(engine, args: dict, hint: str = "") -> ToolExecutionResult:
    try:
        from src.pipeline.steps.step4.verification._verify_header import (
            verify_api_header,
        )

        api_tracker = getattr(engine, "_api_tracker", None)
        check = {
            "type": "api_header_check",
            "url_pattern": args.get("url_pattern", ""),
            "header_name": args.get("header_name", ""),
            "expected": args.get("expected", ""),
            "operator": args.get("operator", "exists"),
        }
        r = await verify_api_header(api_tracker, check, 0)
        return ToolExecutionResult(r.get("passed", True), r.get("evidence", ""))
    except Exception as e:
        return ToolExecutionResult(False, "verify_api_header 失败: " + str(e))


async def _verify_db_field_value(
    engine, args: dict, hint: str = ""
) -> ToolExecutionResult:
    try:
        from src.pipeline.steps.step4.verification._verify_db_ext import verify_db_ext

        check = {
            "type": "db_field_value_check",
            "table": args.get("table", ""),
            "field": args.get("field", ""),
            "where": args.get("where", ""),
            "expected": args.get("expected", ""),
            "operator": args.get("operator", "eq"),
            "tolerance": args.get("tolerance", 0),
        }
        r = await verify_db_ext(check)
        return ToolExecutionResult(r.get("passed", True), r.get("evidence", ""))
    except Exception as e:
        return ToolExecutionResult(False, "verify_db_field_value 失败: " + str(e))


async def _verify_db_query(engine, args: dict, hint: str = "") -> ToolExecutionResult:
    try:
        from src.pipeline.steps.step4.verification._verify_db_ext import verify_db_ext

        check = {
            "type": "db_query_check",
            "sql": args.get("sql", ""),
            "expected": args.get("expected", ""),
            "operator": args.get("operator", "eq"),
            "tolerance": args.get("tolerance", 0),
            "result_field": args.get("result_field", ""),
        }
        r = await verify_db_ext(check)
        return ToolExecutionResult(r.get("passed", True), r.get("evidence", ""))
    except Exception as e:
        return ToolExecutionResult(False, "verify_db_query 失败: " + str(e))


async def _verify_db_record_count(
    engine, args: dict, hint: str = ""
) -> ToolExecutionResult:
    try:
        from src.pipeline.steps.step4.verification._verify_db_ext import verify_db_ext

        check = {
            "type": "db_record_count_check",
            "table": args.get("table", ""),
            "where": args.get("where", ""),
            "expected_count": args.get("expected_count", 0),
            "operator": args.get("operator", "eq"),
        }
        r = await verify_db_ext(check)
        return ToolExecutionResult(r.get("passed", True), r.get("evidence", ""))
    except Exception as e:
        return ToolExecutionResult(False, "verify_db_record_count 失败: " + str(e))


async def _verify_db_record_exists(
    engine, args: dict, hint: str = ""
) -> ToolExecutionResult:
    try:
        from src.pipeline.steps.step4.verification._verify_db_ext import verify_db_ext

        check = {
            "type": "db_record_exists_check",
            "table": args.get("table", ""),
            "where": args.get("where", ""),
        }
        r = await verify_db_ext(check)
        return ToolExecutionResult(r.get("passed", True), r.get("evidence", ""))
    except Exception as e:
        return ToolExecutionResult(False, "verify_db_record_exists 失败: " + str(e))


async def _verify_db_record_not_exists(
    engine, args: dict, hint: str = ""
) -> ToolExecutionResult:
    try:
        from src.pipeline.steps.step4.verification._verify_db_ext import verify_db_ext

        check = {
            "type": "db_record_not_exists_check",
            "table": args.get("table", ""),
            "where": args.get("where", ""),
        }
        r = await verify_db_ext(check)
        return ToolExecutionResult(r.get("passed", True), r.get("evidence", ""))
    except Exception as e:
        return ToolExecutionResult(False, "verify_db_record_not_exists 失败: " + str(e))


async def _verify_db_ui_consistency(
    engine, args: dict, hint: str = ""
) -> ToolExecutionResult:
    try:
        from src.pipeline.steps.step4.verification._verify_db_ui import (
            verify_db_ui_consistency,
        )

        check = {
            "type": "db_ui_consistency_check",
            "ui_selector": args.get("ui_selector", ""),
            "db_table": args.get("db_table", ""),
            "db_where": args.get("db_where", ""),
            "expected_relation": args.get("expected_relation", "eq"),
        }
        r = await verify_db_ui_consistency(engine._page, check)
        return ToolExecutionResult(r.get("passed", True), r.get("evidence", ""))
    except Exception as e:
        return ToolExecutionResult(False, "verify_db_ui_consistency 失败: " + str(e))


async def _verify_dialog(engine, args: dict, hint: str = "") -> ToolExecutionResult:
    try:
        from src.pipeline.steps.step4.verification._verify_dialog import verify_dialog

        check = {
            "type": "dialog_check",
            "expected_type": args.get("expected_type", ""),
            "expected_text": args.get("expected_text", ""),
        }
        r = await verify_dialog(engine, check)
        return ToolExecutionResult(r.get("passed", True), r.get("evidence", ""))
    except Exception as e:
        return ToolExecutionResult(False, "verify_dialog 失败: " + str(e))


async def _verify_download(engine, args: dict, hint: str = "") -> ToolExecutionResult:
    try:
        from src.pipeline.steps.step4.verification._verify_download import (
            verify_download,
        )

        check = {
            "type": "download_check",
            "expected_filename": args.get("expected_filename", ""),
            "expected_min_size": args.get("expected_min_size", 1),
        }
        r = await verify_download(engine, check)
        return ToolExecutionResult(r.get("passed", True), r.get("evidence", ""))
    except Exception as e:
        return ToolExecutionResult(False, "verify_download 失败: " + str(e))


async def _verify_element_attribute(
    engine, args: dict, hint: str = ""
) -> ToolExecutionResult:
    try:
        selector = args.get("selector", "")
        attribute = args.get("attribute", "")
        expected = args.get("expected", "")
        if not selector or not attribute:
            return ToolExecutionResult(
                False, "verify_element_attribute 缺少 selector/attribute"
            )
        loc = engine._page.locator(selector).first
        actual = await loc.get_attribute(attribute)
        passed = str(actual) == str(expected) if expected else actual is not None
        return ToolExecutionResult(
            passed,
            "element_attribute %s[%s] = '%s' (期望 '%s')"
            % (selector, attribute, actual, expected),
        )
    except Exception as e:
        return ToolExecutionResult(False, "verify_element_attribute 失败: " + str(e))


async def _verify_element_attribute_all(
    engine, args: dict, hint: str = ""
) -> ToolExecutionResult:
    try:
        from src.pipeline.steps.step4.verification._verify_attribute_all import (
            verify_element_attribute_all,
        )

        check = {
            "type": "element_attribute_all_check",
            "selector": args.get("selector", ""),
            "attribute": args.get("attribute", ""),
            "expected": args.get("expected", ""),
            "operator": args.get("operator", "all"),
        }
        r = await verify_element_attribute_all(engine._page, check)
        return ToolExecutionResult(r.get("passed", True), r.get("evidence", ""))
    except Exception as e:
        return ToolExecutionResult(
            False, "verify_element_attribute_all 失败: " + str(e)
        )


async def _verify_element_checked(
    engine, args: dict, hint: str = ""
) -> ToolExecutionResult:
    try:
        from src.pipeline.steps.step4.verification._verify_state import verify_state

        check = {
            "type": "element_checked_check",
            "selector": args.get("selector", ""),
            "expected": args.get("expected", True),
        }
        r = await verify_state(engine._page, check)
        return ToolExecutionResult(r.get("passed", True), r.get("evidence", ""))
    except Exception as e:
        return ToolExecutionResult(False, "verify_element_checked 失败: " + str(e))


async def _verify_element_count(
    engine, args: dict, hint: str = ""
) -> ToolExecutionResult:
    try:
        from src.pipeline.steps.step4.verification._verify_count import verify_count

        check = {
            "type": "element_count_check",
            "selector": args.get("selector", ""),
            "expected_count": args.get("expected_count", 0),
            "operator": args.get("operator", "eq"),
        }
        r = await verify_count(engine._page, check)
        return ToolExecutionResult(r.get("passed", True), r.get("evidence", ""))
    except Exception as e:
        return ToolExecutionResult(False, "verify_element_count 失败: " + str(e))


async def _verify_element_enabled(
    engine, args: dict, hint: str = ""
) -> ToolExecutionResult:
    try:
        from src.pipeline.steps.step4.verification._verify_state import verify_state

        check = {
            "type": "element_enabled_check",
            "selector": args.get("selector", ""),
            "expected": args.get("expected", True),
        }
        r = await verify_state(engine._page, check)
        return ToolExecutionResult(r.get("passed", True), r.get("evidence", ""))
    except Exception as e:
        return ToolExecutionResult(False, "verify_element_enabled 失败: " + str(e))


async def _verify_element_exists(
    engine, args: dict, hint: str = ""
) -> ToolExecutionResult:
    try:
        page = getattr(engine, "_page", None)
        if not page:
            return ToolExecutionResult(False, "页面未初始化")
        locator = await _locate_with_fallback(
            page, hint, selector=args.get("selector", ""), strict=True
        )
        if not locator:
            return ToolExecutionResult(False, "未找到元素: " + hint)
        count = await locator.count()
        if count > 0:
            return ToolExecutionResult(True, "存在元素: " + hint)
        return ToolExecutionResult(False, "不存在元素: " + hint)
    except Exception as e:
        return ToolExecutionResult(False, "元素检查失败: " + str(e))


async def _verify_element_exists_check(
    engine, args: dict, hint: str = ""
) -> ToolExecutionResult:
    try:
        selector = args.get("selector", "")
        expected = args.get("expected", True)
        if isinstance(expected, str):
            expected = expected.lower() not in ("false", "0", "no", "")
        if not selector:
            return ToolExecutionResult(
                False, "verify_element_exists_check 缺少 selector"
            )
        exists = False
        try:
            loc = engine._page.locator(selector)
            exists = await loc.count() > 0
        except Exception:
            exists = False
        passed = exists if expected else (not exists)
        return ToolExecutionResult(
            passed,
            "element_exists selector=%s → %d (期望%s)"
            % (selector, int(exists), "存在" if expected else "不存在"),
        )
    except Exception as e:
        return ToolExecutionResult(False, "verify_element_exists_check 失败: " + str(e))


async def _verify_element_not_exists(
    engine, args: dict, hint: str = ""
) -> ToolExecutionResult:
    """元素不存在断言（element_exists 的语义对偶）。

    ★ 精确判定，不走模糊回退：显式给了 selector 就表达「精确语义」，
      若再 fallback 到模糊匹配，会把「不存在」误判成「存在」（假 PASS），
      对"不存在"类断言（删除成功/弹窗消失/禁用生效）危害尤大。
    """
    try:
        selector = args.get("selector", "")
        if not selector:
            return ToolExecutionResult(
                False, "verify_element_not_exists 缺少 selector"
            )
        page = getattr(engine, "_page", None)
        if not page:
            return ToolExecutionResult(False, "页面未初始化")
        try:
            count = await page.locator(selector).count()
        except Exception:
            count = 0
        passed = count == 0
        return ToolExecutionResult(
            passed,
            "element_not_exists selector=%s → count=%d (期望 0)" % (selector, count),
        )
    except Exception as e:
        return ToolExecutionResult(
            False, "verify_element_not_exists 失败: " + str(e)[:150]
        )


async def _verify_element_text_equals(
    engine, args: dict, hint: str = ""
) -> ToolExecutionResult:
    try:
        from src.pipeline.steps.step4.verification._verify_state import verify_state

        check = {
            "type": "element_text_equals_check",
            "selector": args.get("selector", ""),
            "expected": args.get("expected", ""),
        }
        r = await verify_state(engine._page, check)
        return ToolExecutionResult(r.get("passed", True), r.get("evidence", ""))
    except Exception as e:
        return ToolExecutionResult(False, "verify_element_text_equals 失败: " + str(e))


async def _verify_element_visible(
    engine, args: dict, hint: str = ""
) -> ToolExecutionResult:
    try:
        selector = args.get("selector", "")
        expected = args.get("expected", True)
        if isinstance(expected, str):
            expected = expected.lower() not in ("false", "0", "no", "")
        if not selector:
            return ToolExecutionResult(False, "verify_element_visible 缺少 selector")
        visible = False
        try:
            loc = engine._page.locator(selector).first
            visible = await loc.is_visible()
        except Exception:
            visible = False
        passed = visible if expected else (not visible)
        return ToolExecutionResult(
            passed,
            "element_visible selector=%s → %s (期望%s)"
            % (
                selector,
                "可见" if visible else "不可见",
                "可见" if expected else "不可见",
            ),
        )
    except Exception as e:
        return ToolExecutionResult(False, "verify_element_visible 失败: " + str(e))


async def _verify_image_loaded(
    engine, args: dict, hint: str = ""
) -> ToolExecutionResult:
    try:
        from src.pipeline.steps.step4.verification._verify_image import verify_image

        check = {
            "type": "image_loaded_check",
            "selector": args.get("selector", ""),
            "expected": args.get("expected", True),
        }
        r = await verify_image(engine._page, check)
        return ToolExecutionResult(r.get("passed", True), r.get("evidence", ""))
    except Exception as e:
        return ToolExecutionResult(False, "verify_image_loaded 失败: " + str(e))


async def _verify_list_order(engine, args: dict, hint: str = "") -> ToolExecutionResult:
    try:
        from src.pipeline.steps.step4.verification._verify_list import verify_list_order

        check = {
            "type": "list_order_check",
            "selector": args.get("selector", ""),
            "value_selector": args.get("value_selector", ""),
            "data_type": args.get("data_type", "numeric"),
            "order": args.get("order", "asc"),
        }
        r = await verify_list_order(engine._page, check)
        return ToolExecutionResult(r.get("passed", True), r.get("evidence", ""))
    except Exception as e:
        return ToolExecutionResult(False, "verify_list_order 失败: " + str(e))


async def _verify_loading_complete(
    engine, args: dict, hint: str = ""
) -> ToolExecutionResult:
    try:
        from src.pipeline.steps.step4.verification._verify_loading import (
            verify_loading_complete,
        )

        check = {
            "type": "loading_complete_check",
            "loading_selector": args.get("loading_selector", ""),
            "expected_state": args.get("expected_state", "hidden"),
            "timeout_ms": args.get("timeout_ms", 10000),
            "wait_network_idle": args.get("wait_network_idle", True),
        }
        r = await verify_loading_complete(engine._page, check)
        return ToolExecutionResult(r.get("passed", True), r.get("evidence", ""))
    except Exception as e:
        return ToolExecutionResult(False, "verify_loading_complete 失败: " + str(e))


async def _verify_network(engine, args: dict, hint: str = "") -> ToolExecutionResult:
    try:
        from src.pipeline.steps.step4.verification._verify_network import verify_network

        api_tracker = getattr(engine, "_api_tracker", None)
        check = {
            "type": "network_check",
            "url_pattern": args.get("url_pattern", ""),
            "expected": args.get("expected", True),
            "expected_count": args.get("expected_count"),
            "operator": args.get("operator", "eq"),
        }
        r = await verify_network(api_tracker, check, 0)
        return ToolExecutionResult(r.get("passed", True), r.get("evidence", ""))
    except Exception as e:
        return ToolExecutionResult(False, "verify_network 失败: " + str(e))


async def _verify_no_console_error(
    engine, args: dict, hint: str = ""
) -> ToolExecutionResult:
    try:
        from src.pipeline.steps.step4.verification._verify_console import (
            verify_no_console_error,
        )

        check = {"type": "console_error_check", "expected": args.get("expected", True)}
        r = await verify_no_console_error(engine, check)
        return ToolExecutionResult(r.get("passed", True), r.get("evidence", ""))
    except Exception as e:
        return ToolExecutionResult(False, "verify_no_console_error 失败: " + str(e))


async def _verify_no_error_text(
    engine, args: dict, hint: str = ""
) -> ToolExecutionResult:
    try:
        page = getattr(engine, "_page", None)
        if not page:
            return ToolExecutionResult(False, "页面未初始化")
        body_text = await page.evaluate("() => document.body.innerText || ''")
        not_expected = args.get("not_expected", "")
        if not_expected:
            if not_expected in body_text:
                return ToolExecutionResult(False, "页面包含错误文字: " + not_expected)
            return ToolExecutionResult(True, "页面不包含: " + not_expected)
        error_keywords = ("error", "Error", "失败", "错误", "alert", "warning")
        found = [k for k in error_keywords if k in body_text]
        if found:
            return ToolExecutionResult(False, "页面包含错误关键词: " + ", ".join(found))
        return ToolExecutionResult(True, "页面不包含常见错误提示")
    except Exception as e:
        return ToolExecutionResult(False, "错误检查失败: " + str(e))


async def _verify_page_load_time(
    engine, args: dict, hint: str = ""
) -> ToolExecutionResult:
    try:
        from src.pipeline.steps.step4.verification._verify_page_load import (
            verify_page_load_time,
        )

        check = {
            "type": "page_load_time_check",
            "metric": args.get("metric", "load"),
            "max_ms": args.get("max_ms", 5000),
        }
        r = await verify_page_load_time(engine._page, check)
        return ToolExecutionResult(r.get("passed", True), r.get("evidence", ""))
    except Exception as e:
        return ToolExecutionResult(False, "verify_page_load_time 失败: " + str(e))


async def _verify_pagination(engine, args: dict, hint: str = "") -> ToolExecutionResult:
    try:
        from src.pipeline.steps.step4.verification._verify_pagination import (
            verify_pagination,
        )

        check = {
            "type": "pagination_check",
            "current_page_selector": args.get("current_page_selector", ""),
            "items_selector": args.get("items_selector", ""),
            "expected_page": args.get("expected_page"),
            "expected_items_per_page": args.get("expected_items_per_page"),
        }
        r = await verify_pagination(engine._page, check)
        return ToolExecutionResult(r.get("passed", True), r.get("evidence", ""))
    except Exception as e:
        return ToolExecutionResult(False, "verify_pagination 失败: " + str(e))


async def _verify_position(engine, args: dict, hint: str = "") -> ToolExecutionResult:
    try:
        from src.pipeline.steps.step4.verification._verify_position import (
            verify_position,
        )

        check = {
            "type": args.get("check_type", ""),
            "selector": args.get("selector", ""),
            "tolerance": args.get("tolerance", 0),
        }
        for k in ("x", "y", "width", "height"):
            if k in args:
                check[k] = args[k]
        r = await verify_position(engine._page, check)
        return ToolExecutionResult(r.get("passed", True), r.get("evidence", ""))
    except Exception as e:
        return ToolExecutionResult(False, "verify_position 失败: " + str(e))


async def _verify_refresh(engine, args: dict, hint: str = "") -> ToolExecutionResult:
    try:
        from src.pipeline.steps.step4.verification._verify_refresh import verify_refresh

        check = {
            "type": "page_refresh_check",
            "expected": args.get("expected", "refreshed"),
        }
        r = await verify_refresh(engine._page, check)
        return ToolExecutionResult(r.get("passed", True), r.get("evidence", ""))
    except Exception as e:
        return ToolExecutionResult(False, "verify_refresh 失败: " + str(e))


async def _verify_request_body(
    engine, args: dict, hint: str = ""
) -> ToolExecutionResult:
    try:
        from src.pipeline.steps.step4.verification._verify_api_ext import verify_api_ext

        api_tracker = getattr(engine, "_api_tracker", None)
        check = {
            "type": "request_body_check",
            "url_pattern": args.get("url_pattern", ""),
            "expected_field": args.get("expected_field", ""),
            "expected_value": args.get("expected_value", ""),
        }
        r = await verify_api_ext(api_tracker, check, 0)
        return ToolExecutionResult(r.get("passed", True), r.get("evidence", ""))
    except Exception as e:
        return ToolExecutionResult(False, "verify_request_body 失败: " + str(e))


async def _verify_response_body(
    engine, args: dict, hint: str = ""
) -> ToolExecutionResult:
    try:
        from src.pipeline.steps.step4.verification._verify_api_ext import verify_api_ext

        api_tracker = getattr(engine, "_api_tracker", None)
        check = {
            "type": "api_response_body_check",
            "url_pattern": args.get("url_pattern", ""),
            "expected_field": args.get("expected_field", ""),
            "expected_value": args.get("expected_value", ""),
        }
        r = await verify_api_ext(api_tracker, check, 0)
        return ToolExecutionResult(r.get("passed", True), r.get("evidence", ""))
    except Exception as e:
        return ToolExecutionResult(False, "verify_response_body 失败: " + str(e))


async def _verify_response_time(
    engine, args: dict, hint: str = ""
) -> ToolExecutionResult:
    try:
        from src.pipeline.steps.step4.verification._verify_perf import (
            verify_performance,
        )

        api_tracker = getattr(engine, "_api_tracker", None)
        check = {
            "type": "response_time_check",
            "url_pattern": args.get("url_pattern", ""),
            "max_ms": args.get("max_ms", 5000),
        }
        r = await verify_performance(api_tracker, check)
        return ToolExecutionResult(r.get("passed", True), r.get("evidence", ""))
    except Exception as e:
        return ToolExecutionResult(False, "verify_response_time 失败: " + str(e))


async def _verify_select_value(
    engine, args: dict, hint: str = ""
) -> ToolExecutionResult:
    try:
        from src.pipeline.steps.step4.verification._verify_state import verify_state

        check = {
            "type": "select_value_check",
            "selector": args.get("selector", ""),
            "expected": args.get("expected", ""),
        }
        r = await verify_state(engine._page, check)
        return ToolExecutionResult(r.get("passed", True), r.get("evidence", ""))
    except Exception as e:
        return ToolExecutionResult(False, "verify_select_value 失败: " + str(e))


async def _verify_storage(engine, args: dict, hint: str = "") -> ToolExecutionResult:
    try:
        from src.pipeline.steps.step4.verification._verify_storage import verify_storage

        check = {
            "type": "storage_check",
            "storage_type": args.get("storage_type", "localStorage"),
            "key": args.get("key", ""),
            "expected": args.get("expected", ""),
            "operator": args.get("operator", "exists"),
        }
        r = await verify_storage(engine._page, check)
        return ToolExecutionResult(r.get("passed", True), r.get("evidence", ""))
    except Exception as e:
        return ToolExecutionResult(False, "verify_storage 失败: " + str(e))


async def _verify_style(engine, args: dict, hint: str = "") -> ToolExecutionResult:
    try:
        from src.pipeline.steps.step4.verification._verify_style import verify_style

        check = {
            "type": args.get("check_type", ""),
            "selector": args.get("selector", ""),
            "expected": args.get("expected", ""),
            "property": args.get("property", ""),
            "attribute": args.get("attribute", ""),
            "class_name": args.get("class_name", ""),
        }
        r = await verify_style(engine._page, check)
        return ToolExecutionResult(r.get("passed", True), r.get("evidence", ""))
    except Exception as e:
        return ToolExecutionResult(False, "verify_style 失败: " + str(e))


async def _verify_text_contains(
    engine, args: dict, hint: str = ""
) -> ToolExecutionResult:
    try:
        from src.pipeline.steps.step4.verification._verify_text_contains import (
            verify_text_contains,
        )

        check = {
            "type": "text_contains_check",
            "selector": args.get("selector", ""),
            "expected": args.get("expected", ""),
            "operator": args.get("operator", "contains"),
        }
        r = await verify_text_contains(engine._page, check)
        return ToolExecutionResult(r.get("passed", True), r.get("evidence", ""))
    except Exception as e:
        return ToolExecutionResult(False, "verify_text_contains 失败: " + str(e))


async def _verify_text_in_page(
    engine, args: dict, hint: str = ""
) -> ToolExecutionResult:
    try:
        page = getattr(engine, "_page", None)
        if not page:
            return ToolExecutionResult(False, "页面未初始化")
        body_text = await page.evaluate("() => document.body.innerText || ''")
        expected = args.get("expect_text", "")
        if expected and expected in body_text:
            return ToolExecutionResult(True, "页面包含文字: " + expected)
        return ToolExecutionResult(False, "页面不包含文字: " + expected)
    except Exception as e:
        return ToolExecutionResult(False, "文本检查失败: " + str(e))


async def _verify_text_regex(engine, args: dict, hint: str = "") -> ToolExecutionResult:
    try:
        from src.pipeline.steps.step4.verification._verify_regex import (
            verify_text_regex,
        )

        check = {
            "type": "text_regex_check",
            "selector": args.get("selector", ""),
            "pattern": args.get("pattern", ""),
            "expected": args.get("expected", True),
        }
        r = await verify_text_regex(engine._page, check)
        return ToolExecutionResult(r.get("passed", True), r.get("evidence", ""))
    except Exception as e:
        return ToolExecutionResult(False, "verify_text_regex 失败: " + str(e))


async def _verify_time_range(engine, args: dict, hint: str = "") -> ToolExecutionResult:
    try:
        from src.pipeline.steps.step4.verification._verify_time import verify_time_range

        check = {
            "type": "time_range_check",
            "value_source": args.get("value_source", "element"),
            "selector": args.get("selector", ""),
            "db_table": args.get("db_table", ""),
            "db_field": args.get("db_field", ""),
            "db_where": args.get("db_where", ""),
            "start_time": args.get("start_time", ""),
            "end_time": args.get("end_time", ""),
        }
        r = await verify_time_range(engine._page, check)
        return ToolExecutionResult(r.get("passed", True), r.get("evidence", ""))
    except Exception as e:
        return ToolExecutionResult(False, "verify_time_range 失败: " + str(e))


async def _verify_toast(engine, args: dict, hint: str = "") -> ToolExecutionResult:
    try:
        from src.pipeline.steps.step4.verification._verify_toast import verify_toast

        check = {
            "type": args.get("check_type", ""),
            "text": args.get("text", ""),
            "expected": args.get("expected", "hidden"),
            "timeout_ms": args.get("timeout_ms", 3000),
        }
        r = await verify_toast(engine._page, check)
        return ToolExecutionResult(r.get("passed", True), r.get("evidence", ""))
    except Exception as e:
        return ToolExecutionResult(False, "verify_toast 失败: " + str(e))


async def _verify_url(engine, args: dict, hint: str = "") -> ToolExecutionResult:
    try:
        from src.pipeline.steps.step4.verification._verify_url import verify_url

        check = {
            "type": "url_check",
            "expected": args.get("expected", ""),
            "operator": args.get("operator", "contains"),
        }
        r = verify_url(engine._page, check)
        return ToolExecutionResult(r.get("passed", True), r.get("evidence", ""))
    except Exception as e:
        return ToolExecutionResult(False, "verify_url 失败: " + str(e))


async def _verify_websocket(engine, args: dict, hint: str = "") -> ToolExecutionResult:
    try:
        from src.pipeline.steps.step4.verification._verify_websocket import (
            verify_websocket,
        )

        check = {
            "type": "websocket_check",
            "expected_message": args.get("expected_message", ""),
            "operator": args.get("operator", "contains"),
        }
        r = verify_websocket(engine, check)
        return ToolExecutionResult(r.get("passed", True), r.get("evidence", ""))
    except Exception as e:
        return ToolExecutionResult(False, "verify_websocket 失败: " + str(e))
