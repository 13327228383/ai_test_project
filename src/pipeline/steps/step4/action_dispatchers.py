# -*- coding: utf-8 -*-
from __future__ import annotations
from src.core.guarded import guard

"""
Action Dispatcher — 操作指令库查表执行器
==========================================
职责：
- 根据 test_case.actions[].type 到 action_library_store 查表。
- 对库中已注册且映射到 browser_tools 的 action，直接调用工具执行。
- 未命中或执行失败时返回 (handled=False, ...)，由上层硬编码分支或 AI 兜底。

设计约束：
- 不重复实现 Playwright 原子操作，优先复用 browser_tools._execute_tool。
- action → tool 的映射从 action_actions.tool_name 读取（DB 是唯一事实源）。
- 新增 action 只需：① 在 action_actions 中写入记录（含 tool_name）；② 对应 tool 在 ALL_TOOLS 中已注册。
"""

import logging
from typing import Any

from src.pipeline.steps.step4.browser_tools.execution import _execute_tool
from src.pipeline.steps.step4.browser_tools._tool_definitions import ALL_TOOLS
from src.pipeline.utils.action_library_store import action_library_store
from src.extensions.metrics_collector import record_metric as _record_metric

_log = logging.getLogger(__name__)


# ----------------------------------------------------------------------------
# action → tool 映射状态常量（dispatch 三态语义，system_design Part A §4.1）
# ----------------------------------------------------------------------------
ACTION_TOOL_VALID = "VALID"  # tool_name 有效且在 ALL_TOOLS 中 → 可执行
ACTION_TOOL_EMPTY = "EMPTY"  # tool_name 为空且非 workflow → elif 兜底
ACTION_TOOL_INVALID = "INVALID"  # tool_name 非空但不在 ALL_TOOLS → 配置错误，记 warning
ACTION_TOOL_WORKFLOW = "WORKFLOW"  # is_workflow=1 且 tool_name 空 → 工作流专用通道


# ----------------------------------------------------------------------------
# action_library id → browser_tools tool_name 动态映射（DB 是唯一事实源）
# ----------------------------------------------------------------------------
def _build_action_to_tool_map() -> dict[str, tuple[str, str]]:
    """
    从 action_actions 读取 action_id → (tool_name, status) 映射。

    ★ 归一化（2026-08-04）：不再「静默剔除」tool_name 空/无效行，而是显式返回状态：
      - VALID   — tool_name 在 ALL_TOOLS 中（可执行）
      - EMPTY   — tool_name 为空且非 workflow（上层走 elif 兜底）
      - INVALID — tool_name 非空但不在 ALL_TOOLS（配置错误，记 warning + 指标）
      - WORKFLOW— is_workflow=1（工作流专用，返回 handled=False + WORKFLOW_ONLY）
    """
    valid_tools = {t.get("function", {}).get("name", "") for t in ALL_TOOLS}
    status_map = action_library_store.load_action_tool_status_map()
    result: dict[str, tuple[str, str]] = {}
    for aid, info in status_map.items():
        tn = str(info.get("tool_name", "") or "").strip()
        is_wf = bool(info.get("is_workflow"))
        if not tn:
            status = ACTION_TOOL_WORKFLOW if is_wf else ACTION_TOOL_EMPTY
        elif tn in valid_tools:
            status = ACTION_TOOL_VALID
        else:
            status = ACTION_TOOL_INVALID
            _log.warning(
                "[dispatcher] action '%s' tool_name='%s' 不在 ALL_TOOLS（INVALID）",
                aid,
                tn,
            )
        result[aid] = (tn, status)
    return result


_action_to_tool_map_cache: dict[str, tuple[str, str]] | None = None


def _get_action_to_tool_map() -> dict[str, tuple[str, str]]:
    """惰性加载 action_id → (tool_name, status) 映射（首次调用时从 DB 读取并缓存）。"""
    global _action_to_tool_map_cache
    if _action_to_tool_map_cache is None:
        _action_to_tool_map_cache = _build_action_to_tool_map()
    return _action_to_tool_map_cache


def invalidate_cache() -> None:
    """★ 缓存失效（2026-08-04）：action_library_store 写操作后调用，避免脏缓存。

    由 action_library_store/write.py 的写操作（save_library/upsert_action/
    delete_action/reset_to_default）广播触发。
    """
    global _action_to_tool_map_cache
    _action_to_tool_map_cache = None
    _log.info("[ActionDispatcher] action→tool 映射缓存已失效（下次访问重新从 DB 加载）")


