# -*- coding: utf-8 -*-
"""
流式管道调度器 (StreamingPipelineScheduler) — v4.0
==================================================
★ v4.0: Step1 阻塞 → Step2/3/4/5 worker 并行启动，通过 DB 轮询消费上游产出。

核心差异（vs sequential PipelineScheduler）：
- sequential: Step1→Step2→Step3→Step4→Step5 严格串行，每步等上一步全完
- streaming:  Step1 完成后，Step2-5 并行启动，各 worker 检测到上游数据即开始处理

设计原则：
- 复用现有 Step 函数（step1_fetch_data / step2_breakdown / step3_model_cases / step4_execute / generate_report）
- 各 worker 独立线程，通过 MySQL 轮询数据可用性
- Step4 浏览器并发数由 semaphore 控制（默认 2）
- 轮询间隔递增（无数据时 3s→10s→30s 上限）
"""

import time
from src.core.guarded import guard
import uuid
import threading
import logging
from typing import Any

_log = logging.getLogger(__name__)

# Step4 浏览器并发上限
_MAX_BROWSER_WORKERS = 2
_browser_semaphore = threading.Semaphore(_MAX_BROWSER_WORKERS)

# 轮询间隔参数
_POLL_INITIAL_SEC = 3.0  # 初始轮询间隔
_POLL_MAX_SEC = 30.0  # 最大轮询间隔
_POLL_BACKOFF_FACTOR = 2.0  # 每次无数据时间隔翻倍


