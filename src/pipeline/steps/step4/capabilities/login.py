# -*- coding: utf-8 -*-
"""
标准登录能力（账号密码登录）
============================
用 Playwright 顺序推断用户名/密码输入框，填写后点击登录按钮并验证跳转。
仅用于 capability_hint = login_with_password 类业务意图。
返回 True 表示已成功登录；False 表示回退 AI 路径。
"""

import logging
import re as _re

_log = logging.getLogger(__name__)


async def try_login_with_password(engine, goal: str) -> bool:
    """
    标准登录能力：
    1) 从 goal/config 提取用户名和密码；
    2) 用语义选择器顺序推断用户名/密码输入框；
    3) 填写并点击登录按钮；
    4) 验证 URL 跳转或错误提示消失。

    返回 True 表示登录成功；False 表示回退 AI 路径。
    """
    page = engine._page if engine else None
    if page is None:
        return False
    try:
        # ── 1. 提取用户名/密码 ──
        username, password = await _extract_credentials(goal, engine)
        if not username or not password:
            _log.info("  [LOGIN] 标准能力：未能获取用户名/密码")
            return False
        _log.info(
            "  [LOGIN] 标准能力：用户=%s 密码=***（长度%d）", username, len(password)
        )

        # ── 2. 顺序推断输入框 ──
        user_loc, pwd_loc = await _locate_input_fields(page)
        if user_loc is None or pwd_loc is None:
            _log.info("  [LOGIN] 标准能力：未能定位用户名/密码输入框，回退 AI")
            return False

        # ── 3. 填写并点击登录 ──
        # ★ 通用修复: 区分 input 和 select 元素
        _user_tag = await user_loc.evaluate("el => el.tagName")
        if _user_tag and _user_tag.lower() == "select":
            await user_loc.select_option(username)
            _log.info("  [LOGIN] 标准能力：已选择用户(select)：%s", username)
        else:
            await user_loc.click()
            await user_loc.fill("")
            await user_loc.fill(username)
            _log.info("  [LOGIN] 标准能力：已填写用户名")

        await pwd_loc.click()
        await pwd_loc.fill("")
        await pwd_loc.fill(password)
        _log.info("  [LOGIN] 标准能力：已填写密码")

        login_btn = await _locate_login_button(page)
        if login_btn is None:
            _log.info("  [LOGIN] 标准能力：未找到登录按钮，尝试按 Enter")
            await page.keyboard.press("Enter")
        else:
            await login_btn.click()
            _log.info("  [LOGIN] 标准能力：已点击登录按钮")

        # ── 4. 验证登录结果 ──
        # ★ S4-011: 删除冗余 sleep(1.5),直接用 wait_for_load_state 确定性等待
        await page.wait_for_timeout(300)  # 让点击事件先触发
        try:
            await page.wait_for_load_state("networkidle", timeout=5000)
        except Exception as _e:
            _log.debug("[login] 非关键异常: %s", _e, exc_info=True)

        if await _verify_login_success(page):
            _log.info("  [LOGIN] 标准能力：✓ 登录成功")
            return True

        _log.info("  [LOGIN] 标准能力：未能确认登录成功，回退 AI")
        return False
    except Exception as e:
        _log.info("  [LOGIN] 标准能力异常，回退 AI: %s", str(e)[:120])
        return False


async def _extract_credentials(goal: str, engine) -> tuple:
    """从 goal 文本和 config 提取用户名/密码。"""
    username = ""
    password = ""

    # 1. 从 goal 文本提取（如 "admin / haorizi@Pwd" 格式）
    cred_patterns = [
        r'账号["\':：\s]*([^\s/，,]+)["\s/，,]*密码["\':：\s]*([^\s，,]+)',
        r'用户名["\':：\s]*([^\s/，,]+)["\s/，,]*密码["\':：\s]*([^\s，,]+)',
        r"([A-Za-z0-9_]+)\s*[/／]\s*([^\s，,]+)",
    ]
    for pat in cred_patterns:
        m = _re.search(pat, goal)
        if m and m.group(1) and m.group(2):
            username = m.group(1).strip()
            password = m.group(2).strip()
            break

    # 2. 从 config 读取默认值兜底
    if not username or not password:
        try:
            from src.core.config_loader import config as _cfg

            username = str(getattr(_cfg, "target_username", "") or "") or username
            password = str(getattr(_cfg, "target_password", "") or "") or password
        except Exception as _e:
            _log.debug("[login] config 读取异常: %s", _e, exc_info=True)

    return username, password


