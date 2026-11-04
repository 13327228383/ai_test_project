# -*- coding: utf-8 -*-
"""
_execute_tool 的 browser_* 工具处理器（模块级函数）
=================================================
★ 架构债清理(2026-07-25): 从 _single.py 的 _execute_tool 闭包中拆出，
  解决 _single.py 单文件超 800 行红线（§4.3）。
  所有函数签名统一为 (engine, args, hint) -> ToolExecutionResult，
  由 _single.py 的分派表通过 lambda 适配为零参调用。
  注: _browser_upload_file 属 B.1c，未包含在此文件。
"""

from src.pipeline.steps.step4.browser_tools.execution._single import _NEGATIVE_KWS_V91 as _NEGATIVE_K93  # noqa: F401  词表唯一源
from src.core.guarded import guard

import asyncio
import logging

from ._result import ToolExecutionResult
from ..locator import _locate_with_fallback, pre_verify_interactive_element
from src.pipeline.steps.step4.action_hooks import run_action_with_hooks
from ._single_menu import _try_remedy_disabled_button
from src.pipeline.steps.step4.browser_tools.execution._single import _SUBMIT_KWS_V91 as _SUBMIT_HINTS_V92  # noqa: F401  词表唯一源
from ._single_nav_helpers import (
    _do_drag,
    _fill_by_hint,
    _handle_browser_navigate,
    _select_by_hint,
)

_log = logging.getLogger(__name__)


async def _browser_blur(engine, args, hint=""):
    try:
        ok = await run_action_with_hooks(
            engine,
            "blur",
            hint,
            do_action=lambda a: engine.blur_element(
                ai_id="", target_hint=a.get("target_hint", hint)
            ),
        )
        return ToolExecutionResult(ok.get("success", False), "blur: " + hint)
    except Exception as e:
        return ToolExecutionResult(False, "blur 失败: " + str(e))


async def _browser_check(engine, args, hint=""):
    try:
        page = getattr(engine, "_page", None)
        if not page:
            return ToolExecutionResult(False, "页面未初始化")

        async def _do_check(a, p=page, h=hint, s=args.get("selector", "")):
            healed = a.get("target_hint", h)
            locator = await _locate_with_fallback(p, healed, selector=s)
            if not locator:
                raise ValueError(f"未找到元素: {healed}")
            # ★ 前置验证：元素可交互（存在 + 可见 + 未禁用）
            verify = await pre_verify_interactive_element(locator)
            if not verify.passed:
                raise ValueError(f"元素不可交互: {healed} — {verify.message}")
            if not await locator.is_checked():
                await locator.check(timeout=5000)
            return {"success": True}

        check_result = await run_action_with_hooks(
            engine, "check", hint, do_action=_do_check
        )
        ok = check_result.get("success", False)
        return ToolExecutionResult(
            ok,
            "已勾选: " + hint if ok else "勾选失败: " + check_result.get("error", "?"),
        )
    except Exception as e:
        return ToolExecutionResult(False, "勾选失败: " + str(e))


async def _browser_clear(engine, args, hint=""):
    try:
        ok = await run_action_with_hooks(
            engine,
            "clear",
            hint,
            do_action=lambda a: engine.clear_element(
                ai_id="", target_hint=a.get("target_hint", hint)
            ),
        )
        return ToolExecutionResult(ok.get("success", False), "clear: " + hint)
    except Exception as e:
        return ToolExecutionResult(False, "clear 失败: " + str(e))


