# -*- coding: utf-8 -*-
"""用例级前置数据保障（执行端）—— 只读上游声明的 `data_precondition`。

why: 断言对象是"列表里已有一条记录"的用例（删除/编辑行）在前序用例清空数据后必然
  落空。此时"未命中"既不是产品缺陷（不记 FAIL），也不是该用例的期望（不记 PASS），
  只能先用 SUT 自身的"新建"入口造一行。声明与执行分离：Step3 在
  `_check_guard.complete_submit_flow` 一次性落 `data_precondition`，本模块只读字段并在
  数据不足时补行；补不出则由 `_base` 记 SKIP(ENGINE_COVERAGE_GAP)。
通用性: 入口/提交控件取 `_trigger_actions` 的通用候选词，表单补全复用 `form_completion`，
  行数读取复用 `read_list_size` —— 本模块不持有任何业务词表与框架选择器。
"""

from __future__ import annotations

import logging

from src.pipeline.step3._trigger_actions import (
    DATA_PRECONDITION_EXISTING_ROW,
    ENTRY_HINT_CREATE,
    SUBMIT_HINT,
)

_log = logging.getLogger(__name__)

_ENTRY_TIMEOUT_MS = 5000
_SUBMIT_SELECTOR = 'button[type="submit"], input[type="submit"]'


async def ensure_existing_row(engine) -> str:
    """确保当前列表至少有一行；返回空串 = 已满足/已补足，否则返回缺口原因。"""
    page = getattr(engine, "_page", None)
    if page is None:
        return "无可用页面"
    from src.pipeline.steps.step4.verification._db_ui import read_list_size

    try:
        _n, _src = await read_list_size(page)
    except Exception as _e:  # noqa: BLE001 —— 读取不到就当数据不足（诚实 SKIP）
        return "列表规模读取失败: %s" % str(_e)[:80]
    if int(_n or 0) > 0:
        return ""

    _before_url = ""
    try:
        _before_url = str(page.url or "")
    except Exception:  # noqa: BLE001
        _before_url = ""

    from src.pipeline.steps.step4.browser_tools.locator import _locate_by_hint

    try:
        _entry = await _locate_by_hint(page, ENTRY_HINT_CREATE)
    except Exception as _e:  # noqa: BLE001
        return "新建入口定位失败: %s" % str(_e)[:80]
    if _entry is None:
        return "未找到新建入口（无法造数）"
    try:
        await _entry.click(timeout=_ENTRY_TIMEOUT_MS)
    except Exception as _e:  # noqa: BLE001
        return "新建入口点击失败: %s" % str(_e)[:80]

    from src.pipeline.steps.step4.action_hooks import settle_page_after_action

    await settle_page_after_action(engine, "click", _before_url)

    from src.pipeline.steps.step4.form_completion import (
        complete_form_fields,
        resolve_submittable_form_scope,
    )

    _scope = await resolve_submittable_form_scope(page)
    if _scope is None:
        return "新建表单未打开（无法造数）"
    _filled = await complete_form_fields(page, set(), _ENTRY_TIMEOUT_MS, [], [])
    if not _filled:
        return "新建表单无可填字段（无法造数）"

    _submit = None
    try:
        _loc = _scope.locator(_SUBMIT_SELECTOR)
        if await _loc.count() > 0:
            _submit = _loc.first
    except Exception:  # noqa: BLE001
        _submit = None
    if _submit is None:
        try:
            _submit = await _locate_by_hint(page, SUBMIT_HINT)
        except Exception as _e:  # noqa: BLE001
            return "提交控件定位失败: %s" % str(_e)[:80]
    if _submit is None:
        return "新建表单未找到提交控件（无法造数）"
    try:
        await _submit.click(timeout=_ENTRY_TIMEOUT_MS)
    except Exception as _e:  # noqa: BLE001
        return "新建提交失败: %s" % str(_e)[:80]
    await settle_page_after_action(engine, "click", _before_url)

    # 提交若跳到详情/表单页，回到造数前的列表页再核对行数
    try:
        if _before_url and str(page.url or "") != _before_url:
            await page.goto(_before_url)
            await settle_page_after_action(engine, "navigate")
    except Exception as _e:  # noqa: BLE001 —— 回退失败不阻断（随后按行数诚实判定）
        _log.debug("[DataPrecondition] 返回列表页失败: %s", str(_e)[:80])

    try:
        _n2, _src2 = await read_list_size(page)
    except Exception as _e:  # noqa: BLE001
        return "造数后列表规模读取失败: %s" % str(_e)[:80]
    if int(_n2 or 0) > 0:
        _log.info("[DataPrecondition] 已造出前置数据行: %s 行(来源=%s)", _n2, _src2)
        return ""
    return "造数后列表仍无数据行（造数前来源=%s）" % _src


__all__ = ["DATA_PRECONDITION_EXISTING_ROW", "ensure_existing_row"]
