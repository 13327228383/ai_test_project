# -*- coding: utf-8 -*-
"""
BrowserCore 并行执行 mixin

职责：
- _execute_intents_parallel — 并行执行多个意图
"""

import asyncio
from src.core.guarded import guard
import logging
import time
from datetime import datetime
from typing import Any

from src.core.config_loader import config
from src.ai.ai_inflight import bounded_wait_inflight
from src.pipeline.steps.step4.browser_core.case_verdict import count_bucket
from src.pipeline.utils.pause_controller import pause_controller
from src.pipeline.steps.step4.browser_core._worker_errors import (
    make_worker_handlers,
    make_worker_network_handlers,
    set_worker_errors,
    set_worker_network,
)
from src.pipeline.steps.step4.browser_core._worker_page import (
    set_worker_page,
)
from src.pipeline.steps.step4.playwright_engine.js_load_phase import (
    PageLoadPhaseTracker,
)

_log = logging.getLogger(__name__)


try:
    from gui_app import log_step_ok, log_step_fail, update_monitor_dom
except Exception as _e:
    _log.debug("[parallel] 非关键异常: %s", _e, exc_info=True)
    log_step_ok = lambda *a: print(
        "    [Step %d/%d] OK (%.1fs) — %s" % (a[0], a[1], a[2], a[3][:50])
    )
    log_step_fail = lambda *a: print(
        "    [Step %d/%d] FAIL (%.1fs) — %s" % (a[0], a[1], a[2], a[3][:50])
    )
    update_monitor_dom = lambda c, o: None


