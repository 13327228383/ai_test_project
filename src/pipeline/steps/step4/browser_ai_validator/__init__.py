# -*- coding: utf-8 -*-
"""
browser_ai_validator — AI 响应解析与校验（从 BrowserAi 拆分出的单元 D）
============================================================================
Package facade：保持外部导入路径不变。

★ v6 新增: _adversarial.py — 红队对抗验证（证据一致性 + 误判审查 + 可靠性评估）
"""

from ._parse import _parse_ai_response
from ._validate import _validate_ai_response, _validate_actions
from ._metrics import _record_metric, _record_guard_rejection, _record_action_attempt
from ._adversarial import adversarial_review_results

# 保持向后兼容的无下划线别名（validator.py 等历史调用方使用）
validate_actions = _validate_actions

__all__ = [
    "_parse_ai_response",
    "_validate_ai_response",
    "_validate_actions",
    "validate_actions",
    "_record_metric",
    "_record_guard_rejection",
    "_record_action_attempt",
    "adversarial_review_results",
]