class StreamingPipelineScheduler:
    """流式管道调度器。"""

    def __init__(self, project_name: str = "sample_project", user_id: str = None):
        self._project_name = project_name
        self._user_id = user_id
        self._start_time = None
        self._force_stop_check = None
        self._progress_callback = None
        self.run_id = None

    def run(
        self,
        from_step: int = 1,
        end_step: int = 5,
        quick: bool = False,
        step4_options: dict = None,
        figma_force_refresh: bool = False,
        force_regen: bool = False,
        resume: bool = False,
        force_overwrite: bool = False,
        force_step2: bool = False,
        skip_sources: list = None,
    ) -> dict[str, Any]:
        """流式管道入口。Step1 先跑完，然后 Step2-5 并行。"""
        from src.core.config_loader import config as _cfg

        if self._project_name:
            _cfg.reload(self._project_name)
        self._start_time = time.time()

        if not self.run_id:
            # ★ 确定性 run_id：项目+版本（与 _start.py / scheduler.py 一致）
            from src.core.versioning import current_run_id

            self.run_id = current_run_id(self._project_name)
        run_id = self.run_id

        # ★ 线程上下文捕获（同 sequential scheduler 模式）
        from src.core.config_loader import (
            get_current_user,
            set_current_user,
            get_session_key,
            set_session_key,
        )
        from src.backend.state import (
            _current_project,
            _set_current_project,
            _thread_run_id,
            _current_version,
            _set_current_version,
        )

        _ctx_user = get_current_user()
        _ctx_key = get_session_key()
        _ctx_project = _current_project()
        _ctx_version = _current_version()  # ★ 补传 version

        def _restore_ctx():
            """在 worker 线程中恢复上下文。"""
            if _ctx_user:
                set_current_user(_ctx_user)
            if _ctx_key:
                set_session_key(_ctx_key)
            if _ctx_project:
                _set_current_project(_ctx_project)
                with guard("_restore_ctx: _cfg.reload(_ctx_project)", log=_log, level=logging.WARNING):
                    _cfg.reload(_ctx_project)
            if _ctx_version:
                _set_current_version(_ctx_version)  # ★ 恢复版本标签
            if run_id:
                _thread_run_id.value = run_id
            # ★ 补传 _current_log_file：threading.local() 不跨线程，否则 MySQL 持久化被跳过
            import src.backend.log_capture as _lc

            _ctx_log_file = getattr(_lc._current_log_file, "value", None)
            if _ctx_log_file:
                _lc._current_log_file.value = _ctx_log_file

        def _clear_ctx():
            """清除线程上下文。"""
            try:
                _thread_run_id.value = None
            except Exception as _e:
                _log.debug(
                    "[streaming_scheduler] 清除线程上下文失败: %s", _e, exc_info=True
                )
            try:
                import src.backend.log_capture as _lc

                _lc._current_log_file.value = None
            except Exception as _e:
                _log.debug(
                    "[streaming_scheduler] 清除日志文件上下文异常: %s",
                    _e,
                    exc_info=True,
                )

        _log.info("[Step1] " + "=" * 50)
        _log.info("[Step1] 拉取数据源...")
        _log.info("[Step1] " + "=" * 50)
        _log.info(
            "[Step1] 流水线启动(streaming) 项目=%s run_id=%s",
            self._project_name,
            run_id,
        )

        results = {"project": self._project_name, "run_id": run_id, "steps": {}}

        # ── Phase 1: Step1 阻塞执行 ──
        if from_step <= 1 <= end_step:
            _log.info("[streaming] Phase 1: Step1 拉取数据源...")
            try:
                _restore_ctx()
                from src.pipeline.step1 import step1_fetch_data

                step1_result = step1_fetch_data(
                    figma_force_refresh=figma_force_refresh,
                    run_id=run_id,
                    force_overwrite=force_overwrite,
                    skip_sources=skip_sources or [],
                )
                results["steps"][1] = step1_result
                _clear_ctx()
                if not step1_result or not step1_result.get("success", True):
                    _log.error("[Step1] 失败，流式管道中止")
                    results["steps"][1] = step1_result or {
                        "success": False,
                        "error": "Step1 失败",
                    }
                    return results

                # ★ RAG 已在 step1_fetch_data() 内部增量构建，此处不再重复
                _log.info("[Step1] 完成（RAG 已在内部构建），启动 Step2-5 worker...")
            except Exception as e:
                _log.error("[streaming] Step1 异常: %s", e, exc_info=True)
                results["steps"][1] = {"success": False, "error": str(e)}
                return results

        # ── Phase 2: Step2-5 并行 worker ──
        step_results = {}
        step_errors = {}
        step_done = threading.Event()
        step_lock = threading.Lock()

        def _worker(step_num: int, step_func, step_name: str):
            """通用 worker：等上游数据就绪 → 执行 → 标记完成。"""
            _restore_ctx()
            # ★ 每个 worker 使用自己 step 的日志文件，确保 MySQL run_logs.step_num 正确
            with guard("_worker: import src.backend.log_capture as _l", log=_log):
                import src.backend.log_capture as _lc
                from src.backend.log_persistence import begin_run_log

                _step_log_file = begin_run_log(run_id, f"step{step_num}")
                _lc._current_log_file.value = _step_log_file
            try:
                _log.info("[Step%d] Worker 启动，等待上游数据...", step_num)
                # 轮询等待上游数据
                self._poll_upstream_data(step_num)

                # 检查是否被强制停止
                if self._force_stop_check and self._force_stop_check():
                    _log.warning("[Step%d] Worker 收到停止信号", step_num)
                    with step_lock:
                        step_results[step_num] = {"status": "stopped"}
                    return

                _log.info("[Step%d] 开始执行 (%s)", step_num, step_name)
                # ★ 步骤开始：通知前端 step 进入 active（done=0, total=-1 表示执行中但无具体进度）
                if self._progress_callback:
                    with guard("_worker: self._progress_callback( step_num, s", log=_log):
                        self._progress_callback(
                            step_num, step_name, 0, -1, from_step, end_step
                        )
                t0 = time.time()
                result = step_func()
                elapsed = time.time() - t0
                if isinstance(result, dict):
                    result["_elapsed_seconds"] = round(elapsed, 1)

                with step_lock:
                    step_results[step_num] = result
                _log.info("[Step%d] 完成 (%.1fs)", step_num, elapsed)
                # ★ 步骤成功完成：通知前端 step 已完成（done=total，前端据此标记 completed）
                if self._progress_callback:
                    with guard("_worker: _step_total = 0", log=_log):
                        _step_total = 0
                        if isinstance(result, dict):
                            _step_total = result.get("count", 0) or 0
                        self._progress_callback(
                            step_num,
                            step_name,
                            _step_total,
                            _step_total if _step_total > 0 else -1,
                            from_step,
                            end_step,
                        )

            except Exception as e:
                _log.error("[Step%d] 异常: %s", step_num, e, exc_info=True)
                with step_lock:
                    step_errors[step_num] = str(e)
                    step_results[step_num] = {"success": False, "error": str(e)}
            finally:
                _clear_ctx()

        # 构建 worker 列表
        workers = []
        step_defs = [
            (2, self._make_step2_func(quick, force_step2, run_id), "AI 需求拆解"),
            (3, self._make_step3_func(quick, force_regen, run_id), "测试用例建模"),
            (4, self._make_step4_func(quick, step4_options, run_id), "用例执行验证"),
            (5, self._make_step5_func(run_id), "生成报告"),
        ]

        for sn, sf, sn_label in step_defs:
            if from_step <= sn <= end_step:
                t = threading.Thread(
                    target=_worker,
                    args=(sn, sf, sn_label),
                    daemon=True,
                    name=f"streaming-step{sn}",
                )
                workers.append(t)
                t.start()

        # 等待所有 worker 完成
        for t in workers:
            t.join()

        # 汇总结果
        for sn in range(2, 6):
            if sn in step_results:
                results["steps"][sn] = step_results[sn]
            elif sn in step_errors:
                results["steps"][sn] = {"success": False, "error": step_errors[sn]}
            elif from_step <= sn <= end_step:
                results["steps"][sn] = {"status": "skipped"}

        elapsed = time.time() - self._start_time
        results["elapsed_seconds"] = elapsed
        _log.info("[streaming] 全流程完成 | 耗时: %.1f 秒", elapsed)
        return results

    # ── Step 函数工厂（复用现有实现） ──

    def _make_step2_func(self, quick: bool, force_step2: bool, run_id: str):
        """Step2: 需求拆解（委托到现有实现）。"""

        def _fn():
            from src.pipeline.step2 import step2_breakdown

            ms = _MockScheduler(self._project_name, self._user_id)
            ms._force_stop_check = self._force_stop_check
            ms._progress_callback = self._progress_callback
            return step2_breakdown(ms, quick, force_step2=force_step2, run_id=run_id)

        return _fn

    def _make_step3_func(self, quick: bool, force_regen: bool, run_id: str):
        """Step3: 测试用例建模（委托到现有实现）。"""

        def _fn():
            from src.pipeline.step3 import step3_model_cases

            ms = _MockScheduler(self._project_name, self._user_id)
            ms._force_stop_check = self._force_stop_check
            ms._progress_callback = self._progress_callback
            return step3_model_cases(ms, quick, force_regen, run_id=run_id)

        return _fn

    def _make_step4_func(self, quick: bool, step4_options: dict, run_id: str):
        """Step4: 用例执行（委托到现有实现，带浏览器并发控制）。"""

        def _fn():
            with _browser_semaphore:
                from src.pipeline.step4_executor import step4_execute
                from src.pipeline.step1_context_loader import load_step1_contexts
                from src.pipeline.api_only_executor import (
                    execute_api_only_intents as _exec_api_only,
                )

                _step1_ctx = load_step1_contexts(self._project_name)
                return step4_execute(
                    quick=quick,
                    run_id=run_id,
                    step4_options=step4_options or {},
                    user_id=self._user_id,
                    force_stop_check=self._force_stop_check,
                    progress_callback=self._progress_callback,
                    execute_api_only_intents=_exec_api_only,
                )

        return _fn

    def _make_step5_func(self, run_id: str):
        """Step5: 报告生成（委托到现有实现，含 HTML 交付物）。"""

        def _fn():
            from src.pipeline.steps.step5.report_generator import generate_report

            # 与 scheduler 同一口径：Step5 必须产出交付标准要求的 HTML 报告，
            #   否则 html_report 产物在流水线内恒不存在（能力存在但不可达）。
            return generate_report(self._project_name, run_id=run_id, generate_html=True)

        return _fn

    # ── DB 轮询 ──

    def _poll_upstream_data(self, step_num: int):
        """轮询 DB 等待上游数据就绪。无数据时递增间隔。"""
        poll_sec = _POLL_INITIAL_SEC
        while True:
            if self._force_stop_check and self._force_stop_check():
                return
            if self._has_upstream_data(step_num):
                return
            _log.debug(
                "[streaming] Step%d 上游暂无数据，%ds 后重试...",
                step_num,
                int(poll_sec),
            )
            time.sleep(poll_sec)
            poll_sec = min(poll_sec * _POLL_BACKOFF_FACTOR, _POLL_MAX_SEC)

    def _has_upstream_data(self, step_num: int) -> bool:
        """检查上游 Step 是否已产出数据。"""
        from src.core.db_manager import get_connection
        from src.core.config_loader import config

        project = config.project_name or self._project_name
        from src.core.versioning import current_version_id

        version_id = current_version_id(project)

        conn = None
        try:
            conn = get_connection()
            if step_num == 2:
                # Step2 依赖 source_documents
                row = conn.execute(
                    "SELECT COUNT(*) AS cnt FROM source_documents WHERE project=%s",
                    (project,),
                ).fetchone()
                return bool(row and row.get("cnt", 0) > 0)
            elif step_num == 3:
                # Step3 依赖 requirements（COUNT>0 表示 Step2 已写入）
                row = conn.execute(
                    "SELECT COUNT(*) AS cnt FROM requirements WHERE project=%s AND version_id=%s",
                    (project, version_id),
                ).fetchone()
                return bool(row and row.get("cnt", 0) > 0)
            elif step_num == 4:
                # Step4 依赖 test_cases（COUNT>0 表示 Step3 已写入）
                row = conn.execute(
                    "SELECT COUNT(*) AS cnt FROM test_cases WHERE project=%s AND version_id=%s",
                    (project, version_id),
                ).fetchone()
                return bool(row and row.get("cnt", 0) > 0)
            elif step_num == 5:
                # Step5 依赖 executions
                row = conn.execute(
                    "SELECT COUNT(*) AS cnt FROM executions WHERE project=%s",
                    (project,),
                ).fetchone()
                return bool(row and row.get("cnt", 0) > 0)
            return True
        except Exception as e:
            _log.debug("[streaming] _has_upstream_data 查询失败: %s", str(e)[:80])
            return False
        finally:
            if conn:
                try:
                    conn.close()
                except Exception as _e:
                    _log.debug(
                        "[streaming_scheduler] 关闭连接失败: %s", _e, exc_info=True
                    )


