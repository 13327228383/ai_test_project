# -*- coding: utf-8 -*-
"""
clarification/__init__.py — 交互式需求澄清 Blueprint 包入口
==========================================================
对齐 quality/__init__.py 的包模式：`__init__.py` 只做 re-export，
实现放在同级模块，外部统一以 `from src.backend.routes.clarification import clarification_bp`
引用，将来内部再拆分子模块时调用方零改动。

端点（url_prefix=/api/step2/clarify，见 _routes.py）：
  - GET  /sessions            列出澄清会话（歧义卡片 + 证据 + 问题）
  - GET  /sessions/<sid>      会话详情（含已答问题）
  - GET  /questions           /sessions 别名（design §3.3 兼容）
  - POST /answers             提交回答 → 回填 requirements + 解除闸门前置
  - POST /release             解除闸门（status → released）
  - GET  /gate                查询闸门是否打开（should_block_generation 状态）
"""

from ._routes import clarification_bp

__all__ = ["clarification_bp"]
