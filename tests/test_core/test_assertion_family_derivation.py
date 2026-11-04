# -*- coding: utf-8 -*-
"""契约：断言族由需求声明派生；观察面（interaction_scope）与前置数据声明各只有一个入口。

缺陷形状
--------
1. 模板通用期望（"系统不崩溃"/"列表有数据行渲染"）只能落 `element_exists + no_error`，
   它只证明"页面加载且无 JS 异常"，不是需求声明的事实 → 假 PASS 面（实测仅弱断言 155 条）。
2. `list_row_count_check` 有两种形态：绝对值（`expected_count`，静态页事实）与
   差分（`expected_delta`，操作后果）。用例自洽契约曾只按**类型**判"需先操作"，
   于是只读模板（`interaction_scope=observe_only`）的绝对值行数断言被判"前置未满足"
   → 64 条本可判定的用例被记 SKIP（实测 step1_verify 2026-10-26 14:02 run）。

契约（本测试锁定）
------------------
1. 弱断言升级的判据入口唯一：`_check_guard.align_requirement_observable_assertions`；
   升级方向由需求/用例语义派生（列表行 → 行数规模断言）。
2. 用例自洽契约优先读上游声明的 `interaction_scope`，类型判据只是无声明时的退化路径。
3. 前置数据需求 `data_precondition=existing_row` 只在 `complete_submit_flow` 产生，
   且负向删除（期望被拒绝）与只读观察面不声明。
"""

from __future__ import annotations

import pytest

from src.pipeline.step3 import _check_guard as cg
from src.pipeline.step3._fallback_cases import _LIST_ROW_SELECTOR


def _row_case() -> dict:
    return {
        "case_id": "TC-REQ-001-PF02",
        "req_id": "REQ-001",
        "module": "商品",
        "scenario_type": "boundary",
        "interaction_scope": "observe_only",
        "expected_result": "列表有数据行渲染（500条场景下不空白）",
        "checks": [
            {
                "track": "ui",
                "check": {
                    "type": "element_exists",
                    "selector": _LIST_ROW_SELECTOR,
                    "expected": "存在",
                },
                "expect": "列表有数据行渲染",
            },
            {"track": "ui", "check": {"type": "no_error"}},
        ],
    }


def test_list_row_element_exists_upgraded_to_row_count(monkeypatch: pytest.MonkeyPatch):
    """断言对象是列表数据行 → 升级为行数规模断言（可证伪），而非"找到了任意元素"。"""
    monkeypatch.setattr(cg, "requirement_texts_of", lambda _rid: [])
    monkeypatch.setattr(cg, "module_requirement_texts", lambda _m: [])
    _case = _row_case()
    assert cg.align_requirement_observable_assertions(_case) is True
    _types = [cg._check_type_of(c) for c in _case["checks"]]
    assert "list_row_count_check" in _types, _types
    _count = next(
        c for c in _case["checks"] if cg._check_type_of(c) == "list_row_count_check"
    )
    assert (_count.get("check") or {}).get("expected_count") == 1


def test_strong_assertion_case_is_untouched(monkeypatch: pytest.MonkeyPatch):
    """已表达需求事实的强断言不被改写（本判据只作用于全弱断言用例）。"""
    monkeypatch.setattr(cg, "requirement_texts_of", lambda _rid: [])
    monkeypatch.setattr(cg, "module_requirement_texts", lambda _m: [])
    _case = _row_case()
    _case["checks"] = [{"track": "ui", "check": {"type": "list_row_count_check",
                                                 "expected_delta": -1}}]
    assert cg.align_requirement_observable_assertions(_case) is False


def test_observe_only_case_needs_no_prior_operation():
    """只读观察面（上游声明）→ 行数断言不需前置操作；无声明则仍按类型要求交互。"""
    from src.pipeline.utils.case_self_consistency import (
        ASSERTION_UNREACHABLE,
        SELF_CONSISTENT,
        assess,
    )

    _checks = [
        {"check": {"type": "list_row_count_check", "operator": "gte", "expected_count": 1}}
    ]
    _acts = [{"type": "navigate"}, {"type": "screenshot"}]
    assert assess(_acts, _checks, "boundary", "observe_only") == SELF_CONSISTENT
    assert assess(_acts, _checks, "boundary", "") == ASSERTION_UNREACHABLE


def test_observe_only_exemption_does_not_cover_state_changing_case():
    """豁免只按声明生效：同一断言在非只读用例里仍要求状态变更动作。"""
    from src.pipeline.utils.case_self_consistency import (
        ASSERTION_UNREACHABLE,
        assess,
    )

    _checks = [{"check": {"type": "field_error_visible_check"}}]
    _acts = [{"type": "navigate"}, {"type": "screenshot"}]
    assert assess(_acts, _checks, "error_path", "") == ASSERTION_UNREACHABLE
    assert assess(_acts, _checks, "error_path", "observe_only") != ASSERTION_UNREACHABLE


def test_delete_flow_declares_existing_row_precondition():
    """删除型正向用例声明"激励对象是已有记录"；负向删除（期望被拒绝）不声明。"""
    from src.pipeline.step3._trigger_actions import DATA_PRECONDITION_EXISTING_ROW

    _base = {
        "case_id": "TC-REQ-061-I01",
        "req_id": "REQ-061",
        "module": "商品",
        "title": "删除商品（二次确认后删除）",
        "actions": [{"type": "navigate", "target_hint": "/"}],
        "checks": [{"track": "ui", "check": {"type": "no_error"}}],
    }
    _pos = dict(_base)
    cg.complete_submit_flow(_pos, expects_error=False)
    assert _pos.get("data_precondition") == DATA_PRECONDITION_EXISTING_ROW

    _neg = dict(_base)
    _neg["title"] = "删除不存在的商品应返回错误提示"
    cg.complete_submit_flow(_neg, expects_error=True)
    assert not _neg.get("data_precondition")
