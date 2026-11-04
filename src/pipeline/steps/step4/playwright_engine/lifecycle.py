# -*- coding: utf-8 -*-
from __future__ import annotations
from typing import Any
from src.core.guarded import guard

"""浏览器生命周期管理模块。"""

import os
import re
import json
import time
import asyncio
import logging
from datetime import datetime
from pathlib import Path

from playwright.async_api import Browser, BrowserContext, Page

# 页面装载期归属判据唯一源（GAP-01 生产者打标 / 消费者判定共用，勿重抄）
from src.pipeline.steps.step4.playwright_engine.js_load_phase import (
    PAGE_LOAD_KEY,
    PageLoadPhaseTracker,
)

# 静态资源后缀唯一源（本类请求过滤 + browser_core._worker_errors 共用，勿重抄）
_STATIC_SUFFIX = (
    ".js", ".css", ".png", ".jpg", ".gif", ".svg", ".ico",
    ".woff", ".woff2", ".ttf", ".map",
)

_log = logging.getLogger(__name__)

# ★ 浏览器资源关闭/停止硬超时（秒）。QA(2026-08-12) 定位：step4 收尾阶段
#   context.close / browser.close / playwright.stop 等 Playwright await 在 Windows IOCP
#   GetQueuedCompletionStatus 上可无超时永久挂起 → 事件循环卡死、执行槽被占、后续 run 429。
#   此处对收尾 await 全部加硬超时，超时即告警放弃，绝不无限等待。
_CLOSE_TIMEOUT_SEC: float = 30.0


async def _close_await(coro, label: str, timeout: float = _CLOSE_TIMEOUT_SEC) -> bool:
    """带硬超时的 Playwright 资源关闭/停止（防 Windows IOCP 无超时挂死）。

    超时只告警不抛错，返回 False；其它异常静默降级为 False。
    """
    try:
        await asyncio.wait_for(coro, timeout=timeout)
        return True
    except asyncio.TimeoutError:
        _log.warning(
            "[lifecycle] %s 超时(%ds)，放弃等待（防 IOCP 挂死）", label, timeout
        )
        return False
    except Exception as e:
        _log.debug("[lifecycle] %s 异常(忽略): %s", label, e)
        return False