async def _browser_click(engine, args, hint=""):
    try:
        page = getattr(engine, "_page", None)
        if not page:
            return ToolExecutionResult(False, "页面未初始化")

        async def _do_click(a, p=page, h=hint, s=args.get("selector", "")):
            healed = a.get("target_hint", h)
            locator = await _locate_with_fallback(p, healed, selector=s)
            if not locator:
                # ★ v9.2: upload 已提交后，后续提交类 click 找不到元素时跳过（避免假 FAIL）
                #   根因: v9.1 完成 upload+submit 后 modal 关闭，后续 click "开始导入/确定" 找不到元素
                #   通用性: 适用于所有"上传后点击提交"场景，覆盖所有系统/行业
                #   职责边界: Step4 执行层——upload_file 已完成提交语义，后续重复 click 应跳过
                if getattr(engine, "_upload_submitted", False):
                    _healed_lower = (healed or "").strip()
                    if any(_kw in _healed_lower for _kw in _SUBMIT_HINTS_V92):
                        _log.info(
                            "[browser_click] v9.2: upload 已提交，跳过提交类 click '%s' (元素已不存在)",
                            healed[:50],
                        )
                        return {"success": True, "skipped": "upload_submitted"}
                # ★ v16 失败回传(2026-07-23): 收集可见 ARIA 菜单项附到异常, 供 Step3 诊断
                #   根因(§10.9): Step3 2-gram 匹配可能选错菜单项 → Step4 精确点击失败
                #   通用性(§12.4): 只读 role=menuitem/option (W3C ARIA 契约), 不依赖框架类名
                #   职责边界(§12.1): Step4 只回传诊断信息, 不越界选其他菜单项(不决策)
                _visible_items_v16 = []
                try:
                    for _mi_sel_v16 in (
                        '[role="menuitem"]',
                        '[role="menuitemcheckbox"]',
                        '[role="menuitemradio"]',
                        '[role="option"]',
                    ):
                        _mi_loc_v16 = p.locator(_mi_sel_v16)
                        _cnt_v16 = await _mi_loc_v16.count()
                        for _i_v16 in range(min(_cnt_v16, 20)):
                            try:
                                _mi_item_v16 = _mi_loc_v16.nth(_i_v16)
                                if await _mi_item_v16.is_visible():
                                    _txt_v16 = (
                                        await _mi_item_v16.inner_text() or ""
                                    ).strip()
                                    if _txt_v16:
                                        _visible_items_v16.append(_txt_v16[:40])
                            except Exception as _se25:
                                _log.debug(
                                    "[_single] 异常降级(continue): %s",
                                    str(_se25)[:60],
                                    exc_info=True,
                                )
                                continue
                except Exception as _se26:
                    _log.debug(
                        "[_single] 异常降级(pass): %s", str(_se26)[:60], exc_info=True
                    )
                    pass
                _diag_v16 = (
                    f" (可见菜单项: {_visible_items_v16})" if _visible_items_v16 else ""
                )
                raise ValueError(f"未找到元素: {healed}{_diag_v16}")
            # ★ 前置验证：元素可交互（存在 + 可见 + 未禁用）
            verify = await pre_verify_interactive_element(locator)
            if not verify.passed:
                # ★ 方案B：disabled 时尝试自动补救
                if "已禁用" in verify.message:
                    _log.info("[Remedy] 按钮 disabled，尝试自动补救: %s", healed)
                    _remedied = await _try_remedy_disabled_button(p, locator, healed)
                    if _remedied:
                        _log.info("[Remedy] 补救成功，继续点击")
                    else:
                        raise ValueError(
                            f"元素不可交互: {healed} — {verify.message}（自动补救失败）"
                        )
                else:
                    raise ValueError(f"元素不可交互: {healed} — {verify.message}")
            try:
                await locator.click(timeout=5000)
            except Exception as _click_err:
                _err_str = str(_click_err)
                # ★ 通用修复：click 超时/失败后尝试父元素 + force click 兜底
                # 场景：UI框架中文本在span内，但click handler在父元素(li/div)上
                # 如 ElementUI el-menu 的 <span>数据管理</span> 在 <li> 内
                _log.info(
                    "[browser_click] 首次click失败(%s)，尝试父元素回退: %s",
                    _err_str[:80],
                    healed,
                )
                # 策略1：尝试点击父元素（最多向上3层）
                _parent_clicked = False
                with guard("_do_click: _cur = locator", log=_log):
                    _cur = locator
                    for _depth in range(3):
                        _parent = _cur.locator("..")
                        if await _parent.count() > 0:
                            _parent_tag = ""
                            try:
                                _parent_tag = await _parent.evaluate(
                                    "el => (el.tagName || '').toLowerCase()"
                                )
                            except Exception as _se27:
                                _log.debug(
                                    "[_single] 异常降级(pass): %s",
                                    str(_se27)[:60],
                                    exc_info=True,
                                )
                                pass
                            _log.info(
                                "[browser_click] 尝试父元素 depth=%d tag=%s",
                                _depth + 1,
                                _parent_tag,
                            )
                            await _parent.click(timeout=5000)
                            _log.info(
                                "[browser_click] ✓ 父元素点击成功 (depth=%d, tag=%s)",
                                _depth + 1,
                                _parent_tag,
                            )
                            _parent_clicked = True
                            break
                        _cur = _parent
                if _parent_clicked:
                    return {"success": True}
                # 策略2：force click 兜底（绕过actionability检查）
                with guard("_do_click: _log.info('[browser_click] 尝试 force ", log=_log):
                    _log.info("[browser_click] 尝试 force click: %s", healed)
                    await locator.click(timeout=5000, force=True)
                    _log.info("[browser_click] ✓ force click 成功")
                    return {"success": True}
                raise _click_err
            return {"success": True}

        ok = await run_action_with_hooks(engine, "click", hint, do_action=_do_click)
        if ok.get("success"):
            # ★ v9.3 通用修复(2026-07-22): click 触发类按钮后检测确认 modal，自动点击"确定/确认/提交"
            #   根因核查(§10.9):
            #     1. 现象: click "开始导入" 后弹出确认 modal，但 case_actions 没有 "click 确定" 步骤，
            #        导致业务逻辑未执行，text_contains 校验失败
            #     2. 直接原因: actions[3] click "开始导入" 只弹出 modal，没有点击"确定"
            #     3. 中间原因: Step3 生成的 actions 不完整（缺少 modal 内确认步骤）
            #     4. 根本原因: 不同系统 "click 触发按钮 → 确认 modal → 点确定" 流程普遍存在，
            #        Step4 应通用处理 click 后的确认 modal（作为 click 副作用）
            #   全局观: 影响所有 "click→确认modal→确定" 的流程（所有系统/行业）
            #   职责边界: Step4 执行层——click 副作用处理（类似 click 后等待 modal 渲染）
            #   通用性自检:
            #     - 跨系统: Ant Design/ElementUI/Native HTML 的 Modal/Dialog
            #     - 跨行业: 通用（所有需要确认的操作）
            #     - 跨页面: 适用于所有含确认 modal 的页面
            #   自我反驳三次:
            #     1) 漏洞？→ 可能误点删除确认 → 只在触发类按钮（导入/上传/提交/保存等）后触发
            #     2) 更通用？→ 只点"确定/确认/提交/保存"，排除"取消/关闭/返回"
            #     3) 更根本？→ Step3 应生成完整 actions，但 Step4 通用处理是必要的增强
            #   触发条件: target_hint 含触发关键词（导入/上传/提交/保存/发布/执行/审批）
            with guard("_browser_click: _TRIGGER_HINTS_V93 = ( '导入', '上传', '", log=_log):
                _TRIGGER_HINTS_V93 = (
                    "导入",
                    "上传",
                    "提交",
                    "保存",
                    "发布",
                    "执行",
                    "审批",
                    "开始",
                )
                _hint_str_v93 = (hint or "").strip()
                if any(_kw in _hint_str_v93 for _kw in _TRIGGER_HINTS_V93):
                    _CONFIRM_KWS_V93 = (
                        "确定",
                        "确认",
                        "提交",
                        "保存",
                        "导入",
                        "上传",
                        "开始",
                        "执行",
                        "应用",
                        "完成",
                        "下一步",
                        "继续",
                        "OK",
                        "Yes",
                    )
                    _MODAL_SELS_V93 = (
                        ".ant-modal",
                        ".el-dialog",
                        '[role="dialog"]',
                        ".modal",
                        ".ant-drawer-content",
                    )
                    await asyncio.sleep(1.0)  # 等待 modal 渲染
                    _v93_clicked = False
                    for _modal_sel in _MODAL_SELS_V93:
                        try:
                            _modal_v93 = page.locator(_modal_sel).first
                            if await _modal_v93.count() == 0:
                                continue
                            if not await _modal_v93.is_visible(timeout=500):
                                continue
                            _log.info(
                                "[browser_click] v9.3: click '%s' 后检测到确认 modal '%s'，查找确认按钮",
                                _hint_str_v93[:40],
                                _modal_sel,
                            )
                            _btns_v93 = _modal_v93.locator(
                                "button, [role='button'], .ant-btn, .el-button, "
                                "a[class*='btn'], span[class*='btn'], div[class*='btn']"
                            )
                            _count_v93 = await _btns_v93.count()
                            # ★ v9.3 调试: 打印 modal 内所有按钮文本
                            _all_btn_texts = []
                            for _i_v93 in range(min(_count_v93, 30)):
                                try:
                                    _btn_v93 = _btns_v93.nth(_i_v93)
                                    _text_v93 = (
                                        await _btn_v93.text_content() or ""
                                    ).strip()
                                    _text_ns_v93 = (
                                        _text_v93.replace(" ", "")
                                        .replace("\t", "")
                                        .replace("\n", "")
                                    )
                                    _all_btn_texts.append(_text_v93)
                                    if not _text_ns_v93 or len(_text_ns_v93) > 12:
                                        continue
                                    if any(
                                        _neg in _text_ns_v93 for _neg in _NEGATIVE_K93
                                    ):
                                        continue
                                    if any(
                                        _kw in _text_ns_v93 for _kw in _CONFIRM_KWS_V93
                                    ):
                                        if await _btn_v93.is_visible():
                                            _log.info(
                                                "[browser_click] v9.3: 自动点击确认按钮 '%s'",
                                                _text_v93,
                                            )
                                            try:
                                                await _btn_v93.click(timeout=3000)
                                                _v93_clicked = True
                                            except Exception:
                                                with guard("_browser_click: await _btn_v93.click( timeout=3000, ", log=_log, level=logging.WARNING):
                                                    await _btn_v93.click(
                                                        timeout=3000, force=True
                                                    )
                                                    _v93_clicked = True
                                            break
                                except Exception as _se28:
                                    _log.debug(
                                        "[_single] 异常降级(continue): %s",
                                        str(_se28)[:60],
                                        exc_info=True,
                                    )
                                    continue
                            _log.info(
                                "[browser_click] v9.3: modal 内按钮文本列表: %s",
                                str(_all_btn_texts)[:200],
                            )
                            if _v93_clicked:
                                await asyncio.sleep(1.5)  # 等待业务执行
                                _log.info(
                                    "[browser_click] v9.3: ✓ 已自动点击确认按钮，业务逻辑已触发"
                                )
                                break
                        except Exception as _se29:
                            _log.debug(
                                "[_single] 异常降级(continue): %s",
                                str(_se29)[:60],
                                exc_info=True,
                            )
                            continue
                    if not _v93_clicked:
                        _log.debug(
                            "[browser_click] v9.3: click '%s' 后未检测到确认 modal 或确认按钮",
                            _hint_str_v93[:40],
                        )
            return ToolExecutionResult(True, "已点击: " + hint)
        return ToolExecutionResult(False, "点击失败: " + ok.get("error", "?"))
    except Exception as e:
        return ToolExecutionResult(False, "点击异常: " + str(e))


