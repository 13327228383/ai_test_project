# -*- coding: utf-8 -*-
"""技术可行性验证：用引擎自带的 PlaywrightEngine 真实驱动浏览器打靶子。

目的（不做假）：
  1. 验证 PlaywrightEngine 能用系统 Chrome channel 启动（沙箱不下载 chromium）
  2. 验证 _execute_tool 能真实下发 browser_* 动作指令
  3. 验证 verification 的 verify_* 校验器能真实判真/判伪
这是「按用例逐条真实执行」的前置 spike，不通过则不能声称能批量执行。
"""
import asyncio
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)

SUT = "http://127.0.0.1:5080"


async def main():
    from src.pipeline.steps.step4.playwright_engine import PlaywrightEngine
    from src.pipeline.steps.step4.browser_tools.execution._single import _execute_tool
    from scripts.case_runner.check_resolver import resolve_check_handler

    verify_text_contains = resolve_check_handler("verify_text_contains")
    verify_element_exists_check = resolve_check_handler("verify_element_exists_check")
    print("[0] handler resolved:", verify_text_contains, verify_element_exists_check)

    print("[1] 启动 PlaywrightEngine(headless, 系统 Chrome channel)")
    eng = PlaywrightEngine(headless=True, viewport_width=1280, viewport_height=800)
    await eng.start()
    page = eng.page
    print("    engine started, page =", page is not None)

    # ── 登录态注入（requests 拿 session cookie，跳过登录页重定向）──
    import requests

    sess = requests.Session()
    r = sess.post(
        SUT + "/login",
        data={"username": "admin", "password": "admin123"},
        allow_redirects=True,
        timeout=15,
    )
    print("[2] 登录 POST ->", r.status_code, r.url)
    cookie = sess.cookies.get_dict()
    print("    cookie keys =", list(cookie.keys()))
    if cookie:
        await eng._context.add_cookies(
            [
                {"name": k, "value": v, "domain": "127.0.0.1", "path": "/"}
                for k, v in cookie.items()
            ]
        )

    print("[3] goto 患者列表页")
    ok = await eng.goto(SUT + "/patients")
    print("    goto ok =", ok, "url =", page.url)

    title = await page.title()
    print("    page title =", title)

    # ── 真实下发动作指令 browser_click ──
    print("[4] _execute_tool browser_click(target_hint='text=新增患者')")
    res = await _execute_tool(
        "browser_click", {"target_hint": "text=新增患者"}, eng
    )
    print("    success =", getattr(res, "success", None))
    print("    message =", str(getattr(res, "message", ""))[:200])

    # ── 真实执行断言指令 ──
    print("[5] verify_element_exists_check(selector='body') —— 判真场景")
    r_true = await verify_element_exists_check(
        page, {"type": "element_exists_check", "selector": "body"}
    )
    print("    ->", r_true)

    print("[6] verify_element_exists_check(selector='#__not_exist__') —— 判伪场景")
    r_false = await verify_element_exists_check(
        page, {"type": "element_exists_check", "selector": "#__not_exist__"}
    )
    print("    ->", r_false)

    print("[7] verify_text_contains 判真/判伪")
    r_t = await verify_text_contains(
        page, {"type": "text_contains_check", "selector": "body", "expected": "a", "operator": "not_contains"}
    )
    print("    not_contains 'a' ->", r_t)

    print("[8] 关闭引擎")
    await eng.stop()
    print("SPIKE DONE")


if __name__ == "__main__":
    asyncio.run(main())
