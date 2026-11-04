# -*- coding: utf-8 -*-
"""网络故障注入（W3 T04，L0-5）—— 基于 Playwright page.route，零新依赖。

args: {"mode":"offline"|"abort"|"fulfill"|"delay"|"throttle"|"connection_reset",
       "url_pattern":"/api/orders",
       "status":500, "body":"{}", "delay_ms":5000,
       "delay_distribution":"uniform"|"normal",
       "delay_min":1000, "delay_max":5000,
       "delay_mean":3000, "delay_stddev":500,
       "download_throughput":50000, "upload_throughput":20000, "latency":100}

  offline     → context.set_offline(True)（全局断网，用后必恢复）
  abort       → page.route(url, lambda r: r.abort())（仅拦截匹配 URL）
  fulfill     → page.route(url, lambda r: r.fulfill(status=..., body=...))
  delay       → page.route(url, handler 内 await page.wait_for_timeout(delay_ms))
                ★ delay_distribution: uniform|normal 支持随机延迟分布
  throttle    → CDP Network.emulateNetworkConditions 带宽限制
                （download_throughput/upload_throughput bytes/s, latency ms）
  connection_reset → page.route(url, lambda r: r.abort("connectionreset"))

★ 安全铁律：仅当 NETWORK_FAULT_ENABLED 且用例显式声明 network_fault 动作才生效。
★ 用后必恢复：offline 恢复 set_offline(False)、route 用 unroute 清除、
  throttle 用 CDP 重置。

★ W3 T04 关键改动 [已复核-DeepSeek-V4-Flash 2026-08-05]（2026-08-05）：
  网络故障注入（基于 Playwright page.route，零新依赖）；NETWORK_FAULT_ENABLED 门控。
★ 增强（2026-08-12）：throttle / connection_reset / delay_distribution / unroute 清理。
"""
from __future__ import annotations
from src.core.guarded import guard

import logging
import random
from typing import Any

_log = logging.getLogger(__name__)


class ToolExecutionResult:
    """与 browser_tools 一致的执行结果结构（避免循环依赖）。"""

    def __init__(self, ok: bool, message: str = "", data: Any = None):
        self.ok = ok
        self.message = str(message or "")
        self.data = data

    def to_dict(self) -> dict[str, Any]:
        return {"ok": self.ok, "message": self.message, "data": self.data}


def _enabled() -> bool:
    try:
        from src.core.pipeline_config import _read_config_value as _cfg

        return bool(_cfg("NETWORK_FAULT_ENABLED", False))
    except Exception:  # noqa: BLE001
        return False


def _get_page(engine) -> Any:
    """从 engine 取 page（兼容 BrowserCore engine.page / engine._page）。"""
    if engine is None:
        return None
    page = getattr(engine, "page", None) or getattr(engine, "_page", None)
    return page


def _route_pattern(url_pattern: str) -> str:
    """url_pattern → Playwright glob（含 http 前缀补 **）。"""
    p = str(url_pattern or "").strip()
    if not p:
        return "**/*"
    if p.startswith("http://") or p.startswith("https://"):
        return p
    return f"**{p}"


def _track_route(page, url_pattern: str) -> None:
    """★ 记录已注入的 route pattern 到 page 上，供 recover_network_fault unroute 使用。"""
    if not hasattr(page, "_network_fault_routes"):
        page._network_fault_routes = []  # type: ignore[attr-defined]
    page._network_fault_routes.append(url_pattern)  # type: ignore[attr-defined]


async def _unroute_all(page) -> None:
    """★ 显式清理 page 上所有已注入的 network_fault route。

    逐个 unroute 已记录的 pattern，再尝试 unroute("**/*") 兜底。
    任何异常静默吞掉（recover 不阻断主流程）。
    """
    routes = getattr(page, "_network_fault_routes", None)
    if routes:
        for pattern in routes:
            with guard("_unroute_all: await page.unroute(pattern)", log=_log):
                await page.unroute(pattern)
        routes.clear()
    # 兜底：尝试 unroute 全局
    with guard("_unroute_all: await page.unroute('**/*')", log=_log):
        await page.unroute("**/*")


