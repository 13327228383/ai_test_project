# -*- coding: utf-8 -*-
"""
置信度与智能洞察相关函数（package facade）
"""
from ._summary import _collect_confidence_summary
from ._insights import _generate_smart_insights
from ._svg_charts import _generate_learning_curve_svg, _generate_cache_dashboard_svg

__all__ = [
    "_collect_confidence_summary",
    "_generate_smart_insights",
    "_generate_learning_curve_svg",
    "_generate_cache_dashboard_svg",
]
