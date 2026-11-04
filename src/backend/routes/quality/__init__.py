# -*- coding: utf-8 -*-
"""
quality/__init__.py — 质量治理只读聚合 API 包入口
================================================
对齐 `metrics_page/__init__.py` 的包模式（§8.10）：
`__init__.py` 只做 re-export，实现放在同级模块，
使外部统一以 `from src.backend.routes.quality import quality_bp` 引用，
将来内部再拆分子模块时调用方零改动。
"""

from .quality_routes import quality_bp, api_w3_summary

__all__ = ["quality_bp", "api_w3_summary"]
