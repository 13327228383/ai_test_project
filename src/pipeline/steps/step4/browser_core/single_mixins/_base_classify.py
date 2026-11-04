# -*- coding: utf-8 -*-
from __future__ import annotations

"""单意图执行 — step_action 分类 / 安全转换助手 —— 从 _base.py 拆出（§4.3 单文件≤800行）。

职责（零业务逻辑变更）：
- DB 驱动的 step_action 分类缓存（替代硬编码 ("click","fill",...) / ("assert","verify")）
- _safe_int 安全整数转换
"""

import logging

from src.pipeline.utils.action_library_prompt import list_action_ids

_log = logging.getLogger(__name__)


# ── f3: DB 驱动的 step_action 分类缓存 ──
#   替代硬编码 step_action in ("click","fill",...) / ("assert","verify") 判断。
#   从 action_library_store 加载 operation_ids / assertion_ids，失败时回退到结构探测。
_STEP_ACTION_IDS_CACHE = None  # type: set | None
_STEP_ASSERTION_IDS_CACHE = None  # type: set | None


def _load_step_action_classification():
    """加载 action_library 分类，返回 (action_ids, assertion_ids)。

    首次调用时从 DB 加载并缓存；DB 失败时返回空集合，调用方按 step 结构兜底。
    """
    global _STEP_ACTION_IDS_CACHE, _STEP_ASSERTION_IDS_CACHE
    if _STEP_ACTION_IDS_CACHE is not None and _STEP_ASSERTION_IDS_CACHE is not None:
        return _STEP_ACTION_IDS_CACHE, _STEP_ASSERTION_IDS_CACHE
    try:
        op_ids, assert_ids = list_action_ids()
        _STEP_ACTION_IDS_CACHE = set(op_ids) if op_ids else set()
        _STEP_ASSERTION_IDS_CACHE = set(assert_ids) if assert_ids else set()
    except Exception as _e:
        _log.debug("[_base] 加载 step_action 分类失败: %s", str(_e)[:80])
        _STEP_ACTION_IDS_CACHE = set()
        _STEP_ASSERTION_IDS_CACHE = set()
    return _STEP_ACTION_IDS_CACHE, _STEP_ASSERTION_IDS_CACHE


def _is_structured_action_step(step_action: str, step: dict) -> bool:
    """判断是否走结构化动作执行路径。

    DB 驱动：step_action 在 action_library 的非断言分类中，或 step_action=="execute"。
    DB 失败时兜底：step 含非空 actions 列表。
    """
    action_ids, _ = _load_step_action_classification()
    if action_ids and step_action in action_ids:
        return True
    if step_action == "execute":
        return True
    # DB 分类为空时按结构兜底
    if not action_ids:
        return bool(step.get("actions"))
    return False


def _is_structured_assertion_step(step_action: str, step: dict) -> bool:
    """判断是否走结构化断言执行路径。

    DB 驱动：step_action 在 action_library 的断言分类中，或 step_action=="assert"。
    DB 失败时兜底：step 含非空 assertions 列表。
    """
    _, assert_ids = _load_step_action_classification()
    if assert_ids and step_action in assert_ids:
        return True
    if step_action == "assert":
        return True
    # DB 分类为空时按结构兜底
    if not assert_ids:
        return bool(step.get("assertions"))
    return False


def _safe_int(value, default: int = 0) -> int:
    """安全转换为整数：空字符串、None、非数字字符串均返回 default。"""
    if value is None or value == "":
        return default
    try:
        return int(value)
    except (ValueError, TypeError):
        return default


# ── ★ 需求权威性单一事实源 ──
#   权威需求 = 有可追溯 req_id 且非隐含（is_implicit=0）。
#   语义：需求已定义 → 引擎必须真实验证（PASS/FAIL），任何路径都绝不允许
#   把权威需求判成 SKIP（SKIP 会掩盖"需求已定义但系统未实现"的真实覆盖缺口）。
#   使用方：_agent_executor（导航失败判决）+ _agent_checks（replay 断言跳过
#   门禁）——所有 SKIP 判决路径统一走本函数，杜绝"同一需求不同判决"的补丁式差异。
def _is_authoritative_requirement(intent, case_meta=None) -> bool:
    """判断需求是否为「权威需求」。

    req_id / is_implicit 读取回退链（兼容 Step3 各种 payload 形态）：
      intent 顶层 → intent._explore_task → case_meta
    （真实生产数据：Step3 把 req_id 放在 intent 顶层，_explore_task 内无。）

    通用性：req_id + is_implicit 是通用需求元数据，任何系统/行业/页面一致适用。
    """
    if not isinstance(intent, dict):
        intent = {}
    _et = intent.get("_explore_task") or {}
    if not isinstance(_et, dict):
        _et = {}
    if not isinstance(case_meta, dict):
        case_meta = {}
    _req_id = str(
        intent.get("req_id")
        or intent.get("_req_id")
        or _et.get("req_id")
        or _et.get("_req_id")
        or case_meta.get("req_id")
        or ""
    ).strip()
    if not _req_id:
        return False
    _req_implicit = bool(
        intent.get("_req_implicit")
        or intent.get("is_implicit")
        or _et.get("_req_implicit")
        or _et.get("is_implicit")
        or case_meta.get("is_implicit")
    )
    return not _req_implicit
