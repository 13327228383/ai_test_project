# -*- coding: utf-8 -*-
"""correctness_vlm / VLMOracle 视觉校验单测。

覆盖(2026-08-29 根因修复的防回归)：
  - 视觉复核主路径传入的自由描述 expectation({"expectation","check_type"})
    → 走"自由期望二元裁决" prompt（不强制元素 grounding），避免恒 match=False；
  - 带 elements 列表的 layout_spec → 走"元素 grounding" prompt；
  - _normalize_result 对 match=true/false/null 的归一。
"""
from __future__ import annotations

import pytest

from src.oracle.correctness_vlm import VLMOracle


@pytest.fixture()
def oracle() -> VLMOracle:
    return VLMOracle()


def test_free_form_expectation_uses_binary_ruling_prompt(oracle):
    """视觉复核主路径契约：{"expectation", "check_type"} 含"严格二选一"裁决，不做 grounding。"""
    spec = {"expectation": "柱状图柱子未遮挡纵坐标", "check_type": "visual"}
    prompt = oracle._build_prompt(spec, "", False)
    assert "match" in prompt
    assert "null" in prompt  # 允许无法确定（fail-closed）
    assert "严格二选一" in prompt or "是否符合" in prompt
    # 不应强制逐元素 grounding
    assert "grounding" not in prompt and "diff_regions" not in prompt


def test_elements_spec_uses_grounding_prompt(oracle):
    """带 elements 的 layout_spec 仍走元素 grounding 模式（原有能力不回退）。"""
    spec = {"elements": [{"type": "button", "text": "保存", "region": [0, 0, 100, 40]}]}
    prompt = oracle._build_prompt(spec, "", False)
    assert "grounding" in prompt
    assert "diff_regions" in prompt
    assert "match" in prompt


def test_parse_normalize_match_bool(oracle):
    """match=true/false/null 分别归一为 True/False/None。"""
    assert oracle._normalize_result({"match": True})["match"] is True
    assert oracle._normalize_result({"match": False})["match"] is False
    assert oracle._normalize_result({"match": None})["match"] is None
    assert oracle._normalize_result({"match": "true"})["match"] is True


def test_parse_normalize_confidence_and_reason(oracle):
    """confidence 界限钳制 + reason 截断。"""
    r = oracle._normalize_result({"match": True, "confidence": 3.7, "reason": "x" * 600})
    assert r["confidence"] == 1.0
    assert len(r["reason"]) <= 512