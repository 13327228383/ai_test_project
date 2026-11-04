# -*- coding: utf-8 -*-
"""
自我修复编排器
================
Step4 执行失败后，自动分析失败原因并重新生成用例（闭环）。

设计原则：
1. 只对 HARNESS_ERROR（引擎问题）触发重建，PRODUCT_BUG（被测系统bug）不重建
2. 单条用例最多重建 N 次（避免死循环）
3. 重建时将失败上下文注入 AI prompt，让 AI 知道上一版为什么失败
4. 重建后重试执行，验证新用例是否通过

零件复用：
- _remodel_intent: AI 重新建模 + 去重 + 替换
- case_requirement_tracker: case_id → req_id 反查
- failure_classifier: 失败分类（HARNESS_ERROR/PRODUCT_BUG）
"""

import logging
from src.core.guarded import guard
from typing import Any

_log = logging.getLogger(__name__)

# 单条用例最多重建次数（避免死循环）
_MAX_REBUILD_PER_CASE = 2


def auto_rebuild_failed_cases(
    execution_results: list[dict], max_rebuild: int = None
) -> dict[str, Any]:
    """Step4 执行失败后，自动重建 HARNESS_ERROR 失败用例。

    Args:
        execution_results: Step4 执行结果列表，每个元素含 case_id/passed/failure_category 等
        max_rebuild: 最多重建多少条（None 用默认值）

    Returns:
        {"rebuilt": [...], "skipped": [...], "errors": [...]}
    """
    _max = max_rebuild or _MAX_REBUILD_PER_CASE * 10  # 默认最多重建 20 条
    rebuilt = []
    skipped = []
    errors = []

    # 筛选需要重建的失败用例：只重建 HARNESS_ERROR（引擎问题），不重建 PRODUCT_BUG
    candidates = []
    for res in execution_results:
        if res.get("passed", True):
            continue  # 通过的不重建
        category = res.get("failure_category", "")
        if category != "HARNESS_ERROR":
            skipped.append(
                {
                    "case_id": res.get("case_id"),
                    "reason": f"category={category}，非引擎问题不重建",
                }
            )
            continue
        candidates.append(res)

    if not candidates:
        _log.info("[SelfRepair] 无 HARNESS_ERROR 失败用例，跳过自我修复")
        return {"rebuilt": [], "skipped": skipped, "errors": []}

    _log.info(
        "[SelfRepair] 发现 %d 条 HARNESS_ERROR 失败用例，开始重建（最多 %d 条）",
        len(candidates),
        min(len(candidates), _max),
    )

    # 逐条重建
    rebuild_count = 0
    for res in candidates:
        if rebuild_count >= _max:
            skipped.append(
                {"case_id": res.get("case_id"), "reason": "达到最大重建数限制"}
            )
            continue
        case_id = res.get("case_id", "")
        try:
            result = _rebuild_one_case(case_id, res)
            if result.get("ok"):
                rebuilt.append(
                    {"case_id": case_id, "new_case_id": result.get("new_case_id")}
                )
                rebuild_count += 1
                _log.info(
                    "[SelfRepair] ✓ 重建成功: %s → %s",
                    case_id,
                    result.get("new_case_id"),
                )
            else:
                skipped.append(
                    {"case_id": case_id, "reason": result.get("reason", "unknown")}
                )
        except Exception as e:
            errors.append({"case_id": case_id, "error": str(e)[:200]})
            _log.warning("[SelfRepair] ✗ 重建异常: %s → %s", case_id, str(e)[:100])

    _log.info(
        "[SelfRepair] 重建完成: 成功 %d 条，跳过 %d 条，异常 %d 条",
        len(rebuilt),
        len(skipped),
        len(errors),
    )
    # ★ H4-6: 记录增量重生成功率
    _total = len(rebuilt) + len(skipped) + len(errors)
    if _total > 0:
        with guard("auto_rebuild_failed_cases: from src.backend.state import _threa", log=_log):
            from src.backend.state import _thread_run_id
            from src.extensions.metrics_collector import metrics_collector

            _rid = getattr(_thread_run_id, "value", "") or ""
            if _rid:
                from src.core.config_loader import config

                metrics_collector.record_incremental_regen(
                    project=config.project_name,
                    run_id=_rid,
                    total=_total,
                    success=len(rebuilt),
                    failed=len(skipped) + len(errors),
                )
    return {"rebuilt": rebuilt, "skipped": skipped, "errors": errors}


def _rebuild_one_case(case_id: str, failure_result: dict) -> dict:
    """重建单条用例（调用 _remodel_intent + 注入失败上下文）。

    Args:
        case_id: 失败用例的 case_id
        failure_result: 失败结果（含 failure_reason/failure_chain）

    Returns:
        {"ok": True/False, "new_case_id": str, "reason": str}
    """
    from src.backend.routes.case_review._remodel import _remodel_intent

    # ★ 注入失败上下文到全局变量（_remodel_intent 内部 AI 调用会读取）
    #   让 AI 知道上一版用例为什么失败，生成更可靠的用例
    _failure_context = {
        "case_id": case_id,
        "failure_reason": failure_result.get("failure_reason", "")[:500],
        "failure_category": failure_result.get("failure_category", ""),
    }
    _set_failure_context(_failure_context)

    try:
        result = _remodel_intent(case_id)
        if result.get("ok"):
            # _remodel_intent 替换了旧用例，新用例的 case_id 可能不同
            new_case_id = result.get("new_case_id", case_id)
            return {"ok": True, "new_case_id": new_case_id}
        return {"ok": False, "reason": result.get("reason", "unknown")}
    finally:
        _clear_failure_context()


# ─── 失败上下文传递（线程安全）───
import threading

_failure_ctx = threading.local()


def _set_failure_context(ctx: dict):
    """设置当前线程的失败上下文（供 _remodel_intent 内部 AI 调用读取）。"""
    _failure_ctx.value = ctx


def _clear_failure_context():
    """清除失败上下文。"""
    if hasattr(_failure_ctx, "value"):
        del _failure_ctx.value


def get_failure_context() -> dict:
    """获取当前线程的失败上下文（供 AI prompt 构建时读取）。"""
    return getattr(_failure_ctx, "value", {})