class LifecycleMixin:
    """浏览器启动、停止、上下文切换等生命周期管理。"""

    _headless: bool
    _viewport_width: int
    _viewport_height: int
    _browser_info: dict
    _timeout_ms: int

    def _new_engine_context_kwargs(self) -> dict[str, Any]:
        """引擎自建 context 的**唯一**视口决策点。

        why: 三个建 context 的位置（CDP 接入 / 服务端启动 / fresh_context）曾都用
             `no_viewport=True`，视口随宿主窗口走 —— headless 下窗口默认 800×600，
             与配置的 1920×1080 完全脱钩，于是"默认 viewport"类断言（水平溢出/
             点击区域/响应式）全部在错误的宽度上判定，宽表格被误判为破版缺陷。
             引擎自建的 context 归引擎所有，视口必须由引擎配置决定。
        """
        return {
            "viewport": {
                "width": self._viewport_width,
                "height": self._viewport_height,
            },
            "is_mobile": False,
            "has_touch": False,
            "locale": "zh-CN",
        }

    # ★ [已复核-DeepSeek-V3 2026-08-16] 浏览器启动硬超时（秒）。
    #   浏览器启动/页面操作在缺依赖/资源争抢/页面不可达时可能无限挂起，耗尽后台线程与
    #   共享 DB 连接，进而拖垮 waitress 线程池（含 /api/status）。
    #   此处设硬性上界（默认 30s），超时即 fail-closed 返回，绝不无限等待。
    _launch_timeout: float = 30.0
    _playwright = None
    _browser: Browser | None = None
    _context: BrowserContext | None = None
    # page 的真实存放位；`_page` 是它的动态视图（见下方 property）。
    _page_real: Page | None = None

    @property
    def _page(self):
        """当前 page。

        并行 worker 内 → 该 worker 自己的 page（contextvar 隔离，并发互不覆盖）；
        无并行上下文（串行路径）→ 引擎主 page，与改动前语义完全一致。

        why: 消除「写共享单例 engine._page」导致的跨 worker page 竞态
             （实测会让干净用例落到别人的页面上，自造 ERROR）。
        """
        try:
            from src.pipeline.steps.step4.browser_core._worker_page import (
                get_worker_page,
            )

            _p = get_worker_page()
        except Exception:  # noqa: BLE001
            _p = None
        return _p if _p is not None else self._page_real

    @_page.setter
    def _page(self, value) -> None:
        self._page_real = value
    # ★ BUG-1(2026-08-05): 最近一次 goto 是否命中网络/连接错误（net::ERR_* 等）。
    #   由 _try_navigate 置位、goto 入口复位；_base.py / _agent_checks.py 在
    #   classify_failure 前读取，保证"目标不可达"能归因为 ENVIRONMENT_ERROR。
    _last_network_error: bool = False

    async def start(self) -> "LifecycleMixin":
        """启动浏览器。

        支持两种模式：
        1. 客户端浏览器模式（CDP 远程连接）：browser_info.cdp_endpoint 存在时，
           通过 Chrome DevTools Protocol 连接用户电脑的 Chrome。
           适用场景：被测系统在客户端内网，需用客户端浏览器访问。
        2. 服务端浏览器模式（launch）：默认模式，在服务端启动浏览器。
        """
        from playwright.async_api import async_playwright

        # ★ [已复核-DeepSeek-V3 2026-08-16] 启动硬超时：避免 async_playwright().start()
        #   在缺依赖/资源争抢时无限挂起，拖垮服务线程。
        try:
            self._playwright = await asyncio.wait_for(
                async_playwright().start(), timeout=self._launch_timeout
            )
        except asyncio.TimeoutError as _te:
            _log.error(
                "[start] Playwright 启动超时(%.0fs)，立即失败，避免服务挂死",
                self._launch_timeout,
            )
            try:
                if self._playwright is not None:
                    await self._playwright.stop()
            except Exception as _pe:
                _log.debug("[start] 超时清理 playwright 异常: %s", _pe)
            self._playwright = None
            raise RuntimeError(
                "浏览器自动化引擎启动超时（Playwright 启动 > %.0fs），"
                "请检查 Playwright/浏览器是否安装或资源是否被占用"
                % self._launch_timeout
            ) from _te

        bi = self._browser_info

        # ★ 客户端浏览器模式：通过 CDP 连接用户电脑的 Chrome
        cdp_endpoint = bi.get("cdp_endpoint")
        if cdp_endpoint:
            _log.info("  [Browser] 客户端浏览器模式 (CDP): %s", cdp_endpoint)
            import time as _time

            _cdp_t0 = _time.time()
            try:
                # ★ [已复核-DeepSeek-V3 2026-08-16] CDP 连接硬超时，避免连不上时无限挂。
                self._browser = await asyncio.wait_for(
                    self._playwright.chromium.connect_over_cdp(cdp_endpoint),
                    timeout=self._launch_timeout,
                )
            except Exception as _cdp_err:
                _log.warning("[start] CDP 连接失败，清理 playwright", exc_info=True)
                # ★ 埋点：CDP 连接失败
                self._record_cdp_metric(
                    "attach",
                    success=False,
                    duration_ms=(_time.time() - _cdp_t0) * 1000,
                    endpoint=cdp_endpoint,
                    error=str(_cdp_err),
                )
                try:
                    if self._playwright:
                        await self._playwright.stop()
                except Exception as _pe:
                    _log.debug("[start] 清理 playwright 异常: %s", _pe)
                self._playwright = None
                raise

            try:
                # CDP 模式：始终新建独立 context，不接管用户的 context/page
                # （避免测试操作污染用户浏览器状态、关闭用户标签页）
                self._context = await self._browser.new_context(
                    **self._new_engine_context_kwargs()
                )
                self._page = await self._context.new_page()
                # ★ [通用性兜底] 统一默认超时：所有高层 Playwright 操作
                #   （click/fill/goto/wait_for_*）均限时，杜绝 Windows IOCP
                #   无超时 await 永久挂死（根因修复，非单点补丁）。
                self._context.set_default_timeout(30000)
                self._page.set_default_timeout(30000)

                self._page.on("dialog", self._on_dialog)
                self._console_errors = []
                self._console_logs = []  # ★ C5 [未复核-需 GLM/DeepSeek 把关]: 全量 console 留存（含非 error 级）
                self._network_logs = []  # ★ C5 [未复核-需 GLM/DeepSeek 把关]: request/response 留存
                self._captured_dialogs = []
                self._load_phase = PageLoadPhaseTracker()
                self._page.on("framenavigated", self._on_framenavigated)
                self._page.on("load", self._on_load)
                self._page.on("console", self._on_console)
                self._page.on("pageerror", self._on_pageerror)
                self._page.on("request", self._on_request)
                self._page.on("response", self._on_response)
            except Exception as _ctx_err:
                _log.warning(
                    "[start] CDP 模式 context/page 获取失败，正在清理", exc_info=True
                )
                # ★ 埋点：CDP 连接成功但 context 创建失败
                self._record_cdp_metric(
                    "attach",
                    success=False,
                    duration_ms=(_time.time() - _cdp_t0) * 1000,
                    endpoint=cdp_endpoint,
                    error=str(_ctx_err),
                )
                # CDP 模式：不关闭 browser（那是用户的浏览器），只断开 playwright 连接
                try:
                    if self._playwright:
                        await self._playwright.stop()
                except Exception as e:
                    _log.debug("[start] 清理 playwright 时异常: %s", e)
                self._browser = None
                self._context = None
                self._page = None
                self._playwright = None
                raise

            # ★ 埋点：CDP 连接成功
            self._record_cdp_metric(
                "attach",
                success=True,
                duration_ms=(_time.time() - _cdp_t0) * 1000,
                endpoint=cdp_endpoint,
            )
            return self

        # ★ 服务端浏览器模式
        #   GUI 显式传入 path/channel 时优先使用（与原逻辑一致：bi 优先）；
        #   仅当 bi 未指定时才调用 BrowserBackendResolver 自动发现可用后端。
        browser_type = bi.get("engine", "chromium")
        launch_kwargs: dict = {
            "headless": self._headless,
            "args": ["--start-maximized"],
        }

        if bi.get("path"):
            # ★ GUI 显式指定可执行文件：直接采用，不调 resolver
            launch_kwargs["executable_path"] = bi["path"]
            _log.info(
                "  [Browser] 使用用户选择: %s (%s)", bi.get("name", ""), bi["path"]
            )
        elif bi.get("channel"):
            # ★ GUI 显式指定 channel：直接采用，不调 resolver
            launch_kwargs["channel"] = bi["channel"]
            _log.info(
                "  [Browser] 使用用户选择: %s (channel=%s)",
                bi.get("name", ""),
                bi["channel"],
            )
        else:
            # ★ 否则：调用 BrowserBackendResolver 自动发现可用浏览器后端。
            #   策略顺序（硬约束）：系统 Chrome/Edge channel → executable_path
            #   → Playwright 内置（最后回退）。全失败由 resolver 明确抛 RuntimeError。
            from src.pipeline.steps.step4.playwright_engine.backend_resolver import (
                BrowserBackendResolver,
            )

            _resolved = BrowserBackendResolver().resolve(engine=browser_type)
            # 用 resolver 返回的启动参数覆盖默认（保留 headless 来自 self._headless 的语义：
            # 若 self._headless 与默认不同，以 self._headless 为准，否则用 resolver 默认）。
            _resolved_kwargs = _resolved.get("launch_kwargs") or {}
            launch_kwargs["headless"] = self._headless
            launch_kwargs["args"] = _resolved_kwargs.get(
                "args", ["--start-maximized"]
            )
            if _resolved.get("channel"):
                launch_kwargs["channel"] = _resolved["channel"]
            if _resolved.get("executable_path"):
                launch_kwargs["executable_path"] = _resolved["executable_path"]
            _log.info(
                "  [Browser] 自动发现后端: kind=%s, channel=%s, exe=%s",
                _resolved.get("kind"),
                _resolved.get("channel"),
                _resolved.get("executable_path"),
            )

        # ★ [已复核-DeepSeek-V3 2026-08-16] 优雅降级：若显式指定了 executable_path 但该文件
        #   不存在，直接抛出明确错误（而非让 chromium.launch 挂起或报晦涩异常）。
        _explicit_exe = launch_kwargs.get("executable_path")
        if _explicit_exe and not Path(_explicit_exe).is_file():
            _log.error(
                "[start] 配置的浏览器可执行文件不存在: %s，立即失败", _explicit_exe
            )
            raise FileNotFoundError(
                "配置的浏览器可执行文件不存在: %s（请检查 Step4 浏览器配置或安装对应浏览器）"
                % _explicit_exe
            )

        try:
            # ★ [已复核-DeepSeek-V3 2026-08-16] 启动硬超时：chromium.launch 在缺依赖/资源争抢时
            #   可能无限挂起，此处设硬性上界，超时即失败返回，绝不无限挂。
            if browser_type == "firefox":
                self._browser = await asyncio.wait_for(
                    self._playwright.firefox.launch(**launch_kwargs),
                    timeout=self._launch_timeout,
                )
            elif browser_type == "webkit":
                self._browser = await asyncio.wait_for(
                    self._playwright.webkit.launch(**launch_kwargs),
                    timeout=self._launch_timeout,
                )
            else:
                self._browser = await asyncio.wait_for(
                    self._playwright.chromium.launch(**launch_kwargs),
                    timeout=self._launch_timeout,
                )
        except asyncio.TimeoutError as _le:
            _log.error(
                "[start] 浏览器 launch 超时(%.0fs)，立即失败，避免服务挂死",
                self._launch_timeout,
            )
            raise RuntimeError(
                "浏览器启动超时（%s.launch > %.0fs），请检查浏览器是否安装或资源是否被占用"
                % (browser_type, self._launch_timeout)
            ) from _le
        except Exception:
            # ★ BE-004: launch 失败时清理已启动的 playwright
            _log.warning("[start] 浏览器 launch 失败，清理 playwright", exc_info=True)
            try:
                if self._playwright:
                    await self._playwright.stop()
            except Exception as _pe:
                _log.debug("[start] 清理 playwright 异常: %s", _pe)
            self._playwright = None
            raise

        try:
            # 创建浏览器上下文：视口由引擎配置决定（引擎自建 context 归引擎所有）
            self._context = await self._browser.new_context(
                **self._new_engine_context_kwargs()
            )

            self._page = await self._context.new_page()
            # ★ [通用性兜底] 统一默认超时：所有高层 Playwright 操作限时，
            #   杜绝 IOCP 无超时 await 永久挂死（根因修复，非单点补丁）。
            self._context.set_default_timeout(30000)
            self._page.set_default_timeout(30000)
            # ★ v7: 自动处理原生 JS 弹窗（alert/confirm/prompt），避免阻塞所有交互
            self._page.on("dialog", self._on_dialog)
            # ★ 2026-07-06: 监听 console / pageerror，供 console_error_check 断言使用
            self._console_errors = []
            self._console_logs = []  # ★ C5 [未复核-需 GLM/DeepSeek 把关]: 全量 console 留存
            self._network_logs = []  # ★ C5 [未复核-需 GLM/DeepSeek 把关]: request/response 留存
            self._captured_dialogs = []
            self._load_phase = PageLoadPhaseTracker()
            self._page.on("framenavigated", self._on_framenavigated)
            self._page.on("load", self._on_load)
            self._page.on("console", self._on_console)
            self._page.on("pageerror", self._on_pageerror)
            self._page.on("request", self._on_request)
            self._page.on("response", self._on_response)

        except Exception:
            # context/page 创建失败，必须清理已启动的 browser 和 playwright
            _log.warning(
                "[start] 创建 context/page 失败，正在清理已分配资源", exc_info=True
            )
            try:
                if self._browser:
                    await _close_await(self._browser.close(), "启动失败清理 browser")
            except Exception as e:
                _log.debug("[start] 清理 browser 时异常: %s", e)
            try:
                if self._playwright:
                    await _close_await(
                        self._playwright.stop(), "启动失败清理 playwright"
                    )
            except Exception as e:
                _log.debug("[start] 清理 playwright 时异常: %s", e)
            self._browser = None
            self._context = None
            self._page = None
            self._playwright = None
            raise

        return self

    async def fresh_context(self) -> "LifecycleMixin":
        """创建全新的浏览器上下文（等于新无痕窗口），清除所有 cookies/storage。

        ★ CDP 模式：不关闭用户的 context（会关掉用户浏览器标签），
           而是新建一个独立 context 用于测试。
        """
        is_cdp = bool(self._browser_info.get("cdp_endpoint"))
        if not is_cdp:
            # 服务端模式：关闭旧 context
            with guard("fresh_context: if self._context: await _close_await", log=_log, level=logging.WARNING):
                if self._context:
                    await _close_await(
                        self._context.close(), "fresh_context 关闭旧 context"
                    )
        # CDP 模式：保留用户 context，新建独立 context
        self._context = await self._browser.new_context(
            **self._new_engine_context_kwargs()
        )
        self._page = await self._context.new_page()
        self._page.on("dialog", self._on_dialog)
        # ★ 2026-07-06: fresh_context 也需重新挂载 console / pageerror 监听
        self._console_errors = []
        self._console_logs = []  # ★ C5 [未复核-需 GLM/DeepSeek 把关]: 全量 console 留存
        self._network_logs = []  # ★ C5 [未复核-需 GLM/DeepSeek 把关]: request/response 留存
        self._captured_dialogs = []
        self._load_phase = PageLoadPhaseTracker()
        self._page.on("framenavigated", self._on_framenavigated)
        self._page.on("load", self._on_load)
        self._page.on("console", self._on_console)
        self._page.on("pageerror", self._on_pageerror)
        self._page.on("request", self._on_request)
        self._page.on("response", self._on_response)
        return self

    async def stop(self) -> None:
        """关闭浏览器并释放所有 Playwright 资源。每个资源独立关闭，确保不因前置失败而泄漏后续资源。

        ★ CDP 模式：不关闭 browser（那是用户的浏览器进程），只断开 playwright 连接。
        """
        is_cdp = bool(self._browser_info.get("cdp_endpoint"))

        # 1. 关闭 context（含 page）
        if self._context is not None:
            try:
                await _close_await(self._context.close(), "关闭 browser context")
            except Exception as e:
                _log.warning("[stop] 关闭 browser context 时异常: %s", e)
            finally:
                self._context = None
                self._page = None

        # 2. 关闭 browser
        if self._browser is not None:
            if is_cdp:
                # ★ CDP 模式：不关闭 browser（用户的浏览器），只断开引用
                _log.info("[stop] CDP 模式：保留客户端浏览器进程，仅断开连接")
            else:
                try:
                    await _close_await(self._browser.close(), "关闭 browser")
                except Exception as e:
                    _log.warning("[stop] 关闭 browser 时异常: %s", e)
            self._browser = None

        # 3. 停止 playwright
        if self._playwright is not None:
            try:
                await _close_await(self._playwright.stop(), "停止 playwright")
            except Exception as e:
                _log.warning("[stop] 停止 playwright 时异常: %s", e)
            finally:
                self._playwright = None

        # ★ 埋点：CDP 断开连接
        if is_cdp:
            self._record_cdp_metric(
                "detach",
                success=True,
                endpoint=self._browser_info.get("cdp_endpoint", ""),
            )

    def _record_cdp_metric(
        self,
        mode: str,
        success: bool,
        duration_ms: float = 0.0,
        endpoint: str = "",
        error: str = "",
    ) -> None:
        """★ CDP 指标埋点辅助方法。

        从当前上下文获取 project / run_id / version_id，调用 metrics_collector 记录。
        埋点失败不影响业务流程。
        """
        with guard("_record_cdp_metric: from src.extensions.metrics_collecto", log=_log):
            from src.extensions.metrics_collector import metrics_collector
            from src.core.config_loader import config
            from src.backend.state import (
                _current_project,
                _current_version,
                _active_runs,
            )

            _proj = _current_project() or getattr(config, "_project_name", "")
            _ver = _current_version() or ""
            _run_id = ""
            with __import__(
                "src.backend.state", fromlist=["_active_runs_lock"]
            )._active_runs_lock:
                if _active_runs:
                    _run_id = next(iter(_active_runs.keys()), "")
            if _proj and _run_id:
                metrics_collector.record_cdp_browser_session(
                    project=_proj,
                    run_id=_run_id,
                    mode=mode,
                    success=success,
                    connect_duration_ms=duration_ms,
                    endpoint=endpoint,
                    error=error,
                    version_id=_ver,
                )

    async def _on_dialog(self, dialog) -> None:
        """自动接受原生 JS 弹窗（alert/confirm/prompt），避免阻塞自动化流程。"""
        # ★  兼容修复: Playwright 新版用 dialog.type，旧版 dialog_type 已移除
        #   （AttributeError → pyee 回调异常 → 中断整批执行）。兼容两者。
        _dtype = getattr(dialog, "type", None) or getattr(dialog, "dialog_type", "") or ""
        _log.info(
            "[dialog] 自动接受弹窗: type=%s, message=%s",
            _dtype,
            dialog.message,
        )
        # ★ 2026-07-06: 缓存弹窗信息，供 dialog_check 断言使用（在 accept 前捕获）
        with guard("_on_dialog: self._captured_dialogs.append( { 'ty", log=_log):
            self._captured_dialogs.append(
                {
                    "type": _dtype,
                    "message": dialog.message or "",
                }
            )
        with guard("_on_dialog: await dialog.accept()", log=_log, level=logging.WARNING):
            await dialog.accept()  # ★ BE-003: async handler 确保 accept 被等待

    def _on_console(self, msg) -> None:
        """★ C5 [未复核-需 GLM/DeepSeek 把关]: 监听浏览器 console 消息。
        - 全量留存到 self._console_logs（默认留存，含非 error 级）
        - error 级额外写入 self._console_errors（维持既有 FAIL 归因语义，零契约变更）
        """
        with guard("_on_console: if not hasattr(self, '_console_logs'", log=_log):
            if not hasattr(self, "_console_logs"):
                self._console_logs = []
            _level = (getattr(msg, "type", "log") or "log")
            _text = getattr(msg, "text", "") or ""
            _url = ""
            _loc = getattr(msg, "location", None)
            if _loc and getattr(_loc, "get", None):
                _url = _loc.get("url", "") or ""
            # ★ 归属标记必须在**两个缓冲同时**写入（唯一判据 js_load_phase）：
            #   全量缓冲 `_console_logs` 是证据落库的输入源，`_console_errors` 是
            #   GAP-01 判定源；此前只给后者打标 → 证据侧无法切分页面级事实，
            #   同一页面缺陷被逐用例复制进 details（体积击穿 + 证据与判定口径不一致）。
            _is_load_phase = self._in_load_phase()
            self._console_logs.append(
                {
                    "level": str(_level),
                    "text": str(_text),
                    "url": str(_url),
                    "ts": datetime.now().isoformat(timespec="milliseconds"),
                    PAGE_LOAD_KEY: _is_load_phase,
                }
            )
            if _level == "error":
                self._console_errors.append(
                    {
                        "type": "console.error",
                        "text": str(_text),
                        "url": str(_url),
                        # ★ 装载期归属标记（唯一判据 js_load_phase）：装载期报错是页面级
                        #   事实，不归因到碰巧访问该页的用例（详见 js_load_phase 契约）。
                        PAGE_LOAD_KEY: _is_load_phase,
                    }
                )

    def _in_load_phase(self) -> bool:
        """当前是否处于页面装载期（归属判据唯一实现，供 console/pageerror 打标）。"""
        _tracker = getattr(self, "_load_phase", None)
        return bool(_tracker.is_load_phase()) if _tracker is not None else False

    def _on_framenavigated(self, frame) -> None:
        """主框架导航开始 → 进入装载期（子框架/广告 iframe 不计）。"""
        with guard("_on_framenavigated: self._page.main_frame", log=_log):
            if self._page is not None and frame == self._page.main_frame:
                self._load_phase.mark_navigated()

    def _on_load(self) -> None:
        """load 事件 → 装载期在短暂静默后收口。"""
        with guard("_on_load: self._load_phase.mark_loaded()", log=_log):
            self._load_phase.mark_loaded()


    def _on_request(self, request) -> None:
        """监听 HTTP 请求，留存（含 post_data）。"""
        with guard("_on_request: if not hasattr(self, '_network_logs'", log=_log):
            if not hasattr(self, "_network_logs"):
                self._network_logs = []
            url = request.url or ""
            path = (url.split("?", 1)[0].split("#", 1)[0]).lower()
            if any(path.endswith(s) for s in _STATIC_SUFFIX) or "favicon" in path:
                return
            post_data = None
            try:
                pd = request.post_data
                if pd:
                    try:
                        post_data = json.loads(pd)
                    except (ValueError, TypeError):
                        post_data = pd[:500]
            except Exception as _e:
                _log.debug("_on_request best-effort 容忍: %s", str(_e)[:120])
            self._network_logs.append(
                {
                    "event": "request",
                    "ts": datetime.now().isoformat(timespec="milliseconds"),
                    "method": (request.method or "").upper(),
                    "url": url,
                    "post_data": post_data,
                }
            )

    async def _on_response(self, response) -> None:
        """监听 HTTP 响应，与最近同 URL 请求配对，留存状态/耗时/响应体。"""
        with guard("_on_response: if not hasattr(self, '_network_logs'", log=_log):
            if not hasattr(self, "_network_logs"):
                self._network_logs = []
            url = response.url or ""
            path = (url.split("?", 1)[0].split("#", 1)[0]).lower()
            if any(path.endswith(s) for s in _STATIC_SUFFIX) or "favicon" in path:
                return
            now = time.time()
            # 配对最近未配对的同 URL request
            paired_ts = None
            elapsed_ms = None
            for rec in reversed(self._network_logs):
                if rec.get("event") == "request" and rec.get("url") == url and "elapsed_ms" not in rec:
                    paired_ts = rec.get("ts")
                    try:
                        from datetime import datetime as _dt

                        _pt = _dt.fromisoformat(paired_ts) if paired_ts else None
                        if _pt is not None:
                            elapsed_ms = max(int((now - _pt.timestamp()) * 1000), 0)
                    except Exception:
                        elapsed_ms = None
                    break
            resp_headers = {}
            resp_body = None
            try:
                resp_headers = dict(response.headers or {})
            except Exception as _e:
                _log.debug("_on_response best-effort 容忍: %s", str(_e)[:120])
            try:
                body = await response.text()
                if body and len(body) < 10000:
                    try:
                        resp_body = json.loads(body)
                    except (ValueError, TypeError):
                        resp_body = body[:500]
            except Exception as _e:
                _log.debug("_on_response best-effort 容忍: %s", str(_e)[:120])
            self._network_logs.append(
                {
                    "event": "response",
                    "ts": datetime.now().isoformat(timespec="milliseconds"),
                    "method": (response.request.method if hasattr(response, "request") else "").upper(),
                    "url": url,
                    "status": int(getattr(response, "status", 0) or 0),
                    "elapsed_ms": elapsed_ms,
                    "resp_headers": resp_headers,
                    "resp_body": resp_body,
                }
            )

    def begin_case_evidence(self, run_id: str, case_id: str) -> None:
        """★ C5 [未复核-需 GLM/DeepSeek 把关]: 标记当前引擎证据缓冲归属的 (run_id, case_id)。
        供 evidence_manager.consume_engine_evidence 关联 execution 使用。
        """
        self._exec_run_id = str(run_id or "")
        self._exec_case_id = str(case_id or "")

    def get_execution_evidence(self) -> dict[str, Any]:
        """★ C5 [未复核-需 GLM/DeepSeek 把关]: 取回当前引擎的 console/network 证据缓冲。
        返回可序列化 dict（含归属键），便于关联 execution / 留存入库。
        """
        return {
            "run_id": getattr(self, "_exec_run_id", "") or "",
            "case_id": getattr(self, "_exec_case_id", "") or "",
            "console": list(getattr(self, "_console_logs", []) or []),
            "network": list(getattr(self, "_network_logs", []) or []),
            "console_errors": list(getattr(self, "_console_errors", []) or []),
        }

    def _on_pageerror(self, err) -> None:
        """监听未捕获的 JS 异常（pageerror）。"""
        with guard("_on_pageerror: self._console_errors.append( { 'type", log=_log):
            self._console_errors.append(
                {
                    "type": "pageerror",
                    "text": (
                        str(err.message or err) if hasattr(err, "message") else str(err)
                    ),
                    "stack": str(err.stack or "") if hasattr(err, "stack") else "",
                    PAGE_LOAD_KEY: self._in_load_phase(),
                }
            )

    async def goto(self, url: str, wait_until: str = "load") -> bool:
        """导航到 URL。wait_until: load(默认)/domcontentloaded/networkidle。

        ★ Fix: SPA 页面加载稳定性增强
        - 先 load 事件，再等 networkidle（最多 10s）
        - 等待页面有可交互元素（最多 15s）
        - 失败时刷新重试 1 次
        """
        if not self._page:
            return False

        # ★ BUG-1(2026-08-05): 每次 goto 复位网络错误信号（仅本次导航的失败可置位，
        #   避免上一个用例的网络错误泄漏到后续用例的归因）。
        self._last_network_error = False

        # ★ 通用修复 (2026-07-18): 相对URL → 绝对URL 解析
        #   如果URL不是绝对路径，从config获取base_url并拼接
        if url and not url.startswith("http://") and not url.startswith("https://"):
            from src.core.config_loader import config

            _base = config.target_url or ""
            if _base:
                _base = _base.rstrip("/")
                url = _base + "/" + url.lstrip("/")
                _log.info("[goto] 相对URL解析: %s", url[:120])

        # ★ 通用修复 (2026-07-18): 剥离查询参数中的占位符值
        #   AI生成的URL可能包含占位符如 YZCODE.000, BLEND_000 等，
        #   这些参数会导致 Playwright 导航失败（invalid URL）
        if url and "?" in url:
            _path_part, _query_part = url.split("?", 1)
            _params = _query_part.split("&")
            _clean_params = []
            _stripped = []
            for _p in _params:
                if "=" in _p:
                    _k, _v = _p.split("=", 1)
                    _v_upper = _v.upper()
                    _is_ph = False
                    if _v_upper.endswith(".000") or _v_upper.endswith("_000"):
                        _is_ph = True
                    elif re.match(r"^[A-Z_]+\.[0-9]+$", _v_upper) or re.match(
                        r"^[A-Z_]+_[0-9]+$", _v_upper
                    ):
                        _is_ph = True
                    elif re.match(r"^[0-9]+$", _v) and len(_v) <= 5:
                        _is_ph = True
                    if _is_ph:
                        _stripped.append(f"{_k}={_v}")
                    else:
                        _clean_params.append(_p)
                elif _p.strip():
                    # 无值参数（如 "blendCode"）跳过
                    _stripped.append(_p)
                else:
                    _clean_params.append(_p)
            if _stripped:
                _clean_query = "&".join(_clean_params) if _clean_params else ""
                url = _path_part + ("?" + _clean_query if _clean_query else "")
                _log.info(
                    "[goto] 剥离占位符后URL: %s (剥离 %d: %s)",
                    url[:120],
                    len(_stripped),
                    _stripped[:3],
                )

        async def _try_navigate(_url: str) -> bool:
            try:
                await self._page.goto(
                    _url, wait_until=wait_until, timeout=self._timeout_ms
                )
                # ★ SPA 稳定性增强：load 后再等 networkidle
                try:
                    await self._page.wait_for_load_state("networkidle", timeout=10000)
                except Exception as _e:
                    _log.debug("wait_for_load_state 超时: %s", _e, exc_info=True)
                # 就绪 = 「文档 complete + 存在可交互元素」，条件驱动（热页 ~100ms，
                # 上限 3s fail-open）。原先此处是无条件 `asyncio.sleep(3)` + 最多
                # 15×1s 轮询：本地靶子白等 3s/次，全量 run 约 1000 次导航即白烧 ≥50min。
                from src.core.page_ready import wait_page_ready

                await wait_page_ready(self._page)
                return True
            except Exception as e:
                _err_str = str(e)
                # ★ 通用修复 (2026-07-18): SPA 页面跳转导致的 net::ERR_ABORTED 视为成功
                #   某SPA系统在 navigate 后会触发客户端路由跳转，
                #   导致浏览器中断原始导航。此时页面实际已加载，不应视为失败。
                if "ERR_ABORTED" in _err_str or "net::ERR_ABORTED" in _err_str:
                    _log.info(
                        "[goto] SPA 跳转中断导航 (ERR_ABORTED)，页面已加载: %s",
                        _url[:120],
                    )
                    # SPA 跳转中断导航 (ERR_ABORTED)：等就绪条件而非固定等待
                    from src.core.page_ready import wait_page_ready

                    await wait_page_ready(self._page)
                    return True
                # ★ BUG-1(2026-08-05): 导航网络/连接错误 → 置位 _last_network_error，
                #   供 classify_failure 归因为 ENVIRONMENT_ERROR（原逻辑仅 log+return False，
                #   信号丢失 → 误判 HARNESS_ERROR）。
                from src.pipeline.steps.step4.failure_classifier import (
                    is_network_error_text,
                )

                if is_network_error_text(_err_str):
                    self._last_network_error = True
                    _log.warning(
                        "[goto] 网络错误(ENVIRONMENT) %s: %s", _url, _err_str[:160]
                    )
                    return False
                _log.warning("[goto] 导航失败 %s: %s", _url, e)
                return False

        # 第一次尝试
        if await _try_navigate(url):
            return True

        # 重试：刷新页面
        _log.warning("[goto] 首次导航后页面可能未加载，刷新重试: %s", url)
        try:
            await self._page.reload(wait_until=wait_until, timeout=self._timeout_ms)
            try:
                await self._page.wait_for_load_state("networkidle", timeout=10000)
            except Exception as _e:
                _log.debug("wait_for_load_state 超时: %s", _e, exc_info=True)
            # SPA 渲染等待 → 同一条就绪条件（原为 sleep(3)+sleep(2) 共 5s 固定白等）
            from src.core.page_ready import wait_page_ready

            await wait_page_ready(self._page)
            return True
        except Exception as e:
            _log.warning("[goto] 刷新重试失败: %s", e)
            return False

    @property
    def page(self) -> Page | None:
        """获取底层 Playwright Page 对象。"""
        return self._page