# ----------------------------------------------------------------------------
# 参数转换：将 Step3 生成的 action 字典转为 tool 所需 args
# ----------------------------------------------------------------------------
def _convert_action_to_tool_args(action: dict[str, Any]) -> dict[str, Any]:
    """把 action 字典统一转换成 browser_tools 所需参数。"""
    args: dict[str, Any] = {}
    atype = action.get("type", "") or action.get("action", "")

    # 元素定位
    if action.get("target_hint"):
        args["target_hint"] = action["target_hint"]
    if action.get("source_hint"):
        args["source_hint"] = action["source_hint"]
    # ★ Phase 1 (2026-07-11): 预解析选择器 — 页面探索器缓存解析的 CSS selector
    if action.get("_resolved_selector"):
        args["selector"] = action["_resolved_selector"]
    elif action.get("selector"):
        # 调用方显式给出的 CSS 选择器（如 AI 决策的 selector）必须按**选择器**传递，
        # 否则会被当作 target_hint 走模糊文本匹配 → CSS 串必不命中（未找到元素）。
        args["selector"] = action["selector"]

    # 文本/值（fill/type_text/set_date 等）
    # ★ 修复: value=0 时 falsy 导致 text 丢失 → 改用 is not None 判断
    text = action.get("text")
    if text is None:
        text = action.get("value")
    if text is not None:
        args["text"] = str(text)  # ★ Playwright fill() 要求 str
    if atype == "set_date" and action.get("value") is not None:
        args["value"] = str(action["value"])

    # select_option
    if action.get("option_text"):
        args["option_text"] = action["option_text"]
    elif atype == "select_option" and action.get("value"):
        args["option_text"] = action["value"]

    # upload_file
    file_path = action.get("file_path") or action.get("file")
    if file_path:
        args["file_path"] = file_path

    # navigate
    # ★ 通用修复 (2026-07-18 v2): 验证 URL 有效性
    #   Step3 AI 可能生成中文文本作为 URL（如 "url": "数据管理"），直接传给 navigate 会 404。
    #   验证规则：URL 必须以 "/" 或 "http" 开头，否则视为无效。
    #   无效时：保留 target_hint 用于菜单导航，url 设为空让 navigate 工具自行处理。
    _raw_url = action.get("url", "")
    if _raw_url:
        _raw_url = str(_raw_url).strip()
        if _raw_url.startswith("/") or _raw_url.startswith("http"):
            args["url"] = _raw_url
        else:
            _log.warning(
                "[dispatcher] navigate URL 无效（非路径/非http），将使用菜单导航: '%s'",
                _raw_url[:80],
            )
            # 不设置 url，让 navigate 工具通过 target_hint 走菜单导航路径
            # 确保 target_hint 存在
            if not args.get("target_hint") and action.get("target_hint"):
                args["target_hint"] = action["target_hint"]
    elif atype == "navigate" and action.get("value"):
        _raw_val = str(action.get("value", "")).strip()
        if _raw_val.startswith("/") or _raw_val.startswith("http"):
            args["url"] = _raw_val
        else:
            _log.warning(
                "[dispatcher] navigate value 无效（非路径/非http），将使用菜单导航: '%s'",
                _raw_val[:80],
            )

    # press_key / key_down / key_up
    if action.get("key"):
        args["key"] = action["key"]
    elif atype in ("press_key", "key_down", "key_up") and action.get("value"):
        args["key"] = action["value"]
    if action.get("focus_hint"):
        args["focus_hint"] = action["focus_hint"]

    # wait / long_press duration
    if "seconds" in action:
        args["seconds"] = action["seconds"]
    elif "duration" in action:
        args["seconds"] = action["duration"]
    if atype == "long_press" and action.get("duration_ms"):
        args["duration_ms"] = action["duration_ms"]

    # scroll / swipe
    if action.get("direction"):
        args["direction"] = action["direction"]
    if action.get("amount"):
        args["amount"] = action["amount"]
    if action.get("distance"):
        args["distance"] = action["distance"]

    # drag_offset
    if "dx" in action:
        args["dx"] = action["dx"]
    if "dy" in action:
        args["dy"] = action["dy"]

    # resize_window
    if "width" in action:
        args["width"] = action["width"]
    if "height" in action:
        args["height"] = action["height"]

    # switch_tab
    if "index" in action:
        args["index"] = action["index"]
    if action.get("url_keyword"):
        args["url_keyword"] = action["url_keyword"]

    # context_menu_click
    if action.get("menu_item_hint"):
        args["menu_item_hint"] = action["menu_item_hint"]

    # download_file
    if action.get("save_dir"):
        args["save_dir"] = action["save_dir"]

    # type_text
    if action.get("delay_ms"):
        args["delay_ms"] = action["delay_ms"]

    # wheel
    if "delta_x" in action:
        args["delta_x"] = action["delta_x"]
    if "delta_y" in action:
        args["delta_y"] = action["delta_y"]

    # multi_touch_pinch
    if "scale" in action:
        args["scale"] = action["scale"]

    # page_reload
    if action.get("wait_until"):
        args["wait_until"] = action["wait_until"]

    # screenshot
    if action.get("description"):
        args["description"] = action["description"]

    return args


