# -*- coding: utf-8 -*-
from __future__ import annotations
from src.core.guarded import guard

"""
流水线总调度器 (PipelineScheduler) — v3.1 模块化拆分
=====================================================
★ v3.1: 将 Step1/Step2/Step3 大方法拆分为独立模块，通过委托调用。
  - pipeline_step1.py: step1_fetch_data()
  - pipeline_step2.py: step2_breakdown()
  - pipeline_step3.py: step3_model_cases()

串联 5 步智能测试流水线：
  Step 1: 拉取 Confluence 需求 + Figma 设计稿 → 解析 UI 结构
  Step 2: AI 需求拆解 → requirements_breakdown.xlsx（注入 UI 上下文）
  Step 3: AI 生成测试用例 → Validation_Intent-*.xlsx + 回写 is_generated（注入 UI 上下文）
  Step 4: Playwright 智能执行 + 失败截图
  Step 5: 生成 Markdown 测试报告

核心设计：
- 每步支持断点续跑：已完成的步骤自动跳过
- 所有中间状态持久化在双 Excel 中
"""

import sys
import time
import re
import uuid
import logging
import threading
from pathlib import Path
from typing import Any

from src.core.log_utils import fmt_log
from src.pipeline.scheduler_helpers import (
    parse_doc_sections as _parse_doc_sections,
    build_skeleton as _build_skeleton,
    extract_keywords as _extract_keywords,
    validate_module_section_match as _validate_module_section_match,
    build_module_context as _build_module_context,
    match_figma_pages as _match_figma_pages,
    is_api_only_intent as _is_api_only_intent,
)

# ★ §4.3 单文件≤800行：ThreadPoolExecutor + 超时轮询拆分到 scheduler_executor.py
from src.pipeline.scheduler_executor import execute_step_with_timeout
from src.core.pipeline_config import pipeline_config
from src.pipeline.pipeline_contracts import validate_step_result

_log = logging.getLogger(__name__)

# ★ 线程锁：保护 sys.path 修改和 importlib.reload 等全局状态操作
_path_lock = threading.Lock()

# ★ Step 级别超时（秒）：动态读取，支持前端修改后热生效
# v2 (2026-06-25): 某SPA系统项目 107 条需求实体/模块视角双跑+AI 拆解耗时大幅超过 1200s，调宽到 2400s
# v3 (2026-07-04): 某SPA系统项目 259 条功能 feature_driven_extract 逐条 AI 调用，2400s 不够，调宽到 7200s（2小时）
_STEP_TIMEOUT_DEFAULTS = {1: 1200, 2: 7200, 3: 3600, 4: 7200, 5: 600}
_STEP_TIMEOUT_KEYS = {
    1: "STEP1_TIMEOUT_SECONDS",
    2: "STEP2_TIMEOUT_SECONDS",
    3: "STEP3_TIMEOUT_SECONDS",
    4: "STEP4_TIMEOUT_SECONDS",
    5: "STEP5_TIMEOUT_SECONDS",
}


def _get_step_timeout(step_num: int) -> int:
    """动态读取 Step 超时配置（DB > 环境变量 > 默认值），支持热生效。"""
    key = _STEP_TIMEOUT_KEYS.get(step_num)
    default = _STEP_TIMEOUT_DEFAULTS.get(step_num, 600)
    if key:
        return int(pipeline_config._read_config_value(key, default))
    return default


# ★ 模块常量：替代魔法数字
_KEYWORD_MIN_MATCH_RATIO = pipeline_config.keyword_min_match_ratio
_KEYWORD_SINGLE_FALLBACK = pipeline_config.keyword_single_fallback
_FIGMA_CONTEXT_MAX_CHARS = pipeline_config.figma_context_max_chars


