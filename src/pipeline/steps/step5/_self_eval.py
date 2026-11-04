# -*- coding: utf-8 -*-
"""P2-12 SelfEval 接入点 — 引擎自评估章节生成。

从 report_generator.py 拆出（§4.3 单文件≤800行 + 类级职责分离）。

职责边界（§13.5）：
- 从 executions 表查询实际 verdict/confidence
- 调用 SelfEvalRunner 对比 golden_set
- 返回 (markdown_section, eval_metrics) 供报告生成 + run_summary 持久化

通用性（§12.4）：
- 纯数据查询 + 评估，不绑定业务术语
- 适用于所有项目/行业/页面
- 降级：查询/评估失败返回 ("", {})，不阻断报告生成
"""
from __future__ import annotations

import logging
from typing import Any

_log = logging.getLogger(__name__)


def _load_actual_results(project_name: str) -> dict[str, dict[str, Any]]:
    """从 executions 表查询最新实际 verdict/confidence，按 case_id 去重。

    Returns:
        {case_id: {"actual_verdict": str, "actual_confidence": float}}
        查询失败返回空 dict（降级，不阻断）。
    """
    from src.models.base import get_conn as _get_conn

    _se_conn = None
    try:
        _se_conn = _get_conn()
        _se_rows = _se_conn.execute(
            "SELECT case_id, status, confidence FROM executions "
            "WHERE project=%s AND status IN ('PASS','FAIL') "
            "ORDER BY executed_at DESC",
            (project_name,),
        ).fetchall()
    except Exception as _qe:
        _log.warning("[SelfEval] executions 查询失败(降级): %s", str(_qe)[:120])
        return {}
    finally:
        if _se_conn:
            try:
                _se_conn.close()
            except Exception as _ce:
                _log.debug("[SelfEval] conn.close: %s", _ce, exc_info=True)

    if not _se_rows:
        return {}

    _se_actual: dict[str, dict[str, Any]] = {}
    for _r in _se_rows:
        _cid = _r["case_id"] if isinstance(_r, dict) else _r[0]
        if _cid in _se_actual:
            continue  # 已有最新记录，跳过旧记录（ORDER BY executed_at DESC）
        _st = (_r["status"] if isinstance(_r, dict) else _r[1] or "").upper()
        _conf = 0.0
        try:
            _conf = float(_r["confidence"] if isinstance(_r, dict) else _r[2] or 0)
        except (TypeError, ValueError) as _ce:
            _log.debug(
                "[SelfEval] confidence 类型异常(降级为0.0): %s", _ce, exc_info=True
            )
        _se_actual[_cid] = {"actual_verdict": _st, "actual_confidence": _conf}
    return _se_actual


def attach_self_eval_section(
    project_name: str,
) -> tuple[str, dict[str, Any]]:
    """生成引擎自评估章节 + 返回评估指标。

    参数:
        project_name: 项目名

    返回:
        (markdown_section, eval_metrics)
        - markdown_section: 追加到报告 lines 的 Markdown 字符串（无评估数据时为空串）
        - eval_metrics: 评估指标 dict（accuracy/false_pass_rate 等），供写入 run_summary.summary_json
        降级：任何异常返回 ("", {})
    """
    try:
        from src.eval.runner import SelfEvalRunner

        _se_actual = _load_actual_results(project_name)
        if not _se_actual:
            _log.debug("[SelfEval] 无 PASS/FAIL 执行数据，跳过自评估章节")
            return "", {}
        _se_runner = SelfEvalRunner()
        _se_result = _se_runner.run(project_name, _se_actual)
        if _se_result.get("evaluated", 0) <= 0:
            return "", {}
        _markdown = _se_runner.format_report(_se_result)
        # 提取关键指标供持久化（渐进增强，旧前端忽略未知字段）
        _metrics = {
            "total": int(_se_result.get("total", 0)),
            "evaluated": int(_se_result.get("evaluated", 0)),
            "correct": int(_se_result.get("correct", 0)),
            "false_pass": int(_se_result.get("false_pass", 0)),
            "false_fail": int(_se_result.get("false_fail", 0)),
            "accuracy": float(_se_result.get("accuracy", 0.0) or 0.0),
            "false_pass_rate": float(_se_result.get("false_pass_rate", 0.0) or 0.0),
            "false_fail_rate": float(_se_result.get("false_fail_rate", 0.0) or 0.0),
            "coverage": float(_se_result.get("coverage", 0.0) or 0.0),
        }
        return _markdown, _metrics
    except Exception as _se_err:
        _log.warning(
            "[SelfEval] 自评估章节生成失败(降级): %s", str(_se_err)[:120], exc_info=True
        )
        return "", {}
