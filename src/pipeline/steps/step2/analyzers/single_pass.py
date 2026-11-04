# -*- coding: utf-8 -*-
"""
UI 功能点摘要提取（从 single_pass 拆分，保留 step1_context_loader 依赖）
"""

import logging

_log = logging.getLogger(__name__)


def _extract_ui_feature_summary(
    ui_context: str, max_chars: int = 2500, focus_module: str = ""
) -> str:
    """★ v6: 委托给统一的 extract_feature_points()，禁止硬截断。

    保留此函数作为门面（step1_context_loader.py 等调用方依赖），
    实际逻辑由 src.extensions.rag_engine.chunking_mixin_mixins._segment.extract_feature_points 提供。

    人类不会逐字记UI结构，而是记"有哪些功能点"。
    """
    if not ui_context:
        return ""

    try:
        from src.extensions.rag_engine.chunking_mixin_mixins._segment import (
            extract_feature_points,
        )

        return extract_feature_points(
            ui_context, max_chars=max_chars, focus_keyword=focus_module
        )
    except Exception as e:
        _log.warning(
            "[ui_summary] 统一功能点提取失败，降级段落级摘要: %s", str(e)[:120]
        )
        # 降级：按段落拼接，超长时丢弃而非截断
        import re as _re

        paragraphs = [x for p in _re.split(r"\n\s*\n", ui_context) if (x := p.strip())]
        result = ""
        for para in paragraphs:
            if len(result) + len(para) > max_chars:
                break  # 丢弃剩余，禁止硬截断
            result += para + "\n"
        return result.strip()
