# -*- coding: utf-8 -*-
from __future__ import annotations
from src.core.guarded import guard

"""api_login_helpers.py — API 直调登录与 HTTP 工具
====================================================
从 api_only_executor.py 拆分（§4.3 单文件≤800行红线）。

★ 安全拆分原则：
  - 零业务逻辑变更，仅物理位置移动
  - 保持所有导入路径、函数签名、返回值 100% 一致
  - 原文件通过 re-import 兼容

包含：
  - _DEFAULT_LOGIN_PATHS: 通用登录路径常量
  - _make_http_request: urllib HTTP 请求工具
  - _try_api_login: 登录入口（Playwright 优先，urllib 兜底）
  - _try_playwright_login: 浏览器登录（处理 JS 加密/CSRF）
  - _try_urllib_login: urllib 直调登录兜底
"""

import json
import logging
import os
import re
import subprocess
import sys

_log = logging.getLogger(__name__)

# ═══════════════════════════════════════════════════════════════
# HTTP 直调验证引擎（零新依赖，纯 stdlib）
# ═══════════════════════════════════════════════════════════════

# ★ 通用化: 登录路径改为模块级常量，可通过 config 覆盖
_DEFAULT_LOGIN_PATHS = ["/api/login", "/login", "/auth/login", "/api/auth/login", "/api/v1/auth/login"]


def _make_http_request(
    url: str,
    method: str = "GET",
    headers: dict = None,
    body: dict = None,
    cookies: dict = None,
    timeout: int = 15,
    content_type: str = None,
) -> dict:
    """使用 urllib 发起 HTTP 请求。

    Returns:
        {"ok": bool, "status": int|None, "body_text": str,
         "body_json": dict|None, "error": str|None,
         "cookies": dict, "url": str, "method": str}
    """
    import urllib.request as _ur
    import json as _json

    hdrs = dict(headers or {})
    hdrs.setdefault("User-Agent", "E2E-Test-Engine/2.0")
    hdrs.setdefault("Accept", "application/json, text/plain, */*")

    data_bytes = None
    if body:
        # 如果 body 是字符串（已编码），直接使用
        if isinstance(body, str):
            data_bytes = body.encode("utf-8")
        else:
            data_bytes = _json.dumps(body, ensure_ascii=False).encode("utf-8")
        hdrs["Content-Type"] = content_type or "application/json; charset=utf-8"
        method = "POST"

    if cookies:
        # ★ 提取 JWT token（__token__ 是特殊 key，不放入 Cookie）
        token = cookies.get("__token__", "")
        if token:
            hdrs["Authorization"] = "Bearer " + token
        # 其余 key 作为 Cookie 发送
        cookie_pairs = [
            "%s=%s" % (k, v) for k, v in cookies.items() if k != "__token__"
        ]
        if cookie_pairs:
            hdrs["Cookie"] = "; ".join(cookie_pairs)

    try:
        req = _ur.Request(url, data=data_bytes, headers=hdrs, method=method)
        resp = _ur.urlopen(req, timeout=timeout)
        body_text = resp.read().decode("utf-8", errors="replace")
        body_json = None
        try:
            body_json = _json.loads(body_text)
        except Exception as _e:
            _log.debug("[api_login_helpers] 非关键异常: %s", _e, exc_info=True)
        new_cookies = {}
        for h in resp.headers.get_all("Set-Cookie") or []:
            cm = re.match(r"(\w+)=([^;]+)", h)
            if cm:
                new_cookies[cm.group(1)] = cm.group(2)
        return {
            "ok": True,
            "status": resp.status,
            "body_text": body_text,
            "body_json": body_json,
            "error": None,
            "cookies": new_cookies,
            "url": url,
            "method": method,
        }
    except _ur.HTTPError as e:
        try:
            err_body = e.read().decode("utf-8", errors="replace")[:1000]
        except Exception as _e:
            _log.debug("[api_login_helpers] 非关键异常: %s", _e, exc_info=True)
            err_body = ""
        return {
            "ok": False,
            "status": e.code,
            "body_text": err_body,
            "body_json": None,
            "error": "HTTP %d: %s" % (e.code, e.reason),
            "url": url,
            "method": method,
        }
    except Exception as e:
        return {
            "ok": False,
            "status": None,
            "body_text": "",
            "body_json": None,
            "error": str(e)[:200],
            "url": url,
            "method": method,
        }


