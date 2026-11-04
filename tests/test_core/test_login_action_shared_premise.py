# -*- coding: utf-8 -*-
"""D44 契约：「登录动作」的两个判据必须共用同一动作前提（否定态排除）。

根因（实测 TC-REQ-016-E01）：`is_login_operation` 排除了否定态（"未登录/登录超时"），
但 `is_login_reject` 没有 —— 于是需求「未登录访问业务页面 → 重定向登录页」
（operation 含"登录"、expected 含"拒绝"）被判为「提交凭证被拒」→ 生成 fill「密码,口令」
动作，而门禁/登录页无该控件 → 填充失败 → 引擎自造 ERROR。
语义上「未认证状态下的访问被拒」属访问控制，不是「提交凭证」。
"""
from __future__ import annotations

import pytest

from src.pipeline.step3._rule_extractor_keywords import (
    is_login_operation,
    is_login_reject,
)


class TestNegatedStateExcludedFromBoth:
    @pytest.mark.parametrize(
        "op",
        [
            "验证未登录访问业务页面",
            "未登录用户尝试访问资产列表",
            "尚未登录时访问详情页",
            "无登录凭证访问接口",
            "验证登录超时后访问",
            "执行登出操作",
        ],
    )
    def test_not_login_operation(self, op):
        assert is_login_operation(op) is False, "%r 不是登录动作" % op

    @pytest.mark.parametrize(
        "op,fb",
        [
            ("验证未登录访问业务页面", "系统重定向到登录页（HTTP 302），拒绝业务数据返回"),
            ("未登录用户尝试访问资产列表", "系统拒绝访问并跳转登录页"),
            ("验证登录超时后访问", "系统重定向到登录页"),
        ],
    )
    def test_not_login_reject(self, op, fb):
        """否定态/终止态下必须 false，否则会生成"填密码"动作（016-E01 假 ERROR）。"""
        assert is_login_reject(op, fb) is False, (
            "%r 是未认证访问，不是提交凭证被拒" % op
        )


class TestTrueLoginRejectStillDetected:
    @pytest.mark.parametrize(
        "op,fb",
        [
            ("提交错误凭证登录", "用户名或密码错误"),
            ("使用错误密码登录", "登录失败，提示密码错误"),
            ("提交登录请求", "用户名或密码错误"),
        ],
    )
    def test_still_true(self, op, fb):
        assert is_login_reject(op, fb) is True

    def test_requires_reject_semantics(self):
        """只有提交凭证词、无拒绝语义 → 不是 reject 场景。"""
        assert is_login_reject("提交登录请求", "登录成功进入首页") is False


class TestSharedPremiseIsStructural:
    def test_both_consult_same_negation_regex(self):
        """两个判据必须共用同一否定态正则（防再次漂移）。"""
        import inspect

        from src.pipeline.step3 import _rule_extractor_keywords as K

        src_op = inspect.getsource(K.is_login_operation)
        src_rj = inspect.getsource(K.is_login_reject)
        for name, src in (("is_login_operation", src_op), ("is_login_reject", src_rj)):
            assert "_LOGIN_NEGATED_STATE_RE" in src, "%s 必须排除否定态" % name
            assert "_LOGIN_TERMINATED_STATE_RE" in src, "%s 必须排除终止态" % name
