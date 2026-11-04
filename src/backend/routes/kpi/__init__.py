# -*- coding: utf-8 -*-
"""
kpi/__init__.py — 统一 KPI 视图 Blueprint 包入口
================================================
对齐 quality/__init__.py 的包模式：`__init__.py` 只做 re-export，
实现放在同级模块，外部统一以 `from src.backend.routes.kpi import kpi_bp` 引用。

端点（url_prefix=/api/kpi，见 _routes.py）：
  - GET /overview    KPI 聚合总览（指标字典 + 分布 + 趋势 + 台账不符明细）
  - GET /trend       近 N 次运行趋势（可选）
"""

from ._routes import kpi_bp

__all__ = ["kpi_bp"]
