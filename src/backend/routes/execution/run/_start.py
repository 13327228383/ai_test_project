# -*- coding: utf-8 -*-
"""
启动路由
========
coverage-check, run-step, step4-run, run-all, retry-failed

[B6 配套] 新增 ``_warn_unsupported_step4_opts()``：前端 Step4 配置面板会提交若干
后端**当前并不消费**的字段（viewport_width / viewport_height /
screenshot_on_failure / screenshot_dir / deployVersion）。原先这些字段被静默丢弃，
用户以为配置生效、实际未生效且无任何提示。现改为逐项打 WARNING 显式告警。
仅新增日志，不改变任何执行行为。

[已复核-DeepSeek-V3 2026-08-16]
"""

import time
from src.core.guarded import guard
import uuid
import threading
import logging
from flask import request, jsonify

_log = logging.getLogger(__name__)

from src.backend.routes.execution import (
    _check_step_permission,
    _current_project,
    _is_running,
    _get_paused_step,
    _active_runs,
    _active_runs_lock,
    _make_session_runner,
    _run_step,
    _retry_failed,
    _case_review_gate_status,
)
from src.backend.executor._run_step import StepRunOptions
from src.core.config_session import ensure_session_key
from src.backend.state import _sweep_stale_runs  # ★ F5: 僵尸 active_runs 清理


# ★ [B6 配套] Step4 前端会提交、但后端流水线**当前并不消费**的配置项。
#   经 grep 核实（src/pipeline/ + src/backend/ 全量搜索键名）这些键 0 引用：
#   用户在 step4-config 页面调了这些值，实际不会产生任何效果。
#   在此显式告警，避免"设置了却无声无息不生效"这种最难排查的问题。
#   key -> 面向用户的说明
_STEP4_UNSUPPORTED_OPTS: dict[str, str] = {
    "viewport_width": "浏览器视口宽度（当前由浏览器/Playwright 默认值决定）",
    "viewport_height": "浏览器视口高度（当前由浏览器/Playwright 默认值决定）",
    "screenshot_on_failure": "失败时截图（当前截图策略由执行器内部固定逻辑决定）",
    "screenshot_dir": "截图目录（当前截图统一存 MySQL execution_screenshots 表）",
    "deployVersion": "转测版本过滤（值为项目版本ID，与转测文档内版本号语义不符，见 B6 遗留项）",
}


def _warn_unsupported_step4_opts(opts: dict) -> list[str]:
    """对前端提交但后端不消费的 Step4 配置项打显式告警。

    只读不改：**不修改 opts**，不影响任何既有执行逻辑，纯可观测性增强。

    Args:
        opts: 前端提交的 Step4 配置字典。

    Returns:
        实际命中的未支持配置项键名列表（便于调用方回传前端或写日志）。
    """
    if not opts or not isinstance(opts, dict):
        return []
    _hit: list[str] = []
    for _key, _desc in _STEP4_UNSUPPORTED_OPTS.items():
        _val = opts.get(_key)
        # 只对"用户确实填了值"的情况告警：空串/None/False/0 视为未设置，避免噪音
        if _val in (None, "", False, 0):
            continue
        _hit.append(_key)
        _log.warning(
            "[Step4配置] ⚠️ 已提交 %s=%r，但后端当前**不消费该配置**，本次执行不会生效 —— %s",
            _key,
            _val if not isinstance(_val, str) else _val[:60],
            _desc,
        )
    return _hit


def _clear_force_stop_for_project(project):
    """★ 通用修复(2026-07-21 v12): 启动新 run 前清除 force_stop 标记。

    根因: stop API 设置 _force_stop_by_project[project]=True 后,
          即使原 run 结束（finally 块执行 _set_force_stop(False)）,
          仍存在时序窗口：原 run 还在 finally 之前, 新 run 已启动,
          scheduler._force_stop_check 检查到残留的 force_stop=True,
          立即判定为"被强制停止"。

    通用性:
      - 跨系统通用: 纯状态清理, 无业务依赖
      - 跨场景通用: 覆盖所有 6 个 run 启动入口
      - 防御性: 即使 watchdog 修复未生效, 启动入口也会兜底清除
    """
    with guard("_clear_force_stop_for_project: from src.backend.state import _set_f", log=_log, level=logging.WARNING):
        from src.backend.state import _set_force_stop

        _set_force_stop(False, project)


from src.core.versioning import make_run_id, current_version_label


def _gen_run_id_and_clear_old_logs(project: str, step: int) -> str:
    """生成确定性 run_id，并只清除「将被重跑的这一步」的旧日志。

    why: run_id = '{project}__{version_label}' 在同一版本内复用，整条流水线共享一个
      run_id；若按键清全部，重跑 Step4 会抹掉 Step1~3 的 RUN_START/RUN_END 证据链，
      验收切片无从判读。故清理范围收敛到 step_num。
    """
    vlabel = current_version_label(project)
    rid = make_run_id(project, vlabel)
    with guard("_gen_run_id_and_clear_old_logs: from src.core.db_manager import get_", log=_log, level=logging.WARNING):
        from src.core.db_manager import get_connection

        conn = get_connection()
        try:
            conn.execute(
                "DELETE FROM run_logs WHERE run_id = %s AND step_num = %s", (rid, step)
            )
            conn.commit()
        finally:
            try:
                conn.close()
            except Exception as _e:
                _log.debug("[run_id] conn.close 失败: %s", _e, exc_info=True)
    return rid