class PipelineScheduler:
    """
    流水线总调度器。
    """

    def __init__(self, project_name: str = "sample_project", user_id: str = None):
        self._project_name = project_name
        self._user_id = user_id
        self._start_time = None
        self._config_loaded = False
        self._force_stop_check = None
        self._progress_callback = None
        self.run_id = None
        _log.info(
            "[PipelineScheduler] 初始化完成，项目: %s, 用户: %s",
            project_name,
            user_id or "—",
        )

    def _ensure_modules(self):
        if self._config_loaded:
            return
        with _path_lock:
            if self._config_loaded:
                return
            root = (
                Path(__file__).resolve().parent.parent.parent
            )  # scheduler.py → src/pipeline/ → src/ → 项目根
            if str(root) not in sys.path:
                sys.path.insert(0, str(root))
            src_path = root / "src"
            if str(src_path) not in sys.path:
                sys.path.insert(0, str(src_path))
            self._config_loaded = True

    # =====================================================================
    # 公共入口
    # =====================================================================

    def run(
        self,
        from_step: int = 1,
        end_step: int = 5,
        quick: bool = False,
        step4_options: dict = None,
        figma_force_refresh: bool = False,
        force_regen: bool = False,  # ★ 强制重新生成测试用例（忽略覆盖率）
        resume: bool = False,
        pipeline_mode: str = "sequential",  # ★ v4.0: sequential | streaming
        skip_sources: list = None,  # ★ 需要跳过的数据源列表（用户选择）
        force_overwrite: bool = False,  # ★ 强制覆盖所有 Step1 数据
        force_step2: bool = False,  # ★ 强制重拆解 Step2（忽略哈希检查，unchanged 改为 modified）
    ) -> dict[str, Any]:
        # ★ v4.0: streaming 模式委托给 StreamingPipelineScheduler
        if pipeline_mode == "streaming":
            _log.info("[PipelineScheduler] 流式模式 → 委托 StreamingPipelineScheduler")
            from src.pipeline.streaming_scheduler import StreamingPipelineScheduler
            from src.core.versioning import current_run_id

            ss = StreamingPipelineScheduler(
                project_name=self._project_name, user_id=self._user_id
            )
            ss.run_id = self.run_id or current_run_id(self._project_name)
            self.run_id = ss.run_id
            ss._force_stop_check = self._force_stop_check
            ss._progress_callback = self._progress_callback
            return ss.run(
                from_step=from_step,
                end_step=end_step,
                quick=quick,
                step4_options=step4_options,
                figma_force_refresh=figma_force_refresh,
                force_regen=force_regen,
                resume=resume,
                force_overwrite=force_overwrite,
                force_step2=force_step2,
                skip_sources=skip_sources,
            )
        self._ensure_modules()
        # ★ 根因修复(2026-09-21) [未复核-需 GLM/DeepSeek 把关]: 流水线进程插件引导。
        #   现象：init_pipeline_plugins 仅在 Web 控制台进程(app.py)调用；独立流水线进程
        #   (full_pipeline_runner/CLI)从不初始化 → _registered_plugins 为空 →
        #   snapshot_registration_counts 无数据可落 → 指标页 pipeline_ext 恒空（跨进程断链）。
        #   修法：在流水线真正执行处幂等初始化（_initialized 标志保证零重复副作用），
        #   任何进程跑流水线均获得一致的插件/订阅态。
        with guard("run: from src.pipeline.plugins.bootstrap ", log=_log, level=logging.WARNING):
            from src.pipeline.plugins.bootstrap import init_pipeline_plugins

            init_pipeline_plugins()
        # ★ 确保配置已加载为当前项目（防止因时序问题导致配置为空）
        from src.core.config_loader import config as _cfg

        if self._project_name:
            _cfg.reload(self._project_name)
        self._start_time = time.time()
        self._force_regen = force_regen  # ★ 保存到实例，供 _step3_model_cases 读取
        self._step4_options = step4_options or {}
        self._skip_sources = skip_sources or []  # ★ 需要跳过的数据源列表
        self._force_overwrite = force_overwrite  # ★ 强制覆盖所有 Step1 数据
        self._force_step2 = force_step2  # ★ 强制重拆解 Step2

        # ★ v2.1: 废弃 _full_auto 全流程自动跳过逻辑
        # 一键执行（from_step=1, end_step=5）也强制暂停弹审核面板，与单步执行一致
        # set_skip_review_gate(True) 已废弃，review_gate.py 不再响应此标志

        # ★ 新启动（非 resume）清除残留暂停状态，防止上次执行残留导致本次被挡
        from src.pipeline.utils.pause_controller import pause_controller
        from src.backend.state import _set_paused_step

        if not resume:
            pause_controller.resume()
            # ★ A1 改造：按 project 清除暂停状态
            _set_paused_step(0, self._project_name)

        # ★ 生成或复用 run_id（确定性：项目+版本）
        if not self.run_id:
            from src.core.versioning import current_run_id

            self.run_id = current_run_id(self._project_name)
        run_id = self.run_id

        # ★ Checkpoint 恢复逻辑
        from src.pipeline.utils.checkpoint_store import checkpoint_store

        if resume:
            last_cp = checkpoint_store.get_last_checkpoint(
                self._project_name, run_id=run_id
            )
            # ★ 回退：按 run_id 查不到时（如审核闸门恢复生成了新 run_id），按项目名查
            if not last_cp:
                last_cp = checkpoint_store.get_last_checkpoint(self._project_name)
                if last_cp:
                    _log.info(
                        "  [Checkpoint] 按 run_id=%s 未找到，回退按项目名查找", run_id
                    )
            if last_cp:
                resume_step = int(last_cp.get("step", 0)) + 1
                if resume_step > from_step:
                    _log.info(
                        "  [Checkpoint] 从上次成功步骤恢复: Step %d → %d (run_id=%s)",
                        from_step,
                        resume_step,
                        run_id,
                    )
                    from_step = resume_step

        _log.info("=" * 60)
        _log.info("  E2E 智能测试流水线 v3.1")
        mode_desc = "快速模式（采样数据/限长）" if quick else "完整模式（全量执行）"
        _log.info(
            "  项目: %s | 步骤: %d → %d | 模式: %s | run_id: %s"
            % (self._project_name, from_step, end_step, mode_desc, run_id)
        )
        _log.info("=" * 60)
        _log.info(
            "流水线启动 项目=%s 步骤=%d→%d 模式=%s run_id=%s",
            self._project_name,
            from_step,
            end_step,
            mode_desc,
            run_id,
        )

        results = {"project": self._project_name, "run_id": run_id, "steps": {}}
        from src.pipeline.saga import PipelineSaga

        saga = PipelineSaga(run_id=run_id, project=self._project_name)

        steps = [
            (1, self._step1_fetch_data, "拉取数据源 (需求+设计+接口+DB) + RAG 索引"),
            (2, self._step2_breakdown, "AI 需求拆解（含 UI 上下文）"),
            (3, self._step3_model_cases, "测试用例建模（含 UI 上下文）"),
            (4, self._step4_execute, "意图执行验证"),
            (5, self._step5_report, "生成报告"),
        ]

        for step_num, step_func, step_name in steps:
            if step_num < from_step:
                results["steps"][step_num] = {"status": "skipped"}
                continue

            if step_num > end_step:
                results["steps"][step_num] = {"status": "skipped"}
                continue

            # ★ 暂停检查点（v2.1: 一键执行也尊重暂停状态）
            if pause_controller.is_paused:
                _log.warning("流水线已暂停（Step %d 前），点击「继续」恢复", step_num)
                _log.info(
                    "##HTML:##<span style='color:#f59e0b;font-weight:bold'>⏸ [PAUSE] 流水线已暂停（Step %d 前），点击「继续」恢复</span>",
                    step_num,
                )
                results["steps"][step_num] = {"status": "paused"}
                results["paused"] = True
                results["paused_at_step"] = step_num
                break

            # ★ 强制停止检查点（全流程自动模式跳过，除非用户主动停止）
            if self._force_stop_check and self._force_stop_check():
                results["force_stopped"] = True
                if self._progress_callback:
                    self._progress_callback(
                        step_num, step_name, 0, -1, from_step, end_step
                    )
                break

            if self._progress_callback:
                self._progress_callback(step_num, step_name, 0, -1, from_step, end_step)

            _log.info("[Step%d] " + "=" * 50, step_num)
            _log.info("[Step%d] %s", step_num, step_name)
            _log.info("[Step%d] " + "=" * 50, step_num)
            _log.info("[Step%d] 开始执行: %s", step_num, step_name)

            # ★ Checkpoint：记录 step 开始
            checkpoint_store.save_checkpoint(
                run_id, self._project_name, step_num, "running"
            )
            step_start_time = time.time()  # ★ 性能监控：记录步骤开始时间

            # ★ Pre-Step Gate：步骤启动前，校验上一步骤的产出质量
            if step_num >= 3:
                _gate_label = ""
                try:
                    from src.pipeline.pre_step_gate import (
                        run_pre_step_gate,
                        get_gate_label,
                        PreStepGateBlockedError,
                    )

                    _gate_label = get_gate_label(step_num) or ""
                    _log.info("[Step%d] ✅ 运行前置准入检查: %s", step_num, _gate_label)
                    _gate_result = run_pre_step_gate(step_num, self._project_name)
                    # 持久化门禁结果到执行记录（供前端展示）
                    _persist_gate_result(
                        step_num, _gate_result, run_id, self._project_name
                    )
                    _log.info(
                        "[Step%d] 前置准入检查通过: %s", step_num, _gate_result.summary
                    )
                except PreStepGateBlockedError as _gate_e:
                    _log.error("[Step%d] ❌ 前置准入检查阻断: %s", step_num, _gate_e)
                    _persist_gate_blocked(
                        step_num, str(_gate_e), run_id, self._project_name
                    )
                    results["gate_blocked"] = True
                    results["gate_blocked_at"] = step_num
                    results["gate_blocked_reason"] = str(_gate_e)
                    # 更新 checkpoint 为失败
                    checkpoint_store.save_checkpoint(
                        run_id, self._project_name, step_num, "failed"
                    )
                    if self._progress_callback:
                        self._progress_callback(
                            step_num, step_name, -1, -1, from_step, end_step
                        )
                    break
            try:
                # ★ §4.3 单函数≤80行：ThreadPoolExecutor + 超时轮询拆分到 scheduler_executor.py
                step_result = execute_step_with_timeout(
                    step_num,
                    step_func,
                    quick,
                    figma_force_refresh,
                    run_id,
                    self._force_step2,
                    self._force_stop_check,
                    _get_step_timeout,
                )
                step_result = validate_step_result(step_num, step_result)
                # ★ 性能监控：记录步骤执行时间
                step_elapsed = time.time() - step_start_time
                _log.info("[Step%d] 执行完成，耗时 %.1f 秒", step_num, step_elapsed)
                if step_result and isinstance(step_result, dict):
                    step_result["_elapsed_seconds"] = round(step_elapsed, 1)
                if step_result.get("contract_errors"):
                    _log.error(
                        "[Step%d] 数据契约校验失败: %s",
                        step_num,
                        step_result.get("contract_errors"),
                    )
                results["steps"][step_num] = step_result
                # ★ 显示 Step 1 的警告信息
                if step_num == 1 and step_result is not None:
                    warnings_list = step_result.get("warnings", [])
                    if warnings_list:
                        _log.info("[Step1] ═══ ⚠ 非致命警告 ═══")
                        for w in warnings_list:
                            _log.info("[Step1] ⚠ %s", w[:120])
                            _log.warning("[Step1] 警告: %s", w[:120])
                        _log.info("[Step1] 流程将继续使用可用数据源执行")
                if step_result and (
                    step_result.get("paused")
                    or step_result.get("paused_for_review")
                    or step_result.get("paused_for_case_review")
                    or step_result.get("review_needed")
                ):
                    # ★ v2.1: 一键执行也强制暂停弹审核面板（不再自动跳过）
                    if step_result.get("review_needed"):
                        _log.info(
                            "[Step%d] ##HTML:##<span style='color:#f59e0b;font-weight:bold'>⏸ [审核闸门] 需求拆解完成，请在审核面板中确认后点击「继续」</span>",
                            step_num,
                        )
                        _log.info("[Step%d] [审核闸门] 暂停流水线等待审核...", step_num)
                        with guard("run: from src.pipeline.utils.pause_contro", log=_log, level=logging.WARNING):
                            from src.pipeline.utils.pause_controller import (
                                pause_controller,
                            )

                            pause_controller.pause()
                            step_result["review_approved"] = True
                            step_result.pop("review_needed", None)
                    elif step_result.get("paused_for_case_review"):
                        _log.info(
                            "[Step%d] ##HTML:##<span style='color:#f59e0b;font-weight:bold'>⏸ [用例审核] 测试用例建模完成，请在审核面板中确认后点击「继续」</span>",
                            step_num,
                        )
                        _log.info(
                            "[Step%d] [用例审核] 暂停流水线等待测试用例审核...",
                            step_num,
                        )
                    else:
                        _log.warning(
                            "[Step%d] 流水线暂停，点击「继续」恢复执行", step_num
                        )
                        _log.info(
                            "[Step%d] ##HTML:##<span style='color:#f59e0b;font-weight:bold'>⏸ [PAUSE] 流水线暂停，点击「继续」恢复执行</span>",
                            step_num,
                        )
                    results["paused"] = True
                    results["paused_at_step"] = step_num
                    checkpoint_store.save_checkpoint(
                        run_id, self._project_name, step_num, "success"
                    )
                    break
                if step_result is not None and not step_result.get("success", True):
                    _log.error("[Step%d] 失败，流水线中止", step_num)
                    _log.error(
                        "[Step%d] %s", step_num, fmt_log("FAIL", "失败，流水线中止")
                    )
                    # ★ 根因修复(2026-10-15) D54：步骤失败/超时必须**回传**给 run 终态。
                    #   根因: 本分支只打日志 + break，`results` 里不留任何失败标志
                    #     → 上层 `_run_step` 无从得知步骤未成功 → run 以 status=completed 收尾。
                    #   后果(实测 v14): Step4 于 7200s 被强杀在 304/597（141 条用例从未执行），
                    #     但 RUN_END 报 completed → 统计层按全分母出"通过率 69.8%"
                    #     → 未执行被算进分母，违反"完成判据本身必须可靠"。
                    #   修法: 显式落 `success=False` + `aborted_at_step` + 错误文本，
                    #     由 _run_step 据此把 run 终态降级为 aborted_timeout / failed。
                    #   通用性: 只表达"步骤未成功"这一事实，零业务/模块硬编码。
                    results["success"] = False
                    results["aborted_at_step"] = step_num
                    results["error"] = str(step_result.get("error", "未知错误"))
                    # ★ Checkpoint：记录 step 失败
                    checkpoint_store.save_checkpoint(
                        run_id,
                        self._project_name,
                        step_num,
                        "failed",
                        error_msg=str(step_result.get("error", "未知错误")),
                    )
                    results["saga_compensation"] = saga.compensate(
                        failed_step=step_num,
                        reason=str(step_result.get("error", "未知错误")),
                    )
                    break
                # ★ Checkpoint：只有真正成功后才记录 step 成功
                checkpoint_store.save_checkpoint(
                    run_id, self._project_name, step_num, "success"
                )
                saga.mark_completed(
                    step_num, step_name, self._get_compensate_hook(step_num)
                )
                if self._progress_callback:
                    step_total = step_result.get("count", -1)
                    self._progress_callback(
                        step_num,
                        step_name,
                        step_total,
                        step_total if step_total > 0 else -1,
                        from_step,
                        end_step,
                    )
            except Exception as e:
                _log.error("Step %d 异常: %s", step_num, e, exc_info=True)
                _log.error(fmt_log("FAIL", "Step %d 异常: %s" % (step_num, e)))
                import traceback

                traceback.print_exc()
                results["steps"][step_num] = {"status": "error", "error": str(e)}
                # ★ Checkpoint：记录 step 失败
                checkpoint_store.save_checkpoint(
                    run_id, self._project_name, step_num, "failed", error_msg=str(e)
                )
                results["saga_compensation"] = saga.compensate(
                    failed_step=step_num, reason=str(e)
                )
                break

        elapsed = time.time() - self._start_time
        results["elapsed_seconds"] = elapsed

        is_paused = results.get("paused", False)
        is_stopped = results.get("force_stopped", False)
        is_gate_blocked = results.get("gate_blocked", False)

        if is_gate_blocked:
            _blocked_step = results.get("gate_blocked_at", 0)
            _blocked_reason = results.get("gate_blocked_reason", "")
            _log.info("=" * 60)
            _log.info("  ❌ 流水线门禁阻断 | Step %d 前置准入检查未通过", _blocked_step)
            _log.info("  原因: %s", _blocked_reason)
            _log.info("=" * 60)
            _log.error("流水线门禁阻断 | Step %d: %s", _blocked_step, _blocked_reason)
        elif not is_paused and not is_stopped:
            with guard("run: from src.core.session_context import", log=_log, level=logging.WARNING):
                from src.core.session_context import session_ctx

                session_ctx.close()
            # ★ 根因修复(2026-09-21) [未复核-需 GLM/DeepSeek 把关]: PIPELINE_COMPLETE 事件发布。
            #   现象：该事件全仓零发布点 → on_pipeline_complete（含插件注册数快照落库
            #   snapshot_registration_counts → ai_health_metrics pipeline_ext）从未触发，
            #   指标页「流水线扩展」恒空。
            #   修法：流水线真实完成（未暂停/未终止/未被门禁阻断）时在此发布，与
            #   bootstrap 的订阅契约对齐；Web 进程与独立流水线进程同享该路径。
            with guard("run: from src.pipeline.plugins.pipeline_b", log=_log, level=logging.WARNING):
                from src.pipeline.plugins.pipeline_bus import (
                    PipelineEvent,
                    pipeline_bus,
                )

                pipeline_bus.publish(
                    PipelineEvent.PIPELINE_COMPLETE,
                    {
                        "project": self._project_name,
                        "execution_id": getattr(self, "run_id", "") or "",
                        "steps": results.get("steps", {}),
                    },
                )
            step_name_map = {s[0]: s[2] for s in steps}
            if from_step == end_step:
                label = "Step %d: %s" % (from_step, step_name_map.get(from_step, ""))
            else:
                label = "全流程流水线"
            _log.info("=" * 60)
            _log.info("  %s 执行完成 | 耗时: %.1f 秒", label, elapsed)
            _log.info("=" * 60)
            _log.info("%s 执行完成 | 耗时: %.1f 秒", label, elapsed)
        elif is_paused:
            _log.info("=" * 60)
            _log.info("  ⏸ 流水线已暂停 | 等待用户操作...")
            _log.info("=" * 60)
            _log.warning("流水线已暂停 | 等待用户操作...")
        elif is_stopped:
            _log.info("=" * 60)
            _log.info("  ⏹ 流水线已被用户强制终止")
            _log.info("=" * 60)
            _log.warning("流水线已被用户强制终止")

        return results

    # =====================================================================
    # Step 1: 拉取数据源 + Figma UI 解析（★ v3.1 委托）
    # =====================================================================

    def _step1_fetch_data(
        self, quick: bool = False, figma_force_refresh: bool = False, run_id: str = None
    ) -> dict[str, Any]:
        """Step 1: 委托到 pipeline_step1.py（内部含 RAG 增量构建）。"""
        from src.pipeline.step1 import step1_fetch_data

        result = step1_fetch_data(
            figma_force_refresh=figma_force_refresh,
            run_id=run_id,
            skip_sources=getattr(self, "_skip_sources", []),
            force_overwrite=getattr(self, "_force_overwrite", False),
        )
        return result

    # =====================================================================
    # Step 2: AI 需求拆解（★ v3.1 委托）
    # =====================================================================

    def _step2_breakdown(
        self, quick: bool = False, force_step2: bool = False, run_id: str = None
    ) -> dict[str, Any]:
        """Step 2: 委托到 pipeline_step2.py"""
        # ★ 移除 importlib.reload：每次 reload 会破坏模块级全局变量（缓存、计数器、类引用）
        #    导致 Step2 状态被重置，断点续跑失效。Python 模块天然单例，无需 reload。
        from src.pipeline.step2 import step2_breakdown

        return step2_breakdown(self, quick, force_step2=force_step2, run_id=run_id)

    def _apply_review_gate(self, count: int, mode: str, **extra) -> dict[str, Any]:
        """★ v5：需求审核闸门。（★ v3.1 门面兼容）"""
        from src.pipeline.review_gate import apply_review_gate

        return apply_review_gate(count, mode, **extra)

    @staticmethod
    def _parse_doc_sections(text: str) -> list[dict]:
        """按 Markdown 标题解析文档为段落树。（委托到 scheduler_helpers）"""
        return _parse_doc_sections(text)

    @staticmethod
    def _build_skeleton(sections: list[dict]) -> str:
        """构建文档骨架（目录结构）。（委托到 scheduler_helpers）"""
        return _build_skeleton(sections)

    @staticmethod
    def _extract_keywords(text: str) -> list[str]:
        """从文本中提取用于匹配的关键词列表。（委托到 scheduler_helpers）"""
        return _extract_keywords(text)

    @staticmethod
    def _validate_module_section_match(mod_name: str, matched: list[dict]) -> bool:
        """校验模块名与匹配段落的关联度（同时搜索标题和正文）。（委托到 scheduler_helpers）"""
        return _validate_module_section_match(mod_name, matched)

    @staticmethod
    def _build_module_context(
        mod_name: str, section_index: int, sections: list[dict], skeleton: str
    ) -> str:
        """为单个模块构建带骨架和溯源信息的上下文文本。（委托到 scheduler_helpers）"""
        return _build_module_context(mod_name, section_index, sections, skeleton)

    @staticmethod
    def _match_figma_pages(ui_ctx: str, nav_ctx: str, module_name: str) -> str:
        """从 UI 描述和导航地图中匹配与模块相关的 Figma 页面。（委托到 scheduler_helpers）"""
        return _match_figma_pages(ui_ctx, nav_ctx, module_name)

    # =====================================================================
    # Step 3: AI 生成测试用例（★ v3.1 委托）
    # =====================================================================

    def _step3_model_cases(
        self, quick: bool = False, force: bool = False, run_id: str = None
    ) -> dict[str, Any]:
        """Step 3: 委托到 pipeline_step3.py"""
        from src.pipeline.step3 import step3_model_cases

        # ★ force_regen 优先于参数 force
        _force = force or getattr(self, "_force_regen", False)
        return step3_model_cases(self, quick, _force, run_id=run_id)

    # =====================================================================
    # Step 4: AI测试用例执行
    # =====================================================================

    def _read_step4_intents_with_meta(
        self, excel_handler, include_virtual_inherited: bool = False
    ):
        """Step4 默认候选测试用例读取。（★ v3.1 门面兼容）"""
        from src.pipeline.step4_case_reader import read_intents_with_meta

        return read_intents_with_meta(
            excel_handler, include_virtual_inherited=include_virtual_inherited
        )

    def _read_step4_intents_by_ids(
        self, excel_handler, case_ids, include_virtual_inherited: bool = False
    ):
        """Step4 指定 case_id 读取。（★ v3.1 门面兼容）"""
        from src.pipeline.step4_case_reader import read_intents_by_ids

        return read_intents_by_ids(
            excel_handler, case_ids, include_virtual_inherited=include_virtual_inherited
        )

    def _read_step4_failed_intents(
        self, excel_handler, user_id=None, include_virtual_inherited: bool = False
    ):
        """Step4 失败重跑读取。（★ v3.1 门面兼容）"""
        from src.pipeline.step4_case_reader import read_failed_intents

        return read_failed_intents(
            excel_handler,
            user_id=user_id,
            include_virtual_inherited=include_virtual_inherited,
        )

    def _read_step4_unexecuted_cases_by_files(
        self,
        excel_handler,
        case_asset_files,
        user_id=None,
        include_virtual_inherited: bool = False,
    ):
        """Step4 多文件未执行测试用例读取。（★ v3.1 门面兼容）"""
        from src.pipeline.step4_case_reader import read_unexecuted_cases_by_files

        return read_unexecuted_cases_by_files(
            excel_handler,
            case_asset_files,
            user_id=user_id,
            include_virtual_inherited=include_virtual_inherited,
        )

    def _read_step4_intents_for_versions(
        self,
        excel_handler,
        version_ids,
        *,
        case_ids=None,
        include_executed: bool = False,
    ):
        """多版本执行读取。（★ v3.1 门面兼容）"""
        from src.pipeline.step4_case_reader import read_intents_for_versions

        return read_intents_for_versions(
            excel_handler,
            version_ids,
            case_ids=case_ids,
            include_executed=include_executed,
        )

    # =====================================================================
    # ★ 向后兼容别名：f3995cc4 拆分后，旧名 → 新名映射
    # =====================================================================
    @staticmethod
    def _is_api_only_case(case: dict) -> bool:
        """[兼容别名] → is_api_only_intent"""
        from src.pipeline.scheduler_helpers import is_api_only_intent

        return is_api_only_intent(case)

    @staticmethod
    def _execute_api_only_cases(cases, **kwargs):
        """[兼容别名] → execute_api_only_intents"""
        from src.pipeline.api_only_executor import execute_api_only_intents

        return execute_api_only_intents(cases, **kwargs)

    def _read_step4_cases_with_meta(
        self, excel_handler, include_virtual_inherited: bool = False
    ):
        """[兼容别名] → _read_step4_intents_with_meta"""
        return self._read_step4_intents_with_meta(
            excel_handler, include_virtual_inherited=include_virtual_inherited
        )

    def _read_step4_cases_by_ids(
        self, excel_handler, case_ids, include_virtual_inherited: bool = False
    ):
        """[兼容别名] → _read_step4_intents_by_ids"""
        return self._read_step4_intents_by_ids(
            excel_handler, case_ids, include_virtual_inherited=include_virtual_inherited
        )

    def _read_step4_failed_cases(
        self, excel_handler, user_id=None, include_virtual_inherited: bool = False
    ):
        """[兼容别名] → _read_step4_failed_intents"""
        return self._read_step4_failed_intents(
            excel_handler,
            user_id=user_id,
            include_virtual_inherited=include_virtual_inherited,
        )

    def _step4_execute(self, quick: bool = False, run_id: str = "") -> dict[str, Any]:
        """启动 Playwright 浏览器，执行所有未完成的测试用例。

        ★ v3.1 门面兼容：委托到 step4_executor.py，保持原有接口不变。
        """
        from src.pipeline.step4_executor import step4_execute

        # ★ [INT-1 fix 2026-08-28 | 已复核 2026-09-04] 全流程 force/新鲜模式下
        #   Step4 必须全量执行：force_regen/force_step2 语义即"重生成+重执行全部"，若仍走
        #   增量过滤会跳过已验证用例，造成"假完成"（只跑增量、掩盖全量结果）。
        #   复核确认（2026-09-04）：该判定正确 —— force 即"全部重做"，include_executed=True
        #   是语义必然；代价是慢（全量重跑），但属于 force 的预期开销，非缺陷。
        #   通用性修复：不依赖调用方传 include_executed，改为按 force 语义在调度层强制。
        _step4_opts = dict(getattr(self, "_step4_options", {}) or {})
        if getattr(self, "_force_regen", False) or getattr(self, "_force_step2", False):
            _step4_opts["include_executed"] = True
        # 跨 Step 只通过 MySQL 通信：Step1 上下文从 source_documents 读取
        from src.pipeline.step1_context_loader import load_step1_contexts

        _step1_ctx = load_step1_contexts(self._project_name)
        return step4_execute(
            quick=quick,
            run_id=run_id,
            step4_options=_step4_opts,
            user_id=self._user_id,
            force_stop_check=self._force_stop_check,
            progress_callback=self._progress_callback,
            read_intents_with_meta=self._read_step4_intents_with_meta,
            read_intents_by_ids=self._read_step4_intents_by_ids,
            read_failed_intents=self._read_step4_failed_intents,
            read_unexecuted_cases_by_files=self._read_step4_unexecuted_cases_by_files,
            read_intents_for_versions=self._read_step4_intents_for_versions,
            execute_api_only_intents=self._execute_api_only_intents,
        )

    @staticmethod
    def _is_api_only_intent(intent: dict) -> bool:
        """判断测试用例是否可以走 API 直调快速通道。（委托到 scheduler_helpers）"""
        return _is_api_only_intent(intent)

    def _execute_api_only_intents(self, intents: list[dict]) -> dict[str, int]:
        """快速通道：api_only 测试用例直接执行可落地的客观校验。

        ★ v3.1 门面兼容：委托到 api_only_executor.py，保持原有接口不变。
        """
        from src.core.excel_handler import excel_handler
        from src.pipeline.api_only_executor import execute_api_only_intents

        return execute_api_only_intents(
            intents,
            user_id=self._user_id,
            project_name=self._project_name,
            excel_handler=excel_handler,
        )

    # =====================================================================
    # Step 5: 生成报告
    # =====================================================================

    def _step5_report(self, quick: bool = False, run_id: str = None) -> dict[str, Any]:
        """基于执行结果生成 Markdown 测试报告（并落 HTML 交付物）。"""
        from src.pipeline.steps.step5.report_generator import generate_report

        # HTML 是交付标准要求的产物，且平台 file_tree/mysql_doc 已按 html_report 类型取用；
        #   此前无任何调用方传 generate_html=True → 该能力在流水线内不可达（恒无 HTML 报告）。
        return generate_report(self._project_name, run_id=run_id, generate_html=True)

    def _get_compensate_hook(self, step_num: int):
        """返回 Step 补偿钩子，实现 PipelineSaga 的逆序回滚。

        ★ v3.1 门面兼容：委托到 saga_compensators.py，保持原有接口不变。
        """
        from src.pipeline.saga_compensators import get_compensate_hook

        return get_compensate_hook(
            step_num, project=self._project_name, run_id=self.run_id
        )

    # =====================================================================
    # 辅助：构建数据库上下文
    # =====================================================================

    @staticmethod
    def _build_db_context() -> str:
        """委托到 pipeline_helpers.build_db_context。"""
        from src.pipeline.helpers import build_db_context

        return build_db_context()

    @staticmethod
    def _build_traceability_and_diff(requirements: list) -> dict:
        """★ v6：Step2 后构建追溯链路 + RAG索引 + 变更快照。

        上下文加载（yapi/db_schema/DOM 双源读取）唯一实现在
        ``helpers.load_step2_dual_source_context``；本方法只做装配。
        """
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


