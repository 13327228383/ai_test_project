# -*- coding: utf-8 -*-
"""
浏览器工具调用层 (BrowserTools)
================================

定位：意图驱动 + 标准化工具调用的融合架构
- 把浏览器原子操作暴露为工具 (function/tool)
- 每种工具声明 JSON Schema（必填/类型/enum）
- 外部通过工具列表执行 → 结果回传

对外 API:
- get_tool_schema(name)
- tool_name_list()
- validate_tool_call(dict)
- execute_tool_calls(list, engine)
- build_text_mode_tool_description()
- parse_text_mode_tool_calls(str)

本模块无步骤驱动残留：所有操作由业务意图触发，而非硬编码脚本。
"""

from src.pipeline.steps.step4.browser_tools.schemas import ALL_TOOLS, TOOL_TO_ACTION_MAP
from src.pipeline.steps.step4.browser_tools.query import (
    get_tool_schema,
    tool_name_list,
    build_tool_schemas,
)
from src.pipeline.steps.step4.browser_tools.validation import (
    SchemaValidationError,
    validate_tool_call,
)
from src.pipeline.steps.step4.browser_tools.execution import (
    ToolExecutionResult,
    execute_tool_calls,
)
from src.pipeline.steps.step4.browser_tools.text_mode import (
    build_text_mode_tool_description,
    parse_text_mode_tool_calls,
)

__all__ = [
    "ALL_TOOLS",
    "ToolExecutionResult",
    "SchemaValidationError",
    "validate_tool_call",
    "execute_tool_calls",
    "build_tool_schemas",
    "tool_name_list",
    "get_tool_schema",
    "build_text_mode_tool_description",
    "parse_text_mode_tool_calls",
    "TOOL_TO_ACTION_MAP",
]
