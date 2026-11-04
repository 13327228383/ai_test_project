# -*- coding: utf-8 -*-
"""
BrowserCore 串行执行 mixin

职责：
- _execute_all — 串行执行主循环
"""

import asyncio
from src.core.guarded import guard
import logging
from datetime import datetime
from typing import Any

from src.core.config_loader import config
from src.core.pipeline_config import pipeline_config
from src.ai.ai_inflight import bounded_wait_inflight
from src.pipeline.steps.step4.browser_core.case_verdict import count_bucket
from src.pipeline.utils.pause_controller import pause_controller

_log = logging.getLogger(__name__)


# ── 通用页面状态重置 ──
async def _reset_page_state(engine) -> None:
    """关闭页面上的残余弹窗/模态框，防止测试用例间页面状态污染。

    通用策略（零业务硬编码）：
    1. 按 Escape 键关闭大多数弹窗
    2. 查找 .ant-modal-close / .el-dialog__close 等常见关闭按钮并点击
    3. 异常静默吞掉，不影响主流程
    """
    try:
        _page = getattr(engine, "_page", None)
        if not _page:
            return
        # 策略 1：按 Escape 关闭弹窗
        try:
            await _page.keyboard.press("Escape")
            await asyncio.sleep(0.2)
        except Exception as _r31:
            _log.debug("[serial] 异常降级(pass): %s", str(_r31)[:60], exc_info=True)
            pass
        # 策略 2：点击常见 UI 框架的关闭按钮
        for _close_sel in [
            ".ant-modal-close",
            ".ant-drawer-close",
            ".el-dialog__close",
            ".el-drawer__close",
            '[aria-label="Close"]',
            '[aria-label="关闭"]',
            ".modal-header .close",
            ".dialog-close",
        ]:
            try:
                _close_btn = _page.locator(_close_sel).first
                if await _close_btn.count() > 0 and await _close_btn.is_visible():
                    await _close_btn.click(timeout=2000)
                    await asyncio.sleep(0.2)
                    _log.debug("[serial] 关闭弹窗: %s", _close_sel)
            except Exception as _r32:
                _log.debug(
                    "[serial] 异常降级(continue): %s", str(_r32)[:60], exc_info=True
                )
                continue
    except Exception as _r33:
        _log.debug("[serial] 异常降级(pass): %s", str(_r33)[:60], exc_info=True)
        pass


async def _recover_network_fault(engine) -> None:
    """★ F-R1 (2026-08-06): 网络故障兜底恢复（用例间 / 执行器 finally 调用）。

    offline 模式 context.set_offline(True) 是浏览器上下文级全局断网：若用例注入后
    崩溃 / 未显式恢复，后续用例会全部断网。此处强制 set_offline(False) 兜底，
    与 _reset_page_state 同类（每次用例执行前清理全局状态）。

    安全保证：
    - NETWORK_FAULT_ENABLED=False 时 recover_network_fault 内部直接返回 → 零行为变更。
    - 任何异常只记日志，绝不阻断执行流（与 TDL teardown 同类兜底）。
    """
    with guard("_recover_network_fault: from src.pipeline.steps.step4.browse", log=_log, level=logging.WARNING):
        from src.pipeline.steps.step4.browser_tools._network_fault import (
            recover_network_fault,
        )

        await recover_network_fault(engine, "offline")


async def _switch_case_identity(engine, intent, project):
    """per-case 身份切换（★ RC-C 根因修复 2026-09-24）。

    根因: 原实现走 ``apply_case_identity`` —— 只改 config 层 username/password，
      浏览器 cookies/localStorage **原样保留**，且**不重登录** → "切换"是装饰性的：
      用例实际仍以 run 级默认身份（admin）的会话执行。后果（实测）:
        · "viewer 不允许新增/编辑/删除（403）" 用例以 admin 跑 → 页面呈现 admin 视图
          （新增按钮可见/可直达）→ 断言"应被拒绝"必然假 FAIL；
        · "未登录用户访问业务页面须重定向登录页" 用例带着 admin 会话跑 → 不重定向 → 假 FAIL；
        · 真实越权漏洞（SUT 漏做角色校验）永远不可能被检出。
      属"验证手段与期望性质错配"的引擎缺陷，非 SUT 缺陷。

    修复: 改走 ``identity_orchestrator.switch_identity`` —— **先隔离会话、再改凭据、
      最后重登录**（顺序不可颠倒，否则旧会话被新身份复用 = 串号）。
    契约: 返回 ``(snapshot, applied)``；``applied=True`` 时调用方 **必须**在 finally
      调用 ``restore_default_identity`` 切回 run 级默认身份（切走必须切回）。

    门控: ``identity_orchestrator._enabled`` —— 显式配置优先，未配置时按靶子
      声明角色数据驱动；用例未声明 identity → 恒 applied=False（零行为变更）。
    """
    snapshot = None
    try:
        from src.pipeline.utils import identity_orchestrator as _orch
        from src.pipeline.utils.identity_pool import snapshot_identity_override

        snapshot = snapshot_identity_override()
        spec, reason = _orch.resolve_identity_spec(intent, project)
        if spec is None:
            if str(reason).startswith("missing:"):
                _log.warning(
                    "[serial] 用例 %s 声明的身份不可解析(%s) → 保持默认身份执行",
                    intent.get("case_id", "?"),
                    reason,
                )
            return snapshot, False
        rec = await _orch.switch_identity(engine, spec, project, relogin=True)
        if not rec.get("ok"):
            _log.warning(
                "[serial] 用例 %s 身份切换未生效(%s) → 保持默认身份执行",
                intent.get("case_id", "?"),
                rec.get("reason"),
            )
            return snapshot, False
        _log.info(
            "[serial] 用例 %s 已切换身份 → %s (会话变更=%s 重登录=%s)",
            intent.get("case_id", "?"),
            spec.key or spec.role,
            rec.get("session_changed"),
            rec.get("relogin_ok") if rec.get("relogin_attempted") else "skip",
        )
        return snapshot, True
    except Exception as _e:  # noqa: BLE001
        _log.warning("[serial] 身份切换异常(保持默认身份): %s", str(_e)[:120])
        return snapshot, False