def _convert_assertion_to_tool_args(assertion: dict[str, Any]) -> dict[str, Any]:
    """把断言字典统一转换成 browser_tools 所需参数。"""
    args: dict[str, Any] = {}
    for key in [
        "check_type",
        "selector",
        "expected",
        "property",
        "attribute",
        "class_name",
        "x",
        "y",
        "width",
        "height",
        "tolerance",
        "text",
        "timeout_ms",
        "expected_count",
        "url_pattern",
        "max_ms",
    ]:
        if key in assertion:
            args[key] = assertion[key]
    return args


# ----------------------------------------------------------------------------
# 元数据查询
# ----------------------------------------------------------------------------
def _get_action_metadata(
    action_id: str, library: dict[str, Any] | None = None
) -> dict[str, Any] | None:
    """从 action_library_store 查询 action 元数据；调用方可传入已加载的 library 避免重复查库。"""
    with guard("_get_action_metadata: lib = library if library is not None", log=_log):
        lib = library if library is not None else action_library_store.load_library()
        for cat in lib.get("categories", []):
            for act in cat.get("actions", []):
                if act.get("id") == action_id:
                    return act
    return None


# ----------------------------------------------------------------------------
# 公共分发接口
# ----------------------------------------------------------------------------
async def dispatch_action(
    engine, action: dict[str, Any], library: dict[str, Any] | None = None
) -> tuple[bool, bool, str]:
    """
    尝试按 action_library 查表执行一个动作。

    Args:
        library: 调用方已加载的操作指令库，避免每个 action 重复查库。

    Returns:
        (handled, success, error_msg): handled=True 表示 dispatcher 已处理（成功或失败）；
                            handled=False 表示应回退到上层硬编码分支或 AI。
                            error_msg 在 success=False 时包含详细错误信息；
                            未命中时携带明确 reason（EMPTY_TOOL/INVALID_TOOL/WORKFLOW_ONLY/
                            UNKNOWN_ACTION），不得静默 miss。
    """
    if not isinstance(action, dict):
        return False, False, "UNKNOWN_ACTION"
    action_id = action.get("type", "") or action.get("action", "")
    if not action_id:
        return False, False, "UNKNOWN_ACTION"

    # 确认 action 在库中存在（防止 AI 编造未注入的操作名）
    meta = _get_action_metadata(action_id, library)
    if meta is None:
        _record_metric("record_op_cache_miss")
        return False, False, "UNKNOWN_ACTION"

    entry = _get_action_to_tool_map().get(action_id)
    if entry is None:
        # action 在库中但不在映射中（理论不应发生）→ 走 elif 兜底
        _record_metric("record_op_cache_miss")
        return False, False, "NO_TOOL_MAPPING"

    tool_name, status = entry
    # ★ 三态显式化（2026-08-04）：EMPTY/INVALID/WORKFLOW 均返回明确 reason + warning，
    #   不再静默剔除。handled=False 表示「未命中需兜底」，不等于失败。
    if status == ACTION_TOOL_EMPTY:
        _record_metric("record_op_cache_miss")
        _log.warning(
            "[ActionDispatcher] %s tool_name 为空（EMPTY），走 elif 兜底", action_id
        )
        return False, False, "EMPTY_TOOL"
    if status == ACTION_TOOL_INVALID:
        _record_metric("record_op_cache_miss")
        _log.warning(
            "[ActionDispatcher] %s tool_name='%s' 无效（INVALID），走 elif 兜底",
            action_id,
            tool_name,
        )
        return False, False, "INVALID_TOOL"
    if status == ACTION_TOOL_WORKFLOW:
        _record_metric("record_op_cache_miss")
        _log.warning(
            "[ActionDispatcher] %s 为 workflow（WORKFLOW_ONLY），由工作流专用通道处理",
            action_id,
        )
        return False, False, "WORKFLOW_ONLY"

    # 查表命中（VALID）：记录缓存命中
    _record_metric("record_op_cache_hit")
    args = _convert_action_to_tool_args(action)
    try:
        result = await _execute_tool(tool_name, args, engine)
        # ★ P10 修复 (2026-07-07): ToolExecutionResult 的属性是 ok（不是 success）
        success = bool(result and result.ok)
        error_msg = ""
        if not success:
            error_msg = (result and result.content) or "unknown"
            _log.warning(
                "[ActionDispatcher] %s 工具执行未成功: %s | args=%s",
                action_id,
                error_msg,
                str(args)[:200],
            )
        else:
            _log.info("[ActionDispatcher] %s 工具执行成功", action_id)
        _record_execution(action_id, success)
        _log_tool_execution(tool_name, args, success, result)
        return True, success, error_msg
    except Exception as e:
        error_msg = str(e)[:200]
        _log.warning(
            "[ActionDispatcher] %s 执行异常: %s | args=%s",
            action_id,
            error_msg,
            str(args)[:200],
        )
        _record_execution(action_id, False)
        _log_tool_execution(tool_name, args, False, None)
        return True, False, error_msg