def _count_test_cases(project: str) -> int:
    """查询 test_cases 表中指定项目的用例数（防御性检查）。"""
    try:
        from src.core.db_manager import get_connection

        conn = get_connection()
        try:
            row = conn.execute(
                "SELECT COUNT(*) AS cnt FROM test_cases WHERE project=%s", (project,)
            ).fetchone()
            return int(row["cnt"]) if row else 0
        finally:
            with guard("_count_test_cases: conn.close()", log=_log):
                conn.close()
    except Exception:
        return -1  # DB 不可用时返回 -1，不阻塞执行


def _preflight_browser_dependency():
    """run-trigger 端点的浏览器依赖前置检查（[已复核-DeepSeek-V3 2026-08-16]）。

    在 spawn 后台线程 / 启动浏览器之前快速检测 Playwright + 浏览器二进制是否就绪。
    缺依赖时返回 (False, reason, 503)，调用方据此直接 503 响应，
    **绝不 spawn 线程、绝不阻塞服务**，避免一个卡住的浏览器 launch 拖垮全局（含 /api/status）。
    """
    try:
        from src.pipeline.steps.step4.playwright_engine._readiness import (
            check_browser_readiness,
        )

        ready, reason = check_browser_readiness()
        if not ready:
            return False, reason, 503
        return True, "", 200
    except Exception as _e:
        # 检测自身异常不应阻断执行（降级为放行，由引擎层 timeout 兜底）
        _log.debug("[preflight] 浏览器依赖检测异常(降级放行): %s", str(_e)[:120])
        return True, "", 200


# ── 独立执行槽（信号量，[已复核-DeepSeek-V3 2026-08-16]）──
#   限制并发 run-trigger 执行体数量，避免一次性 spawn 过多线程 / Chromium 子进程耗尽
#   FD / 内存 / 线程，进而拖垮 waitress 线程池（含 /api/status、/api/login 等基础设施端点）。
#   槽位取用非阻塞：满时直接 429，绝不阻塞当前 waitress 工作线程。
_RUN_SLOT = threading.Semaphore(4)

# ★ watchdog 自愈上限（QA Bug2, 2026-08-12）：
#   单步执行（step4-run-single / step4-run / retry-failed 等）正常耗时约 26s~4min，
#   挂起时需尽快释放槽位避免级联 429，取 180s；
#   run-all（step=1 → Step1..5 全流程）允许更长的合法执行，取 1800s。
#   ★  根因修复：该上限改为「**进展停滞**阈值」而非「总时长」阈值。
#     根因(5层why)：
#       L1 现象: `[run-slot] watchdog 强制释放执行槽 … (运行超时 180s，线程仍存活)`
#          在 Step2 AI 拆解期间反复出现。
#       L2 直接: 本地单实例大模型（qwen2.5:7b）单次推理实测 166s→250s→378s→520s 递增，
#          任何一次调用都超过 180s。
#       L3 为何递增: Step2 默认并发 10 打在单实例 Ollama 上 → 请求排队雪崩。
#       L4 为何危险: 槽位被强制释放而线程仍在跑 → 后续步骤可与运行中步骤**并发**，
#          状态竞争（同一 project/version 双写），互斥锁形同虚设。
#       L5 根本: 挂起判定用了"总时长"这一**非因果**指标——长耗时 ≠ 挂起。
#     根治: 以「该 run 的最近进展时间戳（run_logs/executions 最大值）是否停滞」
#       判定挂起；只要还在产出进展就永不误杀。AI 重步骤给更宽的停滞预算。
_RUN_SLOT_MAX_SECONDS = 180
_RUN_SLOT_MAX_SECONDS_RUN_ALL = 1800
# AI 重步骤（Step2 拆解 / Step3 建模）——本地模型单次调用可达数分钟，
# 停滞预算放宽（这是"无进展"时长，不是"总时长"）。
_RUN_SLOT_MAX_SECONDS_AI_HEAVY = 900


def run_stall_budget_sec(step: int) -> float:
    """run 的无进展预算（秒）。判据是「进展停滞」时长，不是总时长。

    why: 长耗时 ≠ 挂起；单步要快速自愈，AI 重步骤与全流程给更宽的无进展预算。
    """
    if step == 1:
        return _RUN_SLOT_MAX_SECONDS_RUN_ALL
    if step in (2, 3):
        return _RUN_SLOT_MAX_SECONDS_AI_HEAVY
    return _RUN_SLOT_MAX_SECONDS


def _run_progress_marker(run_id):
    """返回该 run 的最近进展时间戳（run_logs 与 executions 取较新者）。

    ★ 2026-09-23: 供 watchdog 以「进展停滞」替代「总时长」判挂起（见上方根因）。
    任何异常一律返回 None（fail-open：读不到进展就当"无新进展"，交由停滞阈值兜底）。
    """
    if not run_id:
        return None
    try:
        from src.core.db_manager import get_connection

        conn = get_connection()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT MAX(t) FROM ("
                    " SELECT MAX(created_at) AS t FROM run_logs WHERE run_id=%s"
                    " UNION ALL"
                    " SELECT MAX(executed_at) AS t FROM executions WHERE run_id=%s"
                    ") _x",
                    (run_id, run_id),
                )
                row = cur.fetchone()
            if not row:
                return None
            # 连接默认返回 dict 行；按位置取会在 dict 上抛 KeyError 被吞成 None。
            return row[0] if not isinstance(row, dict) else next(iter(row.values()), None)
        finally:
            with guard("_run_progress_marker: conn.close()", log=_log):
                conn.close()
    except Exception:
        return None


