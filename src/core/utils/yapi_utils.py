# -*- coding: utf-8 -*-
from __future__ import annotations

"""YApi 接口文档解析工具 — 统一的端点提取和格式化。

rag_engine / diff_engine / traceability_model 中各有独立实现，
本模块提供唯一实现。"""

import json
import logging
import re
from typing import Any

_log = logging.getLogger(__name__)


def parse_yapi_endpoints(yapi_data: Any) -> list[dict]:
    """解析 YApi JSON 结构，提取所有 API 端点列表。

    Returns:
        [{method, path, title, description, params, responses}, ...]
    """
    if isinstance(yapi_data, str):
        try:
            yapi_data = json.loads(yapi_data)
        except json.JSONDecodeError:
            return []

    endpoints = []

    def _extract(obj, depth=0):
        if depth > 10:
            return
        if isinstance(obj, dict):
            # YApi 标准格式
            if "method" in obj and "path" in obj:
                endpoints.append(
                    {
                        "method": str(obj.get("method", "GET")).upper(),
                        "path": str(obj.get("path", "")),
                        "title": str(obj.get("title", "")),
                        "description": str(obj.get("desc", "")),
                    }
                )
            # 嵌套在 list/cat 中
            for v in obj.values():
                _extract(v, depth + 1)
        elif isinstance(obj, list):
            for item in obj:
                _extract(item, depth + 1)

    _extract(yapi_data)
    return endpoints


def extract_api_list(yapi_data: Any) -> list[str]:
    """提取 API 路径列表（仅路径，不含方法）。"""
    endpoints = parse_yapi_endpoints(yapi_data)
    return sorted(set(e["path"] for e in endpoints))


# ══════════════════════════════════════════════════
# ★ P1-3: 响应 Schema 解析
# ══════════════════════════════════════════════════


def extract_response_schemas(yapi_data) -> dict:
    """
    从 YApi 文档中提取每个接口的响应 Schema。
    返回: {"/api/brand/list": {"code_field": "code", "required_fields": ["data"], "status_ok": [0, 200]}}
    """
    if isinstance(yapi_data, str):
        try:
            yapi_data = json.loads(yapi_data)
        except json.JSONDecodeError:
            return {}

    schemas = {}

    def _collect(obj, depth=0):
        if depth > 10:
            return
        if isinstance(obj, dict):
            if "method" in obj and "path" in obj:
                path = obj.get("path", "")
                res_body = obj.get("res_body", "")
                if res_body and path:
                    schema = _parse_res_body(res_body)
                    if schema:
                        schemas[path] = schema
            for v in obj.values():
                _collect(v, depth + 1)
        elif isinstance(obj, list):
            for item in obj:
                _collect(item, depth + 1)

    _collect(yapi_data)
    return schemas


def _parse_res_body(res_body) -> dict | None:
    """
    解析 res_body（可能是 JSON Schema 或示例 JSON）。
    返回: {"code_field": "...", "required_fields": [...], "status_ok": [...]}
    """
    if not res_body:
        return None

    # 如果是字符串，尝试解析为 JSON
    if isinstance(res_body, str):
        try:
            res_body = json.loads(res_body)
        except (json.JSONDecodeError, ValueError) as _e:
            _log.debug("[yapi_utils] 响应体 JSON 解析失败(§5): %s", _e)
            return None

    if not isinstance(res_body, dict):
        return None

    # 检测是否为 JSON Schema 格式（有 properties）
    if "properties" in res_body:
        props = res_body.get("properties", {})
        # 查找业务状态码字段
        code_field = None
        required_fields = list(res_body.get("required", []))
        for field in ("code", "status", "errCode", "errorCode", "retCode"):
            if field in props:
                code_field = field
                break
        if not code_field:
            # 检查 data 字段是否存在
            if "data" in props or "result" in props:
                code_field = "code"  # 默认业务码字段
        return {
            "code_field": code_field,
            "required_fields": required_fields,
            "status_ok": [0, 200],
        }

    # 检测是否为示例 JSON
    code_field = None
    for field in ("code", "status", "errCode", "errorCode", "retCode"):
        if field in res_body:
            code_field = field
            break
    if code_field:
        # 从示例值推断成功值
        success_val = res_body.get(code_field)
        status_ok = [success_val] if success_val is not None else [0]
        return {
            "code_field": code_field,
            "required_fields": [k for k in res_body if k != code_field],
            "status_ok": status_ok,
        }

    return None
