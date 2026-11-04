import logging

_log = logging.getLogger(__name__)
# -*- coding: utf-8 -*-
"""
统一日志前缀格式化工具。
提供 fmt_log() 函数将所有模块标签在 50 字符宽度内右对齐，确保日志视觉整齐。

用法:
    from src.core.log_utils import fmt_log
    _log.info(fmt_log("Layer 1", "从缓存拉取 149 个页面"))
    # →                                   [Layer 1] 从缓存拉取 149 个页面
"""

_LABEL_WIDTH = 50


def fmt_log(label: str, msg: str) -> str:
    """标签 [label] 在 50 字符宽度内右对齐，内容从第 51 字符开始。"""
    bracketed = "[%s]" % label
    return "%*s %s" % (_LABEL_WIDTH, bracketed, msg)
