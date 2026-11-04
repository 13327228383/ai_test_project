# -*- coding: utf-8 -*-
"""
Pydantic 请求校验模块
======================
为 Flask 路由提供类型安全的请求体校验。

- 使用 Pydantic BaseModel 定义请求 schema
- validate_body() 函数：解析 request.get_json() → Pydantic 校验 → 返回 (validated_data, error_tuple)
- 当 pydantic 未安装时，自动回退到 request_schema.validate_json_schema()
- 保持与原 validate_json_schema 完全一致的返回类型签名，向后兼容
"""

from __future__ import annotations

from typing import Any
from flask import jsonify, request

from src.backend.request_schema import validate_json_schema

# ── 尝试导入 pydantic ──────────────────────────────────────────
try:
    import pydantic
    from pydantic import BaseModel, Field, ValidationError as PydanticValidationError

    HAS_PYDANTIC = True
except ImportError:
    HAS_PYDANTIC = False
    # 占位类型，保证未安装时模块仍可被 import
    BaseModel = object  # type: ignore
    PydanticValidationError = Exception  # type: ignore


# ═══════════════════════════════════════════════════════════════
# 核心校验函数
# ═══════════════════════════════════════════════════════════════


def validate_body(model_class) -> tuple[dict[str, Any], Any | None]:
    """使用 Pydantic 模型校验请求 JSON body。

    参数：
        model_class: 一个 Pydantic BaseModel 子类

    返回：
        (validated_data, error_response_tuple)
        - 校验成功：({...字段...}, None)
        - 校验失败：(None, (jsonify({...}), status_code))

    回退：
        若 pydantic 未安装，自动将 model_class 的字段定义
        转换为 dict schema，调用 validate_json_schema。
    """
    data = request.get_json(silent=True)
    if data is None:
        return None, (jsonify({"error": "请求体不能为空，期望 JSON 对象"}), 400)
    if not isinstance(data, dict):
        return None, (jsonify({"error": "请求体必须是 JSON 对象"}), 400)

    if HAS_PYDANTIC:
        return _validate_with_pydantic(model_class, data)
    else:
        return _validate_with_fallback(model_class, data)


def _validate_with_pydantic(
    model_class, data: dict
) -> tuple[dict[str, Any], Any | None]:
    """使用 Pydantic 进行校验。"""
    try:
        instance = model_class(**data)
        if hasattr(instance, "model_dump"):
            return instance.model_dump(), None
        if hasattr(instance, "dict"):
            return instance.dict(), None
        return dict(getattr(instance, "__dict__", {})), None
    except PydanticValidationError as e:
        errors = []
        for err in e.errors():
            loc = ".".join(str(p) for p in err["loc"])
            msg = err["msg"]
            errors.append(f"{loc}: {msg}")
        return None, (
            jsonify(
                {
                    "error": "请求参数校验失败",
                    "details": errors,
                }
            ),
            400,
        )


def _validate_with_fallback(
    model_class, data: dict
) -> tuple[dict[str, Any], Any | None]:
    """将 Pydantic 模型字段转为 dict schema，回退到 validate_json_schema。"""
    explicit_schema = getattr(model_class, "__schema__", None)
    if isinstance(explicit_schema, dict):
        cleaned, err = validate_json_schema(explicit_schema)
        if err:
            return None, err
        # 保留未声明但业务仍需要读取的字段，避免降级模式丢字段
        merged = dict(data)
        merged.update(cleaned)
        return merged, None
    schema = _model_fields_to_schema(model_class)
    if schema:
        cleaned, err = validate_json_schema(schema)
        if err:
            return None, err
        return cleaned, None
    # 如果无法解析 model 字段，直接透传原始数据
    return data, None


