# -*- coding: utf-8 -*-
"""★ §13.3 通用输入值生成器 — 从 yapi_structured 提取 body_params。

定位：
  当 rag_chunks 无 body_params 时，从 source_documents.yapi_structured 的
  req_body JSON Schema 解析字段名+类型，补充到 trace_data["apis"]。
  替代 _infer_fill_target_and_value 兜底分支的 test_value/test_{字段名}。

根因（§10.9）：
  1. 为什么 test_value？→ _rag_api_with_body 为空
  2. 为什么为空？→ rag_chunks 无 body_params（0条）
  3. 为什么 rag_chunks 无？→ Step1 未把 yapi_structured 切块存入 rag_chunks
  本模块绕过 rag_chunks 直接读 source_documents（MySQL 表，§4.3 合规）。

职责边界（§12.4）：
  - 仅做数据提取（读 source_documents + JSON 解析），不做用例建模
  - 降级：未找到/异常返回 0，不阻断主流程
  - 通用性：基于 JSON Schema type，不依赖业务术语

通用性（§12.4）：
  1. 跨系统：JSON Schema 是通用标准
  2. 跨行业：字段类型→合规值规则通用
  3. 跨页面：按 method+path 匹配接口，通用
  4. 降级：异常返回 0，不阻断
  5. 职责边界：仅补充 body_params，用例建模由 _rule_extractor 负责
"""
from __future__ import annotations

import json
import logging
from typing import Any

_log = logging.getLogger(__name__)


def enrich_apis_with_yapi(project: str, apis: list[dict[str, Any]]) -> int:
    """从 source_documents.yapi_structured 补充 apis 中缺失的 body_params。

    遍历 apis，对 body_params 为空的 api，按 (method, path) 匹配
    yapi_structured 的 interface，从 req_body JSON Schema 解析字段名+类型，
    格式化为 "field_name(type), ..." 补充到 api["body_params"]。

    参数:
        project: 项目名
        apis: API 列表，每项含 method/path/body_params

    返回: 补充成功的 API 数量（0=无补充/异常）
    """
    if not project or not apis:
        return 0
    _missing = [a for a in apis if not a.get("body_params")]
    if not _missing:
        return 0
    try:
        from src.core.db_manager import get_connection, release_connection

        conn = get_connection()
        try:
            row = conn.execute(
                "SELECT content FROM source_documents "
                "WHERE project=%s AND doc_type='yapi_structured' LIMIT 1",
                (project,),
            ).fetchone()
        finally:
            release_connection(conn)
        if not row or not row.get("content"):
            return 0
        data = (
            json.loads(row["content"])
            if isinstance(row["content"], str)
            else row["content"]
        )
        if not isinstance(data, dict):
            return 0
        interfaces = data.get("interfaces", [])
        if not interfaces:
            return 0
        # 构建 (method, path) → body_params 映射
        _iface_map: dict[tuple[str, str], str] = {}
        for iface in interfaces:
            if not isinstance(iface, dict):
                continue
            m = (iface.get("method") or "").upper()
            p = iface.get("path") or ""
            if not m or not p:
                continue
            req_body = iface.get("req_body", "")
            if not req_body:
                continue
            schema = json.loads(req_body) if isinstance(req_body, str) else req_body
            if not isinstance(schema, dict):
                continue
            props = schema.get("properties", {})
            if not isinstance(props, dict) or not props:
                continue
            parts = [
                f"{k}({v.get('type', 'string')})"
                for k, v in props.items()
                if isinstance(v, dict)
            ]
            if parts:
                _iface_map[(m, p)] = ", ".join(parts)
        if not _iface_map:
            return 0
        # 补充缺失 body_params 的 api
        count = 0
        for api in apis:
            if api.get("body_params"):
                continue
            m = (api.get("method") or "").upper()
            p = api.get("path") or ""
            bp = _iface_map.get((m, p), "")
            if bp:
                api["body_params"] = bp
                count += 1
        if count:
            _log.info(
                "[value_gen] yapi_structured 补充 body_params: %d/%d 个 API",
                count,
                len(_missing),
            )
        return count
    except Exception as e:
        _log.debug("[value_gen] enrich_apis_with_yapi 失败: %s", str(e)[:80])
        return 0