async def dispatch_assertion(
    engine, assertion: dict[str, Any], library: dict[str, Any] | None = None
) -> tuple[bool, bool]:
    """
    尝试按 action_library 查表执行一个断言。

    Args:
        library: 调用方已加载的操作指令库，避免每个断言重复查库。

    Returns:
        (handled, success)
    """
    if not isinstance(assertion, dict):
        return False, False
    action_id = assertion.get("type", "") or assertion.get("action", "")
    if not action_id:
        return False, False

    meta = _get_action_metadata(action_id, library)
    if meta is None:
        return False, False

    entry = _get_action_to_tool_map().get(action_id)
    if entry is None:
        return False, False
    tool_name, status = entry
    # ★ 三态显式化：非 VALID（EMPTY/INVALID/WORKFLOW）→ 未命中需上层兜底
    if status != ACTION_TOOL_VALID:
        _log.warning(
            "[ActionDispatcher] 断言 %s 状态=%s，未命中查表（交由上层兜底）",
            action_id,
            status,
        )
        return False, False

    args = _convert_assertion_to_tool_args(assertion)
    try:
        result = await _execute_tool(tool_name, args, engine)
        # ★ P10 修复 (2026-07-07): ToolExecutionResult 的属性是 ok（不是 success）
        success = bool(result and result.ok)
        if not success:
            _log.info(
                "[ActionDispatcher] %s 断言未通过: %s",
                action_id,
                (result and result.content) or "unknown",
            )
        _record_execution(action_id, success)
        return True, success
    except Exception as e:
        _log.warning("[ActionDispatcher] %s 断言执行异常: %s", action_id, str(e)[:80])
        _record_execution(action_id, False)
        return True, False


def _record_execution(action_id: str, success: bool) -> None:
    """记录操作执行统计，异步更新数据库。"""
    with guard("_record_execution: action_library_store.record_action_e", log=_log):
        action_library_store.record_action_execution(action_id, success)
        _record_metric("record_action_attempt", {"action_type": action_id})


def _log_tool_execution(tool_name: str, args: dict, success: bool, result) -> None:
    """记录 tool 执行到 tool_execution_log（供 Tool-mode率 统计）。"""
    with guard("_log_tool_execution: from src.pipeline.steps.step4.tool_e", log=_log):
        from src.pipeline.steps.step4.tool_execution_logger import tool_logger
        from src.core.config_loader import config

        tool_logger.log(
            project=config.project_name,
            case_id="",
            step_desc="",
            trace_id="",
            tool_name=tool_name,
            tool_args=args,
            result_ok=success,
            result_content=(result and result.content) or "",
            tool_mode=True,
        )
