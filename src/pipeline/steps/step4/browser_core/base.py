# -*- coding: utf-8 -*-
from __future__ import annotations
from src.core.guarded import guard

"""
BrowserCore 基础 mixin

职责：
- __init__ / _init_plugins
- run_test_cases（公共入口）
- _ensure_page_valid / _check_preconditions
- _parse_import_hint / _build_meta_text / _current_capability_context
- 门面委托方法（静态/实例）
- _extract_keywords / _save_fail_screenshot / _hard_verify_step
- _execute_structured_actions / _execute_structured_assertions
- _verify_recipe_intent_result
- _record_intent_evidence / _record_intent_dominance_metrics / _current_intent_evidence_ok
"""

import asyncio
import functools
import logging
import threading
from typing import Any

from src.core.config_loader import config
from src.core.page_ready import READY_CAP_MS
from src.core.pipeline_config import _read_config_value as _cfg
from src.pipeline.steps.step4.plugins import (
    PluginManager,
    GroundTruthPlugin,
    VisualBaselinePlugin,
    SelfHealingPlugin,
    SelfHealingVisionPlugin,
    LongTermMemoryPlugin,
    PerformanceMonitorPlugin,
    HarnessGovernancePlugin,
    ScorecardOraclePlugin,
    TokenBudgetPlugin,
    FlakyGuardPlugin,
    MultiAgentPlugin,
    # ★ S45-007: 业务能力检测插件
    BusinessCapabilitiesPlugin,
    # ★ CDP 客户端浏览器指标收集插件
    CdpMetricsPlugin,
    # ★ 性能门禁采集插件（perf_gate 链路数据源）
    PerfCollectorPlugin,
)

_log = logging.getLogger(__name__)

# ★ D62：并行 worker 目前共享单例 `engine._page`（并发互相覆盖 → 间歇性假 FAIL），
#   且批次为同步栅栏（木桶效应抵消并发收益，实测并行=2 吞吐 ≤ 串行）。
#   在 per-worker page 隔离重构完成并验证前，**不启用并行**——宁可慢，不可造假。
#   接线已保留（_execute_intents_parallel 可达），改这一个常量即可启用。
_PARALLEL_PAGE_ISOLATED = False


def _get_import_keywords():
    from src.pipeline.steps.step4.browser_ai import _load_import_keywords_from_library

    return _load_import_keywords_from_library()


try:
    from gui_app import log_step_ok, log_step_fail, update_monitor_dom
except Exception as _e:
    _log.debug("[base] _get_import_keywords 异常: %s", _e, exc_info=True)
    log_step_ok = lambda *a: print(
        "    [Step %d/%d] OK (%.1fs) — %s" % (a[0], a[1], a[2], a[3][:50])
    )
    log_step_fail = lambda *a: print(
        "    [Step %d/%d] FAIL (%.1fs) — %s" % (a[0], a[1], a[2], a[3][:50])
    )
    update_monitor_dom = lambda c, o: None


