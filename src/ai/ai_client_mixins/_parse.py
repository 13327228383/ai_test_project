# -*- coding: utf-8 -*-
"""
AiBrain 响应解析 Mixin
=====================
提供 JSON 响应解析等辅助静态方法。
"""

from typing import Any

from src.ai.json_parser import parse_json_response


class _ParseMixin:
    """响应解析辅助方法。"""

    @staticmethod
    def _parse_json_response(raw: str) -> dict[str, Any]:
        """
        解析 AI 返回的原始文本中的 JSON 内容。
        健壮处理 LLM 常见格式问题：markdown 包裹、尾逗号、注释、截断。

        返回：
            {"success": True, "data": <parsed_json>}
            或 {"success": False, "error": "..."}
        """
        return parse_json_response(raw)