# ═══════════════════════════════════════════════════════════════
# 登录函数组（Playwright 优先，urllib 兜底）
# ═══════════════════════════════════════════════════════════════


def _try_api_login(
    base_url: str, username: str, password: str, project: str = ""
) -> dict:
    """尝试登录目标系统，返回 session cookies（全失败返回 {}）。

    ★  修复：先 urllib 直调（HTTP 登录，秒级），失败再 Playwright
      （处理 JS 加密密码、CSRF token 等前端逻辑）。原实现先 Playwright——
      对纯 API 靶子（无浏览器登录表单）会卡住/超时（实测 180s watchdog 释放）。
    """
    # ★ 优先：urllib HTTP 登录（秒级；覆盖 /api/v1/auth/login 等常见 API 登录路径）
    http_auth = _try_urllib_login(base_url, username, password, project)
    if http_auth:
        has_token = "__token__" in http_auth
        cookie_count = sum(1 for k in http_auth if k != "__token__")
        _log.info(
            "[API直调] HTTP 登录成功: %d cookie, token=%s",
            cookie_count,
            has_token,
        )
        return http_auth
    # 回退：Playwright 浏览器登录（JS 加密/CSRF 场景）
    pw_auth = _try_playwright_login(base_url, username, password)
    if pw_auth:
        has_token = "__token__" in pw_auth
        cookie_count = sum(1 for k in pw_auth if k != "__token__")
        _log.info(
            "[API直调] Playwright 浏览器登录成功: %d cookie, token=%s",
            cookie_count,
            has_token,
        )
        return pw_auth

    # ★ 回退：urllib 直调（适用于无前端加密的简单系统）
    _log.info("[API直调] Playwright 登录失败，回退到 urllib 直调")
    return _try_urllib_login(base_url, username, password, project)


# Playwright 登录探测整体超时（秒）。探测是"尽力而为"的前置动作，
# 任何情况下都不允许阻塞 Step4 主执行链路。
_PW_LOGIN_PROBE_TIMEOUT = 45


