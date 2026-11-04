# -*- coding: utf-8 -*-
"""
标准分页控制能力
================
点击下一页或调整每页条数，验证页面数据变化。
仅用于 capability_hint = pagination_control 类业务意图。
返回 True 表示已成功翻页；False 表示回退 AI 路径。
"""

import logging

_log = logging.getLogger(__name__)


async def try_pagination_control(engine, goal: str) -> bool:
    """
    标准分页控制能力：
    1) 记录翻页前的行数/数据；
    2) 点击下一页按钮；
    3) 验证页面数据变化。
    """
    page = engine._page if engine else None
    if page is None:
        return False
    try:
        # ── 1. 记录翻页前状态 ──
        before_rows = await _count_table_rows(page)
        before_text = await _get_page_text_head(page)
        _log.info("  [PAGE] 标准能力：翻页前 行数=%d", before_rows)

        # ── 2. 点击下一页 ──
        clicked = await _click_next_page(page, goal)
        if not clicked:
            _log.info("  [PAGE] 标准能力：未找到分页按钮，回退 AI")
            return False

        # ── 3. 验证数据变化 ──
        # ★ S4-011: 删除冗余 sleep,直接用 wait_for_load_state 确定性等待
        await page.wait_for_timeout(300)  # 让翻页事件先触发
        try:
            await page.wait_for_load_state("networkidle", timeout=5000)
        except Exception as _e:
            _log.debug("[page] 非关键异常: %s", _e, exc_info=True)

        after_rows = await _count_table_rows(page)
        after_text = await _get_page_text_head(page)
        _log.info("  [PAGE] 标准能力：翻页后 行数=%d", after_rows)

        if await _verify_page_change(before_rows, after_rows, before_text, after_text):
            _log.info("  [PAGE] 标准能力：✓ 翻页成功")
            return True

        _log.info("  [PAGE] 标准能力：未能确认翻页成功，回退 AI")
        return False
    except Exception as e:
        _log.info("  [PAGE] 标准能力异常，回退 AI: %s", str(e)[:120])
        return False


async def _count_table_rows(page) -> int:
    """统计表格行数。"""
    selectors = [
        '[role="row"]',
        "table tbody tr",
        '[role="listitem"]',
        '[class*="table"] [class*="row"]',
        '[class*="list"] [class*="item"]',
        "ul li",
    ]
    for sel in selectors:
        try:
            count = await page.locator(sel).count()
            if count > 0:
                return count
        except Exception as _e:
            _log.debug("[page] 行数统计异常: %s", _e, exc_info=True)
            continue
    return 0


async def _get_page_text_head(page) -> str:
    """获取页面文本前 200 字符（用于对比变化）。"""
    try:
        return (
            await page.evaluate(
                "() => (document.body.innerText || '').substring(0, 200)"
            )
            or ""
        )
    except Exception as _e:
        _log.debug("[page] 文本获取异常: %s", _e, exc_info=True)
        return ""


async def _click_next_page(page, goal: str) -> bool:
    """点击分页按钮（默认下一页，goal 指定其他动作时按 goal）。"""
    if "上一页" in goal or "previous" in goal.lower() or "prev" in goal.lower():
        selectors = [
            '[role="button"]:has-text("上一页")',
            'button:has-text("上一页")',
            'a:has-text("上一页")',
            '[aria-label*="previous"]',
            '[aria-label*="上一页"]',
            ".prev",
        ]
        action = "上一页"
    elif "首页" in goal or "first" in goal.lower():
        selectors = [
            '[role="button"]:has-text("首页")',
            'button:has-text("首页")',
            'a:has-text("首页")',
            '[aria-label*="first"]',
            '[aria-label*="首页"]',
        ]
        action = "首页"
    elif "尾页" in goal or "last" in goal.lower():
        selectors = [
            '[role="button"]:has-text("尾页")',
            'button:has-text("尾页")',
            'a:has-text("尾页")',
            '[aria-label*="last"]',
            '[aria-label*="尾页"]',
        ]
        action = "尾页"
    else:
        # 默认下一页
        selectors = [
            '[role="button"]:has-text("下一页")',
            'button:has-text("下一页")',
            'a:has-text("下一页")',
            '[aria-label*="next"]',
            '[aria-label*="下一页"]',
            ".next",
        ]
        action = "下一页"

    for sel in selectors:
        try:
            loc = page.locator(sel)
            if await loc.count() > 0:
                first = loc.first
                if await first.is_visible():
                    await first.click()
                    _log.info("  [PAGE] 标准能力：已点击 %s", action)
                    return True
        except Exception as _e:
            _log.debug("[page] 分页按钮异常: %s", _e, exc_info=True)
            continue
    return False


async def _verify_page_change(
    before_rows: int, after_rows: int, before_text: str, after_text: str
) -> bool:
    """验证翻页后页面数据变化。"""
    # 1. 行数变化
    if before_rows > 0 and after_rows > 0 and before_rows != after_rows:
        _log.info("  [PAGE] 标准能力：✓ 行数变化 %d → %d", before_rows, after_rows)
        return True

    # 2. 文本内容变化
    if before_text and after_text and before_text != after_text:
        # 至少 30% 不同才算真实变化
        diff_ratio = _text_diff_ratio(before_text, after_text)
        if diff_ratio > 0.3:
            _log.info("  [PAGE] 标准能力：✓ 文本变化率 %.0f%%", diff_ratio * 100)
            return True

    # 3. 行数相同但内容不同（说明数据切换了）
    if before_rows == after_rows and before_rows > 0 and before_text != after_text:
        _log.info("  [PAGE] 标准能力：✓ 行数相同但内容已变化")
        return True

    return False


def _text_diff_ratio(text1: str, text2: str) -> float:
    """计算两个文本的差异比例（0.0~1.0）。"""
    if not text1 or not text2:
        return 0.0
    # 简化：按字符对比，计算不同字符比例
    min_len = min(len(text1), len(text2))
    max_len = max(len(text1), len(text2))
    if max_len == 0:
        return 0.0
    diff_count = 0
    for i in range(min_len):
        if text1[i] != text2[i]:
            diff_count += 1
    diff_count += max_len - min_len
    return diff_count / max_len
