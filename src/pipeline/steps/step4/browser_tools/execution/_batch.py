# -*- coding: utf-8 -*-
from __future__ import annotations
from src.core.guarded import guard

"""
批量执行 tool_calls
===================
"""

import logging
import time
from typing import Any

from ._result import ToolExecutionResult
from ..validation import validate_tool_call
from ._single import _execute_tool  # noqa: F401

_log = logging.getLogger(__name__)


def _log_tool_batch(trace_id: str, results: list[ToolExecutionResult]) -> None:
    """将本次实际执行的工具调用写入 tool_execution_log（供 Tool-mode 率等 fusion 指标统计）。

    ★ 根因修复(2026-09-20)：工具执行记录原来只挂在 action_dispatchers 手动 _log_tool_execution
    一条路径上；走 execute_tool_calls 的 AI 执行路径从未落库 → fusion 的 tool_mode/atomic_rollback
    依赖 tool_logger.get_stats(total_calls>0) 恒为 0 → 融合架构面板全空（"做了没用上"）。
    这里在唯一环节点统一补记：到达 execute_tool_calls = 真实工具执行（非缓存回放），
    tool-mode 计为 True，与 capability 计数互为一致来源。异常一律不阻塞。
    """
    with guard("_log_tool_batch: from src.core.config_loader import c", log=_log):
        from src.core.config_loader import config
        from src.pipeline.steps.step4.tool_execution_logger import tool_logger

        executed = []
        for r in results:
            if not getattr(r, "extra", None) or r.extra.get("_atomic_rollback"):
                continue
            r.extra.setdefault("_tool_mode", True)
            executed.append(r)
        if executed:
            tool_logger.log_batch(config.project_name, "", str(trace_id), executed)


async def execute_tool_calls(
    tool_calls: list[dict[str, Any]],
    engine,
    trace_id: str | None = None,
    atomic: bool = False,
) -> list[ToolExecutionResult]:
    """批量执行多个 tool_call。
    参数:
        tool_calls: 工具调用列表
        engine: Playwright 引擎实例
        trace_id: 可选链路追踪 ID（用于关联同一批调用）
        atomic: 是否原子执行。True 时任一工具失败则停止后续执行并标记 _atomic_rollback
    返回:
        ToolExecutionResult 列表，每个结果包含 extra 字段:
        - _tool_name: 工具名
        - _tool_args: 参数摘要
        - _elapsed_ms: 执行耗时(ms)
        - _trace_id: 链路追踪 ID
        - _atomic_rollback: 是否因原子性被跳过
    """
    results = []
    _tid = trace_id or ("tc_" + str(int(time.time() * 1000)))
    _rollback = False  # 原子性回滚标记
    for idx, tc in enumerate(tool_calls):
        ok, err, tool_name, args = validate_tool_call(tc)
        if not ok:
            results.append(
                ToolExecutionResult(
                    False,
                    "Schema 校验失败: " + str(err),
                    {
                        "_tool_name": tool_name,
                        "_tool_args": {},
                        "_elapsed_ms": 0,
                        "_trace_id": _tid,
                        "_index": idx,
                    },
                )
            )
            if atomic:
                _rollback = True
            continue

        # 原子模式下，前面已有失败则跳过
        if atomic and _rollback:
            results.append(
                ToolExecutionResult(
                    False,
                    "原子性回滚: 前面工具执行失败，跳过此工具",
                    {
                        "_tool_name": tool_name,
                        "_tool_args": {},
                        "_elapsed_ms": 0,
                        "_trace_id": _tid,
                        "_index": idx,
                        "_atomic_rollback": True,
                    },
                )
            )
            continue

        _t0 = time.time()
        result = await _execute_tool(tool_name, args, engine)
        _elapsed_ms = int((time.time() - _t0) * 1000)

        # 注入链路追踪元数据
        result.extra = result.extra or {}
        result.extra.update(
            {
                "_tool_name": tool_name,
                "_tool_args": {
                    k: (v[:50] if isinstance(v, str) else v)
                    for k, v in (args or {}).items()
                },
                "_elapsed_ms": _elapsed_ms,
                "_trace_id": _tid,
                "_index": idx,
            }
        )
        results.append(result)

        # 原子模式下，当前工具失败则触发回滚
        if atomic and not result.ok:
            _rollback = True

    _log_tool_batch(_tid, results)
    return results