def _model_fields_to_schema(model_class) -> dict[str, dict[str, Any]]:
    """从 Pydantic 模型类或普通类提取字段约束，生成 validate_json_schema 兼容的 dict。

    支持两种情况：
    1. Pydantic BaseModel → 读取 model_fields（v2）或 __fields__（v1）
    2. 普通 class → 读取类型注解 + 默认值
    """
    schema: dict[str, dict[str, Any]] = {}

    # ── 路径 1：Pydantic v2 model_fields ──
    if HAS_PYDANTIC and hasattr(model_class, "model_fields"):
        for name, field_info in model_class.model_fields.items():
            rule = _build_rule_from_field_info(name, field_info)
            if rule:
                schema[name] = rule
        return schema

    # ── 路径 2：Pydantic v1 __fields__ ──
    if HAS_PYDANTIC and hasattr(model_class, "__fields__"):
        for name, field_info in model_class.__fields__.items():
            rule = _build_rule_from_v1_field(name, field_info)
            if rule:
                schema[name] = rule
        return schema

    # ── 路径 3：普通 class 的类型注解 ──
    annotations = getattr(model_class, "__annotations__", {}) or {}
    for name, annotation in annotations.items():
        rule = _build_rule_from_annotation(name, annotation)
        # 检查是否有默认值（决定 required）
        default = getattr(model_class, name, ...)
        if default is not ...:
            rule["required"] = False
        else:
            rule["required"] = True
        schema[name] = rule

    return schema


def _build_rule_from_field_info(name: str, field_info) -> dict[str, Any]:
    """从 Pydantic v2 FieldInfo 构建 validate_json_schema 规则。"""
    rule: dict[str, Any] = {}

    # 类型
    annotation = field_info.annotation
    rule.update(_annotation_to_type(annotation))

    # required
    rule["required"] = (
        field_info.default is ...
        or field_info.default is pydantic.fields.PydanticUndefined
    )

    # 字符串长度约束 + pattern
    for meta in field_info.metadata:
        if hasattr(meta, "min_length"):
            rule["min_len"] = meta.min_length
        if hasattr(meta, "max_length"):
            rule["max_len"] = meta.max_length
        if hasattr(meta, "pattern"):
            rule["pattern"] = meta.pattern

    return rule


def _build_rule_from_v1_field(name: str, field_info) -> dict[str, Any]:
    """从 Pydantic v1 FieldInfo 构建规则。"""
    rule: dict[str, Any] = {}

    annotation = field_info.outer_type_
    rule.update(_annotation_to_type(annotation))

    rule["required"] = field_info.required

    # Pydantic v1 约束
    if hasattr(field_info, "field_info"):
        fi = field_info.field_info
        if fi.min_length is not None:
            rule["min_len"] = fi.min_length
        if fi.max_length is not None:
            rule["max_len"] = fi.max_length
        # Pydantic v1 用 regex，v2 用 pattern；兼容两种
        if hasattr(fi, "regex") and fi.regex is not None:
            rule["pattern"] = fi.regex
        if hasattr(fi, "pattern") and fi.pattern is not None:
            rule["pattern"] = fi.pattern

    return rule


def _build_rule_from_annotation(name: str, annotation) -> dict[str, Any]:
    """从 Python 类型注解构建规则。"""
    rule: dict[str, Any] = {}
    rule.update(_annotation_to_type(annotation))
    rule["required"] = True
    return rule


def _annotation_to_type(annotation) -> dict[str, Any]:
    """将 Python/Pydantic 类型注解映射为 validate_json_schema 的 type。"""
    # 处理 ... | None
    origin = getattr(annotation, "__origin__", None)
    import typing as _t

    if origin in (_t.Union,):
        args = getattr(annotation, "__args__", ())
        non_none = [a for a in args if a is not type(None)]
        if non_none:
            annotation = non_none[0]

    type_map = {
        str: "str",
        int: "int",
        float: "float",
        bool: "bool",
        dict: "dict",
        list: "list",
    }
    actual_type = type_map.get(annotation, str)
    return {"type": actual_type} if actual_type != "str" else {"type": str}


# ═══════════════════════════════════════════════════════════════
# Pydantic 请求模型定义
# ═══════════════════════════════════════════════════════════════

