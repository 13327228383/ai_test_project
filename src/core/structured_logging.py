# -*- coding: utf-8 -*-
"""
★ KEEP-33: 结构化日志基础设施。

默认不改变现有日志格式；当环境变量 STRUCTURED_LOGS=1 时，
将根 logger 的 handler formatter 切换为 JSON 行格式，便于后续
ELK/Loki/ClickHouse 等日志平台采集。

增强功能 (v2):
  - trace_id 上下文变量，贯穿请求/任务的全链路追踪
  - pipeline_log_id 上下文变量，标识流水线执行批次
  - StructuredAuditLogger：带 MySQL 持久化的审计日志器
  - log_pipeline_event：流水线事件结构化日志
"""

from __future__ import annotations

import json
import logging
import os
import time
from contextvars import ContextVar
from typing import Any

# ---------------------------------------------------------------------------
# 上下文变量：trace_id / pipeline_log_id
# ---------------------------------------------------------------------------

_trace_id: ContextVar[str | None] = ContextVar("_trace_id", default=None)
_pipeline_log_id: ContextVar[str | None] = ContextVar("_pipeline_log_id", default=None)


def get_trace_id() -> str | None:
    """获取当前执行上下文的 trace_id。"""
    return _trace_id.get()


def set_pipeline_log_id(pipeline_log_id: str) -> None:
    """设置当前执行上下文的 pipeline_log_id。"""
    _pipeline_log_id.set(pipeline_log_id)


def get_pipeline_log_id() -> str | None:
    """获取当前执行上下文的 pipeline_log_id。"""
    return _pipeline_log_id.get()


# ---------------------------------------------------------------------------
# JSON 日志格式化器
# ---------------------------------------------------------------------------


class JsonLogFormatter(logging.Formatter):
    """将 LogRecord 格式化为单行 JSON。"""

    def __init__(self) -> None:
        super().__init__()

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(record.created)),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "module": record.module,
            "func": record.funcName,
            "line": record.lineno,
        }

        # 注入 trace_id（若当前上下文已设置）
        tid = _trace_id.get()
        if tid is not None:
            payload["trace_id"] = tid

        # 注入 pipeline_log_id（若当前上下文已设置）
        pid = _pipeline_log_id.get()
        if pid is not None:
            payload["pipeline_log_id"] = pid

        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)

        return json.dumps(payload, ensure_ascii=False)


# ---------------------------------------------------------------------------
# 结构化日志启用入口
# ---------------------------------------------------------------------------


def configure_structured_logging(force: bool = True) -> bool:
    """
    启用结构化日志。返回是否已启用。

    默认为 force=True，始终启用 JSON 日志格式。
    若 force=False，则按 STRUCTURED_LOGS 环境变量判断（向后兼容）。
    """
    enabled = force or os.environ.get("STRUCTURED_LOGS", "").lower() in (
        "1",
        "true",
        "yes",
        "on",
    )
    if not enabled:
        return False
    formatter = JsonLogFormatter()
    root = logging.getLogger()
    if not root.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(formatter)
        root.addHandler(handler)
    else:
        for handler in root.handlers:
            # ★ 只修改 StreamHandler（stdout/stderr），跳过 _SSELogHandler 等内存型 handler，
            # 避免覆盖 SSE 广播所需的 "%(message)s" 格式。
            if isinstance(handler, logging.StreamHandler):
                handler.setFormatter(formatter)
    root.info("structured_logging_enabled")
    return True
