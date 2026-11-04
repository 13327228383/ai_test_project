# -*- coding: utf-8 -*-
"""回归测试：visual_verify 以「模型能力」判定是否发起视觉验证（2026-08-30 根因修复）。

背景：
  旧实现按 provider 名一刀切 —— vision_provider=="ollama" 即无条件跳过，
  并错误注释"ollama 无视觉能力"，导致可用的本地多模态模型（qwen2.5vl:*）被禁用。
  修复后改为 is_vision_model(model) 能力判定：
    - 视觉能力模型（如 qwen2.5vl:3b）→ 真正发起视觉调用，不再跳过
    - 非视觉模型 → 保留跳过，且绝不触达视觉 provider
"""
from src.ai.ai_client_mixins._base import _BaseMixin
from src.ai.model_registry import is_vision_model


class _Duck:
    """最小替身：记录视觉 provider 是否被真正触达。"""

    def __init__(self):
        self.called = False
        self.now_vision_model = ""
        self.expectation = ""

    def call_vision(self, image_base64, prompt, caller_name, vision_model=None,
                    vision_api_key=None, vision_base_url=None):
        self.called = True
        self.now_vision_model = vision_model
        self.expectation = ""
        # 返回可被解析的通过结果（与本研究解耦：仅用于证明"发起了调用"）
        return {"success": True, "data": '{"passed": true, "reason": "符合预期"}',
                "usage": {}, "_model": vision_model or "x"}


def _fire(duck, vision_model):
    """以 duck 为 self 调用真实 visual_verify 方法（桩掉真实网络 provider）。"""
    return _BaseMixin.visual_verify(
        duck,
        b"\x89PNG\r\n\x1a\n" + b"x" * 32,
        "页面符合预期",
        caller_name="回归测试",
        vision_model=vision_model,
        vision_api_key="ollama",
        vision_base_url="http://127.0.0.1:11434/v1",
    )


def test_vision_capable_model_is_not_blanket_skipped():
    """Ollama 上的多模态模型（qwen2.5vl:3b）不再因 provider 名被跳过——真正发起视觉调用。"""
    assert is_vision_model("qwen2.5vl:3b") is True  # 能力探测器正确识别
    duck = _Duck()
    r = _fire(duck, "qwen2.5vl:3b")
    assert duck.called is True, "视觉能力模型必须真正发起视觉调用，而非被跳过"
    assert r.get("success") is True
    assert duck.now_vision_model == "qwen2.5vl:3b"
    # 旧逻辑的"provider 名跳过"文案不应再出现在任何判定结果中
    assert "vision_provider=ollama" not in str(r)


def test_non_vision_model_skips_without_touching_vision_provider():
    """非多模态模型（qwen2.5:7b）→ 跳过，且绝不触达视觉 provider。"""
    assert is_vision_model("qwen2.5:7b") is False
    duck = _Duck()
    r = _fire(duck, "qwen2.5:7b")
    assert duck.called is False, "非视觉模型不得发起视觉调用"
    assert r.get("success") is False
    assert "非多模态" in r.get("reason", "")