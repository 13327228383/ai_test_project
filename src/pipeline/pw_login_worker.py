# -*- coding: utf-8 -*-
"""pw_login_worker — Playwright 登录探测子进程 worker（根治 teardown 死锁）
====================================================================

背景：
  sync Playwright 的 teardown（browser.close() / sync_playwright.__exit__）
  在本机 Windows IOCP+greenlet 上**必然永久死锁**——无论在引擎后台线程内
  （watchdog 栈：_context_manager.py:56 greenlet_main → asyncio
  windows_events.py:778 _poll）还是在本 worker 的独立主线程内（登录成功后
  仍卡死在 close，90s faulthandler 实测）。

根治方案（一次性进程 + os._exit 跳过 teardown）：
  本 worker 由父进程（api_login_helpers._try_playwright_login）拉起：
    - stdin  读 JSON：{"base_url", "username", "password"}
    - 调用 _playwright_login_impl(..., on_result=_emit)（同一实现体，零逻辑复制）
    - on_result 在 impl 的 with 块内被回调：print 结果 JSON 后立即
      os._exit(0) —— **永不走到 with 退出/browser.close()**，死锁路径根本
      不会被执行；一次性进程的浏览器/管道资源由 OS 直接回收。
  父进程仍保留 45s 限时 + taskkill /F /T 整树回收兜底（复用 SutHarness 同款
  模式），覆盖 chromium 启动挂死等极端场景。
"""
import json
from src.core.guarded import guard
import os
import sys
import logging

_log = logging.getLogger(__name__)


def _emit(auth: dict) -> None:
    """on_result 回调：在 with 块内输出结果并硬退出（绕过 teardown 死锁）。"""
    print(json.dumps({"ok": True, "auth": auth or {}}), flush=True)
    os._exit(0)


def main() -> int:
    try:
        payload = json.loads(sys.stdin.read() or "{}")
        base_url = str(payload.get("base_url") or "")
        username = str(payload.get("username") or "")
        password = str(payload.get("password") or "")
        if not base_url:
            print(json.dumps({"ok": False, "error": "base_url empty"}), flush=True)
            return 0
        # 复用唯一实现体：登录策略/选择器/cookie 提取只此一份，不在 worker 里重写
        from src.pipeline.api_login_helpers import _playwright_login_impl

        _playwright_login_impl(
            base_url, username, password, on_result=_emit
        )
        # 理论不可达（_emit 内 os._exit）；防御性兜底
        print(json.dumps({"ok": True, "auth": {}}), flush=True)
    except Exception as e:  # worker 自身绝不挂：任何异常都以 JSON 干净退出
        with guard("main: print(json.dumps({'ok': False, 'erro", log=_log):
            print(json.dumps({"ok": False, "error": str(e)[:300]}), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
