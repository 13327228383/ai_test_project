# -*- coding: utf-8 -*-
"""
BrowserAi 文本模式 mixin (facade)

职责：
- call_ai_for_action — 主入口（优先工具模式，失败回退文本模式）
- _try_text_mode — 纯文本 JSON 操作序列模式

拆分为 facade + mixins 模式，具体实现见 text_mode_mixins/ 子目录。
"""

from src.pipeline.steps.step4.ai.text_mode_mixins._call import _BrowserAiTextCallMixin
from src.pipeline.steps.step4.ai.text_mode_mixins._text_parse import (
    _BrowserAiTextParseMixin,
)
from src.pipeline.steps.step4.ai.text_mode_mixins._decide import _BrowserAiDecideMixin


class _BrowserAiTextModeMixin(
    _BrowserAiTextCallMixin, _BrowserAiTextParseMixin, _BrowserAiDecideMixin
):
    """文本模式能力（facade）。"""

    pass
