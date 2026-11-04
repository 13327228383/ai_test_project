# -*- coding: utf-8 -*-
"""
确定性登录能力（Deterministic Login）
=====================================
从 browser_ai.py 拆分出的独立模块。
优先用稳定语义定位完成登录，避免每次登录都退化为 AI 规划。
"""

import asyncio
from src.core.guarded import guard
import logging
from src.pipeline.steps.step4.ai.login import _SELECT_SELECTORS as _select_selectors  # noqa: F401  词表唯一源

_log = logging.getLogger(__name__)


def _is_chromium_error_page(url: str) -> bool:
    """判断当前 URL 是否为 Chromium 内置错误页。

    ★ BUG-1(2026-08-05): 目标不可达/网络超时后 page.url 会变成
    ``chrome-error://chromewebdata/``（或 chrome://error/）。原逻辑把这类
    页面当成"当前已登录"（无 /login 且无密码框 → return True），导致后续在
    错误页上找不到元素 → 归因 HARNESS_ERROR。这里先识别错误页 → 登录失败。
    """
    if not url:
        return False
    u = (url or "").lower()
    return u.startswith("chrome-error://") or u.startswith("chrome://")


async def try_deterministic_login(engine, username: str, password: str) -> bool:
    """标准登录能力：优先用稳定语义定位完成登录，避免每次登录都退化为 AI 规划。"""
    # ★ 根因修复(2026-09-22)：本函数 L48/L51 引用模块级 config 但从未导入
    #   （此前 import 只存在于 BrowserAiLogin.auto_login 内部）→ 一旦走到
    #   "当前页无登录表单"分支即抛 NameError，标准登录能力整体失效。
    from src.core.config_loader import config as _cfg
    # ★ 2026-10-27: 登录阶段的 count() 一律有界（唯一实现见 core/page_ready.count_bounded）。
    #   登录发生在任何用例之前，没有用例级 wait_for 包裹：页面执行上下文不可用时
    #   count() 会永不返回 → 整轮 run 静默，只能被 run 级看门狗杀掉。
    from src.core.page_ready import count_bounded as _count_bounded

    page = engine._page if engine else None
    if page is None:
        return False
    try:
        cur_url = page.url or ""
        # ★ BUG-1: 浏览器错误页（目标不可达/网络超时）→ 不是登录页也不是已登录
        if _is_chromium_error_page(cur_url):
            _log.warning(
                "  [LOGIN] 标准能力：浏览器错误页(%s)，目标不可达 → 登录失败",
                cur_url[:80],
            )
            return False
        has_pwd = await _count_bounded(page, 'input[type="password"]') > 0
        if "/login" not in cur_url.lower() and not has_pwd:
            # ★ 2026-08-08: 当前页面可能不是登录页（如 Django 欢迎页），
            #   尝试导航到常见 admin 登录路径后再判断。使用 config.target_url 做基准
            _log.info("  [LOGIN] 标准能力：当前页无登录表单，尝试检查 admin 路径")
            _base_url = (_cfg.target_url or cur_url).rstrip("/")
            _found_login = False
            # ★ : 跨系统通用性修复——登录路径从配置读取
            _login_path = getattr(_cfg, 'target_login_path', '') or ''
            # ★ 根因修复(2026-09-25) RC-AB: 配置路径恒优先，但仍追加通用候选兜底。
            #   原实现「配置了就只用配置的」——一旦配置路径失效（路径改名/项目配置
            #   缺失）就直接判定"当前已登录"，没有第二次机会。配置优先 + 通用兜底
            #   与 session_login 的语义对齐（同一契约，两处同源）。
            _login_suffixes = (
                ([_login_path] if _login_path else [])
                + [s for s in ("/login", "/admin/login/", "/admin/", "/signin")
                   if s.rstrip("/") != (_login_path or "").rstrip("/")]
            )
            # ★ 单一事实源: 登录页判定复用 browser_core.session_login.has_login_form
            #   （用户名+密码输入框同现），不再用 URL 子串。
            from .browser_core.session_login import (
                has_login_form as _has_login_form,
                DEFAULT_USERNAME_SEL as _DEF_USER_SEL,
                DEFAULT_PASSWORD_SEL as _DEF_PWD_SEL,
            )

            _u_sel = (getattr(_cfg, "login_username_selector", "") or "") or _DEF_USER_SEL
            _p_sel = (getattr(_cfg, "login_password_selector", "") or "") or _DEF_PWD_SEL
            for _suffix in _login_suffixes:
                try:
                    await engine.goto(_base_url + _suffix)
                    await page.wait_for_load_state("domcontentloaded", timeout=10000)
                    await asyncio.sleep(1)
                    # ★ 根因修复(2026-09-25) RC-AB: 原判据 ``"/login" in page.url or _pwd``
                    #   把**任何 URL 含 "/login" 的页面**当成登录页——靶子 fullcov_sut 的
                    #   ``/admin/login/`` 返回 404（error.html，无任何输入框），URL 仍含
                    #   "/login" → 误判"找到登录页" → 循环 break → 真正的 ``/login``
                    #   永不尝试 → 填表失败 → 登录失败 → 全量用例 HARNESS_ERROR。
                    #   通用修复: 登录页的唯一可信信号是**登录表单实际存在**（用户名+密码
                    #   输入框同现）；URL 只是位置，不是形态。
                    _pwd = await _count_bounded(page, _p_sel) > 0
                    if _pwd and await _has_login_form(page, _u_sel, _p_sel):
                        _found_login = True
                        _log.info("  [LOGIN] 标准能力：在 %s 找到登录页", _base_url + _suffix)
                        break
                    _log.info(
                        "  [LOGIN] 标准能力：%s 无登录表单(url=%s, pwd框=%s)，尝试下一路径",
                        _base_url + _suffix, (page.url or "")[:70], _pwd,
                    )
                except Exception as _e:
                    _log.debug("[LOGIN] 尝试 %s 失败: %s", _suffix, str(_e)[:60])
                    continue
            if not _found_login:
                # ★ 根因修复(2026-09-25) RC-AB: 不能仅凭"未找到登录页"就断言"当前已登录"。
                #   原实现无条件 return True —— 404/错误页也被当作"已登录"，
                #   后续在错误页上找元素必然全失败（假 FAIL/假 PASS 温床）。
                #   通用判据: 仍停留在登录页 URL → 登录失败（诚实失败，交由上层兜底）；
                #   否则视为已登录（会话 cookie 仍有效）。
                _u = (page.url or "").lower()
                if "/login" in _u or "signin" in _u:
                    _log.warning(
                        "  [LOGIN] 标准能力：各候选路径均未找到登录表单(url=%s) → 登录失败",
                        (page.url or "")[:90],
                    )
                    return False
                _log.info("  [LOGIN] 标准能力：当前已登录(url=%s)", (page.url or "")[:90])
                return True

        # ★ 通用修复(2026-07-21 v16): 等待页面加载完成后再填表
        #   根因(3层追问):
        #     1. 表层: auto_login 失败 "未登录态无法执行后续步骤"
        #     2. 中层: try_deterministic_login fill 失败(form render 未完成)
        #     3. 根因: worker page 导航后仅122ms就填表，SPA表单未渲染
        #   通用性:
        #     - 跨系统: 所有SPA系统登录表单都有渲染延迟(100ms-3s)
        #     - 跨行业: 所有行业适用
        #     - 覆盖性: 所有调用 try_deterministic_login 的路径受益
        #   自我反驳:
        #     1) 反驳: 加等待浪费时间 → 仅成功时快速返回，失败时才等
        #     2) 反驳: 固定sleep不通用 → 用 wait_for_selector 等待渲染完成
        #     3) 反驳: 主登录为什么不需要 → 主登录有 wait_for_url，这里缺等待
        try:
            await page.wait_for_load_state("domcontentloaded", timeout=10000)
        except Exception as _e:
            _log.debug(
                "[browser_ai_login] domcontentloaded 等待异常: %s", _e, exc_info=True
            )
        # 等待密码输入框可见(SPA表单渲染完成的信号)
        with guard("try_deterministic_login: await page.locator('input[type='pass", log=_log, level=logging.WARNING):
            await page.locator('input[type="password"]').first.wait_for(
                state="visible", timeout=10000
            )
        # ★ 通用修复(2026-07-21 v16): 等待后重新检查是否已登录
        #   根因: localStorage 注入在等待期间生效，页面自动跳转到 /dashboard
        #   但 try_deterministic_login 未重新检查 URL，误报 filled_pass=False
        cur_url = page.url or ""
        # ★ BUG-1: 等待期间落到浏览器错误页 → 登录失败（不可达）
        if _is_chromium_error_page(cur_url):
            _log.warning(
                "  [LOGIN] 标准能力：等待后浏览器错误页(%s) → 登录失败", cur_url[:80]
            )
            return False
        if "/login" not in cur_url.lower():
            _log.info("  [LOGIN] 标准能力：等待后已自动登录(url=%s)", cur_url)
            return True

        user_selectors = [
            'input[name="username"]',
            'input[name="user"]',
            'input[name="account"]',
            'input[placeholder*="用户名"]',
            'input[placeholder*="账号"]',
            'input[placeholder*="用户"]',
            'input[type="text"]',
        ]
        pass_selectors = [
            'input[type="password"]',
            'input[name="password"]',
            'input[placeholder*="密码"]',
        ]
        button_selectors = [
            'button[type="submit"]',
            'button:has-text("登录")',
            'button:has-text("登 录")',
            'button:has-text("Login")',
            'input[type="submit"]',
        ]

        filled_user = False
        for sel in user_selectors:
            if await _count_bounded(page, sel) > 0:
                await page.locator(sel).first.fill(username or "")
                filled_user = True
                break
        # ★ 通用修复 (2026-07-16): 用户名可能是 <select> 下拉框
        if not filled_user:
            for sel in _select_selectors:
                if await _count_bounded(page, sel) > 0:
                    try:
                        await page.locator(sel).first.select_option(username or "")
                        filled_user = True
                        _log.info(
                            "  [LOGIN] 标准能力：已选择用户(select): %s", username
                        )
                    except Exception as _e:
                        _log.debug("[login] select_option 异常: %s", _e, exc_info=True)
                    break
        filled_pass = False
        for sel in pass_selectors:
            if await _count_bounded(page, sel) > 0:
                await page.locator(sel).first.fill(password or "")
                filled_pass = True
                break
        if not (filled_user and filled_pass):
            _log.warning(
                "  [LOGIN] 标准能力：填表失败 filled_user=%s filled_pass=%s url=%s",
                filled_user,
                filled_pass,
                page.url or "",
            )
            return False

        clicked = False
        for sel in button_selectors:
            if await _count_bounded(page, sel) > 0:
                await page.locator(sel).first.click()
                clicked = True
                break
        if not clicked:
            await page.keyboard.press("Enter")

        # ★ 通用修复(2026-07-21 v15): 轮询等待登录成功，替代固定 sleep(2)
        #   根因(3层追问):
        #     1. 表层: auto_login 失败 "未登录态无法执行后续步骤"
        #     2. 中层: try_deterministic_login 只 sleep(2s) 就检查，某SPA系统 SPA 登录跳转 >2s
        #     3. 根因: 固定 sleep 不通用，不同系统登录跳转时间不同（1-10s）
        #   通用性:
        #     - 跨系统: 适用于所有系统（等待密码框消失/URL跳转/登录按钮消失）
        #     - 跨行业: 适用于所有行业的登录场景
        #     - 覆盖性: 适用于所有调用 try_deterministic_login 的路径（auto_login/会话感知）
        #   合理性: 轮询等待成功信号比固定 sleep 更准确，避免过早判定失败
        #   自我反驳:
        #     1) 反驳: 轮询会浪费时间？ → 不会，成功立即返回，最多等10s
        #     2) 反驳: 10s 不够？ → login.py 用 wait_for_url(timeout=10s) 已验证够用
        #     3) 反驳: 密码框可能一直存在？ → 不会，登录成功密码框必消失
        for _wait_i in range(10):  # 最多等10秒
            await asyncio.sleep(1)
            cur_url = page.url or ""
            # ★ BUG-1: 轮询中出现浏览器错误页 → 登录失败（目标不可达/网络超时）
            if _is_chromium_error_page(cur_url):
                _log.warning(
                    "  [LOGIN] 标准能力：轮询中出现浏览器错误页(%s) → 登录失败",
                    cur_url[:80],
                )
                return False
            try:
                has_pwd = await _count_bounded(page, 'input[type="password"]') > 0
            except Exception:
                has_pwd = False
            # 成功条件1: URL 离开 /login 且密码框消失
            if "/login" not in cur_url.lower() and not has_pwd:
                _log.info(
                    "  [LOGIN] 标准能力：登录成功（URL=%s, 等待%d秒）",
                    cur_url,
                    _wait_i + 1,
                )
                return True
            # 成功条件2: 密码框消失（某些SPA登录后URL不变，但表单消失）
            if not has_pwd:
                _log.info(
                    "  [LOGIN] 标准能力：登录成功（密码框消失, 等待%d秒）", _wait_i + 1
                )
                return True
        # 10秒后仍未成功
        try:
            has_login_btn = (
                await _count_bounded(
                    page,
                    'button:has-text("登录"), button:has-text("Login"), button:has-text("登 录")',
                )
                > 0
            )
        except Exception:
            has_login_btn = False
        _log.warning(
            "  [LOGIN] 标准能力：登录10s后仍未成功 URL=%s has_pwd=%s has_login_btn=%s",
            cur_url,
            has_pwd,
            has_login_btn,
        )
        if not has_pwd and not has_login_btn:
            _log.info("  [LOGIN] 标准能力：登录成功（登录表单消失, 兜底判定）")
            return True
        return False
    except Exception as e:
        _log.info("  [LOGIN] 标准能力失败，回退 AI: %s", str(e)[:100])
        return False


