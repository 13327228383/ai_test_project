# -*- coding: utf-8 -*-
"""
action_hooks — 可复用的 before_action / after_action 插件钩子
============================================================
所有执行路径（AI 循环、结构化执行、工具执行、数据工厂）统一调用，
确保插件钩子真实触发（唯一实现，不再经 monkey-patch 注入）。

使用方式：
    from src.pipeline.steps.step4.action_hooks import run_action_with_hooks
    result = await run_action_with_hooks(engine, "click", target_hint, do_action)
"""

import logging
from src.core.guarded import guard
from collections.abc import Callable
from typing import Any, Awaitable

_log = logging.getLogger(__name__)

# ★ 需要检查元素存在性的动作类型（仅 DOM 交互类，不含 navigate/wait/scroll 等）
_ELEMENT_ACTION_TYPES = {
    "click",
    "fill",
    "select_option",
    "check",
    "uncheck",
    "hover",
    "double_click",
    "right_click",
    "drag",
}

# ── 动作后页面就绪等待（唯一权威，所有执行路径共用）──
# 为什么：click 触发整页跳转后，原先只做固定短 sleep 就返回"成功"，下一步会在尚未
# 就绪的新文档上执行而定位失败（目标页按钮数为 0）；且该等待语义曾分散在多处、
# 时长互不一致。故收敛为单点，判据只看页面事实（动作前 URL + 真实加载状态）。
_NAVIGATING_ACTION_TYPES = {
    "navigate",
    "click",
    "double_click",
    "right_click",
    "middle_click",
    "context_menu_click",
    "long_press",
    "press_key",
    "submit",
}
_SETTLE_DCL_TIMEOUT_MS = 10000  # 等待 DOMContentLoaded 上限
_SETTLE_NETIDLE_TIMEOUT_MS = 8000  # navigate 后追加等网络空闲上限
_SETTLE_POST_NAV_MS = 300  # 新文档就绪后的渲染稳定时间


async def settle_page_after_action(
    engine, action_type: str, before_url: str = ""
) -> None:
    """动作后等待页面就绪（唯一权威）。

    · navigate       → 等 DOMContentLoaded + 网络空闲（均有界），再短稳定；
    · 导航型元素动作 → 等 DOMContentLoaded；若 URL 已变化说明发生跳转，再等一次；
    · 其余动作       → 保留既有可配置短等待（表单填充等无需等加载）。

    任何一步超时都 fail-open（只记 debug），绝不把"等待失败"升级成用例失败。
    """
    page = getattr(engine, "_page", None)
    if page is None:
        return
    import asyncio as _asyncio

    if action_type == "navigate":
        for _state, _tmo in (
            ("domcontentloaded", _SETTLE_DCL_TIMEOUT_MS),
            ("networkidle", _SETTLE_NETIDLE_TIMEOUT_MS),
        ):
            try:
                await page.wait_for_load_state(_state, timeout=_tmo)
            except Exception as _e:
                _log.debug("[action_hooks] 等待 %s 超时(继续): %s", _state, _e)
        await page.wait_for_timeout(_SETTLE_POST_NAV_MS)
        return

    if action_type in _NAVIGATING_ACTION_TYPES:
        try:
            await page.wait_for_load_state(
                "domcontentloaded", timeout=_SETTLE_DCL_TIMEOUT_MS
            )
        except Exception as _e:
            _log.debug("[action_hooks] 等待 DOMContentLoaded 超时(继续): %s", _e)
        _now_url = ""
        try:
            _now_url = str(page.url or "")
        except Exception:
            _now_url = ""
        if before_url and _now_url and _now_url != before_url:
            # 已发生跳转 → 新文档还需一次就绪等待 + 渲染稳定时间
            try:
                await page.wait_for_load_state(
                    "domcontentloaded", timeout=_SETTLE_DCL_TIMEOUT_MS
                )
            except Exception as _e:
                _log.debug("[action_hooks] 新文档等待超时(继续): %s", _e)
            await page.wait_for_timeout(_SETTLE_POST_NAV_MS)
        return

    try:
        from src.core.pipeline_config import pipeline_config

        _wait_ms = float(getattr(pipeline_config, "action_hooks_stable_wait_ms", 300))
    except Exception:
        _wait_ms = 300.0
    try:
        await _asyncio.sleep(_wait_ms / 1000.0)
    except Exception:
        pass


