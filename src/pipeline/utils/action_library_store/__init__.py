# -*- coding: utf-8 -*-
"""
操作指令库 MySQL 事实源 (ActionLibraryStore)
================================================

事实源表：
  - action_categories  : 指令分类（分类 id/名称/图标）
  - action_actions     : 具体操作（id/label/desc/keywords/params/needs_ai_id/is_workflow/example）

设计原则：
  1. 操作指令库是全项目共享的通用能力，MySQL 是唯一结构化事实源。
     本文件是主运行链路唯一的写入/读取入口。
  2. 原有的 ``action_library.json`` 仅作为显式导入源 / 导出物 / 附件角色使用，
     不参与 Step3/Step4/Step5 等主运行链路兜底。
  3. 最小可验证改动：只做表读写封装 + 导入导出 + 健康统计。

典型用法（只读主链路）：
    from src.pipeline.utils.action_library_store import action_library_store
    allowed = action_library_store.load_allowed_action_ids()     # 浏览器白名单
    import_kw = action_library_store.load_import_keywords()       # DataFactory 导入关键词
    lib = action_library_store.load_library()                     # 前端预览 JSON 结构

导入/导出（显式触发，非主运行链路）：
    action_library_store.export_to_json(path)
    action_library_store.import_from_json(path, replace=False)
"""

from typing import Any

from src.pipeline.utils.action_library_store.base import _ActionLibraryBaseMixin
from src.pipeline.utils.action_library_store.write import _ActionLibraryWriteMixin
from src.pipeline.utils.action_library_store.read import _ActionLibraryReadMixin
from src.pipeline.utils.action_library_store.io import _ActionLibraryIOMixin
from src.pipeline.utils.action_library_store.health import _ActionLibraryHealthMixin
from src.pipeline.utils.action_library_store.defaults import _load_default_library


# ----------------------------------------------------------------------------
# ★ f4: 控件类型（语义） -> HTML tag / ARIA role 候选集合
#   通用语义映射，集中管理（原 _action_injection.py 的 _CONTROL_TYPE_TO_TAGS）。
#   未来可由 MySQL 配置表覆盖，当前作为兜底默认值。
# ----------------------------------------------------------------------------
_DEFAULT_CONTROL_TYPE_TAGS = {
    "input": ["input", "textarea"],
    "select": ["select"],
    "button": ["button", "a"],
    "upload": ["input"],
    "nav": ["a", "button"],
    "interactive": ["div", "span", "button", "a"],
    "composite": ["div", "form", "button", "a", "input", "select", "textarea"],
}


# ----------------------------------------------------------------------------
# ★ f4: 控件类型 -> 优先 action id（仅用于排序，不参与源头筛选）
#   通用语义映射，集中管理（原 _action_injection.py 的 _CONTROL_TYPE_PREFERRED_ACTIONS）。
# ----------------------------------------------------------------------------
_DEFAULT_CONTROL_TYPE_PREFERENCES = {
    "input": {"fill": 10, "type_text": 9, "clear": 8, "focus": 6, "blur": 5},
    "select": {"select_option": 10, "check": 4, "uncheck": 4},
    "button": {"click": 10, "double_click": 4, "hover": 3},
    "upload": {"upload_file": 10},
    "nav": {"navigate": 10, "click": 5},
    "interactive": {"click": 5, "hover": 4, "focus": 3},
    "composite": {"click": 5, "fill": 4, "select_option": 3},
}


class ActionLibraryStore(
    _ActionLibraryBaseMixin,
    _ActionLibraryWriteMixin,
    _ActionLibraryReadMixin,
    _ActionLibraryIOMixin,
    _ActionLibraryHealthMixin,
):
    """MySQL ``action_categories`` + ``action_actions`` 表读写工具。"""

    pass


# 模块级单例（主运行链路使用）
action_library_store = ActionLibraryStore()
