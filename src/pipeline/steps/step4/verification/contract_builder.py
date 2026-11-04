# -*- coding: utf-8 -*-
"""契约断言生成器（W3 T04，L0-4）—— 从 YApi / OpenAPI 响应 schema 生成 api 轨断言。

流程：
  load_yapi_schemas()：diff_snapshot_store.load_snapshot("api_schema") → extract_response_schemas()
  load_openapi_schemas(spec)：解析 OpenAPI 3.0 / Swagger 2.0 JSON/YAML → 提取响应 schema
  build_contract_checks(case, schemas)：为 case 的 api 轨生成
    api_contract / api_response_body 断言（仅当该轨尚无 body 级断言）。

完整 JSON Schema 验证（2026-08-12 增强）：
  build_contract_checks 生成的 api_contract 断言现包含 types/enums/formats，
  覆盖字段类型（string/int/bool/array/object）、枚举值、格式（email/uri/date-time）。
  validate_response_schema(body, schema) 使用 jsonschema 库做完整校验，
  库缺失时降级为基本字段检查（required_fields 存在性 + 类型粗校验）。

契约快照 diff（2026-08-12 新增）：
  diff_contract_schemas(old_schema, new_schema) 对比两个 schema 版本的 breaking changes
  （字段删除、类型变更、required 变更），供 CI 门禁检测不兼容变更。

零 DDL；CONTRACT_ENABLED 门控（默认 OFF → 零行为变更）。
"""
from __future__ import annotations
from src.core.guarded import guard

import logging
from typing import Any

from src.core.i18n import tr  # F6: 断言消息按区域切换（zh-CN 默认，零行为变更）

_log = logging.getLogger(__name__)

# ★ jsonschema 降级处理（2026-08-12）：库不存在时降级为基本字段检查
try:
    import jsonschema  # noqa: F401
    from jsonschema import Draft7Validator, ValidationError as _JSONSchemaError

    _HAS_JSONSCHEMA = True
except ImportError:  # pragma: no cover - 依赖缺失场景
    jsonschema = None
    Draft7Validator = None  # type: ignore[assignment,misc]

    class _JSONSchemaError(Exception):  # type: ignore[no-redef]
        """降级占位：jsonschema 缺失时不会真正抛出。"""

    _HAS_JSONSCHEMA = False

# api 轨「body 级断言」类型（已有则不再生成契约断言，避免重复）
_BODY_LEVEL_TYPES = {"api_response_body", "api_contract", "api_contract_check"}

# JSON Schema format → Python 校验提示名（用于 format 校验失败提示）
_FORMAT_NAMES = {
    "email": "email",
    "uri": "uri",
    "date-time": "date-time",
    "date": "date",
    "ipv4": "ipv4",
    "ipv6": "ipv6",
    "uuid": "uuid",
}


def load_yapi_schemas() -> dict:
    """diff_snapshot_store.load_snapshot("api_schema") → extract_response_schemas()。

    无数据/异常 → 返回 {}（CONTRACT_ENABLED 时记 warning 不阻断）。
    返回 {path: {"code_field","required_fields","status_ok"}}
    """
    try:
        from src.extensions.diff_snapshot_store import diff_snapshot_store

        snapshot = diff_snapshot_store.load_snapshot("api_schema")
        if not snapshot:
            return {}
        from src.core.utils.yapi_utils import extract_response_schemas

        schemas = extract_response_schemas(snapshot)
        return schemas or {}
    except Exception as e:  # noqa: BLE001
        _log.warning("[contract_builder] 加载 YApi schema 失败(按空处理): %s", e)
        return {}


# ─────────────────────────────────────────────────────────────────────────────
# OpenAPI 3.0 / Swagger 2.0 解析器（2026-08-12 新增）
# ─────────────────────────────────────────────────────────────────────────────


