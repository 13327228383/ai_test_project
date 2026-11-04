# -*- coding: utf-8 -*-
"""页面「可交互」就绪判定 —— 条件驱动，不是固定等待。

为什么存在
----------
导航后"等 SPA 渲染完"曾用 `asyncio.sleep(3)`（外加最多 15×1s 轮询）实现，属**固定等待**：
  · 热页（本地靶子）实际 100ms 内就已可交互，仍白等 3s；
  · 慢机器上 3s 又可能不够，仍然脆。
一次全量 run 约 1000 次导航 → 单这一处白烧 ≥50 分钟，是"几百条用例跑几十小时"的主因之一。

就绪的**真实条件**是：文档已就绪（readyState=complete）**且**页面存在可交互元素。
本模块直接等这个条件：热页 ~100ms 返回，冷页最多 `cap_ms` 后 fail-open 继续
（绝不把"等待超时"升级成用例失败——那是环境结论，不是产品结论）。

通用性：纯 W3C DOM 事实（readyState + 可交互元素集合），零业务/框架硬编码。
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

_log = logging.getLogger(__name__)

# 轮询间隔与上限。100ms 足够贴合前端首帧；3s 覆盖"慢但正常"的渲染。
READY_POLL_MS = 100
READY_CAP_MS = 3000

# ── 有界 DOM 查询原语（全引擎唯一实现）──
# why: Playwright 的 count()/evaluate()/content()/text_content()/title() **不接受 timeout**。
#   页面执行上下文不可用（导航中断/渲染进程崩溃）时它们会永不返回（实测 60s+ 无响应），
#   调用方没有用例级 wait_for 包裹时整轮 run 静默，只能被 run 级看门狗杀掉。
#   凡"单次页面查询"都必须经此有界，禁止在各业务模块各写一份 try/wait_for。
#
# 两档预算，均复用既有量级，不新增配置旋钮：
#   · QUERY_TIMEOUT_SEC —— 单次交互预算，与就绪探测同一 3s；
#   · PROBE_TIMEOUT_SEC —— 串行诊断/登录探测预算，与登录路径既有逐动作上界一致。
QUERY_TIMEOUT_SEC = READY_CAP_MS / 1000.0
PROBE_TIMEOUT_SEC = 10.0


async def await_bounded(
    awaitable: Any,
    timeout: float = QUERY_TIMEOUT_SEC,
    default: Any = None,
    what: str = "",
) -> Any:
    """给「不接受 timeout 参数的 Playwright 调用」套统一硬上界。

    超时或异常一律返回 `default`（fail-open，绝不把环境问题升级成用例结论）。
    """
    try:
        return await asyncio.wait_for(awaitable, timeout=timeout)
    except asyncio.TimeoutError:
        _log.warning(
            "[PageProbe] %s %.1fs 未返回（执行上下文不可用）", what or "查询", timeout
        )
        return default
    except Exception:  # noqa: BLE001
        return default


async def evaluate_bounded(
    target: Any, script: str, timeout: float = QUERY_TIMEOUT_SEC
) -> Any:
    """有界 evaluate（Page 或 Locator）。超时/异常返回 None。"""
    return await await_bounded(
        target.evaluate(script), timeout=timeout, default=None, what="evaluate"
    )


async def count_bounded(
    target: Any, selector: str = "", timeout: float = PROBE_TIMEOUT_SEC
) -> int:
    """有界 count：所有「元素是否存在」判定的唯一实现。

    target 可为 Page（需给 selector）或 Locator（selector 留空，直接 count 该定位）。
    返回 -1 表示探测超时（执行上下文不可用），0 表示确实不存在。
    调用方按 `> 0` 判定存在性时两者都等价为「不存在」，但日志/归因能区分「环境坏」与「元素缺」。
    """
    if target is None:
        return 0
    if selector:
        _coro = target.locator(selector).count()
        _what = f"count({selector[:60]})"
    elif hasattr(target, "count"):
        _coro = target.count()
        _what = "count(locator)"
    else:
        return 0
    _v = await await_bounded(_coro, timeout=timeout, default=-1, what=_what)
    try:
        return int(_v)
    except (TypeError, ValueError):
        return 0


async def content_bounded(page: Any, timeout: float = PROBE_TIMEOUT_SEC) -> str:
    """有界 page.content()：诊断取页面 HTML 时不得无限等待。"""
    if page is None:
        return ""
    return str(
        await await_bounded(
            page.content(), timeout=timeout, default="", what="content"
        )
        or ""
    )


# 单次 evaluate 统计可交互元素（等价于原 7 选择器 locator.count，但只 1 次 CDP 往返）
_READY_JS = (
    "() => document.readyState === 'complete' && "
    'document.querySelectorAll('
    "'button,a,input,select,textarea,[role=\"button\"],[role=\"tab\"]'"
    ").length > 0"
)


async def wait_page_ready(page, cap_ms: int = READY_CAP_MS) -> bool:
    """等到页面可交互（有界、条件驱动）。返回是否在上限内就绪。

    fail-open：探测异常或达上限都返回 False 并放行，不抛异常、不阻断用例。
    """
    if page is None:
        return False
    loop = asyncio.get_event_loop()
    t0 = loop.time()
    while True:
        # 每次探测的时限 = 本函数剩余预算。why: 原实现只在 evaluate **返回后**检查
        # 上限，执行上下文不可用时 page.evaluate 会一直不返回（实测 60s+），
        # 声明上的「有界」从未生效 → 调用方整体空转。
        _remain = cap_ms / 1000.0 - (loop.time() - t0)
        if _remain <= 0:
            _log.debug("[page_ready] 达上限 %dms，fail-open 继续", cap_ms)
            return False
        try:
            if await asyncio.wait_for(page.evaluate(_READY_JS), timeout=_remain):
                return True
        except asyncio.TimeoutError:
            _log.debug(
                "[page_ready] 就绪探测 %.2fs 未返回（执行上下文不可用），fail-open 继续",
                _remain,
            )
            return False
        except Exception as _e:  # noqa: BLE001
            _log.debug("[page_ready] 就绪探测异常(视为就绪放行): %s", str(_e)[:60])
            return False
        if (loop.time() - t0) * 1000 >= cap_ms:
            _log.debug("[page_ready] 达上限 %dms，fail-open 继续", cap_ms)
            return False
        await asyncio.sleep(READY_POLL_MS / 1000.0)