async def _reset_throttle(page) -> None:
    """★ throttle 恢复：通过 CDP 重置网络条件为无限制。"""
    cdp_sessions = getattr(page, "_network_fault_cdp_sessions", None)
    if not cdp_sessions:
        return
    for session in cdp_sessions:
        with guard("_reset_throttle: await session.send( 'Network.emulate", log=_log):
            await session.send(
                "Network.emulateNetworkConditions",
                {
                    "offline": False,
                    "downloadThroughput": -1,
                    "uploadThroughput": -1,
                    "latencyMs": 0,
                },
            )
    cdp_sessions.clear()


async def apply_network_fault(engine, args: dict) -> ToolExecutionResult:
    """注入网络故障。返回 ToolExecutionResult（ok/message）。

    未启用 NETWORK_FAULT_ENABLED → 明确错误（不允许静默生效）。
    """
    if not _enabled():
        return ToolExecutionResult(
            False, "网络故障注入被禁用（NETWORK_FAULT_ENABLED=False），拒绝生效"
        )
    if not isinstance(args, dict):
        return ToolExecutionResult(False, "network_fault 参数必须为 dict")
    mode = str(args.get("mode", "") or "").strip().lower()
    _VALID_MODES = ("offline", "abort", "fulfill", "delay", "throttle", "connection_reset")
    if mode not in _VALID_MODES:
        return ToolExecutionResult(
            False,
            f"未知故障模式: {mode}（{'/'.join(_VALID_MODES)}）",
        )
    page = _get_page(engine)
    if page is None:
        return ToolExecutionResult(False, f"无法注入 {mode}：engine 无 page 引用")
    url_pattern = _route_pattern(args.get("url_pattern", ""))

    try:
        if mode == "offline":
            context = getattr(page, "context", None)
            if context is None:
                return ToolExecutionResult(
                    False, "offline 需要 page.context（set_offline）"
                )
            await context.set_offline(True)
            return ToolExecutionResult(True, f"offline 已注入（全局断网）")
        if mode == "abort":
            _track_route(page, url_pattern)
            await page.route(url_pattern, lambda route: route.abort())
            return ToolExecutionResult(True, f"abort 已注入: {url_pattern}")
        if mode == "connection_reset":
            # ★ 区分中止类型：connectionreset 模拟 TCP 连接重置
            _track_route(page, url_pattern)

            async def _conn_reset(route):
                await route.abort("connectionreset")

            await page.route(url_pattern, _conn_reset)
            return ToolExecutionResult(
                True, f"connection_reset 已注入: {url_pattern}"
            )
        if mode == "fulfill":
            status = int(args.get("status", 500) or 500)
            body = str(args.get("body", "{}") or "{}")

            async def _fulfill(route):
                await route.fulfill(
                    status=status, body=body, content_type="application/json"
                )

            _track_route(page, url_pattern)
            await page.route(url_pattern, _fulfill)
            return ToolExecutionResult(
                True, f"fulfill 已注入: {url_pattern} status={status}"
            )
        if mode == "throttle":
            # ★ CDP Network.emulateNetworkConditions 带宽限制
            download_throughput = int(
                args.get("download_throughput", 50000) or 50000
            )
            upload_throughput = int(
                args.get("upload_throughput", 20000) or 20000
            )
            latency_ms = int(args.get("latency", 100) or 100)

            context = getattr(page, "context", None)
            if context is None:
                return ToolExecutionResult(
                    False, "throttle 需要 page.context（CDP session）"
                )
            cdp_session = await context.new_cdp_session(page)
            await cdp_session.send(
                "Network.emulateNetworkConditions",
                {
                    "offline": False,
                    "downloadThroughput": download_throughput,
                    "uploadThroughput": upload_throughput,
                    "latencyMs": latency_ms,
                },
            )
            # 存储 CDP session 供 recover 使用
            if not hasattr(page, "_network_fault_cdp_sessions"):
                page._network_fault_cdp_sessions = []  # type: ignore[attr-defined]
            page._network_fault_cdp_sessions.append(cdp_session)  # type: ignore[attr-defined]
            return ToolExecutionResult(
                True,
                f"throttle 已注入: ↓{download_throughput}B/s "
                f"↑{upload_throughput}B/s latency={latency_ms}ms",
            )
        # delay（含 delay_distribution 增强）
        delay_ms = max(int(args.get("delay_ms", 5000) or 5000), 0)
        delay_distribution = str(
            args.get("delay_distribution", "") or ""
        ).strip().lower()

        if delay_distribution == "uniform":
            delay_min = max(int(args.get("delay_min", 0) or 0), 0)
            delay_max = max(
                int(args.get("delay_max", delay_ms) or delay_ms), delay_min
            )

            async def _delay(route):
                actual = random.uniform(delay_min, delay_max)
                await page.wait_for_timeout(int(actual))
                await route.continue_()

            _dist_desc = f"uniform[{delay_min},{delay_max}]ms"
        elif delay_distribution == "normal":
            delay_mean = float(args.get("delay_mean", delay_ms) or delay_ms)
            delay_stddev = max(
                float(args.get("delay_stddev", 0) or 0), 0
            )

            async def _delay(route):
                actual = max(0, random.gauss(delay_mean, delay_stddev))
                await page.wait_for_timeout(int(actual))
                await route.continue_()

            _dist_desc = f"normal(μ={delay_mean},σ={delay_stddev})ms"
        else:
            async def _delay(route):
                await page.wait_for_timeout(delay_ms)
                await route.continue_()

            _dist_desc = f"{delay_ms}ms"

        _track_route(page, url_pattern)
        await page.route(url_pattern, _delay)
        return ToolExecutionResult(
            True, f"delay 已注入: {url_pattern} {_dist_desc}"
        )
    except Exception as e:  # noqa: BLE001
        _log.warning("[network_fault] 注入异常: %s", e)
        return ToolExecutionResult(False, f"注入异常: {type(e).__name__}: {e}")


