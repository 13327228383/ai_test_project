# -*- coding: utf-8 -*-
"""D39 契约：极性判定必须识别**真实对立结局**，不得被非析取的 `或` 污染。

根因（实测 TC-REQ-040-E01 假 PRODUCT_BUG）：
    `is_disjunctive_text` 原实现为 `"或" in text` —— 把 `用户名或密码错误`
    这种**单一固定短语**误判为析取式 → `_check_guard` 的极性反转被错误豁免
    → 负向断言 `no_error` 存活 → SUT 正确返回"用户名或密码错误"仍被判 FAIL。
    实测全库 11 条被误豁免（017/020/021/024/027/040 族）。

设计：**两个函数回答两个不同问题**（非重复实现，故不属"重复造轮子"）
    - `is_disjunctive_text`          ：文本是否含**替代结果**（含中性替代）
      → 供**容错型**判定用（`case_rules/base.py` 的"错误提示必须存在吗"）。
    - `has_opposite_outcome_disjunction`：文本是否并列**对立两极**（拒/受）
      → 供**极性判定**用（`_check_guard` 的 no_error 反转）。

极性判据（单一事实源）：连接词两侧**分属对立的两族**
    （一侧错误族 `_REJECT_KEYWORDS`、另一侧接受族 `_ACCEPT_KEYWORDS`）。
"""
from __future__ import annotations

import inspect
import re

from src.pipeline.step3 import _rule_extractor_keywords as K
from src.pipeline.step3 import _check_guard as G


# ── is_disjunctive_text：容错判定语义（含中性替代）────────────────────

def test_is_disjunctive_is_substring_semantics():
    """`is_disjunctive_text` 是**容错**判据：只要含替代结果即真（含中性项）。"""
    assert K.is_disjunctive_text("显示校验提示或正确处理") is True
    assert K.is_disjunctive_text("出现字段级错误提示或成功落地记录") is True
    assert K.is_disjunctive_text("没有替代方案的错误") is False


# ── has_opposite_outcome_disjunction：极性判定语义（仅对立两极）──────

def test_bare_conjunction_is_not_opposite_disjunction():
    """固有用语 `用户名或密码错误` 不是对立析取式（两侧同属错误族）。"""
    assert K.has_opposite_outcome_disjunction("用户名或密码错误") is False
    assert K.has_opposite_outcome_disjunction(
        "登录失败（用户名或密码错误）需提示错误"
    ) is False


def test_enumeration_is_not_opposite_disjunction():
    """枚举式 `新增、编辑或删除` 不是对立析取式（两侧皆无结局词）。"""
    for t in ("新增、编辑或删除", "超长或为空", "绕过或参数异常",
              "viewer或其他非admin用户"):
        assert K.has_opposite_outcome_disjunction(t) is False, t


def test_neutral_alternative_is_not_opposite_disjunction():
    """中性替代（`显示校验提示或正确处理`）极性明确，不构成对立析取。"""
    for t in ("显示校验提示或正确处理", "显示错误提示或跳转404",
              "导出成功或显示合理提示"):
        assert K.has_opposite_outcome_disjunction(t) is False, t


def test_true_opposite_disjunction_is_recognised():
    """真实对立结局（拒绝/接受并列）仍须被判为析取式。"""
    assert K.has_opposite_outcome_disjunction("出现字段级错误提示或成功落地记录") is True
    assert K.has_opposite_outcome_disjunction("系统提示错误 或者 保存成功") is True


def test_predicate_requires_opposite_families():
    """实现必须以『两侧分属对立族』为准，不得退化为『两侧各有结局词』。"""
    src = inspect.getsource(K.has_opposite_outcome_disjunction)
    assert "_REJECT_KEYWORDS" in src and "_ACCEPT_KEYWORDS" in src, (
        "判据未引用错误族/接受族 —— 会退化成『两侧各有结局词』，"
        "`用户名或密码错误`（两侧皆错误族）将被误判为析取式。"
    )
    assert re.search(
        r"_has\([^)]*_REJECT_KEYWORDS[^)]*\)\s*and\s*_has\([^)]*_ACCEPT_KEYWORDS",
        src,
    ), "判据未表达『一侧错误族 + 另一侧接受族』的对立关系"


def test_polarity_guard_uses_opposite_predicate():
    """`_check_guard` 的极性反转闸门必须用极性判据，不得退回裸子串判据。"""
    src = inspect.getsource(G.sanitize_case_checks)
    assert "has_opposite_outcome_disjunction(_probe_text)" in src, (
        "极性反转闸门未使用极性判据 —— 负向断言会被错误豁免（恒假 FAIL）。"
    )
    assert "not is_disjunctive_text(_probe_text)" not in src, (
        "极性反转闸门退回裸子串判据 —— 回归 D39 缺陷。"
    )


# ── 极性文本净化：引擎推断子句剥离 ─────────────────────────────────

def test_inferred_feedback_clause_stripped():
    """引擎推断的 `系统预期反馈：<推断值>` 必须可被剥离（极性文本净化）。"""
    s = "用户执行登录操作，系统预期反馈：登录成功,欢迎做真实功能探针：进入"
    out = K.strip_inferred_feedback_clause(s)
    assert "登录成功" not in out, (
        "引擎推断的成功文案未被剥离 —— 会污染负向需求的极性判定（假 FAIL）。"
    )
    assert "系统预期反馈：" not in out


def test_requirement_derived_feedback_clause_kept():
    """需求自述的槽位值不得被剥离。"""
    s = "用户执行登录操作，系统预期反馈：服务端拒绝并返回错误提示"
    out = K.strip_inferred_feedback_clause(s)
    assert "服务端拒绝并返回错误提示" in out, (
        "需求自述的期望反馈被误剥离 —— 极性判据将失去需求义务。"
    )
