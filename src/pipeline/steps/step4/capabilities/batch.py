# -*- coding: utf-8 -*-
"""
标准列表批量操作能力
====================
勾选多行，点击批量按钮并验证批量结果。
仅用于 capability_hint = list_batch_action 类业务意图。
返回 True 表示已成功批量操作；False 表示回退 AI 路径。
"""

import logging

_log = logging.getLogger(__name__)


async def try_list_batch_action(engine, goal: str) -> bool:
    """
    标准批量操作能力：
    1) 勾选列表行（全选或逐行勾选）；
    2) 定位批量操作按钮并点击；
    3) 处理确认弹窗（若有）；
    4) 验证批量结果。
    """
    page = engine._page if engine else None
    if page is None:
        return False
    try:
        # ── 1. 勾选列表行 ──
        selected = await _select_rows(page)
        if not selected:
            _log.info("  [BATCH] 标准能力：未能勾选任何行，回退 AI")
            return False
        _log.info("  [BATCH] 标准能力：已勾选行")

        # ── 2. 定位批量按钮 ──
        batch_btn = await _locate_batch_button(page, goal)
        if batch_btn is None:
            _log.info("  [BATCH] 标准能力：未找到批量按钮，回退 AI")
            return False
        await batch_btn.click()
        _log.info("  [BATCH] 标准能力：已点击批量按钮")

        # ★ S4-011: 确定性等待替代 sleep — 等待批量操作弹窗/反馈出现
        try:
            await page.wait_for_load_state("networkidle", timeout=3000)
        except Exception as _e:
            _log.debug("[batch] 等待弹窗网络空闲异常: %s", _e, exc_info=True)

        # ── 3. 处理确认弹窗 ──
        await _handle_confirm_dialog(page)

        # ── 4. 验证批量结果 ──
        # ★ S4-011: 删除冗余 sleep,直接用 wait_for_load_state 确定性等待
        try:
            await page.wait_for_load_state("networkidle", timeout=5000)
        except Exception as _e:
            _log.debug("[batch] 非关键异常: %s", _e, exc_info=True)

        if await _verify_batch_result(page):
            _log.info("  [BATCH] 标准能力：✓ 批量操作成功")
            return True

        _log.info("  [BATCH] 标准能力：未能确认批量结果，回退 AI")
        return False
    except Exception as e:
        _log.info("  [BATCH] 标准能力异常，回退 AI: %s", str(e)[:120])
        return False


async def _select_rows(page) -> bool:
    """勾选列表行（优先全选，失败则逐行勾选前3行）。"""
    # 策略1：全选 checkbox（语义/ARIA 优先，禁止框架类名硬编码）
    select_all_selectors = [
        'th input[type="checkbox"]',
        'thead input[type="checkbox"]',
        '[role="columnheader"] input[type="checkbox"]',
        'input[class*="select-all"]',
        'input[class*="checkAll"]',
    ]
    for sel in select_all_selectors:
        try:
            loc = page.locator(sel)
            if await loc.count() > 0:
                checked = await loc.first.is_checked()
                if not checked:
                    await loc.first.click()
                    _log.info("  [BATCH] 标准能力：已点击全选")
                return True
        except Exception as _e:
            _log.debug("[batch] 全选异常: %s", _e, exc_info=True)
            continue

    # 策略2：逐行勾选前3行（语义/ARIA 优先，禁止框架类名硬编码）
    row_checkbox_selectors = [
        'tbody tr input[type="checkbox"]',
        '[role="row"] input[type="checkbox"]',
        '[class*="row"] input[type="checkbox"]',
    ]
    for sel in row_checkbox_selectors:
        try:
            loc = page.locator(sel)
            count = await loc.count()
            if count > 0:
                checked_count = 0
                for i in range(min(count, 3)):
                    try:
                        checkbox = loc.nth(i)
                        checked = await checkbox.is_checked()
                        if not checked:
                            await checkbox.click()
                            checked_count += 1
                    except Exception as _e:
                        _log.debug("[batch] 行勾选异常: %s", _e, exc_info=True)
                        continue
                if checked_count > 0:
                    return True
        except Exception as _e:
            _log.debug("[batch] 行选择异常: %s", _e, exc_info=True)
            continue
    return False


async def _locate_batch_button(page, goal: str):
    """定位批量操作按钮（从 goal 推断操作类型）。"""
    # 从 goal 提取操作关键词
    if "删除" in goal or "移除" in goal:
        keywords = ["批量删除", "删除"]
    elif "导出" in goal:
        keywords = ["批量导出", "导出"]
    elif "审批" in goal or "通过" in goal:
        keywords = ["批量审批", "批量通过", "审批"]
    else:
        keywords = ["批量"]

    for kw in keywords:
        selectors = [
            'button:has-text("%s")' % kw,
            'a:has-text("%s")' % kw,
            '[class*="batch"] button',
            '[class*="batch"] a',
        ]
        for sel in selectors:
            try:
                loc = page.locator(sel)
                if await loc.count() > 0:
                    return loc.first
            except Exception as _e:
                _log.debug("[batch] 按钮定位异常: %s", _e, exc_info=True)
                continue
    return None


async def _handle_confirm_dialog(page):
    """处理确认弹窗。"""
    confirm_selectors = [
        'button:has-text("确定")',
        'button:has-text("确认")',
        'button:has-text("是")',
        'button:has-text("OK")',
        'button:has-text("Yes")',
    ]
    for sel in confirm_selectors:
        try:
            loc = page.locator(sel)
            # ★ S4-011: 等待弹窗渲染稳定（确定性可见性等待）
            await loc.first.wait_for(state="visible", timeout=2000)
            if await loc.first.is_visible():
                await loc.first.click()
                _log.info("  [BATCH] 标准能力：已确认弹窗")
                return
        except Exception as _e:
            _log.debug("[batch] 确认弹窗异常: %s", _e, exc_info=True)
            continue


async def _verify_batch_result(page) -> bool:
    """验证批量操作结果。"""
    try:
        body_text = await page.evaluate("() => document.body.innerText || ''") or ""
    except Exception as _e:
        _log.debug("[batch] body检查异常: %s", _e, exc_info=True)
        body_text = ""

    success_markers = ("成功", "完成", "success", "done", "completed")
    if any(m.lower() in body_text.lower() for m in success_markers):
        _log.info("  [BATCH] 标准能力：✓ 成功提示命中")
        return True

    error_markers = ("失败", "错误", "error", "fail")
    if any(m.lower() in body_text.lower() for m in error_markers):
        _log.info("  [BATCH] 标准能力：检测到错误提示")
        return False

    # 检查选中状态是否清空（批量操作后通常取消选中）
    try:
        checked = page.locator('tbody input[type="checkbox"]:checked')
        if await checked.count() == 0:
            _log.info("  [BATCH] 标准能力：✓ 选中状态已清空")
            return True
    except Exception as _e:
        _log.debug("[batch] 选中状态检查异常: %s", _e, exc_info=True)

    return False