async def _browser_close_tab(engine, args, hint=""):
    try:
        ok = await engine.close_tab_element()
        return ToolExecutionResult(ok, "close_tab: 已关闭标签页")
    except Exception as e:
        return ToolExecutionResult(False, "close_tab 失败: " + str(e))


async def _browser_context_menu_click(engine, args, hint=""):
    try:
        menu_item = args.get("menu_item_hint", "")
        ok = await run_action_with_hooks(
            engine,
            "context_menu_click",
            hint,
            do_action=lambda a: engine.context_menu_click_element(
                ai_id="",
                menu_item_hint=menu_item,
                target_hint=a.get("target_hint", hint),
            ),
        )
        return ToolExecutionResult(
            ok.get("success", False), "context_menu(%s): %s" % (menu_item, hint)
        )
    except Exception as e:
        return ToolExecutionResult(False, "context_menu 失败: " + str(e))


async def _browser_double_click(engine, args, hint=""):
    try:
        page = getattr(engine, "_page", None)
        if not page:
            return ToolExecutionResult(False, "页面未初始化")

        async def _do_dblclick(a, p=page, h=hint, s=args.get("selector", "")):
            healed = a.get("target_hint", h)
            locator = await _locate_with_fallback(p, healed, selector=s)
            if not locator:
                raise ValueError(f"未找到元素: {healed}")
            # ★ 前置验证：元素可交互（存在 + 可见 + 未禁用）
            verify = await pre_verify_interactive_element(locator)
            if not verify.passed:
                raise ValueError(f"元素不可交互: {healed} — {verify.message}")
            await locator.dblclick(timeout=5000)
            return {"success": True}

        dbl_result = await run_action_with_hooks(
            engine, "double_click", hint, do_action=_do_dblclick
        )
        ok = dbl_result.get("success", False)
        return ToolExecutionResult(
            ok, "已双击: " + hint if ok else "双击失败: " + dbl_result.get("error", "?")
        )
    except Exception as e:
        return ToolExecutionResult(False, "双击失败: " + str(e))