if HAS_PYDANTIC:

    class ConfigUpdateSchema(BaseModel):
        """POST /api/config —— 保存项目配置。"""

        project: str = Field(default="", description="项目名称（可选，默认当前项目）")
        fields: dict[str, Any] = Field(..., description="配置键值对（必填）")

    class NewProjectSchema(BaseModel):
        """POST /api/new-project —— 创建新项目。"""

        name: str = Field(
            ...,
            min_length=1,
            max_length=64,
            pattern=r"^[a-zA-Z0-9_\-一-鿿]+$",
            description="项目名（必填，支持字母、数字、下划线、短横线、汉字）",
        )
        template: str | None = Field(
            default=None, max_length=64, description="模板名称（可选）"
        )

    class ExecutionStartSchema(BaseModel):
        """POST /api/step/<n> /api/run-all —— 启动执行。"""

        project: str = Field(default="", description="项目名称（可选，默认当前项目）")
        step: int | None = Field(default=None, ge=1, le=5, description="指定步骤")
        end_step: int | None = Field(default=None, ge=1, le=5, description="结束步骤")
        force_regen: bool | None = Field(default=False, description="强制重新生成")
        resume: bool | None = Field(default=False, description="从 checkpoint 恢复")

    class VersionCreateSchema(BaseModel):
        """POST /api/project/versions —— 创建/更新项目版本。"""

        project: str = Field(default="", description="项目名称（可选，默认当前项目）")
        version_name: str | None = Field(
            default=None,
            min_length=1,
            max_length=32,
            alias="version_label",
            description="版本标签（创建时必填，更新时可选）",
        )
        description: str | None = Field(
            default=None, max_length=500, description="版本描述（可选）"
        )

        class Config:
            populate_by_name = True

    class IntentReviewSchema(BaseModel):
        """POST /api/cases/review/* —— 测试用例审核。"""

        case_id: str = Field(..., min_length=1, description="测试用例 ID")
        review_status: str = Field(
            ..., pattern=r"^(approved|rejected|needs_revision)$", description="审核状态"
        )
        reviewer_notes: str | None = Field(
            default=None, max_length=2000, description="审核备注"
        )

    class LoginSchema(BaseModel):
        """POST /api/login —— 用户登录。

        ★ 键名说明（2026-09-22）：规范键为 user_id（历史命名，语义=用户名），
          服务端入口已归一化接受别名 username。新调用方建议仍用 user_id。
        """

        user_id: str = Field(..., min_length=1, max_length=64, description="用户名（别名 username 亦接受）")
        password: str = Field(..., min_length=1, max_length=256, description="密码")

else:
    # ── pydantic 未安装时的占位模型（用于回退校验） ──

    class _SchemaBase:
        """无 pydantic 时的占位基类，携带类型注解供 _model_fields_to_schema 解析。"""

        pass

    class ConfigUpdateSchema(_SchemaBase):
        __schema__ = {
            "project": {"type": str, "required": False, "max_len": 128},
            "fields": {"type": dict, "required": True},
        }
        project: str
        fields: dict

    class NewProjectSchema(_SchemaBase):
        __schema__ = {
            "name": {
                "type": str,
                "required": True,
                "min_len": 1,
                "max_len": 64,
                "pattern": r"^[a-zA-Z0-9_\-一-鿿]+$",
            },
            "template": {"type": str, "required": False, "max_len": 64},
        }
        name: str
        template: str | None = None

    class ExecutionStartSchema(_SchemaBase):
        project: str = ""
        step: int | None = None
        end_step: int | None = None
        force_regen: bool | None = False
        resume: bool | None = False

    class VersionCreateSchema(_SchemaBase):
        __schema__ = {
            "project": {"type": str, "required": False, "max_len": 128},
            "version_name": {
                "type": str,
                "required": False,
                "min_len": 1,
                "max_len": 32,
            },
            "version_label": {
                "type": str,
                "required": False,
                "min_len": 1,
                "max_len": 32,
            },
            "description": {"type": str, "required": False, "max_len": 500},
        }
        project: str = ""
        version_name: str | None = None
        description: str | None = None

    class IntentReviewSchema(_SchemaBase):
        __schema__ = {
            "case_id": {"type": str, "required": True, "min_len": 1},
            "review_status": {
                "type": str,
                "required": True,
                "enum": ["approved", "rejected", "needs_revision"],
            },
            "reviewer_notes": {"type": str, "required": False, "max_len": 2000},
        }
        case_id: str
        review_status: str
        reviewer_notes: str | None = None

    class LoginSchema(_SchemaBase):
        __schema__ = {
            "user_id": {"type": str, "required": True, "min_len": 1, "max_len": 64},
            "password": {"type": str, "required": True, "min_len": 1, "max_len": 256},
        }
        user_id: str
        password: str
