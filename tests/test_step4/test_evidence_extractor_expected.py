# -*- coding: utf-8 -*-
"""回归测试：evidence 提取器中 expected 字段的通用性修复（报告阶段）。

背景（2026-09-02）：executions 表 FAIL 记录的 expected 存在两类报告缺陷：
  ① 权威需求 FAIL（无 verify.summary）→ expected 恒空；
  ② verify.summary 携带 [FAIL] text_contains 等观测文本 → expected 被污染。

修复后 expected 以"步骤预期操作描述"优先、verify.summary 清理后兜底，
并去除 [PASS]/[FAIL] 判定态前缀，保证 expected 是"预期行为"而非观测结论。
"""
import pytest

from src.pipeline.steps.step4.browser_core.single_mixins._evidence_extractor import (
    extract_evidence_from_step_results,
    clean_expected,
)


def test_authority_fail_expected_not_empty_and_clean():
    """真实形态：权威需求未实现（无 _verify_result，description 为预期操作，error 为判定）。
    expected 应回填预期操作，且不含 'FAIL(' 判定态。"""
    step_results = [
        {
            "step_idx": 0,
            "description": "在医院诊所管理系统需求规格模块中，用户执行REQ-DOC-01："
                           "医生列表展示医生信息。操作，系统预期反馈：操作成功,成功",
            "action": "assert",
            "passed": False,
            "error": "需求已定义但系统未实现/不可达: 需求 REQ-006 功能(模块 "
                     "'医院诊所管理系统需求规格') 在侧边栏/URL 中无对应实现，需求未得到满足",
            "expected": "REQ-006",
        }
    ]
    _expected, _actual, _details = extract_evidence_from_step_results(step_results)
    assert _expected, "expected 不得为空"
    assert "REQ-DOC-01" in _expected
    assert "系统预期反馈" in _expected
    assert "FAIL(" not in _expected
    assert "[FAIL]" not in _expected
    assert _actual, "actual 应来自 error，非空"
    assert "需求已定义但系统未实现" in _actual


def test_verify_summary_verdict_pollution_is_cleaned():
    """真实形态（id=1358）：verify.summary 携带 [FAIL] text_contains 观测文本；
    description 为 '[PASS] Agent(guided): 验证通过: ... 预期反馈：删除成功'。
    expected 应为去判定态的预期行为（含 '删除成功'），不含 [PASS]/[FAIL] 观测。"""
    step_results = [
        {
            "description": "[PASS] Agent(guided): 验证通过: 在医院诊所管理系统需求规格"
                           "模块中，用户执行REQ-PAT-04：可删除患者（需确认）。操作，系统"
                           "预期反馈：删除成功,移除成功",
            "passed": True,
            "_verify_result": {
                "ui": {"passed": False, "evidence": "[FAIL] text_contains: "
                                                     "找到0个匹配文本节点"
                                                     "（已排除输入框值）"},
                "api": {"passed": False, "evidence": "NOT_AVAILABLE"},
                "db": {"passed": False, "evidence": "NOT_AVAILABLE"},
                "summary": "Agent执行成功 (guided); UI: [FAIL] text_contains: "
                           "找到0个匹配文本节点（已排除输入框值）; [PASS] no_error",
            },
        }
    ]
    _expected, _actual, _details = extract_evidence_from_step_results(step_results)
    assert _expected, "expected 不得为空"
    assert "删除成功" in _expected
    assert "[FAIL]" not in _expected
    assert "[PASS]" not in _expected
    # actual 保留观测（判定 + 证据），expected 不得混入观测判定
    assert "[FAIL] text_contains" in _actual or "text_contains" in _actual


def test_normal_pass_expected_preserved():
    """普通 PASS：clean 后 expected 保留预期行为文本，去判定态前缀。"""
    step_results = [
        {
            "description": "点击删除患者确认按钮，系统提示'删除成功'",
            "passed": True,
            "_verify_result": {
                "ui": {"passed": True, "evidence": "已确认删除按钮响应"},
                "api": {"passed": True, "evidence": "无API调用"},
                "db": {"passed": True, "evidence": "无DB变更"},
                "summary": "验证通过",
            },
        }
    ]
    _expected, _actual, _details = extract_evidence_from_step_results(step_results)
    assert _expected
    assert "删除成功" in _expected
    assert "验证通过" not in _expected


def test_clean_expected_strips_verdict_markers():
    assert clean_expected("验证通过: 删除成功") == "删除成功"
    assert clean_expected("[FAIL] text_contains: 找到0个") == "text_contains: 找到0个"
    assert clean_expected("FAIL(需求未实现): 需求未得到满足") == "需求未得到满足"
    assert clean_expected("Agent执行: 在医院诊所系统中删除患者，系统删除成功") \
        == "在医院诊所系统中删除患者，系统删除成功"
    # "Agent 执行："（带空格、半角/全角冒号）并入清除词表（2026-09-02）
    assert clean_expected("Agent 执行：在医院诊所系统中删除患者，系统删除成功") \
        == "在医院诊所系统中删除患者，系统删除成功"
    assert clean_expected("Agent 执行: 点击保存按钮") == "点击保存按钮"
    assert clean_expected("") == ""