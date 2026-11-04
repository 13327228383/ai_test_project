# -*- coding: utf-8 -*-
"""D41 契约：行为型访问控制的判据只能有一处 —— checks 层的结构化 access_control_check。

根因（实测 TC-REQ-017/020-E01）：`_fallback_cases` 在 access_control 场景同时发射了
①checks[].access_control_check（结构化，有确定性执行器）
②verifications[] 里一条自然语言 verify（"低权身份执行受限操作被系统拒绝…"）
Step4 的 AgentExplorer 路径先跑 guided 的 ②，判失败即提前终结用例，
令 ① 的确定性执行器（_handle_access_control_check）根本无从运行 → 假 ERROR。
"""
from __future__ import annotations

import ast
import inspect

from src.pipeline.step3 import _fallback_cases as F


def _access_control_verifications() -> list:
    """从源码 AST 里取出 access_control 场景的 verifications 字面量。"""
    tree = ast.parse(inspect.getsource(F))
    for node in ast.walk(tree):
        if not isinstance(node, ast.If):
            continue
        test = ast.unparse(node.test)
        if "'access_control'" not in test and '"access_control"' not in test:
            continue
        for sub in ast.walk(node):
            if isinstance(sub, ast.Dict):
                for k, v in zip(sub.keys, sub.values):
                    if isinstance(k, ast.Constant) and k.value == "verifications":
                        try:
                            return ast.literal_eval(v)
                        except Exception:
                            return []
    return []


class TestAccessControlHasSingleJudgementSite:
    def test_scenario_section_exists(self):
        src = inspect.getsource(F)
        assert '"access_control"' in src or "'access_control'" in src

    def test_no_natural_language_verify_for_access_control(self):
        """access_control 场景不得再发射自然语言 verify（判据属 checks 层）。"""
        verifs = _access_control_verifications()
        assert verifs == [], (
            "access_control 场景的 verifications 必须为空（判据由 checks 层 "
            "access_control_check 权威表达），实际=%r" % (verifs,)
        )

    def test_op_type_still_marked(self):
        """清空 verifications 后，op_type 必须仍是 security_behavior，
        否则 _agent_executor 的导航判决豁免（_is_security_behavior_case）会失效。"""
        src = inspect.getsource(F)
        assert "_probe_op_type" in src
        assert "security_behavior" in src

    def test_exempt_predicate_survives(self):
        """执行端豁免条件必须仍能被 op_type 命中（不依赖 verifications target）。"""
        from src.pipeline.steps.step4.browser_core.single_mixins import _agent_executor as A

        src = inspect.getsource(A)
        assert 'op_type") == "security_behavior"' in src, (
            "豁免必须支持仅凭 op_type 命中，否则清空 verifications 会丢失豁免"
        )