class _BrowserCoreBaseMixin:
    """BrowserCore 基础能力。"""

    @property
    def MAX_AI_ROUNDS(self):
        return int(_cfg("browser_max_ai_rounds", 8))

    @property
    def MAX_DOM_ELEMENTS(self):
        return int(_cfg("browser_max_dom_elements", 120))

    VIEWPORT_FIRST_ROUNDS = 3

    def __init__(self):
        self._engine = None
        self._force_stop_check = None
        self._lock = threading.Lock()
        self._last_failure_detail = ""
        self._stop_printed = False
        self._plugin_manager = PluginManager()
        self._allocated_test_accounts = []  # ★ S45-009: 本次执行占用的测试账号
        self._init_plugins()

    def _init_plugins(self):
        """注册所有插件。可通过配置控制启用/禁用。

        ★ 每个插件注册独立包裹 try-except，单个插件初始化失败不影响其他插件。
        """
        # ConfigLoader 将 YAML 平铺为 UPPERCASE，嵌套结构需从 _yaml_raw 读取
        plugin_configs = (
            config._yaml_raw.get("step4_plugins", {})
            if hasattr(config, "_yaml_raw")
            else {}
        )
        # ★ 插件注册清单：(插件类, 配置键)
        _plugin_registry = [
            (GroundTruthPlugin, "ground_truth"),
            (VisualBaselinePlugin, "visual_baseline"),
            (SelfHealingPlugin, "self_healing"),
            (LongTermMemoryPlugin, "long_term_memory"),
            (PerformanceMonitorPlugin, "performance_monitor"),
            (SelfHealingVisionPlugin, "self_healing_vision"),
            # ★ P0修复：注册5个治理插件（代码写好了但从未注册）
            (HarnessGovernancePlugin, "harness_governance"),
            (ScorecardOraclePlugin, "scorecard_oracle"),
            (TokenBudgetPlugin, "token_budget"),
            (FlakyGuardPlugin, "flaky_guard"),
            (MultiAgentPlugin, "multi_agent"),
            # ★ S45-007: 业务能力检测插件（12 个能力检测器）
            (BusinessCapabilitiesPlugin, "business_capabilities"),
            # ★ CDP 客户端浏览器指标收集插件
            (CdpMetricsPlugin, "cdp_metrics"),
            # ★ 性能门禁采集插件（perf_gate 链路数据源）
            (PerfCollectorPlugin, "perf_collector"),
        ]
        for plugin_cls, cfg_key in _plugin_registry:
            with guard("_init_plugins: pcfg = plugin_configs.get(cfg_key, {", log=_log, level=logging.ERROR):
                pcfg = plugin_configs.get(cfg_key, {}) or {}
                # ★ 深度合并：插件 enabled 开关由流水线配置（step4_plugins_<key>_enabled）
                #   作为单一事实源覆盖；未设置时保持 config.yaml 默认。阈值字段仍留
                #   config.yaml 内部默认，不重复暴露。键经 pipeline_config 大小写归一化生效。
                _povr = _cfg("step4_plugins_{}_enabled".format(cfg_key), None)
                if _povr is not None:
                    if isinstance(pcfg, dict):
                        pcfg = dict(pcfg)
                    else:
                        pcfg = {}
                    pcfg["enabled"] = (
                        bool(_povr) if isinstance(_povr, bool)
                        else str(_povr).strip().lower() in ("1", "true", "yes", "on")
                    )
                self._plugin_manager.register(
                    plugin_cls(pcfg)
                )
        _log.info(
            "[BrowserCore] 插件系统初始化完成: %s", self._plugin_manager.list_plugins()
        )

    def _block_all_candidates(self, candidates, reason: str, user_id=None) -> int:
        """把整批用例标记为 BLOCKED（**环境问题，非用例缺陷**），返回写入行数。

        why: 「登录失败」与「目标系统不可达」同属**批次级前置不成立**——继续逐条执行
             既无意义，又会把环境问题伪装成产品 FAIL。两处共用本函数，避免各写一份
             落库逻辑（同一语义两处实现＝补丁温床）。
        """
        from src.core.versioning import resolve_run_id
        from src.users.audit import record_execution

        _proj = getattr(config, "project_name", "") or ""
        _uid = user_id or "system"
        _run_id = resolve_run_id(_proj) or ""
        _n = 0
        for _intent in candidates or []:
            _cid = str(_intent.get("case_id", "") or "").strip()
            if not _cid:
                continue
            with guard("_block_all_candidates: record_execution", log=_log, level=logging.WARNING):
                record_execution(
                    user_id=_uid,
                    project=_proj,
                    case_id=_cid,
                    status="BLOCKED",
                    case_asset_file=str(_intent.get("case_asset_file", "") or ""),
                    case_row=_intent.get("case_row", 0),
                    failure_reason=str(reason or "")[:500],
                    failure_category="ENVIRONMENT_ERROR",
                    run_id=_run_id,
                    check_type="browser_exec",
                )
                _n += 1
        return _n

    def _release_test_accounts(self):
        """★ S45-009: 释放本次执行占用的测试账号（in_use 重置为 0）。
        在 serial.py / parallel.py 的 finally 块中调用，确保强制停止时也不泄漏。"""
        if not self._allocated_test_accounts:
            return
        _log.warning(
            "[BrowserCore] 释放 %d 个测试账号 (in_use→0)",
            len(self._allocated_test_accounts),
        )
        from src.extensions.db.db_agent import db_agent

        project = getattr(config, "project_name", "") or ""
        for account in self._allocated_test_accounts:
            with guard("_release_test_accounts: username = ( account.get('username',", log=_log, level=logging.WARNING):
                username = (
                    account.get("username", "")
                    if isinstance(account, dict)
                    else str(account)
                )
                db_agent.execute(
                    "UPDATE test_accounts SET in_use = 0, released_at = CURRENT_TIMESTAMP "
                    "WHERE project = %s AND username = %s AND in_use = 1",
                    (project, username),
                )
                _log.warning("[BrowserCore]   已释放账号: %s", username)
        self._allocated_test_accounts.clear()

    # ★ S45-009：test_accounts 表位于**靶子库**
    #   （db_agent 连接 TARGET_DB_NAME，非引擎库）——记录靶子系统测试账号的占用，
    #   防并行/多运行同账号会话互踩。方言中立 DDL（MySQL/SQLite 均合法，复合主键
    #   免自增差异），每进程首次使用时自举建表。
    _TEST_ACCOUNTS_DDL = (
        "CREATE TABLE IF NOT EXISTS test_accounts ("
        " project VARCHAR(128) NOT NULL,"
        " username VARCHAR(64) NOT NULL,"
        " in_use INTEGER NOT NULL DEFAULT 0,"
        " allocated_at TIMESTAMP NULL,"
        " released_at TIMESTAMP NULL,"
        " created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,"
        " PRIMARY KEY (project, username)"
        ")"
    )
    _test_accounts_ensured = False

    def _ensure_test_accounts_table(self) -> None:
        from src.extensions.db.db_agent import db_agent

        if _BrowserCoreBaseMixin._test_accounts_ensured:
            return
        result = db_agent.execute(self._TEST_ACCOUNTS_DDL)
        if result.get("success"):
            _BrowserCoreBaseMixin._test_accounts_ensured = True
        else:
            _log.debug(
                "[BrowserCore] test_accounts 建表跳过(靶子库未启用?): %s",
                str(result.get("error"))[:80],
            )

    def _allocate_test_account(self, username: str) -> bool:
        """★ S45-009: 分配测试账号（in_use 0→1），防止多运行同账号互踩。

        ★  死区审计实证的三重断裂修复：
          1. 表从未建过（引擎库/靶子库都没有）→ 现首次使用自举建表（靶子库）；
          2. 原 UPDATE 后把 execute() 返回的 **dict** 当 int 比较 → 必 TypeError
             → 永远走异常分支（互斥 no-op 的直接原因）→ 现按真实契约
             {"success","rows_affected"} 判定；
          3. 原用 NOW()（MySQL 方言）对 SQLite 靶子必炸 → 改 CURRENT_TIMESTAMP。
        语义：未注册 → INSERT 注册并占用；空闲 → UPDATE 占用；已占用 → False。
        任何失败不阻塞执行（与 _release_test_accounts 容错语义对称）。
        """
        if not username:
            return False
        try:
            from src.extensions.db.db_agent import db_agent

            self._ensure_test_accounts_table()
            project = getattr(config, "project_name", "") or ""
            upd = db_agent.execute(
                "UPDATE test_accounts SET in_use = 1, allocated_at = CURRENT_TIMESTAMP"
                " WHERE project = %s AND username = %s AND in_use = 0",
                (project, username),
            )
            if upd.get("success") and (upd.get("rows_affected") or 0) > 0:
                self._allocated_test_accounts.append({"username": username})
                _log.info("[BrowserCore]   已分配账号: %s", username)
                return True
            if not upd.get("success"):
                _log.warning(
                    "[BrowserCore]   分配账号 %s 失败(不阻塞): %s",
                    username,
                    str(upd.get("error"))[:80],
                )
                return False
            # UPDATE 0 行：区分「已占用」与「未注册」
            sel = db_agent.query(
                "SELECT in_use FROM test_accounts WHERE project = %s AND username = %s",
                (project, username),
            )
            data = sel.get("data") or [] if sel.get("success") else []
            if data:
                _log.warning(
                    "[BrowserCore]   账号 %s 互斥命中: 已被其它运行占用", username
                )
                return False
            ins = db_agent.execute(
                "INSERT INTO test_accounts (project, username, in_use, allocated_at)"
                " VALUES (%s, %s, 1, CURRENT_TIMESTAMP)",
                (project, username),
            )
            if ins.get("success"):
                self._allocated_test_accounts.append({"username": username})
                _log.info("[BrowserCore]   已注册并分配账号: %s", username)
                return True
            _log.warning(
                "[BrowserCore]   注册账号 %s 失败(不阻塞): %s",
                username,
                str(ins.get("error"))[:80],
            )
            return False
        except Exception as _e:
            _log.warning(
                "[BrowserCore]   分配账号 %s 失败(不阻塞): %s", username, str(_e)[:80]
            )
            return False

    def _allocate_current_identity(self) -> None:
        """★ S45-009 接线补全（2026-09-22）：运行级登录前占用当前身份账号。
        原特性只有 release 被 finally 调用、allocate 无任何调用方（死区审计实证），
        互斥自诞生即空转。现接线：run 级登录（serial/parallel 的 ai.login()）前
        以 config.target_username 占账号；失败不阻塞（与 release 的容错语义对称）。"""
        with guard("_allocate_current_identity: username = str(getattr(config, 'targ", log=_log):
            username = str(getattr(config, "target_username", "") or "")
            if username:
                self._allocate_test_account(username)

    def _should_stop_on_failure(self) -> bool:
        """§10.1「失败即停」的唯一判据：读管线配置。

        串行计数、并行批次、执行形态决策三处共用这一份；并行曾硬编码
        `total_failed > 0`（第二份实现），导致全自动模式下 653 例只跑 3 例即报 completed。
        """
        from src.core.pipeline_config import pipeline_config as _pc

        return bool(getattr(_pc, "step4_stop_on_failure", True))

    @staticmethod
    def _with_step4_runtime(fn):
        """标记「Step4 运行期」：其内 AI 调用要过 runtime_ai_gate。

        why: 运行期禁用 AI 的范围必须由执行入口界定；用装饰器而非改函数体，
             可保证所有 return 分支（含异常）都能解除标记。
        """
        @functools.wraps(fn)
        def _wrapped(self, *args, **kwargs):
            from src.ai.runtime_ai_gate import (
                enter_step4_runtime,
                exit_step4_runtime,
            )

            enter_step4_runtime()
            try:
                return fn(self, *args, **kwargs)
            finally:
                exit_step4_runtime()

        return _wrapped

    @_with_step4_runtime
    def run_test_cases(
        self,
        candidates,
        quick=False,
        browser_info=None,
        headless=True,
        timeout_ms=None,
        user_id=None,
        progress_callback=None,
        max_parallel=None,
    ):
        timeout_ms = timeout_ms or int(_cfg("default_step4_timeout_ms", 30000))
        self._last_failure_detail = ""
        self._stop_printed = False

        # ── 批次级环境健康门禁（2026-10-27，通用）──
        #   现象: 目标系统不可达时，首个用例的页面查询/导航会长时间无任何日志，
        #         run 级看门狗据「无进展」判挂起 → **整轮 run 被中止**，且无任何
        #         可操作的归因（实测 2026-10-27 09:29:17→09:32:18，停滞 181s）。
        #   根因: 执行层把「环境不可用」当成「逐条用例逐个等待」，既产生假失败，
        #         又让停滞信号只能落在 run 级（终止粒度过粗）。
        #   判据: 用配置层已有的可达性探测（唯一事实源，不另造探测）。
        #   探测预算**不得**复用执行器 timeout_ms（实测该值为 600s）：健康探测必须廉价，
        #   否则黑洞端口（TCP 可连不应答）会让门禁自身卡 600s → 仍被 180s 的 run 级
        #   看门狗判挂起。预算由探测的单一事实源（`_url_reachable` 默认 2s）决定，
        #   本处只传 `fresh=True` 要求「此刻的结论」，不再另设第二个超时旋钮。
        #   结果: 不可达 → 全批 BLOCKED（ENVIRONMENT_ERROR），不启动浏览器、不逐条执行。
        if candidates:
            with guard("run_test_cases: config.is_target_reachable", log=_log, level=logging.WARNING):
                _reachable = bool(config.is_target_reachable(fresh=True))
                if not _reachable:
                    _target = (getattr(config, "target_url", "") or "").strip() or "<未配置>"
                    _reason = (
                        "目标系统不可达: %s（HTTP 健康探测无响应/5xx）" % _target
                    )
                    _log.error(
                        "[BrowserCore] %s → 全批 %d 个用例标记 BLOCKED"
                        "（环境问题，非产品缺陷；不启动浏览器、不逐条等待）",
                        _reason,
                        len(candidates),
                    )
                    _blocked = self._block_all_candidates(
                        candidates, _reason, user_id
                    )
                    return {
                        "passed": 0,
                        "failed": 0,
                        "blocked": _blocked,
                        "total": len(candidates),
                        "error": _reason,
                        "failure_category": "ENVIRONMENT_ERROR",
                        "results": [],
                    }
        # ★ 执行形态的唯一决策点（serial 单条串行 / parallel 批内并发）。
        #   判据一：失败即停必须串行（并行批内无法中断，会违反 AGENTS.md §10.1）。
        #   判据二（D62，实测）：并行 worker 共用**单例 `engine._page`**，
        #          并发时互相覆盖 → 间歇性操作到错误页面（引擎自造假 FAIL）；
        #          且批次是同步栅栏（一批全部完成才起下一批），木桶效应抵消并发收益
        #          —— 实测并行=2 吞吐 ≤ 串行。故在 per-worker page 重构完成前强制串行，
        #          避免用一条会造假的通路去测"通过率"。
        # 与 serial.py 同一份配置对象（`src.core.pipeline_config`）—— 不得另引一处，
        # 否则同一事实两处表达、默认值不一致会把并行悄悄降级回串行。
        from src.core.pipeline_config import pipeline_config as _pc

        _stop_on_fail = self._should_stop_on_failure()
        _mp = int(max_parallel or 0)
        if not _mp:
            _mp = int(getattr(_pc, "browser_max_parallel", 2) or 1)
        if _mp > 1 and _stop_on_fail:
            _log.warning(
                "[BrowserCore] §10.1 失败即停开启 → 强制串行（忽略 max_parallel=%d）", _mp
            )
            _mp = 1
        _use_parallel = _mp > 1 and _PARALLEL_PAGE_ISOLATED

        def _new_run_coro():
            """每次调用返回**新的**协程对象（协程不可复用，两个分支都要新鲜构造）。"""
            if _use_parallel:
                return self._execute_intents_parallel(
                    candidates,
                    browser_info,
                    headless,
                    timeout_ms,
                    user_id,
                    progress_callback,
                    max_parallel=_mp,
                )
            return self._execute_all(
                candidates,
                browser_info,
                headless,
                timeout_ms,
                user_id,
                progress_callback,
                max_parallel=_mp,
            )

        # ★ 捕获当前线程的 _thread_run_id（threading.local() 不跨线程传播）
        # 后续 P0 治理指标 + _record_metric 调用依赖此值
        try:
            from src.backend.state import _thread_run_id

            _captured_run_id = getattr(_thread_run_id, "value", "") or ""
        except Exception:
            _captured_run_id = ""
        # ★ 检测是否已有运行中的事件循环（如 Jupyter / async Web 框架上下文）。
        # asyncio.run() 在已有循环中会抛 RuntimeError。
        # 此时必须在独立线程中创建新事件循环执行，避免死锁。
        try:
            _running_loop = asyncio.get_running_loop()
        except RuntimeError:
            _running_loop = None
        if _running_loop is not None:
            _log.warning("[BrowserCore] 检测到运行中的事件循环，在新线程中执行")
            import concurrent.futures

            def _run_with_run_id(_coro, _rid):
                """在新线程中恢复 _thread_run_id，确保 P0 指标/metrics 能正常写入。"""
                if _rid:
                    try:
                        from src.backend.state import _thread_run_id as _tid

                        _tid.value = _rid
                    except Exception as _r31:
                        _log.debug(
                            "[base] 异常降级(pass): %s", str(_r31)[:60], exc_info=True
                        )
                        pass
                try:
                    return asyncio.run(_coro)
                finally:
                    if _rid:
                        try:
                            from src.backend.state import _thread_run_id as _tid2

                            _tid2.value = None
                        except Exception as _r32:
                            _log.debug(
                                "[base] 异常降级(pass): %s",
                                str(_r32)[:60],
                                exc_info=True,
                            )
                            pass

            _executor = concurrent.futures.ThreadPoolExecutor(max_workers=1)
            try:
                _future = _executor.submit(
                    _run_with_run_id,
                    _new_run_coro(),
                    _captured_run_id,
                )
                return _future.result(timeout=timeout_ms / 1000 + 120)
            except concurrent.futures.TimeoutError:
                _log.error("[BrowserCore] 新线程事件循环执行超时")
                return {
                    "passed": 0,
                    "failed": 0,
                    "total": len(candidates),
                    "error": "Event loop timeout",
                }
            except Exception as e:
                _log.error("[BrowserCore] 新线程事件循环执行异常: %s", str(e)[:120])
                return {
                    "passed": 0,
                    "failed": 0,
                    "total": len(candidates),
                    "error": str(e),
                }
            finally:
                _executor.shutdown(wait=False)
        # ★ QA(2026-08-12) 根治：整体执行加硬超时，防任一 await 在 Windows IOCP
        #   无超时挂死导致 step4 永久挂起。超时 fail-closed 返回（与上面线程池路径
        #   的 timeout=timeout_ms/1000+120 口径一致）；若取消仍失败，执行槽
        #   watchdog(180s) 兜底释放槽位。
        # ★  根因修复: 固定 +120s 只够 1 批(≤4条)；用例多时(如 33 条
        #   / 并行 4 = 9 批)必然超时截断 → 大批用例从未执行。
        #   修复: 整体超时 = 每例超时 × 批次数 + 余量（按候选数与并行度动态计算）。
        import math as _math
        _mp = _mp or 1
        _est_batches = max(1, _math.ceil(len(candidates) / max(1, _mp)))
        # ★ 2026-08-28 二次修正: 单例真实耗时 > timeout_ms（浏览器冷启动、登录、
        #   导航重试、LLM 语义兜底均发生在 timeout_ms 之外的时间轴上），
        #   线性估算系统性低估 → 加 1.5 倍安全系数。
        #   实测: 33条/并行4=9批 × 40s + 120 = 480s 仍只跑 17/33 条。
        #   通用性: 系数与用例数/并行度/业务无关，跨系统适用。
        _outer_timeout = (timeout_ms / 1000) * _est_batches * 1.5 + 120
        try:
            return asyncio.run(
                asyncio.wait_for(
                    _new_run_coro(),
                    timeout=_outer_timeout,
                )
            )
        except asyncio.TimeoutError:
            _log.error(
                "[BrowserCore] _execute_all 整体执行超时(%.0fs)，强制取消返回",
                _outer_timeout,
            )
            return {
                "passed": 0,
                "failed": 0,
                "total": len(candidates),
                "error": "Event loop timeout",
            }
        except (RuntimeError, Exception) as e:
            import traceback

            _log.error("[BrowserCore] _execute_all 异常: %s", str(e)[:200])
            _log.error("[BrowserCore] 堆栈: %s", traceback.format_exc()[-500:])
            return {"passed": 0, "failed": 0, "total": len(candidates), "error": str(e)}

    async def _ensure_page_valid(self, engine, ai) -> bool:
        try:
            page = engine._page
            if page is None or (hasattr(page, "is_closed") and page.is_closed()):
                _log.warning("⚠ 页面实例失效，冷启动新浏览器...")
                await engine.stop()
                await engine.start()
                if not await ai.login():
                    _log.error("[ERROR] 重建后登录失败，中止执行")
                    return False
            return True
        except Exception as e:
            _log.error("[ERROR] 页面有效性检查异常: %s", str(e)[:100])
            return False

    async def _check_preconditions(self, case_id, case_meta, steps=None):
        try:
            engine = self._engine
            if engine is None or engine._page is None:
                return False
            page = engine._page
            _nav_ok = True
            try:
                url = page.url or ""
                if not url or url == "about:blank":
                    _log.warning("[PRE-CHECK] 页面不可达 (url=%s)，尝试导航...", url)
                    _target = config.target_url
                    if _target:
                        # ★ 2026-10-27: goto 自带导航超时，但原实现**丢弃返回值**且
                        #   吞掉异常后仍 return True —— 把「导航失败」当成「前置条件成立」，
                        #   后续操作落在坏页面上长时间无日志（实测 181s 静默后被 run 级
                        #   看门狗判挂起，整轮中止）。现按真实返回值判定。
                        _nav_ok = bool(await engine.goto(_target))
                        if not _nav_ok:
                            _log.error(
                                "[PRE-CHECK] 导航失败: %s（目标不可达/页面不可用）",
                                _target,
                            )
                        else:
                            await asyncio.sleep(2)
            except Exception as _e:
                _nav_ok = False
                _log.error("[PRE-CHECK] 导航异常: %s", str(_e)[:160])
            if not _nav_ok:
                return False
            is_login_intent = any(
                kw in str(steps or []).lower()
                for kw in ("登录系统", "登录页面", "输入用户名", "输入密码")
            )
            if not is_login_intent:
                # ★ 2026-10-27: 页面探测必须有界。执行上下文不可用时
                #   locator.count() 会**永不返回**（实测导航超时后 60s+ 无响应），
                #   原实现无超时 → 整条用例一直空转。
                # ★ 上界口径统一（2026-10-27）：单次页面交互预算取 `page_ready.READY_CAP_MS`
                #   （同一「单次页面交互」预算的既有唯一来源，3s）；不取执行器全局
                #   `_timeout_ms` —— 它可达 600s（> 用例级 300s 硬超时），
                #   会让「探测」先被外层用例超时杀掉、丢掉已获得的证据，等于没上界。
                _probe_to = min(
                    READY_CAP_MS / 1000.0,
                    max(1.0, float(getattr(engine, "_timeout_ms", 30000) or 30000) / 1000.0),
                )
                try:
                    _cnt = await asyncio.wait_for(
                        page.locator('input[type="password"]').count(),
                        timeout=_probe_to,
                    )
                    if _cnt > 0:
                        _log.warning(
                            "[PRE-CHECK] 检测到登录表单但测试用例非登录类，可能会话过期"
                        )
                except asyncio.TimeoutError:
                    _log.error(
                        "[PRE-CHECK] 页面探测超时(%.0fs)：执行上下文不可用 → 前置条件不成立",
                        _probe_to,
                    )
                    return False
                except Exception as _e:
                    _log.debug(
                        "[base] _check_preconditions 异常: %s", _e, exc_info=True
                    )
            return True
        except Exception as e:
            _log.warning("[PRE-CHECK] 异常: %s", str(e)[:60])
            return True

    @staticmethod
    def _parse_import_hint(step_desc):
        try:
            import re

            # ★ 通用提取：从步骤描述中识别数据实体关键词（中文2-4字名词短语 + 英文单词）
            m = re.search(r"(?:表|table|TABLE)[：:\s]*(\w+)", step_desc)
            table_name = m.group(1) if m else ""
            # 提取可能的实体名（连续2-4个中文字或3-20个英文字母）
            entities = re.findall(r"[一-鿿]{2,4}|[a-zA-Z_]{3,20}", step_desc)
            data_hint = "/".join(entities[:3]) if entities else ""
            return data_hint, table_name
        except Exception as _e:
            _log.debug("[base] _parse_import_hint 异常: %s", _e, exc_info=True)
            return "", ""

    @staticmethod
    def _build_meta_text(meta: dict[str, str]) -> str:
        keys = [
            ("case_id", "意图ID"),
            ("title", "意图标题"),
            ("case_type", "意图类型"),
            ("risk_level", "风险等级"),
            ("catalog_name", "目录名称"),
        ]
        parts = ["%s: %s" % (label, meta[k]) for k, label in keys if meta.get(k)]
        return ("## 测试用例上下文\n" + "\n".join(parts) + "\n") if parts else ""

    async def _current_capability_context(self) -> dict[str, Any]:
        page = self._engine._page if self._engine else None
        if page is None:
            return {}
        try:
            page_url = page.url or ""
        except Exception as _e:
            _log.debug("[base] _current_capability_context 异常: %s", _e, exc_info=True)
            page_url = ""
        try:
            page_title = await page.title() or ""
        except Exception as _e:
            _log.debug("[base] _current_capability_context 异常: %s", _e, exc_info=True)
            page_title = ""
        return {"page_url": page_url, "page_title": page_title}

    # ===== 门面委托方法 =====

    @staticmethod
    def _extract_intent_schema(intent):
        from src.pipeline.steps.step4.browser_intent_parser import extract_intent_schema

        return extract_intent_schema(intent)

    @classmethod
    def _has_write_keyword(cls, intent):
        from src.pipeline.steps.step4.browser_intent_parser import has_write_keyword

        return has_write_keyword(intent)

    def _partition_intents_by_io(self, candidates):
        from src.pipeline.steps.step4.browser_intent_parser import (
            partition_intents_by_io,
        )

        return partition_intents_by_io(candidates)

    @staticmethod
    def _normalize_steps(steps: list) -> list:
        from src.pipeline.steps.step4.browser_intent_parser import normalize_steps

        return normalize_steps(steps)

    @staticmethod
    def _steps_from_case(intent_schema: dict[str, Any], intent: dict[str, Any]) -> list:
        from src.pipeline.steps.step4.browser_intent_parser import steps_from_case

        return steps_from_case(intent_schema, intent, config)

    @staticmethod
    def _collapse_steps_to_case(steps: list, case_meta: dict[str, str]) -> list:
        from src.pipeline.steps.step4.browser_intent_parser import (
            collapse_steps_to_case,
        )

        return collapse_steps_to_case(steps, case_meta, config)

    @staticmethod
    def _format_steps(steps: list[dict]) -> str:
        from src.pipeline.steps.step4.browser_intent_parser import format_steps

        return format_steps(steps)

    @staticmethod
    def _intent_needs_login(steps: list) -> bool:
        from src.pipeline.steps.step4.browser_intent_parser import intent_needs_login

        return intent_needs_login(steps)

    # ★ 向后兼容别名（f3995cc4 拆分后旧名 → 新名）
    @staticmethod
    def _case_needs_login(steps: list) -> bool:
        """[兼容别名] → _intent_needs_login"""
        from src.pipeline.steps.step4.browser_intent_parser import intent_needs_login

        return intent_needs_login(steps)

    async def _save_fail_screenshot(self, case_id, step_num):
        from src.pipeline.steps.step4.browser_evidence import save_fail_screenshot

        return await save_fail_screenshot(self._engine, case_id, step_num, cfg=config)

    async def _save_case_screenshot(
        self, case_id, status, project=None, run_id=None, step_num=0
    ):
        from src.pipeline.steps.step4.browser_evidence import save_case_screenshot

        return await save_case_screenshot(
            self._engine,
            case_id,
            status,
            project=project,
            run_id=run_id,
            step_num=step_num,
        )

    async def _hard_verify_step(
        self, step_desc, step_expected, pre_dom_count=0, api_tracker=None
    ):
        from src.pipeline.steps.step4.browser_verification import hard_verify_step

        return await hard_verify_step(
            self._engine,
            step_desc,
            step_expected,
            pre_dom_count,
            api_tracker,
            extract_keywords_fn=self._extract_keywords,
        )

    @staticmethod
    def _extract_keywords(text):
        from src.pipeline.steps.step4.browser_verification import extract_keywords

        return extract_keywords(text)

    async def _execute_structured_actions(self, step, step_desc):
        from src.pipeline.steps.step4.browser_structured_executor import (
            execute_structured_actions,
        )

        return await execute_structured_actions(self._engine, step, step_desc)

    async def _execute_structured_assertions(self, step, step_desc):
        from src.pipeline.steps.step4.browser_structured_executor import (
            execute_structured_assertions,
        )

        return await execute_structured_assertions(
            self._engine, step, step_desc, hard_verify_fn=self._hard_verify_step
        )

    async def _verify_recipe_intent_result(
        self,
        step: dict[str, Any],
        step_desc: str,
        step_expected: str,
        intent_schema: dict[str, Any] | None,
        pre_dom_count: int,
        api_tracker,
        step_t0: float,
    ) -> tuple:
        from src.pipeline.steps.step4.browser_verification import (
            verify_recipe_intent_result,
        )

        return await verify_recipe_intent_result(
            self._engine,
            step,
            step_desc,
            step_expected,
            intent_schema,
            pre_dom_count,
            api_tracker,
            step_t0,
            extract_keywords_fn=self._extract_keywords,
        )

    async def _record_intent_evidence(
        self,
        case_id: str,
        case_meta: dict[str, Any],
        passed: bool,
        step_results: list[dict[str, Any]],
    ):
        from src.pipeline.steps.step4.browser_evidence import record_intent_evidence

        return await record_intent_evidence(
            self._engine,
            case_id,
            case_meta,
            passed,
            step_results,
            extract_keywords_fn=self._extract_keywords,
        )

    def _record_intent_dominance_metrics(
        self,
        case_id: str,
        case_meta: dict[str, Any],
        step_results: list[dict[str, Any]],
    ) -> None:
        from src.pipeline.steps.step4.browser_evidence import (
            record_intent_dominance_metrics,
        )

        return record_intent_dominance_metrics(
            case_id, case_meta, step_results, cfg=config
        )

    async def _current_intent_evidence_ok(self, intent_schema: dict[str, Any]) -> bool:
        from src.pipeline.steps.step4.browser_verification import (
            current_intent_evidence_ok,
        )

        return await current_intent_evidence_ok(
            self._engine, intent_schema, extract_keywords_fn=self._extract_keywords
        )