def _load_spec_text(spec_path_or_url: str) -> str:
    """从文件路径或 URL 读取 spec 原始文本（JSON 或 YAML）。"""
    spec = str(spec_path_or_url or "").strip()
    if not spec:
        raise ValueError("spec_path_or_url 为空")
    # URL → urllib 下载
    if spec.startswith(("http://", "https://")):
        from urllib.request import urlopen

        with urlopen(spec, timeout=30) as resp:  # noqa: S310 - 受控内部用途
            return resp.read().decode("utf-8")
    # 本地文件
    import os

    if not os.path.isfile(spec):
        raise FileNotFoundError("spec 文件不存在: %s" % spec)
    with open(spec, "r", encoding="utf-8") as f:
        return f.read()


def _parse_spec(raw_text: str) -> dict:
    """解析 spec 文本（JSON / YAML）→ dict。"""
    import json

    try:
        return json.loads(raw_text)
    except (json.JSONDecodeError, ValueError) as _e:
        _log.debug("_parse_spec best-effort 容忍: %s", str(_e)[:120])
    # 尝试 YAML
    try:
        import yaml

        data = yaml.safe_load(raw_text)
        if isinstance(data, dict):
            return data
    except ImportError:
        raise ValueError(
            "spec 为 YAML 但 PyYAML 未安装; pip install pyyaml"
        )
    except Exception as e:
        raise ValueError("spec 解析失败(JSON/YAML): %s" % e)
    raise ValueError("spec 解析失败: 非 JSON/YAML 格式")


def _resolve_ref(spec: dict, ref: str) -> dict | None:
    """解析 OpenAPI $ref（如 '#/components/schemas/Pet'）→ dict。"""
    if not ref or not ref.startswith("#/"):
        return None
    parts = ref.lstrip("#/").split("/")
    node: Any = spec
    for part in parts:
        if not isinstance(node, dict):
            return None
        # 兼容 URL-encoded ~1 / ~0
        part = part.replace("~1", "/").replace("~0", "~")
        node = node.get(part)
        if node is None:
            return None
    return node if isinstance(node, dict) else None


def _extract_openapi_response_schema(
    spec: dict, path: str, method: str, resp_def: dict
) -> dict | None:
    """从单个 OpenAPI 响应定义提取契约 schema 信息。

    返回 {code_field, required_fields, status_ok, types, enums, formats, raw_schema}
    """
    # OpenAPI 3.0: content["application/json"].schema
    # Swagger 2.0: schema (直接)
    schema = None
    content = resp_def.get("content")
    if isinstance(content, dict):
        json_media = content.get("application/json") or content.get("*/*")
        if isinstance(json_media, dict):
            schema = json_media.get("schema")
    if schema is None:
        schema = resp_def.get("schema")
    if not isinstance(schema, dict):
        return None

    # 解析 $ref
    ref = schema.get("$ref")
    if ref:
        resolved = _resolve_ref(spec, str(ref))
        if resolved:
            schema = resolved

    # 展开 allOf / oneOf / anyOf（取第一个 allOf 子 schema）
    all_of = schema.get("allOf")
    if isinstance(all_of, list) and all_of:
        for sub in all_of:
            if isinstance(sub, dict):
                sub_ref = sub.get("$ref")
                if sub_ref:
                    sub = _resolve_ref(spec, str(sub_ref)) or sub
                if isinstance(sub, dict):
                    # 合并 properties / required
                    for k, v in sub.items():
                        if k == "properties" and isinstance(v, dict):
                            schema.setdefault("properties", {}).update(v)
                        elif k == "required" and isinstance(v, list):
                            schema.setdefault("required", []).extend(v)
                        elif k not in schema:
                            schema[k] = v

    properties = schema.get("properties") or {}
    required = list(schema.get("required") or [])

    # 提取字段级约束（类型 / 枚举 / 格式）
    types: dict[str, str] = {}
    enums: dict[str, list] = {}
    formats: dict[str, str] = {}
    required_fields: list[str] = []

    for prop_name, prop_def in properties.items():
        if not isinstance(prop_def, dict):
            continue
        prop_type = str(prop_def.get("type", "") or "").strip()
        if prop_type:
            # 兼容 OpenAPI integer→number（与 _verify_contract._type_name 对齐）
            if prop_type in ("integer", "int"):
                prop_type = "number"
            if prop_type in ("bool",):
                prop_type = "boolean"
            types[prop_name] = prop_type
        enum_val = prop_def.get("enum")
        if isinstance(enum_val, list) and enum_val:
            enums[prop_name] = list(enum_val)
        fmt = str(prop_def.get("format", "") or "").strip()
        if fmt and fmt in _FORMAT_NAMES:
            formats[prop_name] = fmt

    # required_fields：schema.required 优先，否则取 properties 全部 key
    required_fields = list(required) if required else list(properties.keys())

    # code_field / status_ok 启发式推断
    code_field = ""
    status_ok: list = []
    for candidate in ("code", "status", "errCode", "errno", "ret", "success"):
        if candidate in properties:
            code_field = candidate
            # 推断成功值：boolean true / 0 / 200
            prop = properties[candidate]
            if isinstance(prop, dict):
                if prop.get("type") == "boolean":
                    status_ok = [True]
                else:
                    enum_val = prop.get("enum")
                    if isinstance(enum_val, list) and enum_val:
                        status_ok = list(enum_val)
                    else:
                        status_ok = [0, 200]
            break
    if not code_field and "success" in properties:
        code_field = "success"
        status_ok = [True]

    return {
        "code_field": code_field,
        "required_fields": required_fields,
        "status_ok": status_ok,
        "types": types,
        "enums": enums,
        "formats": formats,
        "raw_schema": schema,
    }