# ── 用例前置数据保障（声明来自 Step3 的结构化字段，执行端只读）──
async def _ensure_case_data_precondition(engine) -> None:
    """首个元素交互动作之前满足用例声明的前置数据（每用例只做一次）。

    why: 用例声明 `data_precondition=existing_row`（断言对象是列表里的一条记录）时，
      前序破坏性用例可能已把列表清空 —— 元素定位必然 0 命中。此处先造行，造不出就
      记录缺口由 `_base` 记 SKIP，既不把引擎数据条件写成产品缺陷，也不假绿。
    """
    page = getattr(engine, "_page", None)
    if page is None or getattr(page, "_case_data_precondition_done", False):
        return
    setattr(page, "_case_data_precondition_done", True)
    from src.pipeline.step3._trigger_actions import DATA_PRECONDITION_EXISTING_ROW

    if str(getattr(page, "_case_data_precondition", "") or "") != (
        DATA_PRECONDITION_EXISTING_ROW
    ):
        return
    try:
        from src.pipeline.steps.step4.data_precondition import ensure_existing_row

        _gap = await ensure_existing_row(engine)
    except Exception as _e:  # noqa: BLE001 —— 造数异常不阻断动作，按缺口诚实记录
        _gap = "造数执行异常: %s" % str(_e)[:80]
    if _gap:
        setattr(page, "_case_data_precondition_gap", _gap)
        _log.warning("[action_hooks] 前置数据未满足: %s", _gap)


# ── 列表规模基线（用例级，动作前采集）──
async def _ensure_case_row_baseline(engine) -> None:
    """首个**交互动作之前**采集列表规模基线（相对差分断言的唯一前置事实）。

    why: 基线只在被改动**之前**存在；动作之后（断言/校验阶段）再采必是改动后的值。
      用例开始时页面还停在上一用例的页面 —— 也采不到本用例的列表事实。
      故唯一正确时点是「本用例首个元素交互动作即将执行前」（此时已导航到目标页）。
    只在当前页面确有列表时写入（登录页/表单页的 0 行不作为基线）。
    读取口径复用 `read_list_size`（总数优先；分页列表的语义行数）。
    """
    page = getattr(engine, "_page", None)
    if page is None or getattr(page, "_case_row_baseline", None) is not None:
        return
    try:
        from src.pipeline.steps.step4.verification._db_ui import read_list_size

        _n, _src = await read_list_size(page)
    except Exception as _e:  # noqa: BLE001 —— 采集失败不阻断动作（差分断言按诚实跳过处理）
        _log.debug("[action_hooks] 行数基线采集失败: %s", str(_e)[:80])
        return
    if _n and _n > 0:
        setattr(page, "_case_row_baseline", int(_n))
        _log.info("[action_hooks] 列表规模基线=%d (来源=%s)", int(_n), _src)


