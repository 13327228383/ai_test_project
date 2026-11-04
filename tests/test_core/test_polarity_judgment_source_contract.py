# -*- coding: utf-8 -*-
"""契约：极性判据不得把**引擎派生字段**当需求义务（D55）。

背景（2026-10-15 实测，v9 全库 606 条）
----------------------------------------
`_check_guard.sanitize_case_checks` 在把 `no_error`（"系统无致命错误"）
**反转**为 `semantic_check`（"应出现拒绝/错误反馈"）时，期望文案取自 `_cands`：

    _cands = [expected_feedback, _explore_task.expected_feedback, expected_result]
    _req_expect = next(c for c in _cands if expects_error_feedback(c)) \
        or next(c for c in _cands if c) or "系统应拒绝该操作并给出错误提示"

两条缺陷叠加：
  1. `expected_result` 是**引擎派生展示字段**（case_rules 按操作类型套
     `_OP_SCENARIO_TEMPLATES` 模板生成，与需求无关）——实测 REQ-039「备注字段
     接受任意文本」被套成 `expected_result="显示错误提示"`；
  2. 它先被并入极性探测文本 `_probe_text` → 需求自述为**接受型**的用例也被判
     `_expects_error=True` → 触发反转；
  3. 反转时第一候选（expected_feedback）不含拒绝语义 → 回退取到派生的
     `expected_result="显示错误提示"` → **断言要求报错，而用例/需求期望接受**
     → SUT 正确接受输入（无错误）→ 恒假 FAIL，且被归因 PRODUCT_BUG（假 Bug）。

    实测受影响用例：REQ-013/029/032/039/046/049/066 等 I02/I03 变体，共 11 条。

修复原则（§6）：语义在上游一次性判定并落结构化字段，下游只读不推 ——
`expected_feedback` 才是需求自身的结构化声明；`expected_result` 属"引擎猜的"，
不得进入极性判据，也不得充当断言期望文案。
"""

import pytest


def test_polarity_probe_excludes_engine_derived_expected_result():
    """极性文本（`_probe_text`）不得并入引擎派生字段 `expected_result`。"""
    src = open(
        "src/pipeline/step3/_check_guard.py", encoding="utf-8"
    ).read()
    marker = "strip_inferred_feedback_clause("
    seg = src[src.find(marker): src.find(marker) + 1200]
    assert "expected_result" not in seg, (
        "极性判据文本仍并入了引擎派生字段 `expected_result`（D55 根因）："
        "需求自述为接受型时会被误判为'期望拒绝' → 断言反向 → 恒假 FAIL"
    )


def test_flip_requires_requirement_declared_rejection():
    """反转前提必须成立：期望文案只能来自需求自身声明且确含拒绝语义。"""
    from src.pipeline.step3._check_guard import sanitize_case_checks

    # 需求自述 = 接受型（"系统接受任意文本输入并存储"），
    # 而 expected_result 被模板套成 "显示错误提示"（引擎派生，非需求义务）
    case = {
        "case_id": "TC-D55-001",
        "title": "5. 新增资产-REQ-039-输入备注信息-异常输入校验",
        "operation": "输入备注信息",
        "description": "执行输入备注信息-异常输入校验",
        "expected_feedback": "系统接受任意文本输入并存储",
        "expected_result": "显示错误提示",
        "checks": [
            {"track": "ui", "expect": "系统无报错",
             "check": {"type": "no_error", "expected": "无错误"}},
        ],
    }
    sanitize_case_checks(case)
    out = case["checks"]
    assert out, "checks 不应为空"
    flip = [c for c in out if isinstance(c, dict) and c.get("_polarity_flipped")]
    assert not flip, (
        "需求自述为接受型（'系统接受任意文本输入并存储'）时不应发生极性反转；"
        "若反转则会把断言写成'应显示错误提示' → 与用例语义矛盾 → 恒假 FAIL（D55）"
    )


def test_flip_preserved_for_requirement_declared_rejection():
    """反向用例：需求自述确为拒绝语义 → 仍必须反转（防假 PASS 回归）。"""
    from src.pipeline.step3._check_guard import sanitize_case_checks

    case = {
        "case_id": "TC-D55-002",
        "title": "5. 新增资产-REQ-040-未选择信息提交复选框提交-异常输入校验",
        "operation": "未选择信息提交复选框提交",
        "description": "执行未选择信息提交复选框提交-异常输入校验",
        "expected_feedback": "系统客户端拦截提交请求",
        "expected_result": "显示错误提示",
        "checks": [
            {"track": "ui", "expect": "系统无报错",
             "check": {"type": "no_error", "expected": "无错误"}},
        ],
    }
    sanitize_case_checks(case)
    flip = [
        c
        for c in (case["checks"] or [])
        if isinstance(c, dict) and c.get("_polarity_flipped")
    ]
    assert flip, (
        "需求自述为拒绝语义（'系统客户端拦截提交请求'）时仍必须反转，"
        "否则负向需求失去可判定断言 → 探针-only 假 PASS（D39 回归）"
    )
    exp = str((flip[0].get("check") or {}).get("expected") or "")
    assert "拦截" in exp, (
        "反转后的期望文案必须取自**需求自身声明**（应含'拦截'），"
        "不得是派生的'显示错误提示'；实测得到 %r" % exp[:60]
    )