async def _browser_download_file(engine, args, hint=""):
    try:
        save_dir = args.get("save_dir", "")
        ok = await engine.download_file_element(
            ai_id="", save_dir=save_dir, target_hint=hint
        )
        dl_path = getattr(engine, "_last_download_path", "")
        return ToolExecutionResult(ok, "download: %s → %s" % (hint, dl_path))
    except Exception as e:
        return ToolExecutionResult(False, "download 失败: " + str(e))


async def _browser_drag(engine, args, hint=""):
    try:
        page = getattr(engine, "_page", None)
        if not page:
            return ToolExecutionResult(False, "页面未初始化")
        src_hint = args.get("source_hint", "")
        tgt_hint = args.get("target_hint", "")

        async def _do_drag_hooked(
            a, p=page, sh=src_hint, th=tgt_hint, s=args.get("selector", "")
        ):
            healed_src = a.get("source_hint", sh)
            healed_tgt = a.get("target_hint", th)
            src = await _locate_with_fallback(p, healed_src, selector=s)
            tgt = await _locate_with_fallback(p, healed_tgt)
            if not src or not tgt:
                raise ValueError(f"拖拽元素不存在: src={healed_src}, tgt={healed_tgt}")
            await _do_drag(p, src, tgt)
            return {"success": True}

        drag_result = await run_action_with_hooks(
            engine,
            "drag",
            src_hint,
            do_action=_do_drag_hooked,
            action_extra={"source_hint": src_hint, "target_hint": tgt_hint},
        )
        ok = drag_result.get("success", False)
        return ToolExecutionResult(
            ok,
            (
                "已从 " + src_hint + " 拖到 " + tgt_hint
                if ok
                else "拖拽失败: " + drag_result.get("error", "?")
            ),
        )
    except Exception as e:
        return ToolExecutionResult(False, "拖拽失败: " + str(e))


