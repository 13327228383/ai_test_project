# -*- coding: utf-8 -*-
"""
Tool Schema 定义与映射（facade）
=================================
纯数据定义已提取到 ._tool_definitions，本模块保留映射逻辑，
对外暴露与原来完全一致的公共 API。
"""


from ._tool_definitions import _make_schema, ALL_TOOLS, _TOOL_PREFIX_MAP  # noqa: F401


def build_tool_to_action_map() -> dict[str, str]:
    """根据 ALL_TOOLS 和 _TOOL_PREFIX_MAP 生成完整的 tool_name → action_type 映射。
    新增工具时只需在 ALL_TOOLS 中定义，并确保 _TOOL_PREFIX_MAP 中有对应前缀规则。
    """
    mapping = {}
    for tool in ALL_TOOLS:
        fn = tool.get("function", {})
        name = fn.get("name", "")
        if not name:
            continue
        # 精确匹配
        if name in _TOOL_PREFIX_MAP:
            mapping[name] = _TOOL_PREFIX_MAP[name]
            continue
        # 前缀匹配（如 browser_select_option → select_option）
        for prefix, action in _TOOL_PREFIX_MAP.items():
            if name.startswith(prefix):
                mapping[name] = action
                break
    return mapping


# 预计算映射表（模块加载时一次性生成）
TOOL_TO_ACTION_MAP: dict[str, str] = build_tool_to_action_map()