def test_negation_scope_does_not_swallow_affirmative_error_demand():
    """否定防御剥离不得跨「的」吞掉肯定要求（D56）。

    「删除不存在的商品返回错误提示」里的否定作用于"存在"，主谓是"返回错误提示"，
    旧实现把它当防御片段剥离 → 极性判为接受型 → no_error 被保留、负向用例不反转
    → SUT 按需求正确报错却判 FAIL。
    """
    from src.pipeline.step3._rule_extractor_keywords import expects_error_feedback

    for text in (
        "删除不存在的商品返回错误提示",
        "禁止删除并提示\"该分类下仍有商品\"",
    ):
        assert expects_error_feedback(text) is True, f"肯定要求被误剥: {text}"


def _boundary_case_with_tolerant_expectation() -> dict:
    """边界模板用例：期望文案由模板自撰（"…或正确处理"，需求未声明任何极性）。"""
    return {
        "case_id": "TC-REQ-017-I03",
        "req_id": "REQ-017",
        "module": "日志与审计",
        "scenario_type": "boundary",
        "description": "执行新增、编辑、删除操作需记录操作人、时间与对象-边界值验证",
        "expected_feedback": "显示校验提示或正确处理",
        "checks": [
            {"track": "ui", "expect": "系统无报错",
             "check": {"type": "no_error", "expected": "无错误"}},
        ],
    }


def test_flip_suppressed_when_requirement_declares_no_negative_obligation(monkeypatch):
    """需求原文明确未声明拒绝/字段约束 → 不得反转为「必须报错」。

    「正确处理」分支本就是需求容许的结局；反转会把期望抬到需求声明之上，
    SUT 正确处理（无错误条）却被判 FAIL —— 属引擎自撰义务造成的假 Bug。
    """
    from src.pipeline.step3 import _check_guard as cg

    monkeypatch.setattr(
        cg, "requirement_texts_of",
        lambda rid: ["在日志与审计模块中，新增、编辑、删除操作需记录操作人、时间与对象"],
    )
    monkeypatch.setattr(cg, "module_requirement_texts", lambda module: [])

    case = _boundary_case_with_tolerant_expectation()
    cg.sanitize_case_checks(case)
    out = case["checks"]
    assert not [c for c in out if isinstance(c, dict) and c.get("_polarity_flipped")], (
        "需求未声明负向义务时仍发生极性反转：断言变成'必须出现错误反馈' → 假 FAIL"
    )
    assert any(cg._check_type_of(c) == "no_error" for c in out), (
        "抑制反转后应保留 no_error（'正确处理'分支的观察面）"
    )


def test_flip_kept_when_requirement_declares_rejection(monkeypatch):
    """反向守卫：需求原文确声明拒绝语义 → 反转仍必须发生（防假 PASS 回归）。"""
    from src.pipeline.step3 import _check_guard as cg

    monkeypatch.setattr(
        cg, "requirement_texts_of",
        lambda rid: ["登录失败时提示'用户名或密码错误'，不允许区分具体哪个错误"],
    )
    monkeypatch.setattr(cg, "module_requirement_texts", lambda module: [])

    case = _boundary_case_with_tolerant_expectation()
    cg.sanitize_case_checks(case)
    flip = [
        c for c in (case["checks"] or [])
        if isinstance(c, dict) and c.get("_polarity_flipped")
    ]
    assert flip, "需求声明拒绝语义时不得抑制反转，否则负向主张失去可判定断言"


def test_flip_unchanged_when_requirement_corpus_unavailable(monkeypatch):
    """fail-open：需求语料不可得时不得据此放宽（保持既有反转行为）。"""
    from src.pipeline.step3 import _check_guard as cg

    monkeypatch.setattr(cg, "requirement_texts_of", lambda rid: [])
    monkeypatch.setattr(cg, "module_requirement_texts", lambda module: [])

    case = _boundary_case_with_tolerant_expectation()
    cg.sanitize_case_checks(case)
    assert [
        c for c in (case["checks"] or [])
        if isinstance(c, dict) and c.get("_polarity_flipped")
    ], "需求语料不可得属不可判，不得当作'需求未声明'而抑制反转"


def test_negated_defense_phrases_still_stripped():
    """反向守卫：否定防御措辞仍必须判为接受型（防放宽成永真）。"""
    from src.pipeline.step3._rule_extractor_keywords import expects_error_feedback

    for text in (
        "系统无数据库错误泄露",
        "不泄露数据库错误信息",
        "未导致崩溃或泄露",
        "系统无致命错误",
        "不存在错误页",
    ):
        assert expects_error_feedback(text) is False, f"防御措辞被误判为拒绝: {text}"


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