class BrowserAiLogin:
    """★ 修复断链：_base.py:83 from browser_ai_login import BrowserAiLogin，
    但本模块原只有 try_deterministic_login 函数，无 BrowserAiLogin 类 → ImportError（被 try 吞成 warning，会话感知失效）。

    会话感知能力：检测登录页 + 自动登录。基于 try_deterministic_login 实现。
    由 _execute_one_intent 的"智能会话感知"段调用（意图非登录类但落到登录页时自动登录）。
    """

    def __init__(self, engine, ai=None):
        self._engine = engine
        self._ai = ai

    async def detect_login_page(self) -> bool:
        """检测当前是否在登录页（URL 含 /login 或存在密码输入框）。"""
        page = getattr(self._engine, "_page", None)
        if page is None:
            return False
        try:
            cur_url = (page.url or "").lower()
            if "/login" in cur_url or "login" in cur_url:
                return True
            from src.core.page_ready import count_bounded as _count_bounded

            has_pwd = await _count_bounded(page, 'input[type="password"]') > 0
            return bool(has_pwd)
        except Exception as e:
            _log.debug("[BrowserAiLogin] detect_login_page 异常: %s", str(e)[:80])
            return False

    async def auto_login(self) -> bool:
        """自动登录：从 config 取账号，调用 try_deterministic_login。"""
        try:
            from src.core.config_loader import config

            username = config.target_username
            password = config.target_password or ""
            return await try_deterministic_login(self._engine, username, password)
        except Exception as e:
            _log.warning("[BrowserAiLogin] auto_login 异常: %s", str(e)[:100])
            return False