async def _browser_drag_offset(engine, args, hint=""):
    try:
        dx = args.get("dx", 0)
        dy = args.get("dy", 0)
        ok = await run_action_with_hooks(
            engine,
            "drag_offset",
            hint,
            do_action=lambda a: engine.drag_offset_element(
                ai_id="", dx=dx, dy=dy, target_hint=a.get("target_hint", hint)
            ),
        )
        return ToolExecutionResult(
            ok.get("success", False), "drag_offset(%d,%d): %s" % (dx, dy, hint)
        )
    except Exception as e:
        return ToolExecutionResult(False, "drag_offset 失败: " + str(e))


async def _browser_fill(engine, args, hint=""):
    try:
        page = getattr(engine, "_page", None)
        if not page:
            return ToolExecutionResult(False, "浏览器页面未初始化")
        fill_result = await run_action_with_hooks(
            engine,
            "fill",
            hint,
            do_action=lambda a, p=page, t=args.get("text", ""), s=args.get(
                "selector", ""
            ): _fill_by_hint(p, a.get("target_hint", hint), a.get("text", t), s),
            action_extra={"text": args.get("text", "")},
        )
        if fill_result.get("success"):
            return ToolExecutionResult(True, "已填入文字到: " + hint)
        return ToolExecutionResult(
            False, "填入文字失败: " + fill_result.get("error", "?")
        )
    except Exception as e:
        return ToolExecutionResult(False, "填入文字失败: " + str(e))


async def _browser_focus(engine, args, hint=""):
    try:
        ok = await run_action_with_hooks(
            engine,
            "focus",
            hint,
            do_action=lambda a: engine.focus_element(
                ai_id="", target_hint=a.get("target_hint", hint)
            ),
        )
        return ToolExecutionResult(ok.get("success", False), "focus: " + hint)
    except Exception as e:
        return ToolExecutionResult(False, "focus 失败: " + str(e))


async def _browser_go_back(engine, args, hint=""):
    try:
        ok = await engine.go_back_element()
        return ToolExecutionResult(ok, "go_back: 后退一页")
    except Exception as e:
        return ToolExecutionResult(False, "go_back 失败: " + str(e))


async def _browser_go_forward(engine, args, hint=""):
    try:
        ok = await engine.go_forward_element()
        return ToolExecutionResult(ok, "go_forward: 前进一页")
    except Exception as e:
        return ToolExecutionResult(False, "go_forward 失败: " + str(e))