def load_openapi_schemas(spec_path_or_url: str) -> dict:
    """解析 OpenAPI 3.0 / Swagger 2.0 文件（JSON/YAML），提取每个 API 的响应 schema。

    参数:
        spec_path_or_url: spec 文件路径或 URL（http/https）。

    返回:
        {path: {"code_field","required_fields","status_ok","types","enums","formats","raw_schema"}}
        与 load_yapi_schemas() 输出格式兼容（多出 types/enums/formats/raw_schema）。
        解析失败 → 返回 {}（记 warning 不阻断）。
    """
    try:
        raw_text = _load_spec_text(spec_path_or_url)
        spec = _parse_spec(raw_text)
    except Exception as e:  # noqa: BLE001
        _log.warning("[contract_builder] 加载 OpenAPI spec 失败(按空处理): %s", e)
        return {}

    if not isinstance(spec, dict):
        _log.warning("[contract_builder] OpenAPI spec 非 dict，跳过")
        return {}

    # OpenAPI 3.0 → paths；Swagger 2.0 → paths（结构兼容）
    paths = spec.get("paths")
    if not isinstance(paths, dict):
        _log.warning("[contract_builder] OpenAPI spec 无 paths 段，跳过")
        return {}

    result: dict[str, dict] = {}
    for path, path_item in paths.items():
        if not isinstance(path_item, dict):
            continue
        for method, op_def in path_item.items():
            method_lower = method.lower()
            if method_lower not in ("get", "post", "put", "delete", "patch"):
                continue
            if not isinstance(op_def, dict):
                continue
            responses = op_def.get("responses")
            if not isinstance(responses, dict):
                continue
            # 优先取 200 响应，其次 2xx/默认
            resp_def = responses.get("200") or responses.get("201") or responses.get(
                "default"
            )
            if not isinstance(resp_def, dict):
                continue
            extracted = _extract_openapi_response_schema(
                spec, path, method_lower, resp_def
            )
            if extracted is None:
                continue
            # key 格式："GET /api/orders"（与 YApi 路径匹配逻辑兼容）
            key = "%s %s" % (method_lower.upper(), path)
            result[key] = extracted
            # 同时注册纯 path key（便于 url_hint 子串匹配）
            if path not in result:
                result[path] = extracted

    _log.info(
        "[contract_builder] OpenAPI 解析完成: %d 个路径 %d 个端点",
        len(paths),
        len(result),
    )
    return result


# ─────────────────────────────────────────────────────────────────────────────
# 完整 JSON Schema 验证（2026-08-12 新增）
# ─────────────────────────────────────────────────────────────────────────────


def _type_name(value: Any) -> str:
    """把 Python 值映射为契约类型名（object/array/string/number/boolean/null）。"""
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, (int, float)):
        return "number"
    if isinstance(value, str):
        return "string"
    if isinstance(value, list):
        return "array"
    if isinstance(value, dict):
        return "object"
    return "unknown"


