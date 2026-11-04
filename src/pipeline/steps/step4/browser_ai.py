# -*- coding: utf-8 -*-
"""
浏览器 AI 交互模块 (BrowserAi) — AI 调用与操作分发
====================================================

负责：登录、AI 操作指令生成、操作分发（click/fill/verify）。

与 BrowserCore 的关系：由 BrowserCore 实例化并委托 AI 相关调用。

【2026-06-18 重构】
原 ~947 行的单体类已按职责拆分为 ai/ 下的独立 mixin：
- _base.py        — __init__ / _model_supports_tools / 常量 / 标准能力门面
- _tool_mode.py   — _try_tool_mode 原生工具调用模式
- _text_mode.py   — call_ai_for_action / _try_text_mode 纯文本模式
- _validator.py   — AI 响应解析与校验（门面委托到 browser_ai_validator）
- _login.py       — login / _try_deterministic_login

BrowserAi 继承全部 mixin，对外接口 100% 保持兼容。
模块级函数（_load_allowed_actions_from_library /
_load_import_keywords_from_library）保留在本文件；指标记录的唯一实现
在 ``src.extensions.metrics_collector.record_metric``。
"""

import logging
from src.core.guarded import guard

_log = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════
# 模块级函数（保留在 browser_ai.py，供外部和 mixin 共享）
# ═══════════════════════════════════════════════════════════════


# ★ f6: 应急兜底白名单（仅 DB 完全不可用时的最小可执行集）
#   完整白名单由 action_library_store.load_allowed_action_ids() 从 MySQL 提供。
#   此集合只保留 5 个最基础操作，确保 DB 故障时系统仍能导航/点击/输入/验证/截图。
_DEFAULT_ALLOWED_ACTIONS = frozenset(
    {
        "navigate",
        "click",
        "fill",
        "verify",
        "screenshot",
    }
)


def _load_allowed_actions_from_library():
    """浏览器动作白名单：从 MySQL ``action_actions`` 读取 is_workflow=0 的 action_id 集合。"""
    with guard("_load_allowed_actions_from_library: from src.pipeline.utils.action_libra", log=_log, level=logging.WARNING):
        from src.pipeline.utils.action_library_store import action_library_store

        allowed = action_library_store.load_allowed_action_ids()
        if allowed:
            return set(allowed) | set(_DEFAULT_ALLOWED_ACTIONS)
    return set(_DEFAULT_ALLOWED_ACTIONS)


def _load_import_keywords_from_library():
    """DataFactory 导入检测关键词：从 MySQL ``action_actions`` 读取 is_workflow=1 的 keywords。"""
    with guard("_load_import_keywords_from_library: from src.pipeline.utils.action_libra", log=_log, level=logging.WARNING):
        from src.pipeline.utils.action_library_store import action_library_store

        keywords = action_library_store.load_import_keywords()
        if keywords:
            return tuple(keywords)
    return (
        "导入",
        "上传",
        "下载模板",
        "下载模版",
        "模板下载",
        "模版下载",
        "批量导入",
        "导入数据",
        "上传文件",
        "导入文件",
        "import",
        "upload",
        "download template",
    )


# ═══════════════════════════════════════════════════════════════
# BrowserAi 门面类
# ═══════════════════════════════════════════════════════════════

from .ai.base import _BrowserAiBaseMixin
from .ai.tool_mode import _BrowserAiToolModeMixin
from .ai.text_mode import _BrowserAiTextModeMixin
from .ai.validator import _BrowserAiValidatorMixin
from .ai.login import _BrowserAiLoginMixin


class BrowserAi(
    _BrowserAiBaseMixin,
    _BrowserAiToolModeMixin,
    _BrowserAiTextModeMixin,
    _BrowserAiValidatorMixin,
    _BrowserAiLoginMixin,
):
    """
    AI 调用模块（门面类）。由 BrowserCore 实例化并委托登录与决策调用。

    使用方式：
        from src.pipeline.steps.step4.browser_ai import BrowserAi
        ai = BrowserAi(engine)
        ok = await ai.login()
        decision = await ai.decide_action(prompt, screenshot="")
    """

    pass