def generate_value_from_type(field_type: str, field_name: str = "") -> str:
    """根据 JSON Schema 字段类型生成合规输入值（通用，不依赖业务）。

    用于替代 test_value 硬编码。当无法从 yapi example 获取真实样例值时，
    根据字段类型生成类型合规的通用值。

    参数:
        field_type: JSON Schema type（string/integer/number/boolean）
        field_name: 字段名（用于语义推断，可选）

    返回: 合规输入值
    """
    ft = (field_type or "").lower()
    name = (field_name or "").lower()
    if ft in ("integer", "number", "int", "long", "float", "double"):
        # ★ §13.3 通用修复(2026-07-24 续): integer 也看 field_name 语义（金额/数量更合理）
        if any(k in name for k in ("amount", "金额", "价格", "price")):
            return "100"
        if any(k in name for k in ("count", "数量", "num", "qty")):
            return "10"
        return "1"
    if ft in ("boolean", "bool"):
        return "true"
    # string 及其他类型：基于字段名语义生成有意义的值
    if any(k in name for k in ("code", "编号", "编码")):
        return "TEST001"
    if any(k in name for k in ("name", "名称", "名字")):
        return "测试名称"
    if any(k in name for k in ("phone", "手机", "电话")):
        return "13800138000"
    if any(k in name for k in ("email", "邮箱")):
        return "test@example.com"
    # ★ §13.3 通用修复(2026-07-24 续): 增加密码/日期/地址/描述/url 语义
    if any(k in name for k in ("password", "密码", "口令")):
        return "Test@123456"
    if any(k in name for k in ("date", "日期", "时间")):
        return "2026-01-01"
    if any(k in name for k in ("address", "地址")):
        return "测试地址123号"
    if any(k in name for k in ("url", "链接", "网址")):
        return "https://example.com"
    if any(k in name for k in ("desc", "描述", "备注", "remark", "note")):
        return "自动化测试备注"
    return "测试数据"


MAX_LENGTH_OVERFLOW = 500


def generate_invalid_value_from_type(
    field_type: str,
    field_name: str = "",
    constraint: str = "type",
    max_length: int = 0,
) -> str:
    """按「要违反哪一条约束」生成非法值（用于 error/boundary_case）。

    与 generate_value_from_type 对称：正向生成合规值，反向生成违反约束的值。
    通用性：基于字段类型 + 约束种类生成，不依赖业务术语。

    参数:
        field_type: JSON Schema type（string/integer/number/boolean）
        field_name: 字段名（用于语义推断，可选）
        constraint: 违反的约束种类
            - "type"（默认）: 类型/格式不合规
            - "max_length": 超长（len = max_length+1，未给定时取 MAX_LENGTH_OVERFLOW+1）
            - "required": 必填缺失（空值）
        max_length: 目标字段的上限长度（仅 "max_length" 使用）

    返回: 违反指定约束的非法值
    """
    if constraint == "required":
        return ""
    if constraint == "max_length":
        return "A" * ((max_length or MAX_LENGTH_OVERFLOW) + 1)
    ft = (field_type or "").lower()
    if ft in ("integer", "number", "int", "long", "float", "double"):
        return "abc"  # 非数字字符串
    if ft in ("boolean", "bool"):
        return "not_boolean"  # 非布尔值
    # string 及其他类型：基于字段名语义生成非法值
    name = (field_name or "").lower()
    if any(k in name for k in ("email", "邮箱")):
        return "not_an_email"
    if any(k in name for k in ("phone", "手机", "电话")):
        return "abc123"
    if any(k in name for k in ("date", "日期", "时间")):
        return "invalid_date"
    if any(k in name for k in ("url", "链接")):
        return "not_a_url"
    # 通用 string 非法值：特殊字符（类型驱动，仅无特定语义的 string 使用）
    return "!@#"
