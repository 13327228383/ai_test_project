# -*- coding: utf-8 -*-
"""
操作指令库 — 字段校验辅助函数
"""

import logging

_log = logging.getLogger(__name__)

# 已知字段的白名单类型限制；其他自定义字段仅允许字符串 / 简单值，并限制长度 / 嵌套深度。
_RESERVED_ACTION_KEYS = {
    "id",
    "label",
    "desc",
    "params",
    "needs_ai_id",
    "keywords",
    "category",
    "icon",
    "version",
    "tags",
    "applicable_tags",
    "example",
    "is_workflow",
}
# JSON 写入前整个 action 的字节上限（防止大对象写入 MySQL）。
_ACTION_JSON_MAX_BYTES = 64 * 1024  # 64KB


def _validate_str_field(value, field_name, max_len, allow_empty=False):
    """通用：校验字段必须是字符串，且长度在 [0 or 1, max_len] 内。"""
    if not isinstance(value, str):
        return False, f"{field_name} 必须是字符串"
    if not allow_empty and not value.strip():
        return False, f"{field_name} 不能为空"
    if len(value) > max_len:
        return False, f"{field_name} 过长（上限 {max_len} 字符）"
    return True, ""


def _check_nesting_depth(value, max_depth=2, _depth=0):
    """禁止 dict/list 嵌套深度超过 max_depth。"""
    if _depth > max_depth:
        return False
    if isinstance(value, dict):
        for v in value.values():
            if isinstance(v, (dict, list)):
                if not _check_nesting_depth(v, max_depth, _depth + 1):
                    return False
            elif not isinstance(v, (str, int, float, bool, type(None))):
                return False
    elif isinstance(value, list):
        for item in value:
            if isinstance(item, (dict, list)):
                if not _check_nesting_depth(item, max_depth, _depth + 1):
                    return False
            elif not isinstance(item, (str, int, float, bool, type(None))):
                return False
    return True


def _validate_action(action):
    """对 action 内字段执行严格类型与长度校验；不合规返回 (False, msg)。"""
    if not isinstance(action, dict):
        return False, "action 必须是 JSON 对象"

    # -- 必选 / 关键字段类型 + 长度
    act_id = action.get("id")
    ok, msg = _validate_str_field(act_id, "action.id", 64, allow_empty=False)
    if not ok:
        return False, msg
    # id 只允许安全字符（字母 / 数字 / 下划线 / 点 / 减号），防止注入型 ID
    if not all(ch.isalnum() or ch in "_-." for ch in act_id):
        return False, "action.id 仅允许字母、数字及 '-_.'"

    label = action.get("label")
    if label is not None and label != "":
        ok, msg = _validate_str_field(label, "action.label", 128, allow_empty=False)
        if not ok:
            return False, msg

    desc = action.get("desc")
    if desc is not None:
        if not isinstance(desc, str):
            return False, "action.desc 必须是字符串"
        if len(desc) > 512:
            return False, "action.desc 过长（上限 512 字符）"

    params = action.get("params")
    if params is not None and not isinstance(params, (dict, list)):
        return False, "action.params 必须是对象或数组"
    if params is not None and not _check_nesting_depth(params, max_depth=1):
        return False, "action.params 嵌套过深或包含非法值类型（上限 1 层）"

    if "needs_ai_id" in action:
        if not isinstance(action["needs_ai_id"], bool):
            return False, "action.needs_ai_id 必须是布尔值"

    if "is_workflow" in action:
        if not isinstance(action["is_workflow"], bool):
            return False, "action.is_workflow 必须是布尔值"

    if "applicable_tags" in action:
        tags = action["applicable_tags"]
        if not isinstance(tags, list):
            return False, "action.applicable_tags 必须是字符串数组"
        if len(tags) > 32:
            return False, "action.applicable_tags 过多（上限 32 个）"
        for tag in tags:
            if not isinstance(tag, str):
                return False, "action.applicable_tags 每项必须是字符串"
            if not tag or len(tag) > 64:
                return False, "action.applicable_tags 每项长度必须在 1-64 之间"
            if not all(ch.isalnum() or ch in "_-:*" for ch in tag):
                return False, "action.applicable_tags 每项仅允许字母、数字及 '-_:*'"

    # -- 自定义字段：禁止过长 / 过深嵌套
    for k, v in action.items():
        # 白名单字段已在上面检查；下面检查自定义字段
        if k in _RESERVED_ACTION_KEYS:
            continue
        if not isinstance(k, str) or len(k) > 64:
            return False, f"自定义字段名过长或非法: {k!r}"
        if isinstance(v, (dict, list)):
            return False, f"自定义字段 {k} 不允许数组或对象类型"
        elif isinstance(v, str):
            if len(v) > 1024:
                return False, f"自定义字段 {k} 值过长（上限 1024 字符）"
        elif not isinstance(v, (int, float, bool, type(None))):
            return False, f"自定义字段 {k} 包含不支持的值类型"

    # -- 整体体积限制（JSON 序列化后不超过 64KB）
    try:
        import json as _json

        payload = _json.dumps(action, ensure_ascii=False)
    except (TypeError, ValueError):
        return False, "action 无法序列化为 JSON"
    if len(payload.encode("utf-8")) > _ACTION_JSON_MAX_BYTES:
        return False, f"action 序列化后过大（上限 {_ACTION_JSON_MAX_BYTES} 字节）"

    return True, ""
