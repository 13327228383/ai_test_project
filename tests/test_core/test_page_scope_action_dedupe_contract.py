# -*- coding: utf-8 -*-
"""契约：页面级横切用例折叠时，动作去重不得把"测试数据"当"动作语义"。

目的（它修的是哪个既有缺陷）：
  `_collapse_page_scoped_cases` 把同页面同模板的 N 条页面级用例折叠为一条，
  动作按 key 去重。原 key 含 `value`，而 UX01 模板的 fill 值是每次新生成的
  随机数据（`ZZ_NOMATCH_<uuid>`）→ 去重结构性失效 → 折叠后动作数随需求数线性
  膨胀（实测 57 条 → 60 个动作 / 57 个 fill），且每组 fill 缺触发动作。
  本契约锁定：同一 (type, target_hint) 的动作只保留一份。
"""

from __future__ import annotations

import uuid


def _ux01(i: int) -> dict:
    return {
        "case_id": f"TC-REQ-{i:03d}-UX01",
        "req_id": f"REQ-{i:03d}",
        "type": "易用",
        "_scope": "page",
        "_scope_key": "UX01",
        "title": f"需求{i}-易用-空状态有引导文案",
        "actions": [
            {"type": "navigate", "target_hint": "资产"},
            {"type": "fill", "target_hint": "查询输入框",
             "value": "ZZ_NOMATCH_%s" % uuid.uuid4().hex[:8]},
            {"type": "click", "target_hint": "查询按钮"},
            {"type": "screenshot"},
        ],
        "checks": [{"track": "ui", "precision": "fuzzy",
                    "check": {"type": "semantic_check", "expected": "空结果提示"},
                    "expect": "空状态有提示"}],
    }


def test_page_scope_collapse_dedupes_by_action_target_not_value() -> None:
    """折叠后动作数不随需求数增长；随机测试数据不得阻止去重。"""
    from src.pipeline.step3._modeling_intents import _collapse_page_scoped_cases

    out = _collapse_page_scoped_cases([_ux01(i) for i in range(30)])

    assert len(out) == 1, "同页面同模板的页面级用例应折叠为 1 条"
    types = [a.get("type") for a in out[0]["actions"]]
    assert types == ["navigate", "fill", "click", "screenshot"], (
        "动作序列应回到模板原型；出现重复 fill 说明去重 key 里又混入了 value"
    )


def test_page_scope_collapse_keeps_distinct_targets() -> None:
    """不误伤：不同 target 的动作必须保留（去重只针对同一元素上的同一操作）。"""
    from src.pipeline.step3._modeling_intents import _collapse_page_scoped_cases

    a = _ux01(1)
    a["actions"] = [
        {"type": "navigate", "target_hint": "资产"},
        {"type": "fill", "target_hint": "查询输入框", "value": "v1"},
        {"type": "fill", "target_hint": "分类下拉框", "value": "v2"},
    ]
    b = _ux01(2)
    b["actions"] = [
        {"type": "navigate", "target_hint": "资产"},
        {"type": "fill", "target_hint": "查询输入框", "value": "v9"},
        {"type": "fill", "target_hint": "状态下拉框", "value": "v3"},
    ]

    out = _collapse_page_scoped_cases([a, b])
    assert len(out) == 1
    targets = [x.get("target_hint") for x in out[0]["actions"]]
    assert targets == ["资产", "查询输入框", "分类下拉框", "状态下拉框"], (
        "不同 target 的动作不得被合并"
    )