def ai_inflight_deadline() -> float:
    """当前仍在飞行中的**有界等待**的最晚结束时刻（epoch 秒）；无在飞等待返回 0。

    覆盖 AI 请求与单用例超时预算（同一判据、同一登记表，见 src.ai.ai_inflight）。
    why: 看门狗据此把「等一个终将到期的操作」计入进展，避免误杀正常慢的 run。
         任一层不可用即返回 0（fail-open：退回原有停滞阈值，不改变既有兜底行为）。
    """
    try:
        from src.ai.ai_inflight import inflight_deadline

        return float(inflight_deadline() or 0.0)
    except Exception:
        return 0.0


def _slot_runner(runner):
    """在执行槽中运行 run-trigger 执行体；无论成败都在 finally 释放槽位。"""
    try:
        return runner()
    finally:
        try:
            _RUN_SLOT.release()
        except Exception:
            _log.debug("[run-slot] 释放执行槽异常(忽略)", exc_info=True)


def _dump_hung_thread_stack(t, run_id):
    """watchdog 强制释放槽位时，dump 全部线程调用栈到 ERROR 日志。

    ★ QA(2026-08-12)：run 线程常卡在 `Future.result()` 等 step 执行结果，
    真正卡死的是 ThreadPoolExecutor 的 worker 线程——只 dump runner 线程看不到根因。
    故改为 dump `sys._current_frames()` 全部线程（runner 线程置顶标记），
    下一次挂起即可直接看到 worker 卡在哪一行。
    """
    with guard("_dump_hung_thread_stack: import sys", log=_log):
        import sys
        import threading as _th
        import traceback as _tb

        _name_by_ident = {
            th.ident: th.name for th in _th.enumerate() if th.ident is not None
        }
        _items = [
            (tid, frame)
            for tid, frame in sys._current_frames().items()
            if frame is not None
        ]
        # runner 线程置顶，便于快速定位
        _items.sort(key=lambda kv: 0 if kv[0] == t.ident else 1)
        _lines = []
        for _tid, _frame in _items:
            _tname = _name_by_ident.get(_tid, "?")
            _marker = " <== runner" if _tid == t.ident else ""
            _lines.append(
                "----- tid=%s name=%s%s -----\n%s"
                % (
                    _tid,
                    _tname,
                    _marker,
                    "".join(_tb.format_stack(_frame)),
                )
            )
        _log.error(
            "[run-slot] watchdog 挂起全线程栈 run_id=%s (共 %d 线程):\n%s",
            run_id,
            len(_lines),
            "\n".join(_lines),
        )


def _spawn_runner_thread(runner, run_id=None):
    """在执行槽中启动 run-trigger 后台线程。

    Args:
        runner: 已由 _make_session_runner(...) 包装好的执行体可调用对象。
        run_id: 本次运行的 run_id（槽位满时用于清理已注册的 _active_runs 条目）。

    Returns:
        (thread, None) 成功拿到槽位并创建线程；
        (None, (resp, code)) 槽位已满，直接返回 429 响应，绝不阻塞 waitress。
    """
    if not _RUN_SLOT.acquire(blocking=False):
        # ★ 槽位满：清理已注册的 _active_runs 条目（thread=None），避免残留僵尸记录
        if run_id is not None:
            with _active_runs_lock:
                _active_runs.pop(run_id, None)
        return None, (
            jsonify(
                {
                    "ok": False,
                    "error": "当前并发执行任务过多，请稍候重试（执行槽已满）",
                    "reason": "EXECUTION_SLOT_FULL",
                }
            ),
            429,
        )
    # ★ watchdog 兜底（QA Bug2, 2026-08-12）：
    #   若 runner 线程挂起（如浏览器引擎间歇性卡死），_slot_runner 的 finally 永不执行，
    #   执行槽将永久占用 → 后续 run 全部 429（EXECUTION_SLOT_FULL）。
    #   这里在超时后强制释放槽位，避免级联 429；挂起的线程本身仍由 /api/stop 兜底。
    #   仍保持后台线程延迟执行（runner 在子线程内运行，不阻塞 waitress）。
    #   ★ 超时按 step 区分：run-all(step=1) 允许长流程，其余单步执行用 180s 快速自愈。
    try:
        with _active_runs_lock:
            _spawn_step = _active_runs.get(run_id, {}).get("step", 0)
    except Exception:
        _spawn_step = 0
    _slot_max = run_stall_budget_sec(_spawn_step)
    _slot_state = {"acquired": True, "lock": threading.Lock()}
    # ★ QA Bug2 根因修复(2026-08-12)：watchdog 不能直接 t.join()——t 由调用方
    #   （路由）在 `_active_runs[run_id]["thread"] = t` 之后才 start，
    #   join-before-start 会抛 RuntimeError 导致 watchdog 每次启动即崩溃、超时永不生效。
    #   修复：runner 线程 target 首行 set 本 Event，watchdog 先等 Event 再 join。
    #   ★ 不能把 t.start() 提前到本函数内：那会重演 KeyError 竞态——runner 的 finally
    #   pop _active_runs[run_id] 早于路由的 `_active_runs[run_id]["thread"] = t`。
    _t_started = threading.Event()

    def _runner_with_slot_release():
        _t_started.set()
        _t0 = time.time()
        try:
            return _slot_runner(runner)
        finally:
            with _slot_state["lock"]:
                _slot_state["acquired"] = False
            _log.info(
                "[run-slot] 执行槽释放 run_id=%s elapsed=%.1fs",
                run_id,
                time.time() - _t0,
            )

    t = threading.Thread(target=_runner_with_slot_release, daemon=True)

    def _slot_watchdog():
        with guard("_slot_watchdog: _t_started.wait(timeout=_slot_max)", log=_log, level=logging.ERROR):
            # 先等线程真正 start（Event 由线程 target 首行 set）；超时兜底防"永不 start"。
            _t_started.wait(timeout=_slot_max)
            # ★  根因修复：由「总时长」判据 → 「进展停滞」判据。
            #   只要 run 仍在产出进展（run_logs/executions 有新行）就永不误杀，
            #   彻底消除"本地大模型慢 → 槽位被强拆 → 步骤并发"的状态竞争。
            _last_marker = _run_progress_marker(run_id)
            _idle_since = time.time()
            while t.is_alive():
                t.join(timeout=5)
                if not t.is_alive():
                    return
                _m = _run_progress_marker(run_id)
                if _m is None:
                    # 判据读不到 = 不可判，fail-open 不杀；真挂死由「能读到时间戳但不再前进」命中。
                    continue
                if _m != _last_marker:
                    _last_marker = _m
                    _idle_since = time.time()
                    continue
                # ★ 根因修复：进展信号必须把「正在等大模型返回」算作进展。
                #   单次 AI 调用实测 92s（云端）~数分钟（本地），期间既不写日志也不写执行
                #   记录 → 原判据把正常的慢误判为挂起，强拆执行槽 → 后续步骤与仍在运行的
                #   步骤并发，同一 project/version 被双写。
                #   注意语义是「重置空闲计时」，不是「放宽预算」：拉长预算只把误杀点往后挪，
                #   一次 route() 内含云端重试（(N+1)×超时+退避）时仍会算漏。只要此刻确有
                #   请求在网上，线程就是活的、有工作量的，直接判为进展。真正的挂死由 Provider
                #   自身硬超时兜底（超时返回 → 在飞登记被 finally 清除 → 看门狗恢复生效）。
                if ai_inflight_deadline() > time.time():
                    _idle_since = time.time()
                    continue
                if (time.time() - _idle_since) <= _slot_max:
                    continue
                with _slot_state["lock"]:
                    if _slot_state["acquired"]:
                        _slot_state["acquired"] = False
                        _dump_hung_thread_stack(t, run_id)
                        with guard("_slot_watchdog: _RUN_SLOT.release()", log=_log):
                            _RUN_SLOT.release()
                            _log.error(
                                "[run-slot] watchdog 强制释放执行槽 run_id=%s "
                                "(进展停滞 %ds 无新日志/执行记录，线程仍存活；"
                                "停滞预算=%ds，释放时在飞豁免=否)",
                                run_id,
                                int(time.time() - _idle_since),
                                _slot_max,
                            )
                return

    threading.Thread(target=_slot_watchdog, daemon=True).start()
    return t, None


