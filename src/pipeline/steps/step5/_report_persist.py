# -*- coding: utf-8 -*-
"""_report_persist.py — 报告持久化逻辑（从 report_generator.py 拆分，§4.3 单文件≤800行）。

职责：
  - build_empty_report: 无测试用例数据时构建空报告
  - persist_report: 报告内容持久化（保存到 generated_reports + 写入 run_summary 表，带重试）

通用性：纯数据持久化，不依赖具体业务逻辑，适用于所有项目/行业。
设计约束：仅接收参数 + 写 MySQL，不读取 Excel、不生成报告内容。
"""
from __future__ import annotations
from src.core.guarded import guard

import json as _json
import logging
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from src.core.constants import MYSQL_DOC_URI_REPORT
from src.pipeline.steps.step5._db_utils import _ensure_run_summary_table

_log = logging.getLogger(__name__)


def build_empty_report(project_name: str) -> dict[str, Any]:
    """无测试用例数据时构建空报告。

    通用性：纯文本拼接，不依赖业务逻辑，适用于所有项目。
    """
    _log.info("  无可用的测试用例数据，生成空数据报告")
    now = datetime.now()
    display_name = project_name
    try:
        from src.core.config_loader import config as _cfg

        ver = _cfg.project_version
        if ver:
            display_name = "%s--%s" % (project_name, ver)
    except Exception as _e:
        _log.debug("[report_generator] build_empty_report 异常: %s", _e, exc_info=True)
    empty_lines = [
        "# E2E 智能测试报告",
        "",
        "**项目**: %s" % display_name,
        "**生成时间**: %s" % now.strftime("%Y-%m-%d %H:%M:%S"),
        "",
        "---",
        "",
        "## 执行概览",
        "",
        "| 指标 | 数值 |",
        "|------|------|",
        "| 测试用例总数 | 0 |",
        "| 已验证 | 0 |",
        "| 通过 | 0 |",
        "| 失败 | 0 |",
        "| 跳过 | 0 |",
        "| 待验证 | 0 |",
        "| 达成率 | 未执行 |",
        "",
        "> 当前无可用的测试用例数据。请确认 Step3 已成功生成测试用例并写入 Excel。",
        "",
    ]
    report_content = "\n".join(empty_lines)
    return {
        "success": True,
        "count": 0,
        "skipped": False,
        "empty": True,
        "report_markdown": report_content,
    }


def persist_report(
    project_name: str,
    run_id: str,
    timestamp: str,
    report_content: str,
    total: int,
    passed: int,
    failed: int,
    skipped: int,
    pending: int,
    flaky: int,
    executed_count: int,
    pass_rate_value: float,
    confidence_summary: dict[str, Any],
    catalog_stats: dict[str, Any],
    intents: list[dict[str, Any]],
    version_id: int,
    self_eval_metrics: dict[str, Any] = None,
) -> dict[str, Any]:
    """报告持久化：保存到 generated_reports + 写入 run_summary 表（带重试）。

    通用性：纯数据持久化，参数化设计，适用于所有项目/行业。
    合理性：将持久化职责从报告生成中分离，遵循单一职责原则。

    Args:
        project_name: 项目名
        run_id: 运行ID
        timestamp: 报告时间戳（报告版本号）
        report_content: 报告 Markdown 内容
        total/passed/failed/skipped/pending/flaky: 统计计数
        executed_count: 已执行数
        pass_rate_value: 通过率（0~1）
        confidence_summary: 置信度摘要（含 avg_confidence/low_confidence_count 等）
        catalog_stats: 目录统计
        intents: 意图列表（用于提取 duration）
        version_id: 版本ID

    Returns:
        {"report_path": str} 虚拟路径（供前端显示，不对应磁盘文件）
    """
    from src.core.config_loader import config as _cfg

    # ★ 07-06 报告分表：报告内容写入 generated_reports，不再混入 source_documents
    with guard("persist_report: try: from src.users.reports import s", log=_log, level=logging.ERROR):
        with guard("persist_report: from src.users.reports import save_g", log=_log, level=logging.WARNING):
            from src.users.reports import save_generated_report

            save_generated_report(
                project=project_name,
                report_type="report",
                content=report_content,
                version_id=version_id,
                version_label=str(getattr(_cfg, "project_version", "") or ""),
                run_id=run_id or "",
            )
            _log.info(
                "  报告已写入 MySQL: project=%s report_type=report run_id=%s size=%d",
                project_name,
                run_id,
                len(report_content),
            )

        _write_ok = _write_run_summary(
            project_name,
            run_id,
            timestamp,
            version_id,
            _cfg,
            total,
            passed,
            failed,
            skipped,
            pending,
            flaky,
            executed_count,
            pass_rate_value,
            confidence_summary,
            catalog_stats,
            intents,
            self_eval_metrics=self_eval_metrics or {},
        )
        if not _write_ok:
            _log.error("  [报告] run_summary 写入最终失败 (已重试3次)")
    # 为保持调用方接口一致，返回虚拟路径（供前端/调用方显示用，不对应磁盘文件）
    # ★ P2: MySQL doc URI 走 constants
    report_path = Path("%s%s/%s" % (MYSQL_DOC_URI_REPORT, project_name, timestamp))
    return {"report_path": str(report_path)}


