# -*- coding: utf-8 -*-
"""
metrics_page/__init__.py — 指标中心 API 包入口
=============================================
原 metrics_page.py 按功能域拆分为子模块后，本文件保留对外 re-export，
确保已有 import 路径（如 from src.backend.routes.metrics_page import metrics_page_bp）
零变更即可继续工作。
"""

from .metrics_page import metrics_page_bp, api_metrics_summary

__all__ = ["metrics_page_bp", "api_metrics_summary"]