# ═══════════════════════════════════════════════════════════
# Pre-Step Gate 持久化辅助函数
# ═══════════════════════════════════════════════════════════


def _persist_gate_result(step_num: int, gate_result, run_id: str, project: str):
    """持久化门禁通过结果到数据库（upsert，防 pre_gate 骨架行累积）。

    ★ F4 全局修复 [未复核-需 GLM/DeepSeek 把关]（2026-08-29）：
      纯 INSERT → 复用 src/users/audit.py `_upsert_execution`（键 =
      run_id + project + case_id + check_type）。run_id 是确定性值
      `{project}__{version_label}`，同项目同版本每轮 Step4/5 都写同一条
      pre_gate_stepN → 覆盖而非新增，避免两轮 = 4 行累积。
      受 _start.py Step 互斥 + _active_runs 互斥保护，同 run_id 并发概率极低。
    """
    try:
        from datetime import datetime

        from src.core.db_manager import get_connection, release_connection
        from src.users.audit import _upsert_execution

        conn = None
        try:
            conn = get_connection()
            _now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            # ★ 注意: cols 列名间不能带空格 —— _upsert_execution 按 "," 切分后
            #   再按 " " 取首段作为 UPDATE SET 列名（record_execution 同款格式）。
            cols = (
                "run_id,project,case_id,check_type,user_id,status,"
                "failure_reason,expected,actual,details,executed_at"
            )
            ph = "%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s"
            params = [
                run_id,
                project,
                f"pre_gate_step{step_num}",
                "pre_gate",
                "admin",
                "PRE_GATE",
                "",
                "gate_passed",
                "gate_passed",
                f"Step {step_num} 前置准入检查通过: {gate_result.summary}",
                _now,
            ]
            _upsert_execution(
                conn,
                cols,
                ph,
                params,
                run_id,
                project,
                f"pre_gate_step{step_num}",
                "pre_gate",
            )
        finally:
            if conn:
                release_connection(conn)
    except Exception as e:
        _log = __import__("logging").getLogger(__name__)
        _log.warning("[PreGate] 持久化门禁结果失败: %s", e)