def _write_run_summary(
    project_name: str,
    run_id: str,
    timestamp: str,
    version_id: int,
    _cfg: Any,
    total: int,
    passed: int,
    failed: int,
    skipped: int,
    pending: int,
    flaky: int,
    executed_count: int,
    pass_rate_value: float,
    confidence_summary: dict[str, Any],
    catalog_stats: dict[str, Any],
    intents: list[dict[str, Any]],
    self_eval_metrics: dict[str, Any] = None,
) -> bool:
    """写入 run_summary 表（带重试，最多3次指数退避）。

    通用性：纯 SQL 写入，参数化，适用于所有项目。
    Returns:
        True 表示写入成功；False 表示重试后仍失败。
    """
    from src.core.db_manager import get_connection

    conn = get_connection()
    try:
        _ensure_run_summary_table(conn)
        duration_total = _sum_durations(intents, project_name)
        ai_stats = _load_ai_stats()
        summary_ext = _build_summary_ext(
            project_name,
            timestamp,
            confidence_summary,
            catalog_stats,
            ai_stats,
            self_eval_metrics=self_eval_metrics or {},
        )
        # ★ B-6-3: run_summary 写入重试（最多3次，指数退避 1s→2s→4s）
        _MAX_RETRIES = 3
        _RETRY_DELAY_BASE = 1.0
        # ★ 方案B: 获取当前执行用户ID，写入 run_summary.user_id
        try:
            from src.core.config_loader import get_current_user

            _run_user_id = get_current_user() or ""
        except Exception:
            _run_user_id = ""
        for _attempt in range(1, _MAX_RETRIES + 1):
            try:
                conn.execute(
                    _RUN_SUMMARY_INSERT_SQL,
                    _build_run_summary_params(
                        project_name,
                        version_id,
                        run_id,
                        _run_user_id,
                        timestamp,
                        total,
                        executed_count,
                        passed,
                        failed,
                        skipped,
                        pending,
                        flaky,
                        pass_rate_value,
                        confidence_summary,
                        duration_total,
                        summary_ext,
                    ),
                )
                conn.commit()
                _log.info(
                    "  运行摘要已写入 MySQL: project=%s run_id=%s version=%s",
                    project_name,
                    run_id,
                    getattr(_cfg, "project_version", "") or "",
                )
                return True
            except Exception as _se:
                _log.warning(
                    "  [报告] run_summary 写入失败 (第%d次/%d): %s",
                    _attempt,
                    _MAX_RETRIES,
                    str(_se)[:160],
                )
                if _attempt < _MAX_RETRIES:
                    time.sleep(_RETRY_DELAY_BASE * (2 ** (_attempt - 1)))
        return False
    except Exception as _re:
        _log.error("  [报告] run_summary 处理异常: %s", str(_re)[:120])
        return False
    finally:
        if conn:
            try:
                conn.close()
            except (OSError, ConnectionError) as _close_err:
                _log.warning("[Step5] conn.close 失败: %s", _close_err)