# ── 元素存在性检查 ──
async def _check_element_exists(
    engine, target_hint: str, action_type: str = ""
) -> bool:
    """检查目标元素是否真实存在于当前页面，并校验元素类型与 action 语义匹配。

    ★ 定位一律走 `_locate_by_hint`（hint 语法的唯一解析器，与 do_action 内部一致），
       不再另做"hint→CSS 选择器"转换：那种转换会把"输入框"映射成
       `input[type=text]` 从而命中无关元素，导致 SelfHealing 不触发。
       `_locate_by_hint` 返回 None 时触发 SelfHealing。
    ★ Fix (2026-07-15): 增加 action_type 参数，校验元素类型与 action 语义匹配。
       click 类动作要求元素是可点击类型（button/a/[role=button]/[role=menuitem]），
       避免业务术语文本匹配命中无关文本节点（如菜单标签）导致 self-healing 被绕过。
    """
    if (
        not target_hint
        or not engine
        or not hasattr(engine, "_page")
        or not engine._page
    ):
        return True  # 无法检查 → 保守假设存在
    try:
        from src.pipeline.steps.step4.browser_tools.locator import _locate_by_hint

        locator = await _locate_by_hint(engine._page, target_hint)
        if locator is not None:
            try:
                count = await locator.count()
                if count == 0:
                    return False
                # ★ P1 修复：对 click 类动作校验元素类型，避免业务术语命中无关文本节点
                #   场景：target_hint="数据管理" → _locate_by_hint 用 get_by_text 命中菜单 span
                #   → click 执行在 span 上而非 button → 不报错但点错元素 → self-healing 被绕过
                if action_type in (
                    "click",
                    "click_button",
                    "click_link",
                    "click_menu",
                    "double_click",
                    "right_click",
                    "submit",
                ):
                    # 检查命中的元素是否为可点击类型
                    with guard("_check_element_exists: clickable_eval = await engine._page.", log=_log):
                        # ★ 候选组同源(2026-10-14)：hint 可能是"保存,提交,确定,确认"这类
                        #   候选组（定位器按 `split_candidate_hint` 逐个尝试）。此处必须用
                        #   **同一拆分**做文本匹配，否则整串当字面文本 →
                        #   `"保存".includes("保存,提交,确定,确认")` 恒假 → 把真实可点击的
                        #   <button>保存</button> 误判为"非可点击元素" → 误触发 self-healing
                        #   → 结构化路径中止（实测 TC-REQ-024/025-UX02 假 ERROR）。
                        from src.pipeline.steps.step4.browser_tools._hint_utils import (
                            split_candidate_hint,
                        )

                        _hint_tokens = split_candidate_hint(target_hint)
                        # 取第一个元素的可点击性：button/a/[role=button]/[role=menuitem]/[role=link]
                        # input/select/textarea 也可点击（如 submit 按钮），但 span/div 文本节点不可
                        clickable_eval = await engine._page.evaluate(
                            """(tokens) => {
                            // 优先检查 locator 命中的元素
                            const els = document.querySelectorAll(
                                'button, a, [role="button"], [role="link"], [role="menuitem"], ' +
                                '[role="menuitemcheckbox"], [role="menuitemradio"], [role="tab"], ' +
                                'input[type="button"], input[type="submit"], input[type="reset"], ' +
                                'input[type="checkbox"], input[type="radio"], select, summary'
                            );
                            // 任一候选词命中任一可点击元素的文本/aria → 认定可点击
                            for (const el of els) {
                                const elText = (el.innerText || el.textContent || el.value || '').trim().toLowerCase();
                                const aria = (el.getAttribute('aria-label') || '').toLowerCase();
                                for (const raw of tokens) {
                                    const t = (raw || '').toLowerCase();
                                    if (!t) continue;
                                    if ((elText && elText.includes(t)) || (aria && aria.includes(t))) {
                                        return true;
                                    }
                                }
                            }
                            return false;
                        }""",
                            _hint_tokens,
                        )
                        if not clickable_eval:
                            _log.info(
                                "[action_hooks] 元素类型不匹配: hint=%s action=%s "
                                "→ 触发 self-healing（文本命中但非可点击元素）",
                                target_hint[:40],
                                action_type,
                            )
                            return False
                return True
            except Exception as e:
                _log.warning("元素count异常: %s", e, exc_info=True)
                return True  # count() 失败 → 保守假设存在
        # _locate_by_hint 返回 None → 元素不存在或格式不支持 → 触发 SelfHealing
        return False
    except Exception as e:
        _log.warning("元素locate_by_hint异常: %s", e, exc_info=True)
        return True  # 异常 → 保守假设存在


