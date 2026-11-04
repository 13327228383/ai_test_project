# -*- coding: utf-8 -*-
"""
置信度计算器 mixins 包
=====================================
``confidence_calculator.py`` facade 的内部实现拆分。

子模块：
  - ``_meta``:     _DualMethod 描述符 + _ConfidenceMeta 元类
  - ``_calculate``: 权重管理 + 单条置信度计算
  - ``_block``:    低置信度拦截 + 批量计算
  - ``_feedback``: QA 反馈闭环
  - ``_trend``:    跨版本趋势告警

对外入口仍为 ``src.extensions.confidence_calculator.ConfidenceCalculator``。
"""