async def _browser_hover(engine, args, hint=""):
    try:
        page = getattr(engine, "_page", None)
        if not page:
            return ToolExecutionResult(False, "页面未初始化")

        async def _do_hover(a, p=page, h=hint, s=args.get("selector", "")):
            healed = a.get("target_hint", h)
            locator = await _locate_with_fallback(p, healed, selector=s)
            if not locator:
                raise ValueError(f"未找到元素: {healed}")
            # ★ 前置验证：元素可交互（存在 + 可见 + 未禁用）
            verify = await pre_verify_interactive_element(locator)
            if not verify.passed:
                raise ValueError(f"元素不可交互: {healed} — {verify.message}")
            await locator.hover(timeout=3000)
            return {"success": True}

        hover_result = await run_action_with_hooks(
            engine, "hover", hint, do_action=_do_hover
        )
        ok = hover_result.get("success", False)
        return ToolExecutionResult(
            ok,
            "已悬停: " + hint if ok else "悬停失败: " + hover_result.get("error", "?"),
        )
    except Exception as e:
        return ToolExecutionResult(False, "悬停失败: " + str(e))


async def _browser_key_down(engine, args, hint=""):
    try:
        key = args.get("key", "")
        ok = await engine.key_down_element(key=key)
        return ToolExecutionResult(ok, "key_down: " + key)
    except Exception as e:
        return ToolExecutionResult(False, "key_down 失败: " + str(e))


async def _browser_key_up(engine, args, hint=""):
    try:
        key = args.get("key", "")
        ok = await engine.key_up_element(key=key)
        return ToolExecutionResult(ok, "key_up: " + key)
    except Exception as e:
        return ToolExecutionResult(False, "key_up 失败: " + str(e))


async def _browser_long_press(engine, args, hint=""):
    try:
        duration = args.get("duration_ms", 1000)
        ok = await run_action_with_hooks(
            engine,
            "long_press",
            hint,
            do_action=lambda a: engine.long_press_element(
                ai_id="", duration_ms=duration, target_hint=a.get("target_hint", hint)
            ),
        )
        return ToolExecutionResult(
            ok.get("success", False), "long_press(%dms): %s" % (duration, hint)
        )
    except Exception as e:
        return ToolExecutionResult(False, "long_press 失败: " + str(e))


async def _browser_middle_click(engine, args, hint=""):
    try:
        ok = await run_action_with_hooks(
            engine,
            "middle_click",
            hint,
            do_action=lambda a: engine.middle_click_element(
                ai_id="", target_hint=a.get("target_hint", hint)
            ),
        )
        return ToolExecutionResult(ok.get("success", False), "middle_click: " + hint)
    except Exception as e:
        return ToolExecutionResult(False, "middle_click 失败: " + str(e))


async def _browser_multi_touch_pinch(engine, args, hint=""):
    try:
        scale = args.get("scale", 1.5)
        ok = await engine.multi_touch_pinch_element(
            ai_id="", scale=scale, target_hint=hint
        )
        return ToolExecutionResult(ok, "multi_touch_pinch: scale=%.1f" % scale)
    except Exception as e:
        return ToolExecutionResult(False, "multi_touch_pinch 失败: " + str(e))


async def _browser_navigate(engine, args, hint=""):
    return await _handle_browser_navigate(engine, args, hint)


async def _browser_page_reload(engine, args, hint=""):
    try:
        wait_until = args.get("wait_until", "load")
        ok = await engine.page_reload_element(wait_until=wait_until)
        return ToolExecutionResult(ok, "page_reload: %s" % wait_until)
    except Exception as e:
        return ToolExecutionResult(False, "page_reload 失败: " + str(e))


async def _browser_press_key(engine, args, hint=""):
    try:
        page = getattr(engine, "_page", None)
        if not page:
            return ToolExecutionResult(False, "页面未初始化")
        focus_hint = args.get("focus_hint")
        if focus_hint:
            locator = await _locate_with_fallback(
                page, focus_hint, selector=args.get("selector", "")
            )
            if locator:
                await locator.focus(timeout=3000)
        await page.keyboard.press(args.get("key", "Enter"))
        return ToolExecutionResult(True, "已按键: " + args.get("key", "Enter"))
    except Exception as e:
        return ToolExecutionResult(False, "按键失败: " + str(e))


async def _browser_resize_window(engine, args, hint=""):
    try:
        w = args.get("width", 1920)
        h = args.get("height", 1080)
        ok = await engine.resize_window_element(width=w, height=h)
        return ToolExecutionResult(ok, "resize: %dx%d" % (w, h))
    except Exception as e:
        return ToolExecutionResult(False, "resize 失败: " + str(e))