def _check_enums(body: dict, enums: dict) -> list[str]:
    """校验枚举值：字段值必须在 enum 列表中。"""
    bad: list[str] = []
    for field, allowed in (enums or {}).items():
        if field not in body:
            continue
        val = body[field]
        if val not in allowed:
            bad.append(
                tr("contract.enum_violation", field=field, value=val, enum=allowed)
            )
    return bad


def _check_formats(body: dict, formats: dict) -> list[str]:
    """校验格式（email/uri/date-time/date 等）。"""
    import re

    bad: list[str] = []
    _patterns = {
        "email": re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$"),
        "uri": re.compile(r"^[a-zA-Z][a-zA-Z0-9+.\-]*://[^\s]+$"),
        "date": re.compile(r"^\d{4}-\d{2}-\d{2}$"),
        "date-time": re.compile(
            r"^\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}"
        ),
        "ipv4": re.compile(r"^\d{1,3}(\.\d{1,3}){3}$"),
        "uuid": re.compile(
            r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
        ),
    }
    for field, fmt in (formats or {}).items():
        if field not in body:
            continue
        val = body[field]
        if not isinstance(val, str):
            bad.append(
                tr(
                    "contract.format_type_error",
                    field=field,
                    fmt=fmt,
                    actual=_type_name(val),
                )
            )
            continue
        pat = _patterns.get(fmt)
        if pat and not pat.match(val):
            bad.append(
                tr("contract.format_violation", field=field, value=val, fmt=fmt)
            )
    return bad


def _deep_validate_raw_schema(value: Any, schema: dict, path: str = "") -> list[str]:
    """内置 jsonschema 深度校验（不依赖外部 jsonschema 库）。

    ★ 根因修复(2026-08-16): 原 validate_response_schema 仅在 jsonschema 库可用时
       做 raw_schema 深度校验；库缺失时降级为仅顶层 required/type/enums/formats 检查，
       导致嵌套对象类型不匹配被漏检（假通过 / fail-open）。本函数提供语言内递归校验，
       覆盖 type / properties / required / items / enum，作为 fail-closed 兜底，
       保证契约校验在任意环境下都不会静默放过嵌套结构违规。

    返回:
        问题字符串列表（含 "jsonschema" 前缀，便于统一识别）。
    """
    problems: list[str] = []
    if not isinstance(schema, dict):
        return problems
    node_label = path or "(root)"
    expected_type = str(schema.get("type", "") or "").strip()
    if expected_type:
        exp = expected_type
        if exp in ("integer", "int"):
            exp = "number"
        if exp in ("bool",):
            exp = "boolean"
        actual = _type_name(value)
        if exp == "number" and actual == "integer":
            actual = "number"  # integer 视为 number 子集
        if actual != exp:
            problems.append(
                tr(
                    "contract.jsonschema_deep",
                    path=node_label,
                    message="期望类型 %s 实得 %s" % (expected_type, actual),
                )
            )
            return problems  # 类型已错，不再深入子结构避免噪音
    enum_def = schema.get("enum")
    if isinstance(enum_def, list) and enum_def and value not in enum_def:
        problems.append(
            tr(
                "contract.jsonschema_deep",
                path=node_label,
                message="值 %r 不在枚举 %s 中" % (value, enum_def),
            )
        )
    if isinstance(value, dict):
        properties = schema.get("properties") or {}
        if isinstance(properties, dict):
            for prop_name, prop_schema in properties.items():
                if isinstance(prop_schema, dict) and prop_name in value:
                    problems.extend(
                        _deep_validate_raw_schema(
                            value[prop_name],
                            prop_schema,
                            "%s.%s" % (node_label, prop_name),
                        )
                    )
        required = schema.get("required") or []
        if isinstance(required, list):
            for f in required:
                if f not in value:
                    problems.append(
                        tr(
                            "contract.jsonschema_deep",
                            path=node_label,
                            message="缺少必填字段 %s" % f,
                        )
                    )
    elif isinstance(value, list):
        items_schema = schema.get("items")
        if isinstance(items_schema, dict):
            for i, item in enumerate(value):
                problems.extend(
                    _deep_validate_raw_schema(
                        item, items_schema, "%s[%d]" % (node_label, i)
                    )
                )
    return problems


