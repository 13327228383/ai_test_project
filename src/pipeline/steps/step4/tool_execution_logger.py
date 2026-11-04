# -*- coding: utf-8 -*-
"""
融合架构工具执行日志持久化存储
=================================
将工具调用链路追踪数据写入 MySQL，支持性能分析和问题排查。

使用方式:
    from src.pipeline.steps.step4.tool_execution_logger import tool_logger
    tool_logger.log(project, case_id, step_desc, trace_id, tool_name, args, result, model)
    stats = tool_logger.get_stats(project)
"""

import json
import logging
from typing import Any

_log = logging.getLogger(__name__)


class ToolExecutionLogger:
    """工具执行日志持久化存储（MySQL）。"""

    def _conn(self):
        from src.models.base import get_conn

        return get_conn()

    def log(
        self,
        project: str,
        case_id: str,
        step_desc: str,
        trace_id: str,
        tool_name: str,
        tool_args: dict,
        result_ok: bool,
        result_content: str,
        elapsed_ms: int = 0,
        atomic_rollback: bool = False,
        tool_mode: bool = False,
        model: str = "",
    ) -> bool:
        """记录单个工具执行日志。"""
        try:
            args_json = json.dumps(tool_args, ensure_ascii=False) if tool_args else None
            c = self._conn()
            c.execute(
                """INSERT INTO tool_execution_log
                   (project, case_id, step_desc, trace_id, tool_name, tool_args_json,
                    result_ok, result_content, elapsed_ms, atomic_rollback, tool_mode, model, created_at)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, NOW())""",
                (
                    project,
                    case_id,
                    step_desc,
                    trace_id,
                    tool_name,
                    args_json,
                    1 if result_ok else 0,
                    result_content[:500],
                    elapsed_ms,
                    1 if atomic_rollback else 0,
                    1 if tool_mode else 0,
                    model,
                ),
            )
            c.commit()
            c.close()
            return True
        except Exception as e:
            _log.warning("[ToolLogger] log 失败: %s", e)
            return False

    def log_batch(
        self,
        project: str,
        case_id: str,
        step_desc: str,
        results: list[Any],
        model: str = "",
    ) -> bool:
        """批量记录工具执行日志（从 execute_tool_calls 的结果列表）。"""
        try:
            for r in results:
                extra = r.extra or {}
                self.log(
                    project=project,
                    case_id=case_id,
                    step_desc=step_desc,
                    trace_id=extra.get("_trace_id", ""),
                    tool_name=extra.get("_tool_name", ""),
                    tool_args=extra.get("_tool_args", {}),
                    result_ok=r.ok,
                    result_content=r.content,
                    elapsed_ms=extra.get("_elapsed_ms", 0),
                    atomic_rollback=extra.get("_atomic_rollback", False),
                    tool_mode=extra.get("_tool_mode", False),
                    model=model,
                )
            return True
        except Exception as e:
            _log.warning("[ToolLogger] batch log 失败: %s", e)
            return False

    def get_stats(self, project: str, days: int = 7) -> dict[str, Any]:
        """获取项目级工具执行统计。"""
        c = None
        try:
            c = self._conn()
            # 总体统计
            row = c.execute(
                """SELECT COUNT(*) as total,
                          SUM(CASE WHEN result_ok=1 THEN 1 ELSE 0 END) as success,
                          SUM(CASE WHEN tool_mode=1 THEN 1 ELSE 0 END) as tool_mode_count,
                          SUM(CASE WHEN atomic_rollback=1 THEN 1 ELSE 0 END) as rollback_count,
                          AVG(elapsed_ms) as avg_elapsed
                   FROM tool_execution_log
                   WHERE project=%s AND created_at >= DATE_SUB(NOW(), INTERVAL %s DAY)""",
                (project, days),
            ).fetchone()

            # 各工具统计
            tool_rows = c.execute(
                """SELECT tool_name,
                          COUNT(*) as count,
                          AVG(elapsed_ms) as avg_elapsed,
                          SUM(CASE WHEN result_ok=1 THEN 1 ELSE 0 END) * 100.0 / COUNT(*) as success_rate
                   FROM tool_execution_log
                   WHERE project=%s AND created_at >= DATE_SUB(NOW(), INTERVAL %s DAY)
                   GROUP BY tool_name
                   ORDER BY count DESC""",
                (project, days),
            ).fetchall()

            total = int(row["total"] or 0)
            return {
                "total_calls": total,
                "success_calls": int(row["success"] or 0),
                "tool_mode_calls": int(row["tool_mode_count"] or 0),
                "rollback_count": int(row["rollback_count"] or 0),
                "avg_elapsed_ms": round(float(row["avg_elapsed"] or 0), 2),
                "tool_breakdown": [
                    {
                        "tool_name": r["tool_name"],
                        "count": int(r["count"]),
                        "avg_elapsed_ms": round(float(r["avg_elapsed"] or 0), 2),
                        "success_rate": round(float(r["success_rate"] or 0), 2),
                    }
                    for r in tool_rows
                ],
            }
        except Exception as e:
            _log.warning("[ToolLogger] stats 失败: %s", e)
            return {
                "total_calls": 0,
                "success_calls": 0,
                "tool_mode_calls": 0,
                "rollback_count": 0,
                "avg_elapsed_ms": 0,
                "tool_breakdown": [],
            }
        finally:
            if c:
                try:
                    c.close()
                except Exception as _e:
                    _log.debug(
                        "[tool_execution_logger] 非关键异常: %s", _e, exc_info=True
                    )


# ★ 方案C 改造：tool_logger 改为 PerProjectRegistry，按 project 隔离
from src.core.utils.per_project_registry import PerProjectRegistry

tool_logger = PerProjectRegistry(ToolExecutionLogger)