def _persist_gate_blocked(step_num: int, reason: str, run_id: str, project: str):
    """持久化门禁阻断结果到数据库（upsert，防 pre_gate 骨架行累积）。

    ★ W3 T02 (2026-08-05)：failure_category 从空串改为结构化归因码
      （classify_pre_gate_reason 关键词归因；纯读不改历史行，仅影响本次新写入）。
    ★ F4 全局修复 [未复核-需 GLM/DeepSeek 把关]（2026-08-29）：
      与 _persist_gate_result 同策略，纯 INSERT → `_upsert_execution`
      （键 = run_id + project + case_id + check_type），阻断状态覆盖旧行。
    """
    try:
        from datetime import datetime

        from src.core.db_manager import get_connection, release_connection
        from src.users.audit import _upsert_execution

        # 结构化归因码（定位器/数据/权限/建模缺失 → 对应码，默认 OTHER）
        try:
            from src.pipeline.steps.step5.attribution import classify_pre_gate_reason

            _attribution_code = classify_pre_gate_reason(reason)
        except Exception:  # noqa: BLE001 —— 归因失败不影响阻断持久化
            _attribution_code = "OTHER"
        conn = None
        try:
            conn = get_connection()
            _now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            # ★ 注意: cols 列名间不能带空格（见 _persist_gate_result 说明）
            cols = (
                "run_id,project,case_id,check_type,user_id,status,"
                "failure_reason,failure_category,expected,actual,details,executed_at"
            )
            ph = "%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s"
            params = [
                run_id,
                project,
                f"pre_gate_step{step_num}",
                "pre_gate",
                "admin",
                "PRE_GATE_BLOCKED",
                reason,
                _attribution_code,
                "gate_passed",
                "gate_blocked",
                f"Step {step_num} 前置准入阻断: {reason}",
                _now,
            ]
            _upsert_execution(
                conn,
                cols,
                ph,
                params,
                run_id,
                project,
                f"pre_gate_step{step_num}",
                "pre_gate",
            )
        finally:
            if conn:
                release_connection(conn)
    except Exception as e:
        _log = __import__("logging").getLogger(__name__)
        _log.warning("[PreGate] 持久化阻断结果失败: %s", e)
