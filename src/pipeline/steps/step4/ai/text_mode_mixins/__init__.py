# -*- coding: utf-8 -*-
"""
text_mode_mixins — BrowserAi 文本模式拆分子模块

拆分自 text_mode.py，采用 facade + mixins 模式：
- _helpers: 模块级辅助函数
- _call: AI 调用入口 mixin (call_ai_for_action)
- _text_parse: 文本解析 mixin (_try_text_mode, _parse_text_actions)
"""