def _register_start_routes(run_bp):
    """将启动类路由注册到 run_bp Blueprint。"""

    @run_bp.route("/api/coverage-check", methods=["GET"])
    def api_coverage_check():
        """Step 3 强制覆盖弹窗：返回需求覆盖率和测试用例统计，供用户选择生成策略。"""
        from src.models.permission import check_admin_or_permission

        ok, err = check_admin_or_permission("execution.run", project=_current_project())
        if not ok:
            return jsonify({"ok": False, "error": err}), (
                401 if err.startswith("请先登录") else 403
            )
        from src.core.excel_handler import excel_handler
        from src.pipeline.utils.case_requirement_tracker import case_requirement_tracker
        from src.core.config_loader import config

        proj = request.args.get("project") or _current_project()
        if not proj:
            return jsonify({"ok": False, "error": "未选择项目"}), 400
        with guard("api_coverage_check: config.reload(proj, force=True)", log=_log, level=logging.WARNING):
            config.reload(proj, force=True)

        all_reqs = excel_handler.read_requirements()
        total_reqs = len([r for r in all_reqs if r.get("req_id", "").strip()])

        # ★ 全 MySQL：测试用例资产状态查 test_cases 表（_case_asset_path 是死磁盘路径已弃用）
        actual_intents = excel_handler.read_test_cases()
        actual_count = len([c for c in actual_intents if c.get("case_id")])
        case_asset_file_exists = actual_count > 0

        # 追踪器统计
        count_by_req = case_requirement_tracker.count_by_req()
        MIN_INTENTS = 3
        covered = 0
        partial = 0
        missing = 0
        for r in all_reqs:
            rid = r.get("req_id", "").strip()
            if not rid:
                continue
            cnt = count_by_req.get(rid, 0)
            if cnt >= MIN_INTENTS:
                covered += 1
            elif cnt > 0:
                partial += 1
            else:
                missing += 1

        return jsonify(
            {
                "total_requirements": total_reqs,
                "covered": covered,
                "partial": partial,
                "missing": missing,
                "min_intents_per_req": MIN_INTENTS,
                "case_asset_file_exists": case_asset_file_exists,
                "total_cases_in_file": actual_count,
                "tracker_records": len(case_requirement_tracker),
            }
        )

    @run_bp.route("/api/coverage/auto-fill", methods=["POST"])
    def api_auto_fill_gaps():
        """★ P0修复 #7: 前端"智能生成"按钮直连 auto_fill_gaps，针对覆盖缺口补生意图。"""
        from src.models.permission import check_admin_or_permission

        ok, err = check_admin_or_permission("execution.run", project=_current_project())
        if not ok:
            return jsonify({"ok": False, "error": err}), (
                401 if err.startswith("请先登录") else 403
            )

        from src.core.config_loader import config
        from src.core.excel_handler import excel_handler
        from src.extensions.coverage_tracker import coverage_tracker

        proj = (request.get_json(silent=True) or {}).get(
            "project"
        ) or _current_project()
        if not proj:
            return jsonify({"ok": False, "error": "未选择项目"}), 400
        try:
            config.reload(proj, force=True)
        except Exception as _e:
            _log.debug("import + load + import: %s", _e, exc_info=True)

        try:
            all_reqs = excel_handler.read_requirements()
            # 计算当前覆盖矩阵和缺口
            cov = coverage_tracker.compute_coverage(
                all_reqs, excel_handler.read_test_cases()
            )
            gaps = cov.get("gaps", [])
            if not gaps:
                return jsonify({"success": True, "filled": 0, "message": "无覆盖缺口"})

            # 调用 auto_fill_gaps 补生
            new_intents = coverage_tracker.auto_fill_gaps(
                gaps,
                all_reqs,
                ui_context="",
                project_context="",
            )
            filled = 0
            for ni in new_intents:
                rid = ni.get("req_id", "")
                if rid:
                    filled += excel_handler.parse_and_write_intents(rid, [ni])

            _log.info("[auto-fill] 补生 %d 条缺口意图", filled)
            return jsonify({"ok": True, "filled": filled, "total_gaps": len(gaps)})
        except Exception as e:
            _log.error("[auto-fill] 失败: %s", str(e)[:200])
            return jsonify({"ok": False, "error": str(e)[:200]}), 500

    @run_bp.route("/api/step/<int:n>", methods=["POST"])
    def api_run_step(n):
        if n < 1 or n > 5:
            return jsonify({"ok": False, "error": "无效步骤"}), 400
        # 权限校验必须作用于本次请求的项目（body 优先），不能依赖会话全局项目，
        # 否则携带 project 的直接调用会被判成"未选择项目"而误 403。
        opts = request.get_json(silent=True) or {}
        project = (opts.get("project") or _current_project() or "").strip()
        ok, err = _check_step_permission(n, project)
        if not ok:
            return jsonify({"ok": False, "error": err}), (
                401 if err.startswith("请先登录") else 403
            )
        if not project:
            return (
                jsonify(
                    {
                        "ok": False,
                        "error": "未选择项目，请先选择项目后再执行 Step %d" % n,
                    }
                ),
                400,
            )
        try:
            from src.backend.state import _set_current_project
            from src.core.config_loader import config
            from src.core.excel_handler import excel_handler

            _set_current_project(project)
            config.reload(project, force=True)
            excel_handler.reload_paths()
        except Exception as e:
            return (
                jsonify({"ok": False, "error": "项目配置加载失败: %s" % str(e)[:200]}),
                500,
            )
        if not ensure_session_key():
            return (
                jsonify({"ok": False, "error": "请先在 🧠 模型配置中完成设置后再执行"}),
                403,
            )
        # ★ [已复核-DeepSeek-V3 2026-08-16] 浏览器依赖 pre-flight：Step 1/4 需浏览器。
        #   缺依赖直接 503，绝不 spawn 线程、绝不阻塞服务线程（含 /api/status）。
        if n in (1, 4):
            _bok, _breason, _bcode = _preflight_browser_dependency()
            if not _bok:
                return (
                    jsonify(
                        {
                            "ok": False,
                            "error": _breason,
                            "reason": "BROWSER_DEPENDENCY_MISSING",
                        }
                    ),
                    _bcode,
                )
        if n in (4, 5):
            gate_ok, gate_info = _case_review_gate_status()
            if not gate_ok:
                return jsonify({"ok": False, **gate_info}), 409
            # ★ 防御：确认 test_cases 表中有数据，防止审核通过但用例被清空
            if n == 4:
                _tc_count = _count_test_cases(project)
                if _tc_count <= 0:
                    return (
                        jsonify(
                            {
                                "ok": False,
                                "error": "当前项目无测试用例，请先执行 Step 3 生成用例",
                            }
                        ),
                        400,
                    )
        with _active_runs_lock:
            # ★ F5 全局修复 [未复核-需 GLM/DeepSeek 把关]（2026-08-29）：
            #   互斥判定前先清僵尸条目（死线程/超 30min 看门狗），避免"线程已死
            #   但条目未 pop → 同项目 Step2-5 永久 409"。sweep 必须在锁内执行，
            #   避免与登记竞争；conflicting 计算再跳过死线程条目（双保险）。
            _sweep_stale_runs(project)
            # ★ A1 改造：互斥锁按 project 过滤，不同项目可并行执行
            #   原逻辑：Step 2-5 全局互斥（共享 Excel），Step 4 全局互斥（共享浏览器）
            #   新逻辑：同项目内 Step 2-5 互斥、Step 4 互斥；不同项目可并行
            if n >= 2:
                conflicting = [
                    str(r["step"])
                    for r in _active_runs.values()
                    if r["step"] >= 2
                    and r.get("project") == project
                    and not (
                        r.get("thread") is not None
                        and not r["thread"].is_alive()
                    )
                ]
                if conflicting:
                    return (
                        jsonify(
                            {
                                "ok": False,
                                "error": f"Step {','.join(conflicting)} 正在执行中，Step 2-5 不可并行（共享 Excel）",
                            }
                        ),
                        409,
                    )
            if n == 1:
                if any(
                    r["step"] == 1
                    and r.get("project") == project
                    and not (
                        r.get("thread") is not None
                        and not r["thread"].is_alive()
                    )
                    for r in _active_runs.values()
                ):
                    return jsonify({"ok": False, "error": "Step 1 正在执行中"}), 409
            if n == 4:
                if any(
                    r["step"] == 4
                    and r.get("project") == project
                    and not (
                        r.get("thread") is not None
                        and not r["thread"].is_alive()
                    )
                    for r in _active_runs.values()
                ):
                    return (
                        jsonify(
                            {
                                "ok": False,
                                "error": "Step 4 正在执行中，不可并行（共享浏览器/Playwright）",
                            }
                        ),
                        409,
                    )
            # ★ 清旧日志必须在「确认拿到执行槽位」之后：run_id 是确定性的
            #   ({project}__{version_label})，冲突判定前清会删掉正在运行的那次 run
            #   的进度日志（被拒的触发把别人的进度打没）。
            run_id = _gen_run_id_and_clear_old_logs(project, n)
            # ★ v12: 启动前清除残留的 force_stop 标记
            _clear_force_stop_for_project(project)
            _active_runs[run_id] = {
                "thread": None,
                "force_stop": False,
                "step": n,
                "project": project,
                "start_ts": time.time(),
            }
        figma_force = opts.get("figma_force_refresh", False)
        force_regen = opts.get("force_regen", False)
        resume = opts.get("resume", False)
        quick = opts.get("quick", False)
        skip_sources = opts.get("skip_sources", [])  # ★ 前端勾选反选的数据源列表
        force_overwrite = opts.get(
            "force_overwrite", False
        )  # ★ 强制覆盖所有 Step1 数据
        force_step2 = opts.get("force_step2", False)  # ★ 强制重拆解 Step2

        step4_options = opts if n == 4 else None
        t, _rej = _spawn_runner_thread(
            _make_session_runner(
                _run_step,
                StepRunOptions(
                    step_num=n,
                    run_id=run_id,
                    step4_options=step4_options,
                    figma_force_refresh=figma_force,
                    force_regen=force_regen,
                    resume=resume,
                    quick=quick,
                    skip_sources=skip_sources,
                    force_overwrite=force_overwrite,
                    force_step2=force_step2,
                ),
            ),
            run_id=run_id,
        )
        if t is None:
            return _rej
        with _active_runs_lock:
            # ★ 修复 BUG-10：Thread 创建在 lock 内完成，防止 status.py 误判僵尸线程
            #    （之前 thread 字段在 L174 才设置，status 轮询可能在中间时刻读到 None）
            #    如果 run_id 已被并发请求覆盖/清理，跳过
            if run_id in _active_runs:
                _active_runs[run_id]["thread"] = t
        t.start()
        return jsonify({"ok": True, "run_id": run_id, "message": f"Step {n} 已启动"})

    @run_bp.route("/api/step4-run", methods=["POST"])
    def api_step4_run():
        opts = request.get_json(silent=True) or {}
        _warn_unsupported_step4_opts(opts)  # ★ [B6 配套] 不消费的配置项显式告警
        project = (opts.get("project") or _current_project() or "").strip()
        if not project:
            return (
                jsonify(
                    {"ok": False, "error": "未选择项目，请先选择项目后再执行 Step 4"}
                ),
                400,
            )
        # ★ 门禁顺序修复（与 api_step4_run_single 对齐）：先建立项目上下文，
        #   再做任何依赖项目的权限校验 / 审核门禁，避免直接 POST 时读到空全局 project 误报 409。
        try:
            from src.backend.state import _set_current_project
            from src.core.config_loader import config
            from src.core.excel_handler import excel_handler

            _set_current_project(project)
            config.reload(project, force=True)
            excel_handler.reload_paths()
        except Exception as e:
            return (
                jsonify({"ok": False, "error": "项目配置加载失败: %s" % str(e)[:200]}),
                500,
            )
        ok, err = _check_step_permission(4)
        if not ok:
            return jsonify({"ok": False, "error": err}), (
                401 if err.startswith("请先登录") else 403
            )
        if not ensure_session_key():
            return (
                jsonify({"ok": False, "error": "请先在 🧠 模型配置中完成设置后再执行"}),
                403,
            )
        # ★ [已复核-DeepSeek-V3 2026-08-16] 浏览器依赖 pre-flight：Step4 需浏览器。
        #   缺依赖直接 503，绝不 spawn 线程、绝不阻塞服务线程（含 /api/status）。
        _bok, _breason, _bcode = _preflight_browser_dependency()
        if not _bok:
            return (
                jsonify(
                    {
                        "ok": False,
                        "error": _breason,
                        "reason": "BROWSER_DEPENDENCY_MISSING",
                    }
                ),
                _bcode,
            )
        gate_ok, gate_info = _case_review_gate_status()
        if not gate_ok:
            return jsonify({"ok": False, **gate_info}), 409
        # ★ F-05 (2026-08-04): 一条一例红线 — 批量入口下线（PRD Q1 推荐）。
        #   根因(3层): ①不传 case_ids 时 step4_executor 缺省取全部 pending 批量跑，
        #   违反 AGENTS.md v5.2「一条一例」铁律；②批量执行无「逐条确认中断点」；
        #   ③守卫须加在入口层（本处）+ 执行器汇合点（双保险，覆盖 5 条产生路径）。
        #   修复: 未显式指定单条/失败重跑/版本/文件时，拒绝启动并引导走 step4-run-single。
        _bulk_keys = [
            k
            for k in (
                "case_ids",
                "case_id",
                "version_ids",
                "case_asset_files",
                "case_asset_file",
                "retry_failed",
            )
            if opts.get(k)
        ]
        if not _bulk_keys:
            return (
                jsonify(
                    {
                        "ok": False,
                        "error": "一条一例红线：Step4 批量自动执行已下线，请通过 /api/step4-run-single 逐条触发（AGENTS.md v5.2）",
                        "reason": "ONE_CASE_AT_A_TIME",
                    }
                ),
                400,
            )
        # ★ 防御：确认 test_cases 表中有数据
        _tc_count = _count_test_cases(project)
        if _tc_count <= 0:
            return (
                jsonify(
                    {
                        "ok": False,
                        "error": "当前项目无测试用例，请先执行 Step 3 生成用例",
                    }
                ),
                400,
            )
        with _active_runs_lock:
            # ★ F5 全局修复 [未复核-需 GLM/DeepSeek 把关]：先清僵尸再判定（见 api_run_step）
            _sweep_stale_runs(project)
            # ★ A1 改造：同项目内 Step 2-5 互斥；不同项目可并行
            conflicting = [
                str(r["step"])
                for r in _active_runs.values()
                if r["step"] >= 2
                and r.get("project") == project
                and not (
                    r.get("thread") is not None and not r["thread"].is_alive()
                )
            ]
            if conflicting:
                return (
                    jsonify(
                        {
                            "ok": False,
                            "error": f"Step {','.join(conflicting)} 正在执行中，请等待完成",
                        }
                    ),
                    409,
                )
            # ★ 清旧日志必须在「确认拿到执行槽位」之后：run_id 是确定性的
            #   ({project}__{version_label})，若在冲突判定前清，被拒的触发会删掉
            #   正在运行的那次 run 的进度日志（同文件另两个入口即此顺序）。
            run_id = _gen_run_id_and_clear_old_logs(project, 4)
            # ★ v12: 启动前清除残留的 force_stop 标记
            _clear_force_stop_for_project(project)
            _active_runs[run_id] = {
                "thread": None,
                "force_stop": False,
                "step": 4,
                "project": project,
                "start_ts": time.time(),
            }
        t, _rej = _spawn_runner_thread(
            _make_session_runner(
                _run_step, StepRunOptions(step_num=4, run_id=run_id, step4_options=opts)
            ),
            run_id=run_id,
        )
        if t is None:
            return _rej
        with _active_runs_lock:
            if run_id in _active_runs:
                _active_runs[run_id]["thread"] = t
        t.start()
        return jsonify({"ok": True, "run_id": run_id, "message": "Step 4 已启动"})

    @run_bp.route("/api/step4-run-single", methods=["POST"])
    def api_step4_run_single():
        """单条用例执行：只执行指定 case_id 的用例，不执行全部。"""
        opts = request.get_json(silent=True) or {}
        _warn_unsupported_step4_opts(opts)  # ★ [B6 配套] 不消费的配置项显式告警
        project = (opts.get("project") or _current_project() or "").strip()
        case_id = opts.get("case_id", "").strip()
        if not project:
            return jsonify({"ok": False, "error": "未选择项目"}), 400
        if not case_id:
            return jsonify({"ok": False, "error": "缺少 case_id 参数"}), 400
        # ★ 门禁顺序修复：先建立项目上下文（_set_current_project 写入全局 project），
        #   再做任何依赖项目的权限校验 / 审核门禁，避免直接 POST（body 带 project）时
        #   _case_review_gate_status() 读到空全局 project 误报 409「尚无可审核的测试用例」。
        try:
            from src.backend.state import _set_current_project
            from src.core.config_loader import config
            from src.core.excel_handler import excel_handler

            _set_current_project(project)
            config.reload(project, force=True)
            excel_handler.reload_paths()
        except Exception as e:
            return (
                jsonify({"ok": False, "error": "项目配置加载失败: %s" % str(e)[:200]}),
                500,
            )
        ok, err = _check_step_permission(4)
        if not ok:
            return jsonify({"ok": False, "error": err}), (
                401 if err.startswith("请先登录") else 403
            )
        if not ensure_session_key():
            return (
                jsonify({"ok": False, "error": "请先在模型配置中完成设置后再执行"}),
                403,
            )
        # ★ [已复核-DeepSeek-V3 2026-08-16] 浏览器依赖 pre-flight：Step4 需浏览器。
        #   缺依赖直接 503，绝不 spawn 线程、绝不阻塞服务线程（含 /api/status）。
        _bok, _breason, _bcode = _preflight_browser_dependency()
        if not _bok:
            return (
                jsonify(
                    {
                        "ok": False,
                        "error": _breason,
                        "reason": "BROWSER_DEPENDENCY_MISSING",
                    }
                ),
                _bcode,
            )
        gate_ok, gate_info = _case_review_gate_status()
        if not gate_ok:
            return jsonify({"ok": False, **gate_info}), 409
        with _active_runs_lock:
            # ★ F5 全局修复 [未复核-需 GLM/DeepSeek 把关]：先清僵尸再判定（见 api_run_step）
            _sweep_stale_runs(project)
            conflicting = [
                str(r["step"])
                for r in _active_runs.values()
                if r["step"] >= 2
                and r.get("project") == project
                and not (
                    r.get("thread") is not None and not r["thread"].is_alive()
                )
            ]
            if conflicting:
                return (
                    jsonify(
                        {
                            "ok": False,
                            "error": f"Step {','.join(conflicting)} 正在执行中，请等待完成",
                        }
                    ),
                    409,
                )
            # ★ 清旧日志必须在「确认拿到执行槽位」之后（run_id 确定性 → 提前清会
            #   删掉正在运行的那次 run 的进度日志）。与同文件其余入口同序。
            run_id = _gen_run_id_and_clear_old_logs(project, 4)
            # ★ v12: 启动前清除残留的 force_stop 标记
            _clear_force_stop_for_project(project)
            _active_runs[run_id] = {
                "thread": None,
                "force_stop": False,
                "step": 4,
                "project": project,
                "start_ts": time.time(),
            }
        step4_opts = {"case_ids": [case_id]}
        step4_opts.update({k: v for k, v in opts.items() if k != "case_id"})
        t, _rej = _spawn_runner_thread(
            _make_session_runner(
                _run_step,
                StepRunOptions(step_num=4, run_id=run_id, step4_options=step4_opts),
            ),
            run_id=run_id,
        )
        if t is None:
            return _rej
        with _active_runs_lock:
            if run_id in _active_runs:
                _active_runs[run_id]["thread"] = t
        t.start()
        return jsonify(
            {
                "ok": True,
                "run_id": run_id,
                "case_id": case_id,
                "message": "单条用例已启动",
            }
        )

    @run_bp.route("/api/run-all", methods=["POST"])
    def api_run_all():
        # ★ 2026-08-29 [已拍板] 用户铁律：测试禁用一键执行（run-all）。
        #   流水线必须 Step1→5 逐步骤执行；唯一入口 = 引擎前端（Web 控制台）。
        #   保留路由注册（避免前端/旧客户端 404 报错），但一律返回 403，绝不启动任何线程。
        _log.warning("[api_run_all] 已禁用：一键执行 run-all 被用户铁律禁止（须 Step1→5 逐步骤）")
        return jsonify(
            {
                "ok": False,
                "error": "run-all 已禁用：按规范必须 Step1→5 逐步骤执行",
                "degraded": False,
            }
        ), 403

    @run_bp.route("/api/retry-failed", methods=["POST"])
    def api_retry_failed():
        from src.models.permission import can_execute_in_project

        _cur_project = _current_project()
        ok, err = can_execute_in_project(None, _cur_project)
        if not ok:
            return jsonify({"ok": False, "error": err}), (
                401 if err.startswith("请先登录") else 403
            )
        # ★ A1 改造：仅检查当前项目是否在运行（排除暂停状态）
        # ★ F5 全局修复 [未复核-需 GLM/DeepSeek 把关]：互斥判定前先清僵尸条目
        #   （_is_running 读到清理后的状态，避免死线程条目永久 409）
        with _active_runs_lock:
            _sweep_stale_runs(_cur_project)
        if _is_running(_cur_project) and _get_paused_step(_cur_project) <= 0:
            return jsonify({"ok": False, "error": "有任务正在执行中，请等待完成"}), 409
        run_id = _gen_run_id_and_clear_old_logs(_cur_project, 4)
        with _active_runs_lock:
            # ★ v12: 启动前清除残留的 force_stop 标记
            _clear_force_stop_for_project(_cur_project)
            _active_runs[run_id] = {
                "thread": None,
                "force_stop": False,
                "step": 4,
                "project": _cur_project,
                "start_ts": time.time(),
            }
        t, _rej = _spawn_runner_thread(
            _make_session_runner(_retry_failed, run_id), run_id=run_id
        )
        if t is None:
            return _rej
        with _active_runs_lock:
            if run_id in _active_runs:
                _active_runs[run_id]["thread"] = t
        t.start()
        return jsonify({"ok": True, "run_id": run_id, "message": "重跑已启动"})

    @run_bp.route("/api/retry-failed-with-regen", methods=["POST"])
    def api_retry_failed_with_regen():
        """★ E4 (2026-07-10): 失败用例增量重生 + 重跑。
        上传补充文档后，先重新生成失败用例（用最新 RAG），再执行。"""
        from src.models.permission import can_execute_in_project

        _cur_project = _current_project()
        ok, err = can_execute_in_project(None, _cur_project)
        if not ok:
            return jsonify({"ok": False, "error": err}), (
                401 if err.startswith("请先登录") else 403
            )
        # ★ A1 改造：仅检查当前项目是否在运行（排除暂停状态）
        # ★ F5 全局修复 [未复核-需 GLM/DeepSeek 把关]：互斥判定前先清僵尸条目
        with _active_runs_lock:
            _sweep_stale_runs(_cur_project)
        if _is_running(_cur_project) and _get_paused_step(_cur_project) <= 0:
            return jsonify({"ok": False, "error": "有任务正在执行中，请等待完成"}), 409
        run_id = _gen_run_id_and_clear_old_logs(_cur_project, 4)
        with _active_runs_lock:
            # ★ v12: 启动前清除残留的 force_stop 标记
            _clear_force_stop_for_project(_cur_project)
            _active_runs[run_id] = {
                "thread": None,
                "force_stop": False,
                "step": 4,
                "project": _cur_project,
                "start_ts": time.time(),
            }
        from src.backend.executor._retry import retry_failed_with_regen

        t, _rej = _spawn_runner_thread(
            _make_session_runner(retry_failed_with_regen, run_id), run_id=run_id
        )
        if t is None:
            return _rej
        with _active_runs_lock:
            if run_id in _active_runs:
                _active_runs[run_id]["thread"] = t
        t.start()
        return jsonify({"ok": True, "run_id": run_id, "message": "增量重生+重跑已启动"})
