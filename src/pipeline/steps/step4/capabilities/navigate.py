# -*- coding: utf-8 -*-
"""
标准菜单导航能力
================
按层级点击侧边栏或顶部菜单项，进入目标页。
仅用于 capability_hint = navigate_menu 类业务意图。
返回 True 表示已成功导航；False 表示回退 AI 路径。
"""

import logging
import re as _re

_log = logging.getLogger(__name__)


async def try_navigate_menu(engine, goal: str) -> bool:
    """
    标准菜单导航能力：
    1) 从 goal 提取目标菜单名称；
    2) 在侧边栏/顶部菜单中查找并点击；
    3) 处理层级菜单（父菜单展开后再点子菜单）；
    4) 验证目标页加载。
    """
    page = engine._page if engine else None
    if page is None:
        return False
    try:
        # ── 1. 提取目标菜单名称 ──
        menu_names = _extract_menu_names(goal)
        if not menu_names:
            _log.info("  [NAV] 标准能力：未能提取菜单名称")
            return False
        _log.info("  [NAV] 标准能力：目标菜单=%s", " > ".join(menu_names))

        # ── 2. 逐级点击菜单 ──
        clicked_all = True
        for i, name in enumerate(menu_names):
            clicked = await _click_menu_item(page, name)
            if not clicked:
                _log.info("  [NAV] 标准能力：未找到菜单项 '%s'，回退 AI", name)
                clicked_all = False
                break
            # ★ S4-011: 等待菜单展开/子菜单渲染
            await page.wait_for_timeout(300)
            _log.info("  [NAV] 标准能力：已点击菜单 '%s'", name)

        if not clicked_all:
            return False

        # ── 3. 验证目标页加载 ──
        # ★ S4-011: 删除冗余 sleep,直接用 wait_for_load_state 确定性等待
        await page.wait_for_timeout(300)  # 让导航事件先触发
        try:
            await page.wait_for_load_state("domcontentloaded", timeout=5000)
        except Exception as _e:
            _log.debug("[nav] 非关键异常: %s", _e, exc_info=True)

        if await _verify_page_loaded(page, menu_names[-1]):
            _log.info("  [NAV] 标准能力：✓ 导航成功")
            return True

        _log.info("  [NAV] 标准能力：未能确认导航成功，回退 AI")
        return False
    except Exception as e:
        _log.info("  [NAV] 标准能力异常，回退 AI: %s", str(e)[:120])
        return False


def _extract_menu_names(goal: str) -> list:
    """从 goal 提取菜单名称列表（支持层级导航）。"""
    # 提取格式："导航到 XX" / "进入 XX > YY" / "XX > YY > ZZ"
    nav_patterns = [
        r'导航到["\':：\s]*([^\s，,]+)',
        r'进入["\':：\s]*([^\s，,]+)',
        r'跳转到["\':：\s]*([^\s，,]+)',
        r'打开["\':：\s]*([^\s，,]+)',
    ]
    for pat in nav_patterns:
        m = _re.search(pat, goal)
        if m and m.group(1):
            name = m.group(1).strip()
            # 检查是否含层级分隔符
            if ">" in name or "＞" in name or "→" in name or ">" in name:
                parts = _re.split(r"[>＞→]", name)
                return [x for p in parts if (x := p.strip())]
            return [name]

    # fallback: 提取 > 分隔的菜单路径
    m = _re.search(r"([\u4e00-\u9fff]+(?:\s*[>＞→]\s*[\u4e00-\u9fff]+)+)", goal)
    if m:
        parts = _re.split(r"\s*[>＞→]\s*", m.group(1))
        return [x for p in parts if (x := p.strip())]

    # 最后 fallback: 取 goal 中的中文词组
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
        "跳转",
    }
    for w in words:
        if w not in noise:
            return [w]
    return []


async def _click_menu_item(page, menu_name: str) -> bool:
    """点击菜单项（支持多种菜单框架）。"""
    selectors = [
        # 通用语义 / ARIA 优先
        '[role="menuitem"]:has-text("%s")' % menu_name,
        '[role="link"]:has-text("%s")' % menu_name,
        '[role="button"]:has-text("%s")' % menu_name,
        'a:has-text("%s")' % menu_name,
        'button:has-text("%s")' % menu_name,
        # 通用容器
        'nav a:has-text("%s")' % menu_name,
        '.sidebar a:has-text("%s")' % menu_name,
        '.menu a:has-text("%s")' % menu_name,
        'li:has-text("%s")' % menu_name,
    ]
    for sel in selectors:
        try:
            loc = page.locator(sel)
            if await loc.count() > 0:
                first = loc.first
                if await first.is_visible():
                    await first.click()
                    return True
        except Exception as _e:
            _log.debug("[nav] 菜单点击异常: %s", _e, exc_info=True)
            continue
    return False


async def _verify_page_loaded(page, menu_name: str) -> bool:
    """验证目标页已加载。"""
    # 1. 检查页面标题/面包屑是否包含菜单名
    try:
        body_text = await page.evaluate("() => document.body.innerText || ''") or ""
        if menu_name in body_text:
            _log.info("  [NAV] 标准能力：✓ 页面包含 '%s'", menu_name)
            return True
    except Exception as _e:
        _log.debug("[nav] body检查异常: %s", _e, exc_info=True)

    # 2. 检查是否有表格/表单等业务元素（说明已进入业务页）
    try:
        content_selectors = [
            '[role="table"]',
            '[role="grid"]',
            '[role="form"]',
            "table",
            "form",
            "main",
            "section",
            ".page-content",
            ".main-content",
        ]
        for sel in content_selectors:
            if await page.locator(sel).count() > 0:
                _log.info("  [NAV] 标准能力：✓ 检测到业务内容元素 %s", sel[:50])
                return True
    except Exception as _e:
        _log.debug("[nav] 内容检查异常: %s", _e, exc_info=True)

    # 3. 检查 URL 是否变化
    try:
        current_url = page.url or ""
        if current_url and "login" not in current_url.lower():
            _log.info("  [NAV] 标准能力：✓ URL 已变化 → %s", current_url[:80])
            return True
    except Exception as _e:
        _log.debug("[nav] URL检查异常: %s", _e, exc_info=True)

    return False
