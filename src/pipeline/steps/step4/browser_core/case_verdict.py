"""用例终态分类的唯一语义源。

status 由 `single_mixins/_base._execute_one_intent` 一次性判定并落库（PASS/FAIL/
SKIP/MANUAL_REVIEW 等）。本模块只做分类，串行与并行两条路径共用；
下游禁止再用 `passed` 布尔重新推导终态 —— SKIP/BLOCKED 不是失败，
被当成失败会让 §10.1 即停在无害用例上触发（实测：653 例只跑 3 例就报 completed）。
"""

PASS_STATUS = "PASS"
# 终止性失败：产品缺陷或执行器错误，才应触发「失败即停」。
TERMINAL_FAIL_STATUSES = frozenset({"FAIL", "ERROR"})


def case_status(result):
    """取用例终态。历史 result 无 status 字段时按 passed 回落（不重新推导语义）。"""
    _r = result or {}
    _s = (_r.get("status") or "").strip()
    if _s:
        return _s
    return PASS_STATUS if _r.get("passed") else "FAIL"


def is_terminal_failure(result):
    """是否终止性失败。"""
    return case_status(result) in TERMINAL_FAIL_STATUSES


def count_bucket(result):
    """计数桶：pass / fail / skip（skip 含 SKIP、BLOCKED、MANUAL_REVIEW 等非终态）。"""
    _s = case_status(result)
    if _s == PASS_STATUS:
        return "pass"
    if _s in TERMINAL_FAIL_STATUSES:
        return "fail"
    return "skip"