class _BrowserCoreParallelMixin:
    """并行执行能力。"""

    async def _execute_intents_parallel(
        self,
        candidates,
        browser_info=None,
        headless=True,
        timeout_ms=None,
        user_id=None,
        progress_callback=None,
        max_parallel=4,
    ):
        from src.pipeline.steps.step4.browser_ai import BrowserAi
        from src.pipeline.steps.step4.playwright_engine import PlaywrightEngine
        from src.pipeline.steps.step4.api_tracker import ApiTracker
        from src.pipeline.steps.step4.data_factory_core import DataFactory

        if not candidates:
            return {"passed": 0, "failed": 0, "total": 0, "results": []}

        _log.info(
            "[BrowserCore] 并行执行: %d 个意图, max_parallel=%d",
            len(candidates),
            max_parallel,
        )
        _parallel_start_ts = time.time()  # ★ H4-3: 并行执行耗时起点

        all_results = []
        total_passed = 0
        total_failed = 0
        total_skipped = 0
        total = len(candidates)

        engine = PlaywrightEngine(
            headless=headless, timeout_ms=timeout_ms, browser_info=browser_info
        )
        self._engine = engine
        ai = BrowserAi(engine)
        api_tracker = ApiTracker(engine)
        data_factory = DataFactory()
        data_factory.set_engine(engine)
        await engine.start()
        _log.info("[BrowserCore] 浏览器已启动 (headless=%s)", headless)

        try:
            # ★ S45-009 接线补全（2026-09-22）：运行级登录前占账号（防多运行同账号）
            self._allocate_current_identity()
            if not await ai.login():
                # ★ 治根修复：登录失败不中止所有用例，标记为 BLOCKED（环境问题，非用例缺陷）
                _login_error_msg = "登录失败"
                try:
                    if engine._page:
                        _page_text = await engine._page.text_content("body") or ""
                        _login_error_msg = _page_text[:200].strip() or _login_error_msg
                except Exception as _r31:
                    _log.debug(
                        "[parallel] 异常降级(pass): %s", str(_r31)[:60], exc_info=True
                    )
                    pass
                # 落库走 base._block_all_candidates（与「目标不可达」同一实现，
                # 同一语义只在唯一一处维护）。
                _blocked = self._block_all_candidates(
                    candidates, f"登录失败: {_login_error_msg[:200]}", user_id
                )
                _log.warning(
                    "[BrowserCore] 登录失败 → 批量写 BLOCKED: %d/%d — %s",
                    _blocked,
                    total,
                    _login_error_msg[:120],
                )
                return {
                    "passed": 0,
                    "failed": 0,
                    "blocked": _blocked,
                    "total": total,
                    "error": f"登录失败: {_login_error_msg[:120]}",
                }

            # ★ FIX (2026-07-11): 保存登录 storage_state（含 cookies + localStorage + sessionStorage）
            #   原因：parallel 每个 worker 创建独立 new_context()，新 context 无 session 状态，
            #   导致每条用例导航到目标页面时被重定向到 /login（会话过期）。
            #   cookies() 只返回 HTTP cookies，SPA 应用可能用 localStorage/sessionStorage 存 token，
            #   必须用 storage_state() 捕获完整状态。
            _saved_storage_state = None
            with guard("_execute_intents_parallel: _main_ctx = engine._page.context if ", log=_log, level=logging.WARNING):
                _main_ctx = engine._page.context if engine._page else None
                if _main_ctx:
                    _saved_storage_state = await _main_ctx.storage_state()
                    _cookies_count = (
                        len(_saved_storage_state.get("cookies", []))
                        if _saved_storage_state
                        else 0
                    )
                    _origins_count = (
                        len(_saved_storage_state.get("origins", []))
                        if _saved_storage_state
                        else 0
                    )
                    _log.info(
                        "[BrowserCore] 已保存登录 storage_state: %d cookies, %d origins",
                        _cookies_count,
                        _origins_count,
                    )

            # ★ 通用修复(2026-07-21 v10): 显式提取 localStorage + 用 add_init_script 注入
            #   根因(3层追问):
            #     1. 表层: worker page 导航到 base URL 后被 某SPA系统 SPA 重定向到 /login
            #     2. 中层: storage_state 虽传递了 localStorage (1 origin)，但 SPA 加载时 JWT 未生效
            #     3. 根因: Playwright storage_state 的 localStorage 应用依赖 origin 精确匹配，
            #        在某些 SPA 场景下有时序问题；某SPA系统 用 JWT in localStorage (0 cookies)，
            #        storage_state 传递不可靠
            #   通用性: 适用于所有用 localStorage 存 token 的 SPA 系统（JWT/OAuth/自定义 token）
            #   合理性: add_init_script 在页面任何 JS 执行前运行，确保 token 已就位
            #   覆盖性: 覆盖所有 worker context 创建路径
            _saved_local_storage_items = []
            _saved_local_storage_origin = ""
            with guard("_execute_intents_parallel: if engine._page: _saved_local_storag", log=_log, level=logging.WARNING):
                if engine._page:
                    _saved_local_storage_items = await engine._page.evaluate(
                        "() => { let r = []; for (let i = 0; i < localStorage.length; i++) "
                        "{ let k = localStorage.key(i); r.push([k, localStorage.getItem(k)]); } return r; }"
                    )
                    _saved_local_storage_origin = await engine._page.evaluate(
                        "() => window.location.origin"
                    )
                    _log.info(
                        "[BrowserCore] 提取 localStorage: %d 项, origin=%s",
                        len(_saved_local_storage_items),
                        _saved_local_storage_origin,
                    )
                    # 打印 localStorage 键名（不打印 value，避免泄露 token）
                    _ls_keys = (
                        [item[0] for item in _saved_local_storage_items]
                        if _saved_local_storage_items
                        else []
                    )
                    _log.info("[BrowserCore] localStorage keys: %s", _ls_keys)

            # ★ Phase 1 (2026-07-11): 页面探索 + DOM 预缓存
            #   登录后自动浏览所有测试用例涉及的页面，缓存 DOM 结构，
            #   供后续结构化路径解析 target_hint 使用。
            from src.pipeline.steps.step4.browser_core.page_explorer import PageExplorer

            _app_url = getattr(config, "app_url", "") or ""
            _page_explorer = PageExplorer()
            _explored = await _page_explorer.explore_from_test_cases(
                engine, candidates, base_url=_app_url
            )
            self._page_explorer = _page_explorer
            if _explored > 0:
                _stats = _page_explorer.get_stats()
                _log.info(
                    "[BrowserCore] 页面探索: %d 页面, %d 元素, %d 关键词",
                    _stats["explored_pages"],
                    _stats["total_elements"],
                    _stats["indexed_keywords"],
                )

            # ★ v3.1 (2026-07-11): 分批并行执行，替代 semaphore 方案
            #   旧方案创建 119 个 asyncio task 全部竞争 semaphore，导致前 2 个用例耗时 70s
            #   时，剩余 117 个 worker 全部超时（60s < 70s）。
            #   新方案：每批最多 max_parallel 个 task，等全批完成后再起下一批。
            _browser = engine._browser  # 共享浏览器实例
            _main_page = (
                engine._page
            )  # ★ v3.1: 保存主页面引用，避免 worker 间的 page 竞态

            async def _run_one_worker(intent, step_num):
                """执行单个用例，使用独立 worker context"""
                _worker_context = None
                _worker_page = None
                try:
                    await pause_controller.async_sleep_while_paused()

                    if self._force_stop_check and self._force_stop_check():
                        if not self._stop_printed:
                            _log.warning("[STOP] 收到停止信号，中止执行")
                            self._stop_printed = True
                        return None

                    _worker_context = await _browser.new_context(
                        no_viewport=True,
                        is_mobile=False,
                        has_touch=False,
                        locale="zh-CN",
                        storage_state=_saved_storage_state,
                    )
                    # ★ 通用修复(2026-07-21 v10): 用 add_init_script 显式注入 localStorage
                    #   在页面任何 JS 执行前运行，确保 JWT/token 已就位，避免 SPA 重定向到 /login
                    #   通用性: 适用于所有用 localStorage 存 token 的 SPA 系统
                    if _saved_local_storage_items and _saved_local_storage_origin:
                        import json as _json

                        with guard("_run_one_worker: _items_json = _json.dumps( [ [str(k)", log=_log, level=logging.WARNING):
                            _items_json = _json.dumps(
                                [
                                    [str(k), str(v)]
                                    for k, v in _saved_local_storage_items
                                ],
                                ensure_ascii=False,
                            )
                            _origin_str = str(_saved_local_storage_origin)
                            _init_script = (
                                "(() => { try {"
                                f"  if (window.location.origin !== {_json.dumps(_origin_str)}) return;"
                                f"  const items = {_items_json};"
                                "  for (const [k, v] of items) {"
                                "    try { localStorage.setItem(k, v); } catch(e) {}"
                                "  }"
                                "} catch(e) {} })();"
                            )
                            await _worker_context.add_init_script(_init_script)
                            _log.info(
                                "[BrowserCore] worker context 已注入 localStorage init_script (%d 项)",
                                len(_saved_local_storage_items),
                            )

                    _worker_page = await _worker_context.new_page()
                    _worker_page.on("dialog", engine._on_dialog)
                    # ★ GAP-01 (2026-08-16) [已复核-RISK-需人工跟进] → T1 FIX [未复核-需 GLM/DeepSeek 把关]:
                    #   补挂 worker page 的 console / pageerror 监听（与 lifecycle.py
                    #   start/fresh_context 完全同语义），使并行路径的 JS 运行时报错
                    #   也能被 _base.py 用例级消费（GAP-01 消费逻辑）翻 FAIL。
                    #   ★ RISK(DeepSeek-V3 2026-08-16): 原实现把 worker page 的报错
                    #     写入共享 engine._console_errors 缓冲，同批并发 worker 互相
                    #     污染（干净用例可能读到同批脏用例的报错而误 FAIL）。方向为
                    #     「过报」而非「漏报」，与 GAP-01 补漏目标一致；精确隔离需
                    #     per-worker 缓冲（属数据契约变更），本轮按任务约束不硬塞。
                    #   ★ T1 FIX(2026-08-16): 改为 per-worker 缓冲隔离。
                    #     每个 worker 维护独立 _worker_errors 列表，经 contextvars
                    #     绑定到当前 asyncio task（set_worker_errors）；处理器由
                    #     make_worker_handlers 构造，与 lifecycle._on_console /
                    #     _on_pageerror 完全同语义，仅写入目标改为 worker 自己的缓冲；
                    #     _base.py 用例消费时只读自己 worker 的缓冲（get_worker_errors），
                    #     串行路径回落 engine._console_errors（零契约变更）。
                    _worker_errors = []
                    set_worker_errors(_worker_errors)
                    # ★ 装载期归属（判据唯一源 js_load_phase）：worker 自己的 tracker，
                    #   与 serial 同语义地给 console/pageerror 打「装载期」标记。
                    _worker_load_phase = PageLoadPhaseTracker()
                    _worker_page.on(
                        "framenavigated",
                        lambda _f, _lp=_worker_load_phase, _p=_worker_page: (
                            _lp.mark_navigated() if _f == _p.main_frame else None
                        ),
                    )
                    _worker_page.on("load", lambda _lp=_worker_load_phase: _lp.mark_loaded())
                    _worker_on_console, _worker_on_pageerror = make_worker_handlers(
                        _worker_errors, _worker_load_phase
                    )
                    _worker_page.on("console", _worker_on_console)
                    _worker_page.on("pageerror", _worker_on_pageerror)
                    # ★ C5 [未复核-需 GLM/DeepSeek 把关]: worker 级网络日志默认留存。
                    #   复用 _worker_errors 的 contextvars 隔离范式（per-worker 独立缓冲），
                    #   并行路径天然互不可见；串行路径回落 engine._network_logs（零契约变更）。
                    _worker_network = []
                    set_worker_network(_worker_network)
                    _worker_on_request, _worker_on_response = (
                        make_worker_network_handlers(_worker_network)
                    )
                    _worker_page.on("request", _worker_on_request)
                    _worker_page.on("response", _worker_on_response)
                    # per-worker page 隔离（contextvar）：不再写共享单例 engine._page，
                    # 并发 worker 读 engine._page 时各自拿到自己的 page。
                    set_worker_page(_worker_page)
                    # ★ 通用修复(2026-07-19): worker page 初始为 about:blank，
                    #   必须先导航到目标系统根URL，否则后续相对路径导航会失败。
                    _target_url = (getattr(config, "target_url", "") or "").strip()
                    if _target_url and _worker_page.url in ("about:blank", "", None):
                        with guard("_run_one_worker: await _worker_page.goto(_target_url,", log=_log, level=logging.WARNING):
                            await _worker_page.goto(_target_url, timeout=15000)
                            await _worker_page.wait_for_load_state(
                                "domcontentloaded", timeout=10000
                            )
                            _log.info(
                                "[BrowserCore] worker page 已导航到 %s", _target_url
                            )
                            # ★ 通用修复(2026-07-21 v10): 导航后校验是否被重定向到 /login
                            #   若被重定向，说明 storage_state + init_script 都未生效，
                            #   记录详细诊断信息帮助定位
                            _post_nav_url = _worker_page.url or ""
                            if (
                                "/login" in _post_nav_url.lower()
                                and "/login" not in _target_url.lower()
                            ):
                                _log.warning(
                                    "[BrowserCore] worker page 被重定向到登录页: %s → %s "
                                    "(storage_state 可能未生效，SessionAware 将尝试自动登录)",
                                    _target_url,
                                    _post_nav_url,
                                )
                    try:
                        from src.core.pipeline_config import pipeline_config as _pc

                        _CASE_TIMEOUT_SEC = getattr(_pc, "step4_case_timeout_sec", 180)
                        try:
                            # ★ 2026-10-27: 有界等待登记（同 serial）——到期即取消并落 TIMEOUT
                            with bounded_wait_inflight(_CASE_TIMEOUT_SEC):
                                return await asyncio.wait_for(
                                    self._execute_one_intent(
                                        intent,
                                        step_num,
                                        total,
                                        ai,
                                        api_tracker,
                                        data_factory,
                                        progress_callback,
                                    ),
                                    timeout=_CASE_TIMEOUT_SEC,
                                )
                        except asyncio.TimeoutError:
                            _log.warning(
                                "[parallel] 单用例超时 %ds: intent=%s",
                                _CASE_TIMEOUT_SEC,
                                intent.get("id", intent.get("intent_id", "?")),
                            )
                            with guard("_run_one_worker: from src.users.audit import record_e", log=_log):
                                from src.users.audit import record_execution
                                from src.core.versioning import resolve_run_id

                                _proj = config.project_name or ""
                                _uid = user_id or "system"
                                _run_id = resolve_run_id(config.project_name or "")
                                _cid = str(intent.get("case_id", "") or "").strip()
                                if _cid:
                                    # ★ P0-fix(2026-07-24): worker 超时分支补 evidence（§13.4 红线）
                                    from src.pipeline.steps.step4.browser_core.single_mixins._evidence_extractor import (
                                        build_timeout_evidence,
                                    )

                                    _exp, _act, _det = build_timeout_evidence(
                                        _CASE_TIMEOUT_SEC, "单用例"
                                    )
                                    record_execution(
                                        user_id=_uid,
                                        project=_proj,
                                        case_id=_cid,
                                        status="FAIL",
                                        case_asset_file="",
                                        case_row=None,
                                        failure_reason="单用例超时(%ds)"
                                        % _CASE_TIMEOUT_SEC,
                                        failure_category="HARNESS_ERROR",
                                        run_id=_run_id,
                                        check_type="browser_exec",
                                        expected=_exp,
                                        actual=_act,
                                        details=_det,
                                    )
                            return {
                                "passed": False,
                                "failed": True,
                                "error": "单用例超时(%ds)" % _CASE_TIMEOUT_SEC,
                                "failure_category": "TIMEOUT",
                            }
                    finally:
                        # 解除绑定 → 之后读 engine._page 回落引擎主 page。
                        set_worker_page(None)
                finally:
                    if _worker_page:
                        with guard("_run_one_worker: await _worker_page.close()", log=_log):
                            await _worker_page.close()
                    if _worker_context:
                        with guard("_run_one_worker: await _worker_context.close()", log=_log):
                            await _worker_context.close()

            # ★ 滑动窗口并发（不是批同步栅栏）：
            #   批栅栏的吞吐由「批内最慢」决定（木桶效应），并发度收益被抵消
            #   —— 实测并行=2 与串行吞吐持平。改为信号量 + as_completed：
            #   任一 worker 完成即刻补位，窗口始终填满。
            #   page 隔离由 set_worker_page(contextvar) 保证（见 _worker_page.py）。
            _sem = asyncio.Semaphore(max_parallel)

            async def _guarded(_idx, intent):
                async with _sem:
                    return await _run_one_worker(intent, _idx + 1)

            _tasks = [
                asyncio.create_task(_guarded(_i, _it))
                for _i, _it in enumerate(candidates)
            ]
            _stopped = False
            try:
                for _fut in asyncio.as_completed(_tasks):
                    try:
                        result = await _fut
                    except Exception as _re:
                        _log.error("[ERROR] 并行执行异常: %s", str(_re)[:200])
                        total_failed += 1
                        all_results.append(
                            {"passed": False, "status": "ERROR", "error": str(_re)}
                        )
                    else:
                        if result is None:
                            pass  # 停止信号
                        else:
                            all_results.append(result)
                            _bucket = count_bucket(result)
                            if _bucket == "pass":
                                total_passed += 1
                            elif _bucket == "fail":
                                total_failed += 1
                            else:
                                total_skipped += 1
                    # §10.1 失败即停：判据与串行同一份（配置驱动），
                    # 且只统计终止性失败 —— SKIP/BLOCKED 不是失败，不中断整批。
                    if total_failed > 0 and self._should_stop_on_failure():
                        _log.warning(
                            "[parallel] §10.1 失败即停: 已失败 %d 个用例，取消剩余任务",
                            total_failed,
                        )
                        _stopped = True
                        break
            finally:
                if _stopped:
                    for _t in _tasks:
                        if not _t.done():
                            _t.cancel()
                    await asyncio.gather(*_tasks, return_exceptions=True)

        except Exception as e:
            _log.error("[ERROR] 并行执行异常: %s", str(e)[:200])
            self._last_failure_detail = str(e)
        finally:
            await engine.stop()
            _log.info("[BrowserCore] 浏览器已关闭")
            # ★ S45-009: 强制停止后释放测试账号
            self._release_test_accounts()

        # ★ H4-3: 记录并行执行指标
        with guard("_execute_intents_parallel: from src.backend.state import _threa", log=_log):
            from src.backend.state import _thread_run_id, _current_project
            from src.extensions.metrics_collector import metrics_collector

            _rid = getattr(_thread_run_id, "value", "") or ""
            _proj = _current_project() or config.project_name
            _elapsed = time.time() - _parallel_start_ts
            if _rid:
                metrics_collector.record_parallel_exec(
                    _proj,
                    _rid,
                    max_parallel,
                    total,
                    total_passed,
                    total_failed,
                    _elapsed,
                )

        # ★ ClickFallback — 五级点击回退命中率统计（并行路径）
        with guard("_execute_intents_parallel: from src.backend.state import _threa", log=_log):
            from src.backend.state import _thread_run_id
            # ★ F7 根因修复（2026-09-27）：删除函数级 `from ... import config`。
            #   根因（ruff F823 实测命中）：模块级已 `from src.core.config_loader import
            #   config`（L15），此处再在函数体内 import 同一名字 → Python 把 `config`
            #   判定为该函数的**局部变量**，导致函数前段（登录失败 BLOCKED 落库，
            #   L108/L112 读 `config.project_name`）在赋值前读 → UnboundLocalError。
            #   表现：并行路径一旦登录失败，不是"标记 BLOCKED"而是整个 run 崩溃。
            #   通用性：同名符号只保留一处绑定（模块级），消除作用域歧义。
            from src.extensions.metrics_collector import metrics_collector
            from src.pipeline.steps.step4.playwright_engine.stats import _click_stats

            _rid = getattr(_thread_run_id, "value", "") or ""
            _proj = _current_project() or config.project_name
            if _rid:
                stats = _click_stats.snapshot()
                metrics_collector.record_click_fallback_stats(
                    project=_proj,
                    run_id=_rid,
                    stats=stats,
                )

        # ★ v4.0 修复: 并行路径也记录插件指标（原仅 serial 路径记录）
        self._record_plugin_metrics(total)

        return {
            "passed": total_passed,
            "failed": total_failed,
            "skipped": total_skipped,
            "total": total,
            "results": all_results,
        }
