# -*- coding: utf-8 -*-
from __future__ import annotations

"""
op_cache 模块级辅助函数
========================
从 __init__.py 提取的纯辅助函数，供 OperationCache 及外部调用。
"""

import hashlib
import re
import logging

_log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# 模块级辅助函数
# ---------------------------------------------------------------------------


def normalize_goal(goal: str) -> str:
    """去掉时间戳等动态内容，保留步骤语义。"""
    g = re.sub(r"\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2}", "NOW", goal)
    g = re.sub(r"\d{4}-\d{2}-\d{2}", "TODAY", g)
    return g.strip()


def compute_semantic_fingerprint(compact_state: list[dict]) -> str:
    """提取稳定的页面结构指纹，忽略 ai-id 和位置坐标。"""
    parts = []
    for el in compact_state:
        tag = el.get("tag", "")
        sig_parts = [tag]
        for k in ("text", "placeholder", "aria-label", "type", "name", "role"):
            v = el.get(k, "")
            if v:
                sig_parts.append("%s=%s" % (k, str(v)[:40]))
        parts.append("|".join(sig_parts))
    parts.sort()
    return hashlib.md5("\n".join(parts).encode("utf-8")).hexdigest()[:16]


def resolve_ai_ids(actions: list[dict], compact_state: list[dict]) -> list[dict] | None:
    """
    用 target_hint 从当前 DOM 中找到对应的 ai_id。
    全部解析成功 → 返回带 ai_id 的 action 列表；任一失败 → 返回 None。
    """
    result = []
    for action in actions:
        action_type = action.get("action", "").lower()
        if action_type in (
            "verify",
            "scroll",
            "wait",
            "navigate",
            "screenshot",
            "api_verify",
            "db_verify",
            "done",
            "fail",
        ):
            new_action = dict(action)
            new_action.pop("ai_id", None)
            if not new_action.get("ai_id"):
                new_action["ai_id"] = ""
            result.append(new_action)
            continue
        hint = action.get("target_hint", "")
        fallback_list = action.get("fallback", [])
        resolved = _find_by_hint(compact_state, hint)
        if not resolved and fallback_list:
            for fb in fallback_list:
                resolved = _find_by_hint(compact_state, fb)
                if resolved:
                    break
        if resolved:
            new_action = dict(action)
            new_action["ai_id"] = resolved
            result.append(new_action)
        else:
            return None
    return result


def _find_by_hint(compact_state: list[dict], hint: str) -> str | None:
    """根据 target_hint 在 compact_state 中查找匹配元素的 ai-id。"""
    if not hint:
        return None
    hint = hint.strip()
    if hint.startswith("text="):
        search = hint[5:]
        for el in compact_state:
            el_text = (el.get("text") or "").strip()
            if el_text and search in el_text:
                return el.get("id", "")
        return None
    if hint.startswith("placeholder="):
        search = hint[12:]
        for el in compact_state:
            el_ph = (el.get("placeholder") or "").strip()
            if el_ph and search in el_ph:
                return el.get("id", "")
        return None
    if hint.startswith("label="):
        search = hint[6:]
        for el in compact_state:
            el_label = (el.get("aria-label") or "").strip()
            if el_label and search in el_label:
                return el.get("id", "")
        return None
    if hint.startswith("#"):
        search = hint[1:]
        for el in compact_state:
            el_id_attr = (el.get("id") or "").strip()
            if el_id_attr == search:
                return el.get("id", "")
        return None
    if hint.startswith("."):
        search = hint[1:]
        for el in compact_state:
            tag = (el.get("tag") or "").lower()
            el_type = (el.get("type") or "").lower()
            if search in tag or search in el_type:
                return el.get("id", "")
        return None
    name_match = re.search(r'name\s*=\s*["\']?(\w+)', hint, re.IGNORECASE)
    if name_match:
        search = name_match.group(1)
        for el in compact_state:
            el_name = (el.get("name") or "").strip()
            if el_name and search in el_name:
                return el.get("id", "")
        return None
    return None


def compute_config_hash() -> str:
    """计算当前项目配置指纹（URL/账号/项目名变了 → hash 变化 → 缓存自动失效）。"""
    from src.core.config_loader import config

    parts = [
        config.target_url or "",
        config.target_username or "",
        config.project_name or "",
    ]
    return hashlib.md5("|".join(parts).encode("utf-8")).hexdigest()[:8]


def _strip_and_placeholder(actions: list[dict]) -> list[dict]:
    """
    剥离 ai_id，替换用户名/密码为占位符。
    保留 target_hint、fallback、action、text（如果非密码）。
    """
    from src.core.config_loader import config

    cleaned = []
    for action in actions:
        if not isinstance(action, dict):
            continue
        if action.get("action") == "_guard_reject":
            continue
        new = {}
        for k in (
            "action",
            "target_hint",
            "fallback",
            "text",
            "direction",
            "amount",
            "result",
            "reason",
        ):
            if k in action:
                new[k] = action[k]
        if new.get("action") == "fill":
            raw_text = new.get("text", "")
            pwd = config.target_password or ""
            # ★ P2: 走 constants 集中点(单一字面量定义)
            from src.core.constants import (
                LOGIN_PLACEHOLDER_USERNAME,
                LOGIN_PLACEHOLDER_PASSWORD,
            )

            if pwd and raw_text == pwd:
                new["text"] = LOGIN_PLACEHOLDER_PASSWORD
            username = config.target_username or ""
            if username and raw_text == username:
                new["text"] = LOGIN_PLACEHOLDER_USERNAME
        cleaned.append(new)
    return cleaned


def _get_project_name() -> str:
    """获取当前项目名称。"""
    try:
        from src.core.config_loader import config

        return config.project_name or "unknown"
    except Exception as _e:
        _log.debug("[_helpers] _get_project_name 异常: %s", _e, exc_info=True)
        return "unknown"


def _active_version_id(project: str = "") -> int:
    """当前项目活跃版本号；无 active 版本/取不到时返回 0（0=跨版本复用 legacy 缓存）。"""
    try:
        from src.core.versioning import current_version_id

        return int(current_version_id(project) or 0)
    except Exception as _e:
        _log.debug("[_helpers] _active_version_id 异常: %s", _e, exc_info=True)
        return 0
