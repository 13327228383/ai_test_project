# -*- coding: utf-8 -*-
"""
Tool 执行器 (package facade)
============================
- 批量执行 tool_calls
- 单个工具的执行实现
- ToolExecutionResult 结果封装
"""

from ._result import ToolExecutionResult
from ._batch import execute_tool_calls
from ._single import _execute_tool

__all__ = ["ToolExecutionResult", "execute_tool_calls", "_execute_tool"]
