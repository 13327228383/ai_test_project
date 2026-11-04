# -*- coding: utf-8 -*-
from __future__ import annotations

"""API Checks 校验 + 自然语言提取工具 — 从 trace_validator.py 拆出，零业务逻辑变更。"""

import re
from typing import Any


class _ApiAndExtractMixin:
    """_validate_api_checks + _extract_table_from_natural / _extract_fields_from_natural / _extract_api_path。"""

    # ══════════════════════════════════════════════════════════
    # API Checks 校验
    # ══════════════════════════════════════════════════════════

    def _validate_api_checks(
        self, api_checks: list[str], req_id: str = None
    ) -> dict[str, Any]:
        """校验 api_checks 列表。"""
        result = {"valid": True, "issues": [], "warnings": []}

        for check in api_checks:
            if not isinstance(check, str):
                continue

            # 提取 API 路径
            api_path = self._extract_api_path(check)
            if not api_path:
                continue

            # 校验路径是否存在
            if api_path not in self._all_apis:
                similar = self._find_similar_api(api_path)
                if similar:
                    result["warnings"].append(
                        "API路径 '%s' 未找到，您是否指的是 '%s'？" % (api_path, similar)
                    )
                else:
                    result["warnings"].append(
                        "API路径 '%s' 未在追溯链路中找到" % api_path
                    )

        return result

    # ══════════════════════════════════════════════════════════
    # 提取工具函数
    # ══════════════════════════════════════════════════════════

    @staticmethod
    def _extract_table_from_natural(text: str) -> str | None:
        """从自然语言描述中提取表名。"""
        # 模式1: "XXX表" 或 "表XXX"
        m = re.search(r"(\w+)\s*表", text)
        if m:
            return m.group(1)
        m = re.search(r"表\s*(\w+)", text)
        if m:
            return m.group(1)
        # 模式2: FROM xxx
        m = re.search(r"FROM\s+(\w+)", text, re.IGNORECASE)
        if m:
            return m.group(1)
        return None

    @staticmethod
    def _extract_fields_from_natural(text: str) -> list[str]:
        """从自然语言描述中提取字段名。"""
        # 模式: "字段名=" 或 "字段名 ="
        fields = [m.group(1) for m in re.finditer(r"(\w+)\s*=", text)]
        return fields

    @staticmethod
    def _extract_api_path(text: str) -> str | None:
        """从 api_check 字符串中提取 API 路径。"""
        # 模式: GET/POST/PUT/DELETE /api/xxx
        m = re.search(r"(?:GET|POST|PUT|DELETE|PATCH)\s+(/\S+)", text, re.IGNORECASE)
        if m:
            return m.group(1)
        # 模式: /api/xxx
        m = re.search(r"(/api/\S+)", text)
        if m:
            return m.group(1)
        return None