async def _locate_input_fields(page) -> tuple:
    """顺序推断用户名/密码输入框，返回 (user_loc, pwd_loc)。

    策略优先级：
    1. input[type="password"] 定位密码框 + 同表单第一个非密码 input/select 为用户名框
    2. get_by_role("textbox") 前两个按 DOM 顺序
    3. placeholder 含"用户名/账号/username" + placeholder 含"密码/password"
    4. select 下拉框（通用修复：部分系统用 select 选择用户）
    """
    # 策略1：通过 type=password 定位密码框（最可靠）
    try:
        pwd_inputs = page.locator('input[type="password"]')
        if await pwd_inputs.count() > 0:
            pwd_loc = pwd_inputs.first
            # 同表单内找用户名框（input 或 select）
            form = page.locator("form").first
            user_candidates = form.locator(
                'input:not([type="password"]):not([type="hidden"]):not([type="submit"]):not([type="button"])'
            )
            if await user_candidates.count() > 0:
                return user_candidates.first, pwd_loc
            # ★ 退化：form 内找 select 元素
            select_candidates = form.locator("select")
            if await select_candidates.count() > 0:
                return select_candidates.first, pwd_loc
            # 退化：全局找非密码 input
            user_candidates = page.locator(
                'input:not([type="password"]):not([type="hidden"]):not([type="submit"]):not([type="button"])'
            )
            if await user_candidates.count() > 0:
                return user_candidates.first, pwd_loc
            # ★ 退化：全局找 select 元素
            select_candidates = page.locator("select")
            if await select_candidates.count() > 0:
                return select_candidates.first, pwd_loc
    except Exception as _e:
        _log.debug("[login] 策略1异常: %s", _e, exc_info=True)

    # 策略2：get_by_role("textbox") 前2个
    try:
        textboxes = page.get_by_role("textbox")
        count = await textboxes.count()
        if count >= 2:
            return textboxes.nth(0), textboxes.nth(1)
    except Exception as _e:
        _log.debug("[login] 策略2异常: %s", _e, exc_info=True)

    # 策略3：placeholder 关键词匹配
    try:
        user_selectors = [
            'input[placeholder*="用户名"]',
            'input[placeholder*="账号"]',
            'input[placeholder*="account"]',
            'input[placeholder*="username"]',
            'input[name*="user"]',
            'input[name*="account"]',
        ]
        pwd_selectors = [
            'input[placeholder*="密码"]',
            'input[placeholder*="password"]',
            'input[name*="pwd"]',
            'input[name*="password"]',
        ]
        user_loc = None
        for sel in user_selectors:
            loc = page.locator(sel)
            if await loc.count() > 0:
                user_loc = loc.first
                break
        pwd_loc = None
        for sel in pwd_selectors:
            loc = page.locator(sel)
            if await loc.count() > 0:
                pwd_loc = loc.first
                break
        if user_loc and pwd_loc:
            return user_loc, pwd_loc
    except Exception as _e:
        _log.debug("[login] 策略3异常: %s", _e, exc_info=True)

    return None, None


async def _locate_login_button(page):
    """定位登录按钮。"""
    btn_selectors = [
        '[role="button"]:has-text("登录")',
        '[role="button"]:has-text("登 录")',
        '[role="button"]:has-text("Login")',
        '[role="button"]:has-text("Sign in")',
        'button[type="submit"]',
        'input[type="submit"]',
        'button:has-text("登录")',
        'button:has-text("Login")',
        'a:has-text("登录")',
        'button[aria-label*="登录"]',
    ]
    for sel in btn_selectors:
        try:
            loc = page.locator(sel)
            if await loc.count() > 0:
                return loc.first
        except Exception as _e:
            _log.debug("[login] 按钮定位异常: %s", _e, exc_info=True)
            continue
    return None


async def _verify_login_success(page) -> bool:
    """验证登录成功：URL 变化 / 无错误提示 / 首页关键元素出现。"""
    # ★ 2026-10-27: 登录前/后阶段的 count() 一律有界（唯一实现见
    #   core/page_ready.count_bounded）。登录阶段位于任何用例之前，
    #   没有用例级 wait_for 包裹；执行上下文不可用时 count() 会永不返回。
    from src.core.page_ready import count_bounded as _count_bounded

    # 1. 检查是否有错误提示（登录失败标志）
    try:
        error_selectors = [
            "text=用户名或密码错误",
            "text=密码错误",
            "text=账号不存在",
            "text=登录失败",
            "text=Login failed",
            "text=Invalid",
            '[role="alert"]',
            ".error-message",
        ]
        for sel in error_selectors:
            if await _count_bounded(page, sel) > 0:
                _log.info("  [LOGIN] 标准能力：检测到错误提示，登录失败")
                return False
    except Exception as _e:
        _log.debug("[login] 错误检查异常: %s", _e, exc_info=True)

    # 2. 检查 URL 是否变化（登录页 URL 通常含 /login）
    try:
        current_url = page.url or ""
        if "/login" not in current_url and "login" not in current_url.lower():
            _log.info("  [LOGIN] 标准能力：✓ URL 已离开登录页 → %s", current_url[:80])
            return True
    except Exception as _e:
        _log.debug("[login] URL检查异常: %s", _e, exc_info=True)

    # 3. 检查首页关键元素（侧边栏/用户头像/退出按钮等）
    try:
        home_selectors = [
            "nav",
            '[role="navigation"]',
            '[class*="avatar"]',
            '[class*="user-info"]',
            'button:has-text("退出")',
            'button:has-text("登出")',
            'button:has-text("Logout")',
            'a:has-text("退出")',
            'a:has-text("登出")',
            '[role="button"]:has-text("退出")',
            '[role="button"]:has-text("登出")',
            '[role="link"]:has-text("退出")',
            '[role="link"]:has-text("登出")',
        ]
        for sel in home_selectors:
            if await _count_bounded(page, sel) > 0:
                _log.info("  [LOGIN] 标准能力：✓ 检测到首页元素 %s", sel[:50])
                return True
    except Exception as _e:
        _log.debug("[login] 首页元素检查异常: %s", _e, exc_info=True)

    return False