async def _restore_case_identity(engine, snapshot, project, applied) -> None:
    """用例结束后切回 run 级默认身份（``applied`` 为 False 时 no-op）。

    根因: 低权/匿名会话若不带回，后续用例串号 → 大面积假 FAIL（见
      ``identity_orchestrator.restore_default_identity`` 说明）。
    """
    if not applied:
        return
    with guard("_restore_case_identity: from src.pipeline.utils import ident", log=_log, level=logging.WARNING):
        from src.pipeline.utils import identity_orchestrator as _orch

        await _orch.restore_default_identity(engine, snapshot, project)


try:
    from gui_app import log_step_ok, log_step_fail, update_monitor_dom
except Exception as _e:
    _log.debug("[serial] 非关键异常: %s", _e, exc_info=True)
    log_step_ok = lambda *a: print(
        "    [Step %d/%d] OK (%.1fs) — %s" % (a[0], a[1], a[2], a[3][:50])
    )
    log_step_fail = lambda *a: print(
        "    [Step %d/%d] FAIL (%.1fs) — %s" % (a[0], a[1], a[2], a[3][:50])
    )
    update_monitor_dom = lambda c, o: None


class _BrowserCoreSerialMixin:
    """串行执行能力。"""

    async def _execute_all(
        self,
        candidates,
        browser_info=None,
        headless=True,
        timeout_ms=None,
        user_id=None,
        progress_callback=None,
        max_parallel=None,
    ):
        from src.pipeline.steps.step4.browser_ai import BrowserAi
        from src.pipeline.steps.step4.playwright_engine import PlaywrightEngine
        from src.pipeline.steps.step4.api_tracker import ApiTracker
        from src.pipeline.steps.step4.data_factory_core import DataFactory

        if not candidates:
            return {"passed": 0, "failed": 0, "total": 0, "results": []}

        # 执行形态（串行/并行）只在 base.run_test_cases 判定一次：走到本函数即代表已确定为串行
        # （唯一情形：失败即停开启时并行无法批内中断，违反 AGENTS.md §10.1）。此处不二次降级，
        # 否则同一语义两处判定。
        if max_parallel is None:
            from src.core.pipeline_config import pipeline_config

            max_parallel = getattr(pipeline_config, "browser_max_parallel", 1)
        _log.info("[BrowserCore] 串行执行: %d 个用例 (max_parallel=%s)", len(candidates), max_parallel)

        # --- 按 I/O 分区 ---
        read_intents, write_intents = self._partition_intents_by_io(candidates)
        # ★ v4.3.2: write_intents 是 dict[str, list[dict]]（按 location 分桶），len() 只返回桶数
        _write_count = (
            sum(len(v) for v in write_intents.values()) if write_intents else 0
        )
        _log.info(
            "[BrowserCore] 串行执行: %d 个读意图, %d 个写意图",
            len(read_intents),
            _write_count,
        )
        if write_intents:
            _log.info(
                "[BrowserCore] ⚠ 检测到 %d 个写意图，将串行执行以避免数据冲突",
                _write_count,
            )

        all_results = []
        total_passed = 0
        total_failed = 0
        total = len(candidates)
        step_num = 0

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
                except Exception as _r34:
                    _log.debug(
                        "[serial] 异常降级(pass): %s", str(_r34)[:60], exc_info=True
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

            # ★ v3.0 (2026-07-10): 单用例总超时，防 AI 循环卡死
            from src.core.pipeline_config import pipeline_config as _pc

            _CASE_TIMEOUT_SEC = getattr(_pc, "step4_case_timeout_sec", 180)

            # ★ Phase 1 (2026-07-11): 页面探索 + DOM 预缓存（串行路径）
            from src.pipeline.steps.step4.browser_core.page_explorer import PageExplorer
            from src.core.config_loader import (
                config,
            )  # ★ 修复: line 135 局部 import 导致 config 成为整个函数的局部变量

            _app_url = getattr(config, "app_url", "") or ""
            _page_explorer = PageExplorer()
            _explored = await _page_explorer.explore_from_test_cases(
                engine, read_intents, base_url=_app_url
            )
            self._page_explorer = _page_explorer
            if _explored > 0:
                _stats = _page_explorer.get_stats()
                _log.info(
                    "[BrowserCore] 页面探索(串行): %d 页面, %d 元素, %d 关键词",
                    _stats["explored_pages"],
                    _stats["total_elements"],
                    _stats["indexed_keywords"],
                )

            # ★ RC-C (2026-09-24): per-case 身份热切换（声明 identity 才切换，否则零行为变更）
            #   门控见 identity_orchestrator._enabled（显式配置优先 / 靶子 roles 数据驱动）。
            _project_name = str(getattr(config, "project_name", "") or "")

            # 先执行读意图
            for intent in read_intents:
                if self._force_stop_check and self._force_stop_check():
                    if not self._stop_printed:
                        _log.warning("[STOP] 收到停止信号，中止执行")
                        self._stop_printed = True
                    break
                # ★ [FIX] 外层用例循环补暂停检查点，原仅 _base.py 子步骤级检查，
                #    导致"两个用例之间"无法立即响应暂停（会跑完 N+1 的开题逻辑才响应）
                await pause_controller.async_sleep_while_paused()
                step_num += 1
                # ★ 通用页面状态重置：关闭残余弹窗/模态框，防止页面状态污染
                await _reset_page_state(engine)
                # ★ F-R1 (2026-08-06): 网络故障兜底恢复（offline 全局断网 → 用例间强制恢复）
                await _recover_network_fault(engine)
                # ★ RC-C (2026-09-24): per-case 身份热切换
                #   作用域=单条用例执行；finally 恢复 run 级默认身份（切走必须切回）
                _id_snapshot, _id_applied = await _switch_case_identity(
                    engine, intent, _project_name
                )
                try:
                    # ★ v3.0: 用 asyncio.wait_for 包装单用例超时
                    # ★ 2026-10-27: 该等待有硬上界（到期即取消并落结构化 TIMEOUT），
                    #   登记为「在飞有界等待」→ run 级看门狗不再把用例内的长操作误判为
                    #   整轮挂死；恢复粒度下沉到用例（专业测试执行的正确终止粒度）。
                    try:
                        with bounded_wait_inflight(_CASE_TIMEOUT_SEC):
                            result = await asyncio.wait_for(
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
                            "[serial] 用例执行超时(%ds): case_id=%s",
                            _CASE_TIMEOUT_SEC,
                            intent.get("case_id", "?"),
                        )
                        result = {
                            "passed": False,
                            "case_id": intent.get("case_id", ""),
                            "error": "用例执行超时(%ds)" % _CASE_TIMEOUT_SEC,
                            "failure_category": "TIMEOUT",
                        }
                        # ★ v3.0.1 (2026-07-10): timeout 分支补落库，避免 executions 表数据割裂
                        with guard("_execute_all: from src.users.audit import record_e", log=_log):
                            from src.users.audit import record_execution
                            from src.core.config_loader import config as _cfg
                            from src.core.versioning import resolve_run_id

                            _proj = _cfg.project_name or ""
                            _uid = user_id or "system"
                            _run_id = resolve_run_id(_cfg.project_name or "") or ""
                            _cid = str(intent.get("case_id", "") or "").strip()
                            if _cid:
                                # ★ P0-fix(2026-07-24): 超时分支补 evidence（§13.4 红线）
                                #   原 bug: record_execution 未传 expected/actual/details → evidence 全空
                                #   修复: 调用 build_timeout_evidence 构造超时兜底 evidence
                                from src.pipeline.steps.step4.browser_core.single_mixins._evidence_extractor import (
                                    build_timeout_evidence,
                                )

                                _exp, _act, _det = build_timeout_evidence(
                                    _CASE_TIMEOUT_SEC, "用例"
                                )
                                record_execution(
                                    user_id=_uid,
                                    project=_proj,
                                    case_id=_cid,
                                    status="FAIL",
                                    case_asset_file="",
                                    case_row=None,
                                    failure_reason=result["error"],
                                    failure_category="HARNESS_ERROR",
                                    run_id=_run_id,
                                    check_type="browser_exec",
                                    expected=_exp,
                                    actual=_act,
                                    details=_det,
                                )
                finally:
                    await _restore_case_identity(
                        engine, _id_snapshot, _project_name, _id_applied
                    )
                all_results.append(result)
                _bucket = count_bucket(result)
                if _bucket == "pass":
                    total_passed += 1
                elif _bucket == "fail":
                    total_failed += 1
                    # §10.1 失败即停：判据读管线配置（唯一实现在 BrowserCore.
                    # _should_stop_on_failure），全自动管线为 False → 跑完全部用例。
                    if self._should_stop_on_failure():
                        _log.warning(
                            "[serial] §10.1 用例失败即停: case_id=%s error=%s",
                            result.get("case_id", "?"),
                            str(result.get("error", ""))[:120],
                        )
                        break
                    else:
                        _log.info(
                            "[serial] 用例失败但继续执行(step4_stop_on_failure=False): case_id=%s",
                            result.get("case_id", "?"),
                        )

            # 再执行写意图
            # ★ v4.3.2: write_intents 是 dict[str, list[dict]]（按 location 分桶），
            #   原代码 for intent in write_intents 遍历 dict keys（字符串）
            #   → 'str' object has no attribute 'get' 崩溃。
            #   修正：展开分桶，逐个执行。
            _stop_write = False
            for _location, _bucket in write_intents.items():
                if _stop_write:
                    break
                for intent in _bucket:
                    _log.info(
                        "[serial] 准备执行写意图: case_id=%s",
                        (
                            intent.get("case_id", "?")
                            if isinstance(intent, dict)
                            else str(intent)[:80]
                        ),
                    )
                    if self._force_stop_check and self._force_stop_check():
                        if not self._stop_printed:
                            _log.warning("[STOP] 收到停止信号，中止执行")
                            self._stop_printed = True
                        _stop_write = True
                        break
                    # ★ [FIX] 写意图循环补暂停检查点（与读意图循环一致）
                    await pause_controller.async_sleep_while_paused()
                    step_num += 1
                    # ★ 通用页面状态重置：关闭残余弹窗/模态框，防止页面状态污染
                    await _reset_page_state(engine)
                    # ★ F-R1 (2026-08-06): 网络故障兜底恢复（offline 全局断网 → 用例间强制恢复）
                    await _recover_network_fault(engine)
                    # ★ RC-C (2026-09-24): per-case 身份热切换（与读意图循环同源）
                    _id_snapshot, _id_applied = await _switch_case_identity(
                        engine, intent, _project_name
                    )
                    try:
                        # ★ v3.0: 写意图同样加单用例超时（同上：登记有界等待）
                        try:
                            with bounded_wait_inflight(_CASE_TIMEOUT_SEC):
                                result = await asyncio.wait_for(
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
                                "[serial] 写意图执行超时(%ds): case_id=%s",
                                _CASE_TIMEOUT_SEC,
                                intent.get("case_id", "?"),
                            )
                            result = {
                                "passed": False,
                                "case_id": intent.get("case_id", ""),
                                "error": "写意图执行超时(%ds)" % _CASE_TIMEOUT_SEC,
                                "failure_category": "TIMEOUT",
                            }
                            # ★ v3.0.1 (2026-07-10): 写意图 timeout 分支补落库
                            with guard("_execute_all: from src.users.audit import record_e", log=_log):
                                from src.users.audit import record_execution
                                from src.core.config_loader import config as _cfg
                                from src.core.versioning import resolve_run_id

                                _proj = _cfg.project_name or ""
                                _uid = user_id or "system"
                                _run_id = resolve_run_id(_cfg.project_name or "") or ""
                                _cid = str(intent.get("case_id", "") or "").strip()
                                if _cid:
                                    # ★ P0-fix(2026-07-24): 写意图超时分支补 evidence（§13.4 红线）
                                    from src.pipeline.steps.step4.browser_core.single_mixins._evidence_extractor import (
                                        build_timeout_evidence,
                                    )

                                    _exp, _act, _det = build_timeout_evidence(
                                        _CASE_TIMEOUT_SEC, "写意图"
                                    )
                                    record_execution(
                                        user_id=_uid,
                                        project=_proj,
                                        case_id=_cid,
                                        status="FAIL",
                                        case_asset_file="",
                                        case_row=None,
                                        failure_reason=result["error"],
                                        failure_category="HARNESS_ERROR",
                                        run_id=_run_id,
                                        check_type="browser_exec",
                                        expected=_exp,
                                        actual=_act,
                                        details=_det,
                                    )
                    finally:
                        await _restore_case_identity(
                            engine, _id_snapshot, _project_name, _id_applied
                        )
                    all_results.append(result)
                    _bucket = count_bucket(result)
                    if _bucket == "pass":
                        total_passed += 1
                    elif _bucket == "fail":
                        total_failed += 1
                        # 写意图同样走唯一判据（读管线配置）
                        if self._should_stop_on_failure():
                            _log.warning(
                                "[serial] §10.1 写意图失败即停: case_id=%s error=%s",
                                result.get("case_id", "?"),
                                str(result.get("error", ""))[:120],
                            )
                            _stop_write = True
                            break
                        else:
                            _log.info(
                                "[serial] 写意图失败但继续执行(step4_stop_on_failure=False): case_id=%s",
                                result.get("case_id", "?"),
                            )

            # ★ 2026-09-02 横切门禁统一挂载：a11y / perf / visual 三横切维度
            #   在本串行后端一次性执行（读+写意图循环之后、关闭浏览器之前），
            #   结果作为独立验证维度写入返回值（不参与用例 PASS/FAIL，fail-safe）。
            #   设计约束：与 target_empirical 后端共用 run_cross_cut_gates_async 同一
            #   入口，做到"一处定义、任意异步后端复用"，不在这里粘贴三端调用代码。
            _cross_cut = await self._run_cross_cut_once(engine, api_tracker)

        except Exception as e:
            _log.error("[ERROR] 执行异常: %s", str(e)[:200], exc_info=True)
            self._last_failure_detail = str(e)
        finally:
            # ★ F-R1 (2026-08-06): 网络故障兜底恢复（engine 仍存活时清理 offline 残留，
            #   异常只记日志不阻断；NETWORK_FAULT_ENABLED=False 时零行为变更）
            # ★ QA(2026-08-12): 收尾阶段的 Playwright await 也可能在 IOCP 挂死，
            #   此处加 30s 硬超时，超时继续关闭浏览器，绝不无限等待。
            try:
                await asyncio.wait_for(_recover_network_fault(engine), timeout=30)
            except asyncio.TimeoutError:
                _log.warning("[serial] 网络故障兜底恢复超时(30s)，继续关闭浏览器")
            except Exception as _nf2_e:
                _log.warning(
                    "[serial] 网络故障兜底恢复异常(不阻断): %s", _nf2_e
                )
            await engine.stop()  # engine.stop() 内部各 close/stop 均已带硬超时
            _log.info("[BrowserCore] 浏览器已关闭")
            # ★ S45-009: 强制停止后释放测试账号
            self._release_test_accounts()

        self._record_plugin_metrics(total)

        return {
            "passed": total_passed,
            "failed": total_failed,
            "total": total,
            "results": all_results,
            "cross_cut": getattr(self, "_last_cross_cut", None) or {},
        }

    async def _run_cross_cut_once(self, engine, api_tracker) -> dict:
        """串行后端一次性执行 a11y/perf/visual 三横切门禁（fail-safe，不影响用例判定）。

        通用适配：本后端是 async PlaywrightEngine，故复用 cross_cut_gates 的 async
        入口（a11y/visual 走静态快照、perf 走 async 采集）。三入门禁各自 fail-safe，
        任何异常只落日志，绝不阻断用例 PASS/FAIL、绝不伪造结论。
        """
        from src.pipeline.steps.step4.empirical.cross_cut_gates import (
            run_cross_cut_gates_async,
            summarize_cross_cut,
        )

        try:
            page = getattr(engine, "_page", None)
            if page is None:
                _log.info("[serial] 横切门禁跳过：无有效页面")
                return {}
            _api_calls = []
            with guard("_run_cross_cut_once: _api_calls = list(getattr(api_tracke", log=_log):
                _api_calls = list(getattr(api_tracker, "_recent_requests", None) or [])
            _target_cfg = {
                "url": str(getattr(config, "app_url", "") or ""),
                "target_key": str(getattr(config, "project_name", "") or ""),
            }
            from src.core.versioning import resolve_run_id

            _run_id = str(resolve_run_id(config.project_name or "") or "")
            result = await run_cross_cut_gates_async(
                page=page,
                api_calls=_api_calls,
                target_cfg=_target_cfg,
                run_id=_run_id,
                case_id="",
            )
            self._last_cross_cut = result or {}
            # ★ 2026-09-02: 把横切门禁结果落盘到 Step5 报告真正读取的 sink，
            #   否则仅返回 dict/日志，报告中 visual 维度在常规流水线仍缺失。
            #   - a11y：★ 根因修复——per-case 不再是唯一落库路径（其仅在用例显式
            #     声明 step.a11y 时激活）；现由 _persist_cross_cut_a11y 对称落 run 级
            #     oracle_results(accessibility)，且仅当逐用例未覆盖时写入（避免重复计数）。
            #   - visual：常规流水线本不产 visual_review 行 → 报告视觉块空缺；
            #     此处以 run 级独立复核(execution_id=0)写一条通用 vlm_diff，通用无业务词。
            self._persist_cross_cut_visual(self._last_cross_cut)
            # ★ 2026-09-03 性能维唯一事实源：与 visual 对称，把 run 级 perf 门禁结果
            #   落入 oracle_results(performance)。此前 perf 只进日志/报告，无 MySQL
            #   落库——违反「MySQL 为校验数据唯一事实源」约束，前端不可复算。
            self._persist_cross_cut_perf(self._last_cross_cut)
            # ★ 2026-09-05 安全维唯一事实源：与 perf 对称，把 run 级 security 门禁结果
            #   落入 oracle_results(security)，使安全横切维度在常规流水线同样可复算。
            self._persist_cross_cut_security(self._last_cross_cut)
            # ★ 2026-09-20：与 security 同构，run 级 a11y 结果落 oracle_results(accessibility)。
            self._persist_cross_cut_a11y(self._last_cross_cut)
            _log.info("[serial] %s", summarize_cross_cut(self._last_cross_cut))
            return self._last_cross_cut
        except Exception as _e:
            _log.warning("[serial] 横切门禁执行异常(不阻断): %s", str(_e)[:200])
            return {}

    def _persist_cross_cut_visual(self, cross: dict) -> None:
        """把 run 级 visual 横切门禁结果写入 visual_review 表（fail-safe）。"""
        with guard("_persist_cross_cut_visual: _vis = (cross or {}).get('visual') o", log=_log, level=logging.WARNING):
            _vis = (cross or {}).get("visual") or {}
            if not _vis.get("enabled"):
                return
            if not str(_vis.get("status") or "").strip():
                return
            from src.oracle.store import write_visual_review
            from src.core.versioning import current_version_id

            _project = str(getattr(config, "project_name", "") or "")
            if not _project:
                return
            _vlm_diff = {
                "status": _vis.get("status"),
                "passed": _vis.get("passed"),
                "skipped": _vis.get("skipped"),
                "confidence": _vis.get("confidence"),
                "reason": str(_vis.get("reason") or "")[:500],
                "evidence": str(_vis.get("evidence") or "")[:500],
            }
            write_visual_review(
                project=_project,
                version_id=int(current_version_id(_project) or 0),
                execution_id=0,  # run 级独立复核
                vlm_diff=_vlm_diff,
            )

    def _persist_cross_cut_perf(self, cross: dict) -> None:
        """把 run 级 perf 门禁结果写入 oracle_results(performance)（fail-safe）。

        与 :meth:`_persist_cross_cut_visual` 对称：三横切维均落 MySQL 唯一事实源，
        perf 作为「性能 oracle」复用通用 write_oracle_result。无样本/禁用时
        write_perf_result_from_gate 内部返回 False 不写空记录，幂等安全。
        """
        with guard("_persist_cross_cut_perf: _perf = (cross or {}).get('perf') or", log=_log, level=logging.WARNING):
            _perf = (cross or {}).get("perf") or {}
            if not _perf.get("enabled"):
                return
            _project = str(getattr(config, "project_name", "") or "")
            if not _project:
                return
            from src.oracle.store import write_perf_result_from_gate
            from src.core.versioning import current_version_id

            write_perf_result_from_gate(
                project=_project,
                version_id=int(current_version_id(_project) or 0),
                execution_id=0,  # run 级独立复核
                gate=_perf,
            )

    def _persist_cross_cut_security(self, cross: dict) -> None:
        """把 run 级 security 横切门禁结果写入 oracle_results(security)（fail-safe）。

        与 :meth:`_persist_cross_cut_perf` 对称：安全横切维同样落 MySQL 唯一事实源，
        使安全发现可在常规流水线中复算。无样本/禁用时按 fail-safe 跳过。
        """
        with guard("_persist_cross_cut_security: _sec = (cross or {}).get('security')", log=_log, level=logging.WARNING):
            _sec = (cross or {}).get("security") or {}
            if not _sec.get("enabled"):
                return
            _project = str(getattr(config, "project_name", "") or "")
            if not _project:
                return
            _findings = _sec.get("findings") or []
            _has_hi = bool(
                any(f.get("severity") in ("critical", "high") for f in _findings)
            )
            _total = int(_sec.get("total") or 0)
            from src.oracle.store import write_oracle_result
            from src.core.versioning import current_version_id, current_run_id

            # 【2026-09-20】run 级行必须带真实 run_id，否则 Step5 聚合器仅经 executions
            # INNER JOIN 归属 run 时会丢弃 execution_id=0 的 run 级 security 行（曾恒不可见）。
            _rid = current_run_id(_project)

            # 【2026-09-05】authz（越权/访问控制）子维度随 security 落库（与 empirical 对称）。
            _az = _sec.get("authz") or {}
            _az_detail = None
            if _az.get("total"):
                _az_detail = {
                    "all_pass": bool(_az.get("all_pass")),
                    "total": int(_az.get("total") or 0),
                    "passed": int(_az.get("passed") or 0),
                    "failed": int(_az.get("failed") or 0),
                    "probes": (_az.get("probes") or [])[:50],
                    "error": _az.get("error", ""),
                }

            write_oracle_result(
                project=_project,
                version_id=int(current_version_id(_project) or 0),
                execution_id=0,  # run 级独立复核
                run_id=_rid,
                oracle_type="security",
                verdict="fail" if _has_hi else ("warn" if _total else "pass"),
                score=max(0.0, 1.0 - min(_total, 20) * 0.05),
                detail={
                    "case_id": str(_sec.get("source") or ""),
                    "checked": 1 if _total or not _sec.get("error") else 0,
                    "skipped": 0,
                    "total": _total,
                    "by_severity": dict(_sec.get("by_severity") or {}),
                    "findings": _findings[:50],
                    "authz": _az_detail,
                },
            )

    def _persist_cross_cut_a11y(self, cross: dict) -> None:
        """把 run 级 a11y 横切门禁结果写入 oracle_results(accessibility)（fail-safe）。

        ★ 根因修复(2026-09-20): 原 serial 只持久化 visual/perf/security 三门，a11y 完全
          依赖逐用例 ``persist_oracle_after_execution`` 落库；而逐用例 a11y 仅在用例步骤
          显式声明 ``step.a11y.enabled`` 时激活（fail-open）。当用例不声明（如 modsystem 的
          AI 聚合用例）时，run 级 a11y 门禁虽已跑出结果，却既未落库也无人读取 → 报告
          a11y 恒 0。
          修复：与 security 对称，把 run 级 a11y 结果落 oracle_results(accessibility)。
          **仅在逐用例 a11y 未落库时写入**（作为回退单点），避免与 per-case 行重复计数——
          忠实还原 serial 原设计意图「per-case 已覆盖则不重复落库」。
        """
        with guard("_persist_cross_cut_a11y: _a11y = (cross or {}).get('a11y') or", log=_log, level=logging.WARNING):
            _a11y = (cross or {}).get("a11y") or {}
            if not _a11y.get("enabled"):
                return
            _project = str(getattr(config, "project_name", "") or "")
            if not _project:
                return
            from src.core.versioning import current_version_id, current_run_id

            _rid = current_run_id(_project)
            from src.core.db_manager import get_connection

            _conn = None
            try:
                _conn = get_connection()
                # 回退判定：本轮是否已有逐用例 accessibility 行？有则 per-case 已覆盖，跳过。
                _pc = _conn.execute(
                    "SELECT COUNT(*) c FROM oracle_results o "
                    "INNER JOIN executions e ON e.id = o.execution_id "
                    "WHERE o.oracle_type='accessibility' AND o.execution_id>0 "
                    "AND e.project=%s AND e.run_id=%s",
                    (_project, _rid),
                ).fetchone()
                if int(_pc["c"] or 0) > 0:
                    _log.info("[serial] a11y 已有逐用例落库(%d 行)，跳过 run 级回退", _pc["c"])
                    return
                # 幂等：同 run 已有 run 级行则跳过（防重复执行同 run 累积）
                _prev = _conn.execute(
                    "SELECT COUNT(*) c FROM oracle_results "
                    "WHERE oracle_type='accessibility' AND execution_id=0 "
                    "AND project=%s AND run_id=%s",
                    (_project, _rid),
                ).fetchone()
                if int(_prev["c"] or 0) > 0:
                    _log.info("[serial] a11y 已存在 run 级行，跳过")
                    return
            finally:
                if _conn:
                    try:
                        _conn.close()
                    except (OSError, ConnectionError) as _e:
                        _log.debug("_persist_cross_cut_a11y best-effort 容忍: %s", str(_e)[:120])

            from src.oracle.store import write_oracle_result

            _findings = _a11y.get("findings") or []
            _has_hi = bool(
                any(f.get("severity") in ("critical", "high") for f in _findings)
            )
            _total = int(_a11y.get("total") or 0)
            write_oracle_result(
                project=_project,
                version_id=int(current_version_id(_project) or 0),
                execution_id=0,  # run 级独立复核
                run_id=_rid,
                oracle_type="accessibility",
                verdict="fail" if _has_hi else ("warn" if _total else "pass"),
                score=max(0.0, 1.0 - min(_total, 20) * 0.05),
                detail={
                    "case_id": str(_a11y.get("source") or "step4"),
                    "checked": 1 if (_total or not _a11y.get("error")) else 0,
                    "skipped": 0,
                    "total": _total,
                    "by_severity": dict(_a11y.get("by_severity") or {}),
                    "findings": _findings[:50],
                },
            )
            _log.info("[serial] run 级 a11y 落库: run=%s total=%d verdict=%s",
                      _rid, _total, "fail" if _has_hi else ("warn" if _total else "pass"))

    def _record_plugin_metrics(self, total: int) -> None:
        """★ v4.0 修复: 提取插件指标记录为共享方法，供 serial/parallel 两路径调用。

        原代码在 _execute_all 的 finally 块末尾，但 max_parallel > 1 时提前 return
        导致 _execute_intents_parallel 路径完全不记录这些指标。
        现在提取为独立方法，两条路径都能调用。
        """
        # 记录 Flaky 检测指标到 ai_health_metrics
        with guard("_record_plugin_metrics: from src.backend.state import _threa", log=_log):
            from src.backend.state import _thread_run_id
            from src.extensions.metrics_collector import metrics_collector

            run_id = getattr(_thread_run_id, "value", "") or ""
            if not run_id:
                _log.debug("[Metrics] FlakyGuard: run_id 未设置，跳过")
            else:
                flaky_plugin = None
                for p in self._plugin_manager._plugins:
                    if p.name == "flaky_guard":
                        flaky_plugin = p
                        break
                if not flaky_plugin:
                    _log.debug("[Metrics] FlakyGuard: 插件未注册，跳过")
                else:
                    stats = flaky_plugin.get_stats()
                    flaky_count = stats.get("flaky_detected", 0)
                    total_cases = stats.get("total_cases_tracked") or total
                    if total_cases > 0:
                        metrics_collector.record_flaky_count(
                            project=config.project_name,
                            run_id=run_id,
                            flaky_count=flaky_count,
                            total_cases=total_cases,
                        )
                        root_causes = {"env": 0, "timing": 0, "data": 0, "ai": 0}
                        for cid in flaky_plugin.get_flaky_intents():
                            rc = flaky_plugin._analyze_root_cause(
                                cid, flaky_plugin._flaky_intents.get(cid, {})
                            )
                            if rc == "environment":
                                root_causes["env"] += 1
                            elif rc == "timing":
                                root_causes["timing"] += 1
                            elif rc == "data":
                                root_causes["data"] += 1
                            elif rc == "ai_hallucination":
                                root_causes["ai"] += 1
                        metrics_collector.record_flaky_root_cause(
                            project=config.project_name,
                            run_id=run_id,
                            env_issues=root_causes["env"],
                            timing_issues=root_causes["timing"],
                            data_issues=root_causes["data"],
                            ai_hallucination_issues=root_causes["ai"],
                        )

        # ScorecardOracle — 写入 scorecard 指标
        with guard("_record_plugin_metrics: from src.backend.state import _threa", log=_log):
            from src.backend.state import _thread_run_id
            from src.extensions.metrics_collector import metrics_collector

            # ★ 根因修复(2026-09-20): 不再函数内 import config——Python 作用域规则会使
            #   config 成为本函数局部变量，导致本函数前段(Flaky 块 846/865 行)引用 config
            #   抛 UnboundLocalError（被 try/except 吞掉→指标静默丢失）。模块顶部(第14行)
            #   已有等价 import，直接复用。
            run_id = getattr(_thread_run_id, "value", "") or ""
            if not run_id:
                _log.debug("[Metrics] Scorecard: run_id 未设置，跳过")
            else:
                sc_plugin = None
                for p in self._plugin_manager._plugins:
                    if p.name == "scorecard_oracle":
                        sc_plugin = p
                        break
                if not sc_plugin:
                    _log.debug("[Metrics] Scorecard: 插件未注册，跳过")
                else:
                    stats = sc_plugin.get_stats()
                    total_cases = stats.get("total_evaluations") or total
                    if total_cases > 0:
                        avg_score = stats.get("avg_score", 0.0)
                        # ★ 根因修复(2026-09-20)：scorecard 分数为 0~1 加权分
                        #   （_pass_threshold 默认 0.75），原 70/50 是 0~100 阈值，
                        #   与 0~1 分数比较恒真 → 所有用例被误判 low。改按 0~1 分级。
                        high_score_cases = sum(
                            1 for h in sc_plugin._history if h.get("score", 0) >= 0.70
                        )
                        medium_score_cases = sum(
                            1
                            for h in sc_plugin._history
                            if 0.50 <= h.get("score", 0) < 0.70
                        )
                        low_score_cases = sum(
                            1 for h in sc_plugin._history if h.get("score", 0) < 0.50
                        )
                        metrics_collector.record_scorecard(
                            project=config.project_name,
                            run_id=run_id,
                            total_cases=total_cases,
                            avg_score=avg_score,
                            high_score_cases=high_score_cases,
                            medium_score_cases=medium_score_cases,
                            low_score_cases=low_score_cases,
                        )

        # HarnessGovernance — 写入 governance 指标
        with guard("_record_plugin_metrics: from src.backend.state import _threa", log=_log):
            from src.backend.state import _thread_run_id
            from src.extensions.metrics_collector import metrics_collector

            run_id = getattr(_thread_run_id, "value", "") or ""
            if not run_id:
                _log.debug("[Metrics] HarnessGovernance: run_id 未设置，跳过")
            else:
                hg_plugin = None
                for p in self._plugin_manager._plugins:
                    if p.name == "harness_governance":
                        hg_plugin = p
                        break
                if not hg_plugin:
                    _log.debug("[Metrics] HarnessGovernance: 插件未注册，跳过")
                else:
                    stats = hg_plugin.get_stats()
                    metrics_collector.record_harness_governance(
                        project=config.project_name,
                        run_id=run_id,
                        dangerous_intercepts=stats.get("dangerous_intercepts", 0),
                        tool_limit_violations=stats.get("tool_limit_violations", 0),
                        token_used=stats.get("token_used", 0),
                        token_budget_total=stats.get("token_budget_total", 0),
                    )

        # TokenBudget — 写入 token_budget 指标
        with guard("_record_plugin_metrics: from src.backend.state import _threa", log=_log):
            from src.backend.state import _thread_run_id
            from src.extensions.metrics_collector import metrics_collector

            run_id = getattr(_thread_run_id, "value", "") or ""
            if not run_id:
                _log.debug("[Metrics] TokenBudget: run_id 未设置，跳过")
            else:
                tb_plugin = None
                for p in self._plugin_manager._plugins:
                    if p.name == "token_budget":
                        tb_plugin = p
                        break
                if not tb_plugin:
                    _log.debug("[Metrics] TokenBudget: 插件未注册，跳过")
                else:
                    stats = tb_plugin.get_stats()
                    metrics_collector.record_token_budget(
                        project=config.project_name,
                        run_id=run_id,
                        step=4,
                        truncations=stats.get("truncations", 0),
                        budget_exceeded=stats.get("budget_exceeded", 0),
                        compressions=stats.get("compressions", 0),
                    )

        # SelfHealing — 写入 self_heal 指标
        with guard("_record_plugin_metrics: from src.backend.state import _threa", log=_log):
            from src.backend.state import _thread_run_id
            from src.extensions.metrics_collector import metrics_collector

            run_id = getattr(_thread_run_id, "value", "") or ""
            if not run_id:
                _log.debug("[Metrics] SelfHealing: run_id 未设置，跳过")
            else:
                sh_plugin = None
                for p in self._plugin_manager._plugins:
                    if p.name == "self_healing":
                        sh_plugin = p
                        break
                if not sh_plugin:
                    _log.debug("[Metrics] SelfHealing: 插件未注册，跳过")
                else:
                    stats = sh_plugin.get_stats()
                    metrics_collector.record_self_heal(
                        project=config.project_name,
                        run_id=run_id,
                        total_attempts=stats.get("heal_attempts", 0),
                        heal_successes=stats.get("heal_success", 0),
                        verify_successes=stats.get("heal_verify_success", 0),
                        verify_fails=stats.get("heal_verify_fail", 0),
                    )

        # ClickFallback — 五级点击回退命中率统计
        with guard("_record_plugin_metrics: from src.backend.state import _threa", log=_log):
            from src.backend.state import _thread_run_id
            from src.extensions.metrics_collector import metrics_collector
            from src.pipeline.steps.step4.playwright_engine.stats import _click_stats

            run_id = getattr(_thread_run_id, "value", "") or ""
            if run_id:
                stats = _click_stats.snapshot()
                metrics_collector.record_click_fallback_stats(
                    project=config.project_name,
                    run_id=run_id,
                    stats=stats,
                )

        # 原子操作回滚统计 — 从 tool_execution_logger 查询后写入
        with guard("_record_plugin_metrics: from src.backend.state import _threa", log=_log):
            from src.backend.state import _thread_run_id
            from src.extensions.metrics_collector import metrics_collector
            from src.pipeline.steps.step4.tool_execution_logger import tool_logger

            run_id = getattr(_thread_run_id, "value", "") or ""
            if run_id:
                summary = tool_logger.get_stats(config.project_name, days=1)
                rollback_count = summary.get("rollback_count", 0)
                total_calls = summary.get("total_calls", 0)
                if total_calls > 0:
                    metrics_collector.record_atomic_rollback(
                        project=config.project_name,
                        run_id=run_id,
                        rollback_count=rollback_count,
                        total_atomic_executions=total_calls,
                    )
                    tool_mode_count = summary.get("tool_mode_calls", 0)
                    metrics_collector.record_tool_mode(
                        project=config.project_name,
                        run_id=run_id,
                        tool_mode_calls=tool_mode_count,
                        ai_total_calls=total_calls,
                    )