class _MockScheduler:
    """最小化 mock 对象，满足 step2/step3 函数对 scheduler 参数的接口需求。"""

    def __init__(self, project_name: str, user_id: str = None):
        self._project_name = project_name
        self._user_id = user_id
        self.run_id = None
        self._force_regen = False
        self._step4_options = {}
        self._force_stop_check = None
        self._progress_callback = None
        # ★ _restore_step1_contexts 动态设置的上下文属性（默认空字符串）
        self._ui_context = ""
        self._api_docs_context = ""
        self._test_accounts_context = ""
        self._nav_context = ""  # 已废弃但保留兼容

    # step2 需要的属性
    @staticmethod
    def _parse_doc_sections(text: str):
        from src.pipeline.scheduler_helpers import parse_doc_sections

        return parse_doc_sections(text)

    @staticmethod
    def _build_skeleton(sections):
        from src.pipeline.scheduler_helpers import build_skeleton

        return build_skeleton(sections)

    @staticmethod
    def _extract_keywords(text: str):
        from src.pipeline.scheduler_helpers import extract_keywords

        return extract_keywords(text)

    @staticmethod
    def _validate_module_section_match(mod_name, matched):
        from src.pipeline.scheduler_helpers import validate_module_section_match

        return validate_module_section_match(mod_name, matched)

    @staticmethod
    def _build_module_context(mod_name, section_index, sections, skeleton):
        from src.pipeline.scheduler_helpers import build_module_context

        return build_module_context(mod_name, section_index, sections, skeleton)

    @staticmethod
    def _match_figma_pages(ui_ctx, nav_ctx, module_name):
        from src.pipeline.scheduler_helpers import match_figma_pages

        return match_figma_pages(ui_ctx, nav_ctx, module_name)

    @staticmethod
    def _build_traceability_and_diff(requirements: list):
        """★ v4.0: Step2 后构建追溯链路（与 PipelineScheduler 共用 helpers 唯一实现）。"""
        from src.pipeline.helpers import (
            build_traceability_and_diff,
            load_step2_dual_source_context,
        )

        yapi_data, db_schema, dom_snapshots = load_step2_dual_source_context()
        return build_traceability_and_diff(
            requirements=requirements,
            yapi_data=yapi_data,
            db_schema=db_schema,
            dom_snapshots=dom_snapshots,
        )

    @staticmethod
    def _apply_review_gate(count, mode, **extra):
        from src.pipeline.review_gate import apply_review_gate

        return apply_review_gate(count, mode, **extra)

    @staticmethod
    def _build_db_context():
        from src.pipeline.helpers import build_db_context

        return build_db_context()
