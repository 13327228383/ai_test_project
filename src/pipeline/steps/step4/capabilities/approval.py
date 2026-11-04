# -*- coding: utf-8 -*-
"""
标准审批流操作能力
==================
点击同意/驳回按钮，填写审批意见并提交，验证状态变更。
仅用于 capability_hint = approval_flow 类业务意图。
返回 True 表示已成功审批；False 表示回退 AI 路径。
"""

import logging
import re as _re

_log = logging.getLogger(__name__)


async def try_approval_flow(engine, goal: str) -> bool:
    """
    标准审批流能力：
    1) 从 goal 判断审批动作（同意/驳回）；
    2) 定位审批按钮并点击；
    3) 填写审批意见；
    4) 提交并验证状态变更。
    """
    page = engine._page if engine else None
    if page is None:
        return False
    try:
        # ── 1. 判断审批动作 ──
        action_type, opinion = _parse_approval_action(goal)
        _log.info(
            "  [APPROVAL] 标准能力：动作=%s 意见=%s",
            action_type,
            opinion[:30] if opinion else "默认",
        )

        # ── 2. 定位并点击审批按钮 ──
        btn = await _locate_approval_button(page, action_type)
        if btn is None:
            _log.info("  [APPROVAL] 标准能力：未找到审批按钮，回退 AI")
            return False
        await btn.click()
        _log.info("  [APPROVAL] 标准能力：已点击 %s 按钮", action_type)
        # ★ S4-011: 确定性等待替代 sleep — 等待审批弹窗/表单出现
        await page.wait_for_timeout(300)

        # ── 3. 填写审批意见 ──
        filled = await _fill_opinion(page, opinion)
        if not filled:
            _log.info("  [APPROVAL] 标准能力：未找到意见输入框，尝试直接提交")

        # ── 4. 提交审批 ──
        submitted = await _submit_approval(page)
        if not submitted:
            _log.info("  [APPROVAL] 标准能力：未找到提交按钮，回退 AI")
            return False

        # ── 5. 验证状态变更 ──
        # ★ S4-011: 删除冗余 sleep,直接用 wait_for_load_state 确定性等待
        await page.wait_for_timeout(300)  # 让提交事件先触发
        try:
            await page.wait_for_load_state("networkidle", timeout=5000)
        except Exception as _e:
            _log.debug("[approval] 非关键异常: %s", _e, exc_info=True)

        if await _verify_approval_success(page, action_type):
            _log.info("  [APPROVAL] 标准能力：✓ 审批成功")
            return True

        _log.info("  [APPROVAL] 标准能力：未能确认审批成功，回退 AI")
        return False
    except Exception as e:
        _log.info("  [APPROVAL] 标准能力异常，回退 AI: %s", str(e)[:120])
        return False


def _parse_approval_action(goal: str) -> tuple:
    """解析审批动作类型和意见。返回 (action_type, opinion)。"""
    goal_lower = goal.lower()
    if any(k in goal for k in ("驳回", "拒绝", "reject", "拒绝")):
        action_type = "reject"
    else:
        action_type = "approve"

    # 提取审批意见
    opinion = ""
    opinion_patterns = [
        r'意见["\':：\s]*([^\s，,]+)',
        r'备注["\':：\s]*([^\s，,]+)',
        r'说明["\':：\s]*([^\s，,]+)',
    ]
    for pat in opinion_patterns:
        m = _re.search(pat, goal)
        if m and m.group(1):
            opinion = m.group(1).strip()
            break

    if not opinion:
        opinion = "同意" if action_type == "approve" else "驳回"

    return action_type, opinion


async def _locate_approval_button(page, action_type: str):
    """定位审批按钮。"""
    if action_type == "approve":
        selectors = [
            'button:has-text("同意")',
            'button:has-text("通过")',
            'button:has-text("审批通过")',
            'button:has-text("Approve")',
            'a:has-text("同意")',
            'a:has-text("通过")',
        ]
    else:
        selectors = [
            'button:has-text("驳回")',
            'button:has-text("拒绝")',
            'button:has-text("Reject")',
            'a:has-text("驳回")',
            'a:has-text("拒绝")',
        ]
    for sel in selectors:
        try:
            loc = page.locator(sel)
            if await loc.count() > 0:
                return loc.first
        except Exception as _e:
            _log.debug("[approval] 按钮定位异常: %s", _e, exc_info=True)
            continue
    return None


async def _fill_opinion(page, opinion: str) -> bool:
    """填写审批意见。"""
    selectors = [
        'textarea[placeholder*="意见"]',
        'textarea[placeholder*="备注"]',
        'textarea[placeholder*="说明"]',
        'textarea[name*="opinion"]',
        'textarea[name*="remark"]',
        'textarea[name*="comment"]',
        'input[placeholder*="意见"]',
        'input[placeholder*="备注"]',
    ]
    for sel in selectors:
        try:
            loc = page.locator(sel)
            if await loc.count() > 0:
                await loc.first.click()
                await loc.first.fill("")
                await loc.first.fill(opinion)
                _log.info("  [APPROVAL] 标准能力：已填写意见 '%s'", opinion[:30])
                return True
        except Exception as _e:
            _log.debug("[approval] 意见填写异常: %s", _e, exc_info=True)
            continue
    return False


async def _submit_approval(page) -> bool:
    """提交审批。"""
    selectors = [
        'button:has-text("提交")',
        'button:has-text("确认")',
        'button:has-text("确定")',
        'button:has-text("Submit")',
        'button:has-text("OK")',
        'button[type="submit"]',
    ]
    for sel in selectors:
        try:
            loc = page.locator(sel)
            if await loc.count() > 0:
                await loc.first.click()
                _log.info("  [APPROVAL] 标准能力：已点击提交")
                return True
        except Exception as _e:
            _log.debug("[approval] 提交异常: %s", _e, exc_info=True)
            continue
    return False


async def _verify_approval_success(page, action_type: str) -> bool:
    """验证审批成功。"""
    try:
        body_text = await page.evaluate("() => document.body.innerText || ''") or ""
    except Exception as _e:
        _log.debug("[approval] body检查异常: %s", _e, exc_info=True)
        body_text = ""

    success_markers = (
        "审批成功",
        "操作成功",
        "提交成功",
        "success",
        "approved",
        "rejected",
    )
    if any(m.lower() in body_text.lower() for m in success_markers):
        _log.info("  [APPROVAL] 标准能力：✓ 成功提示命中")
        return True

    # 检查状态变更（如"已审批"、"已通过"、"已驳回"）
    status_markers = ("已审批", "已通过", "已驳回", "审批中", "processed")
    if any(m in body_text for m in status_markers):
        _log.info("  [APPROVAL] 标准能力：✓ 状态变更命中")
        return True

    error_markers = ("审批失败", "操作失败", "错误", "error", "fail")
    if any(m.lower() in body_text.lower() for m in error_markers):
        _log.info("  [APPROVAL] 标准能力：检测到错误提示")
        return False

    return False