def _kill_proc_tree(proc: subprocess.Popen) -> None:
    """终止子进程整棵树。

    与 sut_harness 的进程树回收同一模式（复用仓内已验证做法，不另造轮子）：
      - Windows（os.name == "nt"）：taskkill /F /T /PID 递归杀整棵树（含
        chromium 及其 driver 孙进程）；/F 强制。
      - POSIX：proc.kill()。
    任何异常吞掉（多在超时/finally 路径调用，绝不向上抛）。
    """
    with guard("_kill_proc_tree: if os.name == 'nt': subprocess.run( ", log=_log):
        if os.name == "nt":
            subprocess.run(
                ["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=10,
            )
        else:
            proc.kill()


def _try_playwright_login(base_url: str, username: str, password: str) -> dict:
    """使用 Playwright 浏览器登录目标系统，提取 cookies（子进程隔离版）。

    ★  根治（替代此前的守护线程限时守卫）：
      sync Playwright 在引擎后台线程的 teardown（browser.close()/上下文退出）
      在 Windows IOCP+greenlet 上永久死锁（watchdog 栈铁证：卡 playwright
      _context_manager.py:56 greenlet_main）。线程级限时只是护栏——线程泄漏、
      chromium 驱动残留、每次白等 45s。
      根治：探测在**独立子进程**中运行（sync Playwright 官方支持的主线程模式），
      超时整树回收（taskkill /F /T）。进程死了就没了，引擎内零残留；
      登录逻辑仍复用 _playwright_login_impl 唯一实现体（零复制）。
    """
    import time as _t0

    _started = _t0.time()
    root = os.path.dirname(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    )
    env = dict(os.environ)
    env["PYTHONPATH"] = root + (
        os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else ""
    )
    payload = json.dumps(
        {"base_url": base_url, "username": username, "password": password}
    )
    try:
        proc = subprocess.Popen(
            [sys.executable, "-m", "src.pipeline.pw_login_worker"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            cwd=root,
            env=env,
            text=True,
            encoding="utf-8",
        )
    except Exception as e:
        _log.warning(
            "[API直调] Playwright 登录子进程启动失败(降级无认证): %s", str(e)[:120]
        )
        return {}
    try:
        out, _ = proc.communicate(payload, timeout=_PW_LOGIN_PROBE_TIMEOUT)
    except subprocess.TimeoutExpired:
        _log.warning(
            "[API直调] Playwright 登录子进程超时(%ss)，整树终止(pid=%s)，"
            "降级为无认证继续执行 API 校验",
            _PW_LOGIN_PROBE_TIMEOUT,
            proc.pid,
        )
        _kill_proc_tree(proc)
        return {}
    _cost = round(_t0.time() - _started, 1)
    if proc.returncode != 0:
        _log.warning(
            "[API直调] Playwright 登录子进程异常退出 code=%s cost=%ss",
            proc.returncode,
            _cost,
        )
        return {}
    try:
        data = json.loads(out or "{}")
    except Exception:
        _log.warning("[API直调] Playwright 登录子进程输出不可解析(cost=%ss)", _cost)
        return {}
    if not data.get("ok"):
        _log.warning(
            "[API直调] Playwright 登录探测失败(cost=%ss): %s",
            _cost,
            str(data.get("error"))[:120],
        )
        return {}
    auth = data.get("auth") or {}
    if auth:
        _log.info("[API直调] Playwright 登录子进程成功(cost=%ss)", _cost)
    return auth


def _playwright_login_impl(
    base_url: str, username: str, password: str, on_result=None
) -> dict:
    """Playwright 浏览器登录实现体（由 _try_playwright_login 限时隔离调用）。

    ★ on_result（一次性子进程模式，唯一受支持调用方=pw_login_worker）：
      sync Playwright 的 teardown（browser.close() / sync_playwright.__exit__）
      在本机 Windows IOCP+greenlet 上**必然永久死锁**——faulthandler 双重铁证：
      引擎后台线程内（watchdog 栈）与独立子进程主线程内均卡
      ``_context_manager.py:56 greenlet_main`` → ``asyncio/windows_events.py:778
      _poll``。因此 worker 传入 on_result 回调，本函数在 with 块内拿到结果的
      瞬间即回调（worker 内 print 结果 + os._exit(0)），**永不走到 with 退出/
      browser.close()**——一次性进程由 OS 回收全部资源，无需优雅 teardown。
      on_result=None 的直调模式已无调用方，且 teardown 会挂（仅保留兜底）。
    """

    def _finish(auth: dict) -> dict:
        if on_result is not None:
            on_result(auth)  # worker 模式：os._exit，不再返回
        return auth

    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        _log.warning("[API直调] Playwright 未安装，跳过浏览器登录")
        return {}

    if not base_url or not username or not password:
        return {}

    _log.info("[API直调] 启动 Playwright 浏览器登录: %s", base_url)
    with sync_playwright() as p:
        try:
            browser = p.chromium.launch(
                headless=True, args=["--no-sandbox", "--disable-gpu"]
            )
            context = browser.new_context(viewport={"width": 1400, "height": 900})
            page = context.new_page()
        except Exception as e:
            _log.warning("[API直调] Playwright 启动失败: %s", str(e)[:120])
            return _finish({})

        try:
            # 1. 导航到目标系统
            page.goto(base_url, timeout=20000, wait_until="domcontentloaded")
            with guard("_playwright_login_impl: page.wait_for_load_state('networkidl", log=_log):
                page.wait_for_load_state("networkidle", timeout=15000)

            # 2. 等待登录表单出现
            import time as _time

            _time.sleep(1.5)

            # 3. 填写用户名（优先 input，其次 select）
            user_filled = False
            for sel in [
                'input[type="text"]',
                'input[name="username"]',
                'input[placeholder*="账号"]',
                'input[placeholder*="用户"]',
                'input[placeholder*="用户名"]',
            ]:
                try:
                    loc = page.locator(sel)
                    if loc.count() > 0:
                        loc.first.fill(username)
                        user_filled = True
                        _log.debug("[API直调] Playwright 填写用户名: %s", sel)
                        break
                except Exception as _se:
                    _log.debug("[API直调] 选择器尝试失败(跳过): %s", str(_se)[:50])
                    continue
            if not user_filled:
                for sel in [
                    'select[id*="user" i]',
                    'select[name*="user" i]',
                    "select",
                ]:
                    try:
                        loc = page.locator(sel)
                        if loc.count() > 0:
                            loc.first.select_option(username)
                            user_filled = True
                            _log.debug("[API直调] Playwright 选择用户: %s", sel)
                            break
                    except Exception as _rx1:
                        _log.debug(
                            "[api_login_helpers] 异常降级(continue): %s",
                            str(_rx1)[:60],
                            exc_info=True,
                        )
                        continue

            # 4. 填写密码
            pass_filled = False
            for sel in [
                'input[type="password"]',
                'input[name="password"]',
                'input[placeholder*="密码"]',
            ]:
                try:
                    loc = page.locator(sel)
                    if loc.count() > 0:
                        loc.first.fill(password)
                        pass_filled = True
                        _log.debug("[API直调] Playwright 填写密码: %s", sel)
                        break
                except Exception as _se:
                    _log.debug("[API直调] 选择器尝试失败(跳过): %s", str(_se)[:50])
                    continue

            if not user_filled or not pass_filled:
                _log.warning(
                    "[API直调] Playwright 未找到登录表单: user=%s pass=%s",
                    user_filled,
                    pass_filled,
                )
                return _finish({})

            # 5. 点击登录按钮
            _time.sleep(0.3)
            clicked = False
            for sel in [
                'button[type="submit"]',
                'button:has-text("登录")',
                'button:has-text("Login")',
                'button:has-text("登 录")',
                'input[type="submit"]',
            ]:
                try:
                    loc = page.locator(sel)
                    if loc.count() > 0:
                        loc.first.click()
                        clicked = True
                        _log.debug("[API直调] Playwright 点击登录: %s", sel)
                        break
                except Exception as _se:
                    _log.debug("[API直调] 选择器尝试失败(跳过): %s", str(_se)[:50])
                    continue
            if not clicked:
                with guard("_playwright_login_impl: page.keyboard.press('Enter')", log=_log):
                    page.keyboard.press("Enter")

            # 6. 等待登录完成（URL 离开 login 页面 或 登录表单消失）
            try:
                page.wait_for_url(
                    lambda u: "login" not in u.lower()
                    and "signin" not in u.lower(),
                    timeout=12000,
                )
            except Exception:
                _time.sleep(2)
                with guard("_playwright_login_impl: page.wait_for_load_state('networkidl", log=_log):
                    page.wait_for_load_state("networkidle", timeout=10000)

            _time.sleep(1)

            # 7. 验证登录成功并提取 cookies
            current_url = (page.url or "").lower()
            has_password = False
            with guard("_playwright_login_impl: has_password = page.locator('input[t", log=_log):
                has_password = page.locator('input[type="password"]').count() > 0

            if "login" in current_url and has_password:
                _log.warning(
                    "[API直调] Playwright 登录失败: 仍在登录页 %s", current_url
                )
                return _finish({})

            # ★ 提取 cookies（传统 session 系统）
            cookies_list = context.cookies()
            auth = {c["name"]: c["value"] for c in cookies_list}

            # ★ 提取 localStorage token（JWT 等现代认证系统，如 某SPA系统）
            token = None
            with guard("_playwright_login_impl: token = page.evaluate('() => localSt", log=_log):
                token = page.evaluate("() => localStorage.getItem('token') || ''")
            if token:
                auth["__token__"] = token
                _log.info(
                    "[API直调] Playwright 登录成功: URL=%s, %d cookie + JWT token",
                    current_url,
                    len(cookies_list),
                )
            else:
                _log.info(
                    "[API直调] Playwright 登录成功: URL=%s, %d cookie",
                    current_url,
                    len(cookies_list),
                )
            # ★ 不调 browser.close()：teardown 必然死锁（见 docstring）。
            #   worker 模式 _finish 内 os._exit 由 OS 回收；直调模式无调用方。
            return _finish(auth)

        except Exception as e:
            _log.warning("[API直调] Playwright 登录异常: %s", str(e)[:120])
            return _finish({})


def _try_urllib_login(
    base_url: str, username: str, password: str, project: str = ""
) -> dict:
    """urllib 直调登录（回退方案），尝试 form-encoded 和 JSON 两种格式。"""
    from urllib.parse import urlencode as _urlencode
    import json as _json2

    login_paths = _DEFAULT_LOGIN_PATHS

    login_bodies = [
        {
            "body": _urlencode({"username": username, "password": password}),
            "content_type": "application/x-www-form-urlencoded",
            "label": "form",
        },
        {
            "body": _json2.dumps({"username": username, "password": password}),
            "content_type": "application/json; charset=utf-8",
            "label": "json",
        },
    ]

    for path in login_paths[:6]:
        from src.pipeline.api_prefix_detector import build_url_with_auto_prefix

        url = build_url_with_auto_prefix(base_url, path, project=project, method="POST")
        if not url:
            continue

        for body_conf in login_bodies:
            with guard("_try_urllib_login: result = _make_http_request( url, me", log=_log):
                result = _make_http_request(
                    url,
                    method="POST",
                    body=body_conf["body"],
                    content_type=body_conf["content_type"],
                    timeout=10,
                )
                cookies = result.get("cookies") or {}
                is_ok = result["ok"] and result.get("status") in (200, 201, 302)
                if is_ok and cookies:
                    _log.info(
                        "[API直调] urllib 登录成功 (%s): %s → %d, %d 个 cookie",
                        body_conf["label"],
                        path,
                        result["status"],
                        len(cookies),
                    )
                    return cookies
                # ★  修复：JSON body token 登录（JWT 场景，无 Set-Cookie）
                #   常见响应：{"code":0,"data":{"token":"..."}} / {"token":"..."} / {"access_token":"..."}
                if is_ok:
                    bj = result.get("body_json") or {}
                    token = (
                        (bj.get("data") or {}).get("token")
                        if isinstance(bj.get("data"), dict)
                        else None
                    ) or bj.get("token") or bj.get("access_token")
                    if token:
                        _log.info(
                            "[API直调] urllib 登录成功 (%s): %s → %d, JSON token 已提取",
                            body_conf["label"],
                            path,
                            result["status"],
                        )
                        return {"__token__": str(token)}
                if cookies:
                    _log.debug(
                        "[API直调] urllib 登录尝试 (%s) %s → %d, cookies=%s",
                        body_conf["label"],
                        path,
                        result["status"],
                        list(cookies.keys())[:3],
                    )

    _log.info(
        "[API直调] urllib 自动登录未获取到有效 session，将以无认证模式执行 API 校验"
    )
    return {}
