# -*- coding: utf-8 -*-
from __future__ import annotations

"""
AI 调用入口 mixin — call_ai_for_action

职责：主入口（优先工具模式，失败回退文本模式）
"""


class _BrowserAiTextCallMixin:
    """AI 调用入口：优先工具模式，失败回退文本模式。"""
