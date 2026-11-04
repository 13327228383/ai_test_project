# -*- coding: utf-8 -*-
from __future__ import annotations

"""校验逻辑"""

import logging
from typing import Any

from ._metrics import _record_guard_rejection

_log = logging.getLogger(__name__)

# ★ f6: 复用 browser_ai.py 的应急兜底白名单与白名单加载函数，避免重复定义
from src.pipeline.steps.step4.browser_ai import (
    _DEFAULT_ALLOWED_ACTIONS,
    _load_allowed_actions_from_library,
)

# ── 键盘按键合法值集合（通用键值，不携带业务规则）──
_VALID_KEYS = frozenset(
    {
        "Enter",
        "Escape",
        "Tab",
        "Backspace",
        "Delete",
        "ArrowUp",
        "ArrowDown",
        "ArrowLeft",
        "ArrowRight",
        "PageUp",
        "PageDown",
        "Home",
        "End",
        "Shift",
        "Control",
        "Alt",
        "Meta",
        "F1",
        "F2",
        "F3",
        "F4",
        "F5",
        "F6",
        "F7",
        "F8",
        "F9",
        "F10",
        "F11",
        "F12",
        "Space",
        "Insert",
    }
)


def _load_action_meta_from_library():
    """从 action_library_store 加载每个动作的元数据。

    返回 {action_id: {"applicable_tags": set, "required_params": set, "needs_ai_id": bool}}
    """
    try:
        from src.pipeline.utils.action_library_store import (
            action_library_store,
            _load_default_library,
        )

        lib = action_library_store.load_library()
        # DB 为空时回退默认库
        if not lib or not lib.get("categories"):
            lib = _load_default_library()
    except Exception as e:
        _log.warning("  [WARN] 加载 action meta 失败 (%s)，回退默认库", str(e)[:80])
        from src.pipeline.utils.action_library_store import _load_default_library

        lib = _load_default_library()

    meta: dict[str, dict] = {}
    for cat in lib.get("categories", []):
        for act in cat.get("actions", []):
            aid = str(act.get("id", "")).strip()
            if not aid:
                continue
            params = act.get("params") or []
            required = set()
            for p in params:
                if isinstance(p, str):
                    if p.endswith("*"):
                        required.add(p.rstrip("*").strip())
                    else:
                        # 无星号参数作为建议参数，不强求
                        pass
            meta[aid] = {
                "applicable_tags": set(act.get("applicable_tags") or []),
                "required_params": required,
                "needs_ai_id": bool(act.get("needs_ai_id")),
            }
    return meta


def _get_action_meta(
    action_type: str, cache: dict[str, dict] | None = None
) -> dict[str, Any]:
    """获取单个 action 的元数据；优先使用调用方传入的缓存，避免重复查库。"""
    if cache is None:
        cache = _load_action_meta_from_library()
    return cache.get(
        action_type,
        {"applicable_tags": set(), "required_params": set(), "needs_ai_id": False},
    )


def _is_custom_action(action_type: str, allowed_actions: set) -> bool:
    """判断是否为 action library 中自定义、但不在默认集合里的操作。"""
    return (
        action_type in allowed_actions and action_type not in _DEFAULT_ALLOWED_ACTIONS
    )


