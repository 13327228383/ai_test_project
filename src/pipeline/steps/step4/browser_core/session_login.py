# -*- coding: utf-8 -*-
"""通用会话登录（SessionAware 自动登录与 identity_switch 的单一实现）。

★ 根因修复(2026-09-21) [未复核-需 GLM/DeepSeek 把关]:
  此前通用登录逻辑（当前页优先 + 登录路径探测 + 稳健选择器填写验证）内联在
  browser_core/single_mixins/_base.py 的 _execute_one_intent 里（~100 行闭包），
  而 identity_switch 动作（tool_definitions/taxonomy 已声明 W4 骨架）也需要同一能力。
  为避免第二份实现（重复造轮子），抽取为本模块的单一函数，两处共用：

  - SessionAware（_base.py）：每次意图执行前的会话感知登录
  - identity_switch（browser_tools/execution/_single.py）：切换身份后重建会话

通用性纪律：
  - 选择器优先取引擎配置（login_username_selector 等），缺省用稳健默认
    （覆盖 <button type="submit"> 与 <input type="submit"> 两类提交控件）
  - 零业务硬编码：登录路径探测表为通用候选（/login、/admin/login/ 等），
    配置的 target_login_path 恒优先
"""
from __future__ import annotations

import logging

from src.core.page_ready import content_bounded, count_bounded

_log = logging.getLogger(__name__)

# 稳健默认选择器（与 _base.py SessionAware 原实现保持同一份语义）
DEFAULT_USERNAME_SEL = (
    'input[name="username"], input[name="user"], input[name="account"], '
    'input[placeholder*="用户名"], input[placeholder*="账号"], input[placeholder*="用户"]'
)
DEFAULT_PASSWORD_SEL = (
    'input[type="password"], input[name="password"], input[placeholder*="密码"]'
)
DEFAULT_SUBMIT_SEL = (
    'button[type="submit"], input[type="submit"], button:has-text("登录"), '
    'button:has-text("Login"), button:has-text("登 录"), button:has-text("SignIn")'
)

# 通用登录路径候选（配置的 login_path 恒优先）
COMMON_LOGIN_PATHS = ("/login", "/admin/login/", "/login/", "/signin", "/")


async def has_login_form(page, username_sel: str, password_sel: str) -> bool:
    """当前页是否呈现登录表单（用户名+密码输入框同现）。"""
    try:
        return (
            await count_bounded(page, username_sel) > 0
            and await count_bounded(page, password_sel) > 0
        )
    except Exception:
        return False


async def session_login(
    page,
    goto,
    base_url: str,
    username: str,
    password: str,
    login_path: str = "",
    username_sel: str = "",
    password_sel: str = "",
    submit_sel: str = "",
    origin: str = "SessionLogin",
) -> tuple[bool, str]:
    """在给定页面上完成一次真实登录，返回 (ok, 详情)。

    Args:
        page:   Playwright Page。
        goto:   异步导航可调用（引擎 engine.goto，保持就绪等待语义一致）。
        base_url: 目标根地址（登录路径候选拼在其上）。
        username/password: 登录凭据。
        login_path: 配置的登录路径（优先探测）。
        *_sel:  可选的选择器覆盖（配置优先，缺省用稳健默认）。
        origin: 日志标签（SessionAware / identity_switch）。

    行为（与原 SessionAware 内联实现语义一致）：
      1. 当前页已是登录页（用户名+密码框同现）→ 直接填写提交；
      2. 否则按 [login_path] + 通用候选 逐个导航探测，找到登录页即填写；
      3. 提交后验证：URL 离开 /login 或密码框消失 → 成功。
    """
    username_sel = (username_sel or "").strip() or DEFAULT_USERNAME_SEL
    password_sel = (password_sel or "").strip() or DEFAULT_PASSWORD_SEL
    submit_sel = (submit_sel or "").strip() or DEFAULT_SUBMIT_SEL

    async def _fill_submit_verify(hint: str) -> tuple[bool, str]:
        try:
            _u = await page.wait_for_selector(username_sel, timeout=10000, state="visible")
            if not _u:
                _log.warning("[%s] 未找到用户名输入框(%s)", origin, hint)
                return False, f"未找到用户名输入框({hint})"
            await _u.fill(username or "")
            _p = await page.wait_for_selector(password_sel, timeout=5000, state="visible")
            if not _p:
                _log.warning("[%s] 未找到密码输入框(%s)", origin, hint)
                return False, f"未找到密码输入框({hint})"
            await _p.fill(password or "")
            _s = await page.wait_for_selector(submit_sel, timeout=5000, state="visible")
            if not _s:
                _log.warning("[%s] 未找到登录按钮(%s)", origin, hint)
                return False, f"未找到登录按钮({hint})"
            await _s.click()
            await page.wait_for_load_state("networkidle", timeout=15000)
            _final = (page.url or "").lower()
            if "/login" not in _final and "chrome-error://" not in _final:
                _log.info("[%s] 登录成功: %s", origin, _final)
                return True, _final
            # 部分 SPA 登录后 URL 不变但登录表单消失
            try:
                _still_pwd = await count_bounded(page, password_sel) > 0
            except Exception:
                _still_pwd = True
            if not _still_pwd:
                _log.info("[%s] 登录成功(表单消失): %s", origin, _final)
                return True, _final
            _log.warning("[%s] 登录后仍在登录页(%s)", origin, _final)
            return False, f"登录后仍在登录页({_final})"
        except Exception as _e:
            _log.warning("[%s] 登录填写异常(%s): %s", origin, hint, str(_e)[:120])
            return False, f"登录填写异常({hint}): {type(_e).__name__}"

    try:
        # 1) 当前页优先：已是登录页则直接填写，不盲导
        if await has_login_form(page, username_sel, password_sel):
            _log.info("[%s] 当前页已是登录页，直接填写", origin)
            return await _fill_submit_verify("当前页")

        # 2) 候选路径探测
        _cands: list[str] = []
        if (login_path or "").strip():
            _cands.append(login_path.strip())
        _cands += list(COMMON_LOGIN_PATHS)
        _seen: set[str] = set()
        for _suf in _cands:
            _suf_n = (_suf or "").strip()
            if not _suf_n:
                continue
            _key = _suf_n.rstrip("/")
            if _key in _seen:
                continue
            _seen.add(_key)
            _url = (base_url or "").rstrip("/") + (
                _suf_n if _suf_n.startswith("/") else "/" + _suf_n
            )
            try:
                await goto(_url)
                await page.wait_for_load_state("networkidle", timeout=10000)
            except Exception as _ge:
                _log.debug("[%s] 导航%s异常: %s", origin, _url, str(_ge)[:60])
                continue
            if await has_login_form(page, username_sel, password_sel):
                _log.info("[%s] 在 %s 找到登录页", origin, _url)
                ok, detail = await _fill_submit_verify(_url)
                if ok:
                    return True, detail
        return False, "各候选登录路径均未成功（当前页/配置路径/通用路径）"
    except Exception as _e:
        _log.warning("[%s] 会话登录异常: %s", origin, str(_e)[:120])
        return False, f"会话登录异常: {type(_e).__name__}: {str(_e)[:120]}"
