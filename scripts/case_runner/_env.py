# -*- coding: utf-8 -*-
"""用例执行环境（真实浏览器 + 真实靶子，非 mock）。

纪律：
  - 浏览器用引擎自带的 PlaywrightEngine（测的是引擎能力，不是裸 Playwright）
  - 系统 Chrome channel 优先（沙箱装不上 chromium）
  - 登录态经真实 HTTP 登录取 cookie 注入，不走 UI 捷径
  - 靶子不可达 → 如实报错，禁止降级为「跳过即通过」
"""
from __future__ import annotations

import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

SUT_BASE = os.environ.get("CASE_RUNNER_SUT", "http://127.0.0.1:5080")
ENGINE_BASE = os.environ.get("CASE_RUNNER_ENGINE", "http://127.0.0.1:5009")
SUT_USER = os.environ.get("CASE_RUNNER_SUT_USER", "admin")
SUT_PASS = os.environ.get("CASE_RUNNER_SUT_PASS", "admin123")


class CaseEnv:
    """真实浏览器执行环境（靶子轨）。"""

    def __init__(self, headless: bool = True):
        self.headless = headless
        self.engine = None
        self.logged_in = False
        self.login_error = ""
        # ★ 真实登录会话保留（api_request 动作复用同一鉴权态，而非绕过登录）
        self.sut_session = None
        self.sut_cookies: dict = {}

    async def start(self):
        from src.pipeline.steps.step4.playwright_engine import PlaywrightEngine

        self.engine = PlaywrightEngine(
            headless=self.headless, viewport_width=1280, viewport_height=800
        )
        await self.engine.start()
        await self.login()
        return self

    async def login(self):
        """真实登录靶子，注入 session cookie。失败则如实记录（不静默通过）。"""
        import requests

        try:
            s = requests.Session()
            r = s.post(
                SUT_BASE + "/login",
                data={"username": SUT_USER, "password": SUT_PASS},
                timeout=15,
                allow_redirects=True,
            )
            ck = s.cookies.get_dict()
            self.sut_session = s
            self.sut_cookies = ck
            if ck and self.engine._context is not None:
                await self.engine._context.add_cookies(
                    [
                        {"name": k, "value": v, "domain": "127.0.0.1", "path": "/"}
                        for k, v in ck.items()
                    ]
                )
                self.logged_in = True
            else:
                self.login_error = f"登录无 cookie (status={r.status_code})"
        except Exception as e:
            self.login_error = f"登录异常: {e}"
        return self.logged_in

    @property
    def page(self):
        return getattr(self.engine, "_page", None)

    @property
    def auth_headers(self) -> dict:
        """已登录会话的鉴权头（Cookie 形式；未登录返回空）。"""
        if self.sut_cookies:
            return {"Cookie": "; ".join(f"{k}={v}" for k, v in self.sut_cookies.items())}
        return {}

    async def goto(self, url: str) -> bool:
        try:
            return await self.engine.goto(url)
        except Exception:
            return False

    async def close(self):
        if self.engine is not None:
            try:
                await self.engine.stop()
            except Exception:
                pass


class EngineUIEnv:
    """真实浏览器执行环境（引擎控制台轨，用于前端控件轨用例）。"""

    def __init__(self, headless: bool = True):
        from src.pipeline.steps.step4.playwright_engine import PlaywrightEngine

        self.headless = headless
        self._cls = PlaywrightEngine
        self.engine = None

    async def start(self):
        self.engine = self._cls(
            headless=self.headless, viewport_width=1440, viewport_height=900
        )
        await self.engine.start()
        return self

    @property
    def page(self):
        return getattr(self.engine, "_page", None)

    async def goto(self, url: str) -> bool:
        try:
            return await self.engine.goto(url)
        except Exception:
            return False

    async def close(self):
        if self.engine is not None:
            try:
                await self.engine.stop()
            except Exception:
                pass
