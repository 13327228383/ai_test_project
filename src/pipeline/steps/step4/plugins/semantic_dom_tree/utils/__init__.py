# -*- coding: utf-8 -*-
"""
semantic_dom_tree.utils — 模块级常量、辅助函数与 KPI 埋点（facade）

将原单体 utils.py 拆分为 _metrics / _classify / _region 三个子模块，
本文件作为统一入口，re-export 所有公开符号，保持外部导入路径不变。
"""

from ._metrics import _record_tree_metric  # noqa: F401
from ._classify import (  # noqa: F401
    _TYPE_BUTTON,
    _TYPE_INPUT,
    _TYPE_LINK,
    _TYPE_OTHER,
    _TYPE_TABLE,
    _BUTTON_TAGS,
    _INPUT_TAGS,
    _LINK_TAGS,
    _TABLE_TAGS,
    _classify,
    _get_h,
    _get_tag,
    _get_text,
    _get_y,
)
from ._region import (  # noqa: F401
    _cluster_regions,
    _compact_element,
    _extract_keywords,
    _region_name_and_summary,
)
