# -*- coding: utf-8 -*-
"""上下文裁剪辅助 + token 预算常量 — 从 case_generator.py 拆出，零业务逻辑变更。"""

import logging

_log = logging.getLogger(__name__)


_CTX_LIMITS = {
    "ui": 2200,
    "trace": 1400,
    "data_dict": 2200,  # ★ 两层注入：紧凑目录(~1200) + 按需详情(~800)
    "rules": 900,
    "few_shot": 900,
    "feedback": 700,
    "schema": 1400,
    "project": 1000,
    "action_library": 2000,  # ★ 2026-07-02 新增：操作/断言清单裁剪上限
}


def _clip_context(label: str, text: str, limit: int) -> str:
    """意图驱动上下文裁剪：只保留生成意图/证据计划所需的聚焦信息。"""
    if not text:
        return ""
    s = str(text).strip()
    if len(s) <= limit:
        return s
    clipped = s[:limit].rstrip()
    _log.info("    [Prompt] %s 上下文裁剪: %d -> %d 字符", label, len(s), len(clipped))
    return clipped + "\n...（已按意图驱动裁剪，仅保留关键上下文）"