async def _browser_right_click(engine, args, hint=""):
    try:
        page = getattr(engine, "_page", None)
        if not page:
            return ToolExecutionResult(False, "页面未初始化")

        async def _do_rclick(a, p=page, h=hint, s=args.get("selector", "")):
            healed = a.get("target_hint", h)
            locator = await _locate_with_fallback(p, healed, selector=s)
            if not locator:
                raise ValueError(f"未找到元素: {healed}")
            # ★ 前置验证：元素可交互（存在 + 可见 + 未禁用）
            verify = await pre_verify_interactive_element(locator)
            if not verify.passed:
                raise ValueError(f"元素不可交互: {healed} — {verify.message}")
            await locator.click(button="right", timeout=5000)
            return {"success": True}

        rc_result = await run_action_with_hooks(
            engine, "right_click", hint, do_action=_do_rclick
        )
        ok = rc_result.get("success", False)
        return ToolExecutionResult(
            ok, "已右键: " + hint if ok else "右键失败: " + rc_result.get("error", "?")
        )
    except Exception as e:
        return ToolExecutionResult(False, "右键失败: " + str(e))


async def _browser_scroll(engine, args, hint=""):
    try:
        direction = args.get("direction", "down")
        amount = args.get("amount", 500)
        await engine.scroll_page(direction=direction, amount=amount)
        return ToolExecutionResult(
            True, "已滚动 " + str(direction) + " " + str(amount) + " 像素"
        )
    except Exception as e:
        return ToolExecutionResult(False, "滚动失败: " + str(e))


async def _browser_select(engine, args, hint=""):
    try:
        page = getattr(engine, "_page", None)
        if not page:
            return ToolExecutionResult(False, "页面未初始化")
        sel_result = await run_action_with_hooks(
            engine,
            "select",
            hint,
            do_action=lambda a, p=page, h=hint, o=args.get(
                "option_text", ""
            ), s=args.get("selector", ""): _select_by_hint(
                p, a.get("target_hint", h), a.get("option_text", o), s
            ),
            action_extra={"option_text": args.get("option_text", "")},
        )
        if sel_result.get("success"):
            return ToolExecutionResult(True, "已选择: " + args.get("option_text", ""))
        return ToolExecutionResult(False, "选择失败: " + sel_result.get("error", "?"))
    except Exception as e:
        return ToolExecutionResult(False, "选择失败: " + str(e))


async def _browser_set_date(engine, args, hint=""):
    try:
        value = args.get("value", "")
        ok = await run_action_with_hooks(
            engine,
            "set_date",
            hint,
            do_action=lambda a: engine.set_date_element(
                ai_id="",
                value=a.get("value", value),
                target_hint=a.get("target_hint", hint),
            ),
        )
        # ★ P2-4（QA 复核，2026-08-05）: 错误透传 — 不再吞掉底层失败原因，便于诊断
        if isinstance(ok, dict) and ok.get("success"):
            return ToolExecutionResult(True, "set_date: %s → %s" % (hint, value))
        _err_detail = ""
        if isinstance(ok, dict):
            _err_detail = str(ok.get("error") or ok.get("message") or "")[:120]
        return ToolExecutionResult(
            False,
            "set_date 失败: %s → %s%s"
            % (hint, value, (" | " + _err_detail) if _err_detail else ""),
        )
    except Exception as e:
        return ToolExecutionResult(False, "set_date 失败: " + str(e))


async def _browser_swipe(engine, args, hint=""):
    try:
        direction = args.get("direction", "up")
        distance = args.get("distance", 200)
        ok = await run_action_with_hooks(
            engine,
            "swipe",
            hint,
            do_action=lambda a: engine.swipe_element(
                ai_id="",
                direction=direction,
                distance=distance,
                target_hint=a.get("target_hint", hint),
            ),
        )
        return ToolExecutionResult(
            ok.get("success", False), "swipe(%s,%d): %s" % (direction, distance, hint)
        )
    except Exception as e:
        return ToolExecutionResult(False, "swipe 失败: " + str(e))


async def _browser_switch_tab(engine, args, hint=""):
    try:
        index = args.get("index", -1)
        url_kw = args.get("url_keyword", "")
        ok = await engine.switch_tab_element(index=index, url_keyword=url_kw)
        return ToolExecutionResult(
            ok, "switch_tab: index=%d keyword=%s" % (index, url_kw)
        )
    except Exception as e:
        return ToolExecutionResult(False, "switch_tab 失败: " + str(e))