# ── 页面状态捕获（供 GroundTruth 等插件使用）──
async def _capture_page_state(engine) -> dict[str, Any]:
    """捕获当前页面完整状态快照（url/title/text_len/element_count/dom_signature）。

    返回空 dict 表示不可用（引擎未初始化 / 异常）。
    """
    try:
        if not engine or not hasattr(engine, "_page") or not engine._page:
            return {}
        page = engine._page
        # 基础属性（O(1)，极快）
        url = (page.url or "")[:2048]
        try:
            title = (await page.title() or "")[:512]
        except Exception as _e:
            _log.debug("[action_hooks] _capture_page_state 异常: %s", _e, exc_info=True)
            title = ""
        # 文本信息（轻量 JS evaluate — 一次调用获取 text_len + text_head）
        try:
            text_info = await page.evaluate(
                """() => {
                const text = document.body.innerText || '';
                return { len: text.length, head: text.substring(0, 200) };
            }"""
            )
            text_len = (
                (text_info or {}).get("len", 0) if isinstance(text_info, dict) else 0
            )
            text_head = (
                (text_info or {}).get("head", "") if isinstance(text_info, dict) else ""
            )
        except Exception as _e:
            _log.debug("[action_hooks] _capture_page_state 异常: %s", _e, exc_info=True)
            text_len = 0
            text_head = ""
        # DOM 结构化状态（较重，但复用已有调用）
        dom_elements = await engine.get_structured_state()
        element_count = len(dom_elements) if dom_elements else 0
        # DOM 签名（前 2000 字符哈希，供差异检测）
        import json as _json

        dom_str = (
            _json.dumps(dom_elements[:100], ensure_ascii=False) if dom_elements else ""
        )
        dom_signature = str(hash(dom_str[:2000])) if dom_str else ""
        # ★ 精简元素列表供自愈策略使用（仅保留 tag/text/placeholder/ai_id/role/class/interactive）
        _elements = []
        for el in (dom_elements or [])[:100]:
            if isinstance(el, dict):
                _elements.append(
                    {
                        "tag": el.get("tag", ""),
                        "text": el.get("text", ""),
                        "placeholder": el.get("placeholder", ""),
                        "ai_id": el.get("ai_id", ""),
                        "role": el.get("role", ""),
                        "class": el.get("class", "") or el.get("className", ""),
                        "interactive": el.get("interactive", False),
                        "label": el.get("label", ""),
                    }
                )
        return {
            "url": url,
            "title": title,
            "text_len": text_len,
            "text_head": text_head,  # ★ 供 GroundTruth text_head 检查
            "element_count": element_count,
            "dom_signature": dom_signature,
            "_elements": _elements,  # ★ 供 SelfHealing 文本策略使用
        }
    except Exception as _e:
        _log.debug("[action_hooks] 非关键异常: %s", _e, exc_info=True)
        return {}