def _validate_actions(
    actions: list[dict],
    compact_state: list[dict],
    allowed_actions: set | None = None,
    last_valid_ai_ids: set | None = None,
) -> str | None:
    """校验 AI 输出的操作序列合法性。返回 None=合法, 否则返回错误描述字符串。"""
    if not actions:
        return None

    if allowed_actions is None:
        allowed_actions = _load_allowed_actions_from_library()

    # 一次性加载 action 元数据，避免循环内重复查库
    action_meta = _load_action_meta_from_library()

    # 收集当前 DOM 中的有效 ai-id 和元素类型
    valid_ids = set()
    id_to_type = {}  # ai-id -> tag 类型
    for el in compact_state:
        eid = el.get("id", "")
        if eid and eid.startswith("ai-"):
            valid_ids.add(eid)
            id_to_type[eid] = el.get("tag", "").lower()

    # 如有外部传入的上次有效 id 集合，做并集（兼容调用方缓存）
    if last_valid_ai_ids:
        valid_ids |= last_valid_ai_ids

    for i, action in enumerate(actions):
        if not isinstance(action, dict):
            continue

        action_type = action.get("action", "").lower()
        ai_id = action.get("ai_id", "")
        meta = _get_action_meta(action_type, action_meta)

        # ── 校验1: 动作白名单 ──
        if action_type and action_type not in allowed_actions:
            _record_guard_rejection("whitelist", action_type)
            return (
                f"动作[{i}]使用了不在白名单中的操作类型: '{action_type}'。"
                f"允许的操作: {', '.join(sorted(allowed_actions))}"
            )

        # ── 校验2: ai-id 范围校验（需要元素定位的动作）──
        if meta.get("needs_ai_id"):
            if not ai_id:
                _record_guard_rejection("missing_ai_id", action_type)
                return f"动作[{i}] {action_type} 缺少 ai_id 参数"

            if ai_id not in valid_ids:
                # 提供更友好的错误信息
                max_id = max(
                    (int(aid.split("-")[1]) for aid in valid_ids if "-" in aid),
                    default=0,
                )
                _record_guard_rejection("ai_id_out_of_range", action_type)
                return (
                    f"动作[{i}]引用了不存在的元素 {ai_id}。"
                    f"当前页面只有 ai-1 到 ai-{max_id}（共{len(valid_ids)}个交互元素）。"
                    f"请从页面元素列表中选择真实存在的 ai-id。"
                )

            # ── 校验3: 元素类型 -> 动作合理性（库驱动）──
            element_tag = id_to_type.get(ai_id, "")
            if element_tag and ai_id:
                tags = meta.get("applicable_tags", set())
                # 自定义 action（来自 action library 但不在默认集合）放宽校验
                if (
                    tags
                    and tags != {"*"}
                    and not _is_custom_action(action_type, allowed_actions)
                ):
                    if element_tag not in tags:
                        _record_guard_rejection("element_action_mismatch", action_type)
                        return (
                            f"动作[{i}]类型不匹配: 不能对 <{element_tag}> 元素执行 {action_type}。"
                            f"允许的元素标签: {', '.join(sorted(tags))}。"
                            f"请选择正确的元素或调整操作类型。"
                        )

        # ── 校验4: 必填参数 ──
        # ai_id 已单独校验；target_ai_id 等其余必填参数在此统一检查
        for req_param in meta.get("required_params", set()):
            if req_param == "ai_id":
                continue
            value = action.get(req_param)
            if value is None or (isinstance(value, str) and not value.strip()):
                _record_guard_rejection("missing_required_param", action_type)
                return f"动作[{i}] {action_type} 缺少必填参数 '{req_param}'"

        # ── 校验4.5: 任何 *_ai_id 参数若提供，必须存在于当前页面 ──
        for key, value in action.items():
            if key.endswith("_ai_id") and value and value not in valid_ids:
                _record_guard_rejection("ai_id_out_of_range", action_type)
                return f"动作[{i}] {action_type} 的 {key}={value} 不存在于当前页面元素列表中。"

        # ── 校验5: press_key 需要合法的 key 值 ──
        if action_type == "press_key":
            key = action.get("key", "")
            # 组合键如 Control+A, Shift+Tab 也允许（按 + 拆分后逐个校验）
            parts = [p.strip() for p in key.split("+")]
            for part in parts:
                if len(part) == 1 and part.isalnum():
                    continue  # 单字符键 A-Z, 0-9
                if part in _VALID_KEYS:
                    continue
                return (
                    f"动作[{i}] press_key 的 key='{key}' 包含不识别的按键 '{part}'。"
                    f"支持的按键: {', '.join(sorted(_VALID_KEYS))} 及单字符键。"
                )

    return None  # 全部校验通过


def _validate_ai_response(
    response: list[dict] | None,
    compact_state: list[dict],
    goal: str = "",
    page_url: str = "",
    page_title: str = "",
    action_history: list = None,
    previous_failure: str = "",
    allowed_actions: set | None = None,
    last_valid_ai_ids: set | None = None,
) -> str | None:
    """综合校验 AI 响应，返回错误描述或 None（通过）。非阻塞，由调用方决定是否重试。"""
    if response is None:
        return "AI 响应为空/解析失败"

    if not isinstance(response, list):
        return f"AI 响应格式错误: 期望数组，得到 {type(response).__name__}"

    # 空数组 [] 是合法的（AI 认为步骤已完成）
    if len(response) == 0:
        return None

    return _validate_actions(
        response,
        compact_state,
        allowed_actions=allowed_actions,
        last_valid_ai_ids=last_valid_ai_ids,
    )