def _sum_durations(intents: list[dict[str, Any]], project_name: str = "") -> float:
    """从 intents 提取并汇总 duration（秒）。

    ★ v5.0 修复: 如果 intents 无 duration 信息，从 pipeline_checkpoints 读取
      所有 step 的 (completed_at - started_at) 总和作为 fallback。
      根因: intents 从 test_cases 表读取，无 duration 字段；
            executions 表也无 duration 字段。
      通用性: 适用于所有项目，从 MySQL 唯一事实源读取。
    """
    _log.info(
        "[Step5][diag] _sum_durations 入口: intents=%d project_name=%r",
        len(intents),
        project_name,
    )
    duration_total = 0.0
    for c in intents:
        try:
            dur = (
                c.get("duration", 0)
                or c.get("elapsed", 0)
                or c.get("verification_time", 0)
                or 0
            )
            # ★ duration 可能是 datetime.timedelta 类型，需先转 total_seconds
            if hasattr(dur, "total_seconds"):
                dur = dur.total_seconds()
            elif isinstance(dur, datetime):
                # datetime.datetime 不是有效的 duration（是时间点），跳过
                dur = 0
            duration_total += float(dur)
        except (ValueError, TypeError) as _dur_err:
            _log.warning("[Step5] duration 解析失败: %s", _dur_err)
    _log.info("[Step5][diag] intents 循环后 duration_total=%.1fs", duration_total)
    # ★ v5.0 fallback: intents 无 duration 时从 pipeline_checkpoints 读取
    if duration_total == 0.0 and project_name:
        _log.info("[Step5][diag] 进入 fallback: project_name=%r", project_name)
        with guard("_sum_durations: from src.core.db_manager import get_", log=_log, level=logging.WARNING):
            from src.core.db_manager import get_connection as _gc

            _conn = _gc()
            try:
                rows = (
                    _conn.execute(
                        "SELECT started_at, completed_at FROM pipeline_checkpoints "
                        "WHERE project=%s AND status='success' AND completed_at IS NOT NULL",
                        (project_name,),
                    ).fetchall()
                    or []
                )
                _log.info(
                    "[Step5][diag] pipeline_checkpoints 查询返回 %d 行", len(rows)
                )
                for row in rows:
                    sa = row.get("started_at")
                    ca = row.get("completed_at")
                    if sa and ca:
                        # ★ (ca - sa) 返回 timedelta，有 total_seconds() 方法
                        delta = (ca - sa).total_seconds()
                        duration_total += float(delta)
                if duration_total > 0:
                    _log.info(
                        "[Step5] duration 从 pipeline_checkpoints 读取: %.1fs",
                        duration_total,
                    )
            finally:
                with guard("_sum_durations: _conn.close()", log=_log, level=logging.WARNING):
                    _conn.close()
    else:
        _log.info(
            "[Step5][diag] 跳过 fallback: duration_total=%.1f project_name=%r",
            duration_total,
            project_name,
        )
    _log.info("[Step5][diag] _sum_durations 出口: 返回 %.1fs", duration_total)
    return duration_total


def _load_ai_stats() -> dict[str, Any]:
    """读取 AI 调用统计（模型、Token、费用）。失败返回空 dict。"""
    try:
        from src.ai.ai_client import ai_brain

        return ai_brain.get_stats(ai_brain._model)
    except Exception as _ai_err:
        _log.debug("[Step5] 读取 AI 统计失败: %s", str(_ai_err)[:80])
        return {}


def _build_summary_ext(
    project_name: str,
    timestamp: str,
    confidence_summary: dict[str, Any],
    catalog_stats: dict[str, Any],
    ai_stats: dict[str, Any],
    self_eval_metrics: dict[str, Any] = None,
) -> dict[str, Any]:
    """构建 run_summary.summary_json 扩展字段。"""
    _ext = {
        # ★ P2: MySQL doc URI 走 constants 集中点（07-06 改为 generated_reports）
        "report_path": "%s%s/%s" % (MYSQL_DOC_URI_REPORT, project_name, timestamp),
        "confidence_factors": confidence_summary.get("factor_avg", {}),
        "catalog_count": len(catalog_stats),
        # ★ v7: 证据引擎指标（渐进增强，旧前端忽略未知字段）
        "evidence": {
            "calibrated_confidence": confidence_summary.get(
                "evidence_calibrated_confidence"
            ),
            "ai_raw_confidence": confidence_summary.get(
                "evidence_ai_raw_confidence"
            ),  # ★ L1
            "coverage": confidence_summary.get("evidence_coverage", 0.0),
            "cold_start_count": confidence_summary.get("evidence_cold_start_count", 0),
            "calibration_samples": confidence_summary.get(
                "evidence_calibration_samples", 0
            ),  # ★ M3
            "verdict_distribution": confidence_summary.get(
                "evidence_verdict_distribution", {}
            ),  # ★ L3
        },
        # ★ 07-08: AI 调用统计（模型、Token、费用）
        "ai_stats": {
            "total_calls": ai_stats.get("total_calls", 0),
            "total_prompt_tokens": ai_stats.get("total_prompt_tokens", 0),
            "total_completion_tokens": ai_stats.get("total_completion_tokens", 0),
            "total_cost_usd": ai_stats.get("total_cost_usd", 0.0),
            "total_cost_cny": ai_stats.get("total_cost_cny", 0.0),
            "model": ai_stats.get("model", "unknown"),
            "exchange_rate": ai_stats.get("exchange_rate", 7.2),
        },
    }
    # ★ P2-12(2026-07-25): 引擎自评估指标（渐进增强，有评估数据时才写入）
    if self_eval_metrics and self_eval_metrics.get("evaluated", 0) > 0:
        _ext["self_eval"] = {
            "total": int(self_eval_metrics.get("total", 0)),
            "evaluated": int(self_eval_metrics.get("evaluated", 0)),
            "correct": int(self_eval_metrics.get("correct", 0)),
            "false_pass": int(self_eval_metrics.get("false_pass", 0)),
            "false_fail": int(self_eval_metrics.get("false_fail", 0)),
            "accuracy": float(self_eval_metrics.get("accuracy", 0.0) or 0.0),
            "false_pass_rate": float(
                self_eval_metrics.get("false_pass_rate", 0.0) or 0.0
            ),
            "false_fail_rate": float(
                self_eval_metrics.get("false_fail_rate", 0.0) or 0.0
            ),
            "coverage": float(self_eval_metrics.get("coverage", 0.0) or 0.0),
        }
    return _ext


