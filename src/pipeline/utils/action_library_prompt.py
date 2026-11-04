# -*- coding: utf-8 -*-
"""
Action Library Prompt 生成器
==============================
从 MySQL action_library_store 读取用户自定义操作库，生成注入到 Step3/Step4 prompt 中的
可用操作/断言清单文本。

设计原则：
  - 单一事实源：只读 action_library_store，不依赖代码硬编码列表
  - 失败安全：DB 异常时返回空字符串，调用方保持原有兜底逻辑
  - 不重复造轮子：供 Step3 _action_injection 和 Step4 text/tool mode 共用
"""

import logging
from src.core.guarded import guard

_log = logging.getLogger(__name__)

# ── 断言分类 ID（与 _action_injection.py 及 action_categories 约定对齐） ──
_ASSERTION_CATEGORIES = {"verification", "api", "db", "style", "feedback", "perf"}

# 这些 action_id 虽然落在 verification 分类，但实际是操作而非断言
_OPERATION_EXCEPTIONS = {"screenshot"}


def _norm_param(p):
    return p.rstrip("*") if isinstance(p, str) else p


def build_action_library_prompt() -> str:
    """生成 Action Library 可用操作/断言清单 prompt 文本。

    返回空字符串表示未从 DB 加载到有效数据，调用方应使用自身兜底。
    """
    with guard("build_action_library_prompt: from src.pipeline.utils.action_libra", log=_log):
        from src.pipeline.utils.action_library_store import action_library_store

        lib = action_library_store.load_library()
        categories = lib.get("categories", []) if isinstance(lib, dict) else []
        op_lines: list[str] = []
        assert_lines: list[str] = []
        for cat in categories:
            if not isinstance(cat, dict):
                continue
            cat_id = str(cat.get("id", "") or "").strip()
            is_assertion_cat = cat_id in _ASSERTION_CATEGORIES
            for action in cat.get("actions", []):
                if not isinstance(action, dict):
                    continue
                # 工作流指令不在单步执行 prompt 中直接使用
                if bool(action.get("is_workflow")):
                    continue
                aid = str(action.get("id", "") or "").strip()
                if not aid:
                    continue
                desc = str(action.get("desc", "") or action.get("label", "") or "")
                params = [_norm_param(p) for p in (action.get("params") or [])]
                param_str = ", ".join(params) if params else ""
                line = (
                    "- %s(%s): %s" % (aid, param_str, desc)
                    if param_str
                    else "- %s: %s" % (aid, desc)
                )
                if is_assertion_cat and aid not in _OPERATION_EXCEPTIONS:
                    assert_lines.append(line)
                else:
                    op_lines.append(line)
        parts: list[str] = []
        if op_lines:
            parts.append("★ 可用操作类型（只能使用以下 type，禁止编造未列出的 type）：")
            parts.extend(op_lines)
        if assert_lines:
            parts.append("★ 可用断言类型（生成 checks 时使用）：")
            parts.extend(assert_lines)
        if parts:
            return "\n".join(parts) + "\n"
    return ""


def list_action_ids() -> tuple[list[str], list[str]]:
    """返回 (operation_ids, assertion_ids)，供校验/白名单使用。"""
    op_ids: list[str] = []
    assert_ids: list[str] = []
    with guard("list_action_ids: from src.pipeline.utils.action_libra", log=_log):
        from src.pipeline.utils.action_library_store import action_library_store

        lib = action_library_store.load_library()
        for cat in lib.get("categories", []) if isinstance(lib, dict) else []:
            if not isinstance(cat, dict):
                continue
            cat_id = str(cat.get("id", "") or "").strip()
            is_assertion_cat = cat_id in _ASSERTION_CATEGORIES
            for action in cat.get("actions", []):
                if not isinstance(action, dict):
                    continue
                if bool(action.get("is_workflow")):
                    continue
                aid = str(action.get("id", "") or "").strip()
                if not aid:
                    continue
                if is_assertion_cat and aid not in _OPERATION_EXCEPTIONS:
                    assert_ids.append(aid)
                else:
                    op_ids.append(aid)
    return op_ids, assert_ids
