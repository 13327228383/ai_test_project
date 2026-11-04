# -*- coding: utf-8 -*-
"""
browser_ai.ai — BrowserAi 拆分包子包

将原 browser_ai.py 中 947 行的 BrowserAi 按职责拆分为：
- _base.py        — __init__ / _model_supports_tools / 常量 / 标准能力门面
- _tool_mode.py   — _try_tool_mode 原生工具调用模式
- _text_mode.py   — call_ai_for_action / _try_text_mode 纯文本模式
- _validator.py   — AI 响应解析与校验（门面委托到 browser_ai_validator）
- _login.py       — login / _try_deterministic_login

BrowserAi 继承全部 mixin，对外接口 100% 兼容。
模块级函数（_record_metric / _load_allowed_actions_from_library /
_load_import_keywords_from_library / _fill_placeholders）保留在 browser_ai.py。
"""