def _build_run_summary_params(
    project_name: str,
    version_id: int,
    run_id: str,
    run_user_id: str,
    timestamp: str,
    total: int,
    executed_count: int,
    passed: int,
    failed: int,
    skipped: int,
    pending: int,
    flaky: int,
    pass_rate_value: float,
    confidence_summary: dict[str, Any],
    duration_total: float,
    summary_ext: dict[str, Any],
) -> tuple:
    """构建 run_summary INSERT 参数元组。"""
    return (
        project_name,
        str(version_id),
        run_id,
        run_user_id,  # ★ 方案B: user_id 字段
        timestamp,
        total,
        executed_count,
        passed,
        failed,
        skipped,
        pending,
        flaky,
        pass_rate_value,
        float(confidence_summary.get("avg_confidence", 0) or 0),
        int(confidence_summary.get("low_confidence_count", 0) or 0),
        float(confidence_summary.get("op_cache_hit_rate", 0) or 0),
        float(confidence_summary.get("capability_hit_rate", 0) or 0),
        duration_total,
        _json.dumps(summary_ext, ensure_ascii=False, default=str),
        float(confidence_summary.get("evidence_calibrated_confidence", 0) or 0),
        float(confidence_summary.get("evidence_coverage", 0) or 0),
        int(confidence_summary.get("evidence_cold_start_count", 0) or 0),
    )


# run_summary INSERT SQL（带 ON DUPLICATE KEY UPDATE）
_RUN_SUMMARY_INSERT_SQL = """
INSERT INTO run_summary(
    project, version_id, run_id, user_id, report_version,
    total_cases, executed_cases, total_passed, total_failed,
    total_skipped, total_pending, total_flaky, pass_rate,
    avg_confidence, low_confidence_count,
    op_cache_hit_rate, capability_hit_rate,
    total_duration_sec, summary_json,
    evidence_calibrated_confidence, evidence_coverage, evidence_cold_start,
    created_at, updated_at
) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,NOW(),NOW())
ON DUPLICATE KEY UPDATE
    user_id=VALUES(user_id),
    version_id=VALUES(version_id),
    report_version=VALUES(report_version),
    total_cases=VALUES(total_cases),
    executed_cases=VALUES(executed_cases),
    total_passed=VALUES(total_passed),
    total_failed=VALUES(total_failed),
    total_skipped=VALUES(total_skipped),
    total_pending=VALUES(total_pending),
    total_flaky=VALUES(total_flaky),
    pass_rate=VALUES(pass_rate),
    avg_confidence=VALUES(avg_confidence),
    low_confidence_count=VALUES(low_confidence_count),
    op_cache_hit_rate=VALUES(op_cache_hit_rate),
    capability_hit_rate=VALUES(capability_hit_rate),
    total_duration_sec=VALUES(total_duration_sec),
    summary_json=VALUES(summary_json),
    evidence_calibrated_confidence=VALUES(evidence_calibrated_confidence),
    evidence_coverage=VALUES(evidence_coverage),
    evidence_cold_start=VALUES(evidence_cold_start),
    updated_at=NOW()
"""
