# -*- coding: utf-8 -*-
"""
标准数据查询能力
================
填写查询条件，点击查询按钮，验证表格返回数据。
仅用于 capability_hint = data_query 类业务意图。
返回 True 表示已成功查询；False 表示回退 AI 路径。
"""

import logging
import re as _re

_log = logging.getLogger(__name__)


async def try_data_query(engine, goal: str) -> bool:
    """
    标准数据查询能力：
    1) 从 goal 提取查询关键词；
    2) 定位查询输入框并填入关键词；
    3) 点击查询按钮；
    4) 验证表格数据变化。
    """
    page = engine._page if engine else None
    if page is None:
        return False
    try:
        # ── 1. 提取查询关键词 ──
        query_kw = _extract_query_keyword(goal)
        if not query_kw:
            _log.info("  [QUERY] 标准能力：未能提取查询关键词")
            return False
        _log.info("  [QUERY] 标准能力：查询关键词='%s'", query_kw)

        # ── 2. 定位查询输入框 ──
        query_input = await _locate_query_input(page)
        if query_input is None:
            _log.info("  [QUERY] 标准能力：未找到查询输入框，回退 AI")
            return False

        # ── 3. 填入关键词并点击查询 ──
        await query_input.click()
        await query_input.fill("")
        await query_input.fill(query_kw)
        _log.info("  [QUERY] 标准能力：已填入查询关键词")

        query_btn = await _locate_query_button(page)
        if query_btn is None:
            await page.keyboard.press("Enter")
            _log.info("  [QUERY] 标准能力：按 Enter 触发查询")
        else:
            await query_btn.click()
            _log.info("  [QUERY] 标准能力：已点击查询按钮")

        # ── 4. 验证查询结果 ──
        # ★ S4-011: 删除冗余 sleep,直接用 wait_for_load_state 确定性等待
        await page.wait_for_timeout(300)  # 让查询事件先触发
        try:
            await page.wait_for_load_state("networkidle", timeout=5000)
        except Exception as _e:
            _log.debug("[query] 非关键异常: %s", _e, exc_info=True)

        if await _verify_query_result(page, query_kw):
            _log.info("  [QUERY] 标准能力：✓ 查询成功")
            return True

        _log.info("  [QUERY] 标准能力：未能确认查询结果，回退 AI")
        return False
    except Exception as e:
        _log.info("  [QUERY] 标准能力异常，回退 AI: %s", str(e)[:120])
        return False


def _extract_query_keyword(goal: str) -> str:
    """从 goal 提取查询关键词。"""
    patterns = [
        r'查询["\':：\s]*([^\s，,]+)',
        r'查找["\':：\s]*([^\s，,]+)',
        r'检索["\':：\s]*([^\s，,]+)',
        r'关键词["\':：\s]*([^\s，,]+)',
        r'关键字["\':：\s]*([^\s，,]+)',
    ]
    for pat in patterns:
        m = _re.search(pat, goal)
        if m and m.group(1):
            return m.group(1).strip()
    # fallback: 取 goal 中的中文词组
    words = _re.findall(r"[\u4e00-\u9fff]{2,6}", goal)
    noise = {
        "完成",
        "实现",
        "验证",
        "测试",
        "测试用例",
        "功能",
        "页面",
        "列表",
        "数据",
        "系统",
        "当前",
        "步骤",
        "打开",
        "登录",
        "确保",
        "进行",
        "检查",
        "导航",
        "进入",
        "应该",
        "可以",
        "查询",
        "查找",
        "检索",
    }
    for w in words:
        if w not in noise:
            return w
    return ""


async def _locate_query_input(page):
    """定位查询输入框。"""
    selectors = [
        'input[placeholder*="查询"]',
        'input[placeholder*="搜索"]',
        'input[placeholder*="查找"]',
        'input[placeholder*="检索"]',
        'input[placeholder*="关键词"]',
        'input[name*="query"]',
        'input[name*="search"]',
        'input[name*="keyword"]',
        'input[type="search"]',
        ".search-input",
        ".query-input",
    ]
    for sel in selectors:
        try:
            loc = page.locator(sel)
            if await loc.count() > 0:
                return loc.first
        except Exception as _e:
            _log.debug("[query] 输入框定位异常: %s", _e, exc_info=True)
            continue
    return None


async def _locate_query_button(page):
    """定位查询按钮。"""
    selectors = [
        'button:has-text("查询")',
        'button:has-text("搜索")',
        'button:has-text("查找")',
        'button:has-text("Search")',
        'button:has-text("查 询")',
        'button:has-text("搜 索")',
        ".search-btn",
        ".query-btn",
        '[class*="search"] button',
    ]
    for sel in selectors:
        try:
            loc = page.locator(sel)
            if await loc.count() > 0:
                return loc.first
        except Exception as _e:
            _log.debug("[query] 按钮定位异常: %s", _e, exc_info=True)
            continue
    return None


async def _verify_query_result(page, query_kw: str) -> bool:
    """验证查询结果。"""
    # 1. 检查表格是否有数据行
    try:
        row_selectors = [
            '[role="row"]',
            "table tbody tr",
            '[role="listitem"]',
            '[class*="table"] [class*="row"]',
            '[class*="list"] [class*="item"]',
        ]
        for sel in row_selectors:
            count = await page.locator(sel).count()
            if count > 0:
                _log.info("  [QUERY] 标准能力：✓ 表格有 %d 行数据", count)
                return True
    except Exception as _e:
        _log.debug("[query] 表格检查异常: %s", _e, exc_info=True)

    # 2. 检查页面是否包含查询关键词
    try:
        body_text = await page.evaluate("() => document.body.innerText || ''") or ""
        if query_kw in body_text:
            _log.info("  [QUERY] 标准能力：✓ 页面包含查询关键词")
            return True
    except Exception as _e:
        _log.debug("[query] body检查异常: %s", _e, exc_info=True)

    # 3. 检查空状态提示（也算查询成功）
    try:
        empty_selectors = [
            "text=暂无数据",
            "text=没有数据",
            "text=未找到",
            "text=无匹配",
            "text=No data",
            ".empty",
            '[class*="empty"]',
        ]
        for sel in empty_selectors:
            if await page.locator(sel).count() > 0:
                _log.info("  [QUERY] 标准能力：✓ 显示空结果（查询功能正常）")
                return True
    except Exception as _e:
        _log.debug("[query] 空状态检查异常: %s", _e, exc_info=True)

    return False
