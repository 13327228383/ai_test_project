# -*- coding: utf-8 -*-
"""
_retry_failed —— 失败测试用例重跑。
从原 executor.py 提取，逻辑保持不变。
"""

from pathlib import Path
from src.core.guarded import guard
import time
import logging

_log = logging.getLogger(__name__)

ROOT = (
    Path(__file__).resolve().parent.parent.parent.parent
)  # src/backend/executor/../../../ = 项目根

from src.backend.state import (
    _current_project,
    _thread_run_id,
    _set_running,
    _set_force_stop,
    _set_paused_step,
    _get_paused_step,
    _active_runs,
    _active_runs_lock,
    _run_progress,
    _run_progress_lock,
    _run_sse_queues,
    _run_sse_queues_lock,
    _update_progress,
)
from src.backend.helpers import _confirm_figma_fetch


def _retry_failed(run_id):
    """失败测试用例重跑。"""
    _thread_run_id.value = run_id
    _set_running(True)
    with guard("_retry_failed: from src.pipeline.utils.pause_contro", log=_log, level=logging.WARNING):
        from src.pipeline.utils.pause_controller import pause_controller

        pause_controller.resume()
    try:
        from src.core.excel_handler import excel_handler

        cand = excel_handler.get_failed_intents()
        if not cand:
            _log.info("[重跑] 无失败测试用例")
            return
        _log.info(f"[重跑] {len(cand)} 条失败测试用例")
        from src.pipeline.steps.step4.browser_core import browser_executor

        browser_executor.run_test_cases(cand, quick=False)
        _log.info("[重跑] 完成")
    except Exception as e:
        import traceback

        _log.info(f"##HTML:##<span style='color:#ef4444'>✗ 重跑异常: {e}</span>")
        traceback.print_exc()
    finally:
        with _active_runs_lock:
            _active_runs.pop(run_id, None)
            still_active = len(_active_runs) > 0
        with _run_sse_queues_lock:
            _run_sse_queues.pop(run_id, None)
        _thread_run_id.value = None
        if not still_active:
            _set_running(False)
        # ★ AI 健康度指标持久化（重跑路径）
        try:
            from src.extensions.metrics_collector import metrics_collector

            metrics_collector.flush_run(run_id)
        except Exception as e:
            import traceback

            _log.info("    [Metrics] ❌ flush_run(重跑) 异常: %s", e)
            traceback.print_exc()
            # ★ 通知前端：存储错误 + SSE 推送
            with guard("_retry_failed: import json as _json", log=_log, level=logging.WARNING):
                import json as _json
                from src.backend.state import (
                    _health_flush_errors,
                    _health_flush_errors_lock,
                )

                err_msg = str(e)[:200]
                with _health_flush_errors_lock:
                    _health_flush_errors[run_id] = err_msg
                from src.backend.log_capture import _broadcast_log

                _broadcast_log(
                    _json.dumps(
                        {
                            "event": "health_flush_error",
                            "run_id": run_id,
                            "error": err_msg,
                        }
                    ),
                    run_id=run_id,
                )


def retry_failed_with_regen(run_id):
    """★ E4 (2026-07-10): 失败用例增量重生 + 重跑。

    场景：用户上传补充文档 → Step1/Step2 更新 requirements+RAG → 本函数：
      1. 读取失败用例
      2. 批量重新生成（用最新 requirements+RAG）
      3. 执行重新生成的用例

    与 _retry_failed 的区别：前者只重新执行旧 actions，本函数先重新生成再执行。
    """
    _thread_run_id.value = run_id
    _set_running(True)
    with guard("retry_failed_with_regen: from src.pipeline.utils.pause_contro", log=_log, level=logging.WARNING):
        from src.pipeline.utils.pause_controller import pause_controller

        pause_controller.resume()
    try:
        from src.core.excel_handler import excel_handler

        cand = excel_handler.get_failed_intents()
        if not cand:
            _log.info("[增量重生] 无失败测试用例")
            return

        # ★ E3: 批量重新生成失败用例
        case_ids = [c.get("case_id", "") for c in cand if c.get("case_id")]
        _log.info("[增量重生] %d 条失败用例，开始批量重新生成...", len(case_ids))
        from src.backend.routes.case_review._remodel import batch_remodel_intents

        regen_result = batch_remodel_intents(case_ids)
        _log.info(
            "[增量重生] 重生完成: %d 成功, %d 失败",
            regen_result.get("success", 0),
            regen_result.get("failed", 0),
        )
        # ★ H4-6: 记录增量重生指标
        with guard("retry_failed_with_regen: from src.backend.state import _curre", log=_log):
            from src.backend.state import _current_project
            from src.core.config_loader import config
            from src.extensions.metrics_collector import metrics_collector

            _proj = _current_project() or config.project_name
            metrics_collector.record_incremental_regen(
                _proj,
                run_id,
                total=len(case_ids),
                success=regen_result.get("success", 0),
                failed=regen_result.get("failed", 0),
            )

        # ★ 重新读取重生后的用例（actions/checks 已更新）
        regen_case_ids = [
            d["case_id"] for d in regen_result.get("details", []) if d.get("ok")
        ]
        if not regen_case_ids:
            _log.info("[增量重生] 无用例重生成功，跳过执行")
            return

        # 从 case_store 读取重生后的用例
        from src.pipeline.utils.case_store import case_store

        all_intents = case_store.list_intents()
        regen_cand = [c for c in all_intents if c.get("case_id") in regen_case_ids]
        if not regen_cand:
            _log.info("[增量重生] 重生用例读取失败，跳过执行")
            return

        _log.info("[增量重生] 开始执行 %d 条重生用例...", len(regen_cand))
        from src.pipeline.steps.step4.browser_core import browser_executor

        browser_executor.run_test_cases(regen_cand, quick=False)
        _log.info("[增量重生] 执行完成")
    except Exception as e:
        import traceback

        _log.info(f"##HTML:##<span style='color:#ef4444'>✗ 增量重生异常: {e}</span>")
        traceback.print_exc()
    finally:
        with _active_runs_lock:
            _active_runs.pop(run_id, None)
            still_active = len(_active_runs) > 0
        with _run_sse_queues_lock:
            _run_sse_queues.pop(run_id, None)
        _thread_run_id.value = None
        if not still_active:
            _set_running(False)
