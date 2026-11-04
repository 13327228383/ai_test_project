# -*- coding: utf-8 -*-
"""
dynamic_dom_sizer — 动态 DOM 元素上限
====================================
根据意图类型智能调整 MAX_DOM_ELEMENTS，简单场景（如登录）只保留少量元素，
复杂流程（如审批 + 配置）放开上限；同时输出一个三档的 context_size_hint
（"compact" / "normal" / "full"），供 AI prompt 里标注当前 DOM 展示模式。

规则表（按优先级匹配，命中第一条即返回）:
    登录/注册/登出           → 40
    搜索/筛选/查询           → 80
    简单按钮点击/导航跳转     → 30
    填写表单/新增记录/创建    → 100
    审批/审核/确认           → 60
    删除/移除               → 50
    导出/下载               → 50
    分页/翻页               → 40
    复杂业务（含"流程/审批/配置"）→ 150
    默认                    → 120

支持自定义覆盖: set_override(limit) / clear_override()。
"""

from ._rules import get_dom_limit, get_context_size_hint
from ._override import set_override, clear_override, _OVERRIDE_LIMIT
from ._metric import _record_sizer_metric

__all__ = ["get_dom_limit", "set_override", "clear_override", "get_context_size_hint"]
