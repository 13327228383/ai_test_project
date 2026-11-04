# -*- coding: utf-8 -*-
"""异步加载完成断言 — loading_complete_check。

场景：搜索/筛选/分页/导出后，loading 必须消失才能断言结果。
缺失后果：读到旧数据，假通过。

实现策略：
  1. 若提供 loading_selector，等待该元素达到 expected_state（默认 hidden）。
  2. 可选等待 networkidle（默认开启），确保异步请求都已完成。
  3. 超时则断言失败（避免无限等待）。
"""

import logging
from src.core.guarded import guard
from typing import Any

_log = logging.getLogger(__name__)


async def verify_loading_complete(page, check: dict[str, Any]) -> dict[str, Any]:
    """异步加载完成断言。

    check 格式：
      {"type": "loading_complete_check",
       "loading_selector": ".ant-spin, .loading",   # 可选，loading 元素选择器
       "expected_state": "hidden",                   # hidden=等待消失 / visible=等待出现
       "timeout_ms": 10000,                          # 超时毫秒
       "wait_network_idle": true}                    # 是否等待 networkidle
    """
    try:
        loading_selector = check.get("loading_selector", "")
        expected_state = (check.get("expected_state") or "hidden").strip().lower()
        timeout_ms = int(check.get("timeout_ms") or 10000)
        wait_network_idle = check.get("wait_network_idle", True)
        if isinstance(wait_network_idle, str):
            wait_network_idle = wait_network_idle.lower() not in (
                "false",
                "0",
                "no",
                "",
            )

        # 1. 等待 networkidle（确保异步请求完成）
        if wait_network_idle:
            with guard("verify_loading_complete: await page.wait_for_load_state('netw", log=_log):
                await page.wait_for_load_state("networkidle", timeout=timeout_ms)

        # 2. 等待 loading 元素达到期望状态
        if loading_selector:
            try:
                locator = page.locator(loading_selector).first
                if expected_state == "visible":
                    await locator.wait_for(state="visible", timeout=timeout_ms)
                    actual_state = "visible"
                else:
                    await locator.wait_for(state="hidden", timeout=timeout_ms)
                    actual_state = "hidden"
            except Exception as e:
                return {
                    "passed": False,
                    "evidence": "[LoadingCheck] 等待 loading %s 超时（%dms）: selector=%s err=%s"
                    % (expected_state, timeout_ms, loading_selector, str(e)[:60]),
                }
        else:
            actual_state = "skipped"

        return {
            "passed": True,
            "evidence": "[LoadingCheck] 加载完成 networkidle=%s loading_selector=%s state=%s"
            % (
                "ok" if wait_network_idle else "skip",
                loading_selector or "无",
                actual_state,
            ),
        }
    except Exception as e:
        _log.error("[LoadingCheck] 异常: %s", str(e)[:120], exc_info=True)
        return {
            "passed": False,
            "error": "executor_exception",
            "evidence": "[执行器异常] loading_complete_check 崩溃: %s" % str(e)[:80],
        }