async def recover_network_fault(engine, mode: str) -> None:
    """恢复故障注入。

    ★ 增强（2026-08-12）：
      - offline → set_offline(False)
      - abort/fulfill/delay/connection_reset → 显式 page.unroute() 清理 route
      - throttle → CDP Network.emulateNetworkConditions 重置为无限制
      任何异常只记日志，绝不阻断执行流。
    """
    if not _enabled():
        return
    page = _get_page(engine)
    if page is None:
        return
    with guard("recover_network_fault: if mode == 'offline': context = geta", log=_log, level=logging.WARNING):
        if mode == "offline":
            context = getattr(page, "context", None)
            if context is not None:
                await context.set_offline(False)

        # ★ 显式 unroute 清理 route-based 故障
        if mode in ("abort", "fulfill", "delay", "connection_reset"):
            await _unroute_all(page)

        # ★ throttle 恢复：CDP 重置网络条件
        if mode == "throttle":
            await _reset_throttle(page)

        # ★ 全模式兜底：尝试 unroute 所有残余 route（防止 mode 不匹配时遗漏）
        if mode not in ("offline", "throttle"):
            await _unroute_all(page)


def register_network_fault_handlers(handlers: dict) -> None:
    """向 _single._TOOL_HANDLERS 注入 "network_fault" 分发。

    handlers 需支持 {"network_fault": callable} 注入；幂等（已存在则跳过）。
    """
    if handlers is None:
        return
    if "network_fault" in handlers:
        return
    handlers["network_fault"] = "network_fault"  # 占位：实际由 _single 内联 lambda 处理
