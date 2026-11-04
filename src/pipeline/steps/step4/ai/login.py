# -*- coding: utf-8 -*-
"""
BrowserAi 登录 mixin

职责：
- login — 智能登录入口
- _try_deterministic_login — 确定性登录（基于配置）
"""

import logging
from src.core.guarded import guard

from src.core.config_loader import config

_log = logging.getLogger(__name__)


class _BrowserAiLoginMixin:
    """登录能力。"""

    async def login(self) -> bool:
        """智能登录入口：先尝试确定性登录，失败则回退 AI 登录。"""
        # ★ 2026-09-28 通用回归：靶子 auth.type=='none'（如 smartshop Flask 版无登录
        #   体系）→ 直通无需登录。此前只看 TARGET_USERNAME/PASSWORD 非空判"需登录"，
        #   user_credentials 残留的全局 TARGET_PASSWORD(project='*') 会让 auth.none
        #   靶子被误判需登录 → 逐路径探测 → 404 → BLOCKED（与 empirical 口径对齐）。
        if getattr(config, "target_auth_none", False):
            _log.info("[Login] 靶子无需登录 (auth.type=none) → 跳过登录")
            return True
        with guard("login: if await self._try_deterministic_log", log=_log, level=logging.WARNING):
            if await self._try_deterministic_login():
                return True

        with guard("login: from src.pipeline.steps.step4.browse", log=_log, level=logging.WARNING):
            from src.pipeline.steps.step4.browser_ai_login import BrowserAiLogin

            bal = BrowserAiLogin(self._engine, self)
            if await bal.auto_login():
                return True

        return False

    async def _try_deterministic_login(self) -> bool:
        """基于配置的确定性登录（已知用户名密码）。"""
        page = self._engine._page if self._engine else None
        if not page:
            return False

        # ★ 2026-10-27: 登录阶段 count()/content() 一律有界（唯一实现见
        #   core/page_ready 的 count_bounded/content_bounded）。
        #   登录发生在任何用例之前，没有用例级 wait_for 包裹：页面执行上下文不可用时
        #   这两个 API 会永不返回 → 整轮 run 静默，只能被 run 级看门狗杀掉。
        from src.core.page_ready import (
            content_bounded as _content_bounded,
            count_bounded as _count_bounded,
        )

        # ★ P10 诊断 (2026-07-07): 打印 current_user 和凭证读取情况
        from src.core.config_session import get_current_user

        _diag_user = get_current_user()
        username = config.target_username
        password = config.target_password
        _log.info(
            "[login][DIAG] current_user=%s | target_username=%s | target_password_len=%d | target_url=%s",
            _diag_user,
            username or "(empty)",
            len(password or ""),
            config.target_url,
        )

        if not username or not password:
            _log.warning(
                "[login][DIAG] 确定性登录跳过：username 或 password 为空 "
                "(user=%s, username_len=%d, password_len=%d)",
                _diag_user,
                len(username or ""),
                len(password or ""),
            )
            return False

        try:
            # 检查是否在登录页
            url = page.url or ""
            _log.info("[login][DIAG] 当前页面 URL: %s", url)
            if "login" not in url.lower() and "signin" not in url.lower():
                # ★ 2026-08-08: 多路径登录尝试 — 先 target_url，再常见 admin 路径
                _login_urls = [config.target_url]
                if config.target_url:
                    base = config.target_url.rstrip("/")
                    # ★ : 跨系统通用性修复——登录路径从配置读取
                    _login_path = getattr(config, 'target_login_path', '') or ''
                    _login_suffixes = [_login_path] if _login_path else ["/admin/", "/admin/login/", "/login/"]
                    for suffix in _login_suffixes:
                        _login_urls.append(base + suffix)

                _login_success = False
                for _try_url in _login_urls:
                    _log.info("[login][DIAG] 尝试导航到: %s", _try_url)
                    try:
                        await self._engine.goto(_try_url)
                    except Exception as _goto_e:
                        _log.warning("[login][DIAG] 导航 %s 失败: %s", _try_url, str(_goto_e)[:80])
                        continue
                    # 等待 DOM 就绪
                    try:
                        await page.wait_for_load_state("domcontentloaded", timeout=10000)
                    except Exception:
                        await page.wait_for_timeout(500)
                    with guard("_try_deterministic_login: await page.wait_for_load_state('netw", log=_log):
                        await page.wait_for_load_state("networkidle", timeout=15000)
                    _log.info("[login][DIAG] 导航后 URL: %s", page.url or "")

                    # 快速检查当前页面是否有用户名输入框
                    from src.core.constants import LOGIN_USERNAME_SELECTORS_FALLBACK

                    _found = False
                    for _sel in LOGIN_USERNAME_SELECTORS_FALLBACK:
                        try:
                            if await _count_bounded(page, _sel) > 0:
                                _found = True
                                break
                        except Exception:
                            continue
                    if _found:
                        _log.info("[login][DIAG] 在 %s 找到登录表单", _try_url)
                        _login_success = True
                        break
                    _log.info("[login][DIAG] %s 无登录表单，尝试下一路径", _try_url)

                if not _login_success:
                    _log.warning("[login][DIAG] 所有登录路径均未找到登录表单")
                    # 回退到 target_url 继续执行（让后续逻辑报更具体的错误）
                    if config.target_url:
                        with guard("_try_deterministic_login: await self._engine.goto(config.targe", log=_log):
                            await self._engine.goto(config.target_url)
                            await page.wait_for_load_state("domcontentloaded", timeout=10000)

            # ★ P10 修复 (2026-07-07): SPA 应用 JS 渲染延迟，等待输入框出现再查找
            #   问题：domcontentloaded 触发时 JS 还没渲染完输入框，count() 返回 0
            #   方案：用 wait_for_selector 等待任意一个用户名选择器出现（15s 超时）
            from src.core.constants import LOGIN_USERNAME_SELECTORS_FALLBACK

            username_selectors = list(LOGIN_USERNAME_SELECTORS_FALLBACK)
            _log.info(
                "[login][DIAG] 尝试 %d 个用户名选择器: %s",
                len(username_selectors),
                username_selectors[:3],
            )

            # ★ 构建 CSS 复合选择器（逗号分隔），用 wait_for_selector 等待任意一个出现
            # ★ 通用修复: 同时等待 input 和 select 元素（部分系统用 select 选择用户）
            _composite_selector = ", ".join(username_selectors)
            _composite_selector += ', select[id*="user" i], select[name*="user" i], select[id*="account" i], select'
            with guard("_try_deterministic_login: await page.wait_for_selector( _compo", log=_log, level=logging.WARNING):
                await page.wait_for_selector(
                    _composite_selector, timeout=15000, state="visible"
                )
                _log.info("[login][DIAG] 复合选择器等待成功（SPA 输入框已渲染）")

            username_input = None
            for selector in username_selectors:
                try:
                    if await _count_bounded(page, selector) > 0:
                        username_input = selector
                        break
                except Exception as _e:
                    _log.debug(
                        "[login] _try_deterministic_login 异常: %s", _e, exc_info=True
                    )
                    continue

            if not username_input:
                # ★ 通用修复 (2026-07-16): 用户名可能是 <select> 下拉框而非 <input>
                #   部分系统（如 FUB）用户选择用 <select> 元素，需 select_option 而非 fill
                _log.info("[login][DIAG] 未找到 input 用户名框，尝试 select 下拉框")
                for sel in _SELECT_SELECTORS:
                    try:
                        if await _count_bounded(page, sel) > 0:
                            username_input = sel
                            _log.info("[login][DIAG] 找到 select 用户名框: %s", sel)
                            break
                    except Exception as _e:
                        _log.debug("[login] select 查找异常: %s", _e, exc_info=True)
                        continue

            if not username_input:
                _log.warning(
                    "[login][DIAG] 未找到用户名输入框 (尝试了 input+select 选择器)"
                )
                # ★ P10 诊断: 截取页面 HTML 片段帮助定位
                try:
                    _html_preview = await _content_bounded(page)
                    _log.info(
                        "[login][DIAG] 页面 HTML 前 500 字符: %s", _html_preview[:500]
                    )
                except Exception as _e:
                    _log.debug("page.content 失败: %s", _e, exc_info=True)
                return False

            # 查找密码输入框 (★ P2: 走 constants 集中选择器列表)
            from src.core.constants import LOGIN_PASSWORD_SELECTORS_FALLBACK

            password_selectors = list(LOGIN_PASSWORD_SELECTORS_FALLBACK)
            password_input = None
            for selector in password_selectors:
                try:
                    if await _count_bounded(page, selector) > 0:
                        password_input = selector
                        break
                except Exception as _e:
                    _log.debug("[login] 非关键异常: %s", _e, exc_info=True)
                    continue

            if not password_input:
                return False

            # 填写登录信息 — 区分 input 和 select 元素
            _is_select = username_input.startswith("select")
            if _is_select:
                # ★  通用修复: select 选项多为 JS 异步加载（如引擎自身
                #   登录页 #userSelect），select_option 在 option 未就绪时会静默失败。
                #   先等待目标 option 出现（含 value 匹配），再选择。
                with guard("_try_deterministic_login: await page.wait_for_selector( '%s op", log=_log):
                    await page.wait_for_selector(
                        '%s option[value="%s"]' % (username_input, username),
                        timeout=10000,
                        state="attached",
                    )
                await page.locator(username_input).first.select_option(username)
                _log.info("[login][DIAG] 已选择用户(select): %s", username)
            else:
                await page.fill(username_input, username)
                _log.info("[login][DIAG] 已填写用户名: %s", username)
            await page.wait_for_timeout(200)
            await page.fill(password_input, password)
            _log.info("[login][DIAG] 已填写密码 (len=%d)", len(password))
            await page.wait_for_timeout(200)

            # 查找登录按钮
            login_button_selectors = [
                'button[type="submit"]',
                'button:has-text("登录")',
                'button:has-text("Login")',
                'input[type="submit"]',
                'a:has-text("登录")',
            ]
            login_button = None
            for selector in login_button_selectors:
                try:
                    cnt = await _count_bounded(page, selector)
                    _log.info("[login][DIAG] 按钮选择器 %s → count=%d", selector, cnt)
                    if cnt > 0:
                        login_button = selector
                        break
                except Exception as _e:
                    _log.debug("[login] 非关键异常: %s", _e, exc_info=True)
                    continue

            if login_button:
                # ★  通用修复: 部分系统登录按钮默认 disabled（选择用户后
                #   才启用，如引擎自身 #btnLogin）。disabled 时 click 无效果，必须先等待启用。
                with guard("_try_deterministic_login: await page.wait_for_selector( '%s:no", log=_log):
                    await page.wait_for_selector(
                        "%s:not([disabled])" % login_button,
                        timeout=10000,
                        state="visible",
                    )
                _log.info("[login][DIAG] 点击登录按钮: %s", login_button)
                await page.click(login_button)
            else:
                _log.info("[login][DIAG] 未找到登录按钮，按 Enter 键")
                await page.keyboard.press("Enter")

            # ★ S4-011: 确定性等待替代 sleep(3) — 等待登录跳转完成
            # 登录成功后 URL 会离开 login/signin 路径,用 wait_for_url 等待
            try:
                await page.wait_for_url(
                    lambda u: "login" not in u.lower() and "signin" not in u.lower(),
                    timeout=10000,
                )
                _log.info("[login][DIAG] URL 已离开登录页")
            except Exception:
                _log.warning(
                    "[login][DIAG] URL 未离开登录页，当前 URL: %s", page.url or ""
                )
                try:
                    await page.wait_for_load_state("domcontentloaded", timeout=5000)
                except Exception:
                    await page.wait_for_timeout(1000)

            # 验证登录成功
            current_url = page.url or ""
            _log.info("[login][DIAG] 最终 URL: %s", current_url)
            if (
                "login" not in current_url.lower()
                and "signin" not in current_url.lower()
            ):
                _log.info("    [Login] 确定性登录成功")
                return True

            _log.warning("[login][DIAG] 登录失败，URL 仍含 login: %s", current_url)
            return False

        except Exception as e:
            _log.warning("    [Login] 确定性登录异常: %s", str(e)[:80])
            return False


# ── 词表唯一源（提升自 src/pipeline/steps/step4/ai/login.py，下游 import 引用，勿重抄）──
_SELECT_SELECTORS = [
    'select[id*="user" i]',
    'select[name*="user" i]',
    'select[id*="account" i]',
    'select[name*="account" i]',
    'select[id*="login" i]',
    "select",
]