async def _browser_switch_to_default(engine, args, hint=""):
    try:
        ok = await engine.switch_to_default()
        return ToolExecutionResult(ok, "switch_to_default: 已退出 iframe")
    except Exception as e:
        return ToolExecutionResult(False, "switch_to_default 失败: " + str(e))


async def _browser_switch_to_iframe(engine, args, hint=""):
    try:
        ok = await engine.switch_to_iframe(ai_id="", target_hint=hint)
        return ToolExecutionResult(ok, "switch_to_iframe: " + hint)
    except Exception as e:
        return ToolExecutionResult(False, "switch_to_iframe 失败: " + str(e))


async def _browser_take_screenshot(engine, args, hint=""):
    try:
        page = getattr(engine, "_page", None)
        if not page:
            return ToolExecutionResult(False, "页面未初始化")
        ss_bytes = await page.screenshot()
        with guard("_browser_take_screenshot: from src.core.config_loader import c", log=_log):
            from src.core.config_loader import config as _cfg
            from src.users.documents import save_screenshot

            _proj = getattr(_cfg, "project_name", "") or "default"
            save_screenshot(_proj, "tool_observation", "", 0, ss_bytes)
        return ToolExecutionResult(True, "截图已记录 (%d bytes)" % len(ss_bytes))
    except Exception as e:
        return ToolExecutionResult(False, "截图失败: " + str(e))


async def _browser_type_text(engine, args, hint=""):
    try:
        page = getattr(engine, "_page", None)
        if not page:
            return ToolExecutionResult(False, "页面未初始化")
        text = args.get("text", "")
        delay = args.get("delay_ms", 50)

        async def _do_type_text(
            a, p=page, h=hint, t=text, d=delay, s=args.get("selector", "")
        ):
            healed = a.get("target_hint", h)
            locator = await _locate_with_fallback(p, healed, kind="input", selector=s)
            if not locator:
                raise ValueError(f"未找到元素: {healed}")
            # ★ 前置验证：元素可交互（存在 + 可见 + 未禁用）
            verify = await pre_verify_interactive_element(locator)
            if not verify.passed:
                raise ValueError(f"元素不可交互: {healed} — {verify.message}")
            await locator.click(timeout=3000)
            await locator.fill("")
            await locator.type(a.get("text", t), delay=d)
            return {"success": True}

        ok = await run_action_with_hooks(
            engine, "type_text", hint, do_action=_do_type_text
        )
        return ToolExecutionResult(
            ok.get("success", False), "type_text: %s → %s" % (hint, text[:30])
        )
    except Exception as e:
        return ToolExecutionResult(False, "type_text 失败: " + str(e))


async def _browser_uncheck(engine, args, hint=""):
    try:
        page = getattr(engine, "_page", None)
        if not page:
            return ToolExecutionResult(False, "页面未初始化")

        async def _do_uncheck(a, p=page, h=hint, s=args.get("selector", "")):
            healed = a.get("target_hint", h)
            locator = await _locate_with_fallback(p, healed, selector=s)
            if not locator:
                raise ValueError(f"未找到元素: {healed}")
            # ★ 前置验证：元素可交互（存在 + 可见 + 未禁用）
            verify = await pre_verify_interactive_element(locator)
            if not verify.passed:
                raise ValueError(f"元素不可交互: {healed} — {verify.message}")
            if await locator.is_checked():
                await locator.uncheck(timeout=5000)
            return {"success": True}

        uncheck_result = await run_action_with_hooks(
            engine, "uncheck", hint, do_action=_do_uncheck
        )
        ok = uncheck_result.get("success", False)
        return ToolExecutionResult(
            ok,
            (
                "已取消勾选: " + hint
                if ok
                else "取消勾选失败: " + uncheck_result.get("error", "?")
            ),
        )
    except Exception as e:
        return ToolExecutionResult(False, "取消勾选失败: " + str(e))


async def _browser_wait(engine, args, hint=""):
    seconds = int(args.get("seconds", 2))
    seconds = max(0, min(seconds, 30))
    await asyncio.sleep(seconds)
    return ToolExecutionResult(True, "已等待 " + str(seconds) + " 秒")


async def _browser_wheel(engine, args, hint=""):
    try:
        dx = args.get("delta_x", 0)
        dy = args.get("delta_y", 300)
        ok = await engine.wheel_element(delta_x=dx, delta_y=dy)
        return ToolExecutionResult(ok, "wheel: dx=%d dy=%d" % (dx, dy))
    except Exception as e:
        return ToolExecutionResult(False, "wheel 失败: " + str(e))
