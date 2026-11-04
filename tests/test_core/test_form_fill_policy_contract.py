# -*- coding: utf-8 -*-
"""契约：「提交前是否补全表单必填项」的判据只在生成端判定一次，执行端只读。

背景（清空态全链路实测）
------------------------
执行端 `single_mixins/_base.py` 原从用例的 `_explore_task` 取
`operation / expected_feedback / description` 三参调用极性判据；结构化用例
**没有** `_explore_task` → 三参全空 → 极性判据无输入 → 退化为「补全」→
「空提交/非法值触发字段校验」类用例的表单被自动填满（价格/库存拿到合法样例）
→ 提交被受理并跳回列表 → 被测的错误反馈面结构性不存在。

实测作用面：无 `_explore_task` 且含提交点击的用例 86 条，其中期望为错误反馈的
11 条——10 条只在靶子「名称已存在（需唯一）」这条**无关规则**上通过（判定证据与
被测需求不符），1 条（TC-REQ-029-V02）落「页面无可编辑字段」无结论。

修复原则（§6）：语义在上游一次性判定并落结构化字段 `form_fill_policy`
（唯一生产点 `utils.form_fill_policy.declare_form_fill_policy`，挂载于落库前咽喉
`_check_guard.sanitize_intents`），执行端 `autofill_allowed` 只读不推。
"""

import os
import re
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from src.pipeline.utils.form_fill_policy import (  # noqa: E402
    FORM_FILL_LEAVE_EMPTY,
    FORM_FILL_REQUIRED,
    autofill_allowed,
    declare_form_fill_policy,
    should_autofill,
)

_SRC = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "src"
)


def _structural_case() -> dict:
    """结构化执行用例（无 `_explore_task`）：期望字段级校验提示（= 应保持空提交）。"""
    return {
        "case_id": "TC-CONTRACT-FILL-001",
        "scenario_type": "error_path",
        "expected_result": "空提交后字段级错误提示可见（校验反馈）",
        "description": "提交空表单触发校验，断言字段级错误提示可见且文本非空",
    }


def _positive_case() -> dict:
    """正向主流程用例：期望操作被受理。"""
    return {
        "case_id": "TC-CONTRACT-FILL-002",
        "scenario_type": "happy_path",
        "expected_result": "其库存记录同步删除",
        "description": "删除分类后，其库存记录同步删除",
    }


# ── ① 判据本身 ──

def test_error_expectation_without_explore_task_declares_leave_empty():
    """无 `_explore_task` 的错误期望用例必须声明 `leave_empty`（不补全）。"""
    case = _structural_case()
    policy = declare_form_fill_policy(case)
    assert policy == FORM_FILL_LEAVE_EMPTY
    assert case["form_fill_policy"] == FORM_FILL_LEAVE_EMPTY, "判据必须落结构化字段"
    assert autofill_allowed(case) is False


def test_positive_case_declares_fill_required():
    """正向用例必须声明 `fill_required`（不补全则空表单永远提交不成功）。"""
    case = _positive_case()
    assert declare_form_fill_policy(case) == FORM_FILL_REQUIRED
    assert autofill_allowed(case) is True


# ── ② 执行端只读，不重推 ──

def test_executor_reads_declared_policy_over_its_own_texts():
    """已声明字段优先：即使用例文本读起来是正向，也按声明值执行（不再二次判定）。"""
    case = _positive_case()
    case["form_fill_policy"] = FORM_FILL_LEAVE_EMPTY
    assert autofill_allowed(case) is False, (
        "执行端用文本重推极性 → 覆盖了上游声明；同一语义出现第二个判定入口"
    )
    case["form_fill_policy"] = FORM_FILL_REQUIRED
    assert autofill_allowed(_structural_case() | {"form_fill_policy": FORM_FILL_REQUIRED}) is True


