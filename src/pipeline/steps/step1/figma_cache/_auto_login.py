# -*- coding: utf-8 -*-
"""Figma 自动登录（headless Playwright）。"""

from pathlib import Path
import logging

_log = logging.getLogger(__name__)

# ★ 通用化: Figma 登录配置提取为模块级常量，可通过 config 覆盖
_FIGMA_LOGIN_URL = "https://www.figma.com/login"
_FIGMA_EMAIL_SELECTOR = 'input[name="email"], input[type="email"]'
_FIGMA_PASSWORD_SELECTOR = 'input[name="password"], input[type="password"]'
_FIGMA_SUBMIT_SELECTOR = (
    'button[type="submit"], button:has-text("Log in"), '
    'button:has-text("登录"), button:has-text("Sign in")'
)


def _get_meta_profile():
    # ★ playwright 浏览器 profile（存 cookie 的 SQLite）迁系统 tempfile，
    #    不再依赖 data/{project}/（all-mysql-no-file-residue）。
    #    跨 session 保留在系统临时目录，属工具运行时产物非项目数据。
    # ★ 方案C 改造：按 user_id 隔离 profile 目录，防止并发 Figma 登录冲突
    import tempfile
    from pathlib import Path

    try:
        from src.core.config_loader import get_current_user

        _uid = get_current_user() or "default"
    except Exception:
        _uid = "default"
    return Path(tempfile.gettempdir()) / f"ai_test_figma_profile_{_uid}"


def _auto_login_figma() -> bool:
    """
    使用配置文件中的账号密码自动登录 Figma（headless）。
    返回 True 表示登录成功且 cookie 已保存。
    """
    from src.core.config_loader import config

    email = config.figma_username
    password = config.figma_login_password
    if not email or not password:
        _log.info("  [Figma Login] 账号或密码未配置，跳过自动登录")
        return False

    _log.info("  [Figma Login] 自动登录中...")
    try:
        from playwright.sync_api import sync_playwright

        # ★ _auto_login.py 在 figma_cache/ 下，比 playwright_engine/ 多一层目录
        chrome_path = str(
            Path(__file__).resolve().parent.parent.parent.parent.parent.parent
            / "chrome-win64"
            / "chrome.exe"
        )
        if not Path(chrome_path).is_file():
            chrome_path = None

        launch_opts = {
            "headless": True,
            "args": [
                "--disable-gpu",
                "--disable-blink-features=AutomationControlled",
                "--no-sandbox",
            ],
            "no_viewport": True,
            "locale": "zh-CN",
        }
        if chrome_path:
            launch_opts["executable_path"] = chrome_path

        with sync_playwright() as pw:
            ctx = pw.chromium.launch_persistent_context(
                user_data_dir=str(_get_meta_profile()), **launch_opts
            )
            page = ctx.pages[0] if ctx.pages else ctx.new_page()

            # Figma 登录页
            login_url = getattr(config, "figma_login_url", "") or _FIGMA_LOGIN_URL
            page.goto(login_url, wait_until="networkidle", timeout=30000)

            # 输入邮箱
            email_selector = (
                getattr(config, "figma_email_selector", "") or _FIGMA_EMAIL_SELECTOR
            )
            email_input = page.locator(email_selector).first
            email_input.wait_for(state="visible", timeout=10000)
            email_input.fill(email)

            # 输入密码
            pw_selector = (
                getattr(config, "figma_password_selector", "")
                or _FIGMA_PASSWORD_SELECTOR
            )
            pw_input = page.locator(pw_selector).first
            pw_input.wait_for(state="visible", timeout=10000)
            pw_input.fill(password)

            # 点击登录按钮
            submit_selector = (
                getattr(config, "figma_submit_selector", "") or _FIGMA_SUBMIT_SELECTOR
            )
            submit_btn = page.locator(submit_selector).first
            submit_btn.click()

            # 等待登录完成（跳转到 files / dashboard）
            try:
                page.wait_for_url("**/files/**", timeout=30000)
            except Exception:
                page.wait_for_url("**/figma.com/files**", timeout=30000)

            # 检查是否登录成功
            current_url = page.url
            if "/files" in current_url or "/login" not in current_url:
                # 导航到设计文件确保 session cookie 完整
                from src.core.config_loader import config as _cfg

                try:
                    page.goto(
                        f"https://www.figma.com/design/{_cfg.figma_file_key}",
                        wait_until="domcontentloaded",
                        timeout=30000,
                    )
                    import time as _t

                    _t.sleep(3)
                except Exception as _e:
                    _log.debug("[FigmaLogin] 页面跳转失败: %s", _e, exc_info=True)
                ctx.close()
                _log.info("  [Figma Login] 自动登录成功")
                return True

            ctx.close()
            _log.info("  [Figma Login] 登录后 URL 异常: %s", current_url[:80])
            return False

    except Exception as e:
        _log.info("  [Figma Login] 自动登录失败: %s", str(e)[:120])
        return False