# ── 核心钩子包装函数 ──
async def run_action_with_hooks(
    engine,
    action_type: str,
    target_hint: str = "",
    do_action: Callable[[dict[str, Any]], Awaitable[Any]] = None,
    *,
    action_extra: dict[str, Any] = None,
    token_usage: dict[
        str, int
    ] = None,  # ★ {input: int, output: int} 供 HarnessGovernance 追踪
) -> dict[str, Any]:
    """
    执行一个动作，并在前后触发插件钩子。

    Args:
        engine: PlaywrightEngine 实例
        action_type: 动作类型 (click/fill/navigate/select_option/assert)
        target_hint: AI 生成的目标元素 hint (如 "placeholder=用户名")
        do_action: 实际执行动作的 async 回调，接收最终 action 字典（可能已被插件治愈）。
                   可返回 Dict 结果，无返回值时默认 {"success": True}
        action_extra: 额外 action 字段 (如 selector, text, url 等)
        token_usage: AI 调用 token 用量 {"input": int, "output": int}，供 HarnessGovernancePlugin 追踪

    Returns:
        {"success": bool, ...}  — 执行结果，合并 do_action 的返回值
    """
    from src.pipeline.steps.step4.plugins import PluginManager, PluginHook

    action = {
        "action": action_type,
        "target_hint": target_hint,
    }
    if action_extra:
        action.update(action_extra)

    # 1. 页面状态快照（动作前）— 含 url/title/text_len/element_count/dom_signature
    before_state = await _capture_page_state(engine)

    # 2. 元素存在性检查（自愈触发依据）—— 仅 DOM 交互类动作需要
    if action_type in _ELEMENT_ACTION_TYPES:
        # 本用例首个交互动作**之前**：先满足前置数据（造行），再采集列表规模基线
        #   （顺序不可颠倒——基线必须包含造出来的那一行，差分断言才成立）
        await _ensure_case_data_precondition(engine)
        await _ensure_case_row_baseline(engine)
        element_found = await _check_element_exists(engine, target_hint, action_type)
    else:
        element_found = True  # navigate/wait/scroll 等不涉及元素定位，跳过

    # 3. BEFORE_ACTION 插件钩子（自愈 + 治理）
    with guard("run_action_with_hooks: _page_elements = ( (before_state or ", log=_log):
        # ★ 从 before_state 提取精简元素列表，供 SelfHealing 文本策略使用
        _page_elements = (
            (before_state or {}).get("_elements", []) if before_state else []
        )
        plugin_ctx = {
            "action": action,
            "engine": engine,
            "page_state": {"elements": _page_elements},
            "element_found": element_found,
        }
        plugin_result = await PluginManager().execute_hook(
            "before_action", **plugin_ctx
        )
        if plugin_result and plugin_result.get("self_healed"):
            healed_action = plugin_result.get("action", action)
            _log.info(
                "[action_hooks] Self-Healing 修复: %s → %s",
                target_hint[:40],
                healed_action.get("target_hint", "?")[:40],
            )
            action = healed_action

    # 4. 执行实际动作（支持动作失败后一次 SelfHealing 自愈重试）
    # ★ 统一兜底入口：pre-check 阶段的 SelfHealing 处理"元素未找到"；
    #   这里的 post-action healing 处理"pre-check 通过但动作执行时元素失效/消失"。
    #   AI 循环内部不再维护独立的 #ai-N fallback。
    success = True
    error_msg = ""
    action_result = None
    post_heal_attempted = False
    while True:
        try:
            if do_action:
                # ★ 传递最终 action 字典（可能已被 BEFORE_ACTION 插件治愈）
                action_result = await do_action(action)
            break
        except Exception as e:
            _err = str(e)[:200]
            _log.debug("[action_hooks] 动作执行异常: %s", _err)

            # 仅元素交互类动作、且还没做过动作后自愈时，尝试自愈重试
            if post_heal_attempted or action_type not in _ELEMENT_ACTION_TYPES:
                success = False
                error_msg = _err
                break

            # 仅对疑似元素定位/超时异常做动作后自愈，避免网络/权限错误也乱定位
            _is_locator_error = any(
                kw in _err.lower()
                for kw in [
                    "未找到元素",
                    "timeout",
                    "selector",
                    "locator",
                    "element",
                    "strict",
                    "resolved to",
                    "closed",
                    "detached",
                    "waitfor",
                ]
            )
            if not _is_locator_error:
                success = False
                error_msg = _err
                break

            post_heal_attempted = True
            with guard("run_action_with_hooks: _page_elements = ( (before_state or ", log=_log):
                # 动作失败后的自愈：把 element_found 视为 False，让 SelfHealing 再试一次
                _page_elements = (
                    (before_state or {}).get("_elements", []) if before_state else []
                )
                plugin_ctx = {
                    "action": action,
                    "engine": engine,
                    "page_state": {"elements": _page_elements},
                    "element_found": False,
                }
                plugin_result = await PluginManager().execute_hook(
                    "before_action", **plugin_ctx
                )
                if plugin_result and plugin_result.get("self_healed"):
                    healed_action = plugin_result.get("action", action)
                    _new_hint = healed_action.get("target_hint", "")
                    if _new_hint and _new_hint != action.get("target_hint", ""):
                        _log.info(
                            "[action_hooks] 动作失败后自愈重试: %s → %s",
                            action.get("target_hint", "")[:40],
                            _new_hint[:40],
                        )
                        action = healed_action
                        continue  # 用修复后的 action 重试

            success = False
            error_msg = _err
            break

    # 合并 do_action 的返回值
    result = {"success": success}
    if error_msg:
        result["error"] = error_msg
    if isinstance(action_result, dict):
        result.update(action_result)
        # 以 do_action 的 success 为准（如果它返回了 success）
        if "success" in action_result:
            result["success"] = bool(action_result["success"])
    elif isinstance(action_result, bool):
        # ★ bool 返回值（如 engine.click_element / engine.goto）→ 直接作为 success
        result["success"] = action_result

    # 5. 动作后页面就绪等待（唯一权威，见 settle_page_after_action）
    #    原实现仅固定 300ms sleep → click 触发跳转后下一步在未就绪页面上执行（假失败）。
    try:
        await settle_page_after_action(
            engine, action_type, str((before_state or {}).get("url") or "")
        )
    except Exception as _e:
        _log.debug("[action_hooks] 页面就绪等待异常(继续): %s", _e)

    # 6. 页面状态快照（动作后）
    after_state = await _capture_page_state(engine)

    # 7. AFTER_ACTION 插件钩子（真值校验 + 自愈验证 + 治理）
    with guard("run_action_with_hooks: final_ok = result.get('success', suc", log=_log):
        # ★ 以最终合并结果中的 success 为准（do_action 可能返回 {"success": False} 但未抛异常）
        final_ok = result.get("success", success)
        plugin_ctx_after = {
            "action": action,
            "result": final_ok,
            "before_state": before_state,  # ★ 完整页面状态（url/title/text_len/element_count/dom_signature）
            "after_state": after_state,  # ★ 供 GroundTruth 等插件使用
            "engine": engine,
            "token_usage": token_usage or {},  # ★ 供 HarnessGovernancePlugin 追踪
        }
        PluginManager().emit(PluginHook.AFTER_ACTION, plugin_ctx_after)

    return result
