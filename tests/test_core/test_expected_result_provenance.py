# -*- coding: utf-8 -*-
"""D42 契约：用例 expected_result 必须来自**声明的预期**，不得由存在性字面量反推。

根因（实测 v9 全库 61 条 `expected_result='存在'`）：
    `_build_expected_result` 无条件取「第一个 check 的 expect」，而 `element_exists`
    类 check 的 expect 是**存在性字面量**（"存在"/"无错误"）→ 用例预期变成无意义残句。
    同时 `_build_one` 的 `expected` 形参**被完全丢弃**（6 个调用点都传了有语义的值，
    却从未生效）—— 属"上游声明被下游忽略"的 SSOT 违背。

下游消费（证明该字段不是装饰）：用例审核页展示、导出、置信度评分。
"""
from __future__ import annotations

import inspect

from src.pipeline.step3.case_rules.base import BaseCaseRule
from src.pipeline.step3.case_rules.base import _is_presence_only_fragment as P

F = BaseCaseRule._build_expected_result


def test_declared_expectation_wins():
    """调用方声明的预期优先于 checks 的存在性字面量。"""
    got = F(
        [{"expect": "存在"}],
        req={"expected_feedback": ""},
        declared="系统在缺少 CSRF token 时提交不崩溃、无致命错误",
    )
    assert got == "系统在缺少 CSRF token 时提交不崩溃、无致命错误"


def test_requirement_expectation_used_when_declared_empty():
    got = F([{"expect": "存在"}], req={"expected_feedback": "服务端拒绝并返回错误提示"})
    assert got == "服务端拒绝并返回错误提示"


def test_checks_fallback_skips_presence_fragment():
    """checks 兜底须跳过存在性字面量，取首个有语义的 expect。"""
    got = F([{"expect": "存在"}, {"expect": "列表保持不变"}], req={})
    assert got == "列表保持不变"


def test_presence_fragment_never_returned():
    """任何路径都不得把纯存在性字面量当预期结果返回。"""
    for got in (
        F([{"expect": "存在"}], req={}, declared="存在"),
        F([{"expect": "无错误"}], req={"expected_feedback": "无错误"}),
        F([{"expect": "存在"}], req={}, declared=""),
    ):
        assert not P(got), "返回了纯存在性字面量 %r" % got


def test_hard_fallback_is_meaningful():
    assert F([], req={}) == "操作成功"


def test_declared_param_is_actually_consumed():
    """`_build_one` 必须把 `expected` 传给预期构建（防再次被丢弃）。"""
    src = inspect.getsource(BaseCaseRule._build_one)
    assert "_build_expected_result(checks, req=req, declared=expected)" in src, (
        "`_build_one` 未把 `expected` 形参传给 `_build_expected_result` —— "
        "上游声明又被丢弃（D42 回归）。"
    )


def test_presence_fragment_predicate_edges():
    """存在性字面量判定的边界：标点可选、有语义短语不得误判。"""
    for t in ("存在", "无错误", "存在。", "页面存在", "元素存在"):
        assert P(t) is True, t
    for t in ("列表保持不变", "登录成功,欢迎", "服务端拒绝并返回403"):
        assert P(t) is False, t
    assert P("") is True
