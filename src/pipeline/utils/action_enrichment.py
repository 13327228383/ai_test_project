# -*- coding: utf-8 -*-
from __future__ import annotations

"""
操作指令库自动丰富 (ActionEnrichment)
=====================================
当 AI 兜底路径成功执行了一个操作时，把该操作对应的控件 tag 反哺到
action_library_store 的 applicable_tags 字段，使操作指令库随使用自动完善。

设计原则：
  1. 只保存通用的 action_id → applicable_tags 映射，不保存 case-specific 数据
     （selector/value 等案例数据不写入，保持通用性）
  2. 严格去重：tag 已在 applicable_tags 中则跳过
  3. 不阻断主流程：enrichment 失败只 log warning，不影响用例执行
  4. 复用 action_library_store.upsert_action，不绕过 DB 事实源

调用时机：
  - SmartLocator high-confidence 0-token 路径成功后
  - AI 决策路径 click/fill 成功后
"""

import logging

from src.pipeline.utils.action_library_store import action_library_store

_log = logging.getLogger(__name__)

# 从种子数据推导的已知 action 索引（action_id → {category_id, tool_name}）
# 所有种子数据中的 action 都允许自动创建，不限于特定子集
_KNOWN_ACTIONS: dict[str, dict[str, str]] = {}


def _build_known_actions() -> dict[str, dict[str, str]]:
    """从种子数据加载所有已知 action 的 category_id 和 tool_name。"""
    from src.pipeline.utils.action_library_store import _load_default_library

    result: dict[str, dict[str, str]] = {}
    try:
        lib = _load_default_library()
        for cat in lib.get("categories", []):
            cat_id = str(cat.get("id", "") or "").strip()
            if not cat_id:
                continue
            for act in cat.get("actions", []):
                aid = str(act.get("id", "") or "").strip().lower()
                if not aid:
                    continue
                result[aid] = {
                    "category_id": cat_id,
                    "tool_name": str(act.get("tool_name", "") or ""),
                }
    except Exception as _e:
        _log.debug("[action_enrichment] 加载已知 actions 异常: %s", _e, exc_info=True)
    return result


_KNOWN_ACTIONS = _build_known_actions()


def enrich_action_applicable_tags(
    action_id: str,
    element_tag: str,
    element_role: str | None = None,
) -> bool:
    """把成功执行的控件 tag 反哺到操作指令库的 applicable_tags。

    通用性保证：只更新 action_id → applicable_tags 映射，不保存 selector/value
    等案例数据。其他用例的 Step3 可通过 filter_actions_by_control_type 复用。

    去重逻辑：先加载当前 applicable_tags，tag 已存在则跳过，不重复写入。

    Args:
        action_id: 操作指令 id（如 "click", "fill"）
        element_tag: 成功匹配的元素 HTML tag（如 "button", "input"）
        element_role: 元素的 ARIA role（可选，如 "button", "textbox"）

    Returns:
        True 表示库已更新（新增了 tag），False 表示无需更新或更新失败
    """
    action_id = (action_id or "").strip().lower()
    element_tag = (element_tag or "").strip().lower()
    if not action_id or not element_tag:
        return False

    # 收集要添加的 tag（HTML tag + ARIA role）
    new_tags = [element_tag]
    if element_role and element_role.strip():
        role = element_role.strip().lower()
        if role not in new_tags:
            new_tags.append(role)

    try:
        lib = action_library_store.load_library()
    except Exception as e:
        _log.debug("[ActionEnrichment] 加载操作库失败: %s", str(e)[:80])
        return False

    # 查找现有 action
    existing_action = None
    existing_cat_id = None
    for cat in lib.get("categories", []):
        for act in cat.get("actions", []):
            if act.get("id", "").lower() == action_id:
                existing_action = act
                existing_cat_id = cat.get("id", "")
                break
        if existing_action:
            break

    if existing_action:
        # action 已存在 → 检查去重
        current_tags = set(
            t.lower() for t in (existing_action.get("applicable_tags") or [])
        )
        tags_to_add = [t for t in new_tags if t not in current_tags]
        if not tags_to_add:
            # 所有 tag 已存在，无需更新
            return False

        # 合并后更新
        merged_tags = list(existing_action.get("applicable_tags") or []) + tags_to_add
        existing_action["applicable_tags"] = merged_tags
        try:
            result = action_library_store.upsert_action(
                category_id=existing_cat_id or "advanced",
                action=existing_action,
            )
            if result:
                _log.info(
                    "[ActionEnrichment] %s applicable_tags += %s → %s",
                    action_id,
                    tags_to_add,
                    merged_tags,
                )
                return True
            return False
        except Exception as e:
            _log.warning("[ActionEnrichment] upsert 失败: %s", str(e)[:80])
            return False
    else:
        # action 不存在 → 新建通用 action 条目
        # 只对种子数据中已知的 action_id 创建，防止 AI 编造的操作名污染库
        known = _KNOWN_ACTIONS.get(action_id)
        if not known:
            _log.debug(
                "[ActionEnrichment] 跳过未知 action_id: %s（不自动创建）", action_id
            )
            return False

        cat_id = known["category_id"]
        new_action = {
            "id": action_id,
            "label": action_id.replace("_", " ").title(),
            "desc": "AI 兜底执行时自动注册",
            "keywords": [action_id],
            "params": [],
            "applicable_tags": new_tags,
            "needs_ai_id": False,
            "tool_name": known["tool_name"],
        }
        try:
            result = action_library_store.upsert_action(
                category_id=cat_id,
                action=new_action,
                category_name=cat_id.title(),
            )
            if result:
                _log.info(
                    "[ActionEnrichment] 新建 action %s → cat=%s tags=%s",
                    action_id,
                    cat_id,
                    new_tags,
                )
                return True
            return False
        except Exception as e:
            _log.warning("[ActionEnrichment] 新建 action 失败: %s", str(e)[:80])
            return False