def test_missing_field_falls_back_to_same_judge():
    """历史用例行无该字段 → 回退到同一判据函数（不得返回固定默认值）。"""
    err = _structural_case()
    pos = _positive_case()
    assert "form_fill_policy" not in err
    assert autofill_allowed(err) is should_autofill("", err["expected_result"], err["description"])
    assert autofill_allowed(pos) is should_autofill("", pos["expected_result"], pos["description"])
    assert autofill_allowed(err) is False and autofill_allowed(pos) is True


# ── ③ 「服务端校验」例外的双向守卫 ──

def test_server_side_validation_still_autofills():
    """「服务端校验失败」型需求：客户端须先通过，否则请求到不了被测分支 → 必须补全。"""
    assert should_autofill(
        "", "服务端校验失败时返回错误提示", "提交重复名称，服务端校验失败并返回错误"
    ) is True


def test_client_side_block_wins_over_server_wording():
    """同一句里声明了客户端拦截 → 请求根本不该发出 → 不得补全（防放宽成永真）。"""
    assert should_autofill(
        "",
        "客户端拦截提交请求，服务端校验不会执行",
        "未填写必填项，客户端拦截提交请求",
    ) is False


# ── ④ 单一生产点（静态门禁） ──

def _read(_rel: str) -> str:
    with open(os.path.join(_SRC, _rel), encoding="utf-8") as _f:
        return _f.read()


def test_sanitize_intents_calls_the_single_producer():
    """落库前咽喉 `sanitize_intents` 必须调用唯一生产点（否则存量/新生成用例都不会带字段）。"""
    src = _read("pipeline/step3/_check_guard.py")
    _start = src.find("def sanitize_intents(")
    assert _start >= 0
    _end = src.find("\ndef ", _start + 10)
    body = src[_start: _end if _end > 0 else len(src)]
    assert "declare_form_fill_policy" in body, (
        "sanitize_intents 未调用 declare_form_fill_policy → form_fill_policy 永不落库"
    )


def test_policy_field_has_single_writer_and_judge():
    """`form_fill_policy` 的写入点与极性判据必须全局唯一（禁第二套定义/推导）。"""
    writers, judges = [], []
    for _root, _dirs, _files in os.walk(_SRC):
        _dirs[:] = [d for d in _dirs if d != "__pycache__"]
        for _fn in _files:
            if not _fn.endswith(".py"):
                continue
            _rel = os.path.relpath(os.path.join(_root, _fn), _SRC).replace(os.sep, "/")
            _txt = _read(_rel)
            if re.search(r"\[\s*[\"']form_fill_policy[\"']\s*\]\s*=", _txt):
                writers.append(_rel)
            if re.search(r"^def should_autofill\(", _txt, re.MULTILINE) or re.search(
                r"^_SERVER_REJECT_RE\s*=", _txt, re.MULTILINE
            ):
                judges.append(_rel)
    assert writers == ["pipeline/utils/form_fill_policy.py"], (
        "form_fill_policy 出现多处写入点: %r（下游只读，不得各自重写语义）" % writers
    )
    assert judges == ["pipeline/utils/form_fill_policy.py"], (
        "极性判据被重复定义: %r（应统一 import 单一事实源）" % judges
    )


def test_executor_no_longer_derives_policy_from_explore_task():
    """执行端（结构化路径）必须只读声明字段，不得再自行拼 `_explore_task` 三参判极性。"""
    src = _read("pipeline/steps/step4/browser_core/single_mixins/_base.py")
    assert "autofill_allowed(" in src
    assert "should_autofill(" not in src, "结构化执行路径仍在自推极性（第二个判定入口）"
    code_lines = [
        ln for ln in src.splitlines() if ln.strip() and not ln.lstrip().startswith("#")
    ]
    assert not [ln for ln in code_lines if "_explore_task" in ln], (
        "执行端代码仍引用 `_explore_task` 拼判据输入 —— 应改读 form_fill_policy"
    )


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