# ─────────────────────────────────────────────────────────────────────────────
# 契约快照 diff（2026-08-12 新增）
# ─────────────────────────────────────────────────────────────────────────────


# ─────────────────────────────────────────────────────────────────────────────
# 原有：从 case 提取 api 轨已有断言
# ─────────────────────────────────────────────────────────────────────────────


def _extract_api_checks(case: dict) -> list[dict]:
    """从 case 提取 api 轨已有断言（verification_plan.api 或 checks）。"""
    plan = case.get("verification_plan")
    if isinstance(plan, dict):
        api = plan.get("api")
        if isinstance(api, list):
            return [a for a in api if isinstance(a, dict)]
    checks = case.get("checks") or []
    if isinstance(checks, str):
        import json as _json

        try:
            checks = _json.loads(checks)
        except (ValueError, TypeError):
            checks = []
    out = []
    for c in checks if isinstance(checks, list) else []:
        if isinstance(c, dict):
            track = str(c.get("track", "") or "").strip().lower()
            if track in ("api", ""):
                out.append(c)
    return out


def _has_body_level(case: dict) -> bool:
    """api 轨是否已有 body 级断言。"""
    for c in _extract_api_checks(case):
        t = str(c.get("evidence") or c.get("type") or c.get("check_type") or "").strip()
        if t in _BODY_LEVEL_TYPES:
            return True
    return False


def build_contract_checks(case: dict, yapi_schemas: dict) -> list[dict]:
    """从 {path: {code_field, required_fields, status_ok, types, enums, formats}}
    为 case 的 api 轨生成 api_contract / api_response_body 断言（仅当该轨尚无 body 级断言）。

    ★ 2026-08-12 增强：生成的 api_contract 断言现包含 types/enums/formats，
    覆盖字段类型（string/int/bool/array/object）、枚举值、格式（email/uri/date-time）。

    返回 list[dict]（check 格式，供 verification_plan_builder 追加到 api 轨）。
    """
    if (
        not isinstance(case, dict)
        or not isinstance(yapi_schemas, dict)
        or not yapi_schemas
    ):
        return []
    if _has_body_level(case):
        return []
    # 从 case 提取可能的 API 路径关键词（url/action/接口描述）
    title = str(case.get("title", "") or "")
    url_hint = str(case.get("url", "") or "")
    checks: list[dict] = []
    for path, schema in yapi_schemas.items():
        if not isinstance(schema, dict):
            continue
        # 路径匹配：url_hint 命中 / title 包含路径片段
        hit = False
        if url_hint and url_hint in path:
            hit = True
        if (
            not hit
            and title
            and any(seg and seg in title for seg in path.split("/") if seg.isalnum())
        ):
            hit = True
        if not hit and len(yapi_schemas) == 1:
            hit = True  # 单 schema 时兜底为该 case 补契约
        if not hit:
            continue

        # ★ 完整 JSON Schema 约束提取（types/enums/formats）
        types = dict(schema.get("types") or {})
        enums = dict(schema.get("enums") or {})
        formats = dict(schema.get("formats") or {})

        contract_check = {
            "type": "api_contract",
            "url_pattern": path,
            "code_field": str(schema.get("code_field", "") or ""),
            "required_fields": list(schema.get("required_fields") or []),
            "status_ok": list(schema.get("status_ok") or []),
        }
        # 仅在有约束时附加，避免空字段污染断言
        if types:
            contract_check["types"] = types
        if enums:
            contract_check["enums"] = enums
        if formats:
            contract_check["formats"] = formats
        checks.append(contract_check)

        # 附带 api_response_body 断言（body 级，分数更高）
        checks.append(
            {
                "type": "api_response_body",
                "url_pattern": path,
                "expect": "响应体包含必填字段: %s"
                % ", ".join(schema.get("required_fields") or []),
            }
        )
    return checks[:8]  # 防爆炸：单 case 最多 8 条契约断言
